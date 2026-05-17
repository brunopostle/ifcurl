// Copyright 2026 The Forgejo Authors. All rights reserved.
// SPDX-License-Identifier: MIT
import { THREE, OBC, OBCF, JSZip } from "/assets/viewer-deps.js";
import { parseIfcUrl, toRawUrl, buildIfcUrl as _buildIfcUrl } from "./viewer-url.js";
import { isSimpleTypeSelector, subtractIdMap, isCommitHash } from "./viewer-util.js";

const params   = new URLSearchParams(window.location.search);
let currentIfcUrl = params.get("url") ?? "";
const statusEl = document.getElementById("status");
const urlInput = document.getElementById("url-input");
const fovInput = document.getElementById("fov-input");
const toolbar  = document.getElementById("toolbar");
const repoInput     = document.getElementById("repo-input");
const refInput      = document.getElementById("ref-input");
const pathInput     = document.getElementById("path-input");
const selectorInput = document.getElementById("selector-input");
const queryInput    = document.getElementById("query-input");
const queryPanel      = document.getElementById("query-panel");
const queryTbody      = document.getElementById("query-tbody");
const queryPanelTitle = document.getElementById("query-panel-title");
const queryValHeader  = document.getElementById("query-val-header-col");
const queryCloseBtn   = document.getElementById("query-close-btn");
const copyUrlBtn       = document.getElementById("copy-url-btn");
const newIssueBtn      = document.getElementById("new-issue-btn");
const visibilitySelect = document.getElementById("visibility-select");
const visibilityField  = document.getElementById("visibility-field");
const propsPanel    = document.getElementById("props-panel");
const propsType     = document.getElementById("props-type");
const propsName     = document.getElementById("props-name");
const propsGuid     = document.getElementById("props-guid");
const propsCopyBtn  = document.getElementById("props-copy-btn");
const propsCloseBtn = document.getElementById("props-close-btn");

// Keep viewer below the full toolbar height.
const resizeObs = new ResizeObserver(() => {
  document.documentElement.style.setProperty("--toolbar-h", toolbar.offsetHeight + "px");
});
resizeObs.observe(toolbar);

let components     = null;
let world          = null;
let cameraControls = null;
let threeCamera    = null;
let activeClipper  = null;
let loadedModel    = null;
let rendererCanvas = null;
// Raw HTTP URL of the loaded IFC file, captured once at model load time.
// Used to decide whether a loadUrl call needs a full reload or an in-place update.
let modelRawUrl = null;
function removeSnapshot() {
  const el = document.getElementById("snapshot-overlay");
  if (!el) return;
  el.style.opacity = "0";
  setTimeout(() => el.remove(), 550);
  sessionStorage.removeItem("viewerSnapshot");
}

// Build an ifc:// URL from the structured form fields.
function buildIfcUrl() {
  return _buildIfcUrl(
    repoInput.value.trim(), refInput.value.trim(),
    pathInput.value.trim(), selectorInput.value.trim(),
    queryInput.value.trim()
  );
}

// Populate structured fields from the current URL.
const parsed = parseIfcUrl(currentIfcUrl);
if (parsed) {
  repoInput.value     = parsed.repo;
  refInput.value      = parsed.ref;
  pathInput.value     = parsed.path;
  selectorInput.value = parsed.selector;
  queryInput.value    = parsed.query;
}
urlInput.value = currentIfcUrl;

// Initialise visibility dropdown from URL and show it when selector is non-empty.
{
  const qIdx = currentIfcUrl.indexOf("?");
  const visVal = qIdx < 0 ? "highlight"
    : (new URLSearchParams(currentIfcUrl.slice(qIdx + 1)).get("visibility") ?? "highlight");
  visibilitySelect.value = visVal;
}
function syncVisibilityField() {
  visibilityField.style.display = selectorInput.value.trim() ? "" : "none";
}
selectorInput.addEventListener("input", syncVisibilityField);
syncVisibilityField();
visibilitySelect.addEventListener("change", loadFromFields);

// Show commit-pin indicator when the ref is a bare commit hash.
const refBadge     = document.getElementById("ref-badge");
const refSwitchBtn = document.getElementById("ref-switch-btn");
if (parsed && isCommitHash(parsed.ref)) {
  refBadge.textContent = parsed.ref.slice(0, 7);
  refBadge.style.display = "";
  if (parsed.host && parsed.repoSuffix) {
    fetchBranchForCommit(parsed.host, parsed.repoSuffix, parsed.ref)
      .then(branchName => {
        const targetRef = `heads/${branchName}`;
        refSwitchBtn.textContent = `→ ${targetRef}`;
        refSwitchBtn.style.display = "";
        refSwitchBtn.addEventListener("click", () => {
          const qI = currentIfcUrl.indexOf("?");
          const base = qI < 0 ? currentIfcUrl : currentIfcUrl.slice(0, qI);
          const tail = qI < 0 ? "" : currentIfcUrl.slice(qI);
          loadUrl(base.slice(0, base.lastIndexOf("@") + 1) + targetRef + tail);
        });
      })
      .catch(() => { /* silent — badge alone is sufficient */ });
  }
}

// Populate ref datalist in background (non-blocking).
if (parsed?.host && parsed?.repoSuffix) {
  populateRefList(parsed.host, parsed.repoSuffix).catch(() => { /* silent */ });
}

// -----------------------------------------------------------------------
// Apply view-only param changes (camera, clip, selector, visibility, query)
// in-place without re-fetching the IFC file.
// -----------------------------------------------------------------------
async function applyViewChanges(newUrl) {
  const oldQIdx = currentIfcUrl.indexOf("?");
  const oldQs   = new URLSearchParams(oldQIdx < 0 ? "" : currentIfcUrl.slice(oldQIdx + 1));
  const newQIdx = newUrl.indexOf("?");
  const newQs   = new URLSearchParams(newQIdx < 0 ? "" : newUrl.slice(newQIdx + 1));

  // Update currentIfcUrl and the URL bar first so any triggered syncCameraUrl
  // calls use the correct base.
  currentIfcUrl  = newUrl;
  urlInput.value = newUrl;
  const pageUrl  = new URL(window.location.href);
  pageUrl.searchParams.set("url", newUrl);
  history.replaceState(null, "", pageUrl.toString());

  // Sync structured form fields.
  const parsedNew = parseIfcUrl(newUrl);
  if (parsedNew) {
    selectorInput.value = parsedNew.selector;
    queryInput.value    = parsedNew.query;
  }
  const newVisibility = newQs.get("visibility") ?? "highlight";
  visibilitySelect.value = newVisibility;
  syncVisibilityField();

  // Camera / projection.
  const oldCamera = oldQs.get("camera") ?? "";
  const newCamera = newQs.get("camera") ?? "";
  const oldFov    = oldQs.get("fov")    ?? "";
  const newFov    = newQs.get("fov")    ?? "";
  const oldScale  = oldQs.get("scale")  ?? "";
  const newScale  = newQs.get("scale")  ?? "";
  if (newCamera !== oldCamera || newFov !== oldFov || newScale !== oldScale) {
    await applyCameraParam(cameraControls, world.camera, newUrl);
    // Flush camera-controls position into the Three.js camera matrix before rendering.
    // setLookAt(false) sets the destination but doesn't write to Three.js until update().
    cameraControls.update(0);
    threeCamera = world.camera.three;
    if (loadedModel) loadedModel.useCamera(threeCamera);
    await components.get(OBC.FragmentsManager).core.update(true);
  }

  // Clipping planes.
  const oldClips = oldQs.getAll("clip");
  const newClips = newQs.getAll("clip");
  if (JSON.stringify(newClips) !== JSON.stringify(oldClips) && activeClipper) {
    activeClipper.deleteAll();
    const planes = newClips.map(s => s.split(",").map(Number))
      .filter(v => v.length === 6 && !v.some(isNaN));
    for (const [px, py, pz, nx, ny, nz] of planes) {
      activeClipper.createFromNormalAndCoplanarPoint(
        world, toThree(nx, ny, nz).normalize(), toThree(px, py, pz)
      );
    }
    const clipClearBtn = document.getElementById("clip-clear-btn");
    clipClearBtn.style.display = activeClipper.list.size > 0 ? "" : "none";
  }

  // Selector / visibility.
  const oldSelector   = oldQs.get("selector") ?? "";
  const newSelector   = newQs.get("selector") ?? "";
  const oldVisibility = oldQs.get("visibility") ?? "highlight";
  if (newSelector !== oldSelector || newVisibility !== oldVisibility) {
    const frags = components.get(OBC.FragmentsManager);
    const hider = components.get(OBC.Hider);
    await frags.resetHighlight();
    await hider.set(true);
    if (newSelector) {
      statusEl.textContent = "Applying selector…";
      await applySelector(components, loadedModel, newSelector, newUrl, newVisibility);
    }
    await frags.core.update(true);
  }

  // Query.
  const oldQuery = oldQs.get("query") ?? "";
  const newQuery = newQs.get("query") ?? "";
  if (newQuery !== oldQuery) {
    if (newQuery) await applyQuery(newUrl, newQuery);
    else queryPanel.style.display = "none";
  }

  statusEl.textContent = "";
}

// -----------------------------------------------------------------------
// Navigate to the viewer with a different ifc:// URL.
// If only view params changed (camera, clip, selector, visibility, query)
// the changes are applied in-place without re-fetching the IFC model.
// A full page reload only happens when the source changes (host/repo/ref/path).
// -----------------------------------------------------------------------
function loadUrl(url) {
  // In-place update when the loaded IFC file is unchanged (only view params differ).
  if (modelRawUrl) {
    let rawUrl;
    try { rawUrl = toRawUrl(url); } catch (_) { /* malformed url — fall through */ }
    if (rawUrl && rawUrl === modelRawUrl) {
      applyViewChanges(url).catch(err => {
        statusEl.textContent = `Update error: ${err.message}`;
        console.error(err);
      });
      return;
    }
  }

  // Capture a JPEG snapshot of the WebGL canvas so the overlay script on the
  // new page can cover the blank canvas while the model reloads.
  if (rendererCanvas) {
    try {
      sessionStorage.setItem("viewerSnapshot", rendererCanvas.toDataURL("image/jpeg", 0.6));
    } catch (_) { /* context-lost or cross-origin — skip */ }
  }
  const pageUrl = new URL(window.location.href);
  pageUrl.searchParams.set("url", url);
  window.location.href = pageUrl.toString();
}

urlInput.addEventListener("keydown", e => {
  if (e.key === "Enter") { const v = urlInput.value.trim(); if (v) loadUrl(v); }
});

// Build URL from structured fields, preserving camera/clip/fov/scale from
// the current URL so that ref/path changes keep the viewer's camera state.
function loadFromFields() {
  if (threeCamera) syncCameraUrl(threeCamera);
  const structural = buildIfcUrl();
  if (!structural) return;
  const current = urlInput.value.trim();
  const qI = current.indexOf("?");
  if (qI < 0) { loadUrl(structural); return; }
  const currentQs = new URLSearchParams(current.slice(qI + 1));
  const qI2 = structural.indexOf("?");
  const structBase = qI2 < 0 ? structural : structural.slice(0, qI2);
  const structQs   = new URLSearchParams(qI2 < 0 ? "" : structural.slice(qI2 + 1));
  for (const key of ["camera", "fov", "scale"]) {
    const v = currentQs.get(key);
    if (v) structQs.set(key, v); else structQs.delete(key);
  }
  structQs.delete("clip");
  for (const v of currentQs.getAll("clip")) structQs.append("clip", v);
  // Include visibility when a selector is set; omit for the default (highlight).
  if (selectorInput.value.trim()) {
    const vis = visibilitySelect.value;
    if (vis && vis !== "highlight") structQs.set("visibility", vis);
    else structQs.delete("visibility");
  } else {
    structQs.delete("visibility");
  }
  const qsStr = structQs.toString()
    .replace(/%2C/g, ",").replace(/%2B/g, "+").replace(/%24/g, "$");
  loadUrl(structBase + (qsStr ? "?" + qsStr : ""));
}

// Pressing Enter in any structured field rebuilds the URL and navigates.
for (const el of [repoInput, refInput, pathInput, selectorInput, queryInput]) {
  el.addEventListener("keydown", e => { if (e.key === "Enter") loadFromFields(); });
}

queryCloseBtn.addEventListener("click", () => { queryPanel.style.display = "none"; });

// ▾ button: show a custom dropdown of available refs.
const refPickBtn = document.getElementById("ref-pick-btn");
refPickBtn.addEventListener("click", () => {
  const existing = document.getElementById("ref-dropdown");
  if (existing) { existing.remove(); return; }
  const dl = document.getElementById("ref-list");
  const opts = [...dl.options].map(o => o.value);
  if (!opts.length) { refInput.value = ""; refInput.focus(); return; }
  const rect = refInput.getBoundingClientRect();
  const ul = document.createElement("ul");
  ul.id = "ref-dropdown";
  ul.style.cssText = `position:fixed;top:${rect.bottom + 2}px;left:${rect.left}px;
    min-width:${rect.width}px;max-height:220px;overflow-y:auto;z-index:50;
    background:#2c2c2e;border:1px solid rgba(255,255,255,0.15);border-radius:4px;
    list-style:none;padding:2px 0;`;
  for (const val of opts) {
    const li = document.createElement("li");
    li.textContent = val;
    li.style.cssText = "padding:4px 8px;font-size:12px;font-family:monospace;" +
      "cursor:pointer;color:#f0f0f0;white-space:nowrap;";
    li.addEventListener("mouseover", () => { li.style.background = "rgba(255,255,255,0.1)"; });
    li.addEventListener("mouseout",  () => { li.style.background = ""; });
    li.addEventListener("click", () => {
      refInput.value = val;
      ul.remove();
      loadFromFields();
    });
    ul.appendChild(li);
  }
  document.body.appendChild(ul);
  const close = e => {
    if (!ul.contains(e.target) && e.target !== refPickBtn) {
      ul.remove();
      document.removeEventListener("click", close, true);
    }
  };
  setTimeout(() => document.addEventListener("click", close, true), 0);
});

// Auto-navigate when user selects a datalist option (exact match).
// Listen to both input and change events for cross-browser coverage.
function onRefInputChange() {
  const dl = document.getElementById("ref-list");
  const known = [...dl.options].map(o => o.value);
  if (known.includes(refInput.value.trim())) loadFromFields();
}
refInput.addEventListener("input",  onRefInputChange);
refInput.addEventListener("change", onRefInputChange);

// Disable camera controls while any input has focus.
function onInputFocus() { if (cameraControls) cameraControls.enabled = false; }
function onInputBlur()  { if (cameraControls) cameraControls.enabled = true;  }
const metaBtn   = document.getElementById("meta-btn");
const metaPanel = document.getElementById("meta-panel");
metaBtn.addEventListener("click", () => {
  const open = metaPanel.style.display === "none";
  metaPanel.style.display = open ? "" : "none";
  metaBtn.classList.toggle("active", open);
});

const bcfBtn        = document.getElementById("bcf-btn");
const bcfRow        = document.getElementById("bcf-row");
const bcfTitleIn    = document.getElementById("bcf-title");
const bcfCommentIn  = document.getElementById("bcf-comment");
const bcfCancelBtn  = document.getElementById("bcf-cancel-btn");
const bcfExportBtn  = document.getElementById("bcf-export-btn");
const bcfImportBtn  = document.getElementById("bcf-import-btn");
const bcfImportInput = document.getElementById("bcf-import-input");

function bcfPanelOpen(open) {
  bcfRow.style.display = open ? "" : "none";
  bcfBtn.classList.toggle("active", open);
}

bcfBtn.addEventListener("click", () => bcfPanelOpen(bcfRow.style.display === "none"));
bcfCancelBtn.addEventListener("click", () => bcfPanelOpen(false));
bcfExportBtn.addEventListener("click", async () => {
  await generateBcf(bcfTitleIn.value.trim(), bcfCommentIn.value.trim());
  bcfPanelOpen(false);
});

bcfImportBtn.addEventListener("click", () => bcfImportInput.click());
bcfImportInput.addEventListener("change", async () => {
  const file = bcfImportInput.files[0];
  if (file) await importBcf(file);
  bcfImportInput.value = "";
});

copyUrlBtn.addEventListener("click", async () => {
  if (threeCamera) syncCameraUrl(threeCamera);
  const url = urlInput.value.trim();
  if (!url) return;
  try {
    await navigator.clipboard.writeText(url);
    const saved = copyUrlBtn.textContent;
    copyUrlBtn.textContent = "✓";
    setTimeout(() => { copyUrlBtn.textContent = saved; }, 1500);
  } catch {
    urlInput.select();
  }
});

newIssueBtn.addEventListener("click", () => {
  if (threeCamera) syncCameraUrl(threeCamera);
  const src = urlInput.value.trim();
  if (!src) return;
  const p = parseIfcUrl(src);
  if (!p || !p.host || !p.repoSuffix) return;
  const title = p.path ? `IFC view: ${p.path}` : "IFC view";
  const body = `[${title}](${src})`;
  const issueUrl = `${window.location.origin}/${p.repoSuffix}/issues/new` +
    `?title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`;
  window.open(issueUrl, "_blank", "noopener");
});

document.getElementById("fit-btn").addEventListener("click", () => {
  if (cameraControls && loadedModel && !loadedModel.box.isEmpty())
    cameraControls.fitToBox(loadedModel.box, true);
});

propsCloseBtn.addEventListener("click", () => { propsPanel.style.display = "none"; });
propsCopyBtn.addEventListener("click", async () => {
  const guid = propsGuid.textContent;
  if (!guid) return;
  try {
    await navigator.clipboard.writeText(guid);
    const saved = propsCopyBtn.textContent;
    propsCopyBtn.textContent = "✓";
    setTimeout(() => { propsCopyBtn.textContent = saved; }, 1500);
  } catch { /* ignore */ }
});

const allInputs = [urlInput, fovInput, repoInput, refInput, pathInput, selectorInput,
                   queryInput, visibilitySelect, bcfTitleIn, bcfCommentIn];
for (const el of allInputs) {
  el.addEventListener("focus",       onInputFocus);
  el.addEventListener("blur",        onInputBlur);
  el.addEventListener("pointerdown", e => e.stopPropagation());
  el.addEventListener("mousedown",   e => e.stopPropagation());
}
refPickBtn.addEventListener("pointerdown", e => e.stopPropagation());
refPickBtn.addEventListener("mousedown",   e => e.stopPropagation());

fovInput.addEventListener("change", () => {
  if (!threeCamera?.isPerspectiveCamera) return;
  const v = parseFloat(fovInput.value);
  if (isNaN(v)) return;
  threeCamera.fov = Math.max(10, Math.min(120, v));
  threeCamera.updateProjectionMatrix();
  syncCameraUrl(threeCamera);
});

// -----------------------------------------------------------------------
// Drag-and-drop: accept ifc:// URLs or viewer page URLs.
// -----------------------------------------------------------------------
document.body.addEventListener("dragover", e => {
  e.preventDefault();
  document.body.classList.add("drag-over");
});
document.body.addEventListener("dragleave", e => {
  if (!e.relatedTarget) document.body.classList.remove("drag-over");
});
document.body.addEventListener("drop", async e => {
  e.preventDefault();
  document.body.classList.remove("drag-over");
  // Check for .bcf file drops first.
  for (const file of (e.dataTransfer.files || [])) {
    if (file.name.toLowerCase().endsWith(".bcf")) {
      await importBcf(file);
      return;
    }
  }
  const candidates = [
    e.dataTransfer.getData("text/uri-list"),
    e.dataTransfer.getData("text/plain"),
  ];
  for (const blob of candidates) {
    for (const line of (blob || "").split(/\r?\n/)) {
      const t = line.trim();
      if (!t || t.startsWith("#")) continue;
      if (t.startsWith("ifc://")) { loadUrl(t); return; }
      try {
        const u = new URL(t, window.location.href);
        const p = u.searchParams.get("url");
        if (p?.startsWith("ifc://")) { loadUrl(p); return; }
      } catch { /* not a URL */ }
    }
  }
});

// -----------------------------------------------------------------------
// Coordinate transforms: IFC (Z-up) ↔ Three.js (Y-up).
//   IFC → Three.js:  (x, y, z) → (x,  z, -y)
//   Three.js → IFC:  (x, y, z) → (x, -z,  y)
// -----------------------------------------------------------------------
const toIfc   = v => ({ x: v.x, y: -v.z, z: v.y });
const toThree = (x, y, z) => new THREE.Vector3(x, z, -y);

// -----------------------------------------------------------------------
// Sync camera viewpoint (in IFC coords) into the URL and toolbar row 1.
// The structured fields in row 2 are not affected by camera movement.
// -----------------------------------------------------------------------
function syncCameraUrl(camera) {
  const pos = camera.position;
  const dir = new THREE.Vector3();
  camera.getWorldDirection(dir);
  const up = camera.up;

  const f  = v => parseFloat(v.toFixed(4));
  const ip = toIfc(pos), id = toIfc(dir), iu = toIfc(up);
  const cameraParam = [
    f(ip.x), f(ip.y), f(ip.z),
    f(id.x), f(id.y), f(id.z),
    f(iu.x), f(iu.y), f(iu.z),
  ].join(",");

  const qIdx = currentIfcUrl.indexOf("?");
  const base = qIdx < 0 ? currentIfcUrl : currentIfcUrl.slice(0, qIdx);
  const qs   = new URLSearchParams(qIdx < 0 ? "" : currentIfcUrl.slice(qIdx + 1));
  qs.set("camera", cameraParam);
  if (camera.isPerspectiveCamera) {
    qs.set("fov", f(camera.fov).toString());
    qs.delete("scale");
    fovInput.value = Math.round(camera.fov);
  } else {
    qs.set("scale", f(camera.top - camera.bottom).toString());
    qs.delete("fov");
    fovInput.value = "";
  }
  // Replace original clip= params with current interactive planes.
  qs.delete("clip");
  if (activeClipper) {
    for (const [, plane] of activeClipper.list) {
      const n3 = plane.three.normal;
      const p3 = n3.clone().multiplyScalar(-plane.three.constant);
      const ni = toIfc(n3), pi = toIfc(p3);
      qs.append("clip", [f(pi.x),f(pi.y),f(pi.z),f(ni.x),f(ni.y),f(ni.z)].join(","));
    }
  }

  const newIfcUrl = base + "?" + qs.toString()
    .replace(/%2C/g, ",").replace(/%2B/g, "+").replace(/%24/g, "$");

  currentIfcUrl  = newIfcUrl;
  urlInput.value = newIfcUrl;

  const pageUrl = new URL(window.location.href);
  pageUrl.searchParams.set("url", newIfcUrl);
  history.replaceState(null, "", pageUrl.toString());
}

// -----------------------------------------------------------------------
// Set up OBC.Clipper for interactive clipping planes.
// Seeds initial planes from the URL, wires toolbar buttons, and syncs
// plane state back to the URL on create/delete/drag.
// -----------------------------------------------------------------------
function setupClipper(components, world, clips) {
  const clipper = components.get(OBC.Clipper);
  clipper.setup();
  activeClipper = clipper;

  const clipBtn      = document.getElementById("clip-btn");
  const clipClearBtn = document.getElementById("clip-clear-btn");
  const container    = document.getElementById("viewer");

  function updateClearBtn() {
    clipClearBtn.style.display = clipper.list.size > 0 ? "" : "none";
  }

  // Seed planes from URL as interactive planes.
  for (const [px, py, pz, nx, ny, nz] of clips) {
    clipper.createFromNormalAndCoplanarPoint(
      world, toThree(nx, ny, nz).normalize(), toThree(px, py, pz)
    );
  }
  updateClearBtn();

  // Double-click on model surface adds a plane when clip mode is active.
  let clipMode = false;
  clipBtn.addEventListener("click", () => {
    clipMode = !clipMode;
    clipBtn.classList.toggle("active", clipMode);
    container.style.cursor = clipMode ? "crosshair" : "";
    statusEl.textContent = clipMode ? "Double-click model surface to place clip plane" : "";
  });
  container.addEventListener("dblclick", async () => {
    if (!clipMode) return;
    await clipper.create(world);
    syncCameraUrl(threeCamera);
    updateClearBtn();
  });

  clipClearBtn.addEventListener("click", () => {
    clipper.deleteAll();
    syncCameraUrl(threeCamera);
    updateClearBtn();
  });

  // Sync URL whenever a plane is dragged.
  clipper.onAfterDrag.add(() => syncCameraUrl(threeCamera));
}

// -----------------------------------------------------------------------
// Strip Clearance representation geometry from an IFC STEP buffer so that
// door-swing and equipment-clearance volumes are never rendered.
//
// web-ifc disposes its IfcAPI after load, so context filtering must happen
// before the buffer reaches ifcLoader.load().  IFCSHAPEREPRESENTATION items
// are always bare entity refs (#NNN) with no nested parens, so the items
// list (…) is safe to match with a simple non-nested regex.
// -----------------------------------------------------------------------
function removeClearanceGeometry(buffer) {
  // Only process IFC-SPF (STEP Physical Format) files.
  const sig = [73, 83, 79, 45, 49, 48]; // 'ISO-10'
  if (buffer.length < 6 || sig.some((b, i) => buffer[i] !== b)) return buffer;

  const text = new TextDecoder().decode(buffer);

  // 1. Find subcontext IDs where ContextIdentifier = 'Clearance'.
  const ctxIds = new Set();
  for (const m of text.matchAll(
    /#(\d+)\s*=\s*IFCGEOMETRICREPRESENTATIONSUBCONTEXT\s*\(\s*'Clearance'/gi
  )) ctxIds.add(m[1]);
  if (!ctxIds.size) return buffer;

  // 2. Find IFCSHAPEREPRESENTATION IDs that reference those contexts.
  const repIds = new Set();
  for (const ctxId of ctxIds)
    for (const m of text.matchAll(
      new RegExp(`#(\\d+)\\s*=\\s*IFCSHAPEREPRESENTATION\\s*\\(\\s*#${ctxId}\\s*,`, 'gi')
    )) repIds.add(m[1]);
  if (!repIds.size) return buffer;

  // 3. Empty the items list of each clearance shape representation.
  let modified = text;
  for (const repId of repIds)
    modified = modified.replace(
      new RegExp(
        `(#${repId}\\s*=\\s*IFCSHAPEREPRESENTATION\\s*\\([^,]+,[^,]+,[^,]+,)\\([^)]*\\)`,
        'gi'
      ),
      '$1()'
    );

  return new TextEncoder().encode(modified);
}

// -----------------------------------------------------------------------
// Apply selector= by isolating matching IFC elements.
//
// Simple selectors (IFC type names, "+" union): handled client-side.
//   "IfcWall"  "IfcWall+IfcSlab"
//   Subtypes matched by prefix: IfcWall matches IfcWallStandardCase.
//
// Complex selectors (property filters, name, GlobalId, pset): resolved
//   server-side via GET /select, then applied by GUID in the viewer.
//   Requires the ifcurl service to be running.
// -----------------------------------------------------------------------

// Apply a fragment material style to the given items via FragmentsManager.
async function applyFragmentStyle(components, items, color, opacity) {
  const fragments = components.get(OBC.FragmentsManager);
  await fragments.resetHighlight();
  if (Object.keys(items).length > 0) {
    await fragments.highlight(
      { customId: "sel", color, opacity, transparent: opacity < 1 },
      items
    );
  }
  await fragments.core.update(true);
}

async function applySelector(components, model, selectorStr, srcUrl, visibility = "highlight") {
  if (!selectorStr) return;

  const hider = components.get(OBC.Hider);

  if (isSimpleTypeSelector(selectorStr)) {
    const userTypes = selectorStr.split("+").map(p => p.trim().toUpperCase());
    const classifier = components.get(OBC.Classifier);
    await classifier.byCategory();
    const categoryGroups = classifier.list.get("Categories");
    if (!categoryGroups) return;
    const matchingCategories = [];
    for (const [cat] of categoryGroups) {
      if (userTypes.some(t => cat === t || cat.startsWith(t)))
        matchingCategories.push(cat);
    }
    if (!matchingCategories.length) return;
    const matching = await classifier.find({ Categories: matchingCategories });
    if (!matching || Object.keys(matching).length === 0) return;
    if (visibility === "ghost") {
      // Dim non-selected; selected appear normally.
      const allItems = await classifier.find({ Categories: [...categoryGroups.keys()] });
      await applyFragmentStyle(components, subtractIdMap(allItems, matching),
                               new THREE.Color(0x888888), 0.15);
    } else if (visibility === "isolate") {
      await hider.isolate(matching);
    } else if (visibility === "clash") {
      await applyFragmentStyle(components, matching, new THREE.Color(0xdc3232), 1);
    } else {
      // highlight (default): colour-overlay the selected elements, all others visible.
      await applyFragmentStyle(components, matching, new THREE.Color(0xff8800), 1);
    }
    return;
  }

  // Complex selector: ask the service to resolve it to GUIDs.
  statusEl.textContent = "Resolving selector…";
  let guids;
  try {
    const resp = await fetch(`/select?url=${encodeURIComponent(srcUrl)}`);
    if (!resp.ok) {
      const detail = await resp.text().catch(() => resp.statusText);
      statusEl.textContent = `Selector error: ${detail}`;
      return;
    }
    guids = (await resp.json()).guids ?? [];
  } catch (_) {
    statusEl.textContent = "Complex selector requires the ifcurl service — /select not reachable";
    return;
  }

  if (!guids.length) return;

  const localIds = (await model.getLocalIdsByGuids(guids)).filter(id => id !== null);
  if (!localIds.length) return;

  const matchingMap = { [model.modelId]: new Set(localIds) };
  if (visibility === "ghost") {
    const allVisible = await hider.getVisibilityMap(true);
    await applyFragmentStyle(components, subtractIdMap(allVisible, matchingMap),
                             new THREE.Color(0x888888), 0.15);
  } else if (visibility === "isolate") {
    await hider.isolate(matchingMap);
  } else if (visibility === "clash") {
    await applyFragmentStyle(components, matchingMap, new THREE.Color(0xdc3232), 1);
  } else {
    await applyFragmentStyle(components, matchingMap, new THREE.Color(0xff8800), 1);
  }
}

// -----------------------------------------------------------------------
// Fetch /query results and display them in the query panel.
// -----------------------------------------------------------------------
async function applyQuery(srcUrl, queryPath) {
  statusEl.textContent = "Querying…";
  let data;
  try {
    const resp = await fetch(`/query?url=${encodeURIComponent(srcUrl)}`);
    if (!resp.ok) {
      const detail = await resp.text().catch(() => resp.statusText);
      statusEl.textContent = `Query error: ${detail}`;
      return;
    }
    data = await resp.json();
  } catch (_) {
    statusEl.textContent = "Query requires the ifcurl service — /query not reachable";
    return;
  }

  statusEl.textContent = "";
  const entries = Object.entries(data);
  if (!entries.length) return;

  queryPanelTitle.textContent = queryPath;
  queryValHeader.textContent  = queryPath;
  queryTbody.innerHTML = "";
  for (const [guid, value] of entries) {
    const tr = document.createElement("tr");
    const tdGuid = document.createElement("td");
    tdGuid.textContent = guid;
    const tdVal = document.createElement("td");
    tdVal.textContent = value;
    tr.appendChild(tdGuid);
    tr.appendChild(tdVal);
    queryTbody.appendChild(tr);
  }
  queryPanel.style.display = "";
}

// -----------------------------------------------------------------------
// Place the camera from ifc:// URL camera=/fov=/scale= params.
// obcCamera is the OBC OrthoPerspectiveCamera instance (world.camera).
// -----------------------------------------------------------------------
async function applyCameraParam(controls, obcCamera, url) {
  const qIdx = url.indexOf("?");
  if (qIdx < 0) return false;
  const qs = new URLSearchParams(url.slice(qIdx + 1));

  const camStr = qs.get("camera");
  if (!camStr) return false;
  const v = camStr.split(",").map(Number);
  if (v.length < 9 || v.some(isNaN)) return false;

  const pos    = toThree(v[0], v[1], v[2]);
  const dir    = toThree(v[3], v[4], v[5]);
  const target = pos.clone().add(dir);
  await controls.setLookAt(pos.x, pos.y, pos.z, target.x, target.y, target.z, false);

  const fovStr   = qs.get("fov");
  const scaleStr = qs.get("scale");
  if (scaleStr) {
    // Switch to orthographic projection and scale to match the URL's
    // ViewToWorldScale (height of view in world units).
    await obcCamera.projection.set("Orthographic");
    const s = parseFloat(scaleStr);
    if (!isNaN(s)) {
      const ortho = obcCamera.threeOrtho;
      const currentHeight = ortho.top - ortho.bottom;
      if (currentHeight > 0) {
        const ratio = s / currentHeight;
        ortho.top    *= ratio;
        ortho.bottom *= ratio;
        ortho.left   *= ratio;
        ortho.right  *= ratio;
        ortho.updateProjectionMatrix();
      }
    }
    fovInput.value = "";
  } else if (fovStr) {
    const persp = obcCamera.threePersp;
    persp.fov = Math.max(10, Math.min(120, parseFloat(fovStr)));
    persp.updateProjectionMatrix();
    fovInput.value = Math.round(persp.fov);
  }
  return true;
}

// -----------------------------------------------------------------------
// BCF 2.1 export.
// With a selector: delegates to POST /bcf so the service resolves component
// GUIDs server-side.  Fails visibly if the service is unreachable — no
// silent fallback to a GUIDless file.
// Without a selector: builds the zip client-side (camera + clips only).
// -----------------------------------------------------------------------
async function generateBcf(title, comment) {
  if (threeCamera) syncCameraUrl(threeCamera);
  const src = urlInput.value.trim();
  if (!src) return;

  const qIdx = src.indexOf("?");
  const qs   = new URLSearchParams(qIdx < 0 ? "" : src.slice(qIdx + 1));

  const camV      = (qs.get("camera") ?? "").split(",").map(Number);
  const cam       = camV.length === 9 && !camV.some(isNaN) ? camV : null;
  const fov       = cam ? (parseFloat(qs.get("fov")) || null) : null;
  const scale     = cam ? (parseFloat(qs.get("scale")) || null) : null;
  const clips     = qs.getAll("clip")
    .map(s => s.split(",").map(Number))
    .filter(v => v.length === 6 && !v.some(isNaN));
  const selector   = qs.get("selector") || "";
  const visibility = qs.get("visibility") || "highlight";

  if (selector) {
    try {
      const resp = await fetch("/bcf", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: src, title: title || "IFC View", comment }),
      });
      if (!resp.ok) {
        const detail = await resp.text().catch(() => resp.statusText);
        statusEl.textContent = `BCF export failed: ${detail}`;
        return;
      }
      const blob = await resp.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "view.bcf";
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (_) {
      statusEl.textContent = "BCF export requires the ifcurl service — /bcf not reachable";
    }
    return;
  }

  // Capture a snapshot of the current view for the BCF zip.
  let snapshotB64 = null;
  if (rendererCanvas) {
    try {
      snapshotB64 = rendererCanvas.toDataURL("image/png").split(",")[1];
    } catch (_) { /* context lost or cross-origin — skip */ }
  }

  const topicGuid = crypto.randomUUID();
  const vpGuid    = cam ? crypto.randomUUID() : null;
  const now       = new Date().toISOString().replace(/\.\d{3}Z$/, "Z");

  function esc(s) {
    return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;")
                    .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }
  function xyz(tag, a, b, c) {
    return `<${tag}><X>${a}</X><Y>${b}</Y><Z>${c}</Z></${tag}>`;
  }

  const versionXml = `<?xml version="1.0" encoding="utf-8"?>
<Version VersionId="2.1" xsi:noNamespaceSchemaLocation="https://raw.githubusercontent.com/buildingSMART/BCF-XML/release_2_1/Schemas/version.xsd" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <DetailedVersion>2.1</DetailedVersion>
</Version>`;

  let viewpointXml = null;
  if (vpGuid && cam) {
    const [px,py,pz, dx,dy,dz, ux,uy,uz] = cam;
    const camXml = fov != null
      ? `<PerspectiveCamera>
    ${xyz("CameraViewPoint",px,py,pz)}
    ${xyz("CameraDirection",dx,dy,dz)}
    ${xyz("CameraUpVector",ux,uy,uz)}
    <FieldOfView>${fov}</FieldOfView>
  </PerspectiveCamera>`
      : scale != null
      ? `<OrthogonalCamera>
    ${xyz("CameraViewPoint",px,py,pz)}
    ${xyz("CameraDirection",dx,dy,dz)}
    ${xyz("CameraUpVector",ux,uy,uz)}
    <ViewToWorldScale>${scale}</ViewToWorldScale>
  </OrthogonalCamera>`
      : "";
    const clipsXml = clips.length
      ? `<ClippingPlanes>
    ${clips.map(([cx,cy,cz,nx,ny,nz]) =>
        `<ClippingPlane>${xyz("Location",cx,cy,cz)}${xyz("Direction",nx,ny,nz)}</ClippingPlane>`
      ).join("\n    ")}
  </ClippingPlanes>` : "";
    // Reflect visibility mode: isolate → hide everything by default,
    // ghost/highlight → show everything by default.
    const defaultVis = visibility === "isolate" ? "false" : "true";
    viewpointXml = `<?xml version="1.0" encoding="utf-8"?>
<VisualizationInfo Guid="${vpGuid}">
  <Components><Visibility DefaultVisibility="${defaultVis}"/></Components>
  ${camXml}
  ${clipsXml}
</VisualizationInfo>`;
  }

  const desc = selector
    ? `${esc(src)}\nselector: ${esc(selector)}`
    : esc(src);
  const vpEntry  = vpGuid
    ? `<Viewpoints Guid="${vpGuid}"><Viewpoint>viewpoint.bcfv</Viewpoint>` +
      (snapshotB64 ? `<Snapshot>snapshot.png</Snapshot>` : "") +
      `</Viewpoints>`
    : "";
  const cmtEntry = comment
    ? `<Comment Guid="${crypto.randomUUID()}">
    <Date>${now}</Date><Author>anonymous</Author>
    <Comment>${esc(comment)}</Comment>
    ${vpGuid ? `<Viewpoint Guid="${vpGuid}"/>` : ""}
  </Comment>` : "";

  const markupXml = `<?xml version="1.0" encoding="utf-8"?>
<Markup>
  <Topic Guid="${topicGuid}" TopicType="Coordination" TopicStatus="Open">
    <Title>${esc(title || "IFC View")}</Title>
    <Description>${desc}</Description>
    <CreationDate>${now}</CreationDate>
    <CreationAuthor>anonymous</CreationAuthor>
    ${vpEntry}
  </Topic>
  ${cmtEntry}
</Markup>`;

  const zip = new JSZip();
  zip.file("bcf.version", versionXml);
  zip.folder(topicGuid).file("markup.bcf", markupXml);
  if (vpGuid && viewpointXml) zip.folder(topicGuid).file("viewpoint.bcfv", viewpointXml);
  if (snapshotB64) zip.folder(topicGuid).file("snapshot.png", snapshotB64, { base64: true });

  const blob = await zip.generateAsync({ type: "blob" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "view.bcf";
  a.click();
  URL.revokeObjectURL(a.href);
}

// -----------------------------------------------------------------------
// BCF 2.1 import — read a .bcf zip, extract the first viewpoint, apply
// camera/clips/GUIDs to the current ifc:// URL and reload the viewer.
// -----------------------------------------------------------------------
async function importBcf(file) {
  const src = urlInput.value.trim();
  if (!src) {
    statusEl.textContent = "Load a model first, then import a BCF viewpoint";
    return;
  }

  statusEl.textContent = "Importing BCF…";
  try {
    const zip = await JSZip.loadAsync(file);

    // Find the first viewpoint.bcfv entry.
    const vpPath = Object.keys(zip.files).find(p => p.endsWith("viewpoint.bcfv"));
    if (!vpPath) {
      statusEl.textContent = "BCF: no viewpoint found in file";
      return;
    }

    const vpXml = await zip.file(vpPath).async("text");
    const doc = new DOMParser().parseFromString(vpXml, "text/xml");

    const getXYZ = (parent, tag) => {
      const el = parent.querySelector(tag);
      if (!el) return null;
      const v = ["X", "Y", "Z"].map(c => parseFloat(el.querySelector(c)?.textContent));
      return v.some(isNaN) ? null : v;
    };

    // Camera
    let camera = null, fov = null, scale = null;
    const perspCam = doc.querySelector("PerspectiveCamera");
    const orthoCam = doc.querySelector("OrthogonalCamera");
    const camEl    = perspCam || orthoCam;
    if (camEl) {
      const pos = getXYZ(camEl, "CameraViewPoint");
      const dir = getXYZ(camEl, "CameraDirection");
      const up  = getXYZ(camEl, "CameraUpVector");
      if (pos && dir && up) {
        camera = [...pos, ...dir, ...up];
        if (perspCam) {
          fov = parseFloat(perspCam.querySelector("FieldOfView")?.textContent);
          if (isNaN(fov)) fov = null;
        } else {
          scale = parseFloat(orthoCam.querySelector("ViewToWorldScale")?.textContent);
          if (isNaN(scale)) scale = null;
        }
      }
    }

    // Clipping planes
    const clips = [];
    for (const cp of doc.querySelectorAll("ClippingPlane")) {
      const loc = getXYZ(cp, "Location");
      const dir = getXYZ(cp, "Direction");
      if (loc && dir) clips.push([...loc, ...dir]);
    }

    // Component GUIDs from Selection
    const guids = Array.from(doc.querySelectorAll("Components Selection Component"))
      .map(el => el.getAttribute("IfcGuid"))
      .filter(Boolean);

    // Build updated URL: keep repo/ref/path, replace viewpoint params.
    const qIdx = src.indexOf("?");
    const base = qIdx < 0 ? src : src.slice(0, qIdx);
    const qs   = new URLSearchParams(qIdx < 0 ? "" : src.slice(qIdx + 1));

    qs.delete("camera"); qs.delete("fov"); qs.delete("scale"); qs.delete("clip");
    qs.delete("selector"); qs.delete("visibility");

    if (camera) {
      const f = v => parseFloat(v.toFixed(4));
      qs.set("camera", camera.map(f).join(","));
      if (fov   != null) qs.set("fov",   fov.toString());
      if (scale != null) qs.set("scale", scale.toString());
    }
    for (const clip of clips) {
      const f = v => parseFloat(v.toFixed(4));
      qs.append("clip", clip.map(f).join(","));
    }
    if (guids.length > 0) qs.set("selector", guids.join("+"));

    const newUrl = base + "?" + qs.toString()
      .replace(/%2C/g, ",").replace(/%2B/g, "+").replace(/%24/g, "$");
    loadUrl(newUrl);
  } catch (err) {
    statusEl.textContent = `BCF import error: ${err.message}`;
    console.error(err);
  }
}

// -----------------------------------------------------------------------
// Element properties panel
// -----------------------------------------------------------------------
function showPropsPanel(typeName, name, guid) {
  propsType.textContent = typeName ?? "";
  propsName.textContent = name ?? "";
  propsGuid.textContent = guid ?? "";
  propsPanel.style.display = "";
}

// -----------------------------------------------------------------------
// Model metadata panel population.
// -----------------------------------------------------------------------
async function populateMetaPanel(components) {
  const classifier = components.get(OBC.Classifier);
  const hider      = components.get(OBC.Hider);

  // --- Type breakdown ---
  await classifier.byCategory();
  const categories = classifier.list.get("Categories");
  const types = [];
  if (categories) {
    for (const [name, groupData] of categories) {
      const fragmentMap = await groupData.get();
      let count = 0;
      for (const ids of Object.values(fragmentMap)) { if (ids) count += ids.size; }
      if (count > 0) types.push({ name, count });
    }
    types.sort((a, b) => b.count - a.count);
  }

  // --- Storey list ---
  await classifier.byIfcBuildingStorey();
  const storeyGroups = classifier.list.get("Storeys");
  const storeyNames = storeyGroups ? [...storeyGroups.keys()] : [];

  // --- Render ---
  const html = [];

  if (types.length) {
    html.push("<h3>Types</h3>");
    for (const { name, count } of types) {
      html.push(
        `<div class="meta-row">` +
        `<span class="meta-name">${name}</span>` +
        `<span class="meta-count">${count}</span>` +
        `</div>`
      );
    }
  }

  if (storeyNames.length) {
    html.push("<h3>Storeys</h3>");
    html.push(
      `<div class="meta-row storey-row active" data-storey="">` +
      `<span class="meta-name">All storeys</span>` +
      `</div>`
    );
    for (const name of storeyNames) {
      html.push(
        `<div class="meta-row storey-row" data-storey="${name.replace(/"/g, "&quot;")}">` +
        `<span class="meta-name">${name}</span>` +
        `</div>`
      );
    }
  }

  if (!types.length && !storeyNames.length) {
    html.push('<div class="meta-row"><span class="meta-name" style="color:#505058">No metadata</span></div>');
  }

  metaPanel.innerHTML = html.join("");

  // Wire up storey click-to-isolate.
  const allRow = metaPanel.querySelector(".storey-row[data-storey='']");
  let activeRow = allRow;
  for (const row of metaPanel.querySelectorAll(".storey-row")) {
    row.addEventListener("click", async () => {
      const name = row.dataset.storey;
      if (!name || activeRow === row) {
        // "All storeys" row or clicking the active row: show all.
        activeRow?.classList.remove("active");
        activeRow = allRow;
        allRow?.classList.add("active");
        await hider.set(true);
      } else {
        activeRow?.classList.remove("active");
        activeRow = row;
        row.classList.add("active");
        const found = await classifier.find({ Storeys: [name] });
        await hider.isolate(found);
      }
    });
  }
}

// -----------------------------------------------------------------------
// Ref picker: populate <datalist id="ref-list"> with branches + tags.
// -----------------------------------------------------------------------
async function populateRefList(host, repoSuffix) {
  const proto = host.startsWith("localhost") ? "http" : "https";
  const base = `${proto}://${host}/api/v1/repos/${repoSuffix}`;
  const sig = AbortSignal.timeout(4000);
  const [branchRes, tagRes] = await Promise.allSettled([
    fetch(`${base}/branches?limit=50`, { signal: sig }),
    fetch(`${base}/tags?limit=50`,     { signal: sig }),
  ]);
  const dl = document.getElementById("ref-list");
  if (branchRes.status === "fulfilled" && branchRes.value.ok) {
    const branches = await branchRes.value.json().catch(() => []);
    for (const b of (branches ?? [])) {
      const opt = document.createElement("option");
      opt.value = `heads/${b.name}`;
      dl.appendChild(opt);
    }
  }
  if (tagRes.status === "fulfilled" && tagRes.value.ok) {
    const tags = await tagRes.value.json().catch(() => []);
    for (const t of (tags ?? [])) {
      const opt = document.createElement("option");
      opt.value = `tags/${t.name}`;
      dl.appendChild(opt);
    }
  }
}

// -----------------------------------------------------------------------
// Commit-pin indicator helpers.
// -----------------------------------------------------------------------
async function fetchBranchForCommit(host, repoSuffix, hash) {
  const proto = host.startsWith("localhost") ? "http" : "https";
  const apiBase = `${proto}://${host}/api/v1/repos/${repoSuffix}`;
  const sig = AbortSignal.timeout(3000);
  const [branchRes, repoRes] = await Promise.allSettled([
    fetch(`${apiBase}/branches?limit=50`, { signal: sig }),
    fetch(apiBase, { signal: sig }),
  ]);
  let defaultBranch = "main";
  if (repoRes.status === "fulfilled" && repoRes.value.ok) {
    const info = await repoRes.value.json().catch(() => null);
    if (info?.default_branch) defaultBranch = info.default_branch;
  }
  if (branchRes.status === "fulfilled" && branchRes.value.ok) {
    const branches = await branchRes.value.json().catch(() => []);
    const h = hash.toLowerCase();
    for (const b of (branches ?? [])) {
      const id = (b.commit?.id ?? "").toLowerCase();
      if (id.startsWith(h) || h.startsWith(id.slice(0, Math.max(h.length, 7)))) {
        return b.name;
      }
    }
  }
  return defaultBranch;
}

// -----------------------------------------------------------------------
// Main
// -----------------------------------------------------------------------
async function main() {
  if (!currentIfcUrl) {
    statusEl.textContent = "Fill in the fields above and press Enter, or paste an ifc:// URL.";
    return;
  }

  statusEl.textContent = "Loading…";

  components       = new OBC.Components();
  const worlds     = components.get(OBC.Worlds);
  world            = worlds.create();
  const container  = document.getElementById("viewer");

  world.scene    = new OBC.SimpleScene(components);
  world.renderer = new OBC.SimpleRenderer(components, container, { preserveDrawingBuffer: true });
  world.camera   = new OBC.OrthoPerspectiveCamera(components);
  rendererCanvas = world.renderer.three.domElement;
  world.scene.setup();
  components.init();

  // Register a raycaster for this world so OBC.Clipper can raycast
  // against the model surface when creating interactive clip planes.
  components.get(OBC.Raycasters).get(world);

  cameraControls = world.camera.controls;
  threeCamera    = world.camera.three;

  // Firefox blocks cross-origin module workers; wrap in a blob URL.
  const fragments  = components.get(OBC.FragmentsManager);
  const workerResp = await fetch("/assets/fragments-worker.js");
  fragments.init(URL.createObjectURL(
    new Blob([await workerResp.text()], { type: "application/javascript" })
  ));

  const ifcLoader = components.get(OBC.IfcLoader);
  await ifcLoader.setup({
    autoSetWasm: false,
    wasm: { path: "/assets/", absolute: true },
    // Keep geometry in IFC world coords so camera= URL params (which are
    // in IFC world coords per spec) match without a COORDINATE_TO_ORIGIN shift.
    webIfc: { COORDINATE_TO_ORIGIN: false },
  });

  statusEl.textContent = "Fetching IFC file…";
  let buffer;
  try {
    const resp = await fetch(toRawUrl(currentIfcUrl));
    if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
    const contentLength = resp.headers.get("Content-Length");
    const total = contentLength ? parseInt(contentLength, 10) : 0;
    const reader = resp.body?.getReader();
    if (reader) {
      const chunks = [];
      let received = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        chunks.push(value);
        received += value.length;
        if (total > 0) {
          statusEl.textContent = `Fetching… ${Math.round(received * 100 / total)}%`;
        } else {
          statusEl.textContent = `Fetching… ${(received / 1048576).toFixed(1)} MB`;
        }
      }
      buffer = new Uint8Array(received);
      let offset = 0;
      for (const chunk of chunks) { buffer.set(chunk, offset); offset += chunk.length; }
    } else {
      buffer = new Uint8Array(await resp.arrayBuffer());
    }
  } catch (err) {
    statusEl.textContent = `Fetch error: ${err.message}`;
    return;
  }

  buffer = removeClearanceGeometry(buffer);

  statusEl.textContent = "Parsing IFC…";
  try {
    const model  = await ifcLoader.load(buffer, true, "model");
    loadedModel  = model;
    modelRawUrl  = toRawUrl(currentIfcUrl);
    world.scene.three.add(model.object);

    const hasCam = await applyCameraParam(cameraControls, world.camera, currentIfcUrl);
    threeCamera = world.camera.three; // refresh: may now be ortho
    if (!hasCam && !model.box.isEmpty()) {
      await cameraControls.fitToBox(model.box, false);
    }
    model.useCamera(threeCamera);

    const qIdxPost = currentIfcUrl.indexOf("?");
    const qsPost = new URLSearchParams(
      qIdxPost < 0 ? "" : currentIfcUrl.slice(qIdxPost + 1)
    );

    const clips = qsPost.getAll("clip")
      .map(s => s.split(",").map(Number))
      .filter(v => v.length === 6 && !v.some(isNaN));
    setupClipper(components, world, clips);

    const visibility  = qsPost.get("visibility") ?? "highlight";
    const selectorStr = qsPost.get("selector") ?? "";
    const queryStr    = qsPost.get("query") ?? "";

    // Set up Highlighter before selector so visibility=highlight can use it.
    let highlighter = null;
    try {
      highlighter = components.get(OBCF.Highlighter);
      highlighter.setup({ world });
      highlighter.events.select.onHighlight.add(async (fragmentIdMap) => {
        for (const localIds of Object.values(fragmentIdMap)) {
          if (!localIds) continue;
          const localId = [...localIds][0];
          if (localId == null) continue;
          const item = model.getItem(localId);
          if (!item) continue;
          const [attrs, [category], guid] = await Promise.all([
            item.getAttributes(),
            model.threads.invoke(model.modelId, "getItemsCategories", [[localId]]),
            item.getGuid(),
          ]);
          if (attrs) showPropsPanel(
            category ?? "",
            attrs.get("Name")?.value ?? attrs.get("LongName")?.value ?? "",
            guid ?? "",
          );
          return;
        }
      });
      highlighter.events.select.onClear.add(() => {
        propsPanel.style.display = "none";
      });
    } catch (err) {
      console.warn("Highlighter unavailable:", err.message);
    }

    if (selectorStr) {
      statusEl.textContent = "Applying selector…";
      await applySelector(components, model, selectorStr, currentIfcUrl, visibility);
    }

    if (selectorStr && queryStr) {
      await applyQuery(currentIfcUrl, queryStr);
    }

    await fragments.core.update(true);

    syncCameraUrl(threeCamera);
    cameraControls.addEventListener("rest", () => syncCameraUrl(threeCamera));
    // Wait for the renderer to paint at least one frame before fading the overlay,
    // otherwise the WebGL canvas may still be blank when the fade starts.
    requestAnimationFrame(() => requestAnimationFrame(() => removeSnapshot()));

    populateMetaPanel(components).catch(err => {
      console.warn("Metadata panel unavailable:", err.message);
    });

    statusEl.textContent = "";
  } catch (err) {
    removeSnapshot();
    statusEl.textContent = `Render error: ${err.message}`;
    console.error(err);
  }
}

// bfcache restore can leave the WebGL canvas blank. Reload fresh so the
// inline snapshot script runs and the model reloads properly.
window.addEventListener("pageshow", (e) => { if (e.persisted) window.location.reload(); });

main();
