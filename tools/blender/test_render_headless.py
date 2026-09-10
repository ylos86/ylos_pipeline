# -*- coding: utf-8 -*-
"""Headless Blender test: the Render operator (ylos.render, Prism-parity feature 3).
Renders the active entity+step over the frame range to a versioned EXR sequence in the
CACHE render tier (same location Houdini writes), realizing create_project.render_spec().

Covers:
  - a render writes v001 EXR frames into cache/<project>/render/<entity>/<step>/v001/,
    output settings restored afterwards (no leak into the scene).
  - a render with no camera is refused (CANCELLED) with a clear message.
  - non-destructive versioning: a second render writes v002, v001 preserved.

Run:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --factory-startup --python tools/blender/test_render_headless.py

Exit code != 0 on failure (assert / exception), 0 if everything passes.
"""
import glob
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


def main():
    import bpy
    import create_project as cp
    import blender as addon

    work = tempfile.mkdtemp(prefix="ylos_render_test_")
    try:
        root = os.path.join(work, "src")
        cache = os.path.join(work, "cache")
        os.makedirs(root)
        os.makedirs(cache)
        # render_spec resolves the cache tier via $PROJ_CACHE (same as prod / Houdini).
        os.environ[cp.ENV_CACHE] = cache

        proj = cp.create("RenderTest", root=root, cache=cache, prod_type="FILM")
        project_dir = str(proj["source"])
        entity = "CHARACTER_Lina_Default"
        cp.create_asset(project_dir, entity, entity_type="asset", asset_type="CHARACTER")

        try:
            addon.register()
        except Exception as e:
            _fail("addon.register() raised", e)
        print("ok  addon.register() without exception")

        scene = bpy.context.scene
        scene.ylos_project_path  = project_dir
        scene.ylos_project_name  = "RenderTest"
        scene.ylos_current_asset = entity
        scene.ylos_current_step  = "lookdev"
        scene.ylos_context_type  = "ASSET"

        bpy.ops.mesh.primitive_cube_add(size=2.0)
        # Fast, deterministic render for the test — the op uses whatever engine the scene has.
        scene.render.engine = "BLENDER_WORKBENCH"
        scene.render.resolution_x = 128
        scene.render.resolution_y = 128
        scene.frame_start = 1
        scene.frame_end = 1

        # --- no camera -> refused ---
        # An operator that reports {'ERROR'} + returns {'CANCELLED'} makes bpy.ops raise
        # RuntimeError (native API behavior, not a crash to swallow) — assert on that.
        scene.camera = None
        try:
            bpy.ops.ylos.render('EXEC_DEFAULT')
            _fail("render without a camera should have been refused")
        except RuntimeError as e:
            if "camera" not in str(e).lower():
                _fail(f"render/no-camera: unexpected message: {e}")
        print("ok  render without a camera refused")

        # Add a camera for the real render.
        cam_data = bpy.data.cameras.new("CAM_Main_A")
        cam = bpy.data.objects.new("CAM_Main_A", cam_data)
        scene.collection.objects.link(cam)
        cam.location = (7.36, -6.93, 4.96)
        cam.rotation_euler = (1.109, 0.0, 0.815)
        scene.camera = cam

        # --- 1. first render -> v001 in the cache render tier ---
        spec1 = cp.render_spec(entity, "lookdev", project_root=project_dir, ext="exr")
        if not spec1.get("ok"):
            _fail(f"render_spec not ok: {spec1.get('reason')}")
        vdir1 = spec1["version_dir"]
        if "/cache/" not in vdir1.replace("\\", "/") or "/render/" not in vdir1.replace("\\", "/"):
            _fail(f"render output not in the cache render tier: {vdir1}")

        saved_fmt = scene.render.image_settings.file_format
        res = bpy.ops.ylos.render('EXEC_DEFAULT')
        if res != {"FINISHED"}:
            _fail(f"ylos.render returned {res} (expected FINISHED)")

        frames1 = glob.glob(os.path.join(vdir1, "*.exr"))
        if len(frames1) != 1:
            _fail(f"expected 1 EXR frame, got {len(frames1)}: {frames1}")
        if os.path.getsize(frames1[0]) <= 0:
            _fail("render frame is empty")
        print(f"ok  render -> v001/{os.path.basename(frames1[0])} (cache render tier)")

        # Output settings restored (file_format not leaked).
        if scene.render.image_settings.file_format != saved_fmt:
            _fail(f"render leaked file_format ({scene.render.image_settings.file_format} != {saved_fmt})")
        print("ok  output settings restored (no leak)")

        # --- 2. second render -> v002, v001 preserved ---
        spec2 = cp.render_spec(entity, "lookdev", project_root=project_dir, ext="exr")
        if spec2["version"] != 2:
            _fail(f"second render should allocate v002, got v{spec2['version']:03d}")
        vdir2 = spec2["version_dir"]
        scene.frame_start = 1
        scene.frame_end = 1
        res = bpy.ops.ylos.render('EXEC_DEFAULT')
        if res != {"FINISHED"}:
            _fail(f"second ylos.render returned {res} (expected FINISHED)")
        if not glob.glob(os.path.join(vdir2, "*.exr")):
            _fail(f"v002 render not written at {vdir2}")
        if not glob.glob(os.path.join(vdir1, "*.exr")):
            _fail("v001 render was overwritten — must be non-destructive")
        print("ok  second render -> v002, v001 preserved")

        try:
            addon.unregister()
        except Exception as e:
            _fail("addon.unregister() raised", e)
        print("ok  addon.unregister() without exception")

        print("\nPASS: Render (ylos.render) headless OK")
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
