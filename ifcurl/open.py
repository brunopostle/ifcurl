# IFC URL — resolve and render ifc:// URLs
# Copyright (C) 2026 Bruno Postle <bruno@postle.net>
# SPDX-License-Identifier: LGPL-3.0-or-later
#
# This file is part of IFC URL.
#
# IFC URL is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# IFC URL is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with IFC URL.  If not, see <http://www.gnu.org/licenses/>.

"""ifcurl-open: OS protocol handler dispatcher for ifc:// URLs.

Receives a raw ifc:// URL from the OS (launched by a browser or file manager),
reads the configured viewer from ``~/.config/ifcurl/config.toml``, and
forwards the URL to that viewer as a command-line argument.

Config example (~/.config/ifcurl/config.toml)::

    [handler]
    default_viewer = "bonsai"   # or "ifcviewer"

    # Or provide a fully custom command template:
    # command = ["myviewer", "--open", "{url}"]

The viewer is expected to call ``ifcurl.resolve(url)`` internally to fetch
and render the model.  ifcurl-open does not pre-fetch the URL.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

from platformdirs import user_config_dir

_CONFIG_PATH = Path(user_config_dir("ifcurl")) / "config.toml"

# Known viewer presets: list of argv tokens, with {url} as a placeholder.
# Phase 4 will refine the bonsai invocation once the Bonsai API is settled.
_KNOWN_VIEWERS: dict[str, list[str]] = {
    "bonsai": ["blender", "--python-expr", "import bonsai; bonsai.open_ifc_url('{url}')"],
    "ifcviewer": ["ifcviewer", "{url}"],
}


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ifcurl-open",
        description="Open an ifc:// URL in the configured viewer.",
    )
    parser.add_argument("url", help="The ifc:// URL to open")
    args = parser.parse_args()

    cmd = _build_command(args.url)
    try:
        subprocess.Popen(cmd)
    except FileNotFoundError:
        print(f"Error: viewer executable not found — {cmd[0]}", file=sys.stderr)
        sys.exit(1)


def _build_command(url: str) -> list[str]:
    config = _read_config()
    handler = config.get("handler", {})

    if "command" in handler:
        template = handler["command"]
        if not isinstance(template, list):
            print(
                f"Error: [handler] command in {_CONFIG_PATH} must be a TOML array of strings",
                file=sys.stderr,
            )
            sys.exit(1)
        return [tok.replace("{url}", url) for tok in template]

    viewer = handler.get("default_viewer")
    if viewer is None:
        print(
            "Error: no viewer configured.\n"
            f"Add to {_CONFIG_PATH}:\n\n"
            "    [handler]\n"
            '    default_viewer = "bonsai"  # or "ifcviewer"',
            file=sys.stderr,
        )
        sys.exit(1)

    if viewer not in _KNOWN_VIEWERS:
        known = ", ".join(f'"{v}"' for v in _KNOWN_VIEWERS)
        print(
            f"Error: unknown viewer '{viewer}' in {_CONFIG_PATH}.\n"
            f"Known presets: {known}\n"
            "Or use [handler] command = [\"myviewer\", \"{url}\"] for a custom command.",
            file=sys.stderr,
        )
        sys.exit(1)

    return [tok.replace("{url}", url) for tok in _KNOWN_VIEWERS[viewer]]


def _read_config() -> dict:
    if not _CONFIG_PATH.exists():
        return {}
    with open(_CONFIG_PATH, "rb") as f:
        return tomllib.load(f)
