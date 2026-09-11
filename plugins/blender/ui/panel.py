# -*- coding: utf-8 -*-
# Unified "Ylos" N-panel (sidebar category). Sections aligned with the production cycle:
# Context, Assets (panel_asset_list.py), Scenefile, State Manager, Scene Check. The original
# Publish + Imports sections are subsumed by the State Manager (Prism-style: stackable
# export states + a single Publish + import states) - drawn in ui/state_manager.py.
# Scene Check gathers the scene-checker of the old tabbed popup (op_popup.py) that was removed.

import os
import sys
import bpy

from ..core.asset import (
    get_latest_wip_version, list_scenefiles, get_entity_step_status,
)
from ..core import vocab
from .state_manager import draw_state_manager
from ..operators.op_scene_check import get_cached_results
from ..operators.op_update_imports import (
    count_available_updates, has_checked_updates, tagged_import_collections,
)

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


# Steps are written IN FULL. The old _abbrev(label) = label[:3] gave
# "Mod"/"Sur"/"Lay"/"Loo"/"Lig": 30px of width saved against a decoding on every read,
# and two steps could collide on their first 3 letters. The N-panel is narrow but
# resizable - Blender truncates a too-long label cleanly, which a brain does not
# do with "Loo".


def _active_step_status(scene):
    """{'status','explicit','derived'} of the active entity+step (schema 2.2), or None when
    the step is not declared for that entity. Reads core.asset's TTL cache: draw() runs on
    every redraw and must never walk a manifest itself."""
    if not _has_asset(scene):
        return None
    status = get_entity_step_status(scene.ylos_project_path, scene.ylos_current_asset)
    return status.get(scene.ylos_current_step)


def _draw_step_status(layout, scene):
    """Production status of the active entity+step + the three explicit actions
    (Review / Approved / Auto). All three go through ylos.set_step_status ->
    create_project.set_step_status, the single point that validates and persists.
    A DERIVED status (empty/wip/published) is displayed but never written."""
    entry = _active_step_status(scene)
    box = layout.box().column(align=True)

    head = box.row(align=True)
    head.label(text="Status", icon="INFO")
    value = head.row()
    value.alignment = "RIGHT"
    if entry is None:
        # Not an error state to hide: a step outside the entity's declared steps cannot
        # carry a status, and set_step_status would rightly refuse it.
        value.label(text="step not declared", icon="ERROR")
        return
    value.label(text=vocab.status_label(entry["status"]),
                icon=vocab.status_icon(entry["status"]))

    if not entry["explicit"]:
        note = box.row(align=True)
        note.scale_y = 0.8
        note.label(text="derived from disk")

    actions = box.row(align=True)
    for value_id, label, _desc in vocab.STEP_STATUS_ITEMS:
        btn = actions.operator(
            "ylos.set_step_status", text=label,
            # 'auto' is depressed when nothing explicit is stored: it IS the current mode.
            depress=(entry["status"] == value_id
                     or (value_id == "auto" and not entry["explicit"])),
        )
        btn.status = value_id


def _draw_updates_line(layout):
    """Compact 'Updates available' line + the ylos.check_updates button. One row, always the
    same place, so a stale import is visible without unfolding the State Manager. Never
    claims "up to date" before a check actually ran (silence and freshness are not the
    same thing)."""
    imported = tagged_import_collections()
    if not imported:
        return
    row = layout.row(align=True)
    n = count_available_updates()
    if not has_checked_updates():
        row.label(text=f"{len(imported)} import(s) - not checked", icon="QUESTION")
    elif n:
        warn = row.row()
        warn.alert = True
        warn.label(text=f"{n} update(s) available", icon="ERROR")
    else:
        row.label(text=f"{len(imported)} import(s) up to date", icon="CHECKMARK")
    right = row.row(align=True)
    right.alignment = "RIGHT"
    right.operator("ylos.check_updates", text="", icon="FILE_REFRESH")


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
        # column(align=True) and not row: in a column each step keeps its full name whatever
        # the N-panel width, whereas a row of 5 buttons crushes them all.
        step_col = col.column(align=True)
        step_col.scale_y = 1.05
        for value, label, _desc in steps:
            b = step_col.operator("ylos.switch_step_confirm", text=label,
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

    def draw_header(self, context):
        """Status of the active step in the panel HEADER: readable while the section is
        collapsed, and free (TTL cache, no disk touch). Cheap discoverability, no
        restructuring of the section itself."""
        entry = _active_step_status(context.scene)
        if entry:
            self.layout.label(text="", icon=vocab.status_icon(entry["status"]))

    def draw(self, context):
        layout = self.layout
        scene  = context.scene

        # Production status of entity+step (schema 2.2) - first thing in the section:
        # "where is this task" comes before "which file am I on".
        _draw_step_status(layout, scene)
        layout.separator(factor=0.4)

        latest_wip = get_latest_wip_version(
            scene.ylos_project_path, scene.ylos_current_asset,
            scene.ylos_current_step, scene.ylos_context_type.lower(),
        )
        # Every DCC's scenefiles for this step (create_project.list_scenefiles): a Houdini
        # .hip* WIP is COUNTED and shown here, but never openable from Blender.
        scenefiles = list_scenefiles(
            scene.ylos_project_path, scene.ylos_current_asset,
            scene.ylos_current_step, scene.ylos_context_type.lower(),
        )
        blend_files = [s for s in scenefiles if s.get("dcc") == "blender"]
        other_dccs = [s for s in scenefiles if s.get("dcc") != "blender"]

        header = layout.row(align=True)
        header.label(text=f"WIP  ({len(blend_files)})", icon="FILE_BLEND")
        ver = header.row()
        ver.alignment = "RIGHT"
        ver.label(text=f"v{latest_wip:03d}" if latest_wip else "none yet")

        # Scene Builder — a fresh, department-contextualized authoring scene in one click.
        # Emphasised when nothing is saved yet: this is what removes the "you must Save
        # Version before anything exists" gap.
        new_row = layout.row(align=True)
        new_row.scale_y = 1.2 if not latest_wip else 1.0
        new_row.operator("ylos.create_scene", text="New Scene", icon="FILE_NEW")

        if blend_files:
            last_comment = blend_files[-1].get("comment")
            if last_comment:
                layout.box().label(text=last_comment, icon="TEXT")

        if other_dccs:
            # Visible, explicitly NOT openable: hiding another DCC's work makes the step
            # look empty when it is not; offering an Open button that fails is worse.
            by_dcc = {}
            for row in other_dccs:
                by_dcc[row["dcc"]] = by_dcc.get(row["dcc"], 0) + 1
            summary = ", ".join(f"{n} {dcc}" for dcc, n in sorted(by_dcc.items()))
            info = layout.row(align=True)
            info.scale_y = 0.9
            info.label(text=f"{summary} WIP (open in its DCC)", icon="FILE_HIDDEN")

        layout.separator(factor=0.4)
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(scene, "ylos_wip_comment", text="Comment")
        layout.use_property_split = False

        layout.separator(factor=0.3)
        save_row = layout.row(align=True)
        save_row.scale_y = 1.2
        save_row.operator("ylos.save_wip", text="Save Version", icon="FILE_TICK")

        # Review / output media over the frame range: a fast viewport playblast, or a
        # real engine render into the versioned cache tier (same place Houdini renders).
        media_row = layout.row(align=True)
        media_row.operator("ylos.playblast", text="Playblast", icon="RENDER_ANIMATION")
        media_row.operator("ylos.render", text="Render", icon="RENDER_STILL")

        layout.separator(factor=0.4)
        open_row = layout.row(align=True)
        open_row.operator("ylos.open_latest_wip", text="Open Latest", icon="IMPORT")
        open_row.operator("ylos.open_wip", text="", icon="TRIA_DOWN")

        op = layout.operator("ylos.open_folder", text="Open WIP Folder",
                             icon="FOLDER_REDIRECT")
        op.folder_path = _step_folder(scene, "wip")

        # Imported products: one compact line, always at the same place (the detail stays
        # in the State Manager section).
        layout.separator(factor=0.4)
        _draw_updates_line(layout)


# ---------------------------------------------------------------------------
# Section: State Manager (Prism-style - single draw in ui/state_manager.py, also mounted
# as a popup via ylos.open_state_manager). Subsumes the old Publish + Imports sections.
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

    def draw_header(self, context):
        """Counters in the header (in-memory only, no disk): how many export states are
        stacked and how many imports are behind. Both are the reason to unfold the section."""
        n_states = len(context.scene.ylos_export_states)
        n_updates = count_available_updates()
        parts = []
        if n_states:
            parts.append(f"{n_states} state{'s' if n_states > 1 else ''}")
        if n_updates:
            parts.append(f"{n_updates} update{'s' if n_updates > 1 else ''}")
        if parts:
            row = self.layout.row()
            row.alert = bool(n_updates)
            row.label(text="  ·  ".join(parts))

    def draw(self, context):
        draw_state_manager(self.layout, context)


# ---------------------------------------------------------------------------
# Section: Scene Check - gathers the scene-checker of the old tabbed popup
# (op_popup._draw_scene, removed). The operators (ylos.run_scene_check / fix_all / auto_fix)
# are already registered (op_scene_check.py) - this section is only layout.
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

    def draw_header(self, context):
        """Error / warning counts of the last scan in the header (in-memory cache). The
        section is DEFAULT_CLOSED: without this, a blocking error stays invisible."""
        results = get_cached_results()
        if not results:
            return
        err, warn = results["error_count"], results["warning_count"]
        row = self.layout.row(align=True)
        if err:
            sub = row.row()
            sub.alert = True
            sub.label(text=str(err), icon="CANCEL")
        if warn:
            row.label(text=str(warn), icon="ERROR")
        if not err and not warn:
            row.label(text="", icon="CHECKMARK")

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
