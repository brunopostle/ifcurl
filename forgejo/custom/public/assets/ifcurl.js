// Copyright 2026 The Forgejo Authors. All rights reserved.
// SPDX-License-Identifier: MIT
//
// ifcurl — inject "View in 3D" button on .ifc file view and history pages.
//
// Deploy to: /var/lib/forgejo/custom/public/assets/ifcurl.js
//
// Uses commit-hash hrefs (always unambiguous) to construct ifc:// URLs.
import { parseCommitHref } from "./viewer-util.js";

const VIEWER_BASE = "/assets/viewer.html";

function makeViewerLink(info, label, cls) {
  const host   = window.location.host;
  const ifcUrl = "ifc://" + host + "/" + info.repoPath
               + "@" + info.hash
               + "?path=" + encodeURIComponent(info.treePath);
  const a = document.createElement("a");
  a.href       = VIEWER_BASE + "?url=" + encodeURIComponent(ifcUrl);
  a.target     = "_blank";
  a.rel        = "noopener noreferrer";
  a.className  = cls || "ui mini basic button";
  a.textContent = label;
  return a;
}

// Replace <a href="ifc://..."> links in rendered markdown with preview figures.
// Handles [title](ifc://...) links produced by Goldmark's standard link parser —
// no Go extension required.  Skips anchors already inside .ifcurl-preview (those
// were rendered server-side by ifc_url.go when the patched binary is in use).
function replaceIfcAnchors() {
  const origin = window.location.origin;
  document.querySelectorAll('a[href^="ifc://"]').forEach(function(a) {
    if (a.closest(".ifcurl-preview")) return;
    const ifcUrl = a.getAttribute("href");
    const img = document.createElement("img");
    img.src = origin + "/preview?url=" + encodeURIComponent(ifcUrl);
    img.alt = "IFC preview";
    img.loading = "lazy";
    img.style.maxWidth = "100%";
    const imgLink = document.createElement("a");
    imgLink.href = VIEWER_BASE + "?url=" + encodeURIComponent(ifcUrl);
    imgLink.title = ifcUrl;
    imgLink.target = "_blank";
    imgLink.rel = "noopener noreferrer";
    imgLink.appendChild(img);
    const code = document.createElement("code");
    code.textContent = ifcUrl;
    const urlLink = document.createElement("a");
    urlLink.href = ifcUrl;
    urlLink.appendChild(code);
    const caption = document.createElement("figcaption");
    caption.appendChild(urlLink);
    const figure = document.createElement("figure");
    figure.className = "ifcurl-preview";
    figure.appendChild(imgLink);
    figure.appendChild(caption);
    a.parentNode.replaceChild(figure, a);
  });
}

function init() {
  const host = window.location.host;

  replaceIfcAnchors();

  // -----------------------------------------------------------------------
  // Case 1: single file view — permalink button in .file-header-right.
  // -----------------------------------------------------------------------
  const btnGroup = document.querySelector(".file-header-right .ui.buttons");
  if (btnGroup) {
    // Permalink href or current URL when already at a commit.
    const a = document.querySelector('a[href*="/src/commit/"]');
    const href = a ? a.getAttribute("href") : window.location.pathname;
    const info = parseCommitHref(href);
    if (info && info.treePath.toLowerCase().endsWith(".ifc")) {
      btnGroup.appendChild(makeViewerLink(info, "View in 3D"));
    }
    return;
  }

  // -----------------------------------------------------------------------
  // Case 3: PR diff page — inject a render_diff image below the header of
  // each .ifc file in the diff.  Requires Nginx to proxy /render_diff to
  // the ifcurl preview service (see forgejo/README.md).
  // -----------------------------------------------------------------------
  const prMatch = window.location.pathname.match(/^\/([^/]+\/[^/]+)\/pulls\/(\d+)\/files$/);
  if (prMatch) {
    const prRepoPath = prMatch[1];
    const prNum = prMatch[2];
    fetch("/api/v1/repos/" + prRepoPath + "/pulls/" + prNum)
      .then(function(r) { return r.ok ? r.json() : Promise.reject(r.status); })
      .then(function(pr) {
        const baseSha = pr.base && pr.base.sha;
        const headSha = pr.head && pr.head.sha;
        if (!baseSha || !headSha) return;
        const origin = window.location.origin;
        document.querySelectorAll('a[href*="/src/commit/"]').forEach(function(a) {
          const info = parseCommitHref(a.getAttribute("href"));
          if (!info || !info.treePath.toLowerCase().endsWith(".ifc")) return;
          if (info.hash !== headSha) return;
          const baseIfc = "ifc://" + host + "/" + prRepoPath + "@" + baseSha
                      + "?path=" + encodeURIComponent(info.treePath);
          const headIfc = "ifc://" + host + "/" + prRepoPath + "@" + headSha
                      + "?path=" + encodeURIComponent(info.treePath);
          const img = document.createElement("img");
          img.src = origin + "/render_diff"
                  + "?base=" + encodeURIComponent(baseIfc)
                  + "&head=" + encodeURIComponent(headIfc);
          img.style.cssText = "max-width:100%;display:block";
          img.alt = info.treePath;
          const imgLink = document.createElement("a");
          imgLink.href = VIEWER_BASE + "?url=" + encodeURIComponent(headIfc);
          imgLink.target = "_blank";
          imgLink.rel = "noopener noreferrer";
          imgLink.appendChild(img);
          const code = document.createElement("code");
          code.style.cssText = "display:block;font-size:0.85em;word-break:break-all;margin-top:4px";
          code.textContent = headIfc;
          const urlLink = document.createElement("a");
          urlLink.href = headIfc;
          urlLink.appendChild(code);
          const container = document.createElement("div");
          container.style.cssText = "margin:8px 0";
          container.appendChild(imgLink);
          container.appendChild(urlLink);
          const fileBox = a.closest(".diff-file-box");
          const header = fileBox && fileBox.querySelector(".file-header");
          if (header) {
            header.insertAdjacentElement("afterend", container);
          } else {
            a.insertAdjacentElement("afterend", container);
          }
        });
      })
      .catch(function() {}); // silently skip on API error
    return;
  }

  // -----------------------------------------------------------------------
  // Case 4: individual commit page — inject a render_diff image (or plain
  // preview for added/deleted files) below the header of each .ifc file
  // in the diff.
  // -----------------------------------------------------------------------
  const commitMatch = window.location.pathname.match(/^\/([^/]+\/[^/]+)\/commit\/([0-9a-f]{40})$/i);
  if (commitMatch) {
    const repoPath = commitMatch[1];
    const headSha = commitMatch[2];
    const origin = window.location.origin;

    // Extract parent SHA from the "parent" commit link at the top of the page.
    let parentSha = null;
    const parentLink = document.querySelector("a.primary.sha.label[href*='/commit/']");
    if (parentLink) {
      const pm = parentLink.getAttribute("href").match(/\/commit\/([0-9a-f]{40})$/i);
      if (pm) parentSha = pm[1];
    }

    document.querySelectorAll(".diff-file-box").forEach(function(box) {
      const newFile = box.getAttribute("data-new-filename") || "";
      const oldFile = box.getAttribute("data-old-filename") || "";
      const newIsIfc = newFile.toLowerCase().endsWith(".ifc");
      const oldIsIfc = oldFile.toLowerCase().endsWith(".ifc");

      let ifcPath, imgSrc, viewerIfc;
      if (newIsIfc && oldIsIfc && newFile === oldFile && parentSha) {
        // Modified: show colour-coded diff
        ifcPath = newFile;
        const baseIfc = "ifc://" + host + "/" + repoPath + "@" + parentSha + "?path=" + encodeURIComponent(ifcPath);
        viewerIfc = "ifc://" + host + "/" + repoPath + "@" + headSha + "?path=" + encodeURIComponent(ifcPath);
        imgSrc = origin + "/render_diff?base=" + encodeURIComponent(baseIfc) + "&head=" + encodeURIComponent(viewerIfc);
      } else if (newIsIfc) {
        // Added (or renamed to .ifc): plain preview of new version
        ifcPath = newFile;
        viewerIfc = "ifc://" + host + "/" + repoPath + "@" + headSha + "?path=" + encodeURIComponent(ifcPath);
        imgSrc = origin + "/preview?url=" + encodeURIComponent(viewerIfc);
      } else if (oldIsIfc && parentSha) {
        // Deleted (or renamed from .ifc): plain preview of old version
        ifcPath = oldFile;
        viewerIfc = "ifc://" + host + "/" + repoPath + "@" + parentSha + "?path=" + encodeURIComponent(ifcPath);
        imgSrc = origin + "/preview?url=" + encodeURIComponent(viewerIfc);
      } else {
        return;
      }

      const img = document.createElement("img");
      img.src = imgSrc;
      img.style.cssText = "max-width:100%;display:block";
      img.alt = ifcPath;
      const imgLink = document.createElement("a");
      imgLink.href = VIEWER_BASE + "?url=" + encodeURIComponent(viewerIfc);
      imgLink.target = "_blank";
      imgLink.rel = "noopener noreferrer";
      imgLink.appendChild(img);
      const code = document.createElement("code");
      code.style.cssText = "display:block;font-size:0.85em;word-break:break-all;margin-top:4px";
      code.textContent = viewerIfc;
      const urlLink = document.createElement("a");
      urlLink.href = viewerIfc;
      urlLink.appendChild(code);
      const container = document.createElement("div");
      container.style.cssText = "margin:8px 0";
      container.appendChild(imgLink);
      container.appendChild(urlLink);
      const header = box.querySelector(".diff-file-header");
      if (header) header.insertAdjacentElement("afterend", container);
    });
    return;
  }

  // -----------------------------------------------------------------------
  // Case 2: file history page — inject a "3D" link next to each commit's
  // browse-file link (/src/commit/{hash}/{treepath}).
  // -----------------------------------------------------------------------
  document.querySelectorAll('a[href*="/src/commit/"]').forEach(function(a) {
    const info = parseCommitHref(a.getAttribute("href"));
    if (!info || !info.treePath.toLowerCase().endsWith(".ifc")) return;
    const btn = makeViewerLink(info, "3D");
    btn.style.marginLeft = "4px";
    a.insertAdjacentElement("afterend", btn);
  });
}

// ES modules are deferred by default, so the DOM is always ready here.
init();
