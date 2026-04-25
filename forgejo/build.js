#!/usr/bin/env node
// Bundles viewer JS dependencies and copies web-ifc WASM files into
// custom/public/assets/ so the viewer works without any CDN access.
//
// Run: npm install && npm run build
// Re-run whenever package.json dependency versions change.

import esbuild from "esbuild";
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const outdir = path.join(__dirname, "custom/public/assets");

// Bundle three + @thatopen/components + jszip into a single ESM file.
// viewer.html imports { THREE, OBC, JSZip } from this bundle.
await esbuild.build({
  entryPoints: [path.join(__dirname, "viewer-deps-entry.js")],
  bundle: true,
  format: "esm",
  outfile: path.join(outdir, "viewer-deps.js"),
  minify: true,
  logLevel: "info",
});

// Bundle the fragments worker as a self-contained ESM (all deps inlined).
// viewer.html fetches this and passes the text as a blob URL to fragments.init().
const workerEntry = path.join(
  __dirname,
  "node_modules/@thatopen/fragments/dist/Worker/worker.mjs"
);
await esbuild.build({
  entryPoints: [workerEntry],
  bundle: true,
  format: "esm",
  outfile: path.join(outdir, "fragments-worker.js"),
  minify: true,
  logLevel: "info",
});

// Copy web-ifc WASM files (loaded at runtime by web-ifc when parsing IFC).
const wasmSrc = path.join(__dirname, "node_modules/web-ifc");
for (const name of ["web-ifc.wasm", "web-ifc-mt.wasm"]) {
  const src = path.join(wasmSrc, name);
  const dst = path.join(outdir, name);
  if (fs.existsSync(src)) {
    fs.copyFileSync(src, dst);
    const size = (fs.statSync(dst).size / 1024 / 1024).toFixed(1);
    console.log(`copied ${name} (${size} MB)`);
  } else {
    console.warn(`WARNING: ${name} not found in node_modules/web-ifc`);
  }
}
