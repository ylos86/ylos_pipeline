#!/usr/bin/env python3
"""
tests/test_entity_overview.py - stdlib tests for what a project-level view shows of one entity:
create_project.entity_overview, abandoned_reservations and artifact_extension (web home of
direction C; the Blender browser and the Houdini panel read the same function).

Locks: a version number only travels with the render of THAT version (an approval of v001
keeps showing v001's render after v002 is published); newer unpublished work is reported only
past the grace that covers the save around a publish; a reservation is abandoned only when no
running publisher can finalize it; the last activity is the latest of publishes, saves and the
manifest; every read is safe on an unknown entity.

Usage: python3 -m unittest tests.test_entity_overview
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
import create_project as cp  # noqa: E402

ENTITY = "CHARACTER_Lina_Default"


def _dead_pid() -> int:
    """PID of a process that has already exited (reliably dead, unlike a made-up number)."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ylos_overview_")).resolve()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        info = cp.create("Proj", root=self._tmp / "src", cache=self._tmp / "cache")
        self.project = Path(info["source"])
        cp.create_asset(self.project, ENTITY, entity_type="asset", asset_type="CHARACTER")
        self.entity_dir = self.project / "assets" / ENTITY

    def _publish(self, step, ext="usdc"):
        staging, final = cp.allocate_publish_version(self.project, ENTITY, comment="", kind=step)
        version = cp.publish_version_from_dir(final)
        stem = f"{ENTITY}_{step}_v{version:03d}"
        (staging / f"{stem}.{ext}").write_bytes(b"artifact")
        (staging / "thumb.png").write_bytes(b"png")
        cp.finalize_publish_version(self.project, ENTITY, staging, final, version,
                                    expected_artifacts=[stem, "thumb.png"])
        return version

    def _reserve(self, step):
        """A reservation left pending, with its staging dir (owned by THIS live process)."""
        staging, final = cp.allocate_publish_version(self.project, ENTITY, comment="", kind=step)
        return staging, cp.publish_version_from_dir(final)

    def _wip(self, step, filename, when=None):
        wip_dir = self.entity_dir / step / "wip"
        wip_dir.mkdir(parents=True, exist_ok=True)
        f = wip_dir / filename
        f.write_bytes(b"scene")
        if when is not None:
            os.utime(f, (when.timestamp(), when.timestamp()))
        return f

    def _published_at(self, step, version):
        manifest = json.loads((self.entity_dir / "manifest.json").read_text(encoding="utf-8"))
        entry = next(e for e in manifest["step_publishes"][step] if e["version"] == version)
        return cp._parse_utc(entry["published_utc"])

    def overview(self):
        return cp.entity_overview(self.project, ENTITY)


class TestShownVersion(_Base):
    def test_fresh_entity_lists_every_declared_step_with_nothing_to_show(self):
        ov = self.overview()
        manifest = json.loads((self.entity_dir / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(list(ov["steps"]), manifest["steps"])   # the manifest's order
        for step, info in ov["steps"].items():
            self.assertEqual(info["status"], "empty", step)
            self.assertIsNone(info["shown_version"], step)
            self.assertIsNone(info["shown_thumb"], step)
            self.assertIsNone(info["latest_scenefile"], step)
            self.assertFalse(info["newer_wip"], step)
            self.assertEqual(info["abandoned"], [], step)
        self.assertEqual(ov["last_activity_utc"], cp._parse_utc(manifest["modified_utc"]).isoformat())

    def test_latest_publish_is_shown_with_its_own_render(self):
        self._publish("modeling")
        self._publish("modeling", ext="glb")
        info = self.overview()["steps"]["modeling"]
        self.assertEqual(info["status"], "published")
        self.assertEqual(info["shown_version"], 2)
        self.assertEqual(info["shown_ext"], "glb")
        self.assertIn("v002", info["shown_thumb"])
        self.assertTrue((self.entity_dir / info["shown_thumb"]).is_file())
        self.assertIsNotNone(info["shown_published_utc"])

    def test_an_approval_keeps_showing_the_render_of_its_version(self):
        self._publish("modeling")
        cp.set_step_status(self.project, ENTITY, "modeling", "approved")   # binds v001
        self._publish("modeling")                                         # v002, not reviewed
        info = self.overview()["steps"]["modeling"]
        self.assertEqual(info["status"], "approved")
        self.assertTrue(info["behind"])
        self.assertEqual(info["latest_version"], 2)
        self.assertEqual(info["shown_version"], 1)          # never v002's render next to it
        self.assertIn("v001", info["shown_thumb"])

    def test_a_22_status_without_version_falls_back_to_the_latest_publish(self):
        self._publish("modeling")
        manifest_path = self.entity_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["step_status"] = {"modeling": "review"}    # written by a 2.2 tool: no meta
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        info = self.overview()["steps"]["modeling"]
        self.assertEqual(info["status"], "review")
        self.assertIsNone(info["version"])
        self.assertEqual(info["shown_version"], 1)


class TestScenefiles(_Base):
    def test_latest_scenefile_is_the_highest_version_across_dccs(self):
        self._wip("modeling", f"{ENTITY}_modeling_v001.blend")
        self._wip("modeling", f"{ENTITY}_modeling_v003.hipnc")
        info = self.overview()["steps"]["modeling"]
        self.assertEqual(info["latest_scenefile"]["version"], 3)
        self.assertEqual(info["latest_scenefile"]["dcc"], "houdini")
        self.assertIsNotNone(info["latest_scenefile"]["mtime_utc"])
        self.assertEqual(info["status"], "wip")

    def test_scenefile_rows_carry_their_save_date(self):
        when = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
        self._wip("modeling", f"{ENTITY}_modeling_v001.blend", when=when)
        row = cp.list_scenefiles(self.project, ENTITY)["modeling"][0]
        self.assertEqual(cp._parse_utc(row["mtime_utc"]), when)

    def test_newer_wip_only_past_the_grace(self):
        self._publish("modeling")
        published = self._published_at("modeling", 1)
        grace = timedelta(seconds=cp.NEWER_WIP_GRACE_S)
        self._wip("modeling", f"{ENTITY}_modeling_v001.blend", when=published - timedelta(minutes=5))
        self.assertFalse(self.overview()["steps"]["modeling"]["newer_wip"], "saved before the publish")
        self._wip("modeling", f"{ENTITY}_modeling_v002.blend", when=published + grace - timedelta(seconds=10))
        self.assertFalse(self.overview()["steps"]["modeling"]["newer_wip"], "the save around the publish")
        self._wip("modeling", f"{ENTITY}_modeling_v003.blend", when=published + grace + timedelta(minutes=5))
        self.assertTrue(self.overview()["steps"]["modeling"]["newer_wip"], "unpublished work")

    def test_no_publish_means_no_newer_wip(self):
        self._wip("modeling", f"{ENTITY}_modeling_v004.blend")
        self.assertFalse(self.overview()["steps"]["modeling"]["newer_wip"])


class TestLastActivity(_Base):
    def test_latest_of_publishes_saves_and_manifest(self):
        self._publish("modeling")
        later = datetime.now(timezone.utc) + timedelta(days=1)
        self._wip("rigging", f"{ENTITY}_rigging_v001.blend", when=later)
        self.assertEqual(cp._parse_utc(self.overview()["last_activity_utc"]),
                         later.replace(microsecond=later.microsecond))

    def test_a_publish_moves_the_last_activity(self):
        before = cp._parse_utc(self.overview()["last_activity_utc"])
        self._publish("modeling")
        self.assertGreaterEqual(cp._parse_utc(self.overview()["last_activity_utc"]), before)


class TestAbandonedReservations(_Base):
    def test_a_reservation_whose_publisher_runs_is_not_abandoned(self):
        self._reserve("modeling")                      # staging owned by this live process
        self.assertEqual(cp.abandoned_reservations(self.project, ENTITY), {})
        self.assertEqual(self.overview()["steps"]["modeling"]["abandoned"], [])

    def test_no_staging_dir_means_abandoned(self):
        staging, version = self._reserve("modeling")
        shutil.rmtree(staging)
        self.assertEqual(cp.abandoned_reservations(self.project, ENTITY), {"modeling": [version]})
        self.assertEqual(self.overview()["steps"]["modeling"]["abandoned"], [version])

    def test_staging_of_a_dead_process_means_abandoned(self):
        staging, version = self._reserve("lookdev")
        staging.rename(staging.with_name(staging.name.rsplit("-", 1)[0] + f"-{_dead_pid()}"))
        self.assertEqual(cp.abandoned_reservations(self.project, ENTITY), {"lookdev": [version]})

    def test_a_complete_publish_is_never_abandoned_and_versions_sort(self):
        for _ in range(2):
            staging, _v = self._reserve("modeling")
            shutil.rmtree(staging)
        self._publish("modeling")
        self.assertEqual(cp.abandoned_reservations(self.project, ENTITY), {"modeling": [1, 2]})
        self.assertEqual(self.overview()["steps"]["modeling"]["shown_version"], 3)

    def test_reading_never_rewrites_the_manifest(self):
        staging, _v = self._reserve("modeling")
        shutil.rmtree(staging)
        manifest_path = self.entity_dir / "manifest.json"
        before = manifest_path.read_bytes()
        cp.abandoned_reservations(self.project, ENTITY)
        cp.entity_overview(self.project, ENTITY)
        self.assertEqual(manifest_path.read_bytes(), before)


class TestSafeReads(_Base):
    def test_unknown_entity(self):
        self.assertIsNone(cp.entity_overview(self.project, "PROP_Ghost_Default"))
        self.assertEqual(cp.abandoned_reservations(self.project, "PROP_Ghost_Default"), {})

    def test_orphan_folder(self):
        (self.project / "sets" / "lecube").mkdir(parents=True)
        self.assertIsNone(cp.entity_overview(self.project, "lecube"))

    def test_malformed_pending_entry_is_skipped(self):
        manifest_path = self.entity_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["step_publishes"] = {"modeling": [{"status": "pending", "version": None},
                                                   {"status": "pending", "version": "2"}]}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(cp.abandoned_reservations(self.project, ENTITY), {})
        self.assertIn("modeling", self.overview()["steps"])


class TestArtifactExtension(unittest.TestCase):
    def test_longest_known_suffix_wins(self):
        self.assertEqual(cp.artifact_extension("fx/publish/x_v001/x.bgeo.sc"), "bgeo.sc")
        self.assertEqual(cp.artifact_extension("modeling/publish/x_v001/x.USDC"), "usdc")
        self.assertEqual(cp.artifact_extension("x.glb"), "glb")
        self.assertEqual(cp.artifact_extension("x.weird"), "weird")
        self.assertIsNone(cp.artifact_extension(None))
        self.assertIsNone(cp.artifact_extension(""))


if __name__ == "__main__":
    unittest.main()
