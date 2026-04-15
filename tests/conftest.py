"""Shared pytest fixtures for ifcurl tests."""

from __future__ import annotations

import ifcopenshell.api.aggregate
import ifcopenshell.api.context
import ifcopenshell.api.geometry
import ifcopenshell.api.owner.settings
import ifcopenshell.api.project
import ifcopenshell.api.root
import ifcopenshell.api.spatial
import ifcopenshell.api.unit
import numpy as np
import pytest

# ---------------------------------------------------------------------------
# IFC model fixtures
# ---------------------------------------------------------------------------


def _suppress_owner(f):
    ifcopenshell.api.owner.settings.get_user = lambda ifc: (ifc.by_type("IfcPersonAndOrganization") or [None])[0]
    ifcopenshell.api.owner.settings.get_application = lambda ifc: (ifc.by_type("IfcApplication") or [None])[0]
    return f


@pytest.fixture(scope="session")
def model_with_geometry():
    """IFC4 model with two walls that have geometric representations."""
    f = ifcopenshell.api.project.create_file()
    _suppress_owner(f)

    project = ifcopenshell.api.root.create_entity(f, ifc_class="IfcProject", name="TestProject")
    ifcopenshell.api.unit.assign_unit(f)

    site = ifcopenshell.api.root.create_entity(f, ifc_class="IfcSite", name="TestSite")
    building = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBuilding", name="TestBuilding")
    storey = ifcopenshell.api.root.create_entity(f, ifc_class="IfcBuildingStorey", name="Ground Floor")

    ifcopenshell.api.aggregate.assign_object(f, products=[site], relating_object=project)
    ifcopenshell.api.aggregate.assign_object(f, products=[building], relating_object=site)
    ifcopenshell.api.aggregate.assign_object(f, products=[storey], relating_object=building)

    model_ctx = ifcopenshell.api.context.add_context(f, context_type="Model")
    body = ifcopenshell.api.context.add_context(
        f, context_type="Model", context_identifier="Body",
        target_view="MODEL_VIEW", parent=model_ctx,
    )

    wall1 = ifcopenshell.api.root.create_entity(f, ifc_class="IfcWall", name="Wall001")
    rep1 = ifcopenshell.api.geometry.add_wall_representation(f, context=body, length=5, height=3, thickness=0.2)
    ifcopenshell.api.geometry.assign_representation(f, product=wall1, representation=rep1)
    ifcopenshell.api.spatial.assign_container(f, products=[wall1], relating_structure=storey)

    wall2 = ifcopenshell.api.root.create_entity(f, ifc_class="IfcWall", name="Wall002")
    rep2 = ifcopenshell.api.geometry.add_wall_representation(f, context=body, length=4, height=3, thickness=0.2)
    ifcopenshell.api.geometry.assign_representation(f, product=wall2, representation=rep2)
    ifcopenshell.api.spatial.assign_container(f, products=[wall2], relating_structure=storey)
    matrix = np.eye(4)
    matrix[1, 3] = 3.0
    ifcopenshell.api.geometry.edit_object_placement(f, product=wall2, matrix=matrix)

    return f


# ---------------------------------------------------------------------------
# Local git repo fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def local_ifc_repo(tmp_path_factory, model_with_geometry):
    """A local bare-ish git repo containing model.ifc committed at HEAD.

    Returns a dict with keys:
      path     — absolute path to the working-tree repo
      hexsha   — the commit hexsha
      bytes    — raw IFC file bytes as committed
    """
    import git as gitpkg

    repo_dir = tmp_path_factory.mktemp("ifc_repo")
    ifc_path = repo_dir / "model.ifc"
    model_with_geometry.write(str(ifc_path))

    repo = gitpkg.Repo.init(str(repo_dir))
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "Test")
        cw.set_value("user", "email", "test@example.com")
    repo.index.add(["model.ifc"])
    commit = repo.index.commit("Add model")
    repo.create_tag("v1.0")

    return {
        "path": str(repo_dir),
        "hexsha": commit.hexsha,
        "bytes": ifc_path.read_bytes(),
    }
