# -*- coding: utf-8 -*-
"""Headless Blender test: the "Ylos" top bar menu (plugins/blender/ui/menu.py) registers
without exception and the menu class is known to bpy.types after register().

Run:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --python tools/blender/test_menu_headless.py

Exit code != 0 on failure (assert / exception), 0 if everything passes.
"""
import os
import sys
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
    import blender as addon  # package plugins/blender imported as 'blender'
    from blender.ui import menu

    try:
        addon.register()
    except Exception as e:
        _fail("addon.register() raised", e)
    print("ok  addon.register() without exception")

    # The menu class is registered under bpy.types.<bl_idname>.
    if not hasattr(bpy.types, menu.YLOS_MT_TopbarMenu.bl_idname):
        _fail(f"bpy.types.{menu.YLOS_MT_TopbarMenu.bl_idname} missing after register()")
    print(f"ok  bpy.types.{menu.YLOS_MT_TopbarMenu.bl_idname} registered")

    # The menu's small operators are registered (bpy.ops ids).
    for idname in ("ylos.open_project_browser", "ylos.reload_pipeline", "ylos.about"):
        category, name = idname.split(".")
        if not hasattr(getattr(bpy.ops, category), name):
            _fail(f"bpy.ops.{idname} missing after register()")
    print("ok  bpy.ops.ylos.{open_project_browser,reload_pipeline,about} registered")

    # The draw function is hooked onto TOPBAR_MT_editor_menus.
    draw_funcs = bpy.types.TOPBAR_MT_editor_menus._dyn_ui_initialize()
    if menu.draw_topbar_menu not in draw_funcs:
        _fail("menu.draw_topbar_menu missing from TOPBAR_MT_editor_menus after register()")
    print("ok  menu.draw_topbar_menu hooked onto TOPBAR_MT_editor_menus")

    try:
        addon.unregister()
    except Exception as e:
        _fail("addon.unregister() raised", e)
    print("ok  addon.unregister() without exception")

    if hasattr(bpy.types, menu.YLOS_MT_TopbarMenu.bl_idname):
        _fail(f"bpy.types.{menu.YLOS_MT_TopbarMenu.bl_idname} still present after unregister()")
    print(f"ok  bpy.types.{menu.YLOS_MT_TopbarMenu.bl_idname} unregistered")

    print("\nPASS: Ylos top bar menu headless OK")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _fail("unexpected exception", e)
