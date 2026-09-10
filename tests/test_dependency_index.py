#!/usr/bin/env python3
"""
tests/test_dependency_index.py — stdlib tests (unittest) for the schema 2.2 dependency
tracking: 'dependencies' recorded by finalize_publish_version(), the ASCII USD layer scan,
build_dependency_index() and entity_dependencies() - plan-usable-v1 Phase 3.1.

Locks: validation before commit, both sources (manifest / usda) merged, self-edges
(an entity's own subLayers) excluded, $PROJ_ROOT expansion, binary layers skipped,
'outdated' = pinned older than latest complete, unpinned root never outdated, dedupe.

Usage: python3 tests/test_dependency_index.py
    or: python3 -m unittest tests.test_dependency_index
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

ASSET = "PROP_Tente_Default"
ASSET2 = "PROP_Barrel_Default"
SET = "EXTERIOR_Village_Default"
SHOT = "ANIMATION_Sq010_Default"


class _BaseCase(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ylos_deps_")).resolve()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        info = cp.create("Proj", root=self._tmp / "src", cache=self._tmp / "cache")
        self.project = Path(info["source"])
        cp.create_asset(self.project, ASSET, entity_type="asset", asset_type="PROP")
        cp.create_asset(self.project, ASSET2, entity_type="asset", asset_type="PROP")
        cp.create_asset(self.project, SET, entity_type="set", asset_type="EXTERIOR")
        cp.create_asset(self.project, SHOT, entity_type="shot", asset_type="ANIMATION")

    def _dir(self, name):
        return Path(cp.resolve_entity(self.project, name)["dir"])

    def _publish(self, entity, step, ext="usdc", content=b"artifact", dependencies=None):
        staging, final = cp.allocate_publish_version(self.project, entity, comment="", kind=step)
        version = cp.publish_version_from_dir(final)
        stem = f"{entity}_{step}_v{version:03d}"
        (staging / f"{stem}.{ext}").write_bytes(content)
        (staging / "thumb.png").write_bytes(b"png")
        cp.finalize_publish_version(self.project, entity, staging, final, version,
                                    expected_artifacts=[stem, "thumb.png"],
                                    dependencies=dependencies)
        return version

    def _manifest(self, name):
        return json.loads((self._dir(name) / "manifest.json").read_text(encoding="utf-8"))


class TestFinalizeDependencies(_BaseCase):
    def test_none_means_key_absent_empty_list_recorded(self):
        v = self._publish(ASSET, "modeling")
        entry = self._manifest(ASSET)["step_publishes"]["modeling"][0]
        self.assertEqual(entry["version"], v)
        self.assertNotIn("dependencies", entry)
        self._publish(ASSET, "modeling", dependencies=[])
        entry = self._manifest(ASSET)["step_publishes"]["modeling"][1]
        self.assertEqual(entry["dependencies"], [])

    def test_dependencies_normalized_and_recorded(self):
        self._publish(SET, "layout", ext="usda", content=b"#usda 1.0\n",
                      dependencies=[{"entity": ASSET, "step": "modeling", "version": "7", "extra": 1},
                                    {"entity": ASSET2, "step": "modeling", "version": None}])
        entry = self._manifest(SET)["step_publishes"]["layout"][0]
        self.assertEqual(entry["dependencies"], [
            {"entity": ASSET, "step": "modeling", "version": 7},
            {"entity": ASSET2, "step": "modeling", "version": None},
        ])

    def test_malformed_dependency_refused_before_commit(self):
        staging, final = cp.allocate_publish_version(self.project, SET, comment="", kind="layout")
        version = cp.publish_version_from_dir(final)
        stem = f"{SET}_layout_v{version:03d}"
        (staging / f"{stem}.usda").write_bytes(b"#usda 1.0\n")
        (staging / "thumb.png").write_bytes(b"png")
        for bad in ([{"entity": ASSET}], [{"step": "modeling"}], ["x"],
                    [{"entity": ASSET, "step": "modeling", "version": "seven"}]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                cp.finalize_publish_version(self.project, SET, staging, final, version,
                                            expected_artifacts=[stem, "thumb.png"],
                                            dependencies=bad)
        self.assertTrue(staging.is_dir())          # nothing committed
        self.assertFalse(final.exists())
        entry = self._manifest(SET)["step_publishes"]["layout"][0]
        self.assertEqual(entry["status"], "pending")


class TestUsdLayerScan(_BaseCase):
    def test_classify_project_paths(self):
        root = self._dir(ASSET) / "asset_root.usda"
        self.assertEqual(cp._classify_project_path(root, self.project),
                         {"entity": ASSET, "family": "asset", "step": None, "version": None})
        pub = self._dir(ASSET) / "modeling" / "publish" / f"{ASSET}_modeling_v007" / f"{ASSET}_modeling_v007.usdc"
        self.assertEqual(cp._classify_project_path(pub, self.project)["version"], 7)
        legacy = self._dir(ASSET) / "modeling" / "publish" / f"{ASSET}_modeling_v002.usda"
        self.assertEqual(cp._classify_project_path(legacy, self.project)["version"], 2)
        lop = self._dir(ASSET) / "lop" / "publish" / f"{ASSET}_lop_v001" / f"{ASSET}_lop_v001.usdnc"
        self.assertEqual(cp._classify_project_path(lop, self.project)["step"], "lop")
        self.assertIsNone(cp._classify_project_path(self.project / "resources" / "hdri" / "x.exr", self.project))
        self.assertIsNone(cp._classify_project_path(self._tmp / "elsewhere.usda", self.project))
        self.assertIsNone(cp._classify_project_path(self._dir(ASSET) / "modeling" / "wip" / "x.usda", self.project))

    def test_scan_relative_absolute_and_env_paths_dedupes(self):
        layer = self._dir(SET) / "layout" / "scratch.usda"
        layer.parent.mkdir(parents=True, exist_ok=True)
        abs_root = self._dir(ASSET) / "asset_root.usda"
        env_pub = f"$PROJ_ROOT/Proj/assets/{ASSET2}/modeling/publish/{ASSET2}_modeling_v003/{ASSET2}_modeling_v003.usdc"
        layer.write_text(
            "#usda 1.0\n(\n    subLayers = [\n"
            f"        @../../../assets/{ASSET}/asset_root.usda@,\n"
            f"        @{abs_root}@,\n"
            f"        @{env_pub}@,\n"
            "        @${PROJ_ROOT}/Proj/assets/" + ASSET2 + "/asset_root.usda@,\n"
            "        @$PROJ_CACHE/Proj/houdini/x.vdb@,\n"
            "        @/somewhere/else/thing.usd@\n    ]\n)\n", encoding="utf-8")
        deps = cp._usd_layer_dependencies(layer, self.project)
        self.assertEqual(deps, [
            {"entity": ASSET, "family": "asset", "step": None, "version": None},
            {"entity": ASSET2, "family": "asset", "step": "modeling", "version": 3},
            {"entity": ASSET2, "family": "asset", "step": None, "version": None},
        ])

    def test_binary_and_missing_layers_skipped(self):
        layer = self._dir(SET) / "layout" / "bin.usdc"
        layer.parent.mkdir(parents=True, exist_ok=True)
        layer.write_bytes(b"PXR-USDC\x00@../../../assets/" + ASSET.encode() + b"/asset_root.usda@")
        self.assertEqual(cp._usd_layer_dependencies(layer, self.project), [])
        self.assertEqual(cp._usd_layer_dependencies(layer.with_name("nope.usda"), self.project), [])


class TestDependencyIndex(_BaseCase):
    def test_empty_project_has_no_edges_and_own_sublayers_are_not_dependencies(self):
        self._publish(ASSET, "modeling", ext="usda", content=b"#usda 1.0\n")
        idx = cp.build_dependency_index(self.project)
        self.assertEqual(idx["edges"], [])
        self.assertEqual(cp.entity_dependencies(self.project, ASSET, idx),
                         {"uses": [], "used_in": [], "outdated": []})

    def test_manifest_source_and_outdated_flag(self):
        v1 = self._publish(ASSET, "modeling")
        self._publish(SET, "layout", ext="usda", content=b"#usda 1.0\n",
                      dependencies=[{"entity": ASSET, "step": "modeling", "version": v1}])
        idx = cp.build_dependency_index(self.project)
        self.assertEqual(len(idx["edges"]), 1)
        edge = idx["edges"][0]
        self.assertEqual(edge["source"], "manifest")
        self.assertEqual(edge["consumer"]["entity"], SET)
        self.assertEqual(edge["consumer"]["step"], "layout")
        self.assertTrue(edge["consumer"]["is_latest"])
        self.assertEqual(edge["dependency"]["family"], "asset")
        self.assertEqual((edge["dependency"]["version"], edge["dependency"]["latest_version"]), (v1, v1))
        self.assertFalse(edge["dependency"]["outdated"])
        # a newer asset publish -> the set's latest layout is now outdated
        v2 = self._publish(ASSET, "modeling")
        view = cp.entity_dependencies(self.project, SET)
        self.assertEqual(len(view["outdated"]), 1)
        self.assertEqual(view["outdated"][0]["dependency"]["latest_version"], v2)
        self.assertEqual([e["consumer"]["entity"] for e in cp.entity_dependencies(self.project, ASSET)["used_in"]], [SET])
        # the set republishes on the new version: previous edge no longer 'is_latest'
        self._publish(SET, "layout", ext="usda", content=b"#usda 1.0\n",
                      dependencies=[{"entity": ASSET, "step": "modeling", "version": v2}])
        view = cp.entity_dependencies(self.project, SET)
        self.assertEqual(view["outdated"], [])
        self.assertEqual(sorted(e["consumer"]["version"] for e in view["uses"]), [1, 2])

    def test_usda_source_from_composed_root_and_publish(self):
        self._publish(ASSET, "modeling")
        # the shot's composed root references the asset root (unpinned) and a pinned publish
        pinned = (self._dir(ASSET) / "modeling" / "publish" / f"{ASSET}_modeling_v001" / f"{ASSET}_modeling_v001.usdc")
        self.assertTrue(pinned.is_file())
        shot_root = self._dir(SHOT) / "shot_root.usda"
        shot_root.write_text(
            "#usda 1.0\n(\n    defaultPrim = \"ROOT\"\n)\ndef Xform \"ROOT\" {\n"
            f"    def Xform \"Tente\" ( references = @../../assets/{ASSET}/asset_root.usda@ ) {{}}\n"
            f"    def Xform \"Tente_pinned\" ( references = @{pinned}@ ) {{}}\n}}\n", encoding="utf-8")
        idx = cp.build_dependency_index(self.project)
        edges = [e for e in idx["edges"] if e["consumer"]["entity"] == SHOT]
        self.assertEqual(len(edges), 2)
        self.assertTrue(all(e["source"] == "usda" and e["file"] == "shot_root.usda"
                            and e["consumer"]["step"] is None and e["consumer"]["is_latest"] for e in edges))
        by_ver = {e["dependency"]["version"]: e for e in edges}
        self.assertFalse(by_ver[None]["dependency"]["outdated"])   # unpinned root: never outdated
        self.assertFalse(by_ver[1]["dependency"]["outdated"])
        self._publish(ASSET, "modeling")
        view = cp.entity_dependencies(self.project, SHOT)
        self.assertEqual([e["dependency"]["version"] for e in view["outdated"]], [1])
        # a .usda step publish of the set referencing the asset is scanned too
        self._publish(SET, "layout", ext="usda",
                      content=("#usda 1.0\n(\n    subLayers = [@../../../../../assets/"
                               f"{ASSET}/asset_root.usda@]\n)\n").encode())
        idx = cp.build_dependency_index(self.project)
        set_edges = [e for e in idx["edges"] if e["consumer"]["entity"] == SET]
        self.assertEqual(len(set_edges), 1)
        self.assertEqual(set_edges[0]["consumer"]["step"], "layout")
        self.assertTrue(set_edges[0]["file"].startswith("layout/publish/"))
        self.assertEqual(sorted(idx["used_in"][ASSET][i]["consumer"]["entity"] for i in range(3)),
                         sorted([SHOT, SHOT, SET]))

    def test_dedupe_prefers_manifest_source(self):
        v1 = self._publish(ASSET, "modeling")
        pinned = self._dir(ASSET) / "modeling" / "publish" / f"{ASSET}_modeling_v001" / f"{ASSET}_modeling_v001.usdc"
        self._publish(SET, "layout", ext="usda",
                      content=f"#usda 1.0\n(\n    subLayers = [@{pinned}@]\n)\n".encode(),
                      dependencies=[{"entity": ASSET, "step": "modeling", "version": v1}])
        idx = cp.build_dependency_index(self.project)
        self.assertEqual(sorted(e["source"] for e in idx["uses"][SET]), ["manifest", "usda"])
        view = cp.entity_dependencies(self.project, SET, idx)
        self.assertEqual(len(view["uses"]), 1)
        self.assertEqual(view["uses"][0]["source"], "manifest")

    def test_orphan_and_unknown_dependency_never_raise(self):
        (self.project / "sets" / "lecube" / "lookdev").mkdir(parents=True)
        self._publish(SET, "layout", ext="usda", content=b"#usda 1.0\n",
                      dependencies=[{"entity": "GHOST_Nope_Default", "step": "modeling", "version": 3}])
        idx = cp.build_dependency_index(self.project)
        self.assertEqual(len(idx["edges"]), 1)
        dep = idx["edges"][0]["dependency"]
        self.assertEqual((dep["entity"], dep["family"], dep["latest_version"], dep["outdated"]),
                         ("GHOST_Nope_Default", None, None, False))

    def test_cli_dependencies_prints_edges(self):
        import io
        from contextlib import redirect_stdout
        v1 = self._publish(ASSET, "modeling")
        self._publish(SET, "layout", ext="usda", content=b"#usda 1.0\n",
                      dependencies=[{"entity": ASSET, "step": "modeling", "version": v1}])
        self._publish(ASSET, "modeling")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cp._cli(["dependencies", str(self.project)])
        self.assertEqual(rc, 0)
        self.assertIn("UPDATE AVAILABLE", out.getvalue())
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cp._cli(["dependencies", str(self.project), "--entity", ASSET, "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out.getvalue())["used_in"][0]["consumer"]["entity"], SET)
        out = io.StringIO()
        with redirect_stdout(out):
            rc = cp._cli(["set-step-status", str(self.project), ASSET, "modeling", "approved"])
        self.assertEqual(rc, 0)
        self.assertIn("approved", out.getvalue())
        self.assertEqual(cp.get_step_status(self.project, ASSET, "modeling")["status"], "approved")


if __name__ == "__main__":
    unittest.main()
