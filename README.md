# ifcurl

**ifcurl** is a command-line tool and Python library for resolving `ifc://` URLs — a URL scheme for addressing a specific view of an IFC building model stored in a git repository.

A single URL encodes the model source (git host, repository, ref), an optional element selection, and an optional camera viewpoint. ifcurl fetches the model and renders a PNG, the way curl fetches a web resource.

```
ifcurl render "ifc://github.com/brunopostle/simple-ifc@heads/main?path=_test_simple.ifc"
```

![Example render](https://raw.githubusercontent.com/brunopostle/simple-ifc/main/_test_simple.png)

---

## URL format

```
ifc://[user@]host/org/repo@<ref>?<parameters>
```

### Transport

Transport is inferred from the URL structure — no prefix required:

| URL form | Transport |
|---|---|
| `ifc://host/org/repo` | HTTPS |
| `ifc://git@host/org/repo` | SSH |
| `ifc:///path/to/repo` | Local file |

### Ref

| Form | Meaning |
|---|---|
| `@heads/main` | Branch tip |
| `@tags/v1.2` | Tag |
| `@abc123def` | Commit hash |
| `@HEAD` | Default branch |

### Parameters

| Parameter | Description |
|---|---|
| `path=` | Path to the IFC file within the repository |
| `selector=` | [IfcOpenShell selector](https://docs.ifcopenshell.org/ifcopenshell-python/selector_syntax.html) expression to filter elements |
| `camera=px,py,pz,dx,dy,dz,ux,uy,uz` | Camera position, view direction, and up vector in IFC world coordinates |
| `fov=60` | Perspective field of view in degrees (use with `camera`) |
| `scale=50` | Orthographic view-to-world scale (use with `camera`) |
| `clip=px,py,pz,nx,ny,nz` | Clipping plane — repeatable |
| `visibility=` | `highlight` (default), `ghost`, or `isolate` |

### Visibility modes

| Mode | Behaviour |
|---|---|
| `highlight` | All elements shown; selected elements shown in highlight colour |
| `ghost` | Selected elements shown normally; all others dimmed |
| `isolate` | Only selected elements shown |

---

## Examples

Open a model at the default isometric view:

```
ifcurl render "ifc://github.com/brunopostle/simple-ifc@heads/main?path=_test_simple.ifc"
```

Walls only, ghost mode:

```
ifcurl render "ifc://github.com/brunopostle/simple-ifc@heads/main?path=_test_simple.ifc&selector=IfcWall&visibility=ghost"
```

Perspective view with a clipping plane (section cut at z=3):

```
ifcurl render "ifc://example.com/org/project@abc123def?path=models/building.ifc&camera=10,20,5,0,-1,0,0,0,1&fov=60&clip=0,0,3,0,0,-1"
```

Orthographic plan view from above:

```
ifcurl render "ifc://example.com/org/project@tags/v2.0?path=models/building.ifc&camera=0,0,8,0,0,-1,0,1,0&scale=50"
```

Local repository:

```
ifcurl render "ifc:///home/alice/projects/office@HEAD?path=model.ifc"
```

Save to a specific file:

```
ifcurl render "ifc://..." -o output.png
```

---

## Installation

```bash
pip install ifcurl
```

For rendering support (required for `ifcurl render`):

```bash
pip install "ifcurl[render]"
```

**Dependencies:**

- [ifcopenshell](https://ifcopenshell.org) — IFC parsing and selector execution
- [GitPython](https://gitpython.readthedocs.io) — git repository access
- [platformdirs](https://platformdirs.readthedocs.io) — OS-appropriate cache directory
- [pyvista](https://pyvista.org) + [numpy](https://numpy.org) — 3D rendering (optional, required for `render`)

---

## Python library

```python
from ifcurl import IfcUrl, fetch_ifc_bytes
import ifcopenshell
import tempfile, os
from ifcurl import render

url = IfcUrl.parse("ifc://github.com/brunopostle/simple-ifc@heads/main?path=_test_simple.ifc&selector=IfcWall&visibility=ghost")

ifc_bytes = fetch_ifc_bytes(url)

tmp_fd, tmp_path = tempfile.mkstemp(suffix=".ifc")
os.write(tmp_fd, ifc_bytes)
os.close(tmp_fd)
model = ifcopenshell.open(tmp_path)
os.unlink(tmp_path)

png_bytes = render.render(
    model,
    selector=url.selector,
    camera=url.camera,
    fov=url.fov,
    scale=url.scale,
    clips=url.clips or None,
    visibility=url.visibility,
)

with open("output.png", "wb") as f:
    f.write(png_bytes)
```

---

## Preview service

ifcurl includes an HTTP preview service for use by Gitea and other consumers.

```bash
pip install "ifcurl[service]"
ifcurl serve                          # 127.0.0.1:8000
ifcurl serve --host 0.0.0.0 --port 9000
```

### Endpoint

```
POST /preview
Content-Type: application/json

{"url": "ifc://..."}
```

Returns `image/png`.

### Service caching

| Tier | Key | Contents | Notes |
|---|---|---|---|
| 2 | commit hash + path | IFC bytes | In-memory LRU, avoids repeated git blob reads |
| 3 | commit hash + path + selector | Resolved GlobalId set | In-memory LRU, avoids re-running selector |
| 4 | SHA-256 of full URL | Rendered PNG | Filesystem, immutable refs only |

Tier 4 is never written for mutable refs (`@heads/`, `@HEAD`) since the underlying commit may change.

---

## Remote repository caching

Remote repositories are cloned as bare repos to the OS cache directory (`~/.cache/ifcurl/` on Linux, `~/Library/Caches/ifcurl/` on macOS, `%LOCALAPPDATA%\ifcurl\Cache\` on Windows) on first use. Subsequent calls to the same repository reuse the cache. Mutable refs (`@heads/`, `@HEAD`) trigger a `git fetch` to pick up upstream changes; immutable refs (commit hashes, tags) use the cache as-is.

---

## Development

```bash
git clone https://github.com/brunopostle/ifcurl
cd ifcurl
pip install -e ".[render]"
python -m pytest tests/
```

---

## Licence

LGPL-3.0-or-later
