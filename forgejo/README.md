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
  server-config/
    ifcurl-preview.service              ← systemd unit for the preview service
    gitconfig-ifcmerge                  ← git merge driver registration for /etc/gitconfig
    gitattributes                       ← system-wide gitattributes for bare repos
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

## Running the preview service

### As a systemd service (recommended)

A systemd unit is provided at `server-config/ifcurl-preview.service`.

```bash
# Create a dedicated user
sudo useradd --system --no-create-home --shell /usr/sbin/nologin ifcurl

# Install the unit (edit AllowedHosts and port first)
sudo cp forgejo/server-config/ifcurl-preview.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now ifcurl-preview
```

Edit the `ExecStart` line to set your Forgejo hostname:

```ini
ExecStart=/usr/local/bin/ifcurl serve \
    --host 127.0.0.1 \
    --port 8000 \
    --allowed-hosts git.example.com
```

Cache limits and other environment variables can be set in
`/etc/ifcurl/env` (one `KEY=value` per line):

```bash
IFCURL_CACHE_MAX_GB=10
IFCURL_T2_MAX=16
```

### Manually (for testing)

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

`PREVIEW_SERVICE_URL` is used **server-side** by Forgejo's markdown renderer to
fetch preview images when rendering ifc:// links in issue descriptions and
comments.  It runs on the Forgejo server itself, so `localhost:8000` is the
right value.

If left empty, ifc:// links in markdown render as plain links with no preview
image.

---

## Nginx reverse-proxy for PR diff images

The PR diff viewer (Case 3 in `footer.tmpl`) injects `<img src="/render_diff?…">`
tags that the **browser** must fetch.  The browser resolves `/render_diff` against
the Forgejo origin, so the ifcurl service must be reachable at the public URL.

Add these proxy locations to the Nginx virtual host that fronts Forgejo:

```nginx
# ifcurl preview service — served under the same origin as Forgejo
location /preview {
    proxy_pass         http://127.0.0.1:8000;
    proxy_set_header   Host $host;
    proxy_read_timeout 120s;
}

location /render_diff {
    proxy_pass         http://127.0.0.1:8000;
    proxy_set_header   Host $host;
    proxy_read_timeout 120s;
}
```

Without this proxy the "View in 3D" button (Case 1/2) still works — it opens
the viewer HTML which fetches the IFC file directly.  The PR diff images
(Case 3) will silently fail to load until the proxy is in place.

---

## IFC merge driver (ifcmerge)

Configuring `ifcmerge` as the git merge driver for `.ifc` files lets Forgejo
automatically merge IFC pull requests instead of marking them as conflicted.

### How Forgejo checks mergeability

Forgejo (with git ≥ 2.38) uses `git merge-tree --write-tree` to perform a
trial merge in the bare repository.  This command **does** invoke configured
merge drivers — confirmed by testing.  With ifcmerge set up correctly,
the "can be automatically merged" indicator on a PR reflects ifcmerge's
actual result, not a naive text-conflict check.

**Important:** bare repositories do not read committed `.gitattributes` files
from the tree.  The merge driver must be declared via a system-wide
`core.attributesFile` setting in `/etc/gitconfig`.

### Prerequisites

`ifcmerge` is a single Perl script from
[github.com/brunopostle/ifcmerge](https://github.com/brunopostle/ifcmerge).
Download and install it on the Forgejo server:

```bash
curl -o /usr/local/bin/ifcmerge \
  https://raw.githubusercontent.com/brunopostle/ifcmerge/main/ifcmerge
chmod +x /usr/local/bin/ifcmerge
```

Verify it is on `PATH` for the `forgejo` system user:

```bash
sudo -u forgejo which ifcmerge
```

### Deploy the config files

```bash
# System-wide gitattributes (the critical piece for bare repos)
sudo cp forgejo/server-config/gitattributes /etc/gitattributes

# Append merge driver registration + core.attributesFile to /etc/gitconfig
sudo git config --system include.path /path/to/ifcurl/forgejo/server-config/gitconfig-ifcmerge
```

Or apply the blocks from `server-config/gitconfig-ifcmerge` directly into
`/etc/gitconfig` by hand.

### Merge direction

The merge driver is asymmetrical: `ifcmerge` rewrites STEP IDs from `%B`
(theirs) to match `%A` (ours), so `%A`'s ID space is preserved.

For Forgejo, use the **"Merge commit"** strategy (not rebase or squash).
With a merge commit, `%A` is always the base branch (e.g. `main`) and
`%B` is the pull-request branch — so `main`'s STEP IDs are preserved and
existing cross-references remain valid.

A second driver `ifcmerge_ours` is defined in the config file for cases
where the base branch arrives as `%B`; see comments in
`server-config/gitconfig-ifcmerge` for details.

### Client-side `.gitattributes`

For client-side merges (`git merge` on a developer's machine) committed
`.gitattributes` files in the repository work fine.  It is still worth
adding one:

```
*.ifc merge=ifcmerge
```

This ensures developers with ifcmerge installed get automatic IFC merges
locally, and tools that read gitattributes (e.g. diff viewers) can
identify `.ifc` files correctly.

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
