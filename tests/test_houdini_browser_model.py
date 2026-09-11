#!/usr/bin/env python3
"""
tests/test_houdini_browser_model.py - stdlib tests (unittest) for
plugins/houdini/python/ylos_browser_model.py, the PURE view model of the Houdini Python
Panel cockpit (plan-usable-v1 Phase 4.1).

Why this file exists: hython has no license on this machine and a Qt panel cannot be
driven headlessly, so the cockpit was split in three (model / view / bridge) precisely so
that everything it DISPLAYS is decided here, in plain python3, and can be verified without
Houdini. A regression in what the panel shows is caught by these tests, not by opening
Houdini.

The panel and the bridge are NOT imported here: importing ylos_browser_panel would pull
hutil.Qt (Houdini-only). The guard test below locks in that the model itself never does.

Usage: python3 -m unittest tests.test_houdini_browser_model
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
import create_project as cp  # noqa: E402

# plugins/houdini/python is not a package -> explicit loading by path (same pattern as
# tests/test_ylos_houdini.py).
_MODULE_PATH = _REPO_ROOT / "plugins" / "houdini" / "python" / "ylos_browser_model.py"
_spec = importlib.util.spec_from_file_location("ylos_browser_model", _MODULE_PATH)
bm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bm)   # raises if a top-level hou/Qt import existed -> CI guard


class ImportableWithoutHoudiniTestCase(unittest.TestCase):
    """The model must load without hou AND without Qt: it is the half of the cockpit that
    a CI machine (and this licenseless machine) can actually execute."""

    def test_no_hou_and_no_qt_pulled(self):
        # Asked in a FRESH interpreter rather than against this process's sys.modules:
        # tests/test_houdini_browser_panel.py legitimately injects a 'hutil.Qt' stub, and
        # a guard a sibling test can flip proves nothing. In isolation the question is
        # exact: does importing the model ALONE pull hou or Qt?
        code = (
            "import importlib.util, sys, json\n"
            "spec = importlib.util.spec_from_file_location('m', %r)\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
            "print(json.dumps(sorted(n for n in sys.modules\n"
            "                        if n == 'hou' or n.startswith(('hou.', 'hutil')))))\n"
            % str(_MODULE_PATH)
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, cwd=str(_REPO_ROOT))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), [],
                         "the model must import neither hou nor Qt")

    def test_vocabulary_values_come_from_the_orchestrator(self):
        # the model owns the LABELS/COLOURS, never the values (CLAUDE.md vocabulary rule).
        self.assertEqual(set(bm.FAMILY_ORDER), set(cp.ENTITY_DIR))
        self.assertEqual(bm.STATUS_CHOICES,
                         (cp.STEP_STATUS_AUTO,) + tuple(cp.STEP_STATUS_EXPLICIT))
        for status in cp.STEP_STATUSES:
            self.assertIn(status, bm.STATUS_COLORS, status)

    def test_status_color_falls_back_instead_of_raising(self):
        self.assertEqual(bm.status_color("a_status_added_later"),
                         bm.STATUS_COLOR_DEFAULT)


class ActiveProjectTestCase(unittest.TestCase):
    """Active project header + 'Change project': the ~/.ylos/active_project contract,
    shared with the web UI (ylos_ui._write_active) and Blender."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ylos_bm_active_")).resolve()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        info = cp.create("Proj", root=self._tmp / "src", cache=self._tmp / "cache")
        self.project = Path(info["source"])
        self.active_file = self._tmp / "ylos" / "active_project"

    def test_no_active_project(self):
        info = bm.active_project_info(self.active_file)
        self.assertEqual(info["path"], None)
        self.assertFalse(info["exists"])

    def test_set_then_read_back(self):
        bm.set_active_project(self.project, self.active_file)
        # exactly the ylos_ui._write_active contract: one line + trailing newline.
        self.assertEqual(self.active_file.read_text(encoding="utf-8"),
                         str(self.project) + "\n")
        info = bm.active_project_info(self.active_file)
        self.assertTrue(info["exists"])
        self.assertEqual(info["path"], str(self.project))
        self.assertEqual(info["name"], "Proj")

    def test_refuses_a_folder_that_is_not_a_project(self):
        # pointing the WHOLE machine at a non-project would break every consumer at once.
        with self.assertRaises(ValueError):
            bm.set_active_project(self._tmp / "src", self.active_file)
        self.assertFalse(self.active_file.exists())

    def test_a_stale_path_is_reported_not_hidden(self):
        self.active_file.parent.mkdir(parents=True, exist_ok=True)
        self.active_file.write_text(str(self._tmp / "gone") + "\n", encoding="utf-8")
        info = bm.active_project_info(self.active_file)
        self.assertEqual(info["path"], str(self._tmp / "gone"))
        self.assertFalse(info["exists"])


class ProjectViewTestCase(unittest.TestCase):
    """Entities / steps / scenefiles / products / dependencies on a real temp project."""

    ASSET = "CHARACTER_Lina_Default"
    PROP = "PROP_Barrel_Default"
    SHOT = "ANIMATION_Sq010_Default"

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ylos_bm_proj_")).resolve()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        info = cp.create("Proj", root=self._tmp / "src", cache=self._tmp / "cache")
        self.project = Path(info["source"])
        cp.create_asset(self.project, self.ASSET, entity_type="asset",
                        asset_type="CHARACTER")
        cp.create_asset(self.project, self.PROP, entity_type="asset", asset_type="PROP")
        cp.create_asset(self.project, self.SHOT, entity_type="shot",
                        asset_type="ANIMATION")

    # -- helpers ------------------------------------------------------------------------

    def _entity_dir(self, family, entity):
        return self.project / family / entity

    def _write_manifest_key(self, family, entity, key, value):
        path = self._entity_dir(family, entity) / cp.ASSET_MANIFEST_NAME
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest[key] = value
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def _publish_entry(self, entity, step, version, dependencies=None):
        stem = f"{entity}_{step}_v{version:03d}"
        entry = {"version": version, "status": "complete",
                 "artifact": f"{step}/publish/{stem}/{stem}.usda"}
        if dependencies is not None:
            entry[cp.DEPENDENCIES_KEY] = dependencies
        return entry

    # -- entities -----------------------------------------------------------------------

    def test_entity_rows_sorted_by_family_then_name(self):
        rows = bm.entity_rows(self.project)
        self.assertEqual([r["name"] for r in rows], [self.ASSET, self.PROP, self.SHOT])
        self.assertEqual([r["family"] for r in rows], ["asset", "asset", "shot"])
        self.assertEqual(rows[0]["family_label"], "Assets")
        self.assertEqual(rows[0]["entity_type"], "CHARACTER")

    def test_entity_rows_carry_a_thumbnail_dict(self):
        row = bm.entity_rows(self.project)[0]
        self.assertIn("source", row["thumbnail"])
        self.assertEqual(row["thumbnail"]["source"], "none")   # nothing published yet

    def test_search_matches_name_and_sub_type(self):
        self.assertEqual([r["name"] for r in bm.entity_rows(self.project, "barrel")],
                         [self.PROP])
        self.assertEqual([r["name"] for r in bm.entity_rows(self.project, "CHARACTER")],
                         [self.ASSET])
        self.assertEqual(bm.entity_rows(self.project, "nothing_matches"), [])

    def test_an_orphan_folder_is_listed_and_flagged(self):
        # a folder without a manifest is a GHOST: surfaced with 'broken', never silently
        # skipped (a ghost the tool hides is worse than a flagged one).
        (self.project / "sets" / "lecube").mkdir(parents=True, exist_ok=True)
        rows = {r["name"]: r for r in bm.entity_rows(self.project)}
        self.assertIn("lecube", rows)
        self.assertTrue(rows["lecube"]["broken"])
        self.assertIn("broken", rows["lecube"]["label"])

    def test_entity_groups_drops_empty_families(self):
        groups = bm.entity_groups(self.project)
        self.assertEqual([g["family"] for g in groups], ["asset", "shot"])
        self.assertEqual(groups[0]["label"], "Assets")
        self.assertEqual(len(groups[0]["rows"]), 2)
        self.assertEqual(bm.entity_groups(self.project, "nothing_matches"), [])

    # -- steps --------------------------------------------------------------------------

    def test_step_rows_follow_the_manifest_order_and_derive_the_status(self):
        rows = bm.step_rows(self.project, self.ASSET)
        self.assertEqual([r["step"] for r in rows],
                         ["modeling", "rigging", "lookdev", "fx"])
        self.assertTrue(all(r["status"] == "empty" for r in rows))
        self.assertTrue(all(r["explicit"] is False for r in rows))
        self.assertEqual(rows[0]["color"], bm.STATUS_COLORS["empty"])

    def test_step_rows_count_scenefiles_and_products(self):
        wip = self._entity_dir("assets", self.ASSET) / "modeling" / "wip"
        wip.mkdir(parents=True, exist_ok=True)
        (wip / f"{self.ASSET}_modeling_v001.hipnc").write_text("", encoding="utf-8")
        (wip / f"{self.ASSET}_modeling_v002.blend").write_text("", encoding="utf-8")
        self._write_manifest_key("assets", self.ASSET, cp.STEP_PUBLISHES_KEY, {
            "modeling": [self._publish_entry(self.ASSET, "modeling", 1),
                         {"version": 2, "status": "pending",
                          "artifact": "modeling/publish/x/x.usda"}],
        })
        row = {r["step"]: r for r in bm.step_rows(self.project, self.ASSET)}["modeling"]
        self.assertEqual(row["scenefiles"], 2)      # both DCCs counted
        self.assertEqual(row["products"], 1)        # 'pending' is not a product
        self.assertEqual(row["latest_version"], 1)
        self.assertEqual(row["status"], "published")
        self.assertEqual(row["color"], bm.STATUS_COLORS["published"])

    def test_explicit_status_wins_and_can_be_cleared(self):
        bm.set_status(self.project, self.ASSET, "modeling", "review")
        row = {r["step"]: r for r in bm.step_rows(self.project, self.ASSET)}["modeling"]
        self.assertEqual(row["status"], "review")
        self.assertTrue(row["explicit"])
        self.assertEqual(row["derived"], "empty")
        bm.set_status(self.project, self.ASSET, "modeling", cp.STEP_STATUS_AUTO)
        row = {r["step"]: r for r in bm.step_rows(self.project, self.ASSET)}["modeling"]
        self.assertEqual(row["status"], "empty")
        self.assertFalse(row["explicit"])

    def test_set_status_refuses_an_undeclared_step(self):
        with self.assertRaises(ValueError):
            bm.set_status(self.project, self.ASSET, "bogus_step", "review")

    def test_step_rows_of_an_unknown_entity_is_empty(self):
        self.assertEqual(bm.step_rows(self.project, "NOPE_Nope_Nope"), [])

    # -- scenefiles ---------------------------------------------------------------------

    def test_scenefile_rows_newest_first_with_open_only_for_hip(self):
        wip = self._entity_dir("assets", self.ASSET) / "modeling" / "wip"
        wip.mkdir(parents=True, exist_ok=True)
        (wip / f"{self.ASSET}_modeling_v001.hipnc").write_text("", encoding="utf-8")
        (wip / f"{self.ASSET}_modeling_v002.blend").write_text("", encoding="utf-8")
        rows = bm.scenefile_rows(self.project, self.ASSET, "modeling")
        self.assertEqual([r["version"] for r in rows], [2, 1])
        by_dcc = {r["dcc"]: r for r in rows}
        self.assertTrue(by_dcc["houdini"]["openable"])
        # a .blend is LISTED (shared task) but Houdini never offers to open it.
        self.assertFalse(by_dcc["blender"]["openable"])

    def test_scenefile_label_shows_the_sidecar_comment(self):
        wip = self._entity_dir("assets", self.ASSET) / "modeling" / "wip"
        wip.mkdir(parents=True, exist_ok=True)
        hip = wip / f"{self.ASSET}_modeling_v001.hipnc"
        hip.write_text("", encoding="utf-8")
        (wip / (hip.name + ".json")).write_text(
            json.dumps({"comment": "blocking pass", "user": "seb",
                        "houdini_version": "21.0.631"}), encoding="utf-8")
        row = bm.scenefile_rows(self.project, self.ASSET, "modeling")[0]
        self.assertEqual(row["comment"], "blocking pass")
        self.assertEqual(row["dcc_version"], "21.0.631")
        self.assertIn("blocking pass", row["label"])
        self.assertIn("v001", row["label"])

    def test_scenefile_rows_empty_step(self):
        self.assertEqual(bm.scenefile_rows(self.project, self.ASSET, "rigging"), [])

    # -- products -----------------------------------------------------------------------

    def test_product_rows_flag_latest_and_missing(self):
        self._write_manifest_key("assets", self.ASSET, cp.STEP_PUBLISHES_KEY, {
            "modeling": [self._publish_entry(self.ASSET, "modeling", 1),
                         self._publish_entry(self.ASSET, "modeling", 2)],
        })
        rows = bm.product_rows(self.project, self.ASSET, "modeling")
        self.assertEqual([r["version"] for r in rows], [2, 1])
        self.assertTrue(rows[0]["is_latest"])
        self.assertFalse(rows[1]["is_latest"])
        # the artifacts were never written to disk: kept and flagged, never hidden.
        self.assertFalse(rows[0]["exists"])
        self.assertIn("MISSING", rows[0]["label"])
        self.assertIn("latest", rows[0]["label"])

    def test_product_rows_expose_recorded_dependencies(self):
        self._write_manifest_key("shots", self.SHOT, cp.STEP_PUBLISHES_KEY, {
            "animation": [self._publish_entry(
                self.SHOT, "animation", 1,
                dependencies=[{"entity": self.ASSET, "step": "modeling", "version": 1}])],
        })
        row = bm.product_rows(self.project, self.SHOT, "animation")[0]
        self.assertEqual(row["dependencies"],
                         [{"entity": self.ASSET, "step": "modeling", "version": 1}])

    # -- dependencies -------------------------------------------------------------------

    def _wire_shot_uses_asset(self, pinned_version=1, published_versions=(1, 2)):
        self._write_manifest_key("assets", self.ASSET, cp.STEP_PUBLISHES_KEY, {
            "modeling": [self._publish_entry(self.ASSET, "modeling", v)
                         for v in published_versions],
        })
        self._write_manifest_key("shots", self.SHOT, cp.STEP_PUBLISHES_KEY, {
            "animation": [self._publish_entry(
                self.SHOT, "animation", 1,
                dependencies=[{"entity": self.ASSET, "step": "modeling",
                               "version": pinned_version}])],
        })

    def test_uses_and_used_in_are_two_views_of_the_same_edge(self):
        self._wire_shot_uses_asset()
        shot = bm.dependency_rows(self.project, self.SHOT)
        self.assertEqual([r["entity"] for r in shot["uses"]], [self.ASSET])
        self.assertEqual(shot["used_in"], [])
        asset = bm.dependency_rows(self.project, self.ASSET)
        self.assertEqual([r["entity"] for r in asset["used_in"]], [self.SHOT])
        self.assertEqual(asset["uses"], [])

    def test_update_available_when_pinned_below_the_latest_publish(self):
        self._wire_shot_uses_asset(pinned_version=1, published_versions=(1, 2))
        row = bm.dependency_rows(self.project, self.SHOT)["uses"][0]
        self.assertTrue(row["update_available"])
        self.assertEqual(row["latest_version"], 2)
        self.assertEqual(row["label"], f"{self.ASSET} / modeling v001")
        self.assertEqual(bm.dependency_rows(self.project, self.SHOT)["outdated"], 1)
        # the same edge seen from the asset side marks the consumer as needing an update.
        self.assertTrue(bm.dependency_rows(self.project, self.ASSET)["used_in"][0]
                        ["update_available"])

    def test_no_update_when_pinned_to_the_latest(self):
        self._wire_shot_uses_asset(pinned_version=2, published_versions=(1, 2))
        rows = bm.dependency_rows(self.project, self.SHOT)
        self.assertFalse(rows["uses"][0]["update_available"])
        self.assertEqual(rows["outdated"], 0)

    def test_dependency_rows_of_an_isolated_entity(self):
        rows = bm.dependency_rows(self.project, self.PROP)
        self.assertEqual((rows["uses"], rows["used_in"], rows["outdated"]), ([], [], 0))

    # -- starter preview ----------------------------------------------------------------

    def test_starter_preview_describes_what_new_scene_would_create(self):
        preview = bm.starter_preview(self.project, self.SHOT, "animation")
        self.assertTrue(preview["ok"])
        self.assertEqual(preview["version"], 1)
        self.assertEqual(preview["stem"], f"{self.SHOT}_animation_v001")
        self.assertTrue(preview["camera"])
        self.assertFalse(preview["lighting"])
        self.assertIn("v001", preview["label"])
        self.assertIn("frames 1001-1100", preview["label"])

    def test_starter_preview_reports_a_refusal_instead_of_raising(self):
        preview = bm.starter_preview(self.project, self.ASSET, "bogus_step")
        self.assertFalse(preview["ok"])
        self.assertIn("bogus_step", preview["reason"])
        self.assertEqual(preview["label"], preview["reason"])


if __name__ == "__main__":
    unittest.main()
