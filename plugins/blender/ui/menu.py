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


def _purge_addon_modules(package: str) -> int:
    """Purge le package de l'addon ET tous ses sous-modules de sys.modules.

    BUG REEL, cause racine d'une classe entiere de faux diagnostics : addon_disable +
    addon_enable NE RECHARGE PAS le code de l'addon. Blender ne fait que rappeler
    unregister()/register() sur le module deja en memoire ; `from . import core` etc. ont
    deja tourne au premier import, donc les sous-modules restent ceux de sys.modules —
    y compris un .pyc plus ancien que le .py. Symptome observe en conditions reelles : un
    fix ecrit sur disque (le sun light de thumbnails.py), verifiable par inspect.getsource()
    (qui lit le FICHIER, pas le bytecode charge), et pourtant jamais execute — donc jamais
    efficace, session apres session, sans le moindre message d'erreur.

    Verification cote code, si un doute revient : comparer le bytecode reellement charge au
    fichier, jamais la source.
        f.__code__.co_consts  ->  ce qui tourne
        inspect.getsource(f)  ->  ce qu'il y a sur le disque (peut mentir)

    Appele APRES addon_disable (unregister() est termine, plus aucune classe enregistree ne
    reference ces modules) et AVANT addon_enable (qui re-importera tout a neuf depuis le
    disque). Retourne le nombre de modules purges."""
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
        # Garde-fou (crash Blender reel observe, cf. CLAUDE.md) : addon_disable desenregistre
        # states.YLOS_PG_ExportState pendant qu'une CollectionProperty non vide de ce type peut
        # encore etre affichee par un UIList (State Manager - panel N-panel ou popup) - assez
        # pour faire planter Blender (RNA invalidee sous une instance/un widget encore vivant).
        # On refuse le reload tant qu'un export state existe QUELQUE PART (toutes les scenes du
        # .blend, pas seulement la scene active) plutot que de risquer un crash silencieux : un
        # redemarrage complet de Blender charge le meme code sans ce risque, lui.
        dirty = [s.name for s in bpy.data.scenes if len(s.ylos_export_states) > 0]
        if dirty:
            self.report(
                {"ERROR"},
                "Reload refuse : export state(s) present dans " + ", ".join(dirty) + " - "
                "vide le State Manager (bouton '-') avant de reload, ou redemarre Blender "
                "pour charger le code modifie sans risque de crash.",
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
        self.report({"INFO"}, f"Ylos Pipeline reloaded ({purged} modules rechargés).")
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
