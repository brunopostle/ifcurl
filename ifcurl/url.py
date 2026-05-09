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

from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlparse


@dataclass
class IfcUrl:
    """Parsed representation of an ifc:// URL.

    URL structure:  ifc://[user@]host/org/repo@<ref>?<parameters>

    Transport is inferred:
      user@host  →  SSH
      bare host  →  HTTPS
      empty authority (ifc:///path)  →  local file
    """

    transport: str  # 'ssh', 'https', or 'local'
    host: str  # hostname, or '' for local
    user: str | None  # SSH username, or None
    repo_path: str  # e.g. 'org/repo' (remote) or '/home/alice/project' (local)
    ref: str  # e.g. 'heads/main', 'tags/v1.2', 'abc123def', 'HEAD'
    path: str | None  # IFC file path within the repository
    selector: str | None  # IfcOpenShell selector expression (percent-decoded)
    query: str | None  # dot-notation attribute/property path, e.g. 'Pset_WallCommon.FireRating'
    camera: tuple[float, float, float, float, float, float, float, float, float] | None
    fov: float | None  # perspective field of view in degrees
    scale: float | None  # orthographic view-to-world scale (BCF ViewToWorldScale)
    clips: list[tuple[float, float, float, float, float, float]] = field(
        default_factory=list
    )
    visibility: str = "highlight"  # 'highlight', 'ghost', 'isolate', or 'clash'

    @classmethod
    def parse(cls, url_string: str) -> IfcUrl:
        """Parse an ifc:// URL string.

        Raises ValueError for malformed URLs.
        """
        parsed = urlparse(url_string)
        if parsed.scheme != "ifc":
            raise ValueError(f"Expected ifc:// scheme, got {parsed.scheme!r}")

        # --- Transport and authority ---
        # parsed.hostname strips the port; we rebuild host as hostname:port
        # so that git_remote_url() generates correct clone URLs for
        # non-standard ports (e.g. a local Gitea on :3000 or SSH on :2222).
        def _host(p) -> str:
            return f"{p.hostname}:{p.port}" if p.port else (p.hostname or "")

        if parsed.username:
            transport = "ssh"
            user: str | None = parsed.username
            host = _host(parsed)
        elif parsed.netloc:
            transport = "https"
            user = None
            host = _host(parsed)
        else:
            transport = "local"
            user = None
            host = ""

        # --- Split path into repo_path and ref at the first '@' ---
        # For remote:  /org/repo@heads/main  →  repo_path='org/repo', ref='heads/main'
        # For local:   /home/alice/project@HEAD  →  repo_path='/home/alice/project', ref='HEAD'
        raw_path = parsed.path
        if "@" not in raw_path:
            raise ValueError(f"URL missing ref separator '@' in path: {url_string!r}")
        repo_part, ref = raw_path.split("@", 1)
        if not ref:
            raise ValueError(f"URL has empty ref after '@': {url_string!r}")
        repo_path = repo_part if transport == "local" else repo_part.lstrip("/")

        # --- Query parameters ---
        qs = parse_qs(parsed.query, keep_blank_values=False)

        path = qs["path"][0] if "path" in qs else None
        selector = unquote(qs["selector"][0]) if "selector" in qs else None
        query = qs["query"][0] if "query" in qs else None

        camera: tuple[float, ...] | None = None
        if "camera" in qs:
            try:
                vals = [float(v) for v in qs["camera"][0].split(",")]
            except ValueError as exc:
                raise ValueError(
                    f"'camera' contains non-numeric value: {qs['camera'][0]!r}"
                ) from exc
            if len(vals) != 9:
                raise ValueError(
                    f"'camera' requires exactly 9 values (px,py,pz,dx,dy,dz,ux,uy,uz), got {len(vals)}"
                )
            camera = tuple(vals)  # type: ignore[assignment]

        fov: float | None = None
        if "fov" in qs:
            try:
                fov = float(qs["fov"][0])
            except ValueError as exc:
                raise ValueError(
                    f"'fov' must be a number, got {qs['fov'][0]!r}"
                ) from exc

        scale: float | None = None
        if "scale" in qs:
            try:
                scale = float(qs["scale"][0])
            except ValueError as exc:
                raise ValueError(
                    f"'scale' must be a number, got {qs['scale'][0]!r}"
                ) from exc

        if camera is not None and fov is None and scale is None:
            raise ValueError(
                "'camera' requires either 'fov' (perspective) or 'scale' (orthographic)"
            )
        if fov is not None and scale is not None:
            raise ValueError("'fov' and 'scale' are mutually exclusive")

        clips: list[tuple[float, ...]] = []
        for clip_str in qs.get("clip", []):
            try:
                vals = [float(v) for v in clip_str.split(",")]
            except ValueError as exc:
                raise ValueError(
                    f"'clip' contains non-numeric value: {clip_str!r}"
                ) from exc
            if len(vals) != 6:
                raise ValueError(
                    f"'clip' requires exactly 6 values (px,py,pz,nx,ny,nz), got {len(vals)}"
                )
            clips.append(tuple(vals))  # type: ignore[arg-type]

        visibility_raw = qs.get("visibility", ["highlight"])[0]
        if visibility_raw not in ("highlight", "ghost", "isolate", "clash"):
            raise ValueError(
                f"Unknown visibility mode {visibility_raw!r}; expected 'highlight', 'ghost', 'isolate', or 'clash'"
            )

        return cls(
            transport=transport,
            host=host,
            user=user,
            repo_path=repo_path,
            ref=ref,
            path=path,
            selector=selector,
            query=query,
            camera=camera,  # type: ignore[arg-type]
            fov=fov,
            scale=scale,
            clips=clips,  # type: ignore[arg-type]
            visibility=visibility_raw,
        )

    def git_remote_url(self) -> str:
        """Return the git remote URL for this IFC URL.

        The port is preserved when non-standard.  SSH uses ssh://, HTTPS
        uses https://.  Whether the server actually speaks HTTPS or plain
        HTTP is resolved at clone time — see ifcurl.git._open_remote which
        falls back from https to http when the server refuses the TLS
        handshake.
        """
        if self.transport == "local":
            return self.repo_path
        if self.transport == "ssh":
            return f"ssh://{self.user}@{self.host}/{self.repo_path}"
        return f"https://{self.host}/{self.repo_path}"

    def git_ref(self) -> str:
        """Convert the ifc:// ref to a git ref string.

        heads/main  →  main
        tags/v1.2   →  refs/tags/v1.2
        abc123      →  abc123
        HEAD        →  HEAD
        """
        if self.ref.startswith("heads/"):
            return self.ref[len("heads/") :]
        if self.ref.startswith("tags/"):
            return f"refs/{self.ref}"
        return self.ref  # commit hash or HEAD

    def is_mutable_ref(self) -> bool:
        """Return True if the ref may point to a different commit over time.

        Mutable refs (HEAD, branches) must not be cached at the rendered-PNG tier.
        """
        return self.ref == "HEAD" or self.ref.startswith("heads/")

    def to_string(self) -> str:
        """Serialize to an ifc:// URL string."""
        from urllib.parse import quote

        if self.transport == "local":
            authority = ""
            path = self.repo_path
        elif self.transport == "ssh":
            authority = f"{self.user}@{self.host}"
            path = f"/{self.repo_path}"
        else:  # https
            authority = self.host
            path = f"/{self.repo_path}"

        base = f"ifc://{authority}{path}@{self.ref}"

        parts = []
        if self.path:
            parts.append(f"path={quote(self.path, safe='/')}")
        if self.selector:
            parts.append(f"selector={quote(self.selector, safe='$')}")
        if self.query:
            parts.append(f"query={self.query}")
        if self.camera:
            vals = ",".join(repr(v) for v in self.camera)
            parts.append(f"camera={vals}")
        if self.fov is not None:
            parts.append(f"fov={self.fov!r}")
        if self.scale is not None:
            parts.append(f"scale={self.scale!r}")
        for clip in self.clips:
            vals = ",".join(repr(v) for v in clip)
            parts.append(f"clip={vals}")
        if self.visibility != "highlight":
            parts.append(f"visibility={self.visibility}")

        if parts:
            return base + "?" + "&".join(parts)
        return base
