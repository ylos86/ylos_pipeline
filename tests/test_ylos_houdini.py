#!/usr/bin/env python3
"""
tests/test_ylos_houdini.py — stdlib tests (unittest) for the Houdini bridge
plugins/houdini/python/ylos_houdini.py.

CI constraint (see the module docstring and CLAUDE.md): the bridge is importable WITHOUT hou
nor a Houdini license. Here we test ONLY the pure functions (hip_extension,
parse_wip_context, list_wip_versions, next_wip_path, list_entities, latest_lop_publish,
env_relative) - no call to a hou action. Merely importing the module locks in
the absence of a top-level `import hou` (a CI machine has no hou installed).

The plugins/houdini/python folder is not a package -> import by path
(importlib.util, same pattern as tests/test_migrate_to_2_0.py).

Usage: python3 tests/test_ylos_houdini.py
    or: python3 -m unittest tests.test_ylos_houdini
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

# plugins/houdini/python is not a package -> explicit loading by path.
_MODULE_PATH = _REPO_ROOT / "plugins" / "houdini" / "python" / "ylos_houdini.py"
_spec = importlib.util.spec_from_file_location("ylos_houdini", _MODULE_PATH)
yh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(yh)  # raises if a top-level `import hou` existed -> CI guard


class ImportableWithoutHouTestCase(unittest.TestCase):
    """The bridge must load without hou (CI constraint) - the module has already imported
    at file level; we explicitly lock in that no hou was pulled."""

    def test_import_does_not_pull_hou(self):
        # exec_module above succeeded; had it done a top-level `import hou`, it would have
        # raised ModuleNotFoundError on a machine without Houdini (the CI).
        self.assertFalse("hou" in sys.modules,
                         "ylos_houdini must never import hou at module level")
        self.assertTrue(hasattr(yh, "hip_extension"))


class HipExtensionTestCase(unittest.TestCase):
    """Save extension based on the license, injected (never read from hou in the tests)."""

    def test_commercial(self):
        self.assertEqual(yh.hip_extension("Commercial"), ".hip")

    def test_indie(self):
        self.assertEqual(yh.hip_extension("Indie"), ".hiplc")

    def test_apprentice_and_unknown_fall_back_to_hipnc(self):
        self.assertEqual(yh.hip_extension("Apprentice"), ".hipnc")
        self.assertEqual(yh.hip_extension("Education"), ".hipnc")
        self.assertEqual(yh.hip_extension("SomethingUnknown"), ".hipnc")


class EnvRelativeTestCase(unittest.TestCase):
    """$PROJ_ROOT/<relative> when the path lives under $PROJ_ROOT, absolute otherwise."""

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

    def test_under_proj_root_becomes_variable(self):
        os.environ[cp.ENV_ROOT] = str(self._tmp)
        target = self._tmp / "assets" / "CHARACTER_Lina_Default" / "asset_root.usda"
        self.assertEqual(yh.env_relative(target),
                         "$PROJ_ROOT/assets/CHARACTER_Lina_Default/asset_root.usda")

    def test_without_proj_root_stays_absolute(self):
        os.environ.pop(cp.ENV_ROOT, None)
        target = self._tmp / "x.usda"
        self.assertEqual(yh.env_relative(target), str(target))

    def test_outside_proj_root_stays_absolute(self):
        os.environ[cp.ENV_ROOT] = str(self._tmp / "somewhere")
        other = self._tmp / "elsewhere" / "y.usda"
        self.assertEqual(yh.env_relative(other), str(other))


class CacheDirExpressionTestCase(unittest.TestCase):
    """Increment 5: literal expression $PROJ_CACHE/<project>/houdini/<entity>/<step>/ set
    on a filecache's basedir (relocatable, variable unresolved). Pure, without hou."""

    def test_literal_expression_relocatable(self):
        expr = yh.cache_dir_expression("/vol/ext/MyProj", "FX_Sq010_Default", "fx")
        self.assertEqual(expr, "$PROJ_CACHE/MyProj/houdini/FX_Sq010_Default/fx/")
        # the variable stays literal: no resolved absolute path leaks into the expression.
        self.assertNotIn("/vol/ext", expr)


class RenderOutputExpressionTestCase(unittest.TestCase):
    """Increment 6: literal expression of the output EXR file ($PROJ_CACHE + $F4),
    set on 'outputimage' of the usdrender_rop (relocatable, variable unresolved). Pure."""

    def test_literal_expression_with_f4(self):
        expr = yh.render_output_expression("/vol/ext/MyProj", "SHOT_Sq010_Default",
                                           "lighting", 3)
        self.assertEqual(
            expr,
            "$PROJ_CACHE/MyProj/render/SHOT_Sq010_Default/lighting/v003/"
            "SHOT_Sq010_Default_lighting_v003.$F4.exr")
        # neither the resolved absolute path nor a hard-coded frame number leaks into the expression.
        self.assertNotIn("/vol/ext", expr)
        self.assertIn("$F4", expr)


class RenderCacheTestCase(unittest.TestCase):
    """Increment 6: next_render_version (disk scan of the cache tier, +1) and deliver_render
    (explicit copy to delivery/, refusal if empty). The cache tier is resolved via
    create_project.resolve_cache -> $PROJ_CACHE must be set (as in prod)."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ylos_hou_render_")).resolve()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._saved_cache = os.environ.get(cp.ENV_CACHE)
        self.addCleanup(self._restore_cache)
        os.environ[cp.ENV_CACHE] = str(self._tmp / "cache")
        info = cp.create("Proj", root=self._tmp / "src", cache=self._tmp / "cache")
        self.project = Path(info["source"])
        cp.create_asset(self.project, "ANIMATION_Sq010_Default",
                        entity_type="shot", asset_type="ANIMATION")

    def _restore_cache(self):
        if self._saved_cache is None:
            os.environ.pop(cp.ENV_CACHE, None)
        else:
            os.environ[cp.ENV_CACHE] = self._saved_cache

    def _render_version_dir(self, step, version):
        d = (yh.render_dir(self.project, "ANIMATION_Sq010_Default", step)
             / f"v{version:03d}")
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_next_render_version_no_render(self):
        self.assertEqual(
            yh.next_render_version(self.project, "ANIMATION_Sq010_Default", "lighting"), 1)

    def test_next_render_version_increments_disk_max(self):
        self._render_version_dir("lighting", 1)
        self._render_version_dir("lighting", 3)  # gap: max+1, not count+1
        (self._render_version_dir("lighting", 3)
         / "img.0001.exr").write_text("", encoding="utf-8")
        self.assertEqual(
            yh.next_render_version(self.project, "ANIMATION_Sq010_Default", "lighting"), 4)
        # another step is independent.
        self.assertEqual(
            yh.next_render_version(self.project, "ANIMATION_Sq010_Default", "fx"), 1)

    def test_list_render_versions_ignores_non_vNNN(self):
        self._render_version_dir("lighting", 2)
        (yh.render_dir(self.project, "ANIMATION_Sq010_Default", "lighting")
         / "notes").mkdir(parents=True, exist_ok=True)  # not v<NNN> -> ignored
        self.assertEqual(
            yh.list_render_versions(self.project, "ANIMATION_Sq010_Default", "lighting"), [2])

    def test_deliver_render_copies_to_delivery(self):
        vdir = self._render_version_dir("lighting", 2)
        (vdir / "SHOT_lighting_v002.0001.exr").write_text("exr", encoding="utf-8")
        (vdir / "SHOT_lighting_v002.0002.exr").write_text("exr", encoding="utf-8")
        dst = yh.deliver_render(self.project, "ANIMATION_Sq010_Default", "lighting", 2)
        expected = (self.project / "delivery" / "render" / "ANIMATION_Sq010_Default"
                    / "lighting" / "v002")
        self.assertEqual(Path(dst), expected)
        self.assertTrue((expected / "SHOT_lighting_v002.0001.exr").is_file())
        self.assertTrue((expected / "SHOT_lighting_v002.0002.exr").is_file())

    def test_deliver_render_steps_do_not_merge(self):
        # two steps at the same version -> distinct delivery paths (the <step> separates
        # them), no silent overwrite via copytree dirs_exist_ok.
        for step in ("lighting", "fx"):
            vdir = self._render_version_dir(step, 1)
            (vdir / f"{step}.0001.exr").write_text(step, encoding="utf-8")
        d_light = yh.deliver_render(self.project, "ANIMATION_Sq010_Default", "lighting", 1)
        d_fx = yh.deliver_render(self.project, "ANIMATION_Sq010_Default", "fx", 1)
        self.assertNotEqual(Path(d_light), Path(d_fx))
        self.assertTrue((Path(d_light) / "lighting.0001.exr").is_file())
        self.assertTrue((Path(d_fx) / "fx.0001.exr").is_file())

    def test_deliver_render_refuses_missing_source(self):
        with self.assertRaises(FileNotFoundError):
            yh.deliver_render(self.project, "ANIMATION_Sq010_Default", "lighting", 9)

    def test_deliver_render_refuses_empty_source(self):
        self._render_version_dir("lighting", 1)  # v001 folder created but empty
        with self.assertRaises(FileNotFoundError):
            yh.deliver_render(self.project, "ANIMATION_Sq010_Default", "lighting", 1)


class RealProjectTestCase(unittest.TestCase):
    """Real project + entities (create()/create_asset()) - for everything that reads a
    project.json / manifest.json on disk: parse_wip_context, list_wip_versions,
    next_wip_path, list_entities, latest_lop_publish."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ylos_hou_proj_")).resolve()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        info = cp.create("Proj", root=self._tmp / "src", cache=self._tmp / "cache")
        self.project = Path(info["source"])
        # one asset (steps modeling/rigging/lookdev/fx) and one shot (animation/fx/lighting/comp)
        cp.create_asset(self.project, "CHARACTER_Lina_Default",
                        entity_type="asset", asset_type="CHARACTER")
        cp.create_asset(self.project, "ANIMATION_Sq010_Default",
                        entity_type="shot", asset_type="ANIMATION")

    # -- parse_wip_context --------------------------------------------------------------

    def test_parse_wip_context_conforming_path(self):
        hip = (self.project / "shots" / "ANIMATION_Sq010_Default" / "animation" / "wip"
               / "ANIMATION_Sq010_Default_animation_v001.hipnc")
        ctx = yh.parse_wip_context(hip)
        self.assertIsNotNone(ctx)
        project_root, entity_name, step = ctx
        self.assertEqual(Path(project_root), self.project)
        self.assertEqual(entity_name, "ANIMATION_Sq010_Default")
        self.assertEqual(step, "animation")

    def test_parse_wip_context_outside_project(self):
        # good lexical form but no _pipeline/project.json at the deduced root.
        hip = (self._tmp / "assets" / "CHARACTER_Lina_Default" / "modeling" / "wip"
               / "x_v001.hipnc")
        self.assertIsNone(yh.parse_wip_context(hip))

    def test_parse_wip_context_unknown_family(self):
        hip = (self.project / "foobar" / "CHARACTER_Lina_Default" / "modeling" / "wip"
               / "x_v001.hipnc")
        self.assertIsNone(yh.parse_wip_context(hip))

    def test_parse_wip_context_not_in_wip(self):
        hip = (self.project / "assets" / "CHARACTER_Lina_Default" / "modeling"
               / "publish" / "x_v001.hipnc")
        self.assertIsNone(yh.parse_wip_context(hip))

    # -- list_wip_versions / next_wip_path ----------------------------------------------

    def _wip_dir(self, family, entity, step):
        d = self.project / family / entity / step / "wip"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_list_wip_versions_mixed_extensions(self):
        wip = self._wip_dir("assets", "CHARACTER_Lina_Default", "modeling")
        (wip / "CHARACTER_Lina_Default_modeling_v001.hip").write_text("", encoding="utf-8")
        (wip / "CHARACTER_Lina_Default_modeling_v002.hiplc").write_text("", encoding="utf-8")
        (wip / "CHARACTER_Lina_Default_modeling_v003.hipnc").write_text("", encoding="utf-8")
        (wip / "notes.txt").write_text("", encoding="utf-8")  # ignored (no _vNNN.hip*)
        versions = yh.list_wip_versions(self.project, "CHARACTER_Lina_Default", "modeling")
        self.assertEqual([v["version"] for v in versions], [1, 2, 3])

    def test_list_wip_versions_empty(self):
        self.assertEqual(
            yh.list_wip_versions(self.project, "CHARACTER_Lina_Default", "modeling"), [])

    def test_next_wip_path_increments_disk_max(self):
        wip = self._wip_dir("assets", "CHARACTER_Lina_Default", "modeling")
        (wip / "CHARACTER_Lina_Default_modeling_v001.hip").write_text("", encoding="utf-8")
        (wip / "CHARACTER_Lina_Default_modeling_v002.hipnc").write_text("", encoding="utf-8")
        path, version = yh.next_wip_path(self.project, "CHARACTER_Lina_Default", "modeling",
                                         license_category="Commercial")
        self.assertEqual(version, 3)
        self.assertEqual(Path(path).name, "CHARACTER_Lina_Default_modeling_v003.hip")
        self.assertEqual(Path(path).parent, wip)

    def test_next_wip_path_first_version(self):
        _, version = yh.next_wip_path(self.project, "ANIMATION_Sq010_Default", "animation",
                                      license_category="Apprentice")
        self.assertEqual(version, 1)

    def test_next_wip_path_invalid_step(self):
        with self.assertRaises(ValueError):
            yh.next_wip_path(self.project, "CHARACTER_Lina_Default", "bogus_step",
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

    def test_latest_lop_publish_ignores_pending(self):
        self._set_lop_publishes("CHARACTER_Lina_Default", [
            {"version": 1, "status": "complete", "layer": "lop/publish/v001/lina_v001.usdnc"},
            {"version": 2, "status": "complete", "layer": "lop/publish/v002/lina_v002.usdnc"},
            {"version": 3, "status": "pending", "layer": "lop/publish/v003/lina_v003.usdnc"},
        ])
        got = yh.latest_lop_publish(self.project, "CHARACTER_Lina_Default")
        expected = (self.project / "assets" / "CHARACTER_Lina_Default"
                    / "lop/publish/v002/lina_v002.usdnc")
        self.assertEqual(Path(got), expected)

    def test_latest_lop_publish_none(self):
        self.assertIsNone(
            yh.latest_lop_publish(self.project, "CHARACTER_Lina_Default"))

    # -- latest_step_publish ------------------------------------------------------------

    def _set_step_publishes(self, family, entity, step_publishes):
        manifest_path = (self.project / family / entity / cp.ASSET_MANIFEST_NAME)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest[cp.STEP_PUBLISHES_KEY] = step_publishes
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def test_latest_step_publish_ignores_pending(self):
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

    def test_latest_step_publish_none_and_step_absent(self):
        # step never published -> None
        self.assertIsNone(
            yh.latest_step_publish(self.project, "ANIMATION_Sq010_Default", "animation"))
        # step present but only 'pending' ones -> None too
        self._set_step_publishes("shots", "ANIMATION_Sq010_Default", {
            "lighting": [
                {"version": 1, "status": "pending",
                 "artifact": "lighting/publish/v001/light_v001.usdnc"},
            ],
        })
        self.assertIsNone(
            yh.latest_step_publish(self.project, "ANIMATION_Sq010_Default", "lighting"))

    # -- shot_root_path -----------------------------------------------------------------

    def test_shot_root_path_absent_raises(self):
        with self.assertRaises(FileNotFoundError):
            yh.shot_root_path(self.project, "ANIMATION_Sq010_Default")

    def test_shot_root_path_present(self):
        shot_dir = self.project / "shots" / "ANIMATION_Sq010_Default"
        root = shot_dir / cp.SHOT_ROOT_NAME
        root.write_text("#usda 1.0\n", encoding="utf-8")
        self.assertEqual(
            Path(yh.shot_root_path(self.project, "ANIMATION_Sq010_Default")), root)


# =======================================================================================
# Prism parity (plan-usable-v1 Phases 1.3 / 4.1) - pure functions only. Everything the
# Houdini realization does (create_scene, the panel, the HDA callback) is built on THESE
# four functions precisely so it stays verifiable on a machine without a Houdini license.
# =======================================================================================


class WipSidecarTestCase(unittest.TestCase):
    """Sidecar payload of a Houdini WIP - the keys must match what
    create_project.list_scenefiles reads back ('houdini_version' -> 'dcc_version')."""

    def test_payload_keys(self):
        payload = yh.wip_sidecar("blocking pass", "seb", "2026-09-10T10:00:00+00:00",
                                 "21.0.631")
        self.assertEqual(payload, {"comment": "blocking pass", "user": "seb",
                                   "date": "2026-09-10T10:00:00+00:00",
                                   "houdini_version": "21.0.631"})

    def test_none_values_become_empty_strings(self):
        # a sidecar written by a batch session (no user, no comment) must stay readable,
        # never carry a null that a JS/Python consumer would have to special-case.
        self.assertEqual(yh.wip_sidecar(None, None, None, None),
                         {"comment": "", "user": "", "date": "", "houdini_version": ""})

    def test_suffix_is_json(self):
        self.assertEqual(yh.SIDECAR_SUFFIX, ".json")


class StarterPlanTestCase(unittest.TestCase):
    """starter_plan(): serializable realization plan of a scene_starter_spec. The
    orchestrator decides WHAT, this decides HOW (LOP chain + hip path). Pure: the license
    is injected, never read from hou."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ylos_hou_starter_")).resolve()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self._saved_root = os.environ.pop(cp.ENV_ROOT, None)
        self.addCleanup(self._restore_root)
        info = cp.create("Proj", root=self._tmp / "src", cache=self._tmp / "cache")
        self.project = Path(info["source"])
        cp.create_asset(self.project, "CHARACTER_Lina_Default",
                        entity_type="asset", asset_type="CHARACTER")
        cp.create_asset(self.project, "ANIMATION_Sq010_Default",
                        entity_type="shot", asset_type="ANIMATION")
        self.asset_dir = self.project / "assets" / "CHARACTER_Lina_Default"
        self.shot_dir = self.project / "shots" / "ANIMATION_Sq010_Default"

    def _restore_root(self):
        if self._saved_root is None:
            os.environ.pop(cp.ENV_ROOT, None)
        else:
            os.environ[cp.ENV_ROOT] = self._saved_root

    def _plan(self, entity, step, license_category="Apprentice"):
        spec = cp.scene_starter_spec(entity, step, "houdini", project_root=self.project)
        self.assertTrue(spec.get("ok"), spec.get("reason"))
        return yh.starter_plan(spec, license_category)

    # -- refusals -----------------------------------------------------------------------

    def test_refuses_a_failed_spec(self):
        plan = yh.starter_plan({"ok": False, "reason": "entity not found"}, "Apprentice")
        self.assertFalse(plan["ok"])
        self.assertIn("entity not found", plan["reason"])

    def test_refuses_a_spec_built_for_another_dcc(self):
        spec = cp.scene_starter_spec("CHARACTER_Lina_Default", "modeling", "blender",
                                     project_root=self.project)
        plan = yh.starter_plan(spec, "Apprentice")
        self.assertFalse(plan["ok"])
        self.assertIn("blender", plan["reason"])

    def test_refuses_an_extension_outside_the_spec(self):
        # defensive: if the orchestrator ever restricts the allowed extensions, a license
        # that cannot produce one of them must fail LOUDLY, not save a .hipnc anyway.
        spec = cp.scene_starter_spec("CHARACTER_Lina_Default", "modeling", "houdini",
                                     project_root=self.project)
        spec["wip"]["extensions"] = [".hip"]
        plan = yh.starter_plan(spec, "Apprentice")
        self.assertFalse(plan["ok"])
        self.assertIn(".hipnc", plan["reason"])

    # -- hip path / license -------------------------------------------------------------

    def test_license_picks_the_hip_extension(self):
        for license_category, ext in (("Commercial", ".hip"), ("Indie", ".hiplc"),
                                      ("Apprentice", ".hipnc"), ("Whatever", ".hipnc")):
            plan = self._plan("CHARACTER_Lina_Default", "modeling", license_category)
            self.assertTrue(plan["ok"])
            self.assertTrue(plan["target"].endswith(
                "CHARACTER_Lina_Default_modeling_v001" + ext), plan["target"])

    def test_target_lives_in_the_step_wip_folder(self):
        plan = self._plan("CHARACTER_Lina_Default", "modeling")
        self.assertEqual(Path(plan["target"]).parent,
                         self.asset_dir / "modeling" / "wip")

    def test_sidecar_is_the_hip_plus_json(self):
        plan = self._plan("CHARACTER_Lina_Default", "modeling")
        self.assertEqual(plan["sidecar"], plan["target"] + ".json")

    def test_version_counts_every_hip_extension_on_disk(self):
        wip = self.asset_dir / "modeling" / "wip"
        wip.mkdir(parents=True, exist_ok=True)
        (wip / "CHARACTER_Lina_Default_modeling_v001.hip").write_text("", encoding="utf-8")
        (wip / "CHARACTER_Lina_Default_modeling_v002.hipnc").write_text("", encoding="utf-8")
        plan = self._plan("CHARACTER_Lina_Default", "modeling")
        self.assertEqual(plan["version"], 3)
        self.assertTrue(plan["target"].endswith("_modeling_v003.hipnc"), plan["target"])

    # -- context ------------------------------------------------------------------------

    def test_env_carries_the_context_and_job(self):
        plan = self._plan("CHARACTER_Lina_Default", "modeling")
        self.assertEqual(plan["env"], {
            yh.CONTEXT_ENV_PROJECT: str(self.project),
            yh.CONTEXT_ENV_ENTITY: "CHARACTER_Lina_Default",
            yh.CONTEXT_ENV_STEP: "modeling",
            "JOB": str(self.project),
        })

    # -- node chains --------------------------------------------------------------------

    def test_asset_modeling_is_an_empty_stage(self):
        # modeling AUTHORS the model, it does not consume it: no reference, no light.
        plan = self._plan("CHARACTER_Lina_Default", "modeling")
        self.assertEqual(plan["nodes"], [])
        self.assertIsNone(plan["display"])
        self.assertIsNone(plan["frame_range"])
        self.assertIsNone(plan["fps"])
        self.assertEqual(plan["family"], "asset")

    def test_asset_lookdev_references_the_asset_root_and_adds_a_dome_light(self):
        (self.asset_dir / cp.ASSET_ROOT_NAME).write_text("#usda 1.0\n", encoding="utf-8")
        plan = self._plan("CHARACTER_Lina_Default", "lookdev")
        self.assertEqual([n["type"] for n in plan["nodes"]], ["reference", "domelight"])
        ref = plan["nodes"][0]
        # 'reference' and NOT 'sublayer': an asset is GRAFTED under its own prim.
        self.assertEqual(ref["parms"]["primpath"], "/CHARACTER_Lina_Default")
        self.assertEqual(ref["parms"]["filepath1"],
                         str(self.asset_dir / cp.ASSET_ROOT_NAME))
        self.assertEqual(plan["nodes"][1]["parms"]["primpath"],
                         yh.STARTER_LIGHT_PRIMPATH_DEFAULT)
        self.assertEqual(plan["display"], "dome_starter")

    def test_reference_path_is_written_in_proj_root_when_set(self):
        # principle 1 (CLAUDE.md): a scene never hard-codes an absolute path when the
        # project lives under $PROJ_ROOT.
        os.environ[cp.ENV_ROOT] = str(self.project.parent)
        (self.asset_dir / cp.ASSET_ROOT_NAME).write_text("#usda 1.0\n", encoding="utf-8")
        plan = self._plan("CHARACTER_Lina_Default", "lookdev")
        self.assertEqual(
            plan["nodes"][0]["parms"]["filepath1"],
            "$PROJ_ROOT/Proj/assets/CHARACTER_Lina_Default/" + cp.ASSET_ROOT_NAME)

    def test_asset_lookdev_without_a_built_root_has_no_reference(self):
        # create_asset() writes a stub asset_root.usda; here we simulate the assembly
        # missing on disk - the starter is then a light setup on an empty stage, never a
        # reference to a file that is not there.
        (self.asset_dir / cp.ASSET_ROOT_NAME).unlink()
        plan = self._plan("CHARACTER_Lina_Default", "lookdev")
        self.assertEqual([n["type"] for n in plan["nodes"]], ["domelight"])

    def test_shot_animation_sublayers_the_shot_root_and_adds_a_camera(self):
        (self.shot_dir / cp.SHOT_ROOT_NAME).write_text("#usda 1.0\n", encoding="utf-8")
        plan = self._plan("ANIMATION_Sq010_Default", "animation")
        self.assertEqual([n["type"] for n in plan["nodes"]], ["sublayer", "camera"])
        sub = plan["nodes"][0]
        # 'sublayer' and NOT 'reference': the shot IS the stage (root prim /ROOT).
        self.assertEqual(sub["parms"]["num_files"], 1)
        self.assertEqual(sub["parms"]["filepath1"],
                         str(self.shot_dir / cp.SHOT_ROOT_NAME))
        self.assertEqual(plan["nodes"][1]["parms"]["primpath"],
                         yh.STARTER_CAMERA_PRIMPATH)
        self.assertEqual(plan["family"], "shot")

    def test_shot_lighting_puts_the_dome_light_under_root(self):
        (self.shot_dir / cp.SHOT_ROOT_NAME).write_text("#usda 1.0\n", encoding="utf-8")
        plan = self._plan("ANIMATION_Sq010_Default", "lighting")
        types = [n["type"] for n in plan["nodes"]]
        self.assertEqual(types, ["sublayer", "domelight"])
        self.assertEqual(plan["nodes"][-1]["parms"]["primpath"],
                         yh.STARTER_LIGHT_PRIMPATH["shot"])

    # -- frame range --------------------------------------------------------------------

    def test_shot_frame_range_and_fps_come_from_the_manifest(self):
        cp.set_frame_range(self.project, "ANIMATION_Sq010_Default", 1010, 1120, fps=25)
        plan = self._plan("ANIMATION_Sq010_Default", "animation")
        self.assertEqual(plan["frame_range"], [1010, 1120])
        self.assertEqual(plan["fps"], 25.0)

    def test_shot_without_frame_range_leaves_houdinis_own(self):
        # create_asset() gives a shot a default frame_range; a legacy 2.0 manifest has
        # none - the starter must then leave the Houdini session's own range alone rather
        # than invent one.
        manifest_path = self.shot_dir / cp.ASSET_MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.pop("frame_range", None)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        plan = self._plan("ANIMATION_Sq010_Default", "animation")
        self.assertIsNone(plan["frame_range"])
        self.assertIsNone(plan["fps"])

    def test_asset_never_gets_a_frame_range(self):
        plan = self._plan("CHARACTER_Lina_Default", "lookdev")
        self.assertIsNone(plan["frame_range"])


# --- stage_layer_paths: stubs of the pxr API (duck-typed, no pxr import needed) ---------

class _StubLayer:
    """Minimal Sdf.Layer: identifier / realPath / anonymous + composition dependencies."""

    def __init__(self, real_path="", refs=(), anonymous=False, identifier=None,
                 legacy=False, explode=False):
        self.realPath = real_path
        self.identifier = identifier if identifier is not None else real_path
        self.anonymous = anonymous
        self._refs = list(refs)
        self._explode = explode
        if legacy:   # older USD: only GetExternalReferences exists
            self.GetExternalReferences = self._deps
        else:
            self.GetCompositionAssetDependencies = self._deps

    def _deps(self):
        if self._explode:
            raise RuntimeError("layer refuses introspection")
        return self._refs

    def ComputeAbsolutePath(self, ref):
        if ref.startswith("/"):
            return ref
        return str(Path(self.realPath).parent / ref)


class _StubStage:
    def __init__(self, layers, explode=False):
        self._layers = list(layers)
        self._explode = explode

    def GetUsedLayers(self):
        if self._explode:
            raise RuntimeError("stage refuses introspection")
        return self._layers


class StageLayerPathsTestCase(unittest.TestCase):
    """stage_layer_paths(): the file paths a composed stage depends on. Duck-typed on the
    pxr API so it is testable without USD - and NEVER raises: a publish must not fail
    because a layer refuses introspection."""

    def test_collects_layers_and_their_references(self):
        layers = [
            _StubLayer("/proj/shots/SHOT_A/shot_root.usda",
                       refs=["animation/publish/v001/a.usda"]),
            _StubLayer("/proj/assets/AST_B/asset_root.usda"),
        ]
        self.assertEqual(yh.stage_layer_paths(_StubStage(layers)), [
            "/proj/shots/SHOT_A/shot_root.usda",
            "/proj/shots/SHOT_A/animation/publish/v001/a.usda",
            "/proj/assets/AST_B/asset_root.usda",
        ])

    def test_skips_anonymous_layers(self):
        layers = [_StubLayer(identifier="anon:0x1234:tmp", anonymous=True),
                  _StubLayer("/proj/assets/AST_B/asset_root.usda")]
        self.assertEqual(yh.stage_layer_paths(_StubStage(layers)),
                         ["/proj/assets/AST_B/asset_root.usda"])

    def test_deduplicates_preserving_order(self):
        layers = [_StubLayer("/proj/a.usda", refs=["/proj/b.usda"]),
                  _StubLayer("/proj/b.usda")]
        self.assertEqual(yh.stage_layer_paths(_StubStage(layers)),
                         ["/proj/a.usda", "/proj/b.usda"])

    def test_legacy_get_external_references_is_used_when_present(self):
        layers = [_StubLayer("/proj/a.usda", refs=["/proj/b.usda"], legacy=True)]
        self.assertEqual(yh.stage_layer_paths(_StubStage(layers)),
                         ["/proj/a.usda", "/proj/b.usda"])

    def test_a_stage_that_refuses_returns_empty(self):
        self.assertEqual(yh.stage_layer_paths(_StubStage([], explode=True)), [])

    def test_a_layer_that_refuses_is_skipped_the_others_are_kept(self):
        layers = [_StubLayer("/proj/a.usda", refs=["x.usda"], explode=True),
                  _StubLayer("/proj/b.usda")]
        # the exploding layer's own path is still collected (added before the refs);
        # only its unreadable dependency list is lost - the other layer is unaffected.
        self.assertEqual(yh.stage_layer_paths(_StubStage(layers)),
                         ["/proj/a.usda", "/proj/b.usda"])


class DependenciesFromPathsTestCase(unittest.TestCase):
    """dependencies_from_paths(): USD layer paths -> publish dependencies
    [{entity, step, version}] for finalize_publish_version(dependencies=...). Built on
    create_project._classify_project_path so Houdini never re-derives the project layout."""

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ylos_hou_deps_")).resolve()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        info = cp.create("Proj", root=self._tmp / "src", cache=self._tmp / "cache")
        self.project = Path(info["source"])
        for name, family, sub in (("CHARACTER_Lina_Default", "asset", "CHARACTER"),
                                  ("PROP_Barrel_Default", "asset", "PROP"),
                                  ("ANIMATION_Sq010_Default", "shot", "ANIMATION")):
            cp.create_asset(self.project, name, entity_type=family, asset_type=sub)

    def _publish_path(self, family, entity, step, version, ext=".usdnc"):
        stem = f"{entity}_{step}_v{version:03d}"
        return (self.project / family / entity / step / "publish" / stem / (stem + ext))

    def _set_step_publishes(self, family, entity, step_publishes):
        manifest_path = self.project / family / entity / cp.ASSET_MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest[cp.STEP_PUBLISHES_KEY] = step_publishes
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # -- path forms ---------------------------------------------------------------------

    def test_absolute_step_publish(self):
        path = self._publish_path("assets", "CHARACTER_Lina_Default", "modeling", 3)
        self.assertEqual(
            yh.dependencies_from_paths([str(path)], self.project),
            [{"entity": "CHARACTER_Lina_Default", "step": "modeling", "version": 3}])

    def test_proj_root_variable_is_expanded_both_syntaxes(self):
        # $PROJ_ROOT/<project> == project_root (the variable is the PARENT of the project
        # folder, see create_project._expand_proj_root and ylos_houdini.env_relative).
        rel = "assets/PROP_Barrel_Default/modeling/publish/" \
              "PROP_Barrel_Default_modeling_v002/PROP_Barrel_Default_modeling_v002.usdnc"
        expected = [{"entity": "PROP_Barrel_Default", "step": "modeling", "version": 2}]
        for form in ("$PROJ_ROOT", "${PROJ_ROOT}"):
            got = yh.dependencies_from_paths(
                [f"{form}/{self.project.name}/{rel}"], self.project)
            self.assertEqual(got, expected, form)

    def test_job_variable_is_expanded_both_syntaxes(self):
        rel = "assets/PROP_Barrel_Default/modeling/publish/" \
              "PROP_Barrel_Default_modeling_v001/PROP_Barrel_Default_modeling_v001.usdnc"
        expected = [{"entity": "PROP_Barrel_Default", "step": "modeling", "version": 1}]
        for form in ("$JOB", "${JOB}"):
            self.assertEqual(yh.dependencies_from_paths([f"{form}/{rel}"], self.project),
                             expected, form)

    def test_a_variable_merely_starting_with_job_is_not_expanded(self):
        # '$JOBS/...' is NOT '$JOB' + 'S/...': it stays an unexpanded variable and is
        # dropped, never rewritten into a half-resolved path.
        self.assertEqual(
            yh.dependencies_from_paths(["$JOBS/assets/X/modeling/publish/v001/x.usda"],
                                       self.project), [])

    def test_unexpanded_and_relative_paths_are_ignored(self):
        self.assertEqual(yh.dependencies_from_paths([
            "$PROJ_CACHE/Proj/houdini/X/fx/cache.bgeo.sc",   # cache tier, never a source dep
            "anon:0xdeadbeef:sim",                            # in-memory layer
            "some/relative/path.usda",
            "",
            None,
        ], self.project), [])

    def test_a_path_outside_the_project_is_ignored(self):
        outside = self._tmp / "elsewhere" / "assets" / "X" / "modeling" / "publish" \
            / "X_modeling_v001" / "X_modeling_v001.usda"
        self.assertEqual(yh.dependencies_from_paths([str(outside)], self.project), [])

    # -- pipeline rules -----------------------------------------------------------------

    def test_the_publishing_entity_is_never_its_own_dependency(self):
        own = self._publish_path("shots", "ANIMATION_Sq010_Default", "animation", 1)
        other = self._publish_path("assets", "CHARACTER_Lina_Default", "modeling", 1)
        got = yh.dependencies_from_paths([str(own), str(other)], self.project,
                                         exclude_entity="ANIMATION_Sq010_Default")
        self.assertEqual(got, [{"entity": "CHARACTER_Lina_Default",
                                "step": "modeling", "version": 1}])

    def test_a_lop_publish_is_recorded_under_the_lop_step(self):
        # a LOP publish is a complete snapshot outside the step taxonomy - it still has
        # to be recordable as a dependency (its 'step' is the reserved 'lop' folder).
        path = (self.project / "assets" / "CHARACTER_Lina_Default" / cp.LOP_DIR_NAME
                / cp.LOP_PUBLISH_DIR_NAME / "CHARACTER_Lina_Default_lop_v004"
                / "CHARACTER_Lina_Default_lop_v004.usdnc")
        self.assertEqual(
            yh.dependencies_from_paths([str(path)], self.project),
            [{"entity": "CHARACTER_Lina_Default", "step": cp.LOP_DIR_NAME, "version": 4}])

    def test_an_assembly_root_expands_into_the_steps_it_composes(self):
        # asset_root.usda is UNPINNED (always-latest): finalize needs a step, so the root
        # is resolved into the exact step publishes it composes right now.
        self._set_step_publishes("assets", "CHARACTER_Lina_Default", {
            "modeling": [
                {"version": 1, "status": "complete",
                 "artifact": "modeling/publish/CHARACTER_Lina_Default_modeling_v001/"
                             "CHARACTER_Lina_Default_modeling_v001.usda"},
                {"version": 2, "status": "complete",
                 "artifact": "modeling/publish/CHARACTER_Lina_Default_modeling_v002/"
                             "CHARACTER_Lina_Default_modeling_v002.usda"},
                {"version": 3, "status": "pending",
                 "artifact": "modeling/publish/CHARACTER_Lina_Default_modeling_v003/"
                             "CHARACTER_Lina_Default_modeling_v003.usda"},
            ],
            "lookdev": [
                {"version": 1, "status": "complete",
                 "artifact": "lookdev/publish/CHARACTER_Lina_Default_lookdev_v001/"
                             "CHARACTER_Lina_Default_lookdev_v001.usda"},
            ],
        })
        root = self.project / "assets" / "CHARACTER_Lina_Default" / cp.ASSET_ROOT_NAME
        got = yh.dependencies_from_paths([str(root)], self.project)
        self.assertEqual(
            sorted((d["step"], d["version"]) for d in got),
            [("lookdev", 1), ("modeling", 2)])   # 'pending' v003 never composed

    def test_an_assembly_root_is_dropped_when_expansion_is_disabled(self):
        root = self.project / "assets" / "CHARACTER_Lina_Default" / cp.ASSET_ROOT_NAME
        self.assertEqual(
            yh.dependencies_from_paths([str(root)], self.project, expand_roots=False), [])

    def test_the_publishing_entitys_own_root_is_never_expanded(self):
        root = (self.project / "shots" / "ANIMATION_Sq010_Default" / cp.SHOT_ROOT_NAME)
        self.assertEqual(
            yh.dependencies_from_paths([str(root)], self.project,
                                       exclude_entity="ANIMATION_Sq010_Default"), [])

    def test_duplicates_are_removed_and_order_preserved(self):
        first = self._publish_path("assets", "PROP_Barrel_Default", "modeling", 1)
        second = self._publish_path("assets", "CHARACTER_Lina_Default", "modeling", 1)
        got = yh.dependencies_from_paths(
            [str(first), str(second), str(first)], self.project)
        self.assertEqual([d["entity"] for d in got],
                         ["PROP_Barrel_Default", "CHARACTER_Lina_Default"])

    def test_result_is_accepted_by_finalize_publish_version(self):
        # the real contract: whatever this returns must survive
        # create_project._normalize_dependencies without raising.
        paths = [str(self._publish_path("assets", "CHARACTER_Lina_Default", "modeling", 2))]
        deps = yh.dependencies_from_paths(paths, self.project)
        self.assertEqual(cp._normalize_dependencies(deps), deps)


if __name__ == "__main__":
    unittest.main()
