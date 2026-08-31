# Continuous integration for IFC repositories

**Status: implemented and live on `bruno/brown-street` since 2026-08-31.** This
documents the Forgejo Actions setup for validating IFC models on push and pull request.

Three things below were wrong as designed and are corrected in place; each cost a failed
run, and each is marked **[CORRECTED]** where it appears. The infrastructure half lives
in `~/src/site_migration/forgejo-actions-plan.md`.

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
      git nodejs ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir ifcopenshell ifctester pytest && \
    pip install --no-cache-dir --no-deps \
      https://github.com/brunopostle/idssplit/releases/download/0.1.0/idssplit-0.1.0-py3-none-any.whl
```

The built version pins every version as a build `ARG` and sha256-verifies the idssplit
wheel; see `~/src/site_migration/forgejo-runner/ifc-ci.Containerfile`, which is the
authoritative copy.

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
- **`pytest` is a RUNTIME dependency [CORRECTED]**, not a test tool. This listing
  originally omitted it. `ifcopenshell.validate --rules` reaches
  `express/rule_executor.py`, which does `from _pytest import assertion` to rewrite the
  schema rules' asserts; without it every run dies with
  `Unhandled exception: No module named '_pytest'` and exit 255. `pip` does not pull it
  in — the GitHub workflow installed it explicitly and that line was lost in the port.

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

`runs-on:` and `shell:` differ from the GitHub originals; everything else below
`steps:` is unchanged, including the `::group::` workflow commands, which Forgejo
supports.

**`shell: bash` is required on every `run:` step [CORRECTED].** The runner hands a
`run:` block to `/bin/sh` — dash on this image — where GitHub uses bash. Both jobs
failed on their first run at line 3, `shopt -s globstar`, with
`/var/run/act/workflow/1.sh: 3: shopt: not found` and exit 127, before either script
reached a file. The `[[ ]]` tests and the arrays in `ids-lint` need bash too. A workflow
passing on GitHub tells you nothing about this.

### `ifc-lint.yml`

```yaml
name: IFC Validation
on: [push, pull_request]

jobs:
  lint-ifc:
    runs-on: ifc                         # [CORRECTED] the runner's LABEL is `ifc`;
                                         # `ifc-ci` is the image name, not the label
    container:
      image: <registry>/<owner>/ifc-ci:latest
    steps:
      - uses: actions/checkout@v4
      - name: Run IFC lint checks
        shell: bash                      # [CORRECTED] see above -- without it, exit 127
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

Keep the existing script body, adding `shell: bash` to the step. It splits each IDS file
into one file per rule with `idssplit`, then runs every rule against every model, so a
failure names the specific rule rather than just the specification.

The live copies of both workflows are `~/src/brown-street/.forgejo/workflows/`, with
reference copies in `~/src/site_migration/forgejo-runner/brown-street-*.yml`.

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

**Test this claim directly after any change to that step.** Because the exit status is
meaningless, a broken grep leaves a check that passes unconditionally and looks healthy.
The test used on 2026-08-31: take a passing IDS rule and widen its applicability until
it matches real entities that cannot satisfy it — dropping the NL-Sfb classification
facet from `IDS_random_example.ids` made the rule apply to all 32 windows in
`brown-street`'s model, giving `[FAIL] (0/32)` and exit 1. A vacuous `(0/0)` pass, which
is what most of these rules currently produce, proves nothing.

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

As built, the runner is registered at **user level** rather than against one repo, so a
new IFC repository needs only a workflow file — no token and no server-side command.
That is safe here precisely because `valid_volumes` is empty: registration scope decides
which repos may send jobs, the label decides which jobs are accepted, and what a job
gets either way is a container with no host mounts.

With `force_pull: false`, jobs read the image from the **runner user's own** rootless
store. Rebuilding as root changes nothing until the image is `podman save`d and
`podman load`ed into that store — a rebuild that was not reloaded caused the
`No module named '_pytest'` failure above to persist after it had been fixed.

## Known gap: no notification on failure

Forgejo 10.0.3 ships no mail template for Actions failures — the embedded set covers
auth, issues, collaborator/repo-transfer/new-user notifications and releases, but
nothing for workflow runs. The pass/fail status appears in the web UI as normal, but
unlike GitHub **no email is sent when a run fails**. On a repository that is pushed to
infrequently this materially weakens the value of the checks.

Options, none implemented yet: an `if: failure()` step that sends mail itself; a
repository webhook; or a newer Forgejo version if one adds native support.
