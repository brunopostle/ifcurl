// Copyright 2026 The Forgejo Authors. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Pure utility functions shared between viewer.js, ifcurl.js, and tests.

// Parse a Forgejo browse path: /owner/repo/src/commit/{40hex}/{treepath}
// Returns {repoPath, hash, treePath} or null.
export function parseCommitHref(href) {
  const m = href.match(/^\/([^/]+\/[^/]+)\/src\/commit\/([0-9a-f]{40})\/(.+)$/i);
  if (!m) return null;
  return { repoPath: m[1], hash: m[2], treePath: m[3] };
}

// Returns true if the selector contains only IFC type names joined by "+".
// E.g. "IfcWall", "IfcWall+IfcSlab". Property filters and name= require
// server-side resolution via /select.
export function isSimpleTypeSelector(selectorStr) {
  return selectorStr.split("+").every(part => /^[Ii]fc[A-Za-z0-9]+$/.test(part.trim()));
}

// Set-subtract toRemove from allItems across all model IDs.
// Both arguments are ModelIdMap: { [modelId]: Iterable<number> }.
export function subtractIdMap(allItems, toRemove) {
  const result = {};
  for (const [modelId, allIds] of Object.entries(allItems)) {
    const removeSet = new Set(toRemove[modelId] ?? []);
    const remaining = [];
    for (const id of allIds) {
      if (!removeSet.has(id)) remaining.push(id);
    }
    if (remaining.length > 0) result[modelId] = remaining;
  }
  return result;
}

// Returns true if ref looks like a git commit hash (7–40 hex chars).
export function isCommitHash(ref) {
  return /^[0-9a-f]{7,40}$/.test(ref);
}
