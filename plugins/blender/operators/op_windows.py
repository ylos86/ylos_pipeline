# -*- coding: utf-8 -*-
"""Floating windows of the Prism model (top-bar menu -> window), one per job:
  ylos.asset_browser      Project Browser   (operators/op_asset_list.py)
  ylos.open_scenefile     Scenefile         status, WIP, Save Version, playblast / render
  ylos.open_scene_check   Scene Check       scan + fixes
  ylos.open_state_manager State Manager     (operators/op_state_manager.py)
  ylos.open_io            Import / Export   (operators/op_io.py)
All are transient popups (invoke_popup): they close when the mouse leaves. No business logic
here - each draw() delegates to a ui/ module that only wires existing operators."""

import bpy

from ..ui.browser import BROWSER_SCREEN, browser_window_open, hide_native_panels
from ..ui.scenefile import draw_scenefile
from ..ui.scene_check import draw_scene_check


class YLOS_OT_OpenScenefile(bpy.types.Operator):
    bl_idname = "ylos.open_scenefile"
    bl_label = "Scenefile"
    bl_description = "Step status, WIP versions, Save Version, playblast and render"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=360)

    def draw(self, context):
        draw_scenefile(self.layout, context)

    def execute(self, context):
        return {"FINISHED"}


class YLOS_OT_OpenSceneCheck(bpy.types.Operator):
    bl_idname = "ylos.open_scene_check"
    bl_label = "Scene Check"
    bl_description = "Scan the scene for naming and readiness issues"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=420)

    def draw(self, context):
        draw_scene_check(self.layout, context)

    def execute(self, context):
        return {"FINISHED"}


class YLOS_OT_BrowserWindow(bpy.types.Operator):
    """The Project Browser in a real, movable and resizable Blender window (Prism-style floating
    page). A popup (ylos.asset_browser) cannot be moved and closes with the mouse, so this is
    the default entry point; the popup stays as 'Quick Switch' and as the fallback."""
    bl_idname = "ylos.browser_window"
    bl_label = "Project Browser"
    bl_description = "Open the Project Browser in its own floating window"
    bl_options = {"REGISTER"}

    def execute(self, context):
        if browser_window_open():
            self.report({"INFO"}, "Project Browser window is already open.")
            return {"FINISHED"}
        wm = context.window_manager
        hide_native_panels()    # idempotent: picks up panels registered after the addon
        try:
            known = {w.as_pointer() for w in wm.windows}
            bpy.ops.wm.window_new()
            fresh = [w for w in wm.windows if w.as_pointer() not in known]
            win = fresh[0] if fresh else wm.windows[-1]
            win.screen.name = BROWSER_SCREEN
            area = win.screen.areas[0]
            area.type = "PROPERTIES"
            space = area.spaces.active
            space.context = "SCENE"
            # A browser window is not a Properties editor: hide the header and the tab column
            # (Tool / Render / Output / ... - none of it is wanted here). Verified live on 5.2:
            # SpaceProperties has show_region_header but NO show_region_navigation_bar flag, so the
            # tab column is closed with screen.region_toggle (the 'View > Navigation Bar' toggle).
            # Cosmetic: a failure here must not break the window.
            if hasattr(area.spaces.active, "show_region_header"):
                area.spaces.active.show_region_header = False
            try:
                nav = [r for r in area.regions if r.type == "NAVIGATION_BAR"]
                main = [r for r in area.regions if r.type == "WINDOW"]
                if nav and main and nav[0].width > 1:
                    with context.temp_override(window=win, area=area, region=main[0]):
                        bpy.ops.screen.region_toggle(region_type="NAVIGATION_BAR")
            except Exception:   # noqa: BLE001
                pass
        except Exception as exc:    # noqa: BLE001 - never leave the user with nothing
            self.report({"WARNING"}, f"Floating window unavailable ({exc}); using the popup.")
            return bpy.ops.ylos.asset_browser("INVOKE_DEFAULT")
        return {"FINISHED"}
