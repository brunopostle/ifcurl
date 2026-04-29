# IFC URL — Specification

## 1. Overview

IFC URL is a URL scheme for addressing a specific view of an IFC model. A
single URL encodes the model source — a Git repository or an OpenCDE Common
Data Environment — an optional element selection, and an optional camera
viewpoint.

The scheme identifier is `ifc://`.

---

## 2. Git source encoding

### Transport inference

The transport is inferred from the URL structure. No transport prefix is
required.

| URL form | Inferred transport |
|---|---|
| `ifc://user@host/org/repo` | SSH (git) |
| `ifc://host/org/repo` | HTTPS (git) |
| `ifc:///path/to/repo` | Local file |
| `ifc://host/...?document_id=...` | OpenCDE HTTPS (see §9) |

The presence of `user@` userinfo signals SSH. A bare hostname signals HTTPS
git. An empty authority (triple slash) signals a local file path. The presence
of a `document_id` query parameter signals OpenCDE transport regardless of the
host form; in this case no `@ref` component is used.

Credentials are never embedded in the URL. Authentication is handled by the
platform's git credential store (desktop apps) or by sideloaded tokens
(preview service).

SCP-style SSH addresses (`git@host:org/repo`) are normalised to
`ifc://git@host/org/repo` when generating URLs.

### Ref format

Refs follow git's namespace structure to prevent ambiguity between branches
and tags that share a name.

| Ref type | Form | Example |
|---|---|---|
| Branch | `@heads/<name>` | `@heads/main` |
| Tag | `@tags/<name>` | `@tags/v1.2` |
| Commit hash | `@<hash>` | `@abc123def` |
| Default branch | `@HEAD` | `@HEAD` |

The `@` character delimits the repository path from the ref. It is always
present in git URLs. It is omitted in OpenCDE URLs (see §9).

### Full structure

```
ifc://[user@]host/org/repo@<ref>?<parameters>
```

---

## 3. Parameters

All parameters are optional. A URL with no parameters opens the model at its
default view with no selection.

### `path`

Path to the IFC file within the repository, relative to the repository root.

```
path=models/architectural/building.ifc
```

May be omitted when the target is a federated root file whose linked model
references are defined within the IFC itself (see section 5).

### `selector`

An IfcOpenShell selector expression, percent-encoded. The selector syntax is
specified at:

    https://docs.ifcopenshell.org/ifcopenshell-python/selector_syntax.html

Selectors are single-line expressions. Filters within a group are
comma-separated; groups are joined with `+` for union.

```
selector=IfcWall
selector=IfcWall,+Name="Core+Wall"
selector=IfcWall+IfcSlab
selector=IfcWall,+Pset_WallCommon.FireRating=2HR
selector=325Q7Fhnf67OZC$$r43uzK
```

When omitted, no selection is applied.

### `camera`

Nine comma-separated decimal values defining the camera in IFC world
coordinates (see section 4):

```
camera=px,py,pz,dx,dy,dz,ux,uy,uz
```

| Values | Meaning |
|---|---|
| `px,py,pz` | Camera position |
| `dx,dy,dz` | View direction vector (unit vector) |
| `ux,uy,uz` | Up vector (unit vector) |

`camera` must be accompanied by either `fov` (perspective) or `scale`
(orthographic). Their presence signals the projection type. Exactly one must
be present if `camera` is present.

When `camera` is omitted entirely, the viewer fits the view to the selection
if a selector is present, or shows the model's default view otherwise.

### `fov`

Field of view in degrees. Signals perspective projection.

```
fov=60
```

### `scale`

View-to-world scale. Signals orthographic projection. The value represents
how many model units correspond to one unit in the normalised view, equivalent
to BCF's `ViewToWorldScale`.

```
scale=50
```

### `clip`

A clipping plane, defined by a point on the plane and a normal vector pointing
toward the visible side. All values are in IFC world coordinates.

```
clip=px,py,pz,nx,ny,nz
```

Repeatable. Multiple `clip` parameters define the intersection of their
half-spaces.

```
clip=0,0,3,0,0,-1&clip=0,0,0,0,0,1
```

### `document_id`

OpenCDE Documents API identifier for the document to fetch. Its presence
signals OpenCDE transport (see §9). Mutually exclusive with `path`.

```
document_id=bf546064-6b97-4730-a094-c21ab929c91a
```

The value is an opaque string assigned by the CDE; no particular format is
required by the OpenCDE specification.

### `version_index`

OpenCDE version index of the specific document version to fetch. Must be a
positive integer matching the `version_index` field returned by the OpenCDE
Documents API, where higher values indicate newer versions. Only meaningful
when `document_id` is also present.

```
version_index=3
```

When omitted, the resolver fetches the version with the highest
`version_index` (the current version). A URL with `version_index` present is
immutable; one with `version_index` absent is mutable.

### `visibility`

Controls how selected and unselected elements are displayed.

| Value | Behaviour |
|---|---|
| `highlight` | Show all elements, highlight selection (default) |
| `ghost` | Show selection normally, dim all other elements |
| `isolate` | Show only selected elements, hide all others |

When omitted, `highlight` is assumed.

---

## 4. Coordinate system

All spatial values (`camera`, `clip`) are expressed in the IFC project's
world coordinate system, consistent with BCF viewpoint conventions. Local
coordinate systems, object placements, and georeferenced survey coordinates
are not used.

Where an IFC file uses `IfcMapConversion` (IFC 4.1+) to define a
georeferenced coordinate system, camera and clipping plane values remain in
IFC world coordinates, not in the mapped survey coordinate system.

---

## 5. Federation

Federated models are referenced within the IFC file itself using
`IFCDOCUMENTINFORMATION` with a `LINKED_MODEL` purpose, associated with the
root `IFCPROJECT` entity via `IFCRELASSOCIATESDOCUMENT`. The location is
carried in `IFCDOCUMENTREFERENCE`:

```
#46471=IFCDOCUMENTINFORMATION('X','model.ifc',$,$,$,$,'LINKED_MODEL',$,$,$,$,$,$,$,$,$,$);
#46472=IFCRELASSOCIATESDOCUMENT('0mypTfsjT...',$,$,$,(#10510),#46471);
#46473=IFCDOCUMENTREFERENCE('../conservatory-ifc/model.ifc','<4x4 matrix>',$,$,#46471);
```

The second field of `IFCDOCUMENTREFERENCE` is a 4x4 transformation matrix
(sixteen comma-separated values) that positions the linked model in the shared
coordinate system. This is resolved entirely by the viewer.

### Location field values

The location field (first field of `IFCDOCUMENTREFERENCE`) supports:

- **Relative path** — resolved relative to the root IFC file's directory
  within the repository, using the same repo and ref context
- **Absolute local path** — resolved on the local filesystem (desktop use only)
- **`ifc://` URL** — resolved as a full IFC URL, enabling cross-repo and
  cross-server federation

When a relative path would traverse above the repository root, an `ifc://`
URL must be used instead. This is the primary motivation for supporting
`ifc://` in the location field.

---

## 6. BCF interoperability

The camera and selection in an IFC URL map directly to BCF viewpoint concepts.

**IFC URL to BCF viewpoint:**
1. Resolve the selector against the model to obtain a set of GUIDs
2. Construct a BCF `PerspectiveCamera` or `OrthogonalCamera` from the camera
   parameters
3. Add clipping planes if present
4. Populate `Components/Selection` with the resolved GUIDs

**BCF viewpoint to IFC URL:**
1. Extract the GUID list from `Components/Selection`; if `DefaultVisibility`
   is false, use the GUIDs from `Visibility/Exceptions` instead and set
   `visibility=isolate`
2. Construct an IfcOpenShell GUID selector (bare GUIDs joined with `+`)
3. Convert the BCF camera to `camera`, `fov` or `scale` parameters
4. Convert clipping planes to `clip` parameters

---

## 7. Viewer compliance

A compliant viewer must:

- Register `ifc://` as an OS-level protocol handler
- Resolve the git source (repo, ref, path) and fetch the IFC file
- Resolve an OpenCDE document source via Foundation API discovery and the
  Documents API (see §9)
- Resolve an IfcOpenShell selector against the loaded model
- Apply perspective camera (`camera` + `fov`)
- Apply orthographic camera (`camera` + `scale`)
- Apply clipping planes (`clip`)
- Apply visibility mode (`highlight`, `ghost`, `isolate`)
- Resolve relative `IFCDOCUMENTREFERENCE` paths using the root file's repo
  and ref context
- Resolve `ifc://` `IFCDOCUMENTREFERENCE` locations

Optional extensions:

- Semantic colouring
- Advanced visual styles beyond the three visibility modes

---

## 8. Complete examples

Open a model at the default view:

```
ifc://example.com/org/project@heads/main?path=models/building.ifc
```

Perspective view of selected walls:

```
ifc://git@example.com/org/project@abc123def?path=models/building.ifc&selector=IfcWall,+Name="Core+Wall"&camera=10,20,5,0,-1,0,0,0,1&fov=60&visibility=ghost
```

Orthographic plan view at level 2 with a clipping plane:

```
ifc://example.com/org/project@tags/v2.0?path=models/building.ifc&camera=0,0,8,0,0,-1,0,1,0&scale=50&clip=0,0,5,0,0,-1
```

Local file, default branch:

```
ifc:///home/alice/projects/office@HEAD?path=model.ifc
```

OpenCDE document, specific version, perspective view of all walls:

```
ifc://cde.example.com/project-123?document_id=bf546064-6b97-4730-a094-c21ab929c91a&version_index=3&selector=IfcWall&camera=10,20,5,0,-1,0,0,0,1&fov=60&visibility=ghost
```

OpenCDE document, current version, no view parameters (opens at default view):

```
ifc://cde.example.com/project-123?document_id=bf546064-6b97-4730-a094-c21ab929c91a
```

---

## 9. OpenCDE transport

When a `document_id` parameter is present, the URL addresses a document on an
OpenCDE-compliant Common Data Environment rather than a Git repository. The
`path` parameter and the `@ref` component are both absent.

### URL structure

```
ifc://host[/project-path]?document_id=<id>[&version_index=<N>]&<view-parameters>
```

The authority and optional path locate the CDE server and project. The path
structure is implementation-defined and is passed through to the resolver
without interpretation; it may be empty, a single project identifier, or a
deeper hierarchy depending on the CDE.

### Version reference

| `version_index` | Meaning | Tier 4 caching |
|---|---|---|
| `version_index=N` | Specific immutable version | Safe |
| absent | Current version (highest index) | Never |

The `version_index` value corresponds directly to the field of the same name
in the OpenCDE Documents API: a machine-readable integer sequence where higher
values represent newer versions. It is distinct from `version_number`, which
is a free-form human-readable label that varies across CDE implementations and
is not used in `ifc://` URLs.

### Resolver workflow

1. **Discover APIs** — `GET https://host/foundation/versions`. This endpoint
   is public and requires no authentication. It returns the base URLs for all
   OpenCDE APIs on this server, including the Documents API endpoint and OAuth2
   configuration.

2. **Authenticate** — OAuth2 authorization code or implicit flow, using the
   configuration obtained from step 1. Credentials are never embedded in the
   `ifc://` URL. Viewer implementations served from the same browser session as
   the CDE web interface may share the existing session cookie, avoiding an
   explicit OAuth2 flow for authenticated users.

3. **Resolve document version** — `POST <documents_api_base>/document-versions`
   with body `{"document_ids": ["<document_id>"]}`. If `version_index` is
   present, select the returned version whose `version_index` matches; if
   absent, select the version with the highest `version_index`.

4. **Download** — Fetch the IFC file from the `document_version_download` URL
   returned in the Documents API response.

### Federation

`ifc://` URLs addressing OpenCDE documents may appear in
`IFCDOCUMENTREFERENCE` location fields (see §5) alongside relative paths and
git-hosted `ifc://` URLs. No changes to the federation model are required.
