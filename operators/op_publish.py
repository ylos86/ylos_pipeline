# -*- coding: utf-8 -*-
# Ylos Pipeline - operators/op_publish.py
# Exports current step to USD and updates asset_root.usd.

import bpy
import os
from bpy.props import IntProperty, BoolProperty, EnumProperty, StringProperty
from ..core.asset import (
    resolve_publish_path,
    get_latest_publish_version,
    list_publish_versions,
)
from ..core.usd_composer import compose_asset_root, compose_set_root
from ..core.scene_checker import get_asset_objects_for_publish


def _usd_export(filepath: str, context,
                asset_name: str = "", step: str = "") -> tuple[bool, str, str]:
    """
    Export USD for the current asset.
    Selects only the asset's objects (collection or name-based),
    exports with selected_objects_only=True, then restores selection.

    Returns (success, error_message, method_used).
    """
    scene    = context.scene
    objects  = []
    method   = "full scene"

    if asset_name:
        objects, method = get_asset_objects_for_publish(scene, asset_name, step)

    if not objects:
        # No asset objects found — warn but still export full scene
        method = "full scene (no asset objects found)"

    # Save current selection state
    prev_selected = [o for o in scene.objects if o.select_get()]
    prev_active   = context.view_layer.objects.active

    try:
        # Deselect all, then select only asset objects
        for o in scene.objects:
            o.select_set(False)

        if objects:
            for o in objects:
                o.select_set(True)
            context.view_layer.objects.active = objects[0]

        # USD export
        try:
            if objects:
                bpy.ops.wm.usd_export(filepath=filepath,
                                      selected_objects_only=True)
            else:
                bpy.ops.wm.usd_export(filepath=filepath)
            return True, "", method
        except Exception:
            # Fallback — filepath only
            try:
                bpy.ops.wm.usd_export(filepath=filepath)
                return True, "", method + " (fallback)"
            except Exception as e:
                return False, str(e), method

    finally:
        # Restore original selection
        for o in scene.objects:
            o.select_set(False)
        for o in prev_selected:
            o.select_set(True)
        context.view_layer.objects.active = prev_active


class YLOS_OT_Publish(bpy.types.Operator):
    bl_idname  = "ylos.publish"
    bl_label   = "Publish Step"
    bl_description = "Export current step to USD and update the asset root composition"
    bl_options = {"REGISTER"}

    version: IntProperty(
        name="Version",
        description="Publish version number (e.g. 1 = v001)",
        min=1, max=999, default=1,
    )

    update_root: BoolProperty(
        name="Update Root USD",
        description="Recompose asset_root.usd after publish",
        default=True,
    )

    variant_name: StringProperty(
        name="Variant",
        description="Optional variant name (e.g. Dirty, Worn). Empty = default publish.",
        default="",
    )

    load_after: BoolProperty(
        name="Load in Scene",
        description="Import the published USD into the current scene after export",
        default=False,
    )

    step: EnumProperty(
        name="Step",
        description="Production step to publish",
        items=[
            ("modeling",  "Modeling",  ""),
            ("rigging",   "Rigging",   ""),
            ("lookdev",   "LookDev",   ""),
            ("fx",        "FX",        ""),
            ("layout",    "Layout",    ""),
            ("animation", "Animation", ""),
            ("lighting",  "Lighting",  ""),
            ("render",    "Render",    ""),
            ("composite", "Composite", ""),
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
        scene  = context.scene
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.label(text=f"Asset: {scene.ylos_current_asset}", icon="OBJECT_DATA")
        layout.separator()
        layout.prop(self, "step")
        layout.prop(self, "version")
        layout.prop(self, "variant_name")
        layout.separator()
        layout.prop(self, "update_root")
        layout.prop(self, "load_after")

        # Preview filename
        vname    = self.variant_name or "Default"
        pub_path = resolve_publish_path(
            scene.ylos_project_path,
            scene.ylos_current_asset,
            self.step,
            self.version,
            "usd",
            scene.ylos_context_type.lower(),
            self.variant_name,
        )
        box = layout.box()
        box.label(text="Publish to:", icon="EXPORT")
        box.label(text=os.path.basename(pub_path))

        existing = [
            (v["version"], v.get("variant", "Default"))
            for v in list_publish_versions(
                scene.ylos_project_path,
                scene.ylos_current_asset,
                self.step,
                scene.ylos_context_type.lower(),
            )
        ]
        if (self.version, vname) in existing:
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
            project_path, asset_name, step,
            self.version, "usd", ctx_type, self.variant_name,
        )

        os.makedirs(os.path.dirname(pub_path), exist_ok=True)

        ok, err, method = _usd_export(pub_path, context, asset_name, step)
        if not ok:
            self.report({"ERROR"}, f"USD export failed: {err}")
            return {"CANCELLED"}

        scene.ylos_current_step = self.step
        self.report({"INFO"}, f"Published: {os.path.basename(pub_path)} [{method}]")

        if self.load_after:
            try:
                bpy.ops.wm.usd_import(filepath=pub_path)
                self.report({"INFO"}, f"Loaded: {os.path.basename(pub_path)}")
            except Exception as e:
                self.report({"WARNING"}, f"Publish OK — USD import failed: {e}")

        if self.update_root:
            if ctx_type == "asset":
                result = compose_asset_root(project_path, asset_name)
            elif ctx_type == "set":
                result = compose_set_root(project_path, asset_name)
            else:
                result = {"success": True, "message": "Shot — no root USD to update."}

            if result["success"]:
                self.report({"INFO"}, result["message"])
            else:
                self.report({"WARNING"}, f"Publish OK — root USD failed: {result['message']}")

        return {"FINISHED"}
