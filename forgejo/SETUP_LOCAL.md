# ifcurl + Forgejo Local Setup (Windows + Docker)

## Prerequisites

- Windows 10
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running
- Git
- The ifcurl repository cloned locally (e.g. `D:\Dropbox\GitHub\ifcurl`)

---

## Directory structure

```
C:\forgejo-local\
  docker-compose.yml
  nginx.conf
  custom\
    public\assets\    ← viewer.html, ifcurl.js, etc.
    templates\custom\ ← footer.tmpl

D:\Dropbox\GitHub\ifcurl\
  docker\
    Dockerfile        ← already in the repo
```

---

## Step 1 — Deploy ifcurl assets to the custom directory

Run from the ifcurl repo root in Git Bash:

```bash
DEST="C:/forgejo-local/custom"
mkdir -p "$DEST/public/assets" "$DEST/templates/custom"

cp forgejo/custom/public/assets/viewer.html         "$DEST/public/assets/"
cp forgejo/custom/public/assets/viewer.js           "$DEST/public/assets/"
cp forgejo/custom/public/assets/viewer-url.js       "$DEST/public/assets/"
cp forgejo/custom/public/assets/viewer-deps.js      "$DEST/public/assets/"
cp forgejo/custom/public/assets/fragments-worker.js "$DEST/public/assets/"
cp forgejo/custom/public/assets/web-ifc.wasm        "$DEST/public/assets/"
cp forgejo/custom/public/assets/web-ifc-mt.wasm     "$DEST/public/assets/"
cp forgejo/custom/public/assets/ifcurl.js           "$DEST/public/assets/"
cp forgejo/templates/custom/footer.tmpl             "$DEST/templates/custom/"
```

> **Git Bash note:** when assigning shell variables, do NOT use a `$` prefix —
> use `DEST="..."` not `$DEST="..."`.

---

## Step 2 — Create `C:\forgejo-local\docker-compose.yml`

Nginx sits in front of Forgejo and proxies the ifcurl service endpoints
(`/preview`, `/render_diff`, `/bcf`, `/select`) at the same origin as Forgejo.
This is required for PR diff images, which are fetched by the browser.

> **Note:** Forgejo's Docker image stores all data under `/data/gitea`, not
> `/var/lib/forgejo` as older guides suggest.

```yaml
services:
  nginx:
    image: nginx:alpine
    container_name: nginx
    ports:
      - "3000:80"
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
    depends_on:
      - forgejo
      - ifcurl
    restart: unless-stopped

  forgejo:
    image: codeberg.org/forgejo/forgejo:15
    container_name: forgejo
    environment:
      - USER_UID=1000
      - USER_GID=1000
    ports:
      - "2222:22"
    volumes:
      - forgejo-data:/data/gitea
      - ./custom/public:/data/gitea/public
      - ./custom/templates:/data/gitea/templates
    depends_on:
      - ifcurl
    restart: unless-stopped

  ifcurl:
    build:
      context: D:/Dropbox/GitHub/ifcurl
      dockerfile: docker/Dockerfile
    container_name: ifcurl
    restart: unless-stopped

volumes:
  forgejo-data:
```

---

## Step 3 — Create `C:\forgejo-local\nginx.conf`

```nginx
server {
    listen 80;

    location /preview {
        proxy_pass         http://ifcurl:8000;
        proxy_set_header   Host $host;
        proxy_read_timeout 120s;
    }

    location /render_diff {
        proxy_pass         http://ifcurl:8000;
        proxy_set_header   Host $host;
        proxy_read_timeout 120s;
    }

    location /bcf {
        proxy_pass         http://ifcurl:8000;
        proxy_set_header   Host $host;
        proxy_read_timeout 120s;
    }

    location /select {
        proxy_pass         http://ifcurl:8000;
        proxy_set_header   Host $host;
        proxy_read_timeout 120s;
    }

    location / {
        proxy_pass         http://forgejo:3000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";
    }
}
```

---

## Step 4 — Dockerfile (already in repo, shown for reference)

`docker/Dockerfile` is committed to the ifcurl repository. Key points:

- `libosmesa6` provides software OpenGL for headless rendering inside the
  container (no GPU or display server required).
- The `git config url.insteadOf` rewrite lets the ifcurl service clone repos
  from `localhost:3000` (as it appears in `ifc://` URLs) by transparently
  redirecting git to `forgejo:3000` inside the Docker network.

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    git libgl1 libglib2.0-0 libosmesa6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir ".[service]"

# Remap localhost:3000 → forgejo:3000 so git can clone from the Forgejo
# container by its Docker-internal hostname when processing ifc:// URLs.
RUN git config --global url."http://forgejo:3000/".insteadOf "http://localhost:3000/"

EXPOSE 8000
CMD ["ifcurl", "serve", "--host", "0.0.0.0", "--port", "8000", "--allowed-hosts", "localhost:3000"]
```

---

## Step 5 — Start everything

Make sure Docker Desktop is running, then:

```bash
cd C:/forgejo-local
docker compose up -d
```

The first run pulls the Forgejo and Nginx images and builds the ifcurl image —
this may take several minutes.

Open `http://localhost:3000` and complete the Forgejo setup wizard.

---

## Verify the setup

Check that Forgejo is serving the ifcurl assets:

```
http://localhost:3000/assets/ifcurl.js
```

You should see JavaScript, not a 404.

---

## What works

| Feature | Works? |
|---|---|
| "View in 3D" button on `.ifc` files | Yes |
| Browser WebGL viewer | Yes |
| PR diff 3D renders (green=added, red=removed) | Yes |
| `[label](ifc://...)` links in markdown → inline preview | Yes |
| CLI rendering (`ifcurl render "ifc://..."`) | Yes |
| Bare `ifc://...` text in markdown | Yes (avoid paired underscores in paths — use `[label](ifc://...)` form) |

For normal workflows, use `[label](ifc://...)` link syntax — the viewer's
**Issue** button generates this format automatically.

---

## Testing the diff render

To see a diff render on a commit page:

1. Push an `.ifc` file to a Forgejo repo
2. Modify the file (move geometry, add/remove elements) and push a second commit
3. Open the second commit's page in Forgejo — the diff section for the `.ifc`
   file shows a rendered PNG with added geometry in green and removed in red
4. Click the image to open the interactive viewer at that commit's viewpoint

---

## Useful commands

```bash
# View logs for all services
docker compose logs -f

# View ifcurl logs only
docker compose logs ifcurl

# Stop everything
docker compose down

# Rebuild ifcurl image after code changes
docker compose build ifcurl
docker compose up -d
```

---

## Tips

- **Model not visible in viewer?** Click the **fit** button in the toolbar to
  fit the camera to the model bounds.
- **Git Bash path mangling:** prefix `docker exec` commands that use Linux
  paths with `MSYS_NO_PATHCONV=1`.
- Forgejo data (repos, users, settings) is stored in the `forgejo-data` named
  Docker volume and persists across container restarts. It is lost if you
  remove the volume (`docker compose down -v`).
- The ifcurl service clones remote git repos as bare repos into the container's
  cache on first use. Mutable refs (`@heads/`, `@HEAD`) trigger a `git fetch`
  on each request; immutable refs (commit hashes, tags) use the cache as-is.
- No Go patch or Forgejo rebuild is needed — all features including bare
  `ifc://` URL rendering are handled client-side by `ifcurl.js`.
