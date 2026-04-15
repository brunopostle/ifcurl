# IFC URL — Specification

## 1. Overview

IFC URL is a URL scheme for addressing a specific view of an IFC model stored
in a Git repository. A single URL encodes the model source, an optional
element selection, and an optional camera viewpoint.

The scheme identifier is `ifc://`.

---

## 2. Git source encoding

### Transport inference

The git transport is inferred from the URL structure. No transport prefix is
required.

| URL form | Inferred transport |
|---|---|
| `ifc://user@host/org/repo` | SSH |
| `ifc://host/org/repo` | HTTPS |
| `ifc:///path/to/repo` | Local file |

The presence of `user@` userinfo signals SSH. A bare hostname signals HTTPS.
An empty authority (triple slash) signals a local file path.

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
present.

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
1. Extract the GUID list from `Components/Selection`
2. Construct an IfcOpenShell GUID selector
3. Convert the BCF camera to `camera`, `fov` or `scale` parameters
4. Convert clipping planes to `clip` parameters

---

## 7. Viewer compliance

A compliant viewer must:

- Register `ifc://` as an OS-level protocol handler
- Resolve the git source (repo, ref, path) and fetch the IFC file
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
