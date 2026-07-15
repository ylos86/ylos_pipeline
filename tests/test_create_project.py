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


class TestPinWebAsset(TempProjectTestCase):
    """INC-6 : API de pinning web dans l'orchestrateur (pin_web_asset / unpin_web_asset /
    set_web_target). Valide contre les publishes GLB reels, ecrit project.json['web']
    atomiquement, ne leve JAMAIS pour un cas metier (retour {"ok": bool, ...})."""

    def setUp(self):
        super().setUp()
        cp.create_asset(self.project, "PROP_Tente_Default", asset_type="PROP")
        self._publish_step("PROP_Tente_Default", "lookdev", ext="glb")   # v1
        self._publish_step("PROP_Tente_Default", "lookdev", ext="glb")   # v2
        self._publish_step("PROP_Tente_Default", "modeling", ext="usd")  # USD : jamais pinnable

    def _pins(self):
        return cp.read_manifest(self.project).get("web", {}).get("pinned_assets", {})

    def test_pin_valid_glb_writes_manifest(self):
        result = cp.pin_web_asset(self.project, "PROP_Tente_Default", "lookdev", 2)
        self.assertTrue(result["ok"])
        self.assertEqual(result["version"], 2)
        self.assertEqual(self._pins()["PROP_Tente_Default"], {"step": "lookdev", "version": 2})

    def test_pin_nonexistent_version_clean_warning(self):
        result = cp.pin_web_asset(self.project, "PROP_Tente_Default", "lookdev", 99)
        self.assertFalse(result["ok"])
        self.assertIn("lookdev", result["error"])  # message liste le step / les disponibles
        self.assertEqual(self._pins(), {})          # rien ecrit au manifeste

    def test_pin_usd_step_rejected(self):
        # Un publish USD n'est jamais un GLB pinnable, meme s'il existe.
        result = cp.pin_web_asset(self.project, "PROP_Tente_Default", "modeling", 1)
        self.assertFalse(result["ok"])
        self.assertEqual(self._pins(), {})

    def test_pin_unknown_asset_no_raise(self):
        result = cp.pin_web_asset(self.project, "PROP_Fantome_Default", "lookdev", 1)
        self.assertFalse(result["ok"])  # ok=False, jamais d'exception

    def test_pin_bad_types_rejected(self):
        self.assertFalse(
            cp.pin_web_asset(self.project, "PROP_Tente_Default", "lookdev", "2")["ok"])
        self.assertFalse(cp.pin_web_asset(self.project, "", "lookdev", 1)["ok"])
        # bool est un int en Python : version=True ne doit pas matcher la v1.
        self.assertFalse(
            cp.pin_web_asset(self.project, "PROP_Tente_Default", "lookdev", True)["ok"])

    def test_unpin_idempotent(self):
        r0 = cp.unpin_web_asset(self.project, "PROP_Tente_Default")
        self.assertTrue(r0["ok"])
        self.assertFalse(r0["was_pinned"])  # de-pinner un non-pinne : ok, was_pinned False
        cp.pin_web_asset(self.project, "PROP_Tente_Default", "lookdev", 2)
        r1 = cp.unpin_web_asset(self.project, "PROP_Tente_Default")
        self.assertTrue(r1["was_pinned"])
        self.assertEqual(self._pins(), {})

    def test_set_web_target_roundtrip(self):
        r = cp.set_web_target(self.project, "/tmp/some/web")
        self.assertEqual(r["target_dir"], "/tmp/some/web")
        self.assertEqual(cp.read_manifest(self.project)["web"]["target_dir"], "/tmp/some/web")
        cp.set_web_target(self.project, "")  # '' efface la cible
        self.assertIsNone(cp.read_manifest(self.project)["web"]["target_dir"])

    def test_pin_then_sync_cycle(self):
        # Circuit complet : pin via l'API -> set target -> sync copie le GLB pinne.
        self.assertTrue(
            cp.pin_web_asset(self.project, "PROP_Tente_Default", "lookdev", 2)["ok"])
        web_dir = tempfile.mkdtemp(prefix="ylos_web_")
        self.addCleanup(shutil.rmtree, web_dir, ignore_errors=True)
        cp.set_web_target(self.project, web_dir)
        result = cp.sync_web_assets(self.project, web_dir)
        self.assertEqual(result["warnings"], [])
        self.assertIn("PROP_Tente_Default", result["synced"])
        self.assertTrue(
            (Path(web_dir) / "public" / "assets" / "PROP_Tente_Default_v002.glb").is_file())


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


class TestFrameRange(TempProjectTestCase):
    """Schema 2.1 : frame_range du shot (defaut a la creation + set_frame_range)."""

    SHOT = "ANIMATION_Sq010_Default"

    def _create_shot(self):
        cp.create_asset(self.project, self.SHOT, entity_type="shot", asset_type="ANIMATION")

    def _manifest(self, name):
        path = Path(self.project) / "shots" / name / cp.ASSET_MANIFEST_NAME
        return json.loads(path.read_text(encoding="utf-8"))

    def _shot_root(self, name):
        return (Path(self.project) / "shots" / name / cp.SHOT_ROOT_NAME).read_text(encoding="utf-8")

    def test_shot_creation_poses_default_frame_range_and_2_1(self):
        self._create_shot()
        m = self._manifest(self.SHOT)
        self.assertEqual(m["schema_version"], "2.1.0")
        self.assertEqual(m["frame_range"],
                         {"start": 1001, "end": 1100, "fps": cp.DEFAULT_SCENE["fps"]})

    def test_asset_has_no_frame_range(self):
        cp.create_asset(self.project, "PROP_Tente_Default", asset_type="PROP")
        m = json.loads(
            (Path(self.project) / "assets" / "PROP_Tente_Default" / cp.ASSET_MANIFEST_NAME)
            .read_text(encoding="utf-8"))
        self.assertNotIn("frame_range", m)
        self.assertEqual(m["schema_version"], "2.1.0")

    def test_set_frame_range_updates_manifest_and_timecodes(self):
        self._create_shot()
        fr = cp.set_frame_range(self.project, self.SHOT, 1010, 1042, fps=25)
        self.assertEqual(fr, {"start": 1010, "end": 1042, "fps": 25})
        self.assertEqual(self._manifest(self.SHOT)["frame_range"], fr)
        out = self._shot_root(self.SHOT)
        self.assertIn("startTimeCode = 1010", out)
        self.assertIn("endTimeCode = 1042", out)
        self.assertIn("timeCodesPerSecond = 25", out)

    def test_set_frame_range_fps_none_keeps_existing(self):
        self._create_shot()
        cp.set_frame_range(self.project, self.SHOT, 1010, 1050, fps=30)
        fr = cp.set_frame_range(self.project, self.SHOT, 1005, 1020)  # fps omis
        self.assertEqual(fr["fps"], 30)

    def test_set_frame_range_rejects_start_ge_end(self):
        self._create_shot()
        with self.assertRaises(ValueError):
            cp.set_frame_range(self.project, self.SHOT, 1100, 1001)
        with self.assertRaises(ValueError):
            cp.set_frame_range(self.project, self.SHOT, 1001, 1001)

    def test_set_frame_range_rejects_non_shot(self):
        cp.create_asset(self.project, "PROP_Tente_Default", asset_type="PROP")
        with self.assertRaises(ValueError):
            cp.set_frame_range(self.project, "PROP_Tente_Default", 1001, 1100)

    def test_legacy_shot_without_frame_range_still_accepted(self):
        # Manifeste 2.0 : shot sans frame_range (cle retiree a la main). set_frame_range
        # doit l'accepter et poser la plage sans crash (fallback fps = defaut scene).
        self._create_shot()
        path = Path(self.project) / "shots" / self.SHOT / cp.ASSET_MANIFEST_NAME
        m = json.loads(path.read_text(encoding="utf-8"))
        del m["frame_range"]
        m["schema_version"] = "2.0.0"
        path.write_text(json.dumps(m), encoding="utf-8")
        fr = cp.set_frame_range(self.project, self.SHOT, 1001, 1024)
        self.assertEqual(fr["fps"], cp.DEFAULT_SCENE["fps"])
        self.assertIn("startTimeCode = 1001", self._shot_root(self.SHOT))


class TestCacheAndConsumablePublish(TempProjectTestCase):
    """Increment 5 : convention cache (entity_cache_dir) + caches consommables publies en
    deux-phases (extensions .vdb/.bgeo.sc/.abc, sequences en dossier) sans polluer la compo."""

    SHOT = "FX_Sq010_Default"

    def _create_shot(self):
        cp.create_asset(self.project, self.SHOT, entity_type="shot", asset_type="FX")

    def test_entity_cache_dir_path_and_mkdir(self):
        # $PROJ_CACHE resolu depuis l'env (create() a pose <cache>/<projet>) - on cible la
        # meme racine cache que le projet pour un chemin coherent.
        os.environ[cp.ENV_CACHE] = self._tmp + "/cache"
        self.addCleanup(os.environ.pop, cp.ENV_CACHE, None)
        d = cp.entity_cache_dir(self.project, self.SHOT, "fx", "explosion")
        expected = (Path(self._tmp) / "cache" / "proj" / "houdini"
                    / self.SHOT / "fx" / "explosion")
        self.assertEqual(d.resolve(), expected.resolve())
        self.assertTrue(d.is_dir())  # parents crees

    def test_entity_cache_dir_validates_label(self):
        os.environ[cp.ENV_CACHE] = self._tmp + "/cache"
        self.addCleanup(os.environ.pop, cp.ENV_CACHE, None)
        with self.assertRaises(ValueError):
            cp.entity_cache_dir(self.project, self.SHOT, "fx", "bad/label")

    def test_new_extensions_accepted_by_two_phase(self):
        # Un cache consommable VDB passe le contrat deux-phases (artefact + thumbnail).
        self._create_shot()
        staging, final = cp.allocate_publish_version(self.project, self.SHOT, comment="", kind="fx")
        version = cp.publish_version_from_dir(final)
        stem = f"{self.SHOT}_fx_v{version:03d}"
        (staging / f"{stem}.vdb").write_bytes(b"vdb-data")
        (staging / "thumb.png").write_bytes(b"png")
        info = cp.finalize_publish_version(
            self.project, self.SHOT, staging, final, version,
            expected_artifacts=[stem, "thumb.png"])
        m = json.loads((Path(info["manifest"])).read_text(encoding="utf-8"))
        entry = m["step_publishes"]["fx"][-1]
        self.assertTrue(entry["artifact"].endswith(f"{stem}.vdb"))

    def test_bgeo_sc_double_suffix_resolved(self):
        # Le suffixe double .bgeo.sc doit resoudre par concatenation f"{stem}{ext}", pas par
        # split d'extension (_missing_artifacts). Un stem sans le fichier -> manquant.
        d = Path(self._tmp) / "stg_bgeo"
        d.mkdir()
        (d / "cache_fx_v001.bgeo.sc").write_bytes(b"geo")
        self.assertEqual(cp._missing_artifacts(d, ["cache_fx_v001"]), [])
        self.assertEqual(cp._missing_artifacts(d, ["absent"]), ["absent"])

    def test_missing_artifacts_sequence_folder(self):
        # Un artefact peut etre un DOSSIER de sequence (non vide) ; vide ou absent -> manquant.
        d = Path(self._tmp) / "stg_seq"
        d.mkdir()
        seq = d / "explosion_fx_v001"
        seq.mkdir()
        (seq / "explosion.0001.vdb").write_bytes(b"f1")
        (d / "thumb.png").write_bytes(b"png")
        self.assertEqual(
            cp._missing_artifacts(d, ["explosion_fx_v001", "thumb.png"]), [])
        # dossier vide -> manquant
        (d / "empty_v001").mkdir()
        self.assertEqual(cp._missing_artifacts(d, ["empty_v001"]), ["empty_v001"])

    def test_missing_artifacts_dot_entry_stays_exact_file(self):
        # La branche '.' (nom exact) reste prioritaire : un dossier 'thumb.png' ne satisfait
        # PAS l'attente d'un fichier thumb.png.
        d = Path(self._tmp) / "stg_dot"
        d.mkdir()
        (d / "thumb.png").mkdir()  # dossier homonyme, pas un fichier
        self.assertEqual(cp._missing_artifacts(d, ["thumb.png"]), ["thumb.png"])

    def test_sequence_publish_finalize_points_to_folder(self):
        # finalize doit decouvrir le dossier de sequence (produced liste aussi les dossiers)
        # et pointer 'artifact' dessus.
        self._create_shot()
        staging, final = cp.allocate_publish_version(self.project, self.SHOT, comment="", kind="fx")
        version = cp.publish_version_from_dir(final)
        stem = f"{self.SHOT}_fx_v{version:03d}"
        seq = staging / stem
        seq.mkdir()
        (seq / f"{stem}.0001.vdb").write_bytes(b"f1")
        (seq / f"{stem}.0002.vdb").write_bytes(b"f2")
        (staging / "thumb.png").write_bytes(b"png")
        info = cp.finalize_publish_version(
            self.project, self.SHOT, staging, final, version,
            expected_artifacts=[stem, "thumb.png"])
        m = json.loads(Path(info["manifest"]).read_text(encoding="utf-8"))
        entry = m["step_publishes"]["fx"][-1]
        self.assertTrue(entry["artifact"].endswith(f"publish/{stem}/{stem}"))
        self.assertTrue((Path(final) / stem).is_dir())

    def test_consumable_cache_absent_from_shot_root(self):
        # Un publish VDB en kind=step NE pollue PAS shot_root.usda (pas un layer USD).
        self._create_shot()
        # publish USD (animation) -> present dans la compo
        staging, final = cp.allocate_publish_version(self.project, self.SHOT, comment="", kind="animation")
        v = cp.publish_version_from_dir(final)
        astem = f"{self.SHOT}_animation_v{v:03d}"
        (staging / f"{astem}.usda").write_bytes(b"usd")
        (staging / "thumb.png").write_bytes(b"png")
        cp.finalize_publish_version(self.project, self.SHOT, staging, final, v,
                                    expected_artifacts=[astem, "thumb.png"])
        # publish VDB (fx) -> absent de la compo
        staging, final = cp.allocate_publish_version(self.project, self.SHOT, comment="", kind="fx")
        v = cp.publish_version_from_dir(final)
        fstem = f"{self.SHOT}_fx_v{v:03d}"
        (staging / f"{fstem}.vdb").write_bytes(b"vdb")
        (staging / "thumb.png").write_bytes(b"png")
        cp.finalize_publish_version(self.project, self.SHOT, staging, final, v,
                                    expected_artifacts=[fstem, "thumb.png"])
        shot_root = (Path(self.project) / "shots" / self.SHOT / cp.SHOT_ROOT_NAME).read_text(
            encoding="utf-8")
        self.assertIn(f"{astem}.usda", shot_root)  # animation USD compose
        self.assertNotIn(".vdb", shot_root)         # VDB jamais en subLayer
        self.assertNotIn(fstem, shot_root)


class TestListPublishes(TempProjectTestCase):
    """Volet lecture (CC#1c) : list_publishes fusionne deux-phases (dossier niche) +
    fichiers plats legacy sans doublon de version ; latest_publish_artifact prend la
    'complete' de version max. Ne leve jamais pour un cas metier."""

    def setUp(self):
        super().setUp()
        cp.create_asset(self.project, "PROP_Tente_Default", asset_type="PROP")

    def _legacy_flat(self, step, version, ext="usd"):
        pub = Path(self.project) / "assets" / "PROP_Tente_Default" / step / "publish"
        pub.mkdir(parents=True, exist_ok=True)
        f = pub / f"PROP_Tente_Default_{step}_v{version:03d}.{ext}"
        f.write_bytes(b"legacy usd layer")
        return f

    def test_two_phase_and_legacy_merge(self):
        self._publish_step("PROP_Tente_Default", "modeling")   # v1 deux-phases (dossier)
        self._legacy_flat("modeling", 2)                       # v2 fichier plat legacy
        pubs = cp.list_publishes(self.project, "PROP_Tente_Default", "modeling")
        self.assertEqual([(e["version"], e["legacy"]) for e in pubs], [(1, False), (2, True)])
        self.assertTrue(all(e["exists"] for e in pubs))
        self.assertTrue(all(Path(e["abs_path"]).is_file() for e in pubs))

    def test_legacy_never_overrides_two_phase_same_version(self):
        version, _ = self._publish_step("PROP_Tente_Default", "modeling")  # v1 deux-phases
        self._legacy_flat("modeling", version)                            # meme numero, a plat
        pubs = cp.list_publishes(self.project, "PROP_Tente_Default", "modeling")
        self.assertEqual(len(pubs), 1)
        self.assertFalse(pubs[0]["legacy"])  # le contrat vivant prime

    def test_latest_publish_artifact_is_complete_max(self):
        self._publish_step("PROP_Tente_Default", "modeling")   # v1 complete (deux-phases)
        self._legacy_flat("modeling", 3)                       # v3 complete (legacy)
        latest = cp.latest_publish_artifact(self.project, "PROP_Tente_Default", "modeling")
        self.assertIsNotNone(latest)
        self.assertEqual(latest["version"], 3)
        self.assertTrue(latest["exists"])
        self.assertTrue(Path(latest["abs_path"]).is_file())

    def test_pending_excluded_from_latest(self):
        # allocate sans finalize -> entree 'pending' (artifact None), visible mais jamais latest.
        cp.allocate_publish_version(self.project, "PROP_Tente_Default", comment="", kind="modeling")
        pubs = cp.list_publishes(self.project, "PROP_Tente_Default", "modeling")
        self.assertEqual([e["status"] for e in pubs], ["pending"])
        self.assertIsNone(cp.latest_publish_artifact(self.project, "PROP_Tente_Default", "modeling"))

    def test_missing_entity_or_step_returns_empty_no_raise(self):
        self.assertEqual(cp.list_publishes(self.project, "NOPE_X_Y", "modeling"), [])
        self.assertIsNone(cp.latest_publish_artifact(self.project, "NOPE_X_Y", "modeling"))
        self.assertEqual(cp.list_publishes(self.project, "PROP_Tente_Default", "rigging"), [])


class TestFinalizeThumbnailField(TempProjectTestCase):
    """Volet contrat (CC#1c) : finalize_publish_version renseigne 'thumbnail' (chemin
    relatif entite) quand thumb.png existe, en plus de 'thumb' (compat lecteurs existants)."""

    def setUp(self):
        super().setUp()
        cp.create_asset(self.project, "PROP_Tente_Default", asset_type="PROP")

    def test_thumbnail_field_populated(self):
        self._publish_step("PROP_Tente_Default", "modeling")
        mpath = Path(self.project) / "assets" / "PROP_Tente_Default" / "manifest.json"
        entry = json.loads(mpath.read_text())["step_publishes"]["modeling"][-1]
        self.assertEqual(entry["status"], "complete")
        self.assertTrue(entry["thumbnail"], "'thumbnail' doit etre renseigne")
        self.assertTrue(entry["thumbnail"].endswith("thumb.png"))
        self.assertTrue(entry["thumbnail"].startswith("modeling/publish/"))
        self.assertEqual(entry["thumbnail"], entry["thumb"])  # meme chemin


class TestPipelineTarget(unittest.TestCase):
    """CC#2 volet B : la cible de pipeline (FORMAT d'artifact) est une decision
    d'orchestrateur. create() ecrit 'pipeline_target' (derive du prod_type) ;
    get_pipeline_target() le lit tolerablement (champ absent -> derive, defaut 'offline')."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def _make(self, name, prod_type):
        info = cp.create(name, root=self._tmp + "/root", cache=self._tmp + "/cache",
                         prod_type=prod_type)
        return str(info["source"])

    def test_mapping_covers_all_prod_types(self):
        # Chaque PROD_TYPE a une cible (pas de KeyError silencieux downstream).
        for pt in cp.PROD_TYPES:
            self.assertIn(cp.PROD_TYPE_TO_TARGET.get(pt), ("web", "offline"),
                          f"{pt} sans cible dans PROD_TYPE_TO_TARGET")

    def test_create_writes_web_for_xr(self):
        proj = self._make("web_proj", "XR")
        pj = json.loads((Path(proj) / "_pipeline" / "project.json").read_text())
        self.assertEqual(pj["pipeline_target"], "web")
        self.assertEqual(cp.get_pipeline_target(proj), "web")

    def test_create_writes_offline_for_film(self):
        proj = self._make("film_proj", "FILM")
        pj = json.loads((Path(proj) / "_pipeline" / "project.json").read_text())
        self.assertEqual(pj["pipeline_target"], "offline")
        self.assertEqual(cp.get_pipeline_target(proj), "offline")

    def test_unknown_prod_type_defaults_offline(self):
        proj = self._make("unk_proj", "ZZ_UNKNOWN")
        self.assertEqual(cp.get_pipeline_target(proj), "offline")

    def test_missing_field_derives_from_prod_type(self):
        # Projet legacy 2.0 sans 'pipeline_target' : derive du prod_type, jamais de crash.
        proj = self._make("legacy_proj", "AR")
        mpath = Path(proj) / "_pipeline" / "project.json"
        data = json.loads(mpath.read_text())
        del data["pipeline_target"]
        mpath.write_text(json.dumps(data))
        self.assertEqual(cp.get_pipeline_target(proj), "web")  # AR -> web

    def test_explicit_field_wins_over_prod_type(self):
        # Un champ explicite prime la derivation (override manuel possible).
        proj = self._make("override_proj", "FILM")  # FILM -> offline par defaut
        mpath = Path(proj) / "_pipeline" / "project.json"
        data = json.loads(mpath.read_text())
        data["pipeline_target"] = "web"
        mpath.write_text(json.dumps(data))
        self.assertEqual(cp.get_pipeline_target(proj), "web")

    def test_unreadable_manifest_defaults_offline(self):
        # Manifeste absent -> defaut tolerant, jamais d'exception.
        self.assertEqual(cp.get_pipeline_target(self._tmp + "/nope"), "offline")


if __name__ == "__main__":
    unittest.main()
