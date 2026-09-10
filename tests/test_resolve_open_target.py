# -*- coding: utf-8 -*-
"""Stdlib tests for create_project.resolve_open_target().

Placed in tests/ (and not tools/tests/ as suggested by the task) so it is picked up by
the CI: `python -m unittest discover -s tests`, like all the other stdlib suites in the repo.
Contract checked: resolve_open_target NEVER RAISES for a business case (project/entity/step
not found, enum value unknown to the manifest) - it returns a dict exists=False + reason.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import create_project as cp


class ResolveOpenTargetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ylos_rot_"))
        info = cp.create(
            "Proj", root=str(self.tmp / "src"), cache=str(self.tmp / "cache"),
            prod_type="FILM",
        )
        self.proj = Path(info["source"])
        # unknown prod_type injected into project.json (legacy/real value like 'XR'):
        # resolve does not read this field, it must not break anything.
        pj = self.proj / "_pipeline" / "project.json"
        d = json.loads(pj.read_text(encoding="utf-8"))
        d["prod_type"] = "ZZ_UNKNOWN"
        pj.write_text(json.dumps(d), encoding="utf-8")
        cp.create_asset(
            str(self.proj), "PROP_Box_Default", entity_type="asset",
            asset_type="PROP", steps=["modeling", "lookdev"],
        )
        self.entity_dir = self.proj / "assets" / "PROP_Box_Default"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_scene_default_when_no_wip(self):
        t = cp.resolve_open_target("PROP_Box_Default", "blender", project_root=str(self.proj))
        self.assertTrue(t["exists"])
        self.assertEqual(t["kind"], "scene_default")
        self.assertTrue(t["path"].endswith("asset_root.usda"))
        self.assertEqual(t["step"], "modeling")  # first declared step

    def test_latest_wip_wins_and_picks_highest_version(self):
        wip = self.entity_dir / "modeling" / "wip"
        (wip / "PROP_Box_Default_modeling_v001.blend").write_text("x")
        (wip / "PROP_Box_Default_modeling_v003.blend").write_text("x")
        (wip / "PROP_Box_Default_modeling_v002.blend").write_text("x")
        t = cp.resolve_open_target(
            "PROP_Box_Default", "blender", step="modeling", project_root=str(self.proj),
        )
        self.assertEqual(t["kind"], "wip")
        self.assertTrue(t["path"].endswith("v003.blend"))
        self.assertTrue(t["exists"])

    def test_unknown_entity_returns_dict_no_raise(self):
        t = cp.resolve_open_target("NOPE_None_None", "blender", project_root=str(self.proj))
        self.assertFalse(t["exists"])
        self.assertIsNone(t["path"])
        self.assertIn("reason", t)

    def test_unknown_enum_in_manifest_does_not_raise(self):
        # unknown prod_type AND type in the entity manifest: resolve resolves anyway.
        mp = self.entity_dir / "manifest.json"
        m = json.loads(mp.read_text(encoding="utf-8"))
        m["prod_type"] = "ZZ_UNKNOWN"
        m["type"] = "ZZ_UNKNOWN"
        mp.write_text(json.dumps(m), encoding="utf-8")
        t = cp.resolve_open_target("PROP_Box_Default", "blender", project_root=str(self.proj))
        self.assertTrue(t["exists"])
        self.assertEqual(t["kind"], "scene_default")

    def test_requested_step_preserved(self):
        t = cp.resolve_open_target(
            "PROP_Box_Default", "blender", step="lookdev", project_root=str(self.proj),
        )
        self.assertTrue(t["exists"])
        self.assertEqual(t["step"], "lookdev")

    def test_missing_project_root_no_raise(self):
        t = cp.resolve_open_target(
            "X_Y_Z", "blender", project_root=str(self.tmp / "does_not_exist"),
        )
        self.assertFalse(t["exists"])
        self.assertIn("reason", t)

    def test_corrupt_manifest_degrades_cleanly(self):
        # unreadable manifest -> empty dict internally, fallback to scene_default if the root
        # exists (it exists after create_asset), never an exception.
        (self.entity_dir / "manifest.json").write_text("{ not json", encoding="utf-8")
        t = cp.resolve_open_target("PROP_Box_Default", "blender", project_root=str(self.proj))
        # entity_type default 'asset' -> asset_root.usda exists
        self.assertTrue(t["exists"])
        self.assertEqual(t["kind"], "scene_default")


if __name__ == "__main__":
    unittest.main(verbosity=2)
