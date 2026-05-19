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

from __future__ import annotations

import argparse
import os
import sys
import tempfile

import ifcopenshell

from ifcurl import render as render_mod
from ifcurl.git import fetch_ifc_bytes
from ifcurl.url import IfcUrl

_URL_FORMAT = """
URL format:
  ifc://[user@]host/org/repo@<ref>?<parameters>

Transport is inferred from the URL structure:
  ifc://host/org/repo          HTTPS
  ifc://git@host/org/repo      SSH
  ifc:///path/to/repo          local file

Ref forms:
  @heads/<branch>              branch tip  (e.g. @heads/main)
  @tags/<name>                 tag         (e.g. @tags/v1.0)
  @<hash>                      commit hash (e.g. @abc123def)
  @HEAD                        default branch

Parameters (all optional):
  path=<file>                  path to the IFC file within the repository
  selector=<expr>              IfcOpenShell selector (see docs.ifcopenshell.org)
  camera=px,py,pz,dx,dy,dz,ux,uy,uz   camera in IFC world coordinates
  fov=<degrees>                perspective field of view (use with camera)
  scale=<value>                orthographic scale (use with camera)
  clip=px,py,pz,nx,ny,nz      clipping plane, normal toward visible side (repeatable)
  visibility=highlight|ghost|isolate   default: highlight
"""

_RENDER_EXAMPLES = """
examples:
  ifcurl render "ifc://github.com/org/repo@heads/main?path=model.ifc"
  ifcurl render "ifc://github.com/org/repo@heads/main?path=model.ifc&selector=IfcWall&visibility=ghost" -o walls.png
  ifcurl render "ifc://git@example.com/org/repo@abc123?path=model.ifc&camera=10,20,5,0,-1,0,0,0,1&fov=60"
  ifcurl render "ifc:///home/alice/project@HEAD?path=model.ifc&camera=0,0,8,0,0,-1,0,1,0&scale=50&clip=0,0,3,0,0,-1"
"""


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ifcurl",
        description=(
            "Fetch and render IFC building models addressed by ifc:// URLs.\n"
            "An ifc:// URL encodes a git source, an optional element selector,\n"
            "and an optional camera viewpoint."
        ),
        epilog=_URL_FORMAT,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    render_parser = subparsers.add_parser(
        "render",
        help="Render an ifc:// URL to a PNG image",
        description=(
            "Fetch an IFC model from a git repository via an ifc:// URL and render it\n"
            "to a PNG image. The selector, camera, clipping planes, and visibility mode\n"
            "are all taken from the URL parameters."
        ),
        epilog=_URL_FORMAT + _RENDER_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    render_parser.add_argument("url", help="The ifc:// URL to render")
    render_parser.add_argument(
        "-o",
        "--output",
        default="",
        metavar="FILE",
        help="Output PNG path (default: ifc-url-render.png)",
    )

    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the ifcurl preview HTTP service",
        description=(
            "Start an HTTP service that renders ifc:// URLs to PNG images on demand.\n"
            'Accepts POST /preview with a JSON body {"url": "ifc://..."} and returns image/png.\n'
            "Results are cached: mutable refs (branches, HEAD) are cached in memory;\n"
            "immutable refs (commit hashes, tags) are also cached to disk."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    serve_parser.add_argument(
        "--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)"
    )
    serve_parser.add_argument(
        "--port", type=int, default=8000, help="Bind port (default: 8000)"
    )
    serve_parser.add_argument(
        "--allowed-hosts",
        default="",
        metavar="HOSTS",
        help=(
            "Comma-separated list of git hostnames (with optional :port) the service "
            "is permitted to fetch from, e.g. 'github.com,gitlab.example.com:3000'. "
            "When omitted, all non-private remote hosts are allowed."
        ),
    )

    render_service_parser = subparsers.add_parser(
        "render-service",
        help="Start the ifcurl render isolation service on a Unix socket",
        description=(
            "Start the render isolation service that handles all IFC parsing and\n"
            "rendering.  This service runs with restricted privileges (no network,\n"
            "no execve) and communicates with the main ifcurl-api service over a\n"
            "Unix-domain socket.  Configure the API service with\n"
            "IFCURL_RENDER_SOCKET pointing to the same socket path."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    render_service_parser.add_argument(
        "--socket",
        default="/run/ifcurl/render.sock",
        metavar="PATH",
        help="Unix socket path to listen on (default: /run/ifcurl/render.sock)",
    )

    cache_parser = subparsers.add_parser(
        "cache",
        help="Inspect and manage the local git repository cache",
        description=(
            "Manage bare git repositories cached under the OS cache directory\n"
            "(~/.cache/ifcurl/ on Linux).  Use 'list' to see what is cached,\n"
            "'prune' to enforce a size limit, or 'clear' to remove everything."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    cache_sub = cache_parser.add_subparsers(dest="cache_cmd")

    cache_sub.add_parser(
        "list", help="Show cached repos with size and last-access time"
    )

    prune_parser = cache_sub.add_parser(
        "prune",
        help="Remove oldest repos until cache is under the size limit",
    )
    prune_parser.add_argument(
        "--max-gb",
        type=float,
        default=None,
        metavar="GB",
        help=(
            "Maximum total cache size in GB.  Defaults to IFCURL_CACHE_MAX_GB "
            "environment variable; required if that is not set."
        ),
    )

    cache_sub.add_parser("clear", help="Remove all cached repos")

    # Print help when called with no arguments
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    if args.command == "render":
        _cmd_render(args)
    elif args.command == "serve":
        _cmd_serve(args)
    elif args.command == "render-service":
        _cmd_render_service(args)
    elif args.command == "cache":
        _cmd_cache(args)


def _cmd_render(args: argparse.Namespace) -> None:
    try:
        ifc_url = IfcUrl.parse(args.url)
    except ValueError as exc:
        print(f"Error: invalid URL — {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        ifc_bytes = fetch_ifc_bytes(ifc_url)
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

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


def _cmd_serve(args: argparse.Namespace) -> None:
    try:
        import uvicorn

        from ifcurl import service
        from ifcurl.service import app
    except ImportError:
        print(
            "Error: service dependencies are not installed.\n"
            "Install with: pip install 'ifcurl[service]'",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.allowed_hosts:
        allowed = {h.strip() for h in args.allowed_hosts.split(",") if h.strip()}
        service.configure_allowed_hosts(allowed)

    uvicorn.run(app, host=args.host, port=args.port)


def _cmd_render_service(args: argparse.Namespace) -> None:
    try:
        import uvicorn

        from ifcurl.render_service import app
    except ImportError:
        print(
            "Error: service dependencies are not installed.\n"
            "Install with: pip install 'ifcurl[service]'",
            file=sys.stderr,
        )
        sys.exit(1)

    uvicorn.run(app, uds=args.socket)


def _cmd_cache(args: argparse.Namespace) -> None:
    import datetime
    import shutil as _shutil

    from ifcurl.git import _evict_if_needed, _get_max_cache_bytes, _repo_cache_entries

    if args.cache_cmd is None or args.cache_cmd == "list":
        entries = _repo_cache_entries()
        if not entries:
            print("No cached repositories.")
            return
        total = 0
        for atime, size, cache_dir, url in entries:
            dt = datetime.datetime.fromtimestamp(atime).strftime("%Y-%m-%d %H:%M")
            mb = size / (1024**2)
            total += size
            print(f"  {url:<55}  {mb:>7.1f} MB  {dt}")
        total_mb = total / (1024**2)
        max_bytes = _get_max_cache_bytes()
        limit_str = f"  limit: {max_bytes / (1024**3):.1f} GB" if max_bytes else ""
        print(f"Total: {total_mb:.1f} MB  ({len(entries)} repos){limit_str}")

    elif args.cache_cmd == "prune":
        max_gb = args.max_gb
        if max_gb is None:
            if os.environ.get("IFCURL_CACHE_MAX_GB"):
                max_gb = float(os.environ["IFCURL_CACHE_MAX_GB"])
            else:
                print(
                    "Error: --max-gb is required when IFCURL_CACHE_MAX_GB is not set",
                    file=sys.stderr,
                )
                sys.exit(1)
        os.environ["IFCURL_CACHE_MAX_GB"] = str(max_gb)
        before = sum(s for _, s, _, _ in _repo_cache_entries())
        _evict_if_needed()
        after = sum(s for _, s, _, _ in _repo_cache_entries())
        freed = (before - after) / (1024**2)
        remaining = after / (1024**2)
        print(f"Freed {freed:.1f} MB — {remaining:.1f} MB remaining.")

    elif args.cache_cmd == "clear":
        entries = _repo_cache_entries()
        if not entries:
            print("Nothing to clear.")
            return
        total = sum(s for _, s, _, _ in entries)
        for _, _, cache_dir, _ in entries:
            try:
                _shutil.rmtree(str(cache_dir))
            except OSError as exc:
                print(f"Warning: could not remove {cache_dir}: {exc}", file=sys.stderr)
        print(f"Cleared {total / (1024**2):.1f} MB ({len(entries)} repos).")


if __name__ == "__main__":
    main()
