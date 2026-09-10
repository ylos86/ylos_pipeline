# -*- coding: utf-8 -*-
"""Headless Blender test: the Scene Builder (ylos.create_scene, plan-usable-v1 Phase 1.3).
Realizes create_project.scene_starter_spec() into a fresh, department-contextualized
authoring scene saved as a versioned WIP .blend.

Covers:
  - asset/modeling starter: empty scene + department collections, no camera, saved at the
    allocated WIP v001, sidecar written, pipeline context stamped.
  - shot/animation starter: frame range from the manifest (schema 2.1) + a camera created.
  - non-destructive versioning: a second New Scene on the same entity/step saves v002.

Run:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --factory-startup --python tools/blender/test_create_scene_headless.py

Exit code != 0 on failure (assert / exception), 0 if everything passes.
"""
import os
import shutil
import sys
import tempfile
import traceback

_THIS = os.path.realpath(__file__)
REPO_ROOT = os.path.normpath(os.path.join(_THIS, "..", "..", ".."))  # tools/blender/.. -> repo
PLUGINS = os.path.join(REPO_ROOT, "plugins")
for p in (REPO_ROOT, PLUGINS):
    if p not in sys.path:
        sys.path.insert(0, p)


def _fail(msg, exc=None):
    print("FAIL:", msg)
    if exc is not None:
        traceback.print_exc()
    sys.exit(1)


def _set_context(scene, project_dir, entity, step, context_type):
    scene.ylos_project_path = project_dir
    scene.ylos_project_name = "SceneBuilderTest"
    scene.ylos_current_asset = entity
    scene.ylos_current_step = step
    scene.ylos_context_type = context_type


def main():
    import bpy
    import create_project as cp
    import blender as addon

    work = tempfile.mkdtemp(prefix="ylos_create_scene_test_")
    try:
        root = os.path.join(work, "src")
        cache = os.path.join(work, "cache")
        os.makedirs(root)
        os.makedirs(cache)

        proj = cp.create("SceneBuilderTest", root=root, cache=cache, prod_type="FILM")
        project_dir = str(proj["source"])
        asset = "CHARACTER_Lina_Default"
        shot = "ANIMATION_Sq010_Default"
        cp.create_asset(project_dir, asset, entity_type="asset", asset_type="CHARACTER")
        cp.create_asset(project_dir, shot, entity_type="shot", asset_type="ANIMATION")

        try:
            addon.register()
        except Exception as e:
            _fail("addon.register() raised", e)
        print("ok  addon.register() without exception")

        # --- 1. asset / modeling starter ---
        scene = bpy.context.scene
        _set_context(scene, project_dir, asset, "modeling", "ASSET")

        spec = cp.scene_starter_spec(asset, "modeling", dcc="blender", project_root=project_dir)
        if not spec.get("ok"):
            _fail(f"spec not ok for asset/modeling: {spec.get('reason')}")
        expected_v1 = spec["wip"]["path"]

        res = bpy.ops.ylos.create_scene('EXEC_DEFAULT')
        if res != {"FINISHED"}:
            _fail(f"ylos.create_scene (asset/modeling) returned {res} (expected FINISHED)")

        if not os.path.isfile(expected_v1):
            _fail(f"WIP .blend not saved at {expected_v1}")
        if not os.path.isfile(expected_v1 + ".json"):
            _fail(f"sidecar not written at {expected_v1}.json")
        if not expected_v1.endswith("_modeling_v001.blend"):
            _fail(f"expected v001 for a fresh step, got {os.path.basename(expected_v1)}")
        print(f"ok  asset/modeling -> {os.path.basename(expected_v1)} + sidecar")

        # Scene stamped with the pipeline context (built scene is the active one).
        scene = bpy.context.scene
        if scene.ylos_current_asset != asset:
            _fail(f"context not stamped: ylos_current_asset={scene.ylos_current_asset!r}")
        if scene.ylos_context_type != "ASSET":
            _fail(f"context_type not stamped: {scene.ylos_context_type!r}")
        if scene.name != f"SCENE_{asset}_modeling":
            _fail(f"scene name not set: {scene.name!r}")
        # Department collections present, no camera for a modeling starter.
        if "COL_CAM" not in bpy.data.collections or "COL_WORLD" not in bpy.data.collections:
            _fail("department collections (COL_WORLD/COL_CAM) not created")
        if any(o.type == "CAMERA" for o in scene.objects):
            _fail("asset/modeling starter should not create a camera")
        print("ok  asset/modeling: context stamped, department collections, no camera")

        # --- 2. shot / animation starter: frame range + camera ---
        scene = bpy.context.scene
        _set_context(scene, project_dir, shot, "animation", "SHOT")
        res = bpy.ops.ylos.create_scene('EXEC_DEFAULT')
        if res != {"FINISHED"}:
            _fail(f"ylos.create_scene (shot/animation) returned {res} (expected FINISHED)")

        scene = bpy.context.scene
        if scene.frame_start != 1001 or scene.frame_end != 1100:
            _fail(f"frame range not applied: {scene.frame_start}-{scene.frame_end} (expected 1001-1100)")
        cams = [o for o in scene.objects if o.type == "CAMERA"]
        if not cams:
            _fail("shot/animation starter did not create a camera")
        if scene.camera is None:
            _fail("scene.camera not set on the shot starter")
        print(f"ok  shot/animation: frame range 1001-1100 + camera ({cams[0].name})")

        # --- 3. non-destructive versioning: second New Scene -> v002 ---
        scene = bpy.context.scene
        _set_context(scene, project_dir, asset, "modeling", "ASSET")
        spec2 = cp.scene_starter_spec(asset, "modeling", dcc="blender", project_root=project_dir)
        if spec2["wip"]["version"] != 2:
            _fail(f"second starter should allocate v002, got v{spec2['wip']['version']:03d}")
        expected_v2 = spec2["wip"]["path"]
        res = bpy.ops.ylos.create_scene('EXEC_DEFAULT')
        if res != {"FINISHED"}:
            _fail(f"second ylos.create_scene returned {res} (expected FINISHED)")
        if not os.path.isfile(expected_v2):
            _fail(f"v002 .blend not saved at {expected_v2}")
        if not os.path.isfile(expected_v1):
            _fail("v001 was overwritten — starter must be non-destructive")
        print("ok  second New Scene -> v002, v001 preserved (non-destructive)")

        try:
            addon.unregister()
        except Exception as e:
            _fail("addon.unregister() raised", e)
        print("ok  addon.unregister() without exception")

        print("\nPASS: Scene Builder (ylos.create_scene) headless OK")
        sys.exit(0)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _fail("unexpected exception", e)
