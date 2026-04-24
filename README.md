# ifcurl

**ifcurl** is a URL scheme, tools, and server integration for addressing shareable views of IFC building models stored in git repositories.

```
ifc://github.com/brunopostle/simple-ifc@heads/main?path=building.ifc&selector=IfcWall&fov=60
```

An `ifc://` URL encodes everything needed to reproduce a specific model view: the git source, the file, which elements to show, and the camera position. Like a permalink for BIM — paste it, share it, embed it in documentation.

---

## Architecture

Four interlocking components:

| Component | Language | What it does |
|---|---|---|
| **Python library + CLI** | Python | Parse ifc:// URLs, fetch IFC from git, render PNG |
| **Preview service** | Python | HTTP server — PNGs on demand |
| **Forgejo integration** | Go + assets | Inline markdown previews, "View in 3D" button |
| **Browser viewer** | JavaScript | Interactive WebGL viewer, served as a Forgejo asset |

```
Markdown source:
  [Section cut](ifc://host/org/repo@heads/main?path=building.ifc&clip=0,0,3,0,0,-1)
                                      │
Forgejo renders (via Go patch):        │
  <figure>                             │
    <img src="/preview?url=..."> ──────┤── preview service renders PNG
    <a href="/assets/viewer.html?url=…">   browser viewer (WebGL, interactive)
  </figure>
```

The viewer is also reachable directly — paste or type an `ifc://` URL into its toolbar, or drag one onto the page.

---

## ifc:// URL scheme

Full specification: [`SPECIFICATION.md`](SPECIFICATION.md)

```
ifc://[user@]host/org/repo@<ref>?<parameters>
```

Transport is inferred from the URL structure — no prefix needed:

| URL form | Transport |
|---|---|
| `ifc://host/org/repo` | HTTPS |
| `ifc://git@host/org/repo` | SSH |
| `ifc:///path/to/repo` | Local file |

Refs follow git namespace form to avoid branch/tag ambiguity:

| Form | Meaning |
|---|---|
| `@heads/main` | Branch tip |
| `@tags/v1.2` | Tag |
| `@abc123def` | Commit hash |
| `@HEAD` | Default branch |

Key query parameters:

| Parameter | Description |
|---|---|
| `path=` | IFC file path within the repository |
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

Remote repositories are cloned as bare repos to the OS cache directory
(`~/.cache/ifcurl/` on Linux) on first use. Mutable refs (`@heads/`, `@HEAD`)
trigger a `git fetch`; immutable refs (commit hashes, tags) use the cache
as-is.

For private repositories, configure tokens per host in
`~/.config/ifcurl/tokens.json`:

```json
{ "hosts": { "github.com": "ghp_…", "gitlab.example.com": "glpat_…" } }
```

---

## Preview service

An HTTP service that renders `ifc://` URLs to PNG, intended for co-location
with a Forgejo/Gitea instance.

```bash
pip install "ifcurl[service]"
ifcurl serve --allowed-hosts git.example.com     # restrict to your Forgejo host
ifcurl serve --host 0.0.0.0 --port 9000 --allowed-hosts git.example.com
```

Pass `--allowed-hosts` as a comma-separated list of hostnames (with optional
`:port`) the service is allowed to fetch from. This prevents the preview
endpoint from being used to reach internal services. Omitting it allows all
non-private remote hosts.

### Endpoints

| Endpoint | Description |
|---|---|
| `POST /preview` | Render an ifc:// URL to PNG |
| `GET /preview?url=ifc://…` | Same, via query string (used by Forgejo `<img>` tags) |
| `POST /bcf` | Generate a BCF 2.1 zip from an ifc:// URL viewpoint |

### Caching

| Tier | Key | Contents | Notes |
|---|---|---|---|
| 2 | commit hash + path | IFC bytes | In-memory LRU |
| 3 | commit hash + path + selector | Resolved element set | In-memory LRU |
| 4 | SHA-256 of full URL | Rendered PNG | Filesystem; immutable refs only |

Tier 4 is never written for mutable refs (`@heads/`, `@HEAD`).

---

## Forgejo integration

A source patch for Forgejo that adds:

- **Inline markdown preview** — `[label](ifc://…)` and bare `ifc://…` in
  markdown render as `<figure>` preview images linked to the browser viewer.
- **"View in 3D" button** — appears on `.ifc` file view pages alongside Raw /
  Permalink / History.
- **Browser viewer asset** — `viewer.html` + `viewer-url.js` served at
  `/assets/viewer.html`.

See [`forgejo/README.md`](forgejo/README.md) for full apply, build, and
deployment instructions.

### Quick summary

```bash
# Apply the Go source patch
cp forgejo/modules/markup/markdown/ifc_url{,_test}.go /path/to/forgejo/modules/markup/markdown/
cd /path/to/forgejo && git apply /path/to/ifcurl/forgejo/go.patch

# Build Forgejo
go build -tags 'sqlite sqlite_unlock_notify' \
  -ldflags "-X 'forgejo.org/modules/setting.StaticRootPath=/usr/share/forgejo'" \
  -o forgejo . && sudo cp forgejo /usr/bin/forgejo

# Deploy assets (no rebuild needed)
sudo cp forgejo/custom/public/assets/viewer*.* /etc/forgejo/public/assets/
sudo cp forgejo/templates/custom/footer.tmpl /var/lib/forgejo/custom/templates/custom/
sudo systemctl restart forgejo

# Install and start preview service as a systemd unit
sudo cp forgejo/server-config/ifcurl-preview.service /etc/systemd/system/
# edit ExecStart --allowed-hosts to match your Forgejo hostname, then:
sudo systemctl enable --now ifcurl-preview
```

Add to `/etc/forgejo/conf/app.ini`:

```ini
[ifcurl]
PREVIEW_SERVICE_URL = http://localhost:8000
; Optional: Forgejo API token for a read-only machine user.
; The preview service will use this token to fetch IFC files from private
; repositories on this Forgejo instance.  The token is appended as a query
; parameter in the <img src> URL generated by the markdown extension, so it
; is visible in page source.  Only set this on trusted private instances.
; SERVICE_TOKEN = <forgejo-api-token>
```

**Private repositories:** for the preview service to access private repos on
this Forgejo instance, create a machine user with read access and set
`SERVICE_TOKEN` to its API token.  The service will use this token when
fetching IFC files server-side.  Alternatively, configure the preview service's
own credentials in `~/.config/ifcurl/tokens.json` under the service user
account — the service already reads host tokens from that file on startup.

---

## Browser viewer

A self-contained WebGL IFC viewer (`viewer.html`) built on
[`@thatopen/components`](https://github.com/ThatOpenCompany/that-open-engine).
No build step — loads dependencies from CDN.

**Features:**

- Toolbar with raw ifc:// URL input and structured fields (repo, ref, path,
  selector) — editing any field reloads the model
- Selector filtering — `IfcWall`, `IfcWall+IfcSlab` (type-name union only in the
  browser viewer; full IfcOpenShell attribute/property filters are applied
  server-side by the preview service and are reflected in the URL but not
  applied visually in the viewer)
- Clipping planes — `✂ clip` button, double-click on model surface to place;
  drag handles to adjust; planes serialised back into the URL
- FOV control, camera sync — the URL in the browser bar always reflects the
  current view and is shareable
- Drag-and-drop ifc:// URLs onto the page
- **Click to identify** — click any element to see its IFC type, name, and
  GlobalId in a side panel; copy GlobalId to clipboard for use in selectors
- **⎘ copy** button — copies the current ifc:// URL to clipboard
- **Issue** button — opens a new issue on the git host with the ifc:// URL
  pre-filled in the body (works for Forgejo, GitHub, GitLab)
- **BCF export** — exports the current view (camera, clipping planes, selected
  elements) as a BCF 2.1 file for import into Revit, Navisworks, etc.
- Download progress — shows percentage or MB while fetching large IFC files

### Collaboration workflow

`ifc://` URLs function as view permalinks that can be embedded anywhere:
Forgejo issues and comments, pull request discussions, markdown documentation,
Slack, or email.

**Basic flow:**

1. Open the viewer and navigate to the view you want to share
2. Add clipping planes or a selector to isolate the relevant geometry
3. Click **⎘** to copy the ifc:// URL, or **Issue** to open a pre-filled issue
4. Paste the URL into a Forgejo issue, PR comment, or any markdown file —
   Forgejo will render it inline as a linked preview image

**Referencing specific elements:**

Click any element to see its GlobalId. Copy the GlobalId and paste it directly
as the `selector=` value — a bare 22-character GlobalId selects that one element
(e.g., `selector=325Q7Fhnf67OZC$$r43uzK`). Use `visibility=highlight`, `ghost`,
or `isolate` to control how the selection is displayed.

**BCF export for external tools:**

Use the **BCF** button to export a BCF 2.1 file. The resulting `.bcf` can be
attached to a Forgejo issue as a file, or imported into Revit, Navisworks,
Solibri, or any BCF-compatible tool to communicate the exact viewpoint and
element selection to contractors or consultants using proprietary software.

### Authentication

When the viewer is served from the same Forgejo instance
(`https://git.example.com/assets/viewer.html`), it shares the browser session
cookie. Any IFC file that the logged-in user can access on that Forgejo instance
loads without extra configuration — private repositories work automatically for
authenticated users.

IFC files hosted on other platforms (GitHub, GitLab, a different Forgejo
instance) are fetched directly from the browser. Only public repositories work
for external hosts; there is currently no authentication path for external
private repositories in the browser viewer.

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

# Go tests (requires Forgejo source tree with patch applied)
cd /path/to/forgejo
go test ./modules/markup/markdown/ -run TestIfcURL -v
```

### Roadmap

| Phase | Status | Description |
|---|---|---|
| 1 — Python core | ✓ done | URL parsing, git fetch, render |
| 2 — Preview service | ✓ done | HTTP service with caching |
| 3 — Forgejo integration | ✓ done | Go patch, viewer, markdown extension |
| 4 — Bonsai integration | planned | Protocol handler + "Copy view URL" |
| 5 — IFC Viewer integration | planned | Plugin for the open-source IFC Viewer |
| 6 — Federation | planned | `IFCDOCUMENTREFERENCE` cross-repo links |

Development tasks are tracked with [beads](https://github.com/brunopostle/beads): `bd ready`.

---

## Licence

LGPL-3.0-or-later
