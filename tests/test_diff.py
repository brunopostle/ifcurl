"""Tests for ifcurl.diff — step ID extraction and entity expansion."""

from __future__ import annotations

import pytest

from ifcurl.diff import expand_step_ids, step_ids_from_diff


# ---------------------------------------------------------------------------
# step_ids_from_diff
# ---------------------------------------------------------------------------

SAMPLE_DIFF = """\
diff --git a/model.ifc b/model.ifc
--- a/model.ifc
+++ b/model.ifc
@@ -1,5 +1,5 @@
 #1=IFCPROJECT('abc');
-#10=IFCWALL('old_wall');
-#11=IFCWALLSTANDARDCASE('deleted_wall');
+#10=IFCWALL('new_wall');
+#99=IFCWALLSTANDARDCASE('added_wall');
 #20=IFCSLAB('slab');
"""


class TestStepIdsFromDiff:
    def test_modified_id_in_both_sets(self):
        ids = step_ids_from_diff(SAMPLE_DIFF)
        assert 10 in ids["modified"]

    def test_added_id_only_in_inserted(self):
        ids = step_ids_from_diff(SAMPLE_DIFF)
        assert 99 in ids["added"]

    def test_removed_id_only_in_deleted(self):
        ids = step_ids_from_diff(SAMPLE_DIFF)
        assert 11 in ids["removed"]

    def test_unchanged_id_absent(self):
        ids = step_ids_from_diff(SAMPLE_DIFF)
        assert 1 not in ids["added"]
        assert 1 not in ids["modified"]
        assert 1 not in ids["removed"]
        assert 20 not in ids["added"]
        assert 20 not in ids["modified"]
        assert 20 not in ids["removed"]

    def test_modified_not_in_added_or_removed(self):
        ids = step_ids_from_diff(SAMPLE_DIFF)
        assert 10 not in ids["added"]
        assert 10 not in ids["removed"]

    def test_empty_diff(self):
        ids = step_ids_from_diff("")
        assert ids == {"added": set(), "modified": set(), "removed": set()}

    def test_context_lines_ignored(self):
        diff = " #5=IFCSITE('site');\n"
        ids = step_ids_from_diff(diff)
        assert 5 not in ids["added"] and 5 not in ids["removed"] and 5 not in ids["modified"]

    def test_git_header_lines_ignored(self):
        diff = "--- a/model.ifc\n+++ b/model.ifc\n"
        ids = step_ids_from_diff(diff)
        assert ids == {"added": set(), "modified": set(), "removed": set()}


# ---------------------------------------------------------------------------
# expand_step_ids — requires a real ifcopenshell model
# ---------------------------------------------------------------------------


class TestExpandStepIds:
    def test_product_promoted_to_modified(self, model_with_geometry):
        """An IfcProduct step ID in 'modified' stays in modified after expansion."""
        # Find a real product step ID in the model
        products = list(model_with_geometry.by_type("IfcProduct"))
        assert products, "fixture must contain at least one IfcProduct"
        pid = products[0].id()

        raw = {"added": set(), "modified": {pid}, "removed": set()}
        expanded = expand_step_ids(model_with_geometry, raw)

        assert pid in expanded["modified"]

    def test_added_ids_not_moved_to_modified(self, model_with_geometry):
        """IDs in 'added' must not be demoted into 'modified' by expansion."""
        products = list(model_with_geometry.by_type("IfcProduct"))
        assert products
        pid = products[0].id()

        raw = {"added": {pid}, "modified": set(), "removed": set()}
        expanded = expand_step_ids(model_with_geometry, raw)

        assert pid in expanded["added"]
        assert pid not in expanded["modified"]

    def test_removed_ids_unchanged(self, model_with_geometry):
        """The 'removed' set is never altered by expansion."""
        raw = {"added": set(), "modified": set(), "removed": {9999}}
        expanded = expand_step_ids(model_with_geometry, raw)
        assert expanded["removed"] == {9999}

    def test_nonexistent_id_silently_skipped(self, model_with_geometry):
        """Step IDs absent from the model don't cause errors; they pass through unchanged."""
        raw = {"added": set(), "modified": {99999999}, "removed": set()}
        expanded = expand_step_ids(model_with_geometry, raw)
        # The ID passes through unmodified — expand adds no extra products for it
        assert expanded["modified"] == {99999999}

    def test_shape_representation_expands_to_product(self, model_with_geometry):
        """A changed IfcShapeRepresentation expands to its parent IfcProduct."""
        reps = list(model_with_geometry.by_type("IfcShapeRepresentation"))
        if not reps:
            pytest.skip("no IfcShapeRepresentation in fixture")

        rep = reps[0]
        products_via_rep = set()
        for prod_rep in rep.OfProductRepresentation:
            for product in prod_rep.ShapeOfProduct:
                products_via_rep.add(product.id())

        if not products_via_rep:
            pytest.skip("representation not linked to any product in fixture")

        raw = {"added": set(), "modified": {rep.id()}, "removed": set()}
        expanded = expand_step_ids(model_with_geometry, raw)

        for pid in products_via_rep:
            assert pid in expanded["modified"]
