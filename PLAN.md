# IFC URL — Development Plan

## Overview

IFC URL requires work across four distinct areas:

1. A Python core library and CLI tool
2. A preview service with Gitea integration
3. Desktop app integration (Bonsai, IFC Viewer)
4. The federation extension to `IFCDOCUMENTREFERENCE`

The Python core is the foundation for all other work. The Gitea integration
and desktop app work can proceed in parallel once the core exists.

### Language ownership

| Component | Language | Rationale |
|---|---|---|
| URL parsing, git fetch, selector execution, rendering | Python | Built on IfcOpenShell |
| Bonsai plugin | Python | Bonsai is a Blender/Python addon |
| CLI tool | Python | Wraps the core library |
| Preview service | Python | Wraps the core library |
| Gitea plugin | Go | Gitea is written in Go; the plugin only calls an HTTP endpoint |
| Web viewer integration | JavaScript | Future, not in current scope |

The Python core is a strong candidate for eventual contribution to
IfcOpenShell, but this is not a prerequisite for any phase.

---

## Phase 1 — Python core and CLI tool

Build a Python library that resolves an `ifc://` URL and produces a rendered
PNG. This validates the spec against real IFC files and is the foundation for
all subsequent work.

### Library responsibilities

- Parse an `ifc://` URL into its components
- Infer git transport from URL structure (SSH, HTTPS, local)
- Fetch the IFC file from the repository at the specified ref
- Execute the IfcOpenShell selector against the loaded model
- Apply camera (perspective and orthographic), clipping planes, and
  visibility mode
- Render a static PNG

### CLI interface

```bash
ifc-url render "ifc://example.com/org/repo@abc123?path=model.ifc&selector=IfcWall&fov=60"
ifc-url render "ifc://example.com/org/repo@heads/main?path=model.ifc&camera=0,0,8,0,0,-1,0,1,0&scale=50"
```

### Notes

- IfcOpenShell handles selector execution, model parsing, and geometry
- Rendering requires a headless approach; options include IfcOpenShell's own
  geometry processing with a software or headless OpenGL renderer
- Git access uses the platform credential store for HTTPS and ssh-agent or
  key files for SSH
- The CLI accepts the URL as a plain string so it can be called from
  non-Python contexts

---

## Phase 2 — Preview service

Wrap the Phase 1 library in an HTTP service for use by Gitea and other
consumers.

### Endpoint

```
POST /preview
body: { url: "ifc://..." }
response: image/png
```

### Caching

| Tier | Cache key | Contents | Notes |
|---|---|---|---|
| 2 | commit hash | Parsed model structure | Shared across requests |
| 3 | commit hash + selector | Resolved GUID set | |
| 4 | full URL hash | Rendered PNG | Not applied to mutable refs |

Mutable refs (`@heads/`, `@HEAD`) must not be cached at tier 4 since the
underlying commit may change. Commit hash refs are immutable and can be
cached indefinitely at all tiers.

### Authentication

- **Co-located with Gitea** — uses the requesting user's Gitea session token,
  passed from the Gitea plugin
- **Standalone** — sideloaded token configuration per git host

---

## Phase 3 — Gitea integration

Add `ifc://` link detection and preview embedding to Gitea.

### Go plugin responsibilities

- Detect `ifc://` links in post and comment content
- Call the preview service with the URL and the current user's session token
- Embed the returned PNG inline with a link that opens the URL
- Degrade gracefully when the preview service is unavailable (show the raw
  URL rather than failing)

### Notes

- The plugin has no IFC awareness; all IFC logic lives in the preview service
- Repeated embeds of the same URL across different posts do not trigger
  re-renders — the preview service cache handles deduplication
- The preview service may run as a companion process on the same host or
  as a separately configured external service

---

## Phase 3b — Web IFC viewer

Add a 3D viewer that opens in a separate browser window when viewing an `.ifc`
file in Forgejo.

### Architecture

- **Forgejo Go patch**: when rendering the file view for a `.ifc` file, inject
  a "View in 3D" button that opens
  `http://localhost:8000/viewer?url=ifc://...` in a new tab.  The ifc:// URL
  encodes the current host, repo, ref, and file path.

- **`/viewer` endpoint** in the ifcurl preview service: returns a self-contained
  HTML page.  No server-side IFC processing — the browser does all rendering.

- **Browser rendering**: the viewer page loads `@thatopen/components` (and its
  `web-ifc` + Three.js peer dependencies) from a CDN via an ES module importmap.
  It constructs the Forgejo raw file URL from the ifc:// URL parameters and
  fetches the IFC bytes directly from Forgejo.

### Current scope (Phase 3b)

- Display the full model for whatever ref is shown in Forgejo.
- Basic orbit/pan/zoom camera controls.
- No filtering or selection UI yet.

### Future scope

- Element filtering and selection using `@thatopen/components` classifier and
  highlighter APIs.
- Camera state serialised to an `ifc://` URL so the user can copy a view link
  (feeds into Phase 4 Bonsai "Copy view URL").
- Diff highlighting driven by ifcmerge output (see Phase 3c).

---

## Phase 3c — ifcmerge integration

`ifcmerge` is a git custom merge driver that resolves conflicts in `.ifc` files
that git's built-in 3-way merge cannot handle.  Where two branches have both
modified an IFC file (a fork), git would report an unresolvable conflict;
ifcmerge performs a semantics-aware merge and produces a valid merged IFC file.

### Git merge driver configuration

Register ifcmerge as the git merge driver for `.ifc` files on the Forgejo
server:

```
# /etc/gitconfig or ~forgejo/.gitconfig
[merge "ifcmerge"]
    name = IFC merge driver
    driver = ifcmerge %O %A %B %L
```

```
# .gitattributes in each repo, or server-side gitattributes
*.ifc merge=ifcmerge
```

With this in place, Forgejo's existing "merge automatically" path will invoke
ifcmerge via git when merging `.ifc` files, and the PR merge button will work
without any Go-side changes for the common case.

### Forgejo integration

- Verify that Forgejo's merge-ability check (the "these branches can be merged
  automatically" indicator) correctly reflects ifcmerge's ability to merge
  `.ifc` files — investigate whether the check runs a trial merge via git or
  uses a heuristic.
- If needed, patch Forgejo to run a trial `git merge --no-commit` to get an
  accurate result for IFC files.

### 3D diff view (future)

- After a merge completes, surface a "View merge in 3D" button in the PR using
  the Phase 3b viewer.
- Use `@thatopen/components` highlighter to colour added, removed, and modified
  elements differently, driven by comparing the merged IFC against either parent.

---

## Phase 4 — Bonsai integration

Add `ifc://` handling to Bonsai.

### Protocol handler

Register `ifc://` as an OS protocol handler on installation (Windows,
macOS, Linux). On receiving a URL, resolve the git source, load the model,
and apply the view state from the URL parameters.

### "Copy view URL"

A button that generates an `ifc://` URL from the current Bonsai state:

- Git remote, ref, and file path of the open model
- Active selection converted to an IfcOpenShell selector
- Current camera converted to the `camera` + `fov` or `scale` format,
  in IFC world coordinates
- Active clipping planes as `clip` parameters
- Current visibility mode

The button always generates a commit hash ref by default. Generating a
`@heads/` ref is a secondary option for authoring links intended to track
a branch.

### Federation

When opening a model via `ifc://`, Bonsai resolves `IFCDOCUMENTREFERENCE`
locations using the repo and ref from the root file's URL as context.
`ifc://` locations in linked model references are resolved recursively.

---

## Phase 5 — IFC Viewer integration

Equivalent to Phase 4 for the IFC Viewer desktop application.

---

## Phase 6 — Federation extension

Extend the `IFCDOCUMENTREFERENCE` location field in Bonsai to accept
`ifc://` URLs, enabling cross-repo and cross-server federation.

### Tasks

- When saving a linked model reference, offer the option to write the
  location as an `ifc://` URL rather than a relative path
- When a relative path would traverse above the repository root, prompt
  the user to convert it to an `ifc://` URL
- Resolve `ifc://` location fields through the Phase 4 URL resolver

The transformation matrix in `IFCDOCUMENTREFERENCE` is unaffected by this
change. Phase 4 must be complete before this phase begins.

---

## Future work

### BCF export

An additional preview service endpoint returning a BCF file:

```
POST /bcf
body: { url: "ifc://..." }
response: application/zip
```

Resolves the selector to a GUID set and constructs a BCF viewpoint from the
camera, clipping planes, and visibility state. No implementation is planned
before Phase 2 is complete.

### Web viewer integration

JavaScript integration with web-based IFC viewers (IFC.js, xeokit) and
browser-side forum embedding. Not in current scope.

### IfcOpenShell contribution

Once the Python core is stable, the URL parsing and resolution logic is a
candidate for contribution to IfcOpenShell as a shared utility. This is not
a prerequisite for any phase.
