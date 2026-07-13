# -*- coding: utf-8 -*-
"""Tests stdlib de create_project.resolve_open_target().

Place dans tests/ (et non tools/tests/ comme suggere par la tache) pour etre pris par la
CI : `python -m unittest discover -s tests`, comme toutes les autres suites stdlib du repo.
Contrat verifie : resolve_open_target NE LEVE JAMAIS pour un cas metier (projet/entite/step
introuvable, valeur d'enum inconnue au manifeste) - il renvoie un dict exists=False + raison.
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
        # prod_type inconnu injecte dans project.json (valeur legacy/reelle type 'XR') :
        # resolve ne lit pas ce champ, ca ne doit rien casser.
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
        self.assertEqual(t["step"], "modeling")  # premier step declare

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
        # prod_type ET type inconnus dans le manifeste d'entite : resolve resout quand meme.
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
        # manifeste illisible -> dict vide en interne, fallback sur scene_default si le root
        # existe (il existe apres create_asset), jamais d'exception.
        (self.entity_dir / "manifest.json").write_text("{ not json", encoding="utf-8")
        t = cp.resolve_open_target("PROP_Box_Default", "blender", project_root=str(self.proj))
        # entity_type defaut 'asset' -> asset_root.usda existe
        self.assertTrue(t["exists"])
        self.assertEqual(t["kind"], "scene_default")


if __name__ == "__main__":
    unittest.main(verbosity=2)
