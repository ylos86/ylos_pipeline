# -*- coding: utf-8 -*-
# N-panel unifie "Ylos" (categorie sidebar). Sections alignees sur le cycle de production :
# Context, Assets (panel_asset_list.py), Scenefile, State Manager, Scene Check. Les sections
# Publish + Imports d'origine sont subsumees par le State Manager (facon Prism : states
# export empilables + un unique Publish + import states) - draw dans ui/state_manager.py.
# Scene Check recueille le scene-checker de l'ancien popup a onglets (op_popup.py) retire.

import os
import sys
import bpy

from ..core.asset import get_latest_wip_version, list_wip_versions
from ..core import vocab
from .state_manager import draw_state_manager
from ..operators.op_scene_check import get_cached_results

_SEVERITY_ICONS = {
    "ERROR":   "CANCEL",
    "WARNING": "ERROR",
    "OK":      "CHECKMARK",
}

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


def _has_project(scene):
    return bool(scene.ylos_project_path and scene.ylos_project_name)


def _has_asset(scene):
    return _has_project(scene) and bool(scene.ylos_current_asset)


def _step_folder(scene, sub):
    ctx = scene.ylos_context_type.lower()
    base = {"asset": "assets", "shot": "shots", "set": "sets"}.get(ctx, "assets")
    return os.path.join(
        scene.ylos_project_path, base,
        scene.ylos_current_asset, scene.ylos_current_step, sub,
    )


def _abbrev(label):
    return label[:3]


# ---------------------------------------------------------------------------
# Section: Context
# ---------------------------------------------------------------------------

class YLOS_PT_Context(bpy.types.Panel):
    bl_label = "Context"
    bl_idname = "YLOS_PT_context"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Ylos"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        scene  = context.scene

        if not _has_project(scene):
            col = layout.column(align=True)
            col.scale_y = 1.4
            col.operator("ylos.new_project", icon="ADD", text="New Project")
            col.operator("ylos.open_context", icon="FILE_FOLDER", text="Load Project")
            return

        target = _cp().get_pipeline_target(scene.ylos_project_path)

        head = layout.box().column(align=True)
        top = head.row(align=True)
        top.label(text=scene.ylos_project_name, icon="FUND")
        badge = top.row()
        badge.alignment = "RIGHT"
        badge.label(text=f"{scene.ylos_prod_type}  ·  {target}")

        actions = head.row(align=True)
        op = actions.operator("ylos.open_folder", text="", icon="FOLDER_REDIRECT")
        op.folder_path = scene.ylos_project_path
        actions.operator("ylos.new_asset", icon="ADD", text="New")
        actions.operator("ylos.asset_browser", icon="VIEWZOOM", text="Browse")

        layout.separator(factor=0.5)

        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(scene, "ylos_context_type")
        if scene.ylos_context_type == "ASSET":
            layout.prop(scene, "ylos_asset_type")
        layout.use_property_split = False

        if not scene.ylos_current_asset:
            layout.separator(factor=0.3)
            layout.label(text="No active asset", icon="INFO")
            return

        layout.separator(factor=0.3)

        if bpy.data.is_dirty:
            warn = layout.box().row()
            warn.alert = True
            warn.label(text="Unsaved changes", icon="FILE_HIDDEN")

        box = layout.box()
        col = box.column(align=True)

        name_row = col.row(align=False)
        name_row.label(text=scene.ylos_current_asset, icon="OBJECT_DATA")
        op = name_row.operator("ylos.switch_asset_confirm", text="Switch",
                               icon="ARROW_LEFTRIGHT")
        op.new_asset = scene.ylos_current_asset

        col.separator(factor=0.3)
        col.label(text="Step:", icon="SEQUENCE")
        steps = vocab.STEP_ITEMS.get(scene.ylos_context_type, vocab.STEP_ITEMS["ASSET"])
        step_row = col.row(align=True)
        step_row.scale_y = 1.1
        for value, label, _desc in steps:
            b = step_row.operator("ylos.switch_step_confirm", text=_abbrev(label),
                                  depress=(scene.ylos_current_step == value))
            b.new_step = value


# ---------------------------------------------------------------------------
# Section: Scenefile
# ---------------------------------------------------------------------------

class YLOS_PT_Scenefile(bpy.types.Panel):
    bl_label = "Scenefile"
    bl_idname = "YLOS_PT_scenefile"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Ylos"
    bl_order = 2

    @classmethod
    def poll(cls, context):
        return _has_asset(context.scene)

    def draw(self, context):
        layout = self.layout
        scene  = context.scene

        latest_wip = get_latest_wip_version(
            scene.ylos_project_path, scene.ylos_current_asset,
            scene.ylos_current_step, scene.ylos_context_type.lower(),
        )

        header = layout.row(align=True)
        header.label(text="WIP", icon="FILE_BLEND")
        ver = header.row()
        ver.alignment = "RIGHT"
        ver.label(text=f"v{latest_wip:03d}" if latest_wip else "none yet")

        if latest_wip:
            versions = list_wip_versions(
                scene.ylos_project_path, scene.ylos_current_asset,
                scene.ylos_current_step, scene.ylos_context_type.lower(),
            )
            last_comment = versions[-1].get("comment") if versions else ""
            if last_comment:
                layout.box().label(text=last_comment, icon="TEXT")

        layout.separator(factor=0.4)
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(scene, "ylos_wip_comment", text="Comment")
        layout.use_property_split = False

        layout.separator(factor=0.3)
        save_row = layout.row(align=True)
        save_row.scale_y = 1.2
        save_row.operator("ylos.save_wip", text="Save Version", icon="FILE_TICK")

        layout.separator(factor=0.4)
        open_row = layout.row(align=True)
        open_row.operator("ylos.open_latest_wip", text="Open Latest", icon="IMPORT")
        open_row.operator("ylos.open_wip", text="", icon="TRIA_DOWN")

        op = layout.operator("ylos.open_folder", text="Open WIP Folder",
                             icon="FOLDER_REDIRECT")
        op.folder_path = _step_folder(scene, "wip")


# ---------------------------------------------------------------------------
# Section: State Manager (facon Prism - draw unique dans ui/state_manager.py, monte aussi
# en popup via ylos.open_state_manager). Subsume les anciennes sections Publish + Imports.
# ---------------------------------------------------------------------------

class YLOS_PT_StateManager(bpy.types.Panel):
    bl_label = "State Manager"
    bl_idname = "YLOS_PT_state_manager"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Ylos"
    bl_order = 3

    @classmethod
    def poll(cls, context):
        return _has_project(context.scene)

    def draw(self, context):
        draw_state_manager(self.layout, context)


# ---------------------------------------------------------------------------
# Section: Scene Check - recueille le scene-checker de l'ancien popup a onglets
# (op_popup._draw_scene, retire). Les operateurs (ylos.run_scene_check / fix_all / auto_fix)
# sont deja enregistres (op_scene_check.py) - cette section n'est que du layout.
# ---------------------------------------------------------------------------

class YLOS_PT_SceneCheck(bpy.types.Panel):
    bl_label = "Scene Check"
    bl_idname = "YLOS_PT_scene_check"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Ylos"
    bl_order = 4
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return _has_asset(context.scene)

    def draw(self, context):
        layout = self.layout

        actions = layout.row(align=True)
        actions.scale_y = 1.2
        actions.operator("ylos.run_scene_check", text="Scan Scene", icon="VIEWZOOM")
        actions.operator("ylos.fix_all",         text="Fix All",    icon="CHECKMARK")

        results = get_cached_results()
        if not results:
            layout.separator(factor=0.3)
            layout.box().label(text="Scan the scene to check naming and readiness.", icon="INFO")
            return

        layout.separator(factor=0.4)
        err  = results["error_count"]
        warn = results["warning_count"]
        summary = layout.box().row(align=True)
        summary.label(text=f"Step: {results['current_step']}", icon="SEQUENCE")
        counts = summary.row(align=True)
        counts.alignment = "RIGHT"
        e = counts.row()
        e.alert = err > 0
        e.label(text=str(err), icon="CANCEL")
        counts.label(text=str(warn), icon="ERROR")

        self._draw_issue_group(layout, "This step", results.get("current_issues", []),
                               ok_text="Naming looks clean.")
        next_step = results.get("next_step")
        if next_step:
            self._draw_issue_group(layout, f"Ready for {next_step}?",
                                   results.get("next_issues", []),
                                   ok_text="Scene is ready for the next step.")

    def _draw_issue_group(self, layout, title, issues, ok_text):
        layout.separator(factor=0.3)
        box = layout.box()
        head = box.row(align=True)
        head.label(text=title, icon="DOT")
        tag = head.row()
        tag.alignment = "RIGHT"
        if not issues:
            tag.label(text="OK", icon="CHECKMARK")
            box.label(text=ok_text)
            return
        blocking = sum(1 for i in issues if i["severity"] == "ERROR")
        if blocking:
            t = tag.row(); t.alert = True
            t.label(text=f"{blocking} blocking", icon="CANCEL")
        else:
            tag.label(text=f"{len(issues)} to review", icon="ERROR")
        for issue in issues:
            self._draw_issue(box, issue)

    def _draw_issue(self, parent, issue):
        cell = parent.column(align=True)
        cell.separator(factor=0.2)
        is_error = issue["severity"] == "ERROR"
        line1 = cell.row(align=True)
        line1.alert = is_error
        line1.label(text=issue["obj_name"] or "(scene-level)",
                    icon=_SEVERITY_ICONS.get(issue["severity"], "DOT"))
        if issue.get("fix_id"):
            fixr = line1.row()
            fixr.alignment = "RIGHT"
            op = fixr.operator("ylos.auto_fix", text="Fix", icon="TOOL_SETTINGS")
            op.fix_id = issue["fix_id"]
        msg = cell.row(align=True)
        msg.label(text="    " + issue["message"])
