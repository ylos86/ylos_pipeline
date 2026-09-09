# -*- coding: utf-8 -*-
# Top bar pull-down menu ("Ylos"), Prism pattern: bpy.types.TOPBAR_MT_editor_menus.append.
# All entries reuse EXISTING operators (Ylos or native Blender) -
# zero business logic in draw(). The only classes added here are small
# operators with no existing equivalent (Open Project Browser, Reload Pipeline, About).

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


def _purge_addon_modules(package: str) -> int:
    """Purge the addon package AND all its sub-modules from sys.modules.

    REAL BUG, root cause of a whole class of false diagnostics: addon_disable +
    addon_enable DOES NOT RELOAD the addon's code. Blender only calls
    unregister()/register() on the module already in memory; `from . import core` etc. have
    already run on the first import, so the sub-modules stay the ones in sys.modules —
    including a .pyc older than the .py. Symptom observed in real conditions: a
    fix written to disk (thumbnails.py's sun light), verifiable via inspect.getsource()
    (which reads the FILE, not the loaded bytecode), yet never executed — hence never
    effective, session after session, without a single error message.

    Code-side verification, if a doubt returns: compare the actually-loaded bytecode to the
    file, never the source.
        f.__code__.co_consts  ->  what runs
        inspect.getsource(f)  ->  what is on disk (can lie)

    Called AFTER addon_disable (unregister() is done, no registered class references
    these modules any more) and BEFORE addon_enable (which will re-import everything fresh from
    disk). Returns the number of purged modules."""
    doomed = [m for m in sys.modules
              if m == package or m.startswith(package + ".")]
    for name in doomed:
        del sys.modules[name]
    return len(doomed)


class YLOS_OT_ReloadPipeline(bpy.types.Operator):
    bl_idname = "ylos.reload_pipeline"
    bl_label = "Reload Pipeline"
    bl_description = "Disable then re-enable the Ylos Pipeline addon (reloads create_project.py)"
    bl_options = {"REGISTER"}

    def execute(self, context):
        # Guard (real observed Blender crash, see CLAUDE.md): addon_disable unregisters
        # states.YLOS_PG_ExportState while a non-empty CollectionProperty of this type may
        # still be displayed by a UIList (State Manager - N-panel section or popup) - enough
        # to crash Blender (RNA invalidated under a still-alive instance/widget).
        # We refuse the reload as long as an export state exists ANYWHERE (all scenes of the
        # .blend, not only the active scene) rather than risk a silent crash: a
        # full Blender restart loads the same code without that risk.
        dirty = [s.name for s in bpy.data.scenes if len(s.ylos_export_states) > 0]
        if dirty:
            self.report(
                {"ERROR"},
                "Reload refused: export state(s) present in " + ", ".join(dirty) + " - "
                "clear the State Manager (button '-') before reloading, or restart Blender "
                "to load the modified code without a crash risk.",
            )
            return {"CANCELLED"}

        module_name = __package__.split(".")[0]
        try:
            bpy.ops.preferences.addon_disable(module=module_name)
            purged = _purge_addon_modules(module_name)
            bpy.ops.preferences.addon_enable(module=module_name)
        except Exception as e:
            self.report({"ERROR"}, f"Reload failed: {e}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Ylos Pipeline reloaded ({purged} modules reloaded).")
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
        layout.operator("ylos.open_io", text="Import / Export…", icon="IMPORT")
        layout.operator("ylos.publish", text="Quick Publish (current step)…", icon="EXPORT")
        layout.operator("ylos.run_scene_check", text="Check Scene", icon="VIEWZOOM")
        layout.operator("ylos.capture_preview", text="Capture Preview (viewport)",
                        icon="RESTRICT_RENDER_OFF")
        layout.separator()
        layout.operator("ylos.reload_pipeline", text="Reload Pipeline", icon="FILE_REFRESH")
        layout.operator("ylos.about", text="About", icon="INFO")


def draw_topbar_menu(self, context):
    self.layout.menu(YLOS_MT_TopbarMenu.bl_idname)
