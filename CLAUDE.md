# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
pip install -e ".[render]"           # install with rendering dependencies
pip install -e ".[service]"          # install with service dependencies (includes render)
python -m pytest tests/              # run all tests
python -m pytest tests/test_url.py -v
ifcurl render "ifc://..."            # render a URL to ifc-url-render.png
ifcurl render "ifc://..." -o out.png
ifcurl serve                         # start preview service on 127.0.0.1:8000
ifcurl serve --host 0.0.0.0 --port 9000
```

## Project overview

`ifcurl` implements the `ifc://` URL scheme — a way to address a specific view of an IFC model stored in a Git repository. A single URL encodes the git source, an optional IfcOpenShell selector, and an optional camera viewpoint.

Full spec: `SPECIFICATION.md`. Development tasks tracked in beads (`bd ready`).

## URL structure

```
ifc://[user@]host/org/repo@<ref>?<parameters>
```

Transport is inferred: `user@host` → SSH, bare `host` → HTTPS, empty authority (`ifc:///path`) → local file. Refs use git namespace form: `@heads/<branch>`, `@tags/<tag>`, `@<hash>`, `@HEAD`.

Key query parameters: `path` (IFC file in repo), `selector` (IfcOpenShell selector expression), `camera` (9 floats: position, direction, up), `fov` (perspective) or `scale` (orthographic), `clip` (repeatable clipping plane, 6 floats), `visibility` (`highlight`/`ghost`/`isolate`).

## Development phases

| Phase | Description | Language |
|---|---|---|
| 1 | Python core library + CLI | Python |
| 2 | HTTP preview service with caching | Python |
| 3 | Gitea plugin for inline preview embedding | Go |
| 4 | Bonsai integration (OS protocol handler + "Copy view URL") | Python |
| 5 | IFC Viewer integration | Python |
| 6 | `IFCDOCUMENTREFERENCE` federation extension | Python |

Phase 1 is the foundation for all other phases. Phases 3–6 cannot begin until Phase 1 exists.

## Phase 1 — Python core responsibilities

- Parse `ifc://` URL into components
- Infer git transport and fetch the IFC file at the specified ref
- Execute an IfcOpenShell selector against the loaded model
- Apply camera, clipping planes, and visibility mode
- Render a static PNG (headless / software OpenGL)

CLI entry point: `ifcurl render "<url>"`

## Caching (Phase 2)

Tier 2: commit hash → parsed model structure (shared across requests)  
Tier 3: commit hash + selector → resolved GUID set  
Tier 4: full URL hash → rendered PNG (**never** applied to mutable refs like `@heads/` or `@HEAD`)

## Key domain facts

- All spatial values (`camera`, `clip`) are in IFC world coordinates, matching BCF viewpoint conventions — not georeferenced survey coordinates.
- The `selector` parameter uses IfcOpenShell selector syntax. `+` joins selector groups (union); `,` separates filters within a group. **`[attr=val]` bracket notation is NOT valid** — the correct form is `IfcWall, Name="Core Wall"`. A bare 22-char GlobalId selects a single element: `selector=325Q7Fhnf67OZC$$r43uzK`.
- `IFCDOCUMENTREFERENCE` location fields may be relative paths (resolved within the same repo/ref), absolute local paths (desktop only), or `ifc://` URLs (cross-repo federation).
- BCF ↔ IFC URL conversion is defined in `SPECIFICATION.md` §6.


## Phase 3 — Forgejo/JS deployment

### Deployed components

| File | Location |
|---|---|
| `forgejo/templates/custom/footer.tmpl` | `/var/lib/forgejo/custom/templates/custom/footer.tmpl` |
| `forgejo/custom/public/assets/*` | `/var/lib/forgejo/custom/public/assets/` |

No Forgejo restart needed for template or static asset changes.

### Deploy command

```bash
sudo cp forgejo/templates/custom/footer.tmpl /var/lib/forgejo/custom/templates/custom/footer.tmpl
sudo cp forgejo/custom/public/assets/* /var/lib/forgejo/custom/public/assets/
```

### Services

- `ifcurl-api.service` — preview/select/query/BCF/render_diff service on port 8000
- Forgejo on `http://localhost:3000`
- Python install: `/opt/ifcurl/lib/python3.14/site-packages/ifcurl/`

### Browser test checklist

Before testing, confirm assets are deployed and services are up:
```bash
curl -s http://localhost:3000/assets/ifcurl.js | head -1   # should print JS comment
systemctl is-active ifcurl-api.service forgejo
```

**1 — ifcurl.js loads.** Open devtools console on any Forgejo page; no JS errors. Fetch `http://localhost:3000/assets/ifcurl.js` directly — must return JS, not 404.

**2 — "View in 3D" button (file view).** Open an `.ifc` file in Forgejo. Expect a "View in 3D" button in the top-right file header button group. Link must point to `/assets/viewer.html?url=ifc://localhost:3000/...@<40-char-hash>?path=...`.

**3 — "3D" links (file history).** Go to the file history page. Each commit row should have a small "3D" link after the browse-file link.

**4 — Viewer loads and renders.** Click "View in 3D". Viewer should show "Fetching IFC file…" → "Parsing IFC…" → blank status → 3D model in canvas.

**5 — Camera syncs to URL.** Orbit the camera. The URL bar updates with `camera=x,y,z,...`. Copy URL, open new tab — camera restores.

**6 — Ref datalist.** Clear the ref field in the toolbar; datalist should populate with `heads/main` etc. Select one and press Enter — model reloads.

**7 — Commit-pin badge.** Opened from "View in 3D" (hash URL): toolbar shows short hash badge and `→ heads/main` switch button.

**8 — Simple type selector.** Set selector to `IfcWall`, press Enter. Walls highlight orange. Visibility dropdown appears. Test `ghost` and `isolate` modes.

**9 — Complex selector (requires ifcurl-api).** Set selector to `IfcWall, Name="..."`. Status shows "Resolving selector…" then applies highlighting. If service unreachable, error appears in status bar (not silent).

**10 — Markdown ifc:// link preview.** Post an issue containing `[label](ifc://localhost:3000/org/repo@heads/main?path=file.ifc)`. Rendered markdown should replace the link with a `<figure>` containing a preview image and URL caption.

**11 — PR diff view.** Open a PR modifying an `.ifc` file. The `/pulls/<N>/files` page should inject a render-diff image below each `.ifc` file header (fetched from `/render_diff?base=...&head=...`).

**12 — Commit diff view.** Open a commit that modifies an `.ifc` file. Same render-diff image below the file header. Added-only file → plain preview; deleted-only → preview of old version.

**13 — BCF export (no selector).** Orient camera, click BCF button, export. Download `view.bcf`. Check zip contains `<guid>/viewpoint.bcfv` with `<PerspectiveCamera>` and `snapshot.png`.

**14 — BCF round-trip.** Drag the exported `.bcf` back into the viewer. Camera restores to exported viewpoint.

**15 — Copy URL button.** Click copy. Paste into address bar — valid `ifc://` URL with current camera state.

**16 — New Issue button.** Click "New Issue" in viewer. Opens Forgejo new-issue form in new tab with ifc:// URL pre-filled in body.

**17 — Metadata panel.** Click the hamburger (☰) button. Shows type counts (IfcWall: N, IfcSlab: N, …) and storey list; clicking a storey isolates its elements.

**18 — Click-to-inspect.** Click a 3D element. Properties panel shows IFC type, name, GlobalId. Copy button copies GUID.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:ca08a54f -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd dolt push
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->
