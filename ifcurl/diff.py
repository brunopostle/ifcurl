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

"""IFC diff utilities: extract changed step IDs from a git diff and expand
them to the IfcProduct entities that are visually affected.

Algorithm adapted from Bonsai / ifcgit (IfcOpenShell project, GPL-3.0).

Step ID extraction
------------------
IFC files are stored in STEP format where every entity has a stable numeric
ID: ``#123 = IfcWall(...)``.  A plain text diff of two versions of the same
file therefore shows which IDs were added (``+#NNN=``) and which were deleted
(``-#NNN=``).  An ID appearing in both sets means the entity was *modified*
in-place; IDs only in the added set are truly *new*; IDs only in the deleted
set were *removed*.

Entity expansion
----------------
Many IFC entities (IfcShapeRepresentation, IfcObjectPlacement, IfcPropertySet,
…) don't have their own visible geometry — they're owned by an IfcProduct.
When such an entity changes, we walk up the IFC graph to find all IfcProducts
that are visually affected, and promote them to *modified*.  This ensures that
a wall whose shape was edited is coloured blue even though the IfcWall entity's
own step ID didn't change in the diff.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import ifcopenshell

DiffIds = dict[str, set[int]]


def step_ids_from_diff(diff_text: str) -> DiffIds:
    """Parse a git diff of an IFC file and return classified step ID sets.

    :param diff_text: Output of ``git diff hash_a hash_b path/to/file.ifc``.
    :returns: ``{"added": set, "modified": set, "removed": set}`` of integer
        step IDs.  *modified* IDs appear in both the inserted and deleted
        halves of the diff.
    """
    inserted: set[int] = set()
    deleted: set[int] = set()
    for line in diff_text.splitlines():
        m = re.match(r"^\+#(\d+)=", line)
        if m:
            inserted.add(int(m.group(1)))
            continue
        m = re.match(r"^-#(\d+)=", line)
        if m:
            deleted.add(int(m.group(1)))
    modified = inserted & deleted
    return {
        "added": inserted - modified,
        "modified": modified,
        "removed": deleted - modified,
    }


def expand_step_ids(model: "ifcopenshell.file", step_ids: DiffIds) -> DiffIds:
    """Propagate changed step IDs to the IfcProduct entities that own them.

    :param model: The *head* (newer) model, used to walk IFC relationships.
    :param step_ids: Raw diff IDs from :func:`step_ids_from_diff`.
    :returns: A new dict with the same *added* and *removed* sets, but with
        *modified* augmented by any IfcProducts whose dependent entities changed.
    """
    extra_modified: set[int] = set()

    def collect(entity: object, depth: int = 0) -> None:
        if depth > 2:
            return
        if entity.is_a("IfcProduct"):
            extra_modified.add(entity.id())
        elif entity.is_a("IfcProductDefinitionShape"):
            for product in entity.ShapeOfProduct:
                extra_modified.add(product.id())
        elif entity.is_a("IfcObjectPlacement"):
            for product in entity.PlacesObject:
                extra_modified.add(product.id())
        elif entity.is_a("IfcTypeProduct"):
            for rel in entity.Types:
                for obj in rel.RelatedObjects:
                    extra_modified.add(obj.id())
        elif entity.is_a("IfcShapeRepresentation"):
            for prod_rep in entity.OfProductRepresentation:
                for product in prod_rep.ShapeOfProduct:
                    extra_modified.add(product.id())
        elif entity.is_a("IfcRepresentationItem"):
            for ref in model.get_inverse(entity):
                if ref.is_a("IfcShapeRepresentation"):
                    collect(ref, depth + 1)
        elif entity.is_a("IfcPropertySet"):
            for rel in entity.DefinesOccurrence:
                for obj in rel.RelatedObjects:
                    extra_modified.add(obj.id())
        elif entity.is_a("IfcProperty"):
            for pset in entity.PartOfPset:
                collect(pset, depth + 1)

    for step_id in step_ids["modified"] | step_ids["added"]:
        try:
            collect(model.by_id(step_id))
        except Exception:
            pass  # entity absent from this model version

    return {
        "added": step_ids["added"],
        "modified": (step_ids["modified"] | extra_modified) - step_ids["added"],
        "removed": step_ids["removed"],
    }
