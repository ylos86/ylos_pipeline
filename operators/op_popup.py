# -*- coding: utf-8 -*-
# Ylos Pipeline - operators/op_popup.py
# Main popup with Pipeline / Assets / Scene tabs, opened from the header button.
# UI goals: clear state header, strong visual hierarchy, red alert on errors,
# two-line issues so long messages do not truncate, prominent primary actions.

import bpy
from ..core.asset import (
    get_latest_wip_version,
    get_latest_publish_version,
    list_project_entities,
)

POPUP_WIDTH = 400

# Tab choices stored on scene (ylos_popup_tab is registered in __init__.py).
TAB_ITEMS = [
    ("PIPELINE", "Pipeline", ""),
    ("ASSETS",   "Assets",   ""),
    ("SCENE",    "Scene",    ""),
]

# Steps per context: (id, short label, full label).
_STEP_MAP = {
    "ASSET": [("modeling", "Mod", "Modeling"), ("rigging", "Rig", "Rigging"),
              ("lookdev", "LkD", "LookDev"), ("fx", "FX", "FX")],
    "SHOT":  [("layout", "Lay", "Layout"), ("animation", "Anm", "Animation"),
              ("lighting", "Lgt", "Lighting"), ("fx", "FX", "FX"),
              ("render", "Rnd", "Render"), ("composite", "Cmp", "Composite")],
    "SET":   [("modeling", "Mod", "Modeling"), ("lookdev", "LkD", "LookDev"),
              ("lighting", "Lgt", "Lighting")],
}

_TYPE_ICONS = {
    "PROP":        "MESH_CUBE",
    "CHARACTER":   "ARMATURE_DATA",
    "ENVIRONMENT": "WORLD",
    "SHOT":        "SEQUENCE",
    "SET":         "PACKAGE",
}

# Semantic severity icons: red X for errors, warning triangle, check for OK.
_SEVERITY_ICONS = {
    "ERROR":   "CANCEL",
    "WARNING": "ERROR",
    "OK":      "CHECKMARK",
}


def _step_full_label(context_type, step_id):
    for sid, _short, full in _STEP_MAP.get(context_type, _STEP_MAP["ASSET"]):
        if sid == step_id:
            return full
    return step_id.capitalize()


class YLOS_OT_OpenPopup(bpy.types.Operator):
    bl_idname = "ylos.open_popup"
    bl_label = "Ylos Pipeline"
    bl_description = "Open the Ylos Pipeline panel"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=POPUP_WIDTH)

    def draw(self, context):
        layout = self.layout
        scene  = context.scene

        self._draw_state_header(layout, context)
        self._draw_tabs(layout, scene)

        tab = scene.ylos_popup_tab
        if tab == "PIPELINE":
            self._draw_pipeline(layout, context)
        elif tab == "ASSETS":
            self._draw_assets(layout, context)
        else:
            self._draw_scene(layout, context)

    # --- Persistent state header ---
    def _draw_state_header(self, layout, context):
        scene = context.scene
        box   = layout.box()
        col   = box.column(align=True)

        top = col.row(align=True)
        if scene.ylos_project_name:
            top.label(text=scene.ylos_project_name, icon="OUTLINER_COLLECTION")
            sub = top.row()
            sub.alignment = "RIGHT"
            sub.label(text=scene.ylos_prod_type)
        else:
            err = top.row()
            err.alert = True
            err.label(text="No project loaded", icon="ERROR")
            return

        # Active asset + step in full words.
        if scene.ylos_current_asset:
            line = col.row(align=True)
            line.label(
                text=scene.ylos_current_asset,
                icon=_TYPE_ICONS.get(scene.ylos_asset_type, "OBJECT_DATA"),
            )
            step = line.row()
            step.alignment = "RIGHT"
            step.label(
                text=_step_full_label(scene.ylos_context_type,
                                      scene.ylos_current_step),
                icon="SEQUENCE",
            )
        else:
            col.label(text="No active asset", icon="INFO")

        if bpy.data.is_dirty:
            warn = col.row()
            warn.alert = True
            warn.label(text="Unsaved changes", icon="FILE_HIDDEN")

    # --- Tabs ---
    def _draw_tabs(self, layout, scene):
        row = layout.row(align=True)
        row.scale_y = 1.15
        row.prop_enum(scene, "ylos_popup_tab", "PIPELINE")
        row.prop_enum(scene, "ylos_popup_tab", "ASSETS")
        row.prop_enum(scene, "ylos_popup_tab", "SCENE")
        layout.separator(factor=0.4)

    # --- Tab: Pipeline ---
    def _draw_pipeline(self, layout, context):
        scene    = context.scene
        has_proj = bool(scene.ylos_project_path and scene.ylos_project_name)

        if not has_proj:
            col = layout.column(align=True)
            col.scale_y = 1.4
            col.operator("ylos.new_project",  icon="ADD",         text="New Project")
            col.operator("ylos.open_context", icon="FILE_FOLDER", text="Load Project")
            return

        if not scene.ylos_current_asset:
            info = layout.box().column(align=True)
            info.label(text="No active asset", icon="INFO")
            info.label(text="Create one or pick from the Assets tab.")
            info.separator(factor=0.3)
            info.operator("ylos.new_asset", text="New Asset", icon="ADD")
            return

        ctx_type = scene.ylos_context_type.lower()

        # Step selector row (full-width, depress on active).
        step_box = layout.box().column(align=True)
        head = step_box.row(align=True)
        head.label(text="Step", icon="SEQUENCE")
        switch = head.row()
        switch.alignment = "RIGHT"
        op = switch.operator("ylos.switch_asset_confirm",
                             text="Switch Asset", icon="ARROW_LEFTRIGHT")
        op.new_asset = scene.ylos_current_asset

        steps = _STEP_MAP.get(scene.ylos_context_type, _STEP_MAP["ASSET"])
        srow = step_box.row(align=True)
        srow.scale_y = 1.1
        for sid, short, _full in steps:
            b = srow.operator("ylos.switch_step_confirm", text=short,
                              depress=(scene.ylos_current_step == sid))
            b.new_step = sid

        layout.separator(factor=0.4)

        # WIP block.
        wip = layout.box().column(align=True)
        latest_wip = get_latest_wip_version(
            scene.ylos_project_path, scene.ylos_current_asset,
            scene.ylos_current_step, ctx_type)
        h = wip.row(align=True)
        h.label(text="WIP", icon="FILE_BLEND")
        ver = h.row(); ver.alignment = "RIGHT"
        ver.label(text=f"v{latest_wip:03d}" if latest_wip else "none yet")
        wip.separator(factor=0.2)
        save = wip.row(align=True)
        save.scale_y = 1.25
        save.operator("ylos.save_wip", text="Save WIP", icon="FILE_TICK")
        open_row = wip.row(align=True)
        open_row.operator("ylos.open_latest_wip", text="Open Latest", icon="IMPORT")
        open_row.operator("ylos.open_wip", text="", icon="DOWNARROW_HLT")

        layout.separator(factor=0.4)

        # Publish block.
        pub = layout.box().column(align=True)
        latest_pub = get_latest_publish_version(
            scene.ylos_project_path, scene.ylos_current_asset,
            scene.ylos_current_step, ctx_type)
        ph = pub.row(align=True)
        ph.label(text="Publish", icon="EXPORT")
        pver = ph.row(); pver.alignment = "RIGHT"
        pver.label(text=f"v{latest_pub:03d}" if latest_pub else "none yet")
        pub.separator(factor=0.2)
        pbtn = pub.row(align=True)
        pbtn.scale_y = 1.25
        pbtn.operator("ylos.publish", text="Publish Step", icon="EXPORT")
        load_row = pub.row(align=True)
        load_row.operator("ylos.load_latest_publish", text="Load Latest", icon="IMPORT")
        load_row.operator("ylos.load_publish", text="", icon="DOWNARROW_HLT")

    # --- Tab: Assets ---
    def _draw_assets(self, layout, context):
        scene = context.scene
        if not scene.ylos_project_path:
            layout.box().label(text="No project loaded", icon="INFO")
            return

        ctx_type = scene.ylos_context_type.lower()
        entities = list_project_entities(scene.ylos_project_path, ctx_type)

        hdr = layout.row(align=True)
        hdr.prop(scene, "ylos_context_type", text="")
        hdr.operator("ylos.asset_browser",      text="", icon="VIEWZOOM")
        hdr.operator("ylos.refresh_asset_list", text="", icon="FILE_REFRESH")
        layout.separator(factor=0.3)

        if not entities:
            empty = layout.box().column(align=True)
            empty.label(text="No assets yet", icon="INFO")
            empty.operator("ylos.new_asset", text="Create First Asset", icon="ADD")
            return

        lst = layout.box().column(align=True)
        for e in entities:
            is_active = (e["name"] == scene.ylos_current_asset)
            row = lst.row(align=True)
            row.scale_y = 1.1
            op = row.operator(
                "ylos.switch_asset_confirm",
                text=e["name"],
                icon=_TYPE_ICONS.get(e["type"], "OBJECT_DATA"),
                depress=is_active,
            )
            op.new_asset = e["name"]

        foot = layout.row(align=True)
        foot.alignment = "RIGHT"
        foot.label(text=f"{len(entities)} {ctx_type}(s)")
        layout.operator("ylos.new_asset", text="New Asset", icon="ADD")

    # --- Tab: Scene ---
    def _draw_scene(self, layout, context):
        from ..operators.op_scene_check import get_cached_results

        actions = layout.row(align=True)
        actions.scale_y = 1.25
        actions.operator("ylos.run_scene_check", text="Scan Scene", icon="VIEWZOOM")
        actions.operator("ylos.fix_all",         text="Fix All",    icon="CHECKMARK")

        results = get_cached_results()
        if not results:
            layout.separator(factor=0.3)
            hint = layout.box()
            hint.label(text="Scan the scene to check naming and readiness.",
                       icon="INFO")
            return

        layout.separator(factor=0.4)

        # Summary line with semantic icons.
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

        # Current step issues.
        self._draw_issue_group(
            layout, "This step", results.get("current_issues", []),
            ok_text="Naming looks clean.")

        # Next step readiness.
        next_step = results.get("next_step")
        if next_step:
            self._draw_issue_group(
                layout, f"Ready for {next_step}?", results.get("next_issues", []),
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
        # Two lines per issue so long messages never truncate.
        cell = parent.column(align=True)
        cell.separator(factor=0.2)

        is_error = issue["severity"] == "ERROR"
        line1 = cell.row(align=True)
        line1.alert = is_error
        line1.label(
            text=issue["obj_name"] or "(scene-level)",
            icon=_SEVERITY_ICONS.get(issue["severity"], "DOT"),
        )
        if issue.get("fix_id"):
            fixr = line1.row()
            fixr.alignment = "RIGHT"
            op = fixr.operator("ylos.auto_fix", text="Fix", icon="TOOL_SETTINGS")
            op.fix_id = issue["fix_id"]

        msg = cell.row(align=True)
        msg.label(text="    " + issue["message"])

    def execute(self, context):
        return {"FINISHED"}
