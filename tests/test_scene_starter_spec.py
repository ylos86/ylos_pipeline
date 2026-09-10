#!/usr/bin/env python3
"""
tests/test_scene_starter_spec.py — stdlib tests (unittest) for the Scene Creator spec
create_project.scene_starter_spec() (plan-usable-v1 Phase 1.1/1.2).

The spec is the single, pure, serializable source of truth for WHAT a fresh authoring
scene contains; the DCC realizes it. These tests lock the department rules table, the
non-destructive WIP versioning, the shot frame_range pass-through, and the never-raises
business contract.

Usage: python3 tests/test_scene_starter_spec.py
    or: python3 -m unittest tests.test_scene_starter_spec
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
import create_project as cp  # noqa: E402


class _BaseCase(unittest.TestCase):
    """Project + one asset and one shot in a throwaway tmpdir."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ylos_starter_")).resolve()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        info = cp.create("Proj", root=self._tmp / "src", cache=self._tmp / "cache",
                         prod_type="FILM")
        self.project = Path(info["source"])
        cp.create_asset(self.project, "CHARACTER_Lina_Default",
                        entity_type="asset", asset_type="CHARACTER")
        cp.create_asset(self.project, "ANIMATION_Sq010_Default",
                        entity_type="shot", asset_type="ANIMATION")

    def _asset_dir(self, name):
        return self.project / "assets" / name

    def _shot_dir(self, name):
        return self.project / "shots" / name


class TestBusinessContract(_BaseCase):
    """Never raises for a business case: ok=False + reason."""

    def test_missing_project_root(self):
        r = cp.scene_starter_spec("CHARACTER_Lina_Default", "modeling")
        self.assertFalse(r["ok"])
        self.assertIn("project_root", r["reason"])

    def test_unknown_entity(self):
        r = cp.scene_starter_spec("NOPE_Absent_Default", "modeling",
                                  project_root=str(self.project))
        self.assertFalse(r["ok"])
        self.assertIn("not found", r["reason"])

    def test_step_not_declared(self):
        r = cp.scene_starter_spec("CHARACTER_Lina_Default", "compositing",
                                  project_root=str(self.project))
        self.assertFalse(r["ok"])
        self.assertIn("not declared", r["reason"])

    def test_spec_is_json_serializable(self):
        r = cp.scene_starter_spec("CHARACTER_Lina_Default", "modeling",
                                  project_root=str(self.project))
        self.assertTrue(r["ok"])
        json.dumps(r)  # must not raise (no Path/bpy leaking into the spec)


class TestAssetModeling(_BaseCase):
    """asset / modeling -> empty scene, no assembly reference, no camera."""

    def test_spec(self):
        r = cp.scene_starter_spec("CHARACTER_Lina_Default", "modeling",
                                  project_root=str(self.project))
        self.assertTrue(r["ok"])
        self.assertEqual(r["family"], "asset")
        self.assertEqual(r["entity_type"], "CHARACTER")
        self.assertEqual(r["prod_type"], "FILM")
        self.assertEqual(r["references"], [])
        self.assertFalse(r["camera"])
        self.assertIsNone(r["frame_range"])
        self.assertEqual(r["context"]["context_type"], "ASSET")
        self.assertEqual(r["context"]["asset_type"], "CHARACTER")

    def test_wip_first_version_and_path(self):
        r = cp.scene_starter_spec("CHARACTER_Lina_Default", "modeling",
                                  project_root=str(self.project))
        self.assertEqual(r["wip"]["version"], 1)
        self.assertEqual(r["wip"]["stem"], "CHARACTER_Lina_Default_modeling_v001")
        self.assertEqual(r["wip"]["filename"], "CHARACTER_Lina_Default_modeling_v001.blend")
        self.assertTrue(r["wip"]["path"].endswith(
            "assets/CHARACTER_Lina_Default/modeling/wip/"
            "CHARACTER_Lina_Default_modeling_v001.blend"))

    def test_wip_version_increments_over_existing(self):
        # A starter never overwrites: with v001 already on disk, allocate v002.
        wip = self._asset_dir("CHARACTER_Lina_Default") / "modeling" / "wip"
        wip.mkdir(parents=True, exist_ok=True)
        (wip / "CHARACTER_Lina_Default_modeling_v001.blend").write_bytes(b"blend")
        r = cp.scene_starter_spec("CHARACTER_Lina_Default", "modeling",
                                  project_root=str(self.project))
        self.assertEqual(r["wip"]["version"], 2)
        self.assertTrue(r["wip"]["stem"].endswith("_v002"))


class TestAssetLookdev(_BaseCase):
    """asset / lookdev -> references the built asset_root.usda, lighting hint on."""

    def test_references_asset_root_and_lighting(self):
        r = cp.scene_starter_spec("CHARACTER_Lina_Default", "lookdev",
                                  project_root=str(self.project))
        self.assertTrue(r["ok"])
        self.assertTrue(r["lighting"])
        self.assertFalse(r["camera"])
        self.assertEqual(len(r["references"]), 1)
        ref = r["references"][0]
        self.assertEqual(ref["rel"], cp.ASSET_ROOT_NAME)
        self.assertEqual(ref["role"], "assembly")
        self.assertTrue(ref["path"].endswith(cp.ASSET_ROOT_NAME))
        self.assertTrue(Path(ref["path"]).is_file())  # stub written by create_asset


class TestShotAnimation(_BaseCase):
    """shot / animation -> frame_range from the manifest, camera created, assembly ref
    only once shot_root.usda exists on disk."""

    def test_frame_range_and_camera(self):
        r = cp.scene_starter_spec("ANIMATION_Sq010_Default", "animation",
                                  project_root=str(self.project))
        self.assertTrue(r["ok"])
        self.assertEqual(r["family"], "shot")
        self.assertTrue(r["camera"])
        # Default shot frame_range (schema 2.1: 1001-1100).
        self.assertEqual(r["frame_range"]["start"], 1001)
        self.assertEqual(r["frame_range"]["end"], 1100)
        self.assertEqual(r["context"]["context_type"], "SHOT")

    def test_no_assembly_reference_before_shot_root_exists(self):
        # A fresh shot has no shot_root.usda yet (it appears after the first step publish):
        # the starter references nothing rather than an absent file.
        r = cp.scene_starter_spec("ANIMATION_Sq010_Default", "animation",
                                  project_root=str(self.project))
        self.assertEqual(r["references"], [])

    def test_assembly_reference_after_shot_root_written(self):
        (self._shot_dir("ANIMATION_Sq010_Default") / cp.SHOT_ROOT_NAME).write_text(
            "#usda 1.0\n", encoding="utf-8")
        r = cp.scene_starter_spec("ANIMATION_Sq010_Default", "animation",
                                  project_root=str(self.project))
        self.assertEqual(len(r["references"]), 1)
        self.assertEqual(r["references"][0]["rel"], cp.SHOT_ROOT_NAME)


if __name__ == "__main__":
    unittest.main()
