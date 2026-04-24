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
