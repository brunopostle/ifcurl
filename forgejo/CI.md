# Continuous integration for IFC repositories

**Status: implemented and running in production since 2026-08-31.** This documents the
Forgejo Actions setup for validating IFC models on push and pull request, end to end:
enabling Actions, building the image, running a registered runner as an unprivileged
user, and the two workflows.

Everything you need to copy is in [`ci/`](ci/):

| File | Goes to |
|---|---|
| [`ci/ifc-ci.Containerfile`](ci/ifc-ci.Containerfile) | wherever you build images |
| [`ci/runner-config.yaml`](ci/runner-config.yaml) | `/var/lib/forgejo-runner/ifc/config.yaml`, **root-owned** |
| [`ci/forgejo-runner-ifc.service`](ci/forgejo-runner-ifc.service) | `/etc/systemd/system/` |
| [`ci/ifc-lint.yml`](ci/ifc-lint.yml), [`ci/ids-lint.yml`](ci/ids-lint.yml) | `.forgejo/workflows/` in each model repo |

**Versions this was built and tested against.** Actions is a fast-moving part of
Forgejo, and several details below are version-specific:

| Component | Version |
|---|---|
| Forgejo | 10.0.3 |
| `forgejo-runner` | v6.4.0 |
| Podman / Buildah | 4.9.3 / 1.33.7 (rootless) |
| Host | Ubuntu 24.04 |

Pair the runner with the Forgejo series it was released alongside. v6.4.0 goes with
Forgejo 10; newer runners are *generally* backward compatible, but a runner many major
versions ahead of the server is an untested Actions protocol handshake.

Three things in the original design were wrong, each costing a failed run. They are
corrected in place and marked **[CORRECTED]**; they are the errors most likely to catch
you too.

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

## Server setup

All of this is on the Forgejo host, as root unless stated. These workflows need no write
access to anything outside their container, so one runner serves every IFC repository.

Build the image (next section) whenever suits — the runner registers and starts fine
without it, and only needs it when a job actually runs.

### 1. Enable Actions

Make sure `app.ini` carries an `[actions]` section, and restart Forgejo:

```ini
[actions]
ENABLED = true
DEFAULT_ACTIONS_URL = https://code.forgejo.org
```

`DEFAULT_ACTIONS_URL` is where `uses: actions/checkout@v4` is fetched from — Forgejo
resolves actions from a forge, not from a local cache. Both keys were set explicitly
here rather than left to defaults, since defaults for both have changed across Forgejo
and Gitea versions; if you inherit an instance with no `[actions]` section, do not
assume which way it is configured.

### 2. Create the runner user

A dedicated unprivileged user with no shell — not root, and not the user Forgejo runs
as:

```bash
useradd --create-home --shell /usr/sbin/nologin ci-ifc
loginctl enable-linger ci-ifc
```

`enable-linger` is load-bearing: without it the user's systemd session — and with it the
rootless Podman socket — is torn down the moment nothing is logged in as that user, and
the runner starts failing whenever the box has been quiet.

`useradd` allocates the `/etc/subuid` and `/etc/subgid` ranges rootless Podman needs.
Confirm they exist (`grep ci-ifc /etc/subuid`) before going further; a user created some
other way may have none, and rootless Podman will not work without them.

### 3. Install Podman and start the rootless socket

```bash
apt install podman buildah uidmap slirp4netns fuse-overlayfs
sudo -u ci-ifc XDG_RUNTIME_DIR=/run/user/$(id -u ci-ifc) \
  systemctl --user enable --now podman.socket
```

The runner speaks the Docker API; Podman's socket is compatible, so no Docker daemon is
needed anywhere. Smoke-test it as that user before continuing:

```bash
cd /tmp    # see the gotcha below -- do not do this from /root
sudo -u ci-ifc XDG_RUNTIME_DIR=/run/user/$(id -u ci-ifc) HOME=/home/ci-ifc \
  podman run --rm alpine echo ok
```

> **Gotcha.** `sudo -u <user> podman ...` from a root shell whose cwd is `/root` fails
> with `cannot chdir to /root: Permission denied` — an obscure message for a trivial
> cause. `cd` somewhere the user can read first.

### 4. Install the runner binary

Download `forgejo-runner` matching your Forgejo series (see the version table above) to
`/usr/local/bin/forgejo-runner`, `root:root 0755`, and **verify it against the upstream
`.sha256`**. Nothing upgrades it automatically; it moves only when you replace it.

### 5. Configuration and systemd unit

Copy [`ci/runner-config.yaml`](ci/runner-config.yaml) to
`/var/lib/forgejo-runner/ifc/config.yaml` and [`ci/forgejo-runner-ifc.service`](ci/forgejo-runner-ifc.service)
to `/etc/systemd/system/`. Edit the uid in both to match `id -u ci-ifc`, and the image
reference to your own registry and owner.

```bash
mkdir -p /var/lib/forgejo-runner/ifc
chown ci-ifc:ci-ifc /var/lib/forgejo-runner/ifc
install -o root -g root -m 0644 runner-config.yaml /var/lib/forgejo-runner/ifc/config.yaml
```

**Leave `config.yaml` owned by root, in a directory owned by the runner user.** That
split is the enforcement model: the runner user must write `.runner` and its own state
into the directory, but must not be able to rewrite the config that constrains it. If
the runner user owns the config, a job that gets code execution as that user can widen
`valid_volumes` to mount anything and the isolation is gone.

### 6. Register the runner

Mint a registration token in the Forgejo web UI, under **Settings → Actions → Runners →
"Create new runner"**. *Which* settings page you take it from decides the runner's scope:

- **A repository's** settings → that runner only ever serves that one repository.
- **Your user** settings → any repository you own may send it jobs. Registration logs
  `Runner in user-mode` and the resulting `.runner` names no repository.
- **Site administration** → instance-wide, every repository on the server.

```bash
cd /var/lib/forgejo-runner/ifc
sudo -u ci-ifc HOME=/home/ci-ifc XDG_RUNTIME_DIR=/run/user/$(id -u ci-ifc) \
  /usr/local/bin/forgejo-runner register \
    --no-interactive --instance https://forge.example.org \
    --token <TOKEN> --name ifc \
    --labels 'ifc:docker://forge.example.org/OWNER/ifc-ci:latest'

chmod 0600 /var/lib/forgejo-runner/ifc/.runner
systemctl enable --now forgejo-runner-ifc
```

Points that bite:

- **The label must match `config.yaml`.** The runner advertises what it registered with;
  a workflow's `runs-on:` is matched against that.
- **`register` writes `.runner` mode 0664.** It holds a long-lived credential — `chmod
  0600` it. The daemon will not start without this file, so register before enabling.
- **Re-registering does not replace a runner**, it adds a second one. Delete the stale
  entry in the web UI.
- **Scope is fixed at registration.** Changing it means registering again, so decide
  first.

### 7. Verify

Push anything to a repository with a workflow in it and watch:

```bash
journalctl -u forgejo-runner-ifc -f
```

A healthy start logs `runner: ifc, ..., declared successfully` and `[poller 0] launched`;
picking up work logs `task N repo is OWNER/REPO`. Per-job output is in the Forgejo web UI
under the repository's **Actions** tab — that is where a failing step's own log lives, and
the runner journal will not show it.

If a run never appears at all, the job was never scheduled and the runner is not the
place to look: check that Actions is enabled, and that the repository has the workflow on
the branch you pushed.

### Troubleshooting

Every one of these cost a real failed run here.

| Symptom | Cause |
|---|---|
| `shopt: not found`, exit 127 | `run:` executes under `sh`. Add `shell: bash` to the step. |
| `No module named '_pytest'`, exit 255 | `pytest` missing from the image — or the image was rebuilt but not reloaded into the runner user's store. Compare image IDs. |
| Job stays queued forever, no runner takes it | `runs-on:` does not match any registered runner's **label**. The label is not the image name. |
| Runner daemon will not start | No `.runner` file — it has not been registered, or registration failed. |
| Runs appear but are cancelled immediately | Same label mismatch, most often a workflow still asking for `ubuntu-latest`. |
| `cannot chdir to /root: Permission denied` | A `sudo -u <runner-user>` command run from root's home. `cd /tmp` first. |
| Action cannot be resolved at job start | Check `DEFAULT_ACTIONS_URL` in `app.ini`, and that the host can reach it. |
| Podman socket missing after the box is idle | `loginctl enable-linger <runner-user>` was not run. |
| Resource limits in `config.yaml` have no effect | cgroup controllers not delegated to the runner user — check `cgroup.controllers`. |
| A workflow's `container.options` appears to do nothing | It does nothing; v6.4.0 drops the field. Set options in the runner config instead. |
| `413` pushing the image to a Forgejo registry | `client_max_body_size` too small on the Forgejo vhost. |

### Choosing the scope

The user-level scope is the one used here, so that a new IFC repository needs only a
workflow file — no token, no server-side command. That is safe *because* this runner's
`valid_volumes` is empty. Registration scope decides which repositories may send jobs;
the label decides which jobs are accepted; and what a job gets either way is a container
with no host mounts.

A runner that writes to the host — a deployment runner, say — is a different case, and
should be scoped to the single repository allowed to drive it.

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

The shipped version, [`ci/ifc-ci.Containerfile`](ci/ifc-ci.Containerfile), pins every
version as a build `ARG` and sha256-verifies the idssplit wheel. Use that rather than
the sketch above.

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

### Rebuilding the image

With `force_pull: false`, jobs read the image from the **runner user's own** rootless
store, which is not the store you get as root. `buildah bud` as root therefore changes
nothing a job will see. After every rebuild:

```bash
podman save -o /tmp/ifc-ci.tar forge.example.org/OWNER/ifc-ci:latest
chmod 0644 /tmp/ifc-ci.tar
sudo -u ci-ifc XDG_RUNTIME_DIR=/run/user/$(id -u ci-ifc) HOME=/home/ci-ifc \
  podman load -i /tmp/ifc-ci.tar
rm -f /tmp/ifc-ci.tar
```

Confirm by comparing image IDs — `podman images` as root and as the runner user must
agree.

**This one misleads.** A rebuild that was not reloaded left the `No module named
'_pytest'` failure in place after it had been fixed, which reads exactly like the fix
being wrong. Check the image ID before re-debugging anything.

Setting `force_pull: true` and pushing to a registry removes this step, at the cost of
needing registry credentials on the runner.

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

Both complete workflows are in [`ci/`](ci/) — copy them into `.forgejo/workflows/` and
change the image reference to your own registry and owner.

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
The test used here: take a rule that passes and widen its applicability until it matches
real entities that cannot satisfy it. Dropping the NL-Sfb classification facet from
buildingSMART's `IDS_random_example.ids` made its rule apply to all 32 windows in the
model instead of none, giving `[FAIL] (0/32)` and exit 1.

**Watch for vacuous passes.** `ifctester` prints the applicable/passing counts: a rule
reporting `[PASS] (0/0)` matched nothing at all and tells you nothing about the model.
Whole IDS files can pass this way. Read the counts, not just the colour.

## Security: what this contains, and what it does not

Worth reading before pointing this at a repository that takes pull requests from people
you do not know — which, given the design, is a likely use.

**What holds — tested, not assumed.** Jobs run in a container, as an unprivileged user,
with `privileged: false` and an empty `valid_volumes`. The config enforcing that is
root-owned, so a workflow cannot rewrite it. Specifically verified on the versions in the
table above:

- A workflow asking for a host path through `volumes:` gets no mount.
- A workflow asking through `options: -v /etc:/host-etc -v /tmp:/host-tmp` also gets no
  mount, cannot read `/etc`, and cannot write to the host.
- `options: --privileged` does not re-enable privilege: `mknod` is refused and `CapEff`
  stays at the unprivileged `00000000800405fb`.

The reason all three fail is worth knowing, because it is blunter than it looks:
**forgejo-runner v6.4.0 ignores workflow-level `container.options` entirely.** A job
asking for `--memory=512m` still ran under the runner config's 2 GiB. It is not that `-v`
is filtered — the whole field is dropped. **Re-test after a runner upgrade**, since a
version that begins honouring workflow options reopens all of this at once.

> **How to test this yourself, and how not to.** Never trust the job's own report: it
> runs inside the thing you are testing, and `ls /host-etc | head -3` exits 0 even when
> `ls` fails, because the status is `head`'s. Have the probe write evidence to a
> bind-mounted host path and look for it from the host, or encode its finding in the
> **job status**, which is recorded server-side. Then run a **positive control** — the
> same `podman run -v ...` directly as the runner user — to prove your evidence path
> would have shown a breach if one had happened. Without that control, a negative result
> may only mean your probe was broken.

**A trap in the same area:** `valid_volumes` fails *silently*. A job asking for an
unlisted path is not refused — it starts, succeeds, and simply has no such mount. A green
run is never evidence that a mount was granted.

**What does not hold:**

- **Resource limits are not on by default — set them.** This is the practical risk and it
  needs no malice: a runaway loop or an oversized model can exhaust the RAM or disk of the
  machine that also serves your Forgejo instance.
  [`ci/runner-config.yaml`](ci/runner-config.yaml) ships
  `--memory=2g --cpus=1.5 --pids-limit=512`, which is in production here and verified
  enforced inside a real job container. **Check cgroup delegation first**: rootless Podman
  can only enforce these if `cpu memory pids` appear in
  `/sys/fs/cgroup/user.slice/user-<uid>.slice/cgroup.controllers`. Without it `--cpus` is
  silently ignored and you have a limit you believe in but do not have.
- **Outbound network from job containers is unrestricted.** A job can reach anything the
  host can reach, your internal network included. This is the largest remaining gap, and
  it matters most on the pull-request path below.
- **`forgejo-runner exec` does not apply `valid_volumes` at all.** It is a local developer
  convenience, not a sandbox — never use it to test isolation, and note that anyone who
  can run it on the box is not constrained by the allowlist.
- **Pull requests from strangers run automatically.** Observed, not assumed: a fork PR
  from a user who was neither a collaborator on the repository nor an admin created
  `pull_request` runs against `refs/pull/N/head` with no approval step. Forgejo 10.0.3
  has no equivalent of GitHub's "require approval for first-time contributors" gate.
  That is the intended behaviour here — it is the point of running CI on PRs — but it
  means the isolation above has to be real rather than assumed, and it is why
  `valid_volumes` is empty rather than merely narrow.

## Known gap: no notification on failure

Forgejo 10.0.3 ships no mail template for Actions failures — the embedded set covers
auth, issues, collaborator/repo-transfer/new-user notifications and releases, but
nothing for workflow runs. The pass/fail status appears in the web UI as normal, but
unlike GitHub **no email is sent when a run fails**. On a repository that is pushed to
infrequently this materially weakens the value of the checks.

Options, none implemented yet: an `if: failure()` step that sends mail itself; a
repository webhook; or a newer Forgejo version if one adds native support.
