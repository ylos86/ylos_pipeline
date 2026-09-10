# -*- coding: utf-8 -*-
"""Headless Blender test: the State Manager (Prism-style). Checks the NEW SEMANTICS -
several export states stacked, executed by ONE single Publish (ylos.publish_states), plus the
add/remove/reorder operators and skipping a disabled state.

Exact scenario:
  - FILM project (offline -> USD publish) + asset with default steps (modeling/lookdev present).
  - add 2 export states (modeling, lookdev); reorder (UP/DOWN); add+remove a 3rd (remove test).
  - ylos.publish_states -> 2 'complete' publishes finalized on disk (one per step).
  - disable the lookdev state -> re-Publish -> only modeling goes to v2 (lookdev stays v1).
  - states' last_result / last_version populated.

Run:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --factory-startup --python tools/blender/test_state_manager_headless.py

Exit code != 0 on failure, 0 if everything passes. Outside the stdlib CI (requires Blender).
"""
import os
import shutil
import sys
import tempfile
import traceback

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


def _complete_versions(cp, project_dir, entity, step):
    return sorted(
        p["version"] for p in cp.list_publishes(project_dir, entity, step, "asset")
        if p.get("status") == "complete"
    )


def main():
    import bpy
    import create_project as cp
    import blender as addon

    work = tempfile.mkdtemp(prefix="ylos_state_mgr_test_")
    try:
        root = os.path.join(work, "src")
        cache = os.path.join(work, "cache")
        os.makedirs(root)
        os.makedirs(cache)

        proj = cp.create("StateMgrTest", root=root, cache=cache, prod_type="FILM")
        project_dir = str(proj["source"])
        entity = "PROP_Box_Default"
        cp.create_asset(project_dir, entity, entity_type="asset", asset_type="PROP")

        try:
            addon.register()
        except Exception as e:
            _fail("addon.register() raised", e)

        scene = bpy.context.scene
        scene.ylos_project_path = project_dir
        scene.ylos_project_name = "StateMgrTest"
        scene.ylos_prod_type = "FILM"
        scene.ylos_context_type = "ASSET"
        scene.ylos_asset_type = "PROP"
        scene.ylos_current_asset = entity

        bpy.ops.mesh.primitive_cube_add(size=2.0)

        states = scene.ylos_export_states

        # --- add: modeling then lookdev ---
        scene.ylos_current_step = "modeling"
        bpy.ops.ylos.state_add_export()
        scene.ylos_current_step = "lookdev"
        bpy.ops.ylos.state_add_export()
        if len(states) != 2:
            _fail(f"after 2 adds: {len(states)} states (expected 2)")
        if states[0].step != "modeling" or states[1].step != "lookdev":
            _fail(f"unexpected steps: {[s.step for s in states]}")
        print("ok  add x2 -> [modeling, lookdev]")

        # --- reorder: UP on index 1 -> lookdev moves up ---
        scene.ylos_export_states_index = 1
        bpy.ops.ylos.state_move_export(direction="UP")
        if scene.ylos_export_states_index != 0 or states[0].step != "lookdev":
            _fail(f"after move UP: index={scene.ylos_export_states_index}, "
                  f"steps={[s.step for s in states]}")
        bpy.ops.ylos.state_move_export(direction="DOWN")
        if states[0].step != "modeling" or states[1].step != "lookdev":
            _fail(f"after move DOWN: steps={[s.step for s in states]}")
        print("ok  reorder UP/DOWN")

        # --- remove: add a throwaway 3rd then remove it ---
        bpy.ops.ylos.state_add_export()  # 3rd (lookdev, current_step)
        if len(states) != 3:
            _fail(f"after adding 3rd: {len(states)} states (expected 3)")
        scene.ylos_export_states_index = 2
        bpy.ops.ylos.state_remove_export()
        if len(states) != 2:
            _fail(f"after remove: {len(states)} states (expected 2)")
        print("ok  remove (3rd removed)")

        # --- Single Publish: the 2 states -> 2 'complete' publishes ---
        for s in states:
            s.allow_full_scene = True  # objects not named by convention -> full scene
        res = bpy.ops.ylos.publish_states('EXEC_DEFAULT')
        if res != {"FINISHED"}:
            _fail(f"ylos.publish_states returned {res} (expected FINISHED)")

        mod_v = _complete_versions(cp, project_dir, entity, "modeling")
        lkd_v = _complete_versions(cp, project_dir, entity, "lookdev")
        if mod_v != [1] or lkd_v != [1]:
            _fail(f"after 1st Publish: modeling={mod_v}, lookdev={lkd_v} (expected [1], [1])")
        for s in states:
            if s.last_version != 1 or not s.last_result:
                _fail(f"state {s.step}: last_version={s.last_version}, "
                      f"last_result={s.last_result!r}")
        print("ok  1 Publish -> 2 complete publishes (modeling v1, lookdev v1) + last_result populated")

        # --- Skip a disabled state: lookdev off -> only modeling goes to v2 ---
        states[1].enabled = False  # lookdev
        res = bpy.ops.ylos.publish_states('EXEC_DEFAULT')
        if res != {"FINISHED"}:
            _fail(f"2nd ylos.publish_states returned {res} (expected FINISHED)")
        mod_v = _complete_versions(cp, project_dir, entity, "modeling")
        lkd_v = _complete_versions(cp, project_dir, entity, "lookdev")
        if mod_v != [1, 2] or lkd_v != [1]:
            _fail(f"after Publish with lookdev off: modeling={mod_v}, lookdev={lkd_v} "
                  f"(expected [1,2], [1])")
        print("ok  disabled state skipped: modeling v2, lookdev stays v1")

        # --- No state enabled -> CANCELLED, nothing published ---
        states[0].enabled = False
        res = bpy.ops.ylos.publish_states('EXEC_DEFAULT')
        if res != {"CANCELLED"}:
            _fail(f"publish_states with no state enabled returned {res} (expected CANCELLED)")
        print("ok  no state enabled -> CANCELLED")

        try:
            addon.unregister()
        except Exception as e:
            _fail("addon.unregister() raised", e)
        print("ok  addon.unregister() without exception")

        print("\nPASS: State Manager (add/remove/reorder + Publish batch + skip) headless OK")
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
