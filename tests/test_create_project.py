#!/usr/bin/env python3
"""
tests/test_create_project.py — tests stdlib (unittest) pour create_project.py.

Couvre : Volet 0 (validation de nommage a la creation), Volet 0bis (ecritures
atomiques), Volet 1 (contrat deux-phases generalise, kind=lop|step, non-regression
LOP), Volet 3 (sync_web_assets).

Usage : python3 tests/test_create_project.py
     ou : python3 -m unittest tests.test_create_project
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


class TempProjectTestCase(unittest.TestCase):
    """Cree un projet 2.0 vide dans un tmpdir jetable pour chaque test."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="ylos_test_")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        info = cp.create("proj", root=self._tmp + "/root", cache=self._tmp + "/cache")
        self.project = info["source"]

    def _publish_step(self, asset_name, step, ext="usd", version_hint=None):
        """Helper : allocate -> ecrit artefact+thumb -> finalize. Retourne (version, info)."""
        staging, final = cp.allocate_publish_version(self.project, asset_name, comment="", kind=step)
        version = cp.publish_version_from_dir(final)
        stem = f"{asset_name}_{step}_v{version:03d}"
        (staging / f"{stem}.{ext}").write_bytes(b"artifact")
        (staging / "thumb.png").write_bytes(b"png")
        info = cp.finalize_publish_version(
            self.project, asset_name, staging, final, version,
            expected_artifacts=[stem, "thumb.png"],
        )
        return version, info


class TestNamingValidation(TempProjectTestCase):
    """Volet 0 : create_asset() valide le nommage a la creation (point unique)."""

    def test_asset_bad_name_raises_with_suggestion(self):
        with self.assertRaises(ValueError) as ctx:
            cp.create_asset(self.project, "tente", asset_type="PROP")
        self.assertIn("PROP_Tente_Default", str(ctx.exception))

    def test_asset_conforming_name_passes(self):
        info = cp.create_asset(self.project, "PROP_Tente_Default", asset_type="PROP")
        self.assertEqual(info["name"], "PROP_Tente_Default")
        self.assertTrue(Path(info["manifest"]).is_file())

    def test_invalid_asset_type_lists_valid_types_no_suggestion(self):
        with self.assertRaises(ValueError) as ctx:
            cp.create_asset(self.project, "Tente", asset_type="FURNITURE")
        msg = str(ctx.exception)
        self.assertIn("CHARACTER", msg)
        self.assertNotIn("suggestion", msg)

    def test_set_naming_convention_enforced(self):
        with self.assertRaises(ValueError):
            cp.create_asset(self.project, "BadSet", entity_type="set", asset_type="EXTERIOR")
        info = cp.create_asset(
            self.project, "EXTERIOR_Backlot_Default", entity_type="set", asset_type="EXTERIOR",
        )
        self.assertEqual(info["entity_type"], "set")

    def test_shot_naming_convention_enforced(self):
        with self.assertRaises(ValueError):
            cp.create_asset(self.project, "BadShot", entity_type="shot", asset_type="LAYOUT")
        info = cp.create_asset(
            self.project, "LAYOUT_SQ010_SH0010", entity_type="shot", asset_type="LAYOUT",
        )
        self.assertEqual(info["entity_type"], "shot")

    def test_validate_publish_asset_name_alias_unchanged(self):
        # Contrat historique (Houdini HDA) : doit rester utilisable tel quel.
        self.assertTrue(cp.validate_publish_asset_name("CHARACTER_Lina_Default", "CHARACTER"))
        with self.assertRaises(ValueError):
            cp.validate_publish_asset_name("Lina", "CHARACTER")


class TestAtomicWrite(unittest.TestCase):
    """Volet 0bis : _atomic_write_text/_atomic_write_json ne corrompent jamais la cible."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="ylos_test_")
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def test_no_tmp_leftover_on_success(self):
        target = Path(self._tmp) / "manifest.json"
        cp._atomic_write_json(target, {"a": 1})
        self.assertEqual(json.loads(target.read_text()), {"a": 1})
        self.assertFalse(target.with_name(target.name + ".tmp").exists())

    def test_target_preserved_on_write_failure(self):
        target = Path(self._tmp) / "manifest.json"
        target.write_text('{"ok": true}')

        orig_write_text = Path.write_text

        def boom(self_path, *a, **kw):
            if str(self_path).endswith(".tmp"):
                raise RuntimeError("simulated crash mid-write")
            return orig_write_text(self_path, *a, **kw)

        Path.write_text = boom
        try:
            with self.assertRaises(RuntimeError):
                cp._atomic_write_json(target, {"broken": True})
        finally:
            Path.write_text = orig_write_text

        self.assertEqual(target.read_text(), '{"ok": true}')
        self.assertFalse(target.with_name(target.name + ".tmp").exists())


class TestTwoPhasePublish(TempProjectTestCase):
    """Volet 1 : allocate_publish_version/finalize_publish_version generalises (kind)."""

    def setUp(self):
        super().setUp()
        cp.create_asset(self.project, "PROP_Tente_Default", asset_type="PROP")

    def test_step_publish_populates_step_publishes(self):
        version, info = self._publish_step("PROP_Tente_Default", "modeling", ext="usd")
        manifest = json.loads(
            (Path(self.project) / "assets" / "PROP_Tente_Default" / "manifest.json").read_text()
        )
        entries = manifest["step_publishes"]["modeling"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["status"], "complete")
        self.assertTrue(entry["artifact"].endswith(".usd"))
        self.assertTrue(entry["thumb"].endswith("thumb.png"))
        self.assertNotIn("layer", entry)  # 'layer' reserve a kind='lop'

    def test_missing_thumb_rejected_staging_intact(self):
        staging, final = cp.allocate_publish_version(
            self.project, "PROP_Tente_Default", comment="", kind="modeling",
        )
        version = cp.publish_version_from_dir(final)
        stem = f"PROP_Tente_Default_modeling_v{version:03d}"
        (staging / f"{stem}.usd").write_bytes(b"x")
        # thumb.png volontairement absent
        with self.assertRaises(ValueError):
            cp.finalize_publish_version(
                self.project, "PROP_Tente_Default", staging, final, version,
                expected_artifacts=[stem, "thumb.png"],
            )
        self.assertTrue(staging.is_dir())
        self.assertFalse(final.exists())

    def test_lop_kind_unchanged_layer_key_and_manifest_key(self):
        staging, final = cp.allocate_publish_version(
            self.project, "PROP_Tente_Default", "PROP", comment="c",
        )
        self.assertEqual(final.parent.parent.name, cp.LOP_DIR_NAME)
        version = cp.publish_version_from_dir(final)
        stem = f"PROP_Tente_Default_lop_v{version:03d}"
        (staging / f"{stem}.usd").write_bytes(b"x")
        (staging / cp.LOP_THUMB_NAME).write_bytes(b"x")
        cp.finalize_publish_version(
            self.project, "PROP_Tente_Default", staging, final, version,
            expected_artifacts=[stem, cp.LOP_THUMB_NAME], comment="c",
        )
        manifest = json.loads(
            (Path(self.project) / "assets" / "PROP_Tente_Default" / "manifest.json").read_text()
        )
        entry = manifest[cp.LOP_PUBLISHES_KEY][0]
        self.assertIn("layer", entry)
        self.assertNotIn("artifact", entry)
        self.assertNotIn("step_publishes", manifest)

    def test_step_publish_works_for_set_entity(self):
        cp.create_asset(
            self.project, "EXTERIOR_Backlot_Default", entity_type="set", asset_type="EXTERIOR",
        )
        version, info = self._publish_step("EXTERIOR_Backlot_Default", "lookdev", ext="glb")
        self.assertEqual(version, 1)
        self.assertTrue(Path(info["final_dir"]).is_dir())


class TestSyncWebAssets(TempProjectTestCase):
    """Volet 3 : sync_web_assets — copie, assets.json, sha256, miroir."""

    def setUp(self):
        super().setUp()
        cp.create_asset(self.project, "PROP_Tente_Default", asset_type="PROP")
        cp.create_asset(self.project, "CHARACTER_Lina_Default", asset_type="CHARACTER")
        self._publish_step("PROP_Tente_Default", "lookdev", ext="glb")
        self._publish_step("PROP_Tente_Default", "lookdev", ext="glb")  # v2
        self._publish_step("CHARACTER_Lina_Default", "modeling", ext="glb")

        manifest = cp.read_manifest(self.project)
        manifest["web"] = {
            "target_dir": None,
            "pinned_assets": {
                "PROP_Tente_Default": {"step": "lookdev", "version": 2},
                "CHARACTER_Lina_Default": {"step": "modeling", "version": 1},
            },
        }
        cp.write_manifest(Path(self.project) / cp.PIPELINE_DIR, manifest)

        self.web_dir = tempfile.mkdtemp(prefix="ylos_web_")
        self.addCleanup(shutil.rmtree, self.web_dir, ignore_errors=True)
        self.assets_dir = Path(self.web_dir) / "public" / "assets"

    def test_sync_copies_pinned_and_writes_assets_json(self):
        result = cp.sync_web_assets(self.project, self.web_dir)
        self.assertEqual(result["warnings"], [])
        self.assertIn("PROP_Tente_Default", result["synced"])
        self.assertIn("CHARACTER_Lina_Default", result["synced"])

        f1 = self.assets_dir / "PROP_Tente_Default_v002.glb"
        f2 = self.assets_dir / "CHARACTER_Lina_Default_v001.glb"
        self.assertTrue(f1.is_file())
        self.assertTrue(f2.is_file())

        assets_json = json.loads((self.assets_dir / "assets.json").read_text())
        self.assertEqual(assets_json["assets"]["PROP_Tente_Default"]["version"], 2)
        self.assertEqual(len(assets_json["assets"]["PROP_Tente_Default"]["sha256"]), 64)

    def test_sync_removes_stale_version_after_repin(self):
        # v1 pinnee, sync, puis re-pin sur v2 -> v1 doit disparaitre.
        manifest = cp.read_manifest(self.project)
        manifest["web"]["pinned_assets"]["PROP_Tente_Default"]["version"] = 1
        cp.write_manifest(Path(self.project) / cp.PIPELINE_DIR, manifest)
        cp.sync_web_assets(self.project, self.web_dir)
        self.assertTrue((self.assets_dir / "PROP_Tente_Default_v001.glb").is_file())

        manifest["web"]["pinned_assets"]["PROP_Tente_Default"]["version"] = 2
        cp.write_manifest(Path(self.project) / cp.PIPELINE_DIR, manifest)
        cp.sync_web_assets(self.project, self.web_dir)

        self.assertFalse((self.assets_dir / "PROP_Tente_Default_v001.glb").is_file())
        self.assertTrue((self.assets_dir / "PROP_Tente_Default_v002.glb").is_file())

    def test_foreign_file_survives_sync(self):
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        foreign = self.assets_dir / "not_a_known_asset_v001.glb"
        foreign.write_bytes(b"foreign")
        cp.sync_web_assets(self.project, self.web_dir)
        self.assertTrue(foreign.is_file())


class TestCleanStaleStaging(TempProjectTestCase):
    """Sweep des allocations orphelines : clean_stale_staging()."""

    def setUp(self):
        super().setUp()
        cp.create_asset(self.project, "PROP_Tente_Default", asset_type="PROP")

    def _allocate_with_pid(self, step, pid):
        """Allocate un staging_dir puis renomme son suffixe PID (simule un process mort ou
        un autre process vivant, sans avoir a en forker un reel)."""
        staging, final = cp.allocate_publish_version(self.project, "PROP_Tente_Default", comment="", kind=step)
        renamed = staging.with_name(staging.name.rsplit("-", 1)[0] + f"-{pid}")
        staging.rename(renamed)
        return renamed, final

    def test_dry_run_reports_without_deleting(self):
        dead_dir, _ = self._allocate_with_pid("modeling", 999999)  # PID quasi-certainement mort
        report = cp.clean_stale_staging(self.project, dry_run=True)
        self.assertIn(str(dead_dir), report["removed_staging"])
        self.assertTrue(dead_dir.is_dir())  # dry-run : rien supprime

    def test_live_pid_never_removed(self):
        import os
        live_dir, _ = self._allocate_with_pid("lookdev", os.getpid())
        report = cp.clean_stale_staging(self.project)
        self.assertNotIn(str(live_dir), report["removed_staging"])
        self.assertTrue(live_dir.is_dir())

    def test_dead_pid_removed_and_reported_as_pending_without_staging(self):
        dead_dir, _ = self._allocate_with_pid("modeling", 999999)
        report = cp.clean_stale_staging(self.project)
        self.assertIn(str(dead_dir), report["removed_staging"])
        self.assertFalse(dead_dir.exists())

        kinds = [(e["kind"], e["version"]) for e in report["pending_without_staging"]]
        self.assertIn(("modeling", 1), kinds)

        # Le manifeste n'est jamais modifie par le sweep (entree reste 'pending').
        manifest = json.loads(
            (Path(self.project) / "assets" / "PROP_Tente_Default" / "manifest.json").read_text()
        )
        self.assertEqual(manifest["step_publishes"]["modeling"][0]["status"], "pending")

    def test_finalized_publish_has_no_leftover_staging(self):
        self._publish_step("PROP_Tente_Default", "modeling", ext="usd")
        report = cp.clean_stale_staging(self.project)
        self.assertEqual(report["removed_staging"], [])
        self.assertEqual(report["pending_without_staging"], [])


if __name__ == "__main__":
    unittest.main()
