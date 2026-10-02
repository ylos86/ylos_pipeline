# -*- coding: utf-8 -*-
"""Headless Blender test: the bodies of the Ylos windows (Project Browser ui/browser.py,
Scenefile ui/scenefile.py, State Manager ui/state_manager.py, Scene Check ui/scene_check.py;
there is no N-panel any more) ACTUALLY
run their draw() without an exception, on a project/entity fixture, in the empty states (nothing
published/saved) AND populated (real WIP + publish). Blender '--background' has no window -> no real bpy.types.UILayout
available: draw() is called with a FAKE layout (duck-type, accepts any call/attribute)
that does NOT catch Blender API errors (wrong widget signature) but catches everything
else (wrong dict key, typo, exception from a create_project.py call) - it's
the only headless verification possible for these methods; visual fidelity stays manual
(see CLAUDE.md, same limit as the addon's other popups/panels).

Run:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --python tools/blender/test_panel_draw_headless.py

Exit code != 0 on failure (assert / exception), 0 if everything passes.
"""
import os
import shutil
import sys
import tempfile
import traceback
import types

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


class _FakeOp:
    """bpy row.operator(...) returns a proxy onto which props are assigned (op.x = y)."""
    pass


class _FakeLayout:
    """Duck-type of a bpy.types.UILayout: any missing attribute/call returns either a
    new _FakeLayout (row/column/box/split...), or a _FakeOp (operator()). Assignments
    (layout.use_property_split = True) go through the normal __dict__."""

    def __getattr__(self, name):
        if name == "operator":
            return lambda *a, **k: _FakeOp()
        return lambda *a, **k: _FakeLayout()


def _draw(draw_fn, context):
    """draw_fn(layout, context): the window-body functions are plain functions."""
    draw_fn(_FakeLayout(), context)


def main():
    import bpy
    import create_project as cp
    import blender as addon
    from blender.ui import browser, scenefile, scene_check, state_manager
    from blender.core import thumbnails

    work = tempfile.mkdtemp(prefix="ylos_panel_draw_test_")
    try:
        root = os.path.join(work, "src")
        cache = os.path.join(work, "cache")
        os.makedirs(root)
        os.makedirs(cache)

        proj = cp.create("PanelDrawTest", root=root, cache=cache, prod_type="XR")
        project_dir = str(proj["source"])
        entity = "PROP_Cube_Default"
        cp.create_asset(project_dir, entity, entity_type="asset", asset_type="PROP")

        try:
            addon.register()
        except Exception as e:
            _fail("addon.register() raised", e)

        scene = bpy.context.scene
        scene.ylos_project_path = project_dir
        scene.ylos_project_name = "PanelDrawTest"
        scene.ylos_prod_type = "XR"
        scene.ylos_context_type = "ASSET"
        scene.ylos_asset_type = "PROP"

        context = bpy.context

        # --- State 1: no project loaded (fresh bpy.context.scene, before assignment) ---
        empty_scene_ns = types.SimpleNamespace(scene=bpy.data.scenes.new("YLOS_empty_ctx"))
        try:
            _draw(browser.draw_project_browser, empty_scene_ns)
        except Exception as e:
            _fail("YLOS_PT_Context.draw() raised (state: no project)", e)
        finally:
            bpy.data.scenes.remove(empty_scene_ns.scene)
        print("ok  YLOS_PT_Context.draw() : state 'no project' without exception")

        # --- State 2: project loaded, no active asset ---
        try:
            _draw(browser.draw_project_browser, context)
        except Exception as e:
            _fail("YLOS_PT_Context.draw() raised (state: project without active asset)", e)
        print("ok  YLOS_PT_Context.draw() : state 'project, no active asset' without exception")

        scene.ylos_current_asset = entity
        scene.ylos_current_step = "modeling"

        # --- State 3: active asset, nothing saved/published (branches 'none yet' / 'no publish') ---
        try:
            _draw(browser.draw_project_browser, context)
            _draw(browser.draw_project_browser, context)
            _draw(scenefile.draw_scenefile, context)
            _draw(state_manager.draw_state_manager, context)  # subsumes Publish + Imports
            _draw(scene_check.draw_scene_check, context)
        except Exception as e:
            _fail("draw() raised (state: active asset, nothing saved/published)", e)
        print("ok  sections draw() : state 'active asset, nothing saved/published' without exception")

        # --- Real fixture: WIP + publish (populated branches) ---
        res = bpy.ops.ylos.save_wip('EXEC_DEFAULT', step="modeling", version=1)
        if res != {"FINISHED"}:
            _fail(f"ylos.save_wip returned {res} (expected FINISHED)")

        bpy.ops.mesh.primitive_cube_add(size=2.0)
        res = bpy.ops.ylos.publish(
            'EXEC_DEFAULT', step="modeling", allow_full_scene=True, load_after=False,
        )
        if res != {"FINISHED"}:
            _fail(f"ylos.publish returned {res} (expected FINISHED) - "
                  f"thumbnails.LAST_ERROR={thumbnails.LAST_ERROR!r}")

        # --- State 4: active asset, WIP + publish present + an active export state ---
        # An export state populates the State Manager's 'active state settings' branch.
        st = scene.ylos_export_states.add()
        st.entity = entity
        st.step = "modeling"
        scene.ylos_export_states_index = 0
        try:
            _draw(browser.draw_project_browser, context)
            _draw(browser.draw_project_browser, context)
            _draw(scenefile.draw_scenefile, context)
            _draw(state_manager.draw_state_manager, context)
            _draw(scene_check.draw_scene_check, context)
        except Exception as e:
            _fail("draw() raised (state: WIP + publish present)", e)
        print("ok  sections draw() : state 'WIP + publish + export state' without exception "
              f"(target=web -> .glb, LAST_ERROR={thumbnails.LAST_ERROR!r})")

        # --- State 5: tagged import present + update available (INC-5) ---
        res = bpy.ops.ylos.import_product(
            'EXEC_DEFAULT', entity=entity, step="modeling", version=0,
        )
        if res != {"FINISHED"}:
            _fail(f"ylos.import_product returned {res} (expected FINISHED)")

        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(3, 0, 0))
        res = bpy.ops.ylos.publish(
            'EXEC_DEFAULT', step="modeling", allow_full_scene=True, load_after=False,
        )
        if res != {"FINISHED"}:
            _fail(f"ylos.publish v2 returned {res} (expected FINISHED)")

        res = bpy.ops.ylos.check_updates('EXEC_DEFAULT')
        if res != {"FINISHED"}:
            _fail(f"ylos.check_updates returned {res} (expected FINISHED)")

        try:
            _draw(state_manager.draw_state_manager, context)
            _draw(scenefile.draw_scenefile, context)          # 'Updates available' line
            _draw(browser.draw_project_browser, context)
        except Exception as e:
            _fail("YLOS_PT_StateManager.draw() raised (tagged import + update available branch)", e)
        print("ok  draw() : tagged import + update available + downstream impact without exception")

        # --- Publish check dry run + cached results + scoped Import panel ---
        try:
            # The operator reports ERROR (-> RuntimeError from bpy.ops) when the dry run
            # finds blocking issues: that is its contract, results stay cached for the UI.
            try:
                bpy.ops.ylos.check_publish('EXEC_DEFAULT')
            except RuntimeError as e:
                print("   check_publish (expected blocking report):", str(e).strip()[:160])
            from blender.operators.op_publish import get_cached_publish_check
            if get_cached_publish_check() is None:
                _fail("ylos.check_publish left no cached result for the UI")
            from blender.ui import io_panel
            _draw(state_manager.draw_state_manager, context)   # draws cached check results
            for allm in (False, True):
                context.scene.ylos_io_all_entities = allm
                _draw(io_panel.draw_io, context)
            context.scene.ylos_io_all_entities = False
        except Exception as e:
            _fail("draw() raised (publish check cache / io_panel scoping)", e)
        print("ok  ylos.check_publish + cached check draw + Import scoped/all draw without exception")

        # --- State 6: an EXPLICIT step status (schema 2.2) is displayed by both panels ---
        res = bpy.ops.ylos.set_step_status('EXEC_DEFAULT', status="review")
        if res != {"FINISHED"}:
            _fail(f"ylos.set_step_status returned {res} (expected FINISHED)")
        try:
            _draw(scenefile.draw_scenefile, context)
            _draw(browser.draw_project_browser, context)
        except Exception as e:
            _fail("draw() raised (state: explicit step status 'review')", e)
        print("ok  draw() : explicit step status branch without exception")

        try:
            addon.unregister()
        except Exception as e:
            _fail("addon.unregister() raised", e)
        print("ok  addon.unregister() without exception")

        print("\nPASS: draw() of the Ylos windows (Project Browser/Scenefile/State Manager/Scene Check) headless OK")
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
