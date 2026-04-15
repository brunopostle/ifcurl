# IFC URL — resolve and render ifc:// URLs
# Copyright (C) 2026 Bruno Postle <bruno@postle.net>
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

from __future__ import annotations

import argparse
import os
import sys
import tempfile

import ifcopenshell

from ifcurl import render as render_mod
from ifcurl.git import fetch_ifc_bytes
from ifcurl.url import IfcUrl


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ifcurl",
        description="Resolve and render ifc:// URLs pointing to IFC models in git repositories",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    render_parser = subparsers.add_parser("render", help="Render an ifc:// URL to a PNG image")
    render_parser.add_argument("url", help="The ifc:// URL to render")
    render_parser.add_argument(
        "-o", "--output", default="", metavar="FILE",
        help="Output PNG path (default: ifc-url-render.png)",
    )

    args = parser.parse_args()

    if args.command == "render":
        _cmd_render(args)


def _cmd_render(args: argparse.Namespace) -> None:
    try:
        ifc_url = IfcUrl.parse(args.url)
    except ValueError as exc:
        print(f"Error: invalid URL — {exc}", file=sys.stderr)
        sys.exit(1)

    # Fetch IFC bytes from git
    try:
        ifc_bytes = fetch_ifc_bytes(ifc_url)
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Load model from bytes via a temp file
    tmp_fd, tmp_ifc = tempfile.mkstemp(suffix=".ifc")
    try:
        os.write(tmp_fd, ifc_bytes)
        os.close(tmp_fd)
        try:
            model = ifcopenshell.open(tmp_ifc)
        except Exception as exc:
            print(f"Error: could not open IFC file — {exc}", file=sys.stderr)
            sys.exit(1)
    finally:
        try:
            os.unlink(tmp_ifc)
        except OSError:
            pass

    # Render
    try:
        png_bytes = render_mod.render(
            model,
            selector=ifc_url.selector,
            camera=ifc_url.camera,
            fov=ifc_url.fov,
            scale=ifc_url.scale,
            clips=ifc_url.clips or None,
            visibility=ifc_url.visibility,
        )
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    out_path = args.output or "ifc-url-render.png"
    with open(out_path, "wb") as f:
        f.write(png_bytes)
    print(f"Saved render to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
