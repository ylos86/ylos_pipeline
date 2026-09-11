# -*- coding: utf-8 -*-
"""Headless Blender test: per-step production status (schema 2.2) on the Blender side.

What is asserted:
  A. vocab.STEP_STATUS_ITEMS is a MODULE-LEVEL tuple (bpy GC trap, see core/vocab.py) whose
     domain is exactly create_project.STEP_STATUS_AUTO + STEP_STATUS_EXPLICIT - the derived
     statuses (empty/wip/published) are never settable.
  B. ylos.set_step_status persists an explicit status and the addon reads it back through
     create_project.get_step_status (single source) - including the cache purge, without
     which the panel would show the old value for the whole TTL.
  C. 'auto' CLEARS the explicit status and the step falls back to the derived one.
  D. Business errors are reported, not raised: an undeclared step is CANCELLED and the
     manifest is left untouched.
  E. The derived status tracks the disk: empty -> wip (a saved scenefile) -> published
     (a complete publish), read through the addon's adapter.

Run:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --python tools/blender/test_step_status_headless.py

Exit code != 0 on failure (assert / exception), 0 if everything passes.
"""
import json
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


def _manifest(project_dir, entity):
    path = os.path.join(project_dir, "assets", entity, "manifest.json")
    return json.loads(open(path, encoding="utf-8").read())


def main():
    import bpy
    import create_project as cp
    import blender as addon
    from blender.core import vocab
    from blender.core import asset as asset_core

    # ---- A. Enum domain (no bpy needed, but it must hold with the addon importable) -----
    if not isinstance(vocab.STEP_STATUS_ITEMS, tuple):
        _fail(f"STEP_STATUS_ITEMS must be a module-level TUPLE (bpy GC trap), got "
              f"{type(vocab.STEP_STATUS_ITEMS).__name__}")
    domain = vocab.values(vocab.STEP_STATUS_ITEMS)
    expected = [cp.STEP_STATUS_AUTO] + list(cp.STEP_STATUS_EXPLICIT)
    if domain != expected:
        _fail(f"STEP_STATUS_ITEMS domain = {domain} (expected {expected})")
    for derived in ("empty", "wip", "published"):
        if derived in domain:
            _fail(f"{derived!r} is DERIVED and must never be settable through the enum")
    print(f"ok  STEP_STATUS_ITEMS = {domain} (settable domain only, module-level tuple)")

    try:
        addon.register()
    except Exception as e:
        _fail("addon.register() raised", e)

    work = tempfile.mkdtemp(prefix="ylos_step_status_")
    try:
        root = os.path.join(work, "src")
        cache = os.path.join(work, "cache")
        os.makedirs(root)
        os.makedirs(cache)

        proj = cp.create("StatusProj", root=root, cache=cache, prod_type="FILM")
        project_dir = str(proj["source"])
        entity = "PROP_Cube_Default"
        cp.create_asset(project_dir, entity, entity_type="asset", asset_type="PROP")
        step = _manifest(project_dir, entity)["steps"][0]

        scene = bpy.context.scene
        scene.ylos_project_path = project_dir
        scene.ylos_project_name = "StatusProj"
        scene.ylos_prod_type = "FILM"
        scene.ylos_context_type = "ASSET"
        scene.ylos_asset_type = "PROP"
        scene.ylos_current_asset = entity
        scene.ylos_current_step = step

        # ---- E (part 1). Nothing on disk -> 'empty', derived, not explicit --------------
        status = asset_core.get_entity_step_status(project_dir, entity)
        if status.get(step, {}).get("status") != "empty" or status[step]["explicit"]:
            _fail(f"fresh step status = {status.get(step)!r} (expected empty, not explicit)")
        print(f"ok  fresh step '{step}' reads as 'empty' (derived)")

        # ---- B. Set an explicit status --------------------------------------------------
        res = bpy.ops.ylos.set_step_status('EXEC_DEFAULT', status="review")
        if res != {"FINISHED"}:
            _fail(f"ylos.set_step_status(review) returned {res}")
        persisted = _manifest(project_dir, entity).get(cp.STEP_STATUS_KEY, {})
        if persisted.get(step) != "review":
            _fail(f"manifest['{cp.STEP_STATUS_KEY}'] = {persisted!r} (expected {step}: review)")
        # Read back THROUGH THE ADDON's cached adapter: this is what the panel shows, and a
        # missing cache purge would still be serving 'empty' here.
        status = asset_core.get_entity_step_status(project_dir, entity)
        if status[step]["status"] != "review" or not status[step]["explicit"]:
            _fail(f"addon read-back after set = {status[step]!r} (cache not invalidated?)")
        if status[step]["derived"] != "empty":
            _fail(f"the DERIVED status must stay 'empty' (nothing on disk), got "
                  f"{status[step]['derived']!r}")
        print("ok  ylos.set_step_status persists 'review' and the addon reads it back "
              "(explicit wins, derived preserved)")

        # An explicit status is a production decision, never proof an artifact exists.
        if asset_core.get_asset_step_status(project_dir, entity).get(step):
            _fail("get_asset_step_status must stay False: 'review' is not a publish")
        print("ok  'review' does not fake a publish in the published/not-published view")

        # ---- D. Undeclared step -> reported error, manifest untouched -------------------
        # The operator self.report({'ERROR'}) + returns CANCELLED; bpy turns that into a
        # RuntimeError for a PYTHON caller (standard bpy.ops behaviour). What matters is
        # that the message is the ORCHESTRATOR's - one wording for web / Blender / CLI -
        # and that nothing was written.
        before = _manifest(project_dir, entity)
        try:
            bpy.ops.ylos.set_step_status('EXEC_DEFAULT', step="not_a_step", status="approved")
            _fail("set_step_status on an undeclared step neither failed nor raised")
        except RuntimeError as e:
            message = str(e)
        if "not_a_step" not in message or "not declared" not in message:
            _fail(f"expected create_project's 'step not declared' message, got {message!r}")
        after = _manifest(project_dir, entity)
        if before.get(cp.STEP_STATUS_KEY) != after.get(cp.STEP_STATUS_KEY):
            _fail("a refused set_step_status still modified the manifest")
        print("ok  an undeclared step is refused with the orchestrator's message, writes nothing")

        # ---- C. 'auto' clears the explicit value ----------------------------------------
        res = bpy.ops.ylos.set_step_status('EXEC_DEFAULT', status=cp.STEP_STATUS_AUTO)
        if res != {"FINISHED"}:
            _fail(f"ylos.set_step_status(auto) returned {res}")
        if cp.STEP_STATUS_KEY in _manifest(project_dir, entity):
            _fail("clearing the last explicit status must remove the whole "
                  f"'{cp.STEP_STATUS_KEY}' key from the manifest")
        status = asset_core.get_entity_step_status(project_dir, entity)
        if status[step]["explicit"] or status[step]["status"] != "empty":
            _fail(f"after 'auto' the step must fall back to derived, got {status[step]!r}")
        print("ok  'auto' clears the explicit status and the step falls back to derived")

        # ---- E (part 2). Derived tracks the disk: wip, then published -------------------
        res = bpy.ops.ylos.save_wip('EXEC_DEFAULT', step=step, version=1)
        if res != {"FINISHED"}:
            _fail(f"ylos.save_wip returned {res}")
        asset_core.invalidate_step_status_cache(project_dir)
        status = asset_core.get_entity_step_status(project_dir, entity)
        if status[step]["status"] != "wip":
            _fail(f"after Save Version the derived status must be 'wip', got {status[step]!r}")
        print("ok  a saved scenefile moves the derived status to 'wip'")

        bpy.ops.mesh.primitive_cube_add(size=2.0)
        res = bpy.ops.ylos.publish('EXEC_DEFAULT', step=step, allow_full_scene=True)
        if res != {"FINISHED"}:
            _fail(f"ylos.publish returned {res}")
        # op_publish purges the cache itself - no manual invalidation here on purpose.
        status = asset_core.get_entity_step_status(project_dir, entity)
        if status[step]["status"] != "published":
            _fail(f"after a publish the derived status must be 'published', got "
                  f"{status[step]!r} (did op_publish purge the status cache?)")
        if not asset_core.get_asset_step_status(project_dir, entity).get(step):
            _fail("get_asset_step_status must be True after a complete publish")
        print("ok  a complete publish moves the derived status to 'published' "
              "(cache purged by op_publish)")

        try:
            addon.unregister()
        except Exception as e:
            _fail("addon.unregister() raised", e)

        print("\nPASS: per-step status (schema 2.2) set / cleared / derived correctly in Blender")
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
