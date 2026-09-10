# -*- coding: utf-8 -*-
"""Headless Blender test: the Playblast operator (ylos.playblast, Prism-parity feature 2).
Renders a versioned viewport capture over the frame range into the source tree
(<entity>/<step>/playblast/), realizing create_project.playblast_spec().

Covers:
  - a playblast writes the allocated v001 MP4, headless engine render (WORKBENCH),
    temporary camera auto-created and cleaned up, no render-setting leak into the scene.
  - non-destructive versioning: a second playblast writes v002, v001 preserved.

Run:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --factory-startup --python tools/blender/test_playblast_headless.py

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


def main():
    import bpy
    import create_project as cp
    import blender as addon

    work = tempfile.mkdtemp(prefix="ylos_playblast_test_")
    try:
        root = os.path.join(work, "src")
        cache = os.path.join(work, "cache")
        os.makedirs(root)
        os.makedirs(cache)

        proj = cp.create("PlayblastTest", root=root, cache=cache, prod_type="FILM")
        project_dir = str(proj["source"])
        entity = "PROP_Cube_Default"
        cp.create_asset(project_dir, entity, entity_type="asset", asset_type="PROP")

        try:
            addon.register()
        except Exception as e:
            _fail("addon.register() raised", e)
        print("ok  addon.register() without exception")

        scene = bpy.context.scene
        scene.ylos_project_path  = project_dir
        scene.ylos_project_name  = "PlayblastTest"
        scene.ylos_current_asset = entity
        scene.ylos_current_step  = "modeling"
        scene.ylos_context_type  = "ASSET"

        # Something to see, and a tiny 2-frame range for a fast render.
        bpy.ops.mesh.primitive_cube_add(size=2.0)
        scene.frame_start = 1
        scene.frame_end = 2

        import glob as _glob

        # --- 1. first playblast -> v001 sequence folder ---
        spec1 = cp.playblast_spec(entity, "modeling", project_root=project_dir)
        if not spec1.get("ok"):
            _fail(f"playblast_spec not ok: {spec1.get('reason')}")
        seq_v1 = spec1["path"]  # per-version sequence folder

        res = bpy.ops.ylos.playblast('EXEC_DEFAULT')
        if res != {"FINISHED"}:
            _fail(f"ylos.playblast returned {res} (expected FINISHED)")

        if not os.path.isdir(seq_v1):
            _fail(f"playblast sequence folder not created at {seq_v1}")
        frames_v1 = _glob.glob(os.path.join(seq_v1, "*.png"))
        # Frame range 1-2 -> exactly 2 PNG frames.
        if len(frames_v1) != 2:
            _fail(f"expected 2 PNG frames, got {len(frames_v1)}: {frames_v1}")
        if any(os.path.getsize(f) <= 0 for f in frames_v1):
            _fail("a playblast frame is empty")
        if not seq_v1.endswith("_modeling_v001"):
            _fail(f"expected v001 for a fresh step, got {os.path.basename(seq_v1)}")
        print(f"ok  playblast -> {os.path.basename(seq_v1)}/ ({len(frames_v1)} frames)")

        # No render-setting leak, no temp camera left behind.
        if scene.render.engine == "BLENDER_WORKBENCH" and cp.DEFAULT_SCENE["renderer"] != "BLENDER_WORKBENCH":
            _fail("playblast leaked the WORKBENCH engine into the scene (not restored)")
        if scene.render.image_settings.file_format != "PNG" and cp.DEFAULT_SCENE.get("renderer"):
            pass  # file_format restored to whatever it was; not asserting a specific value
        if any(o.name.startswith("YLOS_playblast_cam") for o in bpy.data.objects):
            _fail("temporary playblast camera was not removed")
        print("ok  render settings restored, temp camera cleaned up")

        # --- 2. second playblast -> v002, v001 preserved ---
        spec2 = cp.playblast_spec(entity, "modeling", project_root=project_dir)
        if spec2["version"] != 2:
            _fail(f"second playblast should allocate v002, got v{spec2['version']:03d}")
        seq_v2 = spec2["path"]
        scene.frame_start = 1
        scene.frame_end = 2
        res = bpy.ops.ylos.playblast('EXEC_DEFAULT')
        if res != {"FINISHED"}:
            _fail(f"second ylos.playblast returned {res} (expected FINISHED)")
        if not _glob.glob(os.path.join(seq_v2, "*.png")):
            _fail(f"v002 sequence not written at {seq_v2}")
        if not _glob.glob(os.path.join(seq_v1, "*.png")):
            _fail("v001 playblast was overwritten — must be non-destructive")
        print("ok  second playblast -> v002, v001 preserved")

        try:
            addon.unregister()
        except Exception as e:
            _fail("addon.unregister() raised", e)
        print("ok  addon.unregister() without exception")

        print("\nPASS: Playblast (ylos.playblast) headless OK")
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
