# -*- coding: utf-8 -*-
"""Headless Blender test: pipeline USD publishes follow docs/usd-convention.md.

Convention asserted (frozen 2026-06-15, "up axis to verify at usage" now verified):
  * upAxis        = "Y"                        (create_project.USD_UP_AXIS)
  * metersPerUnit = 1                          (create_project.USD_METERS_PER_UNIT)
  * defaultPrim   = "<EntityName>" for an asset/set publish, "ROOT" for a shot
    (create_project.USD_ROOT_PRIM) - so the step layers stack on the same prim, and an
    asset referenced into a set lands correctly named.
  * a bare '.usd' is banned -> an offline-target publish writes '.usdc'.
  * the ROUND-TRIP is lossless: re-importing the published stage restores the source's
    world bounding box exactly (a silent axis swap is precisely what this catches).

Blender 5.2 facts behind the implementation (probed live, see core/usd_convention.py):
convert_orientation=True + up 'Y' + forward 'NEGATIVE_Z' writes upAxis="Y" and bakes the
conversion as xformOp:rotateXYZ = (-90, 0, 0) on the root prim (mesh points stay in Blender
space); wm.usd_import re-absorbs it (merge_parent_xform) and applies metersPerUnit
(apply_unit_conversion_scale).

The published artifact is a CRATE (.usdc): its header cannot be read as text. The test
therefore exports a sibling '.usda' with the IDENTICAL kwargs (core.usd_convention is the
single source of those kwargs, so the two files cannot drift) and reads the header there.

Run:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --python tools/blender/test_usd_convention_headless.py

Exit code != 0 on failure (assert / exception), 0 if everything passes.
"""
import json
import os
import re
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

# Deliberately distinct on all three axes: a Y/Z swap or a sign flip changes the numbers.
BOX_SCALE = (2.0, 0.5, 1.25)
BOX_LOCATION = (0.0, 0.0, 1.25)
TOL = 1e-4


def _fail(msg, exc=None):
    print("FAIL:", msg)
    if exc is not None:
        traceback.print_exc()
    sys.exit(1)


def _world_bbox(objects):
    from mathutils import Vector
    pts = [o.matrix_world @ Vector(c) for o in objects for c in o.bound_box]
    if not pts:
        return None
    return (
        (min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)),
        (max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)),
    )


def _bbox_close(a, b):
    return all(abs(x - y) <= TOL for pair in zip(a, b) for x, y in zip(pair[0], pair[1]))


def _stage_header(path):
    """The '(...)' metadata block at the top of a .usda file."""
    text = open(path, encoding="utf-8", errors="replace").read()
    m = re.search(r"^\(\n(.*?)^\)", text, re.S | re.M)
    return m.group(1) if m else text[:800]


def main():
    import bpy
    import create_project as cp
    import blender as addon
    from blender.core import usd_convention

    try:
        addon.register()
    except Exception as e:
        _fail("addon.register() raised", e)

    work = tempfile.mkdtemp(prefix="ylos_usd_conv_")
    try:
        root = os.path.join(work, "src")
        cache = os.path.join(work, "cache")
        os.makedirs(root)
        os.makedirs(cache)

        # FILM -> pipeline target 'offline' -> USD artifact (the case under test).
        proj = cp.create("UsdConvProj", root=root, cache=cache, prod_type="FILM")
        project_dir = str(proj["source"])
        entity = "PROP_Box_Default"
        cp.create_asset(project_dir, entity, entity_type="asset", asset_type="PROP")
        step = "modeling"

        # ---- root_prim_path per family (pure, before touching any file) -----------------
        if usd_convention.root_prim_path(entity, "asset") != "/" + entity:
            _fail(f"asset root prim = {usd_convention.root_prim_path(entity, 'asset')!r}")
        if usd_convention.root_prim_path("SET_Yard_Default", "set") != "/SET_Yard_Default":
            _fail("a set must be rooted under its own name (it composes as an asset root)")
        if usd_convention.root_prim_path("SHOT_Sq010_Default", "shot") != cp.USD_ROOT_PRIM:
            _fail(f"a shot must be rooted at {cp.USD_ROOT_PRIM} (shot_root.usda authors /ROOT), "
                  f"got {usd_convention.root_prim_path('SHOT_Sq010_Default', 'shot')!r}")
        print(f"ok  root prim per family: asset/set -> /<Entity>, shot -> {cp.USD_ROOT_PRIM}")

        # ---- Source scene: one box with distinct X/Y/Z dimensions -----------------------
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()
        bpy.ops.mesh.primitive_cube_add(size=1.0)
        box = bpy.context.active_object
        box.name = f"GEO_{entity}_body"
        box.scale = BOX_SCALE
        box.location = BOX_LOCATION
        coll = bpy.data.collections.new(entity)
        bpy.context.scene.collection.children.link(coll)
        for c in list(box.users_collection):
            c.objects.unlink(box)
        coll.objects.link(box)
        bpy.context.view_layer.update()

        source_bbox = _world_bbox([box])
        print(f"ok  source box world bbox = {source_bbox}")

        scene = bpy.context.scene
        scene.ylos_project_path = project_dir
        scene.ylos_project_name = "UsdConvProj"
        scene.ylos_prod_type = "FILM"
        scene.ylos_context_type = "ASSET"
        scene.ylos_asset_type = "PROP"
        scene.ylos_current_asset = entity
        scene.ylos_current_step = step

        # ---- Publish through the REAL operator path -------------------------------------
        res = bpy.ops.ylos.publish('EXEC_DEFAULT', step=step, allow_full_scene=False)
        if res != {"FINISHED"}:
            _fail(f"ylos.publish did not FINISH: {res}")

        manifest = json.loads(open(
            os.path.join(project_dir, "assets", entity, "manifest.json"), encoding="utf-8").read())
        entries = [e for e in manifest.get("step_publishes", {}).get(step, [])
                   if e.get("status") == "complete"]
        if not entries:
            _fail(f"no complete publish in manifest: {manifest.get('step_publishes')}")
        artifact_rel = entries[-1]["artifact"]
        if not artifact_rel.endswith(".usdc"):
            _fail(f"offline publish must be '.usdc' (a bare '.usd' is banned by "
                  f"docs/usd-convention.md §2), got {artifact_rel!r}")
        published = os.path.join(project_dir, "assets", entity, artifact_rel)
        if not os.path.isfile(published) or os.path.getsize(published) <= 0:
            _fail(f"published artifact missing or empty: {published}")
        with open(published, "rb") as fh:
            magic = fh.read(8)
        if magic != b"PXR-USDC":
            _fail(f"'.usdc' is not a crate file (magic {magic!r})")
        print(f"ok  publish wrote a real USD crate: {artifact_rel}")

        # ---- Sibling .usda with the IDENTICAL kwargs, to read the header ---------------
        sibling = os.path.join(work, f"{entity}_{step}_header.usda")
        for o in bpy.context.scene.objects:
            o.select_set(False)
        box.select_set(True)
        bpy.context.view_layer.objects.active = box
        kwargs = usd_convention.export_kwargs(
            entity_name=entity, family="asset", selected_only=True,
        )
        try:
            bpy.ops.wm.usd_export(filepath=sibling, **kwargs)
        except Exception as e:
            _fail(f"sibling .usda export raised with the convention kwargs {kwargs!r}", e)

        header = _stage_header(sibling)
        if not re.search(r'upAxis\s*=\s*"Y"', header):
            _fail(f'upAxis = "Y" missing from the stage header:\n{header}')
        if not re.search(rf'defaultPrim\s*=\s*"{re.escape(entity)}"', header):
            _fail(f'defaultPrim = "{entity}" missing from the stage header:\n{header}')
        if not re.search(r"metersPerUnit\s*=\s*1\b", header):
            _fail(f"metersPerUnit = 1 missing from the stage header:\n{header}")
        print(f'ok  stage header: upAxis = "Y", defaultPrim = "{entity}", metersPerUnit = 1')

        # The root prim must really exist in the layer, not just be declared as defaultPrim.
        text = open(sibling, encoding="utf-8").read()
        if not re.search(rf'def \w+ "{re.escape(entity)}"', text):
            _fail(f"the layer declares no '/{entity}' root prim:\n{text[:800]}")
        print(f"ok  the layer authors its content under /{entity}")

        # ---- Round-trip: re-import the PUBLISHED .usdc ----------------------------------
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()
        for c in list(bpy.data.collections):
            bpy.data.collections.remove(c)
        try:
            bpy.ops.wm.usd_import(filepath=published, **usd_convention.import_kwargs())
        except Exception as e:
            _fail("re-import of the published .usdc raised", e)
        bpy.context.view_layer.update()

        meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
        if not meshes:
            _fail("re-import produced no mesh")
        imported_bbox = _world_bbox(meshes)
        if not _bbox_close(source_bbox, imported_bbox):
            _fail(f"round-trip bbox mismatch (axis swap / unit scale):\n"
                  f"  source   = {source_bbox}\n  imported = {imported_bbox}")
        print(f"ok  round-trip bbox identical to the source ({imported_bbox})")

        try:
            addon.unregister()
        except Exception as e:
            _fail("addon.unregister() raised", e)

        print("\nPASS: USD publishes are Y-up, metersPerUnit 1, rooted at the entity, "
              "and round-trip losslessly")
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
