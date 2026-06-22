# -*- coding: utf-8 -*-
import bpy
from ..core.asset import (
    list_project_entities,
    get_asset_step_status,
)
from ..core.project import ASSET_STEPS, SHOT_STEPS, SET_STEPS

_STEP_ABBREVS = {
    "modeling":  "M",
    "rigging":   "R",
    "lookdev":   "L",
    "fx":        "F",
    "layout":    "Ly",
    "animation": "An",
    "lighting":  "Lt",
    "render":    "Rn",
    "composite": "Co",
}

_STEP_MAP = {
    "asset": ASSET_STEPS,
    "shot":  SHOT_STEPS,
    "set":   SET_STEPS,
}

_TYPE_ICONS = {
    "PROP":        "MESH_CUBE",
    "CHARACTER":   "ARMATURE_DATA",
    "ENVIRONMENT": "WORLD",
    "SHOT":        "SEQUENCE",
    "SET":         "PACKAGE",
}


class YLOS_PT_AssetListPanel(bpy.types.Panel):
    bl_label = "Assets"
    bl_idname = "YLOS_PT_asset_list"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Ylos"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        s = context.scene
        return bool(s.ylos_project_path and s.ylos_project_name)

    def draw(self, context):
        layout = self.layout
        scene  = context.scene

        ctx_type = scene.ylos_context_type.lower()
        entities = list_project_entities(scene.ylos_project_path, ctx_type)
        steps    = _STEP_MAP.get(ctx_type, ASSET_STEPS)

        header = layout.row(align=True)
        header.prop(scene, "ylos_context_type", text="")
        header.operator("ylos.refresh_asset_list", text="", icon="FILE_REFRESH")
        header.operator("ylos.asset_browser",      text="", icon="VIEWZOOM")

        layout.separator(factor=0.3)

        if not entities:
            layout.label(text="No assets found", icon="INFO")
            layout.operator("ylos.new_asset", text="+ Create first asset", icon="ADD")
            return

        for entity in entities:
            is_active = (entity["name"] == scene.ylos_current_asset)

            row = layout.row(align=True)
            row.scale_y = 1.15

            op = row.operator(
                "ylos.switch_asset_confirm",
                text=entity["name"],
                icon=_TYPE_ICONS.get(entity["type"], "OBJECT_DATA"),
                depress=is_active,
            )
            op.new_asset = entity["name"]

            if is_active:
                status = get_asset_step_status(
                    scene.ylos_project_path, entity["name"], ctx_type
                )
                step_row = row.row(align=True)
                step_row.scale_x = 0.55
                for step in steps:
                    abbrev = _STEP_ABBREVS.get(step, step[:2].capitalize())
                    btn = step_row.operator(
                        "ylos.switch_step_confirm",
                        text=abbrev,
                        depress=(scene.ylos_current_step == step),
                    )
                    btn.new_step = step

        layout.separator(factor=0.3)

        foot = layout.row(align=True)
        foot.scale_y = 0.9
        foot.operator("ylos.new_asset", text="+ New", icon="ADD")
