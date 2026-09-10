#!/usr/bin/env python3
"""
tests/test_render_spec.py — stdlib tests (unittest) for create_project.render_spec().

The render spec allocates a versioned output in the CACHE render tier, exactly where the
Houdini bridge writes (ylos_houdini.render_dir) — so a Blender and a Houdini render of the
same shot/step land together and version consistently. These lock the tier location, the
non-destructive versioning, the shot frame_range pass-through, and the never-raises contract.

Usage: python3 tests/test_render_spec.py
    or: python3 -m unittest tests.test_render_spec
"""
from __future__ import annotations

import json
import os
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
        self._tmp = Path(tempfile.mkdtemp(prefix="ylos_render_")).resolve()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        # $PROJ_CACHE drives resolve_cache(): point it at the test cache (as prod does).
        self._saved_cache = os.environ.get(cp.ENV_CACHE)
        self.addCleanup(self._restore_cache)
        os.environ[cp.ENV_CACHE] = str(self._tmp / "cache")
        info = cp.create("Proj", root=self._tmp / "src", cache=self._tmp / "cache")
        self.project = Path(info["source"])
        cp.create_asset(self.project, "ANIMATION_Sq010_Default",
                        entity_type="shot", asset_type="ANIMATION")
        cp.create_asset(self.project, "CHARACTER_Lina_Default",
                        entity_type="asset", asset_type="CHARACTER")

    def _restore_cache(self):
        if self._saved_cache is None:
            os.environ.pop(cp.ENV_CACHE, None)
        else:
            os.environ[cp.ENV_CACHE] = self._saved_cache

    def _render_vdir(self, entity, step, version):
        return (Path(self._tmp) / "cache" / "Proj" / "render" / entity / step
                / f"v{version:03d}")


class TestBusinessContract(_BaseCase):
    def test_missing_project_root(self):
        self.assertFalse(cp.render_spec("ANIMATION_Sq010_Default", "lighting")["ok"])

    def test_unknown_entity(self):
        r = cp.render_spec("NOPE_Absent_Default", "lighting", project_root=str(self.project))
        self.assertFalse(r["ok"])
        self.assertIn("not found", r["reason"])

    def test_step_not_declared(self):
        r = cp.render_spec("ANIMATION_Sq010_Default", "modeling",
                           project_root=str(self.project))
        self.assertFalse(r["ok"])
        self.assertIn("not declared", r["reason"])

    def test_json_serializable(self):
        r = cp.render_spec("ANIMATION_Sq010_Default", "lighting",
                           project_root=str(self.project))
        self.assertTrue(r["ok"])
        json.dumps(r)


class TestAllocation(_BaseCase):
    def test_first_version_in_cache_render_tier(self):
        r = cp.render_spec("ANIMATION_Sq010_Default", "lighting",
                           project_root=str(self.project))
        self.assertTrue(r["ok"])
        self.assertEqual(r["version"], 1)
        self.assertEqual(r["stem"], "ANIMATION_Sq010_Default_lighting_v001")
        self.assertEqual(r["ext"], "exr")
        # Same tier as the Houdini bridge: cache/<project>/render/<entity>/<step>/vNNN.
        norm = r["version_dir"].replace("\\", "/")
        self.assertTrue(norm.endswith("cache/Proj/render/ANIMATION_Sq010_Default/lighting/v001"))
        self.assertTrue(r["output_prefix"].endswith(
            "ANIMATION_Sq010_Default_lighting_v001."))
        # Shot -> frame_range carried through.
        self.assertEqual(r["frame_range"]["start"], 1001)
        self.assertEqual(r["frame_range"]["end"], 1100)

    def test_asset_has_no_frame_range(self):
        r = cp.render_spec("CHARACTER_Lina_Default", "lookdev",
                           project_root=str(self.project))
        self.assertIsNone(r["frame_range"])

    def test_version_increments_over_existing(self):
        self._render_vdir("ANIMATION_Sq010_Default", "lighting", 1).mkdir(parents=True)
        self._render_vdir("ANIMATION_Sq010_Default", "lighting", 2).mkdir(parents=True)
        r = cp.render_spec("ANIMATION_Sq010_Default", "lighting",
                           project_root=str(self.project))
        self.assertEqual(r["version"], 3)

    def test_matches_houdini_render_dir(self):
        # The Blender render tier must be byte-identical to the Houdini bridge's, so both
        # DCCs render the same shot/step side by side. Load the bridge without hou.
        import importlib.util
        mp = _REPO_ROOT / "plugins" / "houdini" / "python" / "ylos_houdini.py"
        spec = importlib.util.spec_from_file_location("ylos_houdini", mp)
        yh = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(yh)
        hou_dir = yh.render_dir(str(self.project), "ANIMATION_Sq010_Default", "lighting")
        r = cp.render_spec("ANIMATION_Sq010_Default", "lighting",
                           project_root=str(self.project))
        self.assertEqual(Path(r["dir"]).resolve(), Path(hou_dir).resolve())


if __name__ == "__main__":
    unittest.main()
