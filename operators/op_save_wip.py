# -*- coding: utf-8 -*-
# Ylos Pipeline - operators/op_save_wip.py
# Manual versioned WIP save. User picks the version number.

import bpy
import os
from bpy.props import IntProperty, EnumProperty
from ..core.asset import (
    resolve_wip_save_path,
    get_latest_wip_version,
    list_wip_versions,
)


class YLOS_OT_SaveWip(bpy.types.Operator):
    bl_idname = "ylos.save_wip"
    bl_label = "Save WIP"
    bl_description = "Save current .blend as a versioned WIP file into the active step folder"
    bl_options = {"REGISTER"}

    version: IntProperty(
        name="Version",
        description="Version number to save as (e.g. 1 = v001)",
        min=1,
        max=999,
        default=1,
    )

    step: EnumProperty(
        name="Step",
        description="Production step to save into",
        items=[
            ("modeling",   "Modeling",   ""),
            ("uvs",        "UVs",        ""),
            ("rigging",    "Rigging",    ""),
            ("lookdev",    "LookDev",    ""),
            ("fx",         "FX",         ""),
            ("layout",     "Layout",     ""),
            ("animation",  "Animation",  ""),
            ("lighting",   "Lighting",   ""),
            ("render",     "Render",     ""),
            ("composite",  "Composite",  ""),
        ],
        default="modeling",
    )

    def invoke(self, context, event):
        scene = context.scene
        project_path = scene.ylos_project_path
        asset_name   = scene.ylos_current_asset
        ctx_type     = scene.ylos_context_type.lower()

        if not project_path or not asset_name:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}

        # Sync step from scene and suggest latest + 1
        self.step = scene.ylos_current_step
        latest = get_latest_wip_version(project_path, asset_name, self.step, ctx_type)
        self.version = latest + 1

        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        scene = context.scene
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.label(text=f"Asset : {scene.ylos_current_asset}", icon="OBJECT_DATA")
        layout.separator()
        layout.prop(self, "step")
        layout.prop(self, "version")

        # Preview filename
        project_path = scene.ylos_project_path
        asset_name   = scene.ylos_current_asset
        ctx_type     = scene.ylos_context_type.lower()

        save_path = resolve_wip_save_path(
            project_path, asset_name, self.step, self.version, ctx_type
        )
        box = layout.box()
        box.label(text="Save to:", icon="FILE_BLEND")
        box.label(text=os.path.basename(save_path))

        # Warn if version already exists
        versions = list_wip_versions(project_path, asset_name, self.step, ctx_type)
        existing = [v["version"] for v in versions]
        if self.version in existing:
            box.label(text="WARNING: version already exists - will overwrite", icon="ERROR")

    def execute(self, context):
        scene = context.scene
        project_path = scene.ylos_project_path
        asset_name   = scene.ylos_current_asset
        ctx_type     = scene.ylos_context_type.lower()

        if not project_path or not asset_name:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}

        save_path = resolve_wip_save_path(
            project_path, asset_name, self.step, self.version, ctx_type
        )

        try:
            # copy=False : saves to WIP path AND updates current file path
            # so Blender title bar reflects the actual filename
            bpy.ops.wm.save_as_mainfile(filepath=save_path, copy=False)
        except Exception as e:
            self.report({"ERROR"}, f"Save failed: {e}")
            return {"CANCELLED"}

        # Sync scene step + rename scene to match
        scene.ylos_current_step = self.step
        scene.name = f"SCENE_{asset_name}_{self.step}"

        self.report({"INFO"}, f"WIP saved: {os.path.basename(save_path)}")
        return {"FINISHED"}
