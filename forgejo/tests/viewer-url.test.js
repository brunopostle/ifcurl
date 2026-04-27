// Copyright 2026 The Forgejo Authors. All rights reserved.
// SPDX-License-Identifier: MIT

import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { parseIfcUrl, buildIfcUrl, toRawUrl } from "../custom/public/assets/viewer-url.js";

// ---------------------------------------------------------------------------
// parseIfcUrl
// ---------------------------------------------------------------------------

describe("parseIfcUrl", () => {
  test("returns null for non-ifc URLs", () => {
    assert.equal(parseIfcUrl("https://example.com"), null);
    assert.equal(parseIfcUrl(""), null);
    assert.equal(parseIfcUrl(null), null);
    assert.equal(parseIfcUrl(undefined), null);
  });

  test("parses host and repo path", () => {
    const r = parseIfcUrl("ifc://example.com/org/repo@heads/main?path=model.ifc");
    assert.equal(r.host, "example.com");
    assert.equal(r.repoSuffix, "org/repo");
    assert.equal(r.userAt, "");
  });

  test("parses user@ authority", () => {
    const r = parseIfcUrl("ifc://git@example.com/org/repo@heads/main?path=model.ifc");
    assert.equal(r.userAt, "git@");
    assert.equal(r.host, "example.com");
    assert.equal(r.repo, "git@example.com/org/repo");
  });

  test("parses heads/ ref", () => {
    const r = parseIfcUrl("ifc://example.com/org/repo@heads/main?path=a.ifc");
    assert.equal(r.ref, "heads/main");
  });

  test("parses tags/ ref", () => {
    const r = parseIfcUrl("ifc://example.com/org/repo@tags/v1.0?path=a.ifc");
    assert.equal(r.ref, "tags/v1.0");
  });

  test("parses bare commit hash ref", () => {
    const r = parseIfcUrl("ifc://example.com/org/repo@abc123def456?path=a.ifc");
    assert.equal(r.ref, "abc123def456");
  });

  test("parses path parameter", () => {
    const r = parseIfcUrl("ifc://example.com/org/repo@heads/main?path=subdir/model.ifc");
    assert.equal(r.path, "subdir/model.ifc");
  });

  test("parses selector parameter", () => {
    const r = parseIfcUrl("ifc://example.com/org/repo@heads/main?selector=IfcWall");
    assert.equal(r.selector, "IfcWall");
  });

  test("parses camera parameter", () => {
    const r = parseIfcUrl("ifc://example.com/org/repo@heads/main?camera=1,2,3,4,5,6,7,8,9");
    assert.equal(r.camera, "1,2,3,4,5,6,7,8,9");
  });

  test("parses fov parameter", () => {
    const r = parseIfcUrl("ifc://example.com/org/repo@heads/main?fov=60");
    assert.equal(r.fov, "60");
  });

  test("parses scale parameter", () => {
    const r = parseIfcUrl("ifc://example.com/org/repo@heads/main?scale=0.01");
    assert.equal(r.scale, "0.01");
  });

  test("defaults missing parameters to empty string", () => {
    const r = parseIfcUrl("ifc://example.com/org/repo@heads/main");
    assert.equal(r.path, "");
    assert.equal(r.selector, "");
    assert.equal(r.camera, "");
    assert.equal(r.fov, "");
    assert.equal(r.scale, "");
  });

  test("builds repo field as userAt + host + / + repoSuffix", () => {
    const r = parseIfcUrl("ifc://example.com/org/repo@heads/main");
    assert.equal(r.repo, "example.com/org/repo");
  });
});

// ---------------------------------------------------------------------------
// buildIfcUrl
// ---------------------------------------------------------------------------

describe("buildIfcUrl", () => {
  test("returns null when repo is missing", () => {
    assert.equal(buildIfcUrl("", "heads/main", "model.ifc", ""), null);
    assert.equal(buildIfcUrl(null, "heads/main", "", ""), null);
  });

  test("returns null when ref is missing", () => {
    assert.equal(buildIfcUrl("example.com/org/repo", "", "model.ifc", ""), null);
    assert.equal(buildIfcUrl("example.com/org/repo", null, "", ""), null);
  });

  test("builds minimal URL with no path or selector", () => {
    const url = buildIfcUrl("example.com/org/repo", "heads/main", "", "");
    assert.equal(url, "ifc://example.com/org/repo@heads/main");
  });

  test("includes path in query string", () => {
    const url = buildIfcUrl("example.com/org/repo", "heads/main", "model.ifc", "");
    assert.equal(url, "ifc://example.com/org/repo@heads/main?path=model.ifc");
  });

  test("includes selector in query string", () => {
    const url = buildIfcUrl("example.com/org/repo", "heads/main", "", "IfcWall");
    assert.equal(url, "ifc://example.com/org/repo@heads/main?selector=IfcWall");
  });

  test("includes both path and selector", () => {
    const url = buildIfcUrl("example.com/org/repo", "heads/main", "model.ifc", "IfcWall");
    assert.equal(url, "ifc://example.com/org/repo@heads/main?path=model.ifc&selector=IfcWall");
  });

  test("does not percent-encode commas in selector", () => {
    const url = buildIfcUrl("example.com/org/repo", "heads/main", "", "IfcWall,Name=\"Core\"");
    assert.ok(url.includes(","), "comma should not be encoded");
    assert.ok(!url.includes("%2C"), "%2C should not appear");
  });

  test("does not percent-encode + in selector", () => {
    const url = buildIfcUrl("example.com/org/repo", "heads/main", "", "IfcWall+IfcSlab");
    assert.ok(!url.includes("%2B"), "%2B should not appear");
  });

  test("does not percent-encode $ in selector (GlobalId syntax)", () => {
    const url = buildIfcUrl("example.com/org/repo", "heads/main", "", "325Q7Fhnf67OZC$$r43uzK");
    assert.ok(!url.includes("%24"), "%24 should not appear");
  });
});

// ---------------------------------------------------------------------------
// toRawUrl
// ---------------------------------------------------------------------------

describe("toRawUrl", () => {
  // GitHub
  test("generates GitHub raw URL for heads/ ref", () => {
    const url = toRawUrl("ifc://github.com/org/repo@heads/main?path=model.ifc");
    assert.equal(url, "https://raw.githubusercontent.com/org/repo/main/model.ifc");
  });

  test("generates GitHub raw URL for tags/ ref", () => {
    const url = toRawUrl("ifc://github.com/org/repo@tags/v1.0?path=model.ifc");
    assert.equal(url, "https://raw.githubusercontent.com/org/repo/v1.0/model.ifc");
  });

  test("generates GitHub raw URL for bare commit hash", () => {
    const url = toRawUrl("ifc://github.com/org/repo@abc123?path=model.ifc");
    assert.equal(url, "https://raw.githubusercontent.com/org/repo/abc123/model.ifc");
  });

  // GitLab
  test("generates GitLab raw URL for heads/ ref", () => {
    const url = toRawUrl("ifc://gitlab.com/org/repo@heads/main?path=model.ifc");
    assert.equal(url, "https://gitlab.com/org/repo/-/raw/main/model.ifc");
  });

  test("generates GitLab raw URL for tags/ ref", () => {
    const url = toRawUrl("ifc://gitlab.com/org/repo@tags/v1.0?path=model.ifc");
    assert.equal(url, "https://gitlab.com/org/repo/-/raw/v1.0/model.ifc");
  });

  // Forgejo / Gitea (codeberg.org, self-hosted, etc.)
  test("generates Forgejo raw URL for heads/ ref", () => {
    const url = toRawUrl("ifc://codeberg.org/org/repo@heads/main?path=model.ifc");
    assert.equal(url, "https://codeberg.org/org/repo/raw/branch/main/model.ifc");
  });

  test("generates Forgejo raw URL for tags/ ref", () => {
    const url = toRawUrl("ifc://codeberg.org/org/repo@tags/v1.0?path=model.ifc");
    assert.equal(url, "https://codeberg.org/org/repo/raw/tag/v1.0/model.ifc");
  });

  test("generates Forgejo raw URL for bare commit hash", () => {
    const url = toRawUrl("ifc://codeberg.org/org/repo@abc123?path=model.ifc");
    assert.equal(url, "https://codeberg.org/org/repo/raw/commit/abc123/model.ifc");
  });

  test("generates Forgejo raw URL for self-hosted instance", () => {
    const url = toRawUrl("ifc://git.example.com/org/repo@heads/main?path=model.ifc");
    assert.equal(url, "https://git.example.com/org/repo/raw/branch/main/model.ifc");
  });

  test("uses http for localhost Forgejo (local dev instance)", () => {
    const url = toRawUrl("ifc://localhost:3000/org/repo@heads/main?path=model.ifc");
    assert.equal(url, "http://localhost:3000/org/repo/raw/branch/main/model.ifc");
  });

  test("includes subdirectory file path", () => {
    const url = toRawUrl("ifc://codeberg.org/org/repo@heads/main?path=subdir/model.ifc");
    assert.ok(url.endsWith("/subdir/model.ifc"), `expected path suffix, got: ${url}`);
  });
});
