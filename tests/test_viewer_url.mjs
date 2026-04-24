import { test } from "node:test";
import assert from "node:assert/strict";
import { parseIfcUrl, buildIfcUrl, toRawUrl } from "../forgejo/custom/public/assets/viewer-url.js";

// ---------------------------------------------------------------------------
// parseIfcUrl
// ---------------------------------------------------------------------------

test("parseIfcUrl: null for non-ifc scheme", () => {
  assert.equal(parseIfcUrl("https://example.com/foo"), null);
});

test("parseIfcUrl: null for null input", () => {
  assert.equal(parseIfcUrl(null), null);
});

test("parseIfcUrl: null for empty string", () => {
  assert.equal(parseIfcUrl(""), null);
});

test("parseIfcUrl: simple HTTPS host", () => {
  const u = parseIfcUrl("ifc://example.com/org/repo@heads/main?path=model.ifc");
  assert.equal(u.host,        "example.com");
  assert.equal(u.userAt,      "");
  assert.equal(u.repoSuffix,  "org/repo");
  assert.equal(u.ref,         "heads/main");
  assert.equal(u.path,        "model.ifc");
  assert.equal(u.repo,        "example.com/org/repo");
});

test("parseIfcUrl: SSH user@ in authority", () => {
  const u = parseIfcUrl("ifc://git@github.com/org/repo@abc1234?path=m.ifc");
  assert.equal(u.host,   "github.com");
  assert.equal(u.userAt, "git@");
  assert.equal(u.repo,   "git@github.com/org/repo");
});

test("parseIfcUrl: host with port", () => {
  const u = parseIfcUrl("ifc://localhost:3000/org/repo@heads/main?path=m.ifc");
  assert.equal(u.host, "localhost:3000");
});

test("parseIfcUrl: tag ref", () => {
  const u = parseIfcUrl("ifc://example.com/org/repo@tags/v1.2.3?path=m.ifc");
  assert.equal(u.ref, "tags/v1.2.3");
});

test("parseIfcUrl: commit hash ref", () => {
  const u = parseIfcUrl("ifc://example.com/org/repo@deadbeef?path=m.ifc");
  assert.equal(u.ref, "deadbeef");
});

test("parseIfcUrl: selector param", () => {
  const u = parseIfcUrl("ifc://example.com/org/repo@HEAD?path=m.ifc&selector=IfcWall");
  assert.equal(u.selector, "IfcWall");
});

test("parseIfcUrl: camera param", () => {
  const u = parseIfcUrl("ifc://example.com/org/repo@HEAD?path=m.ifc&camera=1,2,3,4,5,6,7,8,9");
  assert.equal(u.camera, "1,2,3,4,5,6,7,8,9");
});

test("parseIfcUrl: fov param", () => {
  const u = parseIfcUrl("ifc://example.com/org/repo@HEAD?path=m.ifc&fov=60");
  assert.equal(u.fov, "60");
});

test("parseIfcUrl: scale param", () => {
  const u = parseIfcUrl("ifc://example.com/org/repo@HEAD?path=m.ifc&scale=10");
  assert.equal(u.scale, "10");
});

test("parseIfcUrl: missing params default to empty string", () => {
  const u = parseIfcUrl("ifc://example.com/org/repo@HEAD");
  assert.equal(u.path,     "");
  assert.equal(u.selector, "");
  assert.equal(u.camera,   "");
  assert.equal(u.fov,      "");
  assert.equal(u.scale,    "");
});

// ---------------------------------------------------------------------------
// buildIfcUrl
// ---------------------------------------------------------------------------

test("buildIfcUrl: null when repo missing", () => {
  assert.equal(buildIfcUrl("", "heads/main", "", ""), null);
});

test("buildIfcUrl: null when ref missing", () => {
  assert.equal(buildIfcUrl("example.com/org/repo", "", "", ""), null);
});

test("buildIfcUrl: bare repo + ref", () => {
  const url = buildIfcUrl("example.com/org/repo", "heads/main", "", "");
  assert.equal(url, "ifc://example.com/org/repo@heads/main");
});

test("buildIfcUrl: with path", () => {
  const url = buildIfcUrl("example.com/org/repo", "heads/main", "models/building.ifc", "");
  assert.equal(url, "ifc://example.com/org/repo@heads/main?path=models%2Fbuilding.ifc");
});

test("buildIfcUrl: with selector", () => {
  const url = buildIfcUrl("example.com/org/repo", "heads/main", "", "IfcWall");
  assert.equal(url, "ifc://example.com/org/repo@heads/main?selector=IfcWall");
});

test("buildIfcUrl: commas in selector not percent-encoded", () => {
  const url = buildIfcUrl("example.com/org/repo", "heads/main", "m.ifc", "IfcWall+IfcSlab");
  assert.ok(url.includes("IfcWall+IfcSlab"), `got: ${url}`);
  assert.ok(!url.includes("%2B") && !url.includes("%2C"), `got: ${url}`);
});

test("buildIfcUrl: SSH-style repo", () => {
  const url = buildIfcUrl("git@github.com/org/repo", "heads/main", "m.ifc", "");
  assert.ok(url.startsWith("ifc://git@github.com/org/repo@heads/main"));
});

test("buildIfcUrl: round-trips through parseIfcUrl", () => {
  const url = buildIfcUrl("example.com/org/repo", "heads/main", "model.ifc", "IfcWall");
  const p   = parseIfcUrl(url);
  assert.equal(p.repo,     "example.com/org/repo");
  assert.equal(p.ref,      "heads/main");
  assert.equal(p.path,     "model.ifc");
  assert.equal(p.selector, "IfcWall");
});

// ---------------------------------------------------------------------------
// toRawUrl
// ---------------------------------------------------------------------------

test("toRawUrl: GitHub heads/ branch", () => {
  const url = toRawUrl("ifc://github.com/org/repo@heads/main?path=model.ifc");
  assert.equal(url, "https://raw.githubusercontent.com/org/repo/main/model.ifc");
});

test("toRawUrl: GitHub tags/ tag", () => {
  const url = toRawUrl("ifc://github.com/org/repo@tags/v1.0?path=model.ifc");
  assert.equal(url, "https://raw.githubusercontent.com/org/repo/v1.0/model.ifc");
});

test("toRawUrl: GitHub commit hash", () => {
  const url = toRawUrl("ifc://github.com/org/repo@deadbeef?path=model.ifc");
  assert.equal(url, "https://raw.githubusercontent.com/org/repo/deadbeef/model.ifc");
});

test("toRawUrl: GitHub with SSH user@ stripped", () => {
  const url = toRawUrl("ifc://git@github.com/org/repo@heads/main?path=model.ifc");
  assert.equal(url, "https://raw.githubusercontent.com/org/repo/main/model.ifc");
});

test("toRawUrl: GitLab.com heads/ branch", () => {
  const url = toRawUrl("ifc://gitlab.com/org/repo@heads/main?path=model.ifc");
  assert.equal(url, "https://gitlab.com/org/repo/-/raw/main/model.ifc");
});

test("toRawUrl: self-hosted GitLab", () => {
  const url = toRawUrl("ifc://gitlab.example.com/org/repo@heads/main?path=model.ifc");
  assert.equal(url, "https://gitlab.example.com/org/repo/-/raw/main/model.ifc");
});

test("toRawUrl: Forgejo/Gitea heads/ branch", () => {
  const url = toRawUrl("ifc://gitea.example.com/org/repo@heads/main?path=model.ifc");
  assert.equal(url, "https://gitea.example.com/org/repo/raw/branch/main/model.ifc");
});

test("toRawUrl: Forgejo/Gitea tags/ tag", () => {
  const url = toRawUrl("ifc://gitea.example.com/org/repo@tags/v1.0?path=model.ifc");
  assert.equal(url, "https://gitea.example.com/org/repo/raw/tag/v1.0/model.ifc");
});

test("toRawUrl: Forgejo/Gitea commit hash", () => {
  const url = toRawUrl("ifc://gitea.example.com/org/repo@deadbeef?path=model.ifc");
  assert.equal(url, "https://gitea.example.com/org/repo/raw/commit/deadbeef/model.ifc");
});

test("toRawUrl: localhost Forgejo uses http://", () => {
  const url = toRawUrl("ifc://localhost:3000/org/repo@heads/main?path=model.ifc");
  assert.ok(url.startsWith("http://localhost:3000/"), `got: ${url}`);
});

test("toRawUrl: nested path within repo", () => {
  const url = toRawUrl("ifc://github.com/org/repo@heads/main?path=sub/dir/model.ifc");
  assert.equal(url, "https://raw.githubusercontent.com/org/repo/main/sub/dir/model.ifc");
});
