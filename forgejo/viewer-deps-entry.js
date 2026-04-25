// Entry point bundled by build.js into viewer-deps.js.
// Re-exports the three viewer dependencies as named namespace exports so
// viewer.html can do: import { THREE, OBC, JSZip } from "/assets/viewer-deps.js"
export * as THREE from "three";
export * as OBC from "@thatopen/components";
export { default as JSZip } from "jszip";
