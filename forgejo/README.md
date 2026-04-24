# Forgejo integration

This directory contains the Forgejo source patch and custom assets for the
ifcurl preview extension. The integration adds:

- **Markdown preview** — ifc:// URLs in markdown render as inline `<figure>`
  preview images fetched from the ifcurl preview service.
- **"View in 3D" button** — appears on `.ifc` file view pages alongside Raw /
  Permalink / History.
- **Browser viewer** — a self-contained WebGL IFC viewer served as a Forgejo
  custom asset.

---

## Directory layout

```
forgejo/
  go.patch                              ← diff against Forgejo source (apply once)
  modules/markup/markdown/
    ifc_url.go                          ← new Go source file (copy into source tree)
    ifc_url_test.go                     ← Go tests (copy into source tree)
  custom/public/assets/
    viewer.html                         ← browser IFC viewer (no rebuild needed)
    viewer-url.js                       ← viewer URL logic module
  templates/custom/
    footer.tmpl                         ← "View in 3D" button injection
```

---

## Prerequisites

- Forgejo source tree cloned and building cleanly
- `go` 1.21+ in PATH
- ifcurl preview service running and reachable from the Forgejo server

The patch was written against the `v13.0` branch of Forgejo. Check which
Forgejo commit the patch was authored against with:

```bash
cd /path/to/forgejo
git log --oneline modules/markup/markdown/markdown.go | head -3
```

---

## Applying the patch

### 1. Copy new Go source files

```bash
cp forgejo/modules/markup/markdown/ifc_url.go     /path/to/forgejo/modules/markup/markdown/
cp forgejo/modules/markup/markdown/ifc_url_test.go /path/to/forgejo/modules/markup/markdown/
```

### 2. Apply the diff to existing files

```bash
cd /path/to/forgejo
git apply /path/to/ifcurl/forgejo/go.patch
```

This adds one line to `modules/markup/markdown/markdown.go` and six lines to
`modules/setting/markup.go`. If the patch does not apply cleanly (e.g. after
a Forgejo upstream upgrade), the changes are small enough to apply by hand —
see `go.patch` for the exact hunks.

### 3. Run the Go tests

```bash
cd /path/to/forgejo
go test ./modules/markup/markdown/ -run TestIfcURL -v
```

All seven tests should pass before proceeding.

### 4. Build and deploy Forgejo

```bash
cd /path/to/forgejo
go build \
  -tags 'sqlite sqlite_unlock_notify' \
  -ldflags "-X 'forgejo.org/modules/setting.StaticRootPath=/usr/share/forgejo'" \
  -o forgejo .

sudo cp forgejo /usr/bin/forgejo
sudo systemctl restart forgejo
```

Adjust build tags and `-ldflags` to match your existing Forgejo build
configuration.

---

## Deploying custom assets (no rebuild required)

These files can be updated at any time without recompiling Forgejo.

### Viewer and URL logic

```bash
sudo cp forgejo/custom/public/assets/viewer.html    /etc/forgejo/public/assets/
sudo cp forgejo/custom/public/assets/viewer-url.js  /etc/forgejo/public/assets/
```

Served at `/assets/viewer.html` and `/assets/viewer-url.js`.

### "View in 3D" footer template

```bash
sudo mkdir -p /var/lib/forgejo/custom/templates/custom/
sudo cp forgejo/templates/custom/footer.tmpl /var/lib/forgejo/custom/templates/custom/
sudo systemctl restart forgejo
```

---

## Starting the preview service

Run the preview service co-located with Forgejo, passing `--allowed-hosts`
set to your Forgejo hostname so the service will only fetch from that instance:

```bash
ifcurl serve --allowed-hosts git.example.com
```

For a Forgejo instance on a non-standard port (e.g. 3000 for local dev):

```bash
ifcurl serve --allowed-hosts localhost:3000
```

`--allowed-hosts` accepts a comma-separated list. Omitting it allows all
non-private remote hosts, which is unsafe if the service is reachable from
untrusted clients.

## Configuration

Add to `/etc/forgejo/conf/app.ini`:

```ini
[ifcurl]
PREVIEW_SERVICE_URL = http://localhost:8000
```

Set `PREVIEW_SERVICE_URL` to the base URL of the running ifcurl preview
service. If left empty, ifc:// links in markdown render as plain links with
no preview image.

---

## Upgrading Forgejo

After upgrading Forgejo upstream:

1. Re-apply `go.patch` (or apply the two hunks by hand if context has shifted).
2. Copy `ifc_url.go` and `ifc_url_test.go` back in — they are not modified by
   upstream.
3. Run the Go tests to verify compatibility.
4. Rebuild and redeploy.

Custom assets and `footer.tmpl` do not require a rebuild and are unaffected by
Forgejo upgrades.
