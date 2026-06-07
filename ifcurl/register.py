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

"""ifcurl-register: install ifcurl-open as the OS ifc:// protocol handler.

Platform support:

- **Linux**: writes a ``.desktop`` file to ``~/.local/share/applications/``
  and registers it with ``xdg-mime``.
- **macOS**: installs a minimal ``.app`` bundle in ``~/Applications/`` with
  ``CFBundleURLTypes`` for the ``ifc://`` scheme, then calls ``lsregister``.
  The bundle launcher requires ``pyobjc-framework-Cocoa`` to receive URL
  open events from the OS.
- **Windows**: writes per-user registry keys under
  ``HKCU\\SOFTWARE\\Classes\\ifc`` (no administrator privileges required).
"""

from __future__ import annotations

import argparse
import importlib.resources
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ifcurl-register",
        description="Register ifcurl-open as the OS-level ifc:// protocol handler.",
    )
    parser.add_argument(
        "--unregister",
        action="store_true",
        help="Remove the ifc:// protocol handler registration.",
    )
    args = parser.parse_args()

    ifcurl_open = _find_ifcurl_open()

    platform = sys.platform
    if platform.startswith("linux"):
        if args.unregister:
            _unregister_linux()
        else:
            _register_linux(ifcurl_open)
    elif platform == "darwin":
        if args.unregister:
            _unregister_macos()
        else:
            _register_macos(ifcurl_open)
    elif platform == "win32":
        if args.unregister:
            _unregister_windows()
        else:
            _register_windows(ifcurl_open)
    else:
        print(f"Error: unsupported platform '{platform}'", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Linux
# ---------------------------------------------------------------------------


def _register_linux(ifcurl_open: str) -> None:
    apps_dir = Path.home() / ".local" / "share" / "applications"
    apps_dir.mkdir(parents=True, exist_ok=True)

    template = _read_data("ifc-url-handler.desktop")
    content = template.replace("IFCURL_OPEN_EXEC", ifcurl_open)

    desktop_path = apps_dir / "ifc-url-handler.desktop"
    desktop_path.write_text(content)
    print(f"Wrote {desktop_path}")

    try:
        subprocess.run(
            ["xdg-mime", "default", "ifc-url-handler.desktop", "x-scheme-handler/ifc"],
            check=True,
        )
        print("Registered ifc:// with xdg-mime")
    except FileNotFoundError:
        print("Warning: xdg-mime not found; registration may be incomplete", file=sys.stderr)
    except subprocess.CalledProcessError as exc:
        print(f"Warning: xdg-mime failed: {exc}", file=sys.stderr)

    # Non-critical: update the desktop database so the entry is immediately visible
    subprocess.run(["update-desktop-database", str(apps_dir)], check=False)
    print("Done. ifc:// URLs will open with ifcurl-open.")


def _unregister_linux() -> None:
    desktop_path = Path.home() / ".local" / "share" / "applications" / "ifc-url-handler.desktop"
    if desktop_path.exists():
        desktop_path.unlink()
        print(f"Removed {desktop_path}")
        subprocess.run(["update-desktop-database", str(desktop_path.parent)], check=False)
    else:
        print("ifc-url-handler.desktop not found; nothing to remove.")


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------

_LSREGISTER = (
    "/System/Library/Frameworks/CoreServices.framework"
    "/Versions/A/Frameworks/LaunchServices.framework"
    "/Versions/A/Support/lsregister"
)

_MACOS_SHIM = '''\
#!/usr/bin/env python3
"""macOS URL handler shim for ifc:// URLs.

Receives URLs via AppleEvents when invoked by the OS.
Requires pyobjc-framework-Cocoa for AppleEvents support.
Falls back to a command-line argument if PyObjC is unavailable.
"""
import subprocess, sys
from pathlib import Path


def _dispatch(url_str):
    bin_dir = Path(sys.executable).parent
    for name in ("ifcurl-open",):
        exe = bin_dir / name
        if exe.exists():
            subprocess.Popen([str(exe), url_str])
            return
    subprocess.Popen([sys.executable, "-m", "ifcurl.open", url_str])


try:
    from AppKit import NSApplication, NSObject
    from PyObjCTools import AppHelper

    class _Delegate(NSObject):
        def applicationWillFinishLaunching_(self, _n):
            pass

        def application_openURLs_(self, _app, urls):
            for url in urls:
                _dispatch(url.absoluteString())
            AppHelper.stopEventLoop()

    _app = NSApplication.sharedApplication()
    _app.setDelegate_(_Delegate.alloc().init())
    AppHelper.runEventLoop()

except ImportError:
    if len(sys.argv) > 1:
        _dispatch(sys.argv[1])
    else:
        print(
            "Install pyobjc-framework-Cocoa for macOS ifc:// URL scheme support.",
            file=sys.stderr,
        )
        sys.exit(1)
'''


def _register_macos(ifcurl_open: str) -> None:
    app_bundle = Path.home() / "Applications" / "IfcUrlHandler.app"
    contents = app_bundle / "Contents"
    macos_dir = contents / "MacOS"
    macos_dir.mkdir(parents=True, exist_ok=True)

    plist_text = _read_data("IfcUrlHandler-Info.plist")
    (contents / "Info.plist").write_text(plist_text)
    print(f"Wrote {contents / 'Info.plist'}")

    shim_path = macos_dir / "ifcurl-open-shim"
    shim_path.write_text(_MACOS_SHIM)
    shim_path.chmod(0o755)
    print(f"Wrote {shim_path}")

    if Path(_LSREGISTER).exists():
        try:
            subprocess.run([_LSREGISTER, "-f", str(app_bundle)], check=True)
            print(f"Registered {app_bundle} with Launch Services")
        except subprocess.CalledProcessError as exc:
            print(f"Warning: lsregister failed: {exc}", file=sys.stderr)
    else:
        print("Warning: lsregister not found; you may need to log out and back in.", file=sys.stderr)

    print("Done. ifc:// URLs will open with IfcUrlHandler.app.")
    print("Note: install pyobjc-framework-Cocoa for full AppleEvents support.")


def _unregister_macos() -> None:
    app_bundle = Path.home() / "Applications" / "IfcUrlHandler.app"
    if app_bundle.exists():
        if Path(_LSREGISTER).exists():
            subprocess.run([_LSREGISTER, "-u", str(app_bundle)], check=False)
        shutil.rmtree(app_bundle)
        print(f"Removed {app_bundle}")
    else:
        print(f"{app_bundle} not found; nothing to remove.")


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


def _register_windows(ifcurl_open: str) -> None:
    try:
        import winreg
    except ImportError:
        print("Error: winreg is only available on Windows", file=sys.stderr)
        sys.exit(1)

    command = f'"{ifcurl_open}" "%1"'

    # HKCU\SOFTWARE\Classes\ifc  (per-user, no admin required)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Classes\ifc") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, "URL:IFC Protocol")
        winreg.SetValueEx(key, "URL Protocol", 0, winreg.REG_SZ, "")

    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, r"SOFTWARE\Classes\ifc\shell\open\command"
    ) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)

    print(f"Registered ifc:// → {command}")
    print("Done. ifc:// URLs will open with ifcurl-open.")


def _unregister_windows() -> None:
    try:
        import winreg
    except ImportError:
        print("Error: winreg is only available on Windows", file=sys.stderr)
        sys.exit(1)

    for subkey in (
        r"SOFTWARE\Classes\ifc\shell\open\command",
        r"SOFTWARE\Classes\ifc\shell\open",
        r"SOFTWARE\Classes\ifc\shell",
        r"SOFTWARE\Classes\ifc",
    ):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, subkey)
        except FileNotFoundError:
            pass
    print("Removed ifc:// registry keys.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_ifcurl_open() -> str:
    """Return the absolute path to the ifcurl-open executable."""
    # Prefer the executable in the same bin directory as the current interpreter
    bin_dir = Path(sys.executable).parent
    for name in ("ifcurl-open", "ifcurl-open.exe"):
        candidate = bin_dir / name
        if candidate.exists():
            return str(candidate)

    # Fall back to PATH search
    found = shutil.which("ifcurl-open")
    if found:
        return found

    # Last resort: invoke as a module
    return f"{sys.executable} -m ifcurl.open"


def _read_data(filename: str) -> str:
    """Read a file from the ifcurl/data/ package directory."""
    try:
        # Python 3.9+
        ref = importlib.resources.files("ifcurl.data").joinpath(filename)
        return ref.read_text(encoding="utf-8")
    except AttributeError:
        # Python 3.8 fallback
        with importlib.resources.open_text("ifcurl.data", filename) as f:
            return f.read()
