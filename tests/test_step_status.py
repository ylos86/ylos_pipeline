#!/usr/bin/env python3
"""
tests/test_step_status.py — stdlib tests (unittest) for the schema 2.2 per-step status
(create_project.get_step_status / set_step_status), bound to a publish version since schema
2.3, and the multi-DCC scenefile listing (create_project.list_scenefiles) - plan-usable-v1
Phase 2.1.

Locks: derived statuses (empty -> wip -> published) read from disk + manifest, explicit
values (review / approved) persisted by the single writer and overriding derivation,
'auto' clearing, unknown step/status refused, never-raises read contract, the 2.3 version
binding (default = latest complete publish, explicit version validated, 'behind' when a
newer publish exists, 'step_status' kept in its 2.2 shape for rollback), and the sidecar
merge / DCC detection of the scenefile scan.

Usage: python3 tests/test_step_status.py
    or: python3 -m unittest tests.test_step_status
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
        self._tmp = Path(tempfile.mkdtemp(prefix="ylos_status_")).resolve()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        info = cp.create("Proj", root=self._tmp / "src", cache=self._tmp / "cache")
        self.project = Path(info["source"])
        cp.create_asset(self.project, "CHARACTER_Lina_Default",
                        entity_type="asset", asset_type="CHARACTER")
        self.entity_dir = self.project / "assets" / "CHARACTER_Lina_Default"

    def _wip(self, step, filename, sidecar=None):
        wip_dir = self.entity_dir / step / "wip"
        wip_dir.mkdir(parents=True, exist_ok=True)
        (wip_dir / filename).write_bytes(b"scene")
        if sidecar is not None:
            (wip_dir / (filename + ".json")).write_text(json.dumps(sidecar), encoding="utf-8")

    def _publish(self, step, ext="usdc"):
        staging, final = cp.allocate_publish_version(self.project, "CHARACTER_Lina_Default",
                                                     comment="", kind=step)
        version = cp.publish_version_from_dir(final)
        stem = f"CHARACTER_Lina_Default_{step}_v{version:03d}"
        (staging / f"{stem}.{ext}").write_bytes(b"artifact")
        (staging / "thumb.png").write_bytes(b"png")
        cp.finalize_publish_version(self.project, "CHARACTER_Lina_Default", staging, final,
                                    version, expected_artifacts=[stem, "thumb.png"])
        return version

    def _manifest(self):
        return json.loads((self.entity_dir / "manifest.json").read_text(encoding="utf-8"))


class TestDerivedStatus(_BaseCase):
    def test_fresh_entity_every_step_empty_and_not_explicit(self):
        statuses = cp.get_step_status(self.project, "CHARACTER_Lina_Default")
        self.assertEqual(set(statuses), set(self._manifest()["steps"]))
        for step, info in statuses.items():
            self.assertEqual(info, {"status": "empty", "explicit": False, "derived": "empty",
                                    "version": None, "set_utc": None,
                                    "latest_version": None, "behind": False}, step)

    def test_wip_scenefile_derives_wip_for_blender_and_houdini(self):
        self._wip("modeling", "CHARACTER_Lina_Default_modeling_v001.blend")
        self._wip("rigging", "CHARACTER_Lina_Default_rigging_v003.hipnc")
        self.assertEqual(cp.get_step_status(self.project, "CHARACTER_Lina_Default", "modeling")["status"], "wip")
        self.assertEqual(cp.get_step_status(self.project, "CHARACTER_Lina_Default", "rigging")["status"], "wip")
        self.assertEqual(cp.get_step_status(self.project, "CHARACTER_Lina_Default", "lookdev")["status"], "empty")

    def test_non_versioned_file_in_wip_is_ignored(self):
        self._wip("modeling", "notes.txt")
        self._wip("modeling", "CHARACTER_Lina_Default_modeling.blend")   # no _vNNN
        self.assertEqual(cp.get_step_status(self.project, "CHARACTER_Lina_Default", "modeling")["status"], "empty")

    def test_complete_publish_derives_published_over_wip(self):
        self._wip("modeling", "CHARACTER_Lina_Default_modeling_v001.blend")
        self._publish("modeling")
        info = cp.get_step_status(self.project, "CHARACTER_Lina_Default", "modeling")
        self.assertEqual(info["status"], "published")
        self.assertFalse(info["explicit"])

    def test_pending_reservation_is_not_published(self):
        cp.allocate_publish_version(self.project, "CHARACTER_Lina_Default", comment="", kind="modeling")
        self.assertEqual(cp.get_step_status(self.project, "CHARACTER_Lina_Default", "modeling")["status"], "empty")

    def test_legacy_publishes_map_derives_published(self):
        m = self._manifest()
        m["publishes"] = {"modeling": ["modeling/publish/CHARACTER_Lina_Default_modeling_v001.usdc"]}
        (self.entity_dir / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
        self.assertEqual(cp.get_step_status(self.project, "CHARACTER_Lina_Default", "modeling")["status"], "published")

    def test_unknown_entity_or_step_never_raises(self):
        self.assertEqual(cp.get_step_status(self.project, "Nope"), {})
        self.assertIsNone(cp.get_step_status(self.project, "Nope", "modeling"))
        self.assertIsNone(cp.get_step_status(self.project, "CHARACTER_Lina_Default", "compositing"))

    def test_malformed_step_status_field_is_ignored(self):
        m = self._manifest()
        m["step_status"] = "approved"   # wrong shape
        (self.entity_dir / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
        self.assertEqual(cp.get_step_status(self.project, "CHARACTER_Lina_Default", "modeling")["status"], "empty")
        m["step_status"] = {"modeling": "done"}   # unknown value -> derived
        (self.entity_dir / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
        info = cp.get_step_status(self.project, "CHARACTER_Lina_Default", "modeling")
        self.assertEqual((info["status"], info["explicit"]), ("empty", False))


class TestExplicitStatus(_BaseCase):
    def test_set_persists_and_overrides_derivation(self):
        self._publish("modeling")
        info = cp.set_step_status(self.project, "CHARACTER_Lina_Default", "modeling", "review")
        self.assertIsInstance(info.pop("set_utc"), str)
        self.assertEqual(info, {"status": "review", "explicit": True, "derived": "published",
                                "version": 1, "latest_version": 1, "behind": False})
        self.assertEqual(self._manifest()["step_status"], {"modeling": "review"})
        self.assertEqual(self._manifest()["schema_version"], cp.SCHEMA_VERSION)

    def test_approved_then_auto_clears_back_to_derived(self):
        cp.set_step_status(self.project, "CHARACTER_Lina_Default", "lookdev", "approved")
        self.assertEqual(cp.get_step_status(self.project, "CHARACTER_Lina_Default", "lookdev")["status"], "approved")
        info = cp.set_step_status(self.project, "CHARACTER_Lina_Default", "lookdev", cp.STEP_STATUS_AUTO)
        self.assertEqual((info["status"], info["explicit"]), ("empty", False))
        self.assertNotIn("step_status", self._manifest())   # key dropped when empty

    def test_two_steps_keep_independent_values(self):
        cp.set_step_status(self.project, "CHARACTER_Lina_Default", "modeling", "approved")
        cp.set_step_status(self.project, "CHARACTER_Lina_Default", "rigging", "review")
        cp.set_step_status(self.project, "CHARACTER_Lina_Default", "modeling", None)
        self.assertEqual(self._manifest()["step_status"], {"rigging": "review"})

    def test_modified_utc_bumped(self):
        before = self._manifest()["modified_utc"]
        cp.set_step_status(self.project, "CHARACTER_Lina_Default", "modeling", "review")
        self.assertGreaterEqual(self._manifest()["modified_utc"], before)

    def test_derived_values_are_refused_as_explicit(self):
        for bad in ("empty", "wip", "published", "done", "APPROVED"):
            with self.assertRaises(ValueError, msg=bad):
                cp.set_step_status(self.project, "CHARACTER_Lina_Default", "modeling", bad)
        self.assertNotIn("step_status", self._manifest())

    def test_undeclared_step_refused_without_writing(self):
        with self.assertRaises(ValueError) as ctx:
            cp.set_step_status(self.project, "CHARACTER_Lina_Default", "compositing", "review")
        self.assertIn("compositing", str(ctx.exception))
        self.assertNotIn("step_status", self._manifest())

    def test_unknown_entity_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            cp.set_step_status(self.project, "Nope", "modeling", "review")

    def test_vocabulary_constants(self):
        self.assertEqual(cp.STEP_STATUSES, ["empty", "wip", "published", "review", "approved"])
        self.assertEqual(set(cp.STEP_STATUS_EXPLICIT), {"review", "approved"})
        self.assertTrue(set(cp.STEP_STATUS_EXPLICIT) <= set(cp.STEP_STATUSES))


class TestVersionBoundStatus(_BaseCase):
    """Schema 2.3 - an explicit status records the publish version it applies to
    (manifest['step_status_meta']). A later publish is reported as 'behind', never
    silently covered by the old approval, and 'step_status' keeps its 2.2 shape."""

    E = "CHARACTER_Lina_Default"

    def _write(self, manifest):
        (self.entity_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    def test_default_version_is_the_latest_complete_publish(self):
        self._publish("modeling")
        self._publish("modeling")
        # A pending reservation (allocated, never finalized) is not a publish.
        cp.allocate_publish_version(self.project, self.E, comment="", kind="modeling")
        info = cp.set_step_status(self.project, self.E, "modeling", "approved")
        self.assertEqual((info["version"], info["latest_version"], info["behind"]), (2, 2, False))
        meta = self._manifest()[cp.STEP_STATUS_META_KEY]["modeling"]
        self.assertEqual(meta["version"], 2)
        self.assertEqual(meta["set_utc"], info["set_utc"])

    def test_newer_publish_is_reported_behind_and_never_downgrades(self):
        self._publish("modeling")
        cp.set_step_status(self.project, self.E, "modeling", "approved")
        self._publish("modeling")
        info = cp.get_step_status(self.project, self.E, "modeling")
        self.assertEqual(info["status"], "approved")
        self.assertEqual((info["version"], info["latest_version"], info["behind"]), (1, 2, True))

    def test_explicit_older_version_is_accepted_and_behind(self):
        self._publish("modeling")
        self._publish("modeling")
        info = cp.set_step_status(self.project, self.E, "modeling", "review", version=1)
        self.assertEqual((info["status"], info["version"], info["behind"]), ("review", 1, True))

    def test_version_must_be_a_complete_publish_of_the_step(self):
        self._publish("modeling")                                                # v1 complete
        cp.allocate_publish_version(self.project, self.E, comment="", kind="modeling")  # v2 pending
        self._publish("rigging")                                                 # rigging v1
        before = self._manifest()
        for bad in (2, 9, 0, -1, "1", 1.0, True):
            with self.assertRaises(ValueError, msg=repr(bad)):
                cp.set_step_status(self.project, self.E, "modeling", "approved", version=bad)
        with self.assertRaises(ValueError):   # rigging's v1 is not a lookdev publish
            cp.set_step_status(self.project, self.E, "lookdev", "approved", version=1)
        self.assertEqual(self._manifest(), before)   # a refused call writes nothing

    def test_version_is_refused_when_clearing(self):
        self._publish("modeling")
        with self.assertRaises(ValueError):
            cp.set_step_status(self.project, self.E, "modeling", cp.STEP_STATUS_AUTO, version=1)
        self.assertNotIn(cp.STEP_STATUS_META_KEY, self._manifest())

    def test_status_without_any_publish_records_no_version(self):
        self._wip("modeling", "CHARACTER_Lina_Default_modeling_v001.blend")
        info = cp.set_step_status(self.project, self.E, "modeling", "review")
        self.assertEqual((info["status"], info["version"], info["latest_version"], info["behind"]),
                         ("review", None, None, False))
        self.assertIsNone(self._manifest()[cp.STEP_STATUS_META_KEY]["modeling"]["version"])

    def test_clearing_drops_both_maps(self):
        self._publish("modeling")
        cp.set_step_status(self.project, self.E, "modeling", "approved")
        cp.set_step_status(self.project, self.E, "modeling", cp.STEP_STATUS_AUTO)
        manifest = self._manifest()
        self.assertNotIn(cp.STEP_STATUS_KEY, manifest)
        self.assertNotIn(cp.STEP_STATUS_META_KEY, manifest)

    def test_step_status_keeps_its_22_shape(self):
        # Rollback safety: a 2.2 reader (tag snapshot/2026-10-02-before-schema-2.3) must still
        # see every review/approved of a manifest written in 2.3.
        self._publish("modeling")
        cp.set_step_status(self.project, self.E, "modeling", "approved")
        cp.set_step_status(self.project, self.E, "rigging", "review")
        self.assertEqual(self._manifest()[cp.STEP_STATUS_KEY],
                         {"modeling": "approved", "rigging": "review"})

    def test_status_set_before_23_has_an_unknown_version(self):
        self._publish("modeling")
        self._publish("modeling")
        manifest = self._manifest()
        manifest[cp.STEP_STATUS_KEY] = {"modeling": "approved"}   # written by a 2.2 tool
        self._write(manifest)
        info = cp.get_step_status(self.project, self.E, "modeling")
        self.assertEqual((info["status"], info["explicit"], info["version"], info["set_utc"],
                          info["latest_version"], info["behind"]),
                         ("approved", True, None, None, 2, False))

    def test_meta_without_a_status_is_ignored(self):
        # A 2.2 tool cleared the status but left the 2.3 meta behind.
        self._publish("modeling")
        cp.set_step_status(self.project, self.E, "modeling", "approved")
        manifest = self._manifest()
        del manifest[cp.STEP_STATUS_KEY]
        self._write(manifest)
        info = cp.get_step_status(self.project, self.E, "modeling")
        self.assertEqual((info["status"], info["explicit"], info["version"], info["behind"]),
                         ("published", False, None, False))

    def test_malformed_meta_never_raises(self):
        self._publish("modeling")
        manifest = self._manifest()
        manifest[cp.STEP_STATUS_KEY] = {"modeling": "approved", "rigging": "review"}
        manifest[cp.STEP_STATUS_META_KEY] = {"modeling": {"version": "1", "set_utc": 5},
                                             "rigging": "not a dict"}
        self._write(manifest)
        statuses = cp.get_step_status(self.project, self.E)
        self.assertEqual((statuses["modeling"]["version"], statuses["modeling"]["set_utc"]),
                         (None, None))
        self.assertIsNone(statuses["rigging"]["version"])
        manifest[cp.STEP_STATUS_META_KEY] = ["not", "a", "dict"]
        self._write(manifest)
        self.assertIsNone(cp.get_step_status(self.project, self.E, "modeling")["version"])

    def test_derived_status_reports_the_latest_version(self):
        self._publish("modeling")
        info = cp.get_step_status(self.project, self.E, "modeling")
        self.assertEqual((info["status"], info["version"], info["latest_version"], info["behind"]),
                         ("published", None, 1, False))

    def test_asset_schema_documents_the_meta_map(self):
        schema = json.loads((_REPO_ROOT / "asset.schema.json").read_text(encoding="utf-8"))
        meta = schema["properties"][cp.STEP_STATUS_META_KEY]
        self.assertEqual(meta["additionalProperties"]["properties"]["version"]["type"],
                         ["integer", "null"])
        # 'step_status' itself is unchanged from 2.2: plain strings, explicit values only.
        self.assertEqual(schema["properties"][cp.STEP_STATUS_KEY]["additionalProperties"]["enum"],
                         list(cp.STEP_STATUS_EXPLICIT))


class TestListScenefiles(_BaseCase):
    def test_empty_entity_returns_empty_dict(self):
        self.assertEqual(cp.list_scenefiles(self.project, "CHARACTER_Lina_Default"), {})

    def test_unknown_entity_returns_empty_dict(self):
        self.assertEqual(cp.list_scenefiles(self.project, "Nope"), {})

    def test_mixed_dccs_sorted_with_sidecars(self):
        self._wip("modeling", "CHARACTER_Lina_Default_modeling_v002.blend",
                  sidecar={"comment": "second", "user": "seb", "date": "2026-09-10T10:00:00+00:00",
                           "blender_version": "5.2.0"})
        self._wip("modeling", "CHARACTER_Lina_Default_modeling_v001.blend")
        self._wip("modeling", "CHARACTER_Lina_Default_modeling_v003.hiplc",
                  sidecar={"comment": "houdini pass", "houdini_version": "21.0.631"})
        self._wip("modeling", "CHARACTER_Lina_Default_modeling_v002.blend1")   # backup, ignored
        rows = cp.list_scenefiles(self.project, "CHARACTER_Lina_Default")["modeling"]
        self.assertEqual([r["version"] for r in rows], [1, 2, 3])
        self.assertEqual([r["dcc"] for r in rows], ["blender", "blender", "houdini"])
        self.assertEqual(rows[1]["comment"], "second")
        self.assertEqual(rows[1]["user"], "seb")
        self.assertEqual(rows[1]["dcc_version"], "5.2.0")
        self.assertEqual(rows[1]["blender_version"], "5.2.0")
        self.assertEqual(rows[2]["dcc_version"], "21.0.631")
        self.assertEqual(rows[2]["blender_version"], "")
        self.assertEqual(rows[0]["comment"], "")
        self.assertTrue(Path(rows[0]["path"]).is_file())

    def test_unreadable_sidecar_degrades_to_empty_fields(self):
        self._wip("modeling", "CHARACTER_Lina_Default_modeling_v001.blend")
        (self.entity_dir / "modeling" / "wip"
         / "CHARACTER_Lina_Default_modeling_v001.blend.json").write_text("{not json", encoding="utf-8")
        rows = cp.list_scenefiles(self.project, "CHARACTER_Lina_Default", "modeling")["modeling"]
        self.assertEqual(rows[0]["comment"], "")

    def test_step_filter_and_undeclared_step(self):
        self._wip("modeling", "CHARACTER_Lina_Default_modeling_v001.blend")
        self._wip("rigging", "CHARACTER_Lina_Default_rigging_v001.blend")
        self.assertEqual(list(cp.list_scenefiles(self.project, "CHARACTER_Lina_Default", "rigging")), ["rigging"])
        self.assertEqual(cp.list_scenefiles(self.project, "CHARACTER_Lina_Default", "compositing"), {})


class TestLatestWipPerDcc(_BaseCase):
    """_latest_wip / resolve_open_target / scene_starter_spec count each DCC's own files."""

    def test_houdini_wip_resolved_by_resolve_open_target(self):
        self._wip("modeling", "CHARACTER_Lina_Default_modeling_v001.blend")
        self._wip("modeling", "CHARACTER_Lina_Default_modeling_v004.hipnc")
        self._wip("modeling", "CHARACTER_Lina_Default_modeling_v002.hip")
        blender = cp.resolve_open_target("CHARACTER_Lina_Default", "blender", "modeling", project_root=self.project)
        houdini = cp.resolve_open_target("CHARACTER_Lina_Default", "houdini", "modeling", project_root=self.project)
        self.assertEqual((blender["kind"], Path(blender["path"]).name), ("wip", "CHARACTER_Lina_Default_modeling_v001.blend"))
        self.assertEqual((houdini["kind"], Path(houdini["path"]).name), ("wip", "CHARACTER_Lina_Default_modeling_v004.hipnc"))

    def test_unknown_dcc_skips_wip_branch(self):
        self._wip("modeling", "CHARACTER_Lina_Default_modeling_v001.blend")
        target = cp.resolve_open_target("CHARACTER_Lina_Default", "maya", "modeling", project_root=self.project)
        self.assertNotEqual(target.get("kind"), "wip")

    def test_starter_spec_numbers_per_dcc_and_lists_extensions(self):
        self._wip("modeling", "CHARACTER_Lina_Default_modeling_v003.blend")
        self._wip("modeling", "CHARACTER_Lina_Default_modeling_v001.hipnc")
        b = cp.scene_starter_spec("CHARACTER_Lina_Default", "modeling", "blender", project_root=self.project)
        h = cp.scene_starter_spec("CHARACTER_Lina_Default", "modeling", "houdini", project_root=self.project)
        self.assertEqual(b["wip"]["version"], 4)
        self.assertEqual(h["wip"]["version"], 2)
        self.assertEqual(b["wip"]["extensions"], [".blend"])
        self.assertEqual(h["wip"]["extensions"], [".hip", ".hiplc", ".hipnc"])
        self.assertEqual(h["wip"]["stem"], "CHARACTER_Lina_Default_modeling_v002")
        self.assertNotIn("path", h["wip"])   # extension is license-dependent: the adapter decides


class TestListEntities(_BaseCase):
    def test_lists_families_and_flags_orphans(self):
        cp.create_asset(self.project, "EXTERIOR_Village_Default", entity_type="set", asset_type="EXTERIOR")
        cp.create_asset(self.project, "ANIMATION_Sq010_Default", entity_type="shot", asset_type="ANIMATION")
        orphan = self.project / "sets" / "lecube" / "lookdev" / "wip"
        orphan.mkdir(parents=True)
        (self.project / "assets" / ".hidden").mkdir()
        rows = cp.list_entities(self.project)
        by_name = {r["name"]: r for r in rows}
        self.assertEqual([r["name"] for r in rows],
                         ["CHARACTER_Lina_Default", "EXTERIOR_Village_Default", "lecube", "ANIMATION_Sq010_Default"])
        self.assertEqual(by_name["CHARACTER_Lina_Default"]["family"], "asset")
        self.assertEqual(by_name["CHARACTER_Lina_Default"]["entity_type"], "CHARACTER")
        self.assertIsNone(by_name["CHARACTER_Lina_Default"]["broken"])
        self.assertEqual(by_name["EXTERIOR_Village_Default"]["family"], "set")
        self.assertEqual(by_name["ANIMATION_Sq010_Default"]["family"], "shot")
        self.assertIn("manifest.json missing", by_name["lecube"]["broken"])
        self.assertEqual(by_name["lecube"]["steps"], ["lookdev"])
        self.assertEqual(by_name["lecube"]["manifest"], {})

    def test_family_filter_and_unreadable_manifest(self):
        (self.entity_dir / "manifest.json").write_text("{broken", encoding="utf-8")
        rows = cp.list_entities(self.project, family="asset")
        self.assertEqual(len(rows), 1)
        self.assertIn("unreadable", rows[0]["broken"])
        self.assertEqual(cp.list_entities(self.project, family="shot"), [])
        self.assertEqual(cp.list_entities(self.project, family="nope"), [])


if __name__ == "__main__":
    unittest.main()
