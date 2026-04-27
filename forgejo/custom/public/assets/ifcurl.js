// Copyright 2026 The Forgejo Authors. All rights reserved.
// SPDX-License-Identifier: MIT
//
// ifcurl — inject "View in 3D" button on .ifc file view and history pages.
//
// Deploy to: /var/lib/forgejo/custom/public/assets/ifcurl.js
//
// Uses commit-hash hrefs (always unambiguous) to construct ifc:// URLs.
(function () {
  "use strict";

  var VIEWER_BASE = "/assets/viewer.html";

  // Parse /owner/repo/src/commit/{40hex}/{treepath}
  // Returns {repoPath, hash, treePath} or null.
  function parseCommitHref(href) {
    var m = href.match(/^\/([^/]+\/[^/]+)\/src\/commit\/([0-9a-f]{40})\/(.+)$/i);
    if (!m) return null;
    return { repoPath: m[1], hash: m[2], treePath: m[3] };
  }

  function makeViewerLink(info, label, cls) {
    var host   = window.location.host;
    var ifcUrl = "ifc://" + host + "/" + info.repoPath
               + "@" + info.hash
               + "?path=" + encodeURIComponent(info.treePath);
    var a = document.createElement("a");
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
    var origin = window.location.origin;
    document.querySelectorAll('a[href^="ifc://"]').forEach(function(a) {
      if (a.closest(".ifcurl-preview")) return;
      var ifcUrl = a.getAttribute("href");
      var img = document.createElement("img");
      img.src = origin + "/preview?url=" + encodeURIComponent(ifcUrl);
      img.alt = "IFC preview";
      img.loading = "lazy";
      img.style.maxWidth = "100%";
      var imgLink = document.createElement("a");
      imgLink.href = VIEWER_BASE + "?url=" + encodeURIComponent(ifcUrl);
      imgLink.title = ifcUrl;
      imgLink.target = "_blank";
      imgLink.rel = "noopener noreferrer";
      imgLink.appendChild(img);
      var code = document.createElement("code");
      code.textContent = ifcUrl;
      var urlLink = document.createElement("a");
      urlLink.href = ifcUrl;
      urlLink.appendChild(code);
      var caption = document.createElement("figcaption");
      caption.appendChild(urlLink);
      var figure = document.createElement("figure");
      figure.className = "ifcurl-preview";
      figure.appendChild(imgLink);
      figure.appendChild(caption);
      a.parentNode.replaceChild(figure, a);
    });
  }

  function init() {
    var host = window.location.host;

    replaceIfcAnchors();

    // -----------------------------------------------------------------------
    // Case 1: single file view — permalink button in .file-header-right.
    // -----------------------------------------------------------------------
    var btnGroup = document.querySelector(".file-header-right .ui.buttons");
    if (btnGroup) {
      // Permalink href or current URL when already at a commit.
      var a = document.querySelector('a[href*="/src/commit/"]');
      var href = a ? a.getAttribute("href") : window.location.pathname;
      var info = parseCommitHref(href);
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
    var prMatch = window.location.pathname.match(/^\/([^/]+\/[^/]+)\/pulls\/(\d+)\/files$/);
    if (prMatch) {
      var prRepoPath = prMatch[1];
      var prNum = prMatch[2];
      fetch("/api/v1/repos/" + prRepoPath + "/pulls/" + prNum)
        .then(function(r) { return r.ok ? r.json() : Promise.reject(r.status); })
        .then(function(pr) {
          var baseSha = pr.base && pr.base.sha;
          var headSha = pr.head && pr.head.sha;
          if (!baseSha || !headSha) return;
          var origin = window.location.origin;
          document.querySelectorAll('a[href*="/src/commit/"]').forEach(function(a) {
            var info = parseCommitHref(a.getAttribute("href"));
            if (!info || !info.treePath.toLowerCase().endsWith(".ifc")) return;
            if (info.hash !== headSha) return;
            var baseIfc = "ifc://" + host + "/" + prRepoPath + "@" + baseSha
                        + "?path=" + encodeURIComponent(info.treePath);
            var headIfc = "ifc://" + host + "/" + prRepoPath + "@" + headSha
                        + "?path=" + encodeURIComponent(info.treePath);
            var img = document.createElement("img");
            img.src = origin + "/render_diff"
                    + "?base=" + encodeURIComponent(baseIfc)
                    + "&head=" + encodeURIComponent(headIfc);
            img.style.cssText = "max-width:100%;display:block";
            img.alt = info.treePath;
            var imgLink = document.createElement("a");
            imgLink.href = VIEWER_BASE + "?url=" + encodeURIComponent(headIfc);
            imgLink.target = "_blank";
            imgLink.rel = "noopener noreferrer";
            imgLink.appendChild(img);
            var code = document.createElement("code");
            code.style.cssText = "display:block;font-size:0.85em;word-break:break-all;margin-top:4px";
            code.textContent = headIfc;
            var urlLink = document.createElement("a");
            urlLink.href = headIfc;
            urlLink.appendChild(code);
            var container = document.createElement("div");
            container.style.cssText = "margin:8px 0";
            container.appendChild(imgLink);
            container.appendChild(urlLink);
            var fileBox = a.closest(".diff-file-box");
            var header = fileBox && fileBox.querySelector(".file-header");
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
    var commitMatch = window.location.pathname.match(/^\/([^/]+\/[^/]+)\/commit\/([0-9a-f]{40})$/i);
    if (commitMatch) {
      var repoPath = commitMatch[1];
      var headSha = commitMatch[2];
      var origin = window.location.origin;

      // Extract parent SHA from the "parent" commit link at the top of the page.
      var parentSha = null;
      var parentLink = document.querySelector("a.primary.sha.label[href*='/commit/']");
      if (parentLink) {
        var pm = parentLink.getAttribute("href").match(/\/commit\/([0-9a-f]{40})$/i);
        if (pm) parentSha = pm[1];
      }

      document.querySelectorAll(".diff-file-box").forEach(function(box) {
        var newFile = box.getAttribute("data-new-filename") || "";
        var oldFile = box.getAttribute("data-old-filename") || "";
        var newIsIfc = newFile.toLowerCase().endsWith(".ifc");
        var oldIsIfc = oldFile.toLowerCase().endsWith(".ifc");

        var ifcPath, imgSrc, viewerIfc;
        if (newIsIfc && oldIsIfc && newFile === oldFile && parentSha) {
          // Modified: show colour-coded diff
          ifcPath = newFile;
          var baseIfc = "ifc://" + host + "/" + repoPath + "@" + parentSha + "?path=" + encodeURIComponent(ifcPath);
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

        var img = document.createElement("img");
        img.src = imgSrc;
        img.style.cssText = "max-width:100%;display:block";
        img.alt = ifcPath;
        var imgLink = document.createElement("a");
        imgLink.href = VIEWER_BASE + "?url=" + encodeURIComponent(viewerIfc);
        imgLink.target = "_blank";
        imgLink.rel = "noopener noreferrer";
        imgLink.appendChild(img);
        var code = document.createElement("code");
        code.style.cssText = "display:block;font-size:0.85em;word-break:break-all;margin-top:4px";
        code.textContent = viewerIfc;
        var urlLink = document.createElement("a");
        urlLink.href = viewerIfc;
        urlLink.appendChild(code);
        var container = document.createElement("div");
        container.style.cssText = "margin:8px 0";
        container.appendChild(imgLink);
        container.appendChild(urlLink);
        var header = box.querySelector(".diff-file-header");
        if (header) header.insertAdjacentElement("afterend", container);
      });
      return;
    }

    // -----------------------------------------------------------------------
    // Case 2: file history page — inject a "3D" link next to each commit's
    // browse-file link (/src/commit/{hash}/{treepath}).
    // -----------------------------------------------------------------------
    document.querySelectorAll('a[href*="/src/commit/"]').forEach(function(a) {
      var info = parseCommitHref(a.getAttribute("href"));
      if (!info || !info.treePath.toLowerCase().endsWith(".ifc")) return;
      var btn = makeViewerLink(info, "3D");
      btn.style.marginLeft = "4px";
      a.insertAdjacentElement("afterend", btn);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
