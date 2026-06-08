# -*- coding: utf-8 -*-
# Ylos Pipeline - operators/op_publish.py
# Exports current step to USD and updates asset_root.usd.

import bpy
import os
from bpy.props import IntProperty, BoolProperty, EnumProperty
from ..core.asset import (
    resolve_publish_path,
    get_latest_publish_version,
    list_publish_versions,
)
from ..core.usd_composer import compose_asset_root, compose_set_root


def _usd_export(filepath):
    """
    Call bpy.ops.wm.usd_export with the safest possible param set.
    Tries a curated list of params first, falls back to filepath-only.
    Always catches Exception (not just TypeError) since Blender raises
    RuntimeError for unrecognised keyword arguments.
    """
    # Params known to exist across Blender 4.2 and 5.x
    # Deliberately minimal — no generate_preview_surface, no export_textures,
    # no visible_objects_only, no overwrite_textures (all removed in 4.x/5.x)
    minimal_kwargs = {
        "filepath":          filepath,
        "export_materials":  True,
        "export_uvmaps":     True,
        "export_normals":    True,
        "export_animation":  False,
    }

    # Attempt 1 — minimal curated kwargs
    try:
        bpy.ops.wm.usd_export(**minimal_kwargs)
        return True, ""
    except Exception as e:
        err1 = str(e)

    # Attempt 2 — filepath only, Blender uses its defaults
    try:
        bpy.ops.wm.usd_export(filepath=filepath)
        return True, ""
    except Exception as e:
        err2 = str(e)

    return False, f"Attempt1: {err1} | Attempt2: {err2}"


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

    step: EnumProperty(
        name="Step",
        description="Production step to publish",
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

        if not scene.ylos_project_path or not scene.ylos_current_asset:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}

        self.step = scene.ylos_current_step

        latest = get_latest_publish_version(
            scene.ylos_project_path,
            scene.ylos_current_asset,
            self.step,
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
        layout.separator()

        layout.prop(self, "step")
        layout.prop(self, "version")
        layout.prop(self, "update_root")

        pub_path = resolve_publish_path(
            scene.ylos_project_path,
            scene.ylos_current_asset,
            self.step,
            self.version,
            "usd",
            scene.ylos_context_type.lower(),
        )
        box = layout.box()
        box.label(text="Publish to:", icon="EXPORT")
        box.label(text=os.path.basename(pub_path))

        existing = [
            v["version"] for v in list_publish_versions(
                scene.ylos_project_path,
                scene.ylos_current_asset,
                self.step,
                scene.ylos_context_type.lower(),
            )
        ]
        if self.version in existing:
            box.label(text="WARNING: will overwrite existing publish", icon="ERROR")

    def execute(self, context):
        scene        = context.scene
        project_path = scene.ylos_project_path
        asset_name   = scene.ylos_current_asset
        ctx_type     = scene.ylos_context_type.lower()
        step         = self.step

        if not project_path or not asset_name:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}

        pub_path = resolve_publish_path(
            project_path, asset_name, step, self.version, "usd", ctx_type
        )

        # Always ensure directory exists before writing
        os.makedirs(os.path.dirname(pub_path), exist_ok=True)

        ok, err = _usd_export(pub_path)
        if not ok:
            self.report({"ERROR"}, f"USD export failed: {err}")
            return {"CANCELLED"}

        scene.ylos_current_step = self.step
        self.report({"INFO"}, f"Published: {os.path.basename(pub_path)}")

        if self.update_root:
            if ctx_type == "asset":
                result = compose_asset_root(project_path, asset_name)
            elif ctx_type == "set":
                result = compose_set_root(project_path, asset_name)
            else:
                result = {"success": True, "message": "Shot — no root USD to update."}

            if result["success"]:
                self.report({"INFO"}, f"Root USD updated. {result['message']}")
            else:
                self.report({"WARNING"}, f"Publish OK — root USD failed: {result['message']}")

        return {"FINISHED"}
