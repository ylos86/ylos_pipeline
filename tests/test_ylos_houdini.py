#!/usr/bin/env python3
"""
tests/test_ylos_houdini.py — tests stdlib (unittest) pour le bridge Houdini
plugins/houdini/python/ylos_houdini.py.

Contrainte CI (cf. docstring du module et CLAUDE.md) : le bridge est importable SANS hou
ni licence Houdini. On ne teste ici QUE les fonctions pures (hip_extension,
parse_wip_context, list_wip_versions, next_wip_path, list_entities, latest_lop_publish,
env_relative) - aucun appel a une action hou. Le simple import du module verrouille
l'absence d'`import hou` top-level (une machine CI n'a pas hou installe).

Le dossier plugins/houdini/python n'est pas un package -> import par chemin
(importlib.util, meme pattern que tests/test_migrate_to_2_0.py).

Usage : python3 tests/test_ylos_houdini.py
     ou : python3 -m unittest tests.test_ylos_houdini
"""
from __future__ import annotations

import importlib.util
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

# plugins/houdini/python n'est pas un package -> chargement explicite par chemin.
_MODULE_PATH = _REPO_ROOT / "plugins" / "houdini" / "python" / "ylos_houdini.py"
_spec = importlib.util.spec_from_file_location("ylos_houdini", _MODULE_PATH)
yh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(yh)  # leve si un `import hou` top-level existait -> garde CI


class ImportableWithoutHouTestCase(unittest.TestCase):
    """Le bridge doit se charger sans hou (contrainte CI) - le module s'est deja importe
    au niveau fichier ; on verrouille explicitement qu'aucun hou n'a ete tire."""

    def test_import_ne_tire_pas_hou(self):
        # exec_module ci-dessus a reussi ; s'il avait fait `import hou` top-level, il aurait
        # leve ModuleNotFoundError sur une machine sans Houdini (la CI).
        self.assertFalse("hou" in sys.modules,
                         "ylos_houdini ne doit jamais importer hou au niveau module")
        self.assertTrue(hasattr(yh, "hip_extension"))


class HipExtensionTestCase(unittest.TestCase):
    """Extension de save selon la licence, injectee (jamais lue de hou dans les tests)."""

    def test_commercial(self):
        self.assertEqual(yh.hip_extension("Commercial"), ".hip")

    def test_indie(self):
        self.assertEqual(yh.hip_extension("Indie"), ".hiplc")

    def test_apprentice_et_inconnu_tombent_sur_hipnc(self):
        self.assertEqual(yh.hip_extension("Apprentice"), ".hipnc")
        self.assertEqual(yh.hip_extension("Education"), ".hipnc")
        self.assertEqual(yh.hip_extension("QuelqueChoseInconnu"), ".hipnc")


class EnvRelativeTestCase(unittest.TestCase):
    """$PROJ_ROOT/<relatif> quand le chemin vit sous $PROJ_ROOT, absolu sinon."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ylos_hou_env_")).resolve()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._saved_env = os.environ.get(cp.ENV_ROOT)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        if self._saved_env is None:
            os.environ.pop(cp.ENV_ROOT, None)
        else:
            os.environ[cp.ENV_ROOT] = self._saved_env

    def test_sous_proj_root_devient_variable(self):
        os.environ[cp.ENV_ROOT] = str(self._tmp)
        target = self._tmp / "assets" / "CHARACTER_Lina_Default" / "asset_root.usda"
        self.assertEqual(yh.env_relative(target),
                         "$PROJ_ROOT/assets/CHARACTER_Lina_Default/asset_root.usda")

    def test_sans_proj_root_reste_absolu(self):
        os.environ.pop(cp.ENV_ROOT, None)
        target = self._tmp / "x.usda"
        self.assertEqual(yh.env_relative(target), str(target))

    def test_hors_proj_root_reste_absolu(self):
        os.environ[cp.ENV_ROOT] = str(self._tmp / "somewhere")
        other = self._tmp / "ailleurs" / "y.usda"
        self.assertEqual(yh.env_relative(other), str(other))


class RealProjectTestCase(unittest.TestCase):
    """Projet + entites reels (create()/create_asset()) - pour tout ce qui lit un
    project.json / manifest.json sur disque : parse_wip_context, list_wip_versions,
    next_wip_path, list_entities, latest_lop_publish."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ylos_hou_proj_")).resolve()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        info = cp.create("Proj", root=self._tmp / "src", cache=self._tmp / "cache")
        self.project = Path(info["source"])
        # un asset (steps modeling/rigging/lookdev/fx) et un shot (animation/fx/lighting/comp)
        cp.create_asset(self.project, "CHARACTER_Lina_Default",
                        entity_type="asset", asset_type="CHARACTER")
        cp.create_asset(self.project, "ANIMATION_Sq010_Default",
                        entity_type="shot", asset_type="ANIMATION")

    # -- parse_wip_context --------------------------------------------------------------

    def test_parse_wip_context_chemin_conforme(self):
        hip = (self.project / "shots" / "ANIMATION_Sq010_Default" / "animation" / "wip"
               / "ANIMATION_Sq010_Default_animation_v001.hipnc")
        ctx = yh.parse_wip_context(hip)
        self.assertIsNotNone(ctx)
        project_root, entity_name, step = ctx
        self.assertEqual(Path(project_root), self.project)
        self.assertEqual(entity_name, "ANIMATION_Sq010_Default")
        self.assertEqual(step, "animation")

    def test_parse_wip_context_hors_projet(self):
        # bonne forme lexicale mais aucun _pipeline/project.json a la racine deduite.
        hip = (self._tmp / "assets" / "CHARACTER_Lina_Default" / "modeling" / "wip"
               / "x_v001.hipnc")
        self.assertIsNone(yh.parse_wip_context(hip))

    def test_parse_wip_context_famille_inconnue(self):
        hip = (self.project / "foobar" / "CHARACTER_Lina_Default" / "modeling" / "wip"
               / "x_v001.hipnc")
        self.assertIsNone(yh.parse_wip_context(hip))

    def test_parse_wip_context_pas_dans_wip(self):
        hip = (self.project / "assets" / "CHARACTER_Lina_Default" / "modeling"
               / "publish" / "x_v001.hipnc")
        self.assertIsNone(yh.parse_wip_context(hip))

    # -- list_wip_versions / next_wip_path ----------------------------------------------

    def _wip_dir(self, family, entity, step):
        d = self.project / family / entity / step / "wip"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_list_wip_versions_extensions_melangees(self):
        wip = self._wip_dir("assets", "CHARACTER_Lina_Default", "modeling")
        (wip / "CHARACTER_Lina_Default_modeling_v001.hip").write_text("", encoding="utf-8")
        (wip / "CHARACTER_Lina_Default_modeling_v002.hiplc").write_text("", encoding="utf-8")
        (wip / "CHARACTER_Lina_Default_modeling_v003.hipnc").write_text("", encoding="utf-8")
        (wip / "notes.txt").write_text("", encoding="utf-8")  # ignore (pas de _vNNN.hip*)
        versions = yh.list_wip_versions(self.project, "CHARACTER_Lina_Default", "modeling")
        self.assertEqual([v["version"] for v in versions], [1, 2, 3])

    def test_list_wip_versions_vide(self):
        self.assertEqual(
            yh.list_wip_versions(self.project, "CHARACTER_Lina_Default", "modeling"), [])

    def test_next_wip_path_incremente_max_disque(self):
        wip = self._wip_dir("assets", "CHARACTER_Lina_Default", "modeling")
        (wip / "CHARACTER_Lina_Default_modeling_v001.hip").write_text("", encoding="utf-8")
        (wip / "CHARACTER_Lina_Default_modeling_v002.hipnc").write_text("", encoding="utf-8")
        path, version = yh.next_wip_path(self.project, "CHARACTER_Lina_Default", "modeling",
                                         license_category="Commercial")
        self.assertEqual(version, 3)
        self.assertEqual(Path(path).name, "CHARACTER_Lina_Default_modeling_v003.hip")
        self.assertEqual(Path(path).parent, wip)

    def test_next_wip_path_premiere_version(self):
        _, version = yh.next_wip_path(self.project, "ANIMATION_Sq010_Default", "animation",
                                      license_category="Apprentice")
        self.assertEqual(version, 1)

    def test_next_wip_path_step_invalide(self):
        with self.assertRaises(ValueError):
            yh.next_wip_path(self.project, "CHARACTER_Lina_Default", "step_bidon",
                             license_category="Commercial")

    # -- list_entities ------------------------------------------------------------------

    def test_list_entities(self):
        ents = {e["name"]: e for e in yh.list_entities(self.project)}
        self.assertIn("CHARACTER_Lina_Default", ents)
        self.assertIn("ANIMATION_Sq010_Default", ents)
        self.assertEqual(ents["CHARACTER_Lina_Default"]["family"], "assets")
        self.assertEqual(ents["CHARACTER_Lina_Default"]["type"], "CHARACTER")
        self.assertEqual(ents["ANIMATION_Sq010_Default"]["family"], "shots")
        self.assertIn("animation", ents["ANIMATION_Sq010_Default"]["steps"])

    # -- latest_lop_publish -------------------------------------------------------------

    def _set_lop_publishes(self, entity, entries):
        manifest_path = (self.project / "assets" / entity / cp.ASSET_MANIFEST_NAME)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest[cp.LOP_PUBLISHES_KEY] = entries
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def test_latest_lop_publish_ignore_pending(self):
        self._set_lop_publishes("CHARACTER_Lina_Default", [
            {"version": 1, "status": "complete", "layer": "lop/publish/v001/lina_v001.usdnc"},
            {"version": 2, "status": "complete", "layer": "lop/publish/v002/lina_v002.usdnc"},
            {"version": 3, "status": "pending", "layer": "lop/publish/v003/lina_v003.usdnc"},
        ])
        got = yh.latest_lop_publish(self.project, "CHARACTER_Lina_Default")
        expected = (self.project / "assets" / "CHARACTER_Lina_Default"
                    / "lop/publish/v002/lina_v002.usdnc")
        self.assertEqual(Path(got), expected)

    def test_latest_lop_publish_aucun(self):
        self.assertIsNone(
            yh.latest_lop_publish(self.project, "CHARACTER_Lina_Default"))

    # -- latest_step_publish ------------------------------------------------------------

    def _set_step_publishes(self, family, entity, step_publishes):
        manifest_path = (self.project / family / entity / cp.ASSET_MANIFEST_NAME)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest[cp.STEP_PUBLISHES_KEY] = step_publishes
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def test_latest_step_publish_ignore_pending(self):
        self._set_step_publishes("shots", "ANIMATION_Sq010_Default", {
            "animation": [
                {"version": 1, "status": "complete",
                 "artifact": "animation/publish/v001/anim_v001.usdnc"},
                {"version": 2, "status": "complete",
                 "artifact": "animation/publish/v002/anim_v002.usdnc"},
                {"version": 3, "status": "pending",
                 "artifact": "animation/publish/v003/anim_v003.usdnc"},
            ],
        })
        got = yh.latest_step_publish(self.project, "ANIMATION_Sq010_Default", "animation")
        expected = (self.project / "shots" / "ANIMATION_Sq010_Default"
                    / "animation/publish/v002/anim_v002.usdnc")
        self.assertEqual(Path(got), expected)

    def test_latest_step_publish_aucun_et_step_absent(self):
        # step jamais publie -> None
        self.assertIsNone(
            yh.latest_step_publish(self.project, "ANIMATION_Sq010_Default", "animation"))
        # step present mais que des 'pending' -> None aussi
        self._set_step_publishes("shots", "ANIMATION_Sq010_Default", {
            "lighting": [
                {"version": 1, "status": "pending",
                 "artifact": "lighting/publish/v001/light_v001.usdnc"},
            ],
        })
        self.assertIsNone(
            yh.latest_step_publish(self.project, "ANIMATION_Sq010_Default", "lighting"))

    # -- shot_root_path -----------------------------------------------------------------

    def test_shot_root_path_absent_leve(self):
        with self.assertRaises(FileNotFoundError):
            yh.shot_root_path(self.project, "ANIMATION_Sq010_Default")

    def test_shot_root_path_present(self):
        shot_dir = self.project / "shots" / "ANIMATION_Sq010_Default"
        root = shot_dir / cp.SHOT_ROOT_NAME
        root.write_text("#usda 1.0\n", encoding="utf-8")
        self.assertEqual(
            Path(yh.shot_root_path(self.project, "ANIMATION_Sq010_Default")), root)


if __name__ == "__main__":
    unittest.main()
