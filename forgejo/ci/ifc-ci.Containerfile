# ifc-ci -- IFC validation tooling for Forgejo Actions.
#
# Build:
#   buildah bud -t forge.example.org/OWNER/ifc-ci:latest -f ifc-ci.Containerfile .
#
# The workflows are deliberately thin because everything slow is baked in here.
# On GitHub these checks spent most of every run in setup-python and
# `pip install ifcopenshell` (a large wheel); pre-installing removes that.
#
# No OpenGL/OSMesa stack: `ifcopenshell.validate --rules` and `ifctester` are
# parsing-level checks and never build geometry, so libgl1/libosmesa6/
# libglib2.0-0 are not needed. (The ifcurl *render* service does need them;
# this image does not.) That keeps the image much smaller.
FROM docker.io/library/python:3.12-slim

# Pinned so a rebuild reproduces the image. Bump deliberately.
ARG IFCOPENSHELL_VERSION=0.8.5
ARG IFCTESTER_VERSION=0.8.5
ARG PYTEST_VERSION=9.1.1
ARG IDSSPLIT_VERSION=0.1.0
ARG IDSSPLIT_SHA256=98ac2325115ced6acdb50da59bcb756c46b35c9ce61d3b6de968b1e2ae2b0fd6

# nodejs is required even though nothing here is a Node project: actions/checkout
# is a JavaScript action and the runner executes it *inside* this container.
# Without it you must hand-roll `git clone` and lose correct pull-request ref
# handling. git is needed for the same reason.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git nodejs ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# pytest is a RUNTIME dependency of `ifcopenshell.validate --rules`, not a test
# tool: express/rule_executor.py does `from _pytest import assertion` to rewrite
# the schema rules' asserts. Without it every run dies with
#   Unhandled exception: No module named '_pytest'
# and exit 255. pip will not pull it in for you.
RUN pip install --no-cache-dir \
      "ifcopenshell==${IFCOPENSHELL_VERSION}" \
      "ifctester==${IFCTESTER_VERSION}" \
      "pytest==${PYTEST_VERSION}"

# idssplit is not on PyPI -- it installs from a GitHub release wheel with
# --no-deps. Baking it in also removes a per-run fetch from an external host.
# The wheel is sha256-verified so the pin is enforced rather than trusted;
# update IDSSPLIT_SHA256 when you bump IDSSPLIT_VERSION.
RUN set -eux; \
    cd /tmp; \
    wheel="idssplit-${IDSSPLIT_VERSION}-py3-none-any.whl"; \
    curl -sSLO "https://github.com/brunopostle/idssplit/releases/download/${IDSSPLIT_VERSION}/${wheel}"; \
    echo "${IDSSPLIT_SHA256}  ${wheel}" | sha256sum -c -; \
    pip install --no-cache-dir --no-deps "./${wheel}"; \
    rm -f "/tmp/${wheel}"

CMD ["/bin/bash"]
