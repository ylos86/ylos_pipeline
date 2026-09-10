#!/usr/bin/env python3
"""
tests/test_playblast_spec.py — stdlib tests (unittest) for create_project.playblast_spec().

The playblast spec is the pure, serializable allocation of a review-media output path in
the source tree; the DCC renders the viewport into it. These lock the versioning
(non-destructive), the source-tree location (browser-servable), the shot frame_range
pass-through, and the never-raises business contract.

Usage: python3 tests/test_playblast_spec.py
    or: python3 -m unittest tests.test_playblast_spec
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
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ylos_pblast_")).resolve()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        info = cp.create("Proj", root=self._tmp / "src", cache=self._tmp / "cache")
        self.project = Path(info["source"])
        cp.create_asset(self.project, "CHARACTER_Lina_Default",
                        entity_type="asset", asset_type="CHARACTER")
        cp.create_asset(self.project, "ANIMATION_Sq010_Default",
                        entity_type="shot", asset_type="ANIMATION")


class TestBusinessContract(_BaseCase):
    def test_missing_project_root(self):
        r = cp.playblast_spec("CHARACTER_Lina_Default", "modeling")
        self.assertFalse(r["ok"])

    def test_unknown_entity(self):
        r = cp.playblast_spec("NOPE_Absent_Default", "modeling",
                              project_root=str(self.project))
        self.assertFalse(r["ok"])
        self.assertIn("not found", r["reason"])

    def test_step_not_declared(self):
        r = cp.playblast_spec("CHARACTER_Lina_Default", "compositing",
                              project_root=str(self.project))
        self.assertFalse(r["ok"])
        self.assertIn("not declared", r["reason"])

    def test_json_serializable(self):
        r = cp.playblast_spec("CHARACTER_Lina_Default", "modeling",
                              project_root=str(self.project))
        self.assertTrue(r["ok"])
        json.dumps(r)


class TestAllocation(_BaseCase):
    def test_first_version_in_source_tree(self):
        r = cp.playblast_spec("CHARACTER_Lina_Default", "modeling",
                              project_root=str(self.project))
        self.assertEqual(r["version"], 1)
        self.assertEqual(r["stem"], "CHARACTER_Lina_Default_modeling_v001")
        self.assertTrue(r["sequence"])
        self.assertEqual(r["ext"], "png")
        self.assertEqual(r["frame_prefix"], "CHARACTER_Lina_Default_modeling_v001")
        # path is the per-version sequence FOLDER inside the source tree.
        self.assertTrue(r["path"].endswith(
            "assets/CHARACTER_Lina_Default/modeling/playblast/"
            "CHARACTER_Lina_Default_modeling_v001"))
        self.assertIsNone(r["frame_range"])

    def test_version_increments_over_existing_folders(self):
        pdir = (self.project / "assets" / "CHARACTER_Lina_Default" / "modeling" / "playblast")
        (pdir / "CHARACTER_Lina_Default_modeling_v001").mkdir(parents=True, exist_ok=True)
        (pdir / "CHARACTER_Lina_Default_modeling_v002").mkdir(parents=True, exist_ok=True)
        r = cp.playblast_spec("CHARACTER_Lina_Default", "modeling",
                              project_root=str(self.project))
        self.assertEqual(r["version"], 3)

    def test_shot_carries_frame_range(self):
        r = cp.playblast_spec("ANIMATION_Sq010_Default", "animation",
                              project_root=str(self.project))
        self.assertEqual(r["family"], "shot")
        self.assertEqual(r["frame_range"]["start"], 1001)
        self.assertEqual(r["frame_range"]["end"], 1100)


if __name__ == "__main__":
    unittest.main()
