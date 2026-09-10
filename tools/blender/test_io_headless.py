# -*- coding: utf-8 -*-
"""Headless Blender test: the Import / Export panel (ylos.open_io) and its operators.
  - Product Browser: compute_products lists the 'complete' publishes, draw_io() renders
    without exception (populated browser).
  - Raw file I/O: OBJ roundtrip (export selection -> non-empty file -> import -> more
    objects). OBJ is core (no addon required, unlike FBX).

Run:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --factory-startup --python tools/blender/test_io_headless.py

Exit code != 0 on failure, 0 if everything passes. Outside the stdlib CI (requires Blender).
"""
import os
import shutil
import sys
import tempfile
import traceback
import types

_THIS = os.path.realpath(__file__)
REPO_ROOT = os.path.normpath(os.path.join(_THIS, "..", "..", ".."))
PLUGINS = os.path.join(REPO_ROOT, "plugins")
for p in (REPO_ROOT, PLUGINS):
    if p not in sys.path:
        sys.path.insert(0, p)


def _fail(msg, exc=None):
    print("FAIL:", msg)
    if exc is not None:
        traceback.print_exc()
    sys.exit(1)


class _FakeOp:
    def __setattr__(self, k, v):
        object.__setattr__(self, k, v)


class _FakeLayout:
    def __getattr__(self, name):
        if name == "operator":
            return lambda *a, **k: _FakeOp()
        return lambda *a, **k: _FakeLayout()


def main():
    import bpy
    import create_project as cp
    import blender as addon
    from blender.operators import op_io
    from blender.ui import io_panel

    work = tempfile.mkdtemp(prefix="ylos_io_test_")
    try:
        root = os.path.join(work, "src")
        cache = os.path.join(work, "cache")
        os.makedirs(root)
        os.makedirs(cache)

        proj = cp.create("IOTest", root=root, cache=cache, prod_type="FILM")
        project_dir = str(proj["source"])
        entity = "PROP_Box_Default"
        cp.create_asset(project_dir, entity, entity_type="asset", asset_type="PROP")

        try:
            addon.register()
        except Exception as e:
            _fail("addon.register() raised", e)

        scene = bpy.context.scene
        scene.ylos_project_path = project_dir
        scene.ylos_project_name = "IOTest"
        scene.ylos_prod_type = "FILM"
        scene.ylos_context_type = "ASSET"
        scene.ylos_asset_type = "PROP"
        scene.ylos_current_asset = entity
        scene.ylos_current_step = "modeling"

        # --- draw EMPTY browser (no publish) ---
        op_io.refresh_products(project_dir, "asset")
        try:
            io_panel.draw_io(_FakeLayout(), bpy.context)
        except Exception as e:
            _fail("draw_io() raised (empty browser)", e)
        print("ok  draw_io() : empty browser without exception")

        # --- publish a product -> populated browser ---
        bpy.ops.mesh.primitive_cube_add(size=2.0)
        res = bpy.ops.ylos.publish('EXEC_DEFAULT', step="modeling", allow_full_scene=True)
        if res != {"FINISHED"}:
            _fail(f"ylos.publish returned {res} (expected FINISHED)")

        rows = op_io.refresh_products(project_dir, "asset")
        if not any(r["entity"] == entity and r["step"] == "modeling" and r["version"] == 1
                   for r in rows):
            _fail(f"compute_products did not find the published product: {rows}")
        print(f"ok  compute_products -> {len(rows)} product(s), modeling v1 present")

        try:
            io_panel.draw_io(_FakeLayout(), bpy.context)
        except Exception as e:
            _fail("draw_io() raised (populated browser)", e)
        print("ok  draw_io() : populated browser without exception")

        if not hasattr(bpy.types, "YLOS_OT_open_io"):
            _fail("ylos.open_io not registered")
        print("ok  ylos.open_io registered")

        # --- raw OBJ roundtrip (core, no addon) ---
        for o in bpy.data.objects:
            o.select_set(o.type == "MESH")
        obj_path = os.path.join(work, "out.obj")
        res = bpy.ops.ylos.raw_export(fmt="OBJ", filepath=obj_path)
        if res != {"FINISHED"}:
            _fail(f"ylos.raw_export returned {res} (expected FINISHED)")
        if not (os.path.isfile(obj_path) and os.path.getsize(obj_path) > 0):
            _fail(f"exported OBJ missing or empty: {obj_path}")
        print(f"ok  raw_export OBJ -> {os.path.getsize(obj_path)} bytes")

        before = len(bpy.data.objects)
        res = bpy.ops.ylos.raw_import(fmt="OBJ", filepath=obj_path)
        if res != {"FINISHED"}:
            _fail(f"ylos.raw_import returned {res} (expected FINISHED)")
        after = len(bpy.data.objects)
        if after <= before:
            _fail(f"raw_import OBJ added nothing: {before} -> {after}")
        print(f"ok  raw_import OBJ : objects {before} -> {after}")

        try:
            addon.unregister()
        except Exception as e:
            _fail("addon.unregister() raised", e)
        print("ok  addon.unregister() without exception")

        print("\nPASS: Import / Export panel (Product Browser + raw OBJ roundtrip) headless OK")
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
