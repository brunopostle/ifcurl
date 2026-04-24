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

"""Build BCF 2.1 zip archives from ifc:// view state.

BCF (BIM Collaboration Format) 2.1 structure produced here::

    bcf.version
    <topic-guid>/
        markup.bcf      — title, comment, viewpoint reference
        viewpoint.bcfv  — camera, clipping planes, component selection
"""

from __future__ import annotations

import io
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


_VERSION_XML = b"""\
<?xml version="1.0" encoding="utf-8"?>
<Version VersionId="2.1" xsi:noNamespaceSchemaLocation="https://raw.githubusercontent.com/buildingSMART/BCF-XML/release_2_1/Schemas/version.xsd" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <DetailedVersion>2.1</DetailedVersion>
</Version>"""


def _xyz(parent: ET.Element, tag: str, x: float, y: float, z: float) -> None:
    el = ET.SubElement(parent, tag)
    ET.SubElement(el, "X").text = str(x)
    ET.SubElement(el, "Y").text = str(y)
    ET.SubElement(el, "Z").text = str(z)


def _viewpoint_xml(
    guid: str,
    camera: tuple[float, ...],
    fov: float | None,
    scale: float | None,
    clips: list[tuple[float, ...]],
    guids: list[str] | None,
    visibility: str,
) -> bytes:
    root = ET.Element("VisualizationInfo", Guid=guid)

    comps = ET.SubElement(root, "Components")
    if guids:
        if visibility == "isolate":
            vis_el = ET.SubElement(comps, "Visibility", DefaultVisibility="false")
            exc = ET.SubElement(vis_el, "Exceptions")
            for g in guids:
                ET.SubElement(exc, "Component", IfcGuid=g)
        else:
            sel = ET.SubElement(comps, "Selection")
            for g in guids:
                ET.SubElement(sel, "Component", IfcGuid=g)
            ET.SubElement(comps, "Visibility", DefaultVisibility="true")
    else:
        ET.SubElement(comps, "Visibility", DefaultVisibility="true")

    px, py, pz, dx, dy, dz, ux, uy, uz = camera
    if fov is not None:
        cam = ET.SubElement(root, "PerspectiveCamera")
        _xyz(cam, "CameraViewPoint", px, py, pz)
        _xyz(cam, "CameraDirection", dx, dy, dz)
        _xyz(cam, "CameraUpVector", ux, uy, uz)
        ET.SubElement(cam, "FieldOfView").text = f"{fov:.4f}"
    elif scale is not None:
        cam = ET.SubElement(root, "OrthogonalCamera")
        _xyz(cam, "CameraViewPoint", px, py, pz)
        _xyz(cam, "CameraDirection", dx, dy, dz)
        _xyz(cam, "CameraUpVector", ux, uy, uz)
        ET.SubElement(cam, "ViewToWorldScale").text = f"{scale:.4f}"

    if clips:
        cps = ET.SubElement(root, "ClippingPlanes")
        for clip in clips:
            cp = ET.SubElement(cps, "ClippingPlane")
            _xyz(cp, "Location",  clip[0], clip[1], clip[2])
            _xyz(cp, "Direction", clip[3], clip[4], clip[5])

    return b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode").encode()


def _markup_xml(
    topic_guid: str,
    vp_guid: str | None,
    title: str,
    comment: str,
    author: str,
    now: str,
    description: str = "",
) -> bytes:
    root = ET.Element("Markup")
    topic = ET.SubElement(root, "Topic",
                          Guid=topic_guid, TopicType="Coordination", TopicStatus="Open")
    ET.SubElement(topic, "Title").text = title or "IFC View"
    if description:
        ET.SubElement(topic, "Description").text = description
    ET.SubElement(topic, "CreationDate").text = now
    ET.SubElement(topic, "CreationAuthor").text = author
    if vp_guid:
        vps = ET.SubElement(topic, "Viewpoints", Guid=vp_guid)
        ET.SubElement(vps, "Viewpoint").text = "viewpoint.bcfv"

    if comment and comment.strip():
        c = ET.SubElement(root, "Comment", Guid=str(uuid.uuid4()))
        ET.SubElement(c, "Date").text = now
        ET.SubElement(c, "Author").text = author
        ET.SubElement(c, "Comment").text = comment
        if vp_guid:
            ET.SubElement(c, "Viewpoint", Guid=vp_guid)

    return b'<?xml version="1.0" encoding="utf-8"?>\n' + ET.tostring(root, encoding="unicode").encode()


def build_bcf(
    camera: tuple[float, ...] | None = None,
    fov: float | None = None,
    scale: float | None = None,
    clips: list[tuple[float, ...]] | None = None,
    guids: list[str] | None = None,
    visibility: str = "highlight",
    title: str = "IFC View",
    comment: str = "",
    description: str = "",
    author: str = "anonymous",
) -> bytes:
    """Build a BCF 2.1 zip archive and return the raw bytes.

    :param camera: 9-tuple (px, py, pz, dx, dy, dz, ux, uy, uz) in IFC coords.
    :param fov: Perspective field of view in degrees.  Mutually exclusive with *scale*.
    :param scale: Orthographic view-to-world scale.  Mutually exclusive with *fov*.
    :param clips: List of 6-tuples (px, py, pz, nx, ny, nz) clipping planes.
    :param guids: IfcGloballyUniqueId strings for selected/visible elements.
    :param visibility: ``'highlight'``, ``'ghost'``, or ``'isolate'``.
    :param title: BCF topic title.
    :param comment: Optional comment text added to the topic.
    :param description: Optional long description for the topic (e.g. the source ifc:// URL).
    :param author: Author string recorded in the BCF markup.
    :returns: Bytes of a valid BCF 2.1 zip archive.
    """
    topic_guid = str(uuid.uuid4())
    vp_guid = str(uuid.uuid4()) if camera is not None else None
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bcf.version", _VERSION_XML)
        zf.writestr(
            f"{topic_guid}/markup.bcf",
            _markup_xml(topic_guid, vp_guid, title, comment, author, now, description),
        )
        if vp_guid is not None:
            zf.writestr(
                f"{topic_guid}/viewpoint.bcfv",
                _viewpoint_xml(vp_guid, camera, fov, scale, clips or [], guids, visibility),
            )
    return buf.getvalue()
