# Continuous integration for IFC repositories

**Status: designed, not yet implemented.** This documents the intended Forgejo Actions
setup for validating IFC models on push and pull request.

The premise: with IFC in git and Forgejo providing review, branching and merging, an
IFC repository has essentially the same shape as a software repository — so it benefits
from the same CI. A bad model should fail its checks before it reaches `main`, and the
result should appear as the usual pass/fail status against the commit or PR.

Two checks, matching the working GitHub Actions setup in
[`simple-ifc`](https://github.com/brunopostle/simple-ifc):

| Check | Tool | Gates on |
|---|---|---|
| Schema/rule validation | `ifcopenshell.validate --rules` | every `*.ifc` parses and satisfies IFC schema rules |
| IDS compliance | `ifctester` + [`idssplit`](https://github.com/brunopostle/idssplit) | every model satisfies every rule in every `IDS/**/*.ids` |

Both skip `libraries/` — those are vendored component sources, not deliverable models.

## The `ifc-ci` container image

The workflows are deliberately thin, because everything slow is baked into the image.
On GitHub the runs spend most of their time in `setup-python` and `pip install
ifcopenshell` (a large wheel) on *every* run; pre-installing removes that entirely and
gives fast startup.

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      git nodejs ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir ifcopenshell ifctester && \
    pip install --no-cache-dir --no-deps \
      https://github.com/brunopostle/idssplit/releases/download/0.1.0/idssplit-0.1.0-py3-none-any.whl
```

Notes on the contents:

- **No OpenGL/OSMesa stack.** `validate --rules` and `ifctester` are parsing-level
  checks — neither builds geometry, so `libgl1`/`libosmesa6`/`libglib2.0-0` are not
  needed. (The *render* service does need them; the CI image does not.) This keeps the
  image substantially smaller and faster to pull.
- **`nodejs` is required**, even though nothing here is a Node project:
  `actions/checkout` is a JavaScript action and the runner needs a Node interpreter
  inside the container to run it. Without it you must hand-roll `git clone` and lose
  correct pull-request ref handling.
- **`idssplit` is not on PyPI** — it installs from a release wheel with `--no-deps`.
  Baking it in also removes a per-run fetch from an external host.

Build and push to any OCI registry, including Forgejo's own built-in one:

```bash
buildah bud -t <registry>/<owner>/ifc-ci:latest -f Containerfile .
podman push <registry>/<owner>/ifc-ci:latest
```

If pushing to a Forgejo registry behind nginx, raise `client_max_body_size` on that
vhost first — the default in many configurations is far below the image size, and the
push fails with an opaque `413`.

## Workflows

Port of `simple-ifc`'s `.github/workflows/` with the setup steps removed, since the
image now provides them. Place in `.forgejo/workflows/`.

Only `runs-on:` differs from the GitHub originals — everything below `steps:` is
unchanged, including the `::group::` workflow commands, which Forgejo supports.

### `ifc-lint.yml`

```yaml
name: IFC Validation
on: [push, pull_request]

jobs:
  lint-ifc:
    runs-on: ifc-ci                      # label mapped to the ifc-ci image
    container:
      image: <registry>/<owner>/ifc-ci:latest
    steps:
      - uses: actions/checkout@v3
      - name: Run IFC lint checks
        run: |
          set -e
          shopt -s globstar nullglob
          for file in **/*.ifc; do
            [[ "$file" == libraries/* ]] && continue
            echo "Validating $file..."
            python3 -m ifcopenshell.validate --rules "$file"
          done
```

This gates correctly on its own: `ifcopenshell.validate` calls `sys.exit(-1)`/`exit(1)`
on failure, so `set -e` fails the job.

### `ids-lint.yml`

Keep the existing script body. It splits each IDS file into one file per rule with
`idssplit`, then runs every rule against every model, so a failure names the specific
rule rather than just the specification.

### Why the IDS check greps output instead of using the exit status

Worth recording, because it looks like a bug and is not:

**`ifctester`'s CLI never sets an exit code.** `ifctester/__main__.py` loads the model,
validates, prints the report, and falls off the end of the script — there is no
`sys.exit` anywhere in it. A model that violates every rule in the IDS still exits `0`.

A naive `run: ifctester model.ifc spec.ids` therefore produces a **permanently green
check that never fails**, which is worse than having no CI at all. The `simple-ifc`
workflow avoids this by running with `|| true` and grepping stdout for `[FAIL]`.

An alternative worth considering is the JSON reporter, which exposes a top-level
boolean:

```bash
python3 -m ifctester --reporter Json --output report.json "$rule_ids" "$ifc"
python3 -c 'import json,sys; sys.exit(0 if json.load(open("report.json"))["status"] else 1)'
```

`Json.report()` sets `results["status"]` to the conjunction of all specification
statuses, alongside `total_checks`/`total_checks_pass` counts. This is a more stable
contract than parsing console output (which currently relies on `--no-color` keeping
the text greppable), and the report file is also a useful build artifact. The
grep approach is proven and works; this is a hardening option, not a correction.

## Runner requirements

These workflows need no write access to anything outside their container, so they can
share a single runner. Run it as a dedicated unprivileged user with an **empty
`valid_volumes`** in the runner's `config.yaml` — that is the root-owned allowlist of
host paths a job may bind-mount, and a workflow cannot override it. Keeping it empty
means a compromised or hostile workflow (an outside contributor's PR, for instance)
cannot reach the host filesystem at all.

A rootless Podman socket is sufficient as the container backend; the runner speaks the
Docker API and Podman provides a compatible socket. If using rootless Podman under a
system user, remember `loginctl enable-linger <user>` or the socket disappears when the
login session ends.

## Known gap: no notification on failure

Forgejo 10.0.3 ships no mail template for Actions failures — the embedded set covers
auth, issues, collaborator/repo-transfer/new-user notifications and releases, but
nothing for workflow runs. The pass/fail status appears in the web UI as normal, but
unlike GitHub **no email is sent when a run fails**. On a repository that is pushed to
infrequently this materially weakens the value of the checks.

Options, none implemented yet: an `if: failure()` step that sends mail itself; a
repository webhook; or a newer Forgejo version if one adds native support.
