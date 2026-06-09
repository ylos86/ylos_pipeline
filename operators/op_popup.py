# -*- coding: utf-8 -*-
# Ylos Pipeline - operators/op_popup.py
# Main popup with Pipeline / Assets / Scene tabs.
# Triggered from the Ylos header button.

import bpy
import os
from ..core.asset import (
    get_latest_wip_version,
    get_latest_publish_version,
    list_project_entities,
)
from ..core.project import SCENE_PRESETS
from ..operators.op_scene_check import get_cached_results

# Tab choices stored on scene
TAB_ITEMS = [
    ("PIPELINE", "Pipeline", ""),
    ("ASSETS",   "Assets",   ""),
    ("SCENE",    "Scene",    ""),
]

_STEP_MAP = {
    "ASSET": [("modeling","Mod"),("rigging","Rig"),("lookdev","LDv"),("fx","FX")],
    "SHOT":  [("layout","Lay"),("animation","Anim"),("lighting","Lgt"),
              ("fx","FX"),("render","Rndr"),("composite","Comp")],
    "SET":   [("modeling","Mod"),("lookdev","LDv"),("lighting","Lgt")],
}

_SEVERITY_ICONS = {
    "ERROR":   "ERROR",
    "WARNING": "TRIA_RIGHT",
    "OK":      "CHECKMARK",
}


class YLOS_OT_OpenPopup(bpy.types.Operator):
    bl_idname = "ylos.open_popup"
    bl_label = "Ylos Pipeline"
    bl_description = "Open the Ylos Pipeline panel"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=340)

    def draw(self, context):
        layout = self.layout
        scene  = context.scene

        # --- Header ---
        header = layout.box()
        row = header.row()
        if scene.ylos_project_name:
            row.label(text=scene.ylos_project_name, icon="FUND")
            row.label(text=scene.ylos_prod_type)
        else:
            row.label(text="No project loaded", icon="ERROR")

        # --- Tabs ---
        row = layout.row(align=True)
        row.prop_enum(scene, "ylos_popup_tab", "PIPELINE")
        row.prop_enum(scene, "ylos_popup_tab", "ASSETS")
        row.prop_enum(scene, "ylos_popup_tab", "SCENE")

        layout.separator(factor=0.3)

        tab = scene.ylos_popup_tab

        if tab == "PIPELINE":
            self._draw_pipeline(layout, context)
        elif tab == "ASSETS":
            self._draw_assets(layout, context)
        else:
            self._draw_scene(layout, context)

    # --- Tab: Pipeline ---
    def _draw_pipeline(self, layout, context):
        scene    = context.scene
        has_proj = bool(scene.ylos_project_path and scene.ylos_project_name)

        if not has_proj:
            col = layout.column(align=True)
            col.scale_y = 1.3
            col.operator("ylos.new_project",  icon="ADD",         text="New Project")
            col.operator("ylos.open_context", icon="FILE_FOLDER", text="Load Project")
            return

        # Context box
        ctx = layout.box()
        ctx_col = ctx.column(align=True)

        if bpy.data.is_dirty:
            ctx_col.label(text="Unsaved changes", icon="ERROR")

        if scene.ylos_current_asset:
            name_row = ctx_col.row(align=True)
            name_row.label(text=scene.ylos_current_asset, icon="OBJECT_DATA")
            op = name_row.operator("ylos.switch_asset_confirm",
                                   text="Switch", icon="ARROW_LEFTRIGHT")
            op.new_asset = scene.ylos_current_asset

            ctx_col.separator(factor=0.3)

            steps = _STEP_MAP.get(scene.ylos_context_type, _STEP_MAP["ASSET"])
            step_row = ctx_col.row(align=True)
            for sid, sab in steps:
                op = step_row.operator("ylos.switch_step_confirm",
                                       text=sab,
                                       depress=(scene.ylos_current_step == sid))
                op.new_step = sid
        else:
            ctx_col.label(text="No active asset", icon="INFO")

        layout.separator(factor=0.3)

        if not scene.ylos_current_asset:
            return

        ctx_type = scene.ylos_context_type.lower()

        # WIP
        wip_box = layout.box()
        wip_col = wip_box.column(align=True)
        latest_wip = get_latest_wip_version(
            scene.ylos_project_path, scene.ylos_current_asset,
            scene.ylos_current_step, ctx_type,
        )
        r = wip_col.row()
        r.label(text="WIP", icon="FILE_BLEND")
        r.label(text=f"v{latest_wip:03d}" if latest_wip else "-")
        r2 = wip_col.row(align=True)
        r2.operator("ylos.open_latest_wip", text="Open Latest", icon="IMPORT")
        r2.operator("ylos.open_wip",        text="",            icon="TRIA_DOWN")
        wip_col.operator("ylos.save_wip", text="Save WIP", icon="FILE_TICK")

        layout.separator(factor=0.3)

        # Publish
        pub_box = layout.box()
        pub_col = pub_box.column(align=True)
        latest_pub = get_latest_publish_version(
            scene.ylos_project_path, scene.ylos_current_asset,
            scene.ylos_current_step, ctx_type,
        )
        r3 = pub_col.row()
        r3.label(text="Publish", icon="EXPORT")
        r3.label(text=f"v{latest_pub:03d}" if latest_pub else "-")
        r4 = pub_col.row(align=True)
        r4.operator("ylos.load_latest_publish", text="Load Latest", icon="IMPORT")
        r4.operator("ylos.load_publish",        text="",            icon="TRIA_DOWN")
        pub_col.operator("ylos.publish", text="Publish Step", icon="EXPORT")

    # --- Tab: Assets ---
    def _draw_assets(self, layout, context):
        scene    = context.scene
        has_proj = bool(scene.ylos_project_path)

        if not has_proj:
            layout.label(text="No project loaded", icon="INFO")
            return

        ctx_type = scene.ylos_context_type.lower()
        entities = list_project_entities(scene.ylos_project_path, ctx_type)

        hdr = layout.row(align=True)
        hdr.prop(scene, "ylos_context_type", text="")
        hdr.operator("ylos.refresh_asset_list", text="", icon="FILE_REFRESH")

        layout.separator(factor=0.3)

        _TYPE_ICONS = {
            "PROP":        "MESH_CUBE",
            "CHARACTER":   "ARMATURE_DATA",
            "ENVIRONMENT": "WORLD",
            "SHOT":        "SEQUENCE",
            "SET":         "PACKAGE",
        }

        if not entities:
            layout.label(text="No assets found", icon="INFO")
        else:
            for e in entities:
                is_active = (e["name"] == scene.ylos_current_asset)
                op = layout.operator(
                    "ylos.switch_asset_confirm",
                    text=e["name"],
                    icon=_TYPE_ICONS.get(e["type"], "OBJECT_DATA"),
                    depress=is_active,
                )
                op.new_asset = e["name"]

        layout.separator(factor=0.3)
        layout.operator("ylos.new_asset", text="+ New Asset", icon="ADD")

    # --- Tab: Scene ---
    def _draw_scene(self, layout, context):
        scene = context.scene

        # Scan / refresh button
        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator("ylos.run_scene_check", text="Scan Scene", icon="VIEWZOOM")
        row.operator("ylos.fix_all",         text="Fix All",    icon="CHECKMARK")

        results = get_cached_results()

        if not results:
            layout.separator(factor=0.3)
            layout.label(text="Click Scan to check the scene", icon="INFO")
            return

        layout.separator(factor=0.3)

        # Current step issues
        cur_issues = results.get("current_issues", [])
        box = layout.box()
        col = box.column(align=True)
        hdr = col.row()
        hdr.label(text=f"Step: {results['current_step']}",  icon="SEQUENCE")
        err  = results["error_count"]
        warn = results["warning_count"]
        hdr.label(text=f"{err} err  {warn} warn")

        col.separator(factor=0.3)

        if not cur_issues:
            col.label(text="All good", icon="CHECKMARK")
        else:
            for issue in cur_issues:
                r = col.row(align=True)
                r.label(
                    text=f"{issue['obj_name'] or '(scene)'}  -  {issue['message']}",
                    icon=_SEVERITY_ICONS.get(issue["severity"], "DOT"),
                )
                if issue.get("fix_id"):
                    op = r.operator("ylos.auto_fix", text="Fix", icon="TOOL_SETTINGS")
                    op.fix_id = issue["fix_id"]

        # Next step readiness
        next_step   = results.get("next_step")
        next_issues = results.get("next_issues", [])

        if next_step:
            layout.separator(factor=0.3)
            nbox = layout.box()
            ncol = nbox.column(align=True)
            nhdr = ncol.row()
            nhdr.label(
                text=f"Ready for {next_step}?",
                icon="TRIA_RIGHT",
            )
            n_blocking = sum(1 for i in next_issues if i["severity"] == "ERROR")
            nhdr.label(
                text="Ready" if not next_issues else f"{n_blocking} blocking",
            )
            ncol.separator(factor=0.3)

            if not next_issues:
                ncol.label(text="Scene is ready for next step", icon="CHECKMARK")
            else:
                for issue in next_issues:
                    r = ncol.row(align=True)
                    r.label(
                        text=f"{issue['obj_name'] or '(scene)'}  -  {issue['message']}",
                        icon=_SEVERITY_ICONS.get(issue["severity"], "DOT"),
                    )
                    if issue.get("fix_id"):
                        op = r.operator("ylos.auto_fix", text="Fix",
                                        icon="TOOL_SETTINGS")
                        op.fix_id = issue["fix_id"]

    def execute(self, context):
        return {"FINISHED"}
