# ifcurl

**ifcurl** is a Forgejo mod that adds 3D model awareness to BIM workflows on git. When IFC files are committed or compared, diff pages show colour-coded visual renders of what changed. Paste an `ifc://` URL into any issue, PR comment, or markdown file and Forgejo renders it as a linked preview image. Every preview is a clickable link that opens the model in an interactive WebGL viewer at the exact viewpoint encoded in the URL.

---

## What you get

### Visual diffs on commit and PR pages

When an `.ifc` file changes in a commit or pull request, the diff page automatically shows a rendered snapshot of the change — added geometry in green, removed in red. The image links directly to the 3D viewer at the head version of the file, and the `ifc://` URL for that view is shown below the image.

### Inline previews in markdown

Paste a bare `ifc://` URL or write `[label](ifc://…)` in any issue, PR comment, or `.md` file:

```
ifc://github.com/brunopostle/simple-ifc@heads/main?path=building.ifc&selector=IfcWall&fov=60
```

Forgejo renders it as a `<figure>` with a preview image linked to the interactive viewer, and the `ifc://` URL shown below as a clickable link.

### "View in 3D" button

A **View in 3D** button appears on every `.ifc` file page alongside Raw / Permalink / History.

### Browser viewer

A self-contained WebGL IFC viewer (`viewer.html`) served as a Forgejo asset at `/assets/viewer.html`.

**Features:**

- Toolbar with raw ifc:// URL input and structured fields (repo, ref, path, selector) — editing any field reloads the model; ref input has branch/tag autocomplete
- Selector filtering — `IfcWall`, `IfcWall+IfcSlab`; visibility dropdown (`highlight` / `ghost` / `isolate`) for non-selected elements
- Clipping planes — **✂ clip** button, double-click on model surface to place; drag handles to adjust; planes serialised back into the URL
- FOV control, camera sync — the URL in the browser bar always reflects the current view and is shareable
- **Model overview panel** — type counts and storey list; click any storey to isolate it, click again to show all
- **Click to identify** — click any element to see its IFC type, name, and GlobalId; copy GlobalId to clipboard for use in selectors
- **⎘ copy** button — copies the current ifc:// URL to clipboard
- **Issue** button — opens a new issue on the git host with the ifc:// URL pre-filled
- **BCF export** — exports the current view as a BCF 2.1 file for import into Revit, Navisworks, etc.
- Drag-and-drop ifc:// URLs onto the page
- Download progress for large IFC files

### Collaboration workflow

`ifc://` URLs function as view permalinks that can be embedded anywhere: Forgejo issues and comments, pull request discussions, markdown documentation, Slack, or email.

1. Open the viewer and navigate to the view you want to share
2. Add clipping planes or a selector to isolate the relevant geometry
3. Click **⎘** to copy the ifc:// URL, or **Issue** to open a pre-filled issue
4. Paste the URL into a Forgejo issue, PR comment, or any markdown file

**Referencing specific elements:** click any element to see its GlobalId, then paste it directly as `selector=325Q7Fhnf67OZC$$r43uzK`. Use `visibility=highlight`, `ghost`, or `isolate` to control how the selection is displayed.

**BCF export for external tools:** the **BCF** button exports a BCF 2.1 file that can be attached to a Forgejo issue or imported into Revit, Navisworks, Solibri, or any BCF-compatible tool.

---

## Forgejo integration

A set of JS assets for Forgejo — all features work with asset deployment only,
no Forgejo rebuild required. See [`forgejo/README.md`](forgejo/README.md) for
full details, or [`forgejo/SETUP_LOCAL.md`](forgejo/SETUP_LOCAL.md) for a
self-contained local setup on Windows using Docker.

### Quick setup

```bash
# Deploy assets
sudo mkdir -p /var/lib/forgejo/custom/public/assets/ /var/lib/forgejo/custom/templates/custom/
sudo cp forgejo/custom/public/assets/viewer*.* /var/lib/forgejo/custom/public/assets/
sudo cp forgejo/custom/public/assets/ifcurl.js /var/lib/forgejo/custom/public/assets/
sudo cp forgejo/templates/custom/footer.tmpl /var/lib/forgejo/custom/templates/custom/
sudo systemctl restart forgejo

# Install and start preview service as a systemd unit
sudo cp forgejo/server-config/ifcurl-preview.service /etc/systemd/system/
# edit ExecStart --allowed-hosts to match your Forgejo hostname, then:
sudo systemctl enable --now ifcurl-preview
```

### Bare `ifc://` URLs in markdown — underscore caveat

Bare `ifc://` URLs in markdown (pasted without `[title](...)` syntax) are
linkified client-side by `ifcurl.js`. This works for most paths, but if the
file path or query parameters contain matched underscores (e.g. `_model_v2.ifc`),
Goldmark renders them as `<em>` tags and splits the URL — the bare form will
fail silently. Use the explicit link form `[label](ifc://...)` for any URL where
the path or parameters contain paired underscores. The viewer's **Issue** button
always generates this safe form automatically.

### Authentication in the viewer

When the viewer is served from the same Forgejo instance (`https://git.example.com/assets/viewer.html`), it shares the browser session cookie — private repositories work automatically for authenticated users.

IFC files on other platforms (GitHub, GitLab, a different Forgejo instance) are fetched directly from the browser; only public repositories work for external hosts.

### Preview service

An HTTP service that renders `ifc://` URLs to PNG, intended for co-location with a Forgejo instance.

```bash
pip install "ifcurl[service]"
ifcurl serve --allowed-hosts git.example.com     # restrict to your Forgejo host
ifcurl serve --host 0.0.0.0 --port 9000 --allowed-hosts git.example.com
```

| Endpoint | Description |
|---|---|
| `POST /preview` | Render an ifc:// URL to PNG |
| `GET /preview?url=ifc://…` | Same, via query string (used by Forgejo `<img>` tags) |
| `POST /bcf` | Generate a BCF 2.1 zip from an ifc:// URL viewpoint |
| `GET /select?url=ifc://…` | Resolve a complex selector server-side; returns JSON list of GlobalIds |
| `GET /render_diff?base=ifc://…&head=ifc://…` | Render a colour-coded diff PNG (added green, removed red) |
| `GET /foundation/versions` | OpenCDE Foundation API discovery — lists BCF and Documents API endpoints (proxy at `/foundation/` on the Forgejo hostname) |
| `GET /foundation/1.1/oauth2/auth_url` | Return Forgejo authorization URL to begin OAuth2 code flow (requires `IFCURL_OAUTH2_CLIENT_ID`) |
| `POST /foundation/1.1/oauth2/token` | Exchange auth code for access + refresh token pair; client credentials injected server-side |
| `POST /foundation/1.1/oauth2/token_refresh` | Exchange refresh token for new token pair |
| `POST /documents/1.0/document-versions` | OpenCDE Documents API — resolve document_ids to versioned download URLs (proxied at `/documents/`) |
| `POST /documents/1.0/select-documents` | Return a picker UI URL; caller redirects the user's browser to it |
| `GET /documents/1.0/select-documents/ui` | Self-contained document-picker page — user browses Forgejo repos, selects an IFC file, redirected back to `callback_url?document_ids[]=<id>` |
| `GET /bcf/3.0/projects` | BCF 3.0 — list repositories as BCF projects |
| `GET/POST /bcf/3.0/projects/{owner}/{repo}/topics` | BCF 3.0 — list or create topics (Forgejo issues) |
| `GET/PUT /bcf/3.0/projects/{owner}/{repo}/topics/{guid}` | BCF 3.0 — get or update a topic |
| `GET/POST /bcf/3.0/projects/{owner}/{repo}/topics/{guid}/comments` | BCF 3.0 — list or create comments |
| `GET/POST /bcf/3.0/projects/{owner}/{repo}/topics/{guid}/viewpoints` | BCF 3.0 — list or create viewpoints (ifc:// URLs in comment bodies) |
| `GET /bcf/3.0/projects/{owner}/{repo}/topics/{guid}/viewpoints/{guid}` | BCF 3.0 — get a viewpoint |
| `GET /bcf/3.0/projects/{owner}/{repo}/topics/{guid}/viewpoints/{guid}/snapshot` | BCF 3.0 — render viewpoint as PNG via preview service |

The BCF API forwards the client's `Authorization: Bearer` header to Forgejo's REST API verbatim. No service account or machine token is required — Forgejo enforces per-user repository permissions on each call. The Foundation endpoint derives its public base URL from `X-Forwarded-Host` / `X-Forwarded-Proto` headers set by the reverse proxy.

**Configuration** (environment variables for the preview service):

| Variable | Default | Description |
|---|---|---|
| `IFCURL_FORGEJO_URL` | `http://localhost:3000` | Forgejo base URL for BCF API calls |
| `IFCURL_PREVIEW_URL` | `http://localhost:8000` | Preview service URL for BCF snapshot endpoint |
| `IFCURL_OAUTH2_CLIENT_ID` | (none) | Forgejo OAuth2 app client ID — required for Foundation API OAuth2 proxy routes |
| `IFCURL_OAUTH2_CLIENT_SECRET` | (none) | Forgejo OAuth2 app client secret |

**Proxy configuration** — add to nginx or Caddy on the Forgejo hostname:

```nginx
location /foundation/ { proxy_pass http://localhost:8000/foundation/; }
location /bcf/        { proxy_pass http://localhost:8000/bcf/; }
location /documents/  { proxy_pass http://localhost:8000/documents/; }
```

**Caching:**

| Tier | Key | Contents | Notes |
|---|---|---|---|
| 2 | commit hash + path | IFC bytes | In-memory LRU |
| 3 | commit hash + path + selector | Resolved element set | In-memory LRU |
| 4 | SHA-256 of full URL | Rendered PNG | Filesystem; immutable refs only |

Tier 4 is never written for mutable refs (`@heads/`, `@HEAD`).

---

## ifc:// URL scheme

An `ifc://` URL encodes everything needed to reproduce a specific model view: the source — a git repository or an OpenCDE CDE — the file, which elements to show, and the camera position. Like a permalink for BIM — paste it, share it, embed it in documentation.

Full specification: [`SPECIFICATION.md`](SPECIFICATION.md)

**Git source:**

```
ifc://[user@]host/org/repo@<ref>?<parameters>
```

**OpenCDE source:**

```
ifc://host[/project]?document_id=<id>[&version_index=<N>]&<parameters>
```

Transport is inferred from the URL structure:

| URL form | Transport |
|---|---|
| `ifc://host/org/repo` | HTTPS (git) |
| `ifc://git@host/org/repo` | SSH (git) |
| `ifc:///path/to/repo` | Local file |
| `ifc://host/...?document_id=...` | OpenCDE HTTPS |

Git refs follow git namespace form to avoid branch/tag ambiguity:

| Form | Meaning |
|---|---|
| `@heads/main` | Branch tip |
| `@tags/v1.2` | Tag |
| `@abc123def` | Commit hash |
| `@HEAD` | Default branch |

Key query parameters:

| Parameter | Description |
|---|---|
| `path=` | IFC file path within the repository (git) |
| `document_id=` | OpenCDE document identifier — signals OpenCDE transport |
| `version_index=3` | OpenCDE version index — omit for current version |
| `selector=IfcWall` | [IfcOpenShell selector](https://docs.ifcopenshell.org/ifcopenshell-python/selector_syntax.html) — filter elements; `+` for union |
| `camera=px,py,pz,dx,dy,dz,ux,uy,uz` | Camera position, direction, up in IFC world coordinates |
| `fov=60` | Perspective field of view in degrees |
| `scale=50` | Orthographic view-to-world scale |
| `clip=px,py,pz,nx,ny,nz` | Clipping plane — point + normal in IFC coords (repeatable) |
| `visibility=` | `highlight` · `ghost` · `isolate` |

---

## Python library and CLI

Fetch an IFC file from a git repository and render it to PNG.

```bash
pip install "ifcurl[render]"
ifcurl render "ifc://github.com/brunopostle/simple-ifc@heads/main?path=_test_simple.ifc"
ifcurl render "ifc://..." -o output.png

# Manage the local git repository cache
ifcurl cache list           # show cached repos with size and last-access time
ifcurl cache prune --max 5  # remove oldest repos until cache is under 5 GB
ifcurl cache clear          # remove all cached repos

# Register ifc:// as an OS-level protocol handler (Linux, macOS, Windows)
ifcurl-register
ifcurl-register --unregister   # remove registration
```

`ifcurl-register` writes the platform-appropriate handler (`.desktop` + xdg-mime
on Linux, an app bundle + Launch Services on macOS, per-user registry keys on
Windows) and points it at `ifcurl-open`. When the OS invokes the handler,
`ifcurl-open` reads `~/.config/ifcurl/config.toml` and forwards the URL to the
configured viewer:

```toml
[handler]
default_viewer = "bonsai"   # or "ifcviewer"
# Or a fully custom command:
# command = ["myviewer", "--open", "{url}"]
```

Use as a library:

```python
import ifcopenshell
from ifcurl import IfcUrl, fetch_ifc
from ifcurl.render import render

url = IfcUrl.parse(
    "ifc://example.com/org/repo@heads/main"
    "?path=models/building.ifc&selector=IfcWall&visibility=ghost"
)
hexsha, ifc_bytes = fetch_ifc(url)   # hexsha is stable even for mutable refs
model = ifcopenshell.file.from_string(ifc_bytes.decode())

png = render(model, selector=url.selector, clips=url.clips or None,
             camera=url.camera, fov=url.fov, visibility=url.visibility)
```

Serialise a parsed URL back to a string with `.to_string()`:

```python
url = IfcUrl.parse("ifc://example.com/org/repo@heads/main?path=model.ifc")
modified = dataclasses.replace(url, selector="IfcWall", visibility="ghost")
print(modified.to_string())
# ifc://example.com/org/repo@heads/main?path=model.ifc&selector=IfcWall&visibility=ghost
```

Convert a BCF 3.0 REST viewpoint dict to an `ifc://` URL (§6 reverse direction):

```python
from ifcurl.bcf import bcf_viewpoint_to_ifc_url

base = IfcUrl.parse("ifc://example.com/org/repo@heads/main?path=model.ifc")
ifc_url = bcf_viewpoint_to_ifc_url(base, viewpoint_dict)
```

`viewpoint_dict` is a BCF 3.0 REST API viewpoint object (camera, clipping planes,
component selection/visibility). The repo/ref/path context comes from `base`.

Remote repositories are cloned as bare repos to the OS cache directory (`~/.cache/ifcurl/` on Linux) on first use. Mutable refs (`@heads/`, `@HEAD`) trigger a `git fetch`; immutable refs (commit hashes, tags) use the cache as-is.

For private repositories, configure tokens per host in `~/.config/ifcurl/tokens.json`:

```json
{ "hosts": { "github.com": "ghp_…", "gitlab.example.com": "glpat_…" } }
```

**systemd deployment:** the service user typically has no home directory (`ProtectHome=yes`), so `~/.config` is inaccessible. The recommended approach for a server is a dedicated read-only Forgejo account (e.g. `ifcurl-bot`) added as a member of any private repositories that should be renderable. Generate an API token for that account and configure it as follows.

Set `XDG_CONFIG_HOME` to a readable path in the environment file (e.g. `/etc/ifcurl/env`) and ensure the file is readable by the service user:

```
XDG_CONFIG_HOME=/etc/ifcurl
```

```bash
chown root:ifcurl /etc/ifcurl/env
chmod 640 /etc/ifcurl/env
```

Then create `/etc/ifcurl/ifcurl/tokens.json` with the service account token:

```bash
mkdir -p /etc/ifcurl/ifcurl
echo '{"hosts":{"git.example.com":"<service-account-token>"}}' > /etc/ifcurl/ifcurl/tokens.json
chown root:ifcurl /etc/ifcurl/ifcurl/tokens.json
chmod 640 /etc/ifcurl/ifcurl/tokens.json
systemctl restart ifcurl-api
```

---

## Development

```bash
git clone https://github.com/brunopostle/ifcurl
cd ifcurl
pip install -e ".[service]"

# Python tests
python -m pytest tests/

# JavaScript tests (Node 18+)
node --test tests/test_viewer_url.mjs
```

### Roadmap

| Phase | Status | Description |
|---|---|---|
| 1 — Python core | ✓ done | URL parsing, git fetch, render |
| 2 — Preview service | ✓ done | HTTP service with caching |
| 3 — Forgejo integration | ✓ done | Viewer, JS markdown extension |
| 4 — OS protocol handler | ✓ done | `ifcurl-register` + `ifcurl-open`: registers `ifc://` on Linux, macOS, Windows; dispatches to Bonsai or any viewer |
| 5 — IFC Viewer integration | planned | Plugin for the open-source IFC Viewer |
| 6 — Federation | planned | `IFCDOCUMENTREFERENCE` cross-repo links |
| 7 — OpenCDE Foundation API | ✓ done | Discovery endpoint + OAuth2 proxy (Foundation API 1.1) |
| 8 — OpenCDE BCF API | ✓ done | BCF 3.0 REST API — connect Revit, Navisworks, Solibri directly |
| 9 — OpenCDE Documents API | ✓ done | Document versions + select-documents picker |

Development tasks are tracked with [beads](https://github.com/brunopostle/beads): `bd ready`.

---

## Licence

LGPL-3.0-or-later
