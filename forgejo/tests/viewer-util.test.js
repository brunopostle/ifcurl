// Copyright 2026 Bruno Postle <bruno@postle.net>
// SPDX-License-Identifier: MIT

import { test, describe } from "node:test";
import assert from "node:assert/strict";
import { parseCommitHref, isSimpleTypeSelector, subtractIdMap, isCommitHash }
  from "../custom/public/assets/viewer-util.js";

// ---------------------------------------------------------------------------
// parseCommitHref
// ---------------------------------------------------------------------------

describe("parseCommitHref", () => {
  test("parses a standard Forgejo browse path", () => {
    const r = parseCommitHref("/org/repo/src/commit/a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2/model.ifc");
    assert.equal(r.repoPath, "org/repo");
    assert.equal(r.hash,     "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2");
    assert.equal(r.treePath, "model.ifc");
  });

  test("parses a path with subdirectories", () => {
    const r = parseCommitHref("/org/repo/src/commit/a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2/subdir/model.ifc");
    assert.equal(r.treePath, "subdir/model.ifc");
  });

  test("returns null for non-commit paths", () => {
    assert.equal(parseCommitHref("/org/repo/src/branch/main/model.ifc"), null);
    assert.equal(parseCommitHref("/org/repo"), null);
    assert.equal(parseCommitHref(""), null);
  });

  test("returns null for a commit directory link (no file path)", () => {
    // Breadcrumb anchors on a file-view page link to the directory at the
    // commit, e.g. /org/repo/src/commit/{hash} with no trailing file path.
    // These must not be mistaken for a file permalink.
    assert.equal(
      parseCommitHref("/org/repo/src/commit/a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"),
      null
    );
  });

  test("returns null if hash is not 40 hex chars", () => {
    assert.equal(parseCommitHref("/org/repo/src/commit/abc123/model.ifc"), null);
  });

  test("is case-insensitive for hex hash", () => {
    const r = parseCommitHref("/org/repo/src/commit/A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2/model.ifc");
    assert.ok(r !== null);
    assert.equal(r.hash, "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4E5F6A1B2");
  });
});

// ---------------------------------------------------------------------------
// isSimpleTypeSelector
// ---------------------------------------------------------------------------

describe("isSimpleTypeSelector", () => {
  test("accepts a single IFC type", () => {
    assert.equal(isSimpleTypeSelector("IfcWall"), true);
  });

  test("accepts multiple types joined by +", () => {
    assert.equal(isSimpleTypeSelector("IfcWall+IfcSlab"), true);
    assert.equal(isSimpleTypeSelector("IfcWall+IfcSlab+IfcBeam"), true);
  });

  test("accepts subtypes", () => {
    assert.equal(isSimpleTypeSelector("IfcWallStandardCase"), true);
  });

  test("accepts lowercase ifc prefix", () => {
    assert.equal(isSimpleTypeSelector("ifcWall"), true);
  });

  test("rejects name filter", () => {
    assert.equal(isSimpleTypeSelector('IfcWall, Name="Core Wall"'), false);
  });

  test("rejects property set filter", () => {
    assert.equal(isSimpleTypeSelector("IfcWall[Pset_WallCommon.IsExternal=true]"), false);
  });

  test("rejects bare GlobalId (not an IFC type name)", () => {
    assert.equal(isSimpleTypeSelector("325Q7Fhnf67OZC$$r43uzK"), false);
  });

  test("rejects empty string", () => {
    assert.equal(isSimpleTypeSelector(""), false);
  });
});

// ---------------------------------------------------------------------------
// subtractIdMap
// ---------------------------------------------------------------------------

describe("subtractIdMap", () => {
  test("removes ids present in toRemove", () => {
    const all    = { m1: [1, 2, 3, 4] };
    const remove = { m1: [2, 4] };
    const result = subtractIdMap(all, remove);
    assert.deepEqual(result, { m1: [1, 3] });
  });

  test("omits model keys where all ids are removed", () => {
    const all    = { m1: [1, 2] };
    const remove = { m1: [1, 2] };
    const result = subtractIdMap(all, remove);
    assert.deepEqual(result, {});
  });

  test("leaves models untouched that are not in toRemove", () => {
    const all    = { m1: [1, 2], m2: [3, 4] };
    const remove = { m1: [1] };
    const result = subtractIdMap(all, remove);
    assert.deepEqual(result, { m1: [2], m2: [3, 4] });
  });

  test("handles empty toRemove", () => {
    const all    = { m1: [1, 2, 3] };
    const result = subtractIdMap(all, {});
    assert.deepEqual(result, { m1: [1, 2, 3] });
  });

  test("handles multiple models in toRemove", () => {
    const all    = { m1: [1, 2], m2: [3, 4] };
    const remove = { m1: [2], m2: [3] };
    const result = subtractIdMap(all, remove);
    assert.deepEqual(result, { m1: [1], m2: [4] });
  });
});

// ---------------------------------------------------------------------------
// isCommitHash
// ---------------------------------------------------------------------------

describe("isCommitHash", () => {
  test("accepts a full 40-char SHA", () => {
    assert.equal(isCommitHash("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2"), true);
  });

  test("accepts a 7-char short SHA", () => {
    assert.equal(isCommitHash("a1b2c3d"), true);
  });

  test("accepts lengths between 7 and 40", () => {
    assert.equal(isCommitHash("a1b2c3d4e5f6"), true);
  });

  test("rejects heads/ ref", () => {
    assert.equal(isCommitHash("heads/main"), false);
  });

  test("rejects tags/ ref", () => {
    assert.equal(isCommitHash("tags/v1.0"), false);
  });

  test("rejects uppercase hex (hashes are lowercase)", () => {
    assert.equal(isCommitHash("A1B2C3D"), false);
  });

  test("rejects fewer than 7 chars", () => {
    assert.equal(isCommitHash("a1b2c3"), false);
  });

  test("rejects more than 40 chars", () => {
    assert.equal(isCommitHash("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2x"), false);
  });

  test("rejects empty string", () => {
    assert.equal(isCommitHash(""), false);
  });
});
