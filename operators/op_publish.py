# -*- coding: utf-8 -*-
# Ylos Pipeline - operators/op_publish.py
# Exports current step to USD and updates asset_root.usd.

import bpy
import os
from bpy.props import IntProperty, BoolProperty
from ..core.asset import (
    resolve_publish_path,
    get_latest_publish_version,
    list_publish_versions,
)
from ..core.usd_composer import compose_asset_root, compose_set_root


class YLOS_OT_Publish(bpy.types.Operator):
    bl_idname = "ylos.publish"
    bl_label = "Publish Step"
    bl_description = "Export current step to USD and update the asset root composition"
    bl_options = {"REGISTER"}

    version: IntProperty(
        name="Version",
        description="Publish version number (e.g. 1 = v001)",
        min=1,
        max=999,
        default=1,
    )

    update_root: BoolProperty(
        name="Update Root USD",
        description="Recompose asset_root.usd after publish",
        default=True,
    )

    export_materials: BoolProperty(
        name="Export Materials",
        description="Include materials in the USD export",
        default=True,
    )

    def invoke(self, context, event):
        scene = context.scene

        if not scene.ylos_project_path or not scene.ylos_current_asset:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}

        # Suggest latest publish + 1
        latest = get_latest_publish_version(
            scene.ylos_project_path,
            scene.ylos_current_asset,
            scene.ylos_current_step,
            scene.ylos_context_type.lower(),
        )
        self.version = latest + 1

        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, context):
        scene = context.scene
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.label(text=f"Asset : {scene.ylos_current_asset}", icon="OBJECT_DATA")
        layout.label(text=f"Step  : {scene.ylos_current_step}", icon="SEQUENCE")
        layout.separator()

        layout.prop(self, "version")
        layout.prop(self, "export_materials")
        layout.prop(self, "update_root")

        # Preview publish path
        pub_path = resolve_publish_path(
            scene.ylos_project_path,
            scene.ylos_current_asset,
            scene.ylos_current_step,
            self.version,
            "usd",
            scene.ylos_context_type.lower(),
        )
        box = layout.box()
        box.label(text="Publish to:", icon="EXPORT")
        box.label(text=os.path.basename(pub_path))

        # Warn if already exists
        existing = [
            v["version"] for v in list_publish_versions(
                scene.ylos_project_path,
                scene.ylos_current_asset,
                scene.ylos_current_step,
                scene.ylos_context_type.lower(),
            )
        ]
        if self.version in existing:
            box.label(text="WARNING: will overwrite existing publish", icon="ERROR")

    def execute(self, context):
        scene = context.scene
        project_path = scene.ylos_project_path
        asset_name   = scene.ylos_current_asset
        step         = scene.ylos_current_step
        ctx_type     = scene.ylos_context_type.lower()

        if not project_path or not asset_name:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}

        pub_path = resolve_publish_path(
            project_path, asset_name, step, self.version, "usd", ctx_type
        )

        # USD export via Blender native exporter
        # Blender 4.2 LTS - safe USD export parameter set.
        # export_textures / overwrite_textures were removed in 4.x.
        # Textures are handled separately via the textures folder next to the USD.
        try:
            bpy.ops.wm.usd_export(
                filepath=pub_path,
                export_materials=self.export_materials,
                export_uvmaps=True,
                export_normals=True,
                use_instancing=True,
                export_animation=False,
                root_prim_path="/ROOT",
                generate_preview_surface=True,
                selected_objects_only=False,
                visible_objects_only=True,
            )
        except Exception as e:
            self.report({"ERROR"}, f"USD export failed: {e}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Published: {os.path.basename(pub_path)}")

        # Recompose root USD
        if self.update_root:
            if ctx_type == "asset":
                result = compose_asset_root(project_path, asset_name)
            elif ctx_type == "set":
                result = compose_set_root(project_path, asset_name)
            else:
                # Shots don't have a single root USD - skip
                result = {"success": True, "message": "Shot publish - no root USD to update."}

            if result["success"]:
                self.report({"INFO"}, f"Root USD updated. {result['message']}")
            else:
                self.report({"WARNING"}, f"Publish OK but root USD update failed: {result['message']}")

        return {"FINISHED"}
