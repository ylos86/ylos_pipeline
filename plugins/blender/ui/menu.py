# -*- coding: utf-8 -*-
# Top bar pull-down menu ("Ylos"), pattern Prism : bpy.types.TOPBAR_MT_editor_menus.append.
# Toutes les entrees reutilisent des operateurs EXISTANTS (Ylos ou Blender natifs) -
# zero logique metier dans draw(). Les seules classes ajoutees ici sont de petits
# operateurs sans equivalent existant (Open Project Browser, Reload Pipeline, About).

import os
import subprocess
import sys
import webbrowser

import bpy

REPO_ROOT = os.path.normpath(
    os.path.join(os.path.realpath(__file__), "..", "..", "..", "..")
)

PROJECT_BROWSER_URL = "http://127.0.0.1:8765"


class YLOS_OT_OpenProjectBrowser(bpy.types.Operator):
    bl_idname = "ylos.open_project_browser"
    bl_label = "Open Project Browser"
    bl_description = "Open the Ylos web project browser in your default browser"
    bl_options = {"REGISTER"}

    def execute(self, context):
        webbrowser.open(PROJECT_BROWSER_URL)
        self.report({"INFO"}, f"Opened {PROJECT_BROWSER_URL}")
        return {"FINISHED"}


class YLOS_OT_ReloadPipeline(bpy.types.Operator):
    bl_idname = "ylos.reload_pipeline"
    bl_label = "Reload Pipeline"
    bl_description = "Disable then re-enable the Ylos Pipeline addon (reloads create_project.py)"
    bl_options = {"REGISTER"}

    def execute(self, context):
        module_name = __package__.split(".")[0]
        try:
            bpy.ops.preferences.addon_disable(module=module_name)
            bpy.ops.preferences.addon_enable(module=module_name)
        except Exception as e:
            self.report({"ERROR"}, f"Reload failed: {e}")
            return {"CANCELLED"}
        self.report({"INFO"}, "Ylos Pipeline reloaded.")
        return {"FINISHED"}


class YLOS_OT_About(bpy.types.Operator):
    bl_idname = "ylos.about"
    bl_label = "About Ylos Pipeline"
    bl_description = "Show addon version and repository commit"
    bl_options = {"REGISTER"}

    def execute(self, context):
        addon_module = sys.modules.get(__package__.split(".")[0])
        version = getattr(addon_module, "bl_info", {}).get("version", (0, 0, 0))
        version_str = ".".join(str(v) for v in version)

        commit = "unknown"
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                commit = result.stdout.strip()
        except Exception:
            pass

        message = f"Ylos Pipeline v{version_str}  (commit {commit})"

        def _draw(popup, ctx):
            popup.layout.label(text=message, icon="FUND")

        context.window_manager.popup_menu(_draw, title="About Ylos Pipeline", icon="INFO")
        self.report({"INFO"}, message)
        return {"FINISHED"}


class YLOS_MT_TopbarMenu(bpy.types.Menu):
    bl_idname = "YLOS_MT_topbar_menu"
    bl_label = "Ylos"

    def draw(self, context):
        layout = self.layout
        layout.operator("ylos.save_wip", text="Save Version", icon="FILE_TICK")
        layout.operator("wm.save_as_mainfile", text="Save WIP As…", icon="FILE_TICK")
        layout.separator()
        layout.operator("ylos.open_project_browser", text="Open Project Browser", icon="URL")
        layout.operator("ylos.open_context", text="Open Context…", icon="FILE_FOLDER")
        layout.separator()
        layout.operator("ylos.open_state_manager", text="State Manager…", icon="PRESET")
        layout.operator("ylos.publish", text="Quick Publish (current step)…", icon="EXPORT")
        layout.operator("ylos.run_scene_check", text="Check Scene", icon="VIEWZOOM")
        layout.separator()
        layout.operator("ylos.reload_pipeline", text="Reload Pipeline", icon="FILE_REFRESH")
        layout.operator("ylos.about", text="About", icon="INFO")


def draw_topbar_menu(self, context):
    self.layout.menu(YLOS_MT_TopbarMenu.bl_idname)
