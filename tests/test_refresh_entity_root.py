#!/usr/bin/env python3
"""
tests/test_refresh_entity_root.py — tests stdlib (unittest) pour le composeur unifie
create_project.refresh_entity_root() (Increment 1 du plan Houdini shots).

Un seul composeur, dans create_project.py (principe 5) : asset/set -> asset_root.usda
(ordre DOWNSTREAM_ORDER), shot -> shot_root.usda (root prim /ROOT, ordre
SHOT_DOWNSTREAM_ORDER, timecodes depuis frame_range si present). Recompose automatiquement
a la fin de finalize_publish_version() pour kind != 'lop' (un LOP ne compose jamais).

Usage : python3 tests/test_refresh_entity_root.py
     ou : python3 -m unittest tests.test_refresh_entity_root
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
    """Projet 2.0 + un asset (CHARACTER) et un shot (ANIMATION) dans un tmpdir jetable."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ylos_root_")).resolve()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        info = cp.create("proj", root=self._tmp / "root", cache=self._tmp / "cache")
        self.project = Path(info["source"])
        cp.create_asset(self.project, "CHARACTER_Lina_Default",
                        entity_type="asset", asset_type="CHARACTER")
        cp.create_asset(self.project, "ANIMATION_Sq010_Default",
                        entity_type="shot", asset_type="ANIMATION")

    def _publish_step(self, asset_name, step, ext="usda"):
        """allocate -> ecrit artefact USD + thumb -> finalize (kind=step). Retourne version."""
        staging, final = cp.allocate_publish_version(self.project, asset_name,
                                                     comment="", kind=step)
        version = cp.publish_version_from_dir(final)
        stem = f"{asset_name}_{step}_v{version:03d}"
        (staging / f"{stem}.{ext}").write_bytes(b"artifact")
        (staging / "thumb.png").write_bytes(b"png")
        cp.finalize_publish_version(self.project, asset_name, staging, final, version,
                                    expected_artifacts=[stem, "thumb.png"])
        return version

    def _asset_dir(self, name):
        return self.project / "assets" / name

    def _shot_dir(self, name):
        return self.project / "shots" / name

    def _read(self, path):
        return Path(path).read_text(encoding="utf-8")


class BuildRootPureTestCase(unittest.TestCase):
    """Fonctions de composition pures (ordre, root prim, timecodes)."""

    def test_asset_root_order_downstream(self):
        latest = {"modeling": "modeling/publish/m/m.usda",
                  "lookdev": "lookdev/publish/l/l.usda"}
        out = cp.build_asset_root("Lina", latest)
        self.assertIn('defaultPrim = "Lina"', out)
        # lookdev plus fort que modeling (DOWNSTREAM_ORDER) -> apparait en premier.
        self.assertLess(out.index("lookdev/publish"), out.index("modeling/publish"))

    def test_shot_root_prim_and_order(self):
        latest = {"animation": "animation/publish/a/a.usda",
                  "lighting": "lighting/publish/l/l.usda"}
        out = cp.build_shot_root("Sq010", latest)
        self.assertIn('defaultPrim = "ROOT"', out)
        self.assertIn('def Xform "ROOT"', out)
        # lighting override l'anim sur un shot (SHOT_DOWNSTREAM_ORDER) -> en premier.
        self.assertLess(out.index("lighting/publish"), out.index("animation/publish"))
        # pas de frame_range -> aucun timecode.
        self.assertNotIn("startTimeCode", out)

    def test_shot_root_timecodes_present(self):
        out = cp.build_shot_root("Sq010", {}, {"start": 1001, "end": 1100, "fps": 24})
        self.assertIn("startTimeCode = 1001", out)
        self.assertIn("endTimeCode = 1100", out)
        self.assertIn("timeCodesPerSecond = 24", out)  # 24, pas 24.0

    def test_latest_by_step_merges_and_prefers_two_phase(self):
        manifest = {
            "publishes": {"modeling": ["modeling/publish/Lina_modeling_v001.usda"]},
            "step_publishes": {
                "modeling": [
                    {"version": 1, "status": "complete",
                     "artifact": "modeling/publish/Lina_modeling_v001/Lina_modeling_v001.usda"},
                    {"version": 2, "status": "pending",
                     "artifact": "modeling/publish/Lina_modeling_v002/Lina_modeling_v002.usda"},
                ],
                "lookdev": [
                    {"version": 3, "status": "complete",
                     "artifact": "lookdev/publish/Lina_lookdev_v003/Lina_lookdev_v003.usda"},
                ],
            },
        }
        latest = cp._latest_by_step(manifest)
        # step_publishes 'complete' prime sur le 'publishes' legacy a step egal ; le pending
        # v002 est ignore (v001 gagne).
        self.assertEqual(latest["modeling"],
                         "modeling/publish/Lina_modeling_v001/Lina_modeling_v001.usda")
        self.assertEqual(latest["lookdev"],
                         "lookdev/publish/Lina_lookdev_v003/Lina_lookdev_v003.usda")

    def test_latest_by_step_ignores_lop(self):
        manifest = {"lop_publishes": [
            {"version": 1, "status": "complete", "layer": "lop/publish/v001/x.usdnc"}]}
        self.assertEqual(cp._latest_by_step(manifest), {})

    def test_latest_by_step_filters_non_usd_artifacts(self):
        # Increment 5 : un cache consommable (VDB/bgeo/abc) ou GLB publie en kind=step passe le
        # deux-phases mais n'entre JAMAIS en composition. Le filtre USD s'applique AVANT le max :
        # un step avec un VDB plus recent (v002) mais un USD plus ancien (v001) compose l'USD.
        manifest = {
            "step_publishes": {
                "fx": [
                    {"version": 1, "status": "complete",
                     "artifact": "fx/publish/S_fx_v001/S_fx_v001.usda"},
                    {"version": 2, "status": "complete",
                     "artifact": "fx/publish/S_fx_v002/S_fx_v002.vdb"},
                ],
                "modeling": [
                    {"version": 1, "status": "complete",
                     "artifact": "modeling/publish/S_modeling_v001/S_modeling_v001.glb"},
                ],
            },
        }
        latest = cp._latest_by_step(manifest)
        # fx : le VDB v002 est filtre, l'USD v001 compose.
        self.assertEqual(latest["fx"], "fx/publish/S_fx_v001/S_fx_v001.usda")
        # modeling : GLB seul -> aucun layer USD -> step absent de la composition.
        self.assertNotIn("modeling", latest)


class AssetRecompositionTestCase(_BaseCase):
    """finalize_publish_version(kind=step) recompose asset_root.usda."""

    def test_step_publish_refreshes_asset_root(self):
        self._publish_step("CHARACTER_Lina_Default", "modeling")
        self._publish_step("CHARACTER_Lina_Default", "lookdev")
        root = self._read(self._asset_dir("CHARACTER_Lina_Default") / cp.ASSET_ROOT_NAME)
        self.assertIn("CHARACTER_Lina_Default_modeling_v001", root)
        self.assertIn("CHARACTER_Lina_Default_lookdev_v001", root)
        # ordre downstream : lookdev avant modeling.
        self.assertLess(root.index("lookdev/publish"), root.index("modeling/publish"))
        # chemin de subLayer relatif a l'entite (le root vit a sa racine).
        self.assertIn(
            "@modeling/publish/CHARACTER_Lina_Default_modeling_v001/"
            "CHARACTER_Lina_Default_modeling_v001.usda@", root)

    def test_latest_per_step_only(self):
        self._publish_step("CHARACTER_Lina_Default", "modeling")  # v001
        self._publish_step("CHARACTER_Lina_Default", "modeling")  # v002
        root = self._read(self._asset_dir("CHARACTER_Lina_Default") / cp.ASSET_ROOT_NAME)
        self.assertIn("CHARACTER_Lina_Default_modeling_v002", root)
        self.assertNotIn("modeling_v001", root)


class ShotRecompositionTestCase(_BaseCase):
    """finalize_publish_version(kind=step) sur un shot recompose shot_root.usda."""

    def test_step_publish_creates_shot_root(self):
        self._publish_step("ANIMATION_Sq010_Default", "animation")
        self._publish_step("ANIMATION_Sq010_Default", "lighting")
        shot_root = self._shot_dir("ANIMATION_Sq010_Default") / cp.SHOT_ROOT_NAME
        self.assertTrue(shot_root.is_file())
        out = self._read(shot_root)
        self.assertIn('defaultPrim = "ROOT"', out)
        self.assertLess(out.index("lighting/publish"), out.index("animation/publish"))
        # depuis schema 2.1 (Increment 2) un shot nait avec un frame_range par defaut
        # (1001-1100) -> timecodes presents dans le shot_root recompose.
        self.assertIn("startTimeCode = 1001", out)
        self.assertIn("endTimeCode = 1100", out)

    def test_refresh_with_frame_range_writes_timecodes(self):
        # frame_range surcharge a la main (le defaut 2.1 pose 1001-1100@24) :
        # refresh_entity_root doit consommer la valeur presente au manifeste.
        manifest_path = self._shot_dir("ANIMATION_Sq010_Default") / cp.ASSET_MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["frame_range"] = {"start": 1010, "end": 1042, "fps": 25}
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        written = cp.refresh_entity_root(self.project, "ANIMATION_Sq010_Default")
        self.assertEqual(Path(written).name, cp.SHOT_ROOT_NAME)
        out = self._read(written)
        self.assertIn("startTimeCode = 1010", out)
        self.assertIn("endTimeCode = 1042", out)
        self.assertIn("timeCodesPerSecond = 25", out)


class LopLeavesRootIntactTestCase(_BaseCase):
    """Un publish kind='lop' ne recompose JAMAIS le root (hors taxonomie de steps)."""

    def test_lop_publish_does_not_touch_asset_root(self):
        asset_root = self._asset_dir("CHARACTER_Lina_Default") / cp.ASSET_ROOT_NAME
        before = self._read(asset_root)
        staging, final = cp.allocate_publish_version(
            self.project, "CHARACTER_Lina_Default", asset_type="CHARACTER",
            comment="", kind="lop")
        version = cp.publish_version_from_dir(final)
        stem = f"CHARACTER_Lina_Default_lop_v{version:03d}"
        (staging / f"{stem}.usda").write_bytes(b"x")
        (staging / cp.LOP_THUMB_NAME).write_bytes(b"x")
        cp.finalize_publish_version(self.project, "CHARACTER_Lina_Default", staging, final,
                                    version, expected_artifacts=[stem, cp.LOP_THUMB_NAME])
        # root inchange (stub d'origine, subLayers vides) : le LOP n'entre pas en composition.
        self.assertEqual(self._read(asset_root), before)


if __name__ == "__main__":
    unittest.main()
