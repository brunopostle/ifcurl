// Copyright 2026 Bruno Postle <bruno@postle.net>
// SPDX-License-Identifier: MIT
//
// Pure ifc:// URL logic shared between viewer.html and tests.

export function parseIfcUrl(raw) {
  if (!raw?.startsWith("ifc://")) return null;
  const body = raw.slice("ifc://".length);

  const slashIdx     = body.indexOf("/");
  const authority    = slashIdx < 0 ? body : body.slice(0, slashIdx);
  const pathAndQuery = slashIdx < 0 ? "" : body.slice(slashIdx + 1);

  const atInAuth = authority.indexOf("@");
  const userAt   = atInAuth < 0 ? "" : authority.slice(0, atInAuth + 1);
  const host     = atInAuth < 0 ? authority : authority.slice(atInAuth + 1);

  const qIdx     = pathAndQuery.indexOf("?");
  const pathPart = qIdx < 0 ? pathAndQuery : pathAndQuery.slice(0, qIdx);
  const qs       = new URLSearchParams(qIdx < 0 ? "" : pathAndQuery.slice(qIdx + 1));

  const atIdx      = pathPart.lastIndexOf("@");
  const repoSuffix = atIdx < 0 ? pathPart : pathPart.slice(0, atIdx);
  const ref        = atIdx < 0 ? "" : pathPart.slice(atIdx + 1);

  return {
    repo:     userAt + host + "/" + repoSuffix,
    host, userAt, repoSuffix, ref,
    path:     qs.get("path")     ?? "",
    selector: qs.get("selector") ?? "",
    query:    qs.get("query")    ?? "",
    camera:   qs.get("camera")   ?? "",
    fov:      qs.get("fov")      ?? "",
    scale:    qs.get("scale")    ?? "",
  };
}

export function buildIfcUrl(repo, ref, path, selector, query) {
  if (!repo || !ref) return null;
  const qs = new URLSearchParams();
  if (path)     qs.set("path",     path);
  if (selector) qs.set("selector", selector);
  if (query)    qs.set("query",    query);
  const qsStr = qs.toString().replace(/%2C/g, ",").replace(/%2B/g, "+").replace(/%24/g, "$");
  return `ifc://${repo}@${ref}${qsStr ? "?" + qsStr : ""}`;
}

export function toRawUrl(raw) {
  const body = raw.slice("ifc://".length);

  const slashIdx     = body.indexOf("/");
  const authority    = slashIdx < 0 ? body : body.slice(0, slashIdx);
  const pathAndQuery = slashIdx < 0 ? "" : body.slice(slashIdx + 1);

  const atInAuth = authority.indexOf("@");
  const host     = atInAuth < 0 ? authority : authority.slice(atInAuth + 1);

  const qIdx     = pathAndQuery.indexOf("?");
  const pathPart = qIdx < 0 ? pathAndQuery : pathAndQuery.slice(0, qIdx);
  const qs       = new URLSearchParams(qIdx < 0 ? "" : pathAndQuery.slice(qIdx + 1));
  const filePath = qs.get("path") ?? "";

  const atIdx    = pathPart.lastIndexOf("@");
  const repoPath = atIdx < 0 ? pathPart : pathPart.slice(0, atIdx);
  const ref      = atIdx < 0 ? "" : pathPart.slice(atIdx + 1);

  const plainRef = ref.startsWith("heads/") ? ref.slice(6)
                 : ref.startsWith("tags/")  ? ref.slice(5)
                 : ref;

  if (host === "github.com" || host.endsWith(".github.com")) {
    return `https://raw.githubusercontent.com/${repoPath}/${plainRef}/${filePath}`;
  }
  if (host === "gitlab.com" || host.startsWith("gitlab.")) {
    const proto = host.startsWith("localhost") ? "http" : "https";
    return `${proto}://${host}/${repoPath}/-/raw/${plainRef}/${filePath}`;
  }
  const refSeg = ref.startsWith("heads/") ? "branch/" + ref.slice(6)
               : ref.startsWith("tags/")  ? "tag/"    + ref.slice(5)
               : "commit/" + ref;
  const proto = host.startsWith("localhost") ? "http" : "https";
  return `${proto}://${host}/${repoPath}/raw/${refSeg}/${filePath}`;
}
