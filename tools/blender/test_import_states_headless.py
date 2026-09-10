# -*- coding: utf-8 -*-
"""Headless Blender test: import states (State-Manager-lite, INC-5) - op_import_product.py
(absorbs op_load_publish.py) + op_update_imports.py (ylos.check_updates/ylos.update_import).

Exact scenario from the spec: publish a cube v1, import it (tagged props placed), publish
v2 (with one more object, to check the N-objects-before/after report), check_updates
detects the available update, update replaces the collection's content.

Run:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --python tools/blender/test_import_states_headless.py

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


def _move_to_collection(obj, target_collection):
    """Moves 'obj' into 'target_collection', whatever its original collection
    (never any assumption about 'scene.collection' specifically - the active collection at
    the time of primitive_add depends on the view_layer state, not guaranteed)."""
    for coll in list(obj.users_collection):
        coll.objects.unlink(obj)
    target_collection.objects.link(obj)


def main():
    import bpy
    import create_project as cp
    import blender as addon
    from blender.operators.op_import_product import import_state_collection_name
    from blender.operators.op_update_imports import get_cached_update_results

    work = tempfile.mkdtemp(prefix="ylos_import_states_test_")
    try:
        root = os.path.join(work, "src")
        cache = os.path.join(work, "cache")
        os.makedirs(root)
        os.makedirs(cache)

        # Web target (GLB) - complementary to the USD path already tested elsewhere
        # (test_publish_glb_headless / test_launch_context invocation A).
        proj = cp.create("ImportStatesTest", root=root, cache=cache, prod_type="XR")
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
        scene.ylos_project_name  = "ImportStatesTest"
        scene.ylos_current_asset = entity
        scene.ylos_current_step  = "modeling"
        scene.ylos_context_type  = "ASSET"

        # --- 1. Publish v1 (a single cube) ---
        # The objects to publish live in a collection named EXACTLY like the entity
        # (see core/scene_checker.get_asset_objects_for_publish): publish stays scoped to
        # that collection, never 'allow_full_scene' - which would also sweep up the import
        # collection (different name, created at step 2) during the v2 publish.
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()
        src_collection = bpy.data.collections.new(entity)
        scene.collection.children.link(src_collection)

        bpy.ops.mesh.primitive_cube_add(size=2.0)
        _move_to_collection(bpy.context.active_object, src_collection)

        res = bpy.ops.ylos.publish('EXEC_DEFAULT', step="modeling", load_after=False)
        if res != {"FINISHED"}:
            _fail(f"publish v1 returned {res}")
        print("ok  publish v1 (1 object, scoped source collection)")

        # --- 2. Import (tagged props placed) ---
        res = bpy.ops.ylos.import_product(
            'EXEC_DEFAULT', entity=entity, step="modeling", version=0,  # 0 = latest
        )
        if res != {"FINISHED"}:
            _fail(f"import_product returned {res}")

        col_name = import_state_collection_name(entity, "modeling")
        collection = bpy.data.collections.get(col_name)
        if collection is None:
            _fail(f"collection '{col_name}' not created")

        expected_tags = {
            "ylos_import_entity": entity,
            "ylos_import_step": "modeling",
            "ylos_import_version": 1,
        }
        for key, val in expected_tags.items():
            if collection.get(key) != val:
                _fail(f"tag {key!r} : {collection.get(key)!r} != {val!r}")
        if not collection.get("ylos_import_path", "").endswith(".glb"):
            _fail(f"unexpected ylos_import_path: {collection.get('ylos_import_path')!r}")
        n_v1 = len(collection.objects)
        if n_v1 != 1:
            _fail(f"collection v1: {n_v1} object(s) != 1")
        print(f"ok  import_product : collection '{col_name}' tagged v001, {n_v1} object(s)")

        # Re-importing the same entity/step must fail (already imported -> Update Import).
        # bpy.ops (unlike a direct execute() call) raises RuntimeError when the operator
        # reports {'ERROR'} + returns {'CANCELLED'} - native API behavior, not an exception
        # to swallow silently.
        try:
            bpy.ops.ylos.import_product(
                'EXEC_DEFAULT', entity=entity, step="modeling", version=0,
            )
            _fail("re-import on an existing collection did not raise (should be refused)")
        except RuntimeError as e:
            if "already imported" not in str(e):
                _fail(f"re-import: unexpected message: {e}")
        print("ok  re-import on an existing collection refused (use Update Import)")

        # --- 3. Publish v2 (two objects in the source collection, for a non-trivial
        #        before/after report - the import collection created at step 2 stays
        #        out of scope, different name) ---
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(3, 0, 0))
        _move_to_collection(bpy.context.active_object, src_collection)

        res = bpy.ops.ylos.publish('EXEC_DEFAULT', step="modeling", load_after=False)
        if res != {"FINISHED"}:
            _fail(f"publish v2 returned {res}")
        print("ok  publish v2 (2 objects, scoped source collection)")

        # --- 4. check_updates detects the update ---
        res = bpy.ops.ylos.check_updates('EXEC_DEFAULT')
        if res != {"FINISHED"}:
            _fail(f"check_updates returned {res}")

        cache = get_cached_update_results()
        status = cache.get(col_name)
        if status is None:
            _fail(f"check_updates: '{col_name}' missing from the cache: {cache!r}")
        if not status["has_update"] or status["current"] != 1 or status["latest"] != 2:
            _fail(f"check_updates: unexpected state {status!r}")
        print(f"ok  check_updates : {status!r}")

        # --- 5. update_import replaces the content ---
        res = bpy.ops.ylos.update_import('EXEC_DEFAULT', collection_name=col_name)
        if res != {"FINISHED"}:
            _fail(f"update_import returned {res}")

        if collection.get("ylos_import_version") != 2:
            _fail(f"after update, tagged version = {collection.get('ylos_import_version')!r} != 2")
        n_v2 = len(collection.objects)
        if n_v2 != 2:
            _fail(f"collection after update: {n_v2} object(s) != 2 (v1 had {n_v1})")
        print(f"ok  update_import : v001 -> v002, {n_v1} -> {n_v2} objects")

        # Update while already at the latest version -> no-op FINISHED (no error).
        res = bpy.ops.ylos.update_import('EXEC_DEFAULT', collection_name=col_name)
        if res != {"FINISHED"}:
            _fail(f"update_import (already up to date) returned {res}")
        if len(collection.objects) != 2:
            _fail("update_import (already up to date) modified the collection")
        print("ok  update_import (already up to date): clean no-op")

        try:
            addon.unregister()
        except Exception as e:
            _fail("addon.unregister() raised", e)
        print("ok  addon.unregister() without exception")

        print("\nPASS: import states (import_product + check_updates + update_import) headless OK")
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
