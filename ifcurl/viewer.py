# IFC URL — web IFC viewer page
# Copyright (C) 2026 Bruno Postle <bruno@postle.net>
#
# SPDX-License-Identifier: LGPL-3.0-or-later

"""Self-contained HTML viewer page served at GET /viewer.

Loads @thatopen/components v3 from CDN via importmap and renders an IFC model
in the browser using WebGL.

CDN notes
---------
- ``web-ifc`` is loaded directly from unpkg (not via esm.sh) to avoid esm.sh
  injecting a Node.js process polyfill that makes web-ifc's browser ST-build
  environment check throw "not compiled for this environment".
- ``@thatopen/fragments`` worker is fetched and wrapped in a same-origin blob
  URL because Firefox blocks cross-origin module workers.
- web-ifc@0.0.75 is the latest version with a ``browser`` export condition;
  0.0.76+ dropped it, causing esm.sh to bundle the Node.js build by mistake.
- Without COOP/COEP headers the page is not cross-origin isolated, so
  ``crossOriginIsolated = false`` and web-ifc uses its single-threaded WASM
  build (no SharedArrayBuffer / pthreads needed).
"""

VIEWER_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>IFC Viewer</title>
  <link rel="icon" href="data:,">
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body { background: #1c1c1e; color: #f0f0f0;
           font-family: system-ui, sans-serif; overflow: hidden; }
    #viewer { position: fixed; inset: 0; }
    #toolbar {
      position: fixed; top: 0; left: 0; right: 0; z-index: 10;
      display: flex; align-items: center; gap: 8px; padding: 8px 12px;
      background: rgba(28,28,30,0.85); backdrop-filter: blur(8px);
      border-bottom: 1px solid rgba(255,255,255,0.08);
    }
    #url-display {
      flex: 1; font-size: 12px; color: #a0a0a8; font-family: monospace;
      overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    }
    #status {
      position: fixed; bottom: 12px; left: 50%;
      transform: translateX(-50%); z-index: 10;
      font-size: 13px; color: #a0a0a8;
      background: rgba(28,28,30,0.85);
      padding: 4px 12px; border-radius: 4px;
      pointer-events: none;
    }
    #status:empty { display: none; }
  </style>
  <script type="importmap">
  {
    "imports": {
      "three":                "https://esm.sh/three@0.177.0",
      "three/addons/":        "https://esm.sh/three@0.177.0/examples/jsm/",
      "web-ifc":              "https://unpkg.com/web-ifc@0.0.75/web-ifc-api.js",
      "@thatopen/fragments":  "https://esm.sh/@thatopen/fragments@3.3.1?external=three,web-ifc",
      "@thatopen/components": "https://esm.sh/@thatopen/components@3.3.1?external=three,web-ifc,@thatopen/fragments"
    }
  }
  </script>
</head>
<body>
  <div id="toolbar">
    <span id="url-display"></span>
  </div>
  <div id="viewer"></div>
  <div id="status">Loading\u2026</div>

  <script type="module">
    import * as THREE from "three";
    import * as OBC from "@thatopen/components";

    const params   = new URLSearchParams(window.location.search);
    const ifcUrl   = params.get("url") ?? "";
    const statusEl = document.getElementById("status");
    const urlEl    = document.getElementById("url-display");

    urlEl.textContent = ifcUrl || "No ifc:// URL provided";
    urlEl.title       = ifcUrl;

    // -----------------------------------------------------------------------
    // Rebuild the ifc:// URL with the current camera position, direction, and
    // up vector, update the toolbar display and the browser address bar.
    // -----------------------------------------------------------------------
    function syncCameraUrl(camera) {
      const pos = camera.position;
      const dir = new THREE.Vector3();
      camera.getWorldDirection(dir);
      const up = camera.up;

      const f = v => parseFloat(v.toFixed(4));
      const cameraParam = [
        f(pos.x), f(pos.y), f(pos.z),
        f(dir.x), f(dir.y), f(dir.z),
        f(up.x),  f(up.y),  f(up.z),
      ].join(",");

      const qIdx = ifcUrl.indexOf("?");
      const base = qIdx < 0 ? ifcUrl : ifcUrl.slice(0, qIdx);
      const qs   = new URLSearchParams(qIdx < 0 ? "" : ifcUrl.slice(qIdx + 1));
      qs.set("camera", cameraParam);
      if (camera.isPerspectiveCamera) {
        qs.set("fov", f(camera.fov).toString());
        qs.delete("scale");
      } else {
        // OrthographicCamera: world-space viewport height as scale
        qs.set("scale", f(camera.top - camera.bottom).toString());
        qs.delete("fov");
      }
      const newIfcUrl = base + "?" + qs.toString().replace(/%2C/g, ",");

      urlEl.textContent = newIfcUrl;
      urlEl.title       = newIfcUrl;

      const pageUrl = new URL(window.location.href);
      pageUrl.searchParams.set("url", newIfcUrl);
      history.replaceState(null, "", pageUrl.toString());
    }

    // -----------------------------------------------------------------------
    // Convert an ifc:// URL to a same-origin /proxy?url=... request so the
    // browser never makes a cross-origin request for the IFC bytes.
    //
    // ifc://host:port/org/repo@{hash}?path=file.ifc
    //   → /proxy?url=http%3A%2F%2Fhost%3Aport%2Forg%2Frepo%2Fraw%2Fcommit%2F{hash}%2Ffile.ifc
    // -----------------------------------------------------------------------
    function toProxyUrl(raw) {
      const body     = raw.slice("ifc://".length);
      const atIdx    = body.indexOf("@");
      const qIdx     = body.indexOf("?");
      const hostRepo = body.slice(0, atIdx);
      const ref      = body.slice(atIdx + 1, qIdx < 0 ? undefined : qIdx);
      const qs       = new URLSearchParams(qIdx < 0 ? "" : body.slice(qIdx + 1));
      const filePath = qs.get("path") ?? "";

      const slashIdx = hostRepo.indexOf("/");
      const host     = hostRepo.slice(0, slashIdx);
      const repoPath = hostRepo.slice(slashIdx + 1);

      let refSeg;
      if      (ref.startsWith("heads/")) refSeg = "branch/" + ref.slice(6);
      else if (ref.startsWith("tags/"))  refSeg = "tag/"    + ref.slice(5);
      else                               refSeg = "commit/" + ref;

      const protocol = host.startsWith("localhost") ? "http" : "https";
      const rawUrl   = `${protocol}://${host}/${repoPath}/raw/${refSeg}/${filePath}`;
      return `/proxy?url=${encodeURIComponent(rawUrl)}`;
    }

    // -----------------------------------------------------------------------
    // Main
    // -----------------------------------------------------------------------
    async function main() {
      if (!ifcUrl) {
        statusEl.textContent = "No ifc:// URL provided.";
        return;
      }

      // --- World setup ---
      const components = new OBC.Components();
      const worlds     = components.get(OBC.Worlds);
      const world      = worlds.create();
      const container  = document.getElementById("viewer");

      world.scene    = new OBC.SimpleScene(components);
      world.renderer = new OBC.SimpleRenderer(components, container);
      world.camera   = new OBC.OrthoPerspectiveCamera(components);
      world.scene.setup();
      components.init();

      // --- FragmentsManager: must be init'd before ifcLoader.load() ---
      // Firefox blocks cross-origin module workers; wrap in a blob URL so the
      // worker is always same-origin regardless of where it was fetched from.
      const fragments = components.get(OBC.FragmentsManager);
      const workerResp = await fetch(
        "https://unpkg.com/@thatopen/fragments@3.3.1/dist/Worker/worker.mjs"
      );
      const workerUrl = URL.createObjectURL(
        new Blob([await workerResp.text()], { type: "application/javascript" })
      );
      fragments.init(workerUrl);

      // --- IfcLoader: wasm config goes into setup() ---
      const ifcLoader = components.get(OBC.IfcLoader);
      await ifcLoader.setup({
        autoSetWasm: false,
        wasm: {
          path:     "https://unpkg.com/web-ifc@0.0.75/",
          absolute: true,
        },
      });

      // --- Fetch IFC bytes via proxy ---
      statusEl.textContent = "Fetching IFC file\u2026";
      let buffer;
      try {
        const resp = await fetch(toProxyUrl(ifcUrl));
        if (!resp.ok) throw new Error(`HTTP ${resp.status} ${resp.statusText}`);
        buffer = new Uint8Array(await resp.arrayBuffer());
      } catch (err) {
        statusEl.textContent = `Fetch error: ${err.message}`;
        return;
      }

      // --- Parse and render ---
      statusEl.textContent = "Parsing IFC\u2026";
      try {
        // load() returns a FragmentsModel; model.object is its THREE.Group.
        // The modelId arg must be a string — undefined causes a worker hash error.
        const model = await ifcLoader.load(buffer, true, "model");
        world.scene.three.add(model.object);

        // model.box (THREE.Box3) is populated from the worker's CREATE_MODEL
        // response during _setup(), so it's available before any tiles exist.
        // Fit the camera first so the LOD frustum culling generates tiles for
        // the visible region, then force-flush all pending tile requests.
        const box = model.box;
        if (!box.isEmpty()) {
          await world.camera.controls.fitToBox(box, false);
        }
        model.useCamera(world.camera.three);
        await fragments.core.update(true);

        // Show initial camera position in URL, then keep it live.
        syncCameraUrl(world.camera.three);
        world.camera.controls.addEventListener("rest", () => {
          syncCameraUrl(world.camera.three);
        });

        statusEl.textContent = "";
      } catch (err) {
        statusEl.textContent = `Render error: ${err.message}`;
        console.error(err);
      }
    }

    main();
  </script>
</body>
</html>
"""
