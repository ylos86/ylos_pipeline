# -*- coding: utf-8 -*-
"""Test headless Blender : le menu top bar "Ylos" (plugins/blender/ui/menu.py) s'enregistre
sans exception et la classe de menu est bien connue de bpy.types apres register().

Lancer :
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --python tools/blender/test_menu_headless.py

Exit code != 0 en cas d'echec (assert / exception), 0 si tout passe.
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
    import blender as addon  # package plugins/blender importe comme 'blender'
    from blender.ui import menu

    try:
        addon.register()
    except Exception as e:
        _fail("addon.register() a leve", e)
    print("ok  addon.register() sans exception")

    # La classe de menu est enregistree sous bpy.types.<bl_idname>.
    if not hasattr(bpy.types, menu.YLOS_MT_TopbarMenu.bl_idname):
        _fail(f"bpy.types.{menu.YLOS_MT_TopbarMenu.bl_idname} absent apres register()")
    print(f"ok  bpy.types.{menu.YLOS_MT_TopbarMenu.bl_idname} enregistre")

    # Les petits operateurs du menu sont bien enregistres (ids bpy.ops).
    for idname in ("ylos.open_project_browser", "ylos.reload_pipeline", "ylos.about"):
        category, name = idname.split(".")
        if not hasattr(getattr(bpy.ops, category), name):
            _fail(f"bpy.ops.{idname} absent apres register()")
    print("ok  bpy.ops.ylos.{open_project_browser,reload_pipeline,about} enregistres")

    # La fonction de dessin est bien accrochee a TOPBAR_MT_editor_menus.
    draw_funcs = bpy.types.TOPBAR_MT_editor_menus._dyn_ui_initialize()
    if menu.draw_topbar_menu not in draw_funcs:
        _fail("menu.draw_topbar_menu absent de TOPBAR_MT_editor_menus apres register()")
    print("ok  menu.draw_topbar_menu accroche a TOPBAR_MT_editor_menus")

    try:
        addon.unregister()
    except Exception as e:
        _fail("addon.unregister() a leve", e)
    print("ok  addon.unregister() sans exception")

    if hasattr(bpy.types, menu.YLOS_MT_TopbarMenu.bl_idname):
        _fail(f"bpy.types.{menu.YLOS_MT_TopbarMenu.bl_idname} toujours present apres unregister()")
    print(f"ok  bpy.types.{menu.YLOS_MT_TopbarMenu.bl_idname} desenregistre")

    print("\nPASS: menu top bar Ylos headless OK")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _fail("exception inattendue", e)
