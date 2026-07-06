#!/usr/bin/env python3
"""
tests/test_migrate_to_2_0.py — tests stdlib (unittest) pour migrate_to_2.0.py.

Couvre le renommage a la convention TYPE_Nom_Variant pendant la migration : sans lui,
une entite legacy migre mais tout publish LOP echoue a validate_publish_asset_name —
un mur silencieux. Verifie aussi le chemin 'type invalide' (warning actionnable, pas de
renommage), le dry-run, et la publiabilite LOP effective post-migration (end-to-end).

Usage : python3 tests/test_migrate_to_2_0.py
     ou : python3 -m unittest tests.test_migrate_to_2_0
"""
from __future__ import annotations

import importlib.util
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

# 'migrate_to_2.0.py' contient un point -> non importable par nom, chargement explicite.
_spec = importlib.util.spec_from_file_location("migrate_to_2_0", _REPO_ROOT / "migrate_to_2.0.py")
mig = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mig)


def _write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


class LegacyProjectTestCase(unittest.TestCase):
    """Projet legacy synthetique (pre-schema_version) reconstruit pour chaque test."""

    def setUp(self):
        # .resolve() : migrate() resout le chemin projet (macOS : /var -> /private/var),
        # les chemins du rapport doivent se comparer a la meme forme resolue.
        self._tmp = Path(tempfile.mkdtemp(prefix="ylos_mig_test_")).resolve()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.project = self._tmp / "LegacyProj"
        _write_json(self.project / "_pipeline" / "project.json", {
            "project": {"name": "LegacyProj", "created": "2025-05-01"},
            "pipeline": {},
        })

    def _add_legacy_entity(self, name, legacy_type="asset", entity_type="asset",
                           steps=("modeling",), with_publish=True):
        family = {"asset": "assets", "set": "sets", "shot": "shots"}[entity_type]
        entity = self.project / family / name
        for step in steps:
            (entity / step / "wip").mkdir(parents=True, exist_ok=True)
            (entity / step / "publish").mkdir(parents=True, exist_ok=True)
            if with_publish:
                (entity / step / "publish" / f"{name}_{step}_v001.usda").write_text(
                    "#usda 1.0\n", encoding="utf-8")
                (entity / step / "wip" / f"{name}_{step}_v001.blend").write_bytes(b"BLENDER")
        _write_json(entity / "manifest.json", {
            "name": name, "entity_type": entity_type, "type": legacy_type,
            "steps": list(steps),
        })
        (entity / "asset_root.usd").write_text("#usda 1.0\n", encoding="utf-8")
        return entity


class TestRenameToConvention(LegacyProjectTestCase):
    """Le coeur du fix : 'lecube' (type legacy 'asset', override PROP) devient
    PROP_Lecube_Default, publiable."""

    def setUp(self):
        super().setUp()
        self._add_legacy_entity("lecube")
        self.report = mig.migrate(self.project, type_overrides={"lecube": "PROP"})
        self.new_dir = self.project / "assets" / "PROP_Lecube_Default"

    def test_entity_dir_renamed(self):
        self.assertTrue(self.new_dir.is_dir())
        self.assertFalse((self.project / "assets" / "lecube").exists())

    def test_manifest_name_and_type_conform(self):
        manifest = json.loads((self.new_dir / "manifest.json").read_text())
        self.assertEqual(manifest["name"], "PROP_Lecube_Default")
        self.assertEqual(manifest["type"], "PROP")
        self.assertEqual(manifest["schema_version"], cp.SCHEMA_VERSION)
        # La garantie du contrat : le nom migre passe la validation de creation.
        self.assertTrue(cp.validate_entity_name("PROP_Lecube_Default", "asset", "PROP"))

    def test_publish_stems_renamed_wip_untouched(self):
        pub = self.new_dir / "modeling" / "publish"
        self.assertTrue((pub / "PROP_Lecube_Default_modeling_v001.usda").is_file())
        self.assertFalse((pub / "lecube_modeling_v001.usda").exists())
        # wip/ jamais touche : la detection de version Blender est agnostique au nom.
        self.assertTrue((self.new_dir / "modeling" / "wip" / "lecube_modeling_v001.blend").is_file())

    def test_manifest_publishes_and_asset_root_point_to_renamed_files(self):
        manifest = json.loads((self.new_dir / "manifest.json").read_text())
        self.assertEqual(manifest["publishes"]["modeling"],
                         ["modeling/publish/PROP_Lecube_Default_modeling_v001.usda"])
        content = (self.new_dir / cp.ASSET_ROOT_NAME).read_text()
        self.assertIn('defaultPrim = "PROP_Lecube_Default"', content)
        self.assertIn("@modeling/publish/PROP_Lecube_Default_modeling_v001.usda@", content)
        self.assertFalse((self.new_dir / "asset_root.usd").exists())

    def test_report_traces_rename(self):
        entity = next(e for e in self.report["entities"] if e["name"] == "PROP_Lecube_Default")
        self.assertEqual(entity["renamed_from"], "lecube")
        renamed_paths = [r["to"] for r in self.report["renames"]]
        self.assertIn(str(self.new_dir), renamed_paths)

    def test_migrated_asset_is_lop_publishable(self):
        # Le test qui prouve que le mur est tombe : allocate LOP passe la validation.
        staging, final = cp.allocate_publish_version(
            self.project, "PROP_Lecube_Default", "PROP", comment="post-migration")
        self.assertTrue(staging.is_dir())
        self.assertEqual(final.parent.parent.name, cp.LOP_DIR_NAME)


class TestInvalidTypeIsLoud(LegacyProjectTestCase):
    """Type invalide pour la famille (ex ENVIRONMENT) : jamais un mur silencieux."""

    def test_warning_actionable_and_no_rename(self):
        self._add_legacy_entity("montains")
        report = mig.migrate(self.project)  # defauts : montains -> ENVIRONMENT (invalide)
        self.assertTrue((self.project / "assets" / "montains").is_dir())
        warning = next(w for w in report["warnings"] if "montains" in w)
        self.assertIn("ENVIRONMENT", warning)
        self.assertIn("--type-override", warning)
        # L'entite est quand meme migree (schema 2.0), juste pas renommee.
        manifest = json.loads(
            (self.project / "assets" / "montains" / "manifest.json").read_text())
        self.assertEqual(manifest["schema_version"], cp.SCHEMA_VERSION)

    def test_type_override_resolves_it(self):
        self._add_legacy_entity("montains")
        report = mig.migrate(self.project,
                             type_overrides={"montains": "PROP"})
        self.assertTrue((self.project / "assets" / "PROP_Montains_Default").is_dir())
        self.assertFalse(any("montains" in w for w in report["warnings"]))


class TestRenameEdgeCases(LegacyProjectTestCase):

    def test_conforming_entity_untouched(self):
        self._add_legacy_entity("PROP_Tente_Default", legacy_type="PROP")
        report = mig.migrate(self.project, type_overrides={})
        self.assertTrue((self.project / "assets" / "PROP_Tente_Default").is_dir())
        entity = report["entities"][0]
        self.assertNotIn("renamed_from", entity)
        # Ses publishes gardent leur stem (deja conforme).
        pub = self.project / "assets" / "PROP_Tente_Default" / "modeling" / "publish"
        self.assertTrue((pub / "PROP_Tente_Default_modeling_v001.usda").is_file())

    def test_collision_target_warns_and_skips(self):
        self._add_legacy_entity("lecube")
        (self.project / "assets" / "PROP_Lecube_Default").mkdir(parents=True)
        report = mig.migrate(self.project, type_overrides={"lecube": "PROP"})
        self.assertTrue((self.project / "assets" / "lecube").is_dir())
        self.assertTrue(any("cible de renommage" in w for w in report["warnings"]))

    def test_multi_segment_name_keeps_all_segments(self):
        self._add_legacy_entity("le_cube")
        mig.migrate(self.project, type_overrides={"le_cube": "PROP"})
        self.assertTrue((self.project / "assets" / "PROP_LeCube_Default").is_dir())

    def test_dry_run_touches_nothing_but_reports(self):
        self._add_legacy_entity("lecube")
        report = mig.migrate(self.project, dry=True, type_overrides={"lecube": "PROP"})
        entity_dir = self.project / "assets" / "lecube"
        self.assertTrue(entity_dir.is_dir())
        self.assertTrue((entity_dir / "modeling" / "publish" / "lecube_modeling_v001.usda").is_file())
        # Manifeste non reecrit (toujours legacy, sans schema_version).
        manifest = json.loads((entity_dir / "manifest.json").read_text())
        self.assertNotIn("schema_version", manifest)
        # Mais le rapport annonce le renommage projete.
        planned = [r["to"] for r in report["renames"]]
        self.assertIn(str(self.project / "assets" / "PROP_Lecube_Default"), planned)


if __name__ == "__main__":
    unittest.main()
