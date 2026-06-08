# -*- coding: utf-8 -*-
# Ylos Pipeline - operators/op_open_wip.py
# Browse and open existing WIP versions for the active asset/step.
# Also provides a project-level asset browser to switch context.

import bpy
import os
from bpy.props import EnumProperty, StringProperty
from ..core.asset import (
    list_wip_versions,
    get_latest_wip_version,
    _get_entity_root,
)


# ---------------------------------------------------------------------------
# Open a specific WIP version
# ---------------------------------------------------------------------------

def _build_version_items(self, context):
    """Dynamic enum: list all WIP versions for current asset+step."""
    scene = context.scene
    if not scene.ylos_project_path or not scene.ylos_current_asset:
        return [("NONE", "No versions found", "")]

    versions = list_wip_versions(
        scene.ylos_project_path,
        scene.ylos_current_asset,
        scene.ylos_current_step,
        scene.ylos_context_type.lower(),
    )

    if not versions:
        return [("NONE", "No WIP files found", "")]

    items = []
    for v in reversed(versions):    # most recent first
        label = f"v{v['version']:03d} — {v['filename']}"
        items.append((v["path"], label, v["path"]))

    return items


class YLOS_OT_OpenWip(bpy.types.Operator):
    bl_idname = "ylos.open_wip"
    bl_label = "Open WIP"
    bl_description = "Open an existing WIP version for the active asset and step"
    bl_options = {"REGISTER"}

    version_path: EnumProperty(
        name="Version",
        description="WIP version to open",
        items=_build_version_items,
    )

    def invoke(self, context, event):
        scene = context.scene

        if not scene.ylos_project_path or not scene.ylos_current_asset:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}

        versions = list_wip_versions(
            scene.ylos_project_path,
            scene.ylos_current_asset,
            scene.ylos_current_step,
            scene.ylos_context_type.lower(),
        )

        if not versions:
            self.report({"WARNING"}, "No WIP files found for this asset/step.")
            return {"CANCELLED"}

        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.label(
            text=f"{scene.ylos_current_asset}  /  {scene.ylos_current_step}",
            icon="FILE_BLEND",
        )
        layout.separator()
        layout.prop(self, "version_path", text="Version")

    def execute(self, context):
        if not self.version_path or self.version_path == "NONE":
            self.report({"WARNING"}, "No version selected.")
            return {"CANCELLED"}

        if not os.path.isfile(self.version_path):
            self.report({"ERROR"}, f"File not found: {self.version_path}")
            return {"CANCELLED"}

        bpy.ops.wm.open_mainfile(filepath=self.version_path)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Open latest WIP directly (one-click shortcut)
# ---------------------------------------------------------------------------

class YLOS_OT_OpenLatestWip(bpy.types.Operator):
    bl_idname = "ylos.open_latest_wip"
    bl_label = "Open Latest WIP"
    bl_description = "Open the most recent WIP version for the active asset and step"
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene

        if not scene.ylos_project_path or not scene.ylos_current_asset:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}

        versions = list_wip_versions(
            scene.ylos_project_path,
            scene.ylos_current_asset,
            scene.ylos_current_step,
            scene.ylos_context_type.lower(),
        )

        if not versions:
            self.report({"WARNING"}, "No WIP files found for this asset/step.")
            return {"CANCELLED"}

        latest = versions[-1]["path"]
        bpy.ops.wm.open_mainfile(filepath=latest)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Asset browser — switch active asset from project
# ---------------------------------------------------------------------------

def _build_asset_items(self, context):
    """Dynamic enum: list all assets in the project."""
    scene = context.scene
    if not scene.ylos_project_path:
        return [("NONE", "No project loaded", "")]

    import os
    ctx = scene.ylos_context_type.lower()
    folder_map = {
        "asset": "assets",
        "shot":  "shots",
        "set":   "sets",
    }
    base = os.path.join(scene.ylos_project_path, folder_map.get(ctx, "assets"))

    if not os.path.isdir(base):
        return [("NONE", "Folder not found", "")]

    try:
        entries = sorted([
            d for d in os.listdir(base)
            if os.path.isdir(os.path.join(base, d))
        ])
    except Exception:
        return [("NONE", "Could not read folder", "")]

    if not entries:
        return [("NONE", "No entities found", "")]

    return [(e, e, os.path.join(base, e)) for e in entries]


class YLOS_OT_SwitchAsset(bpy.types.Operator):
    bl_idname = "ylos.switch_asset"
    bl_label = "Switch Asset"
    bl_description = "Switch the active asset context to another entity in the project"
    bl_options = {"REGISTER"}

    entity_name: EnumProperty(
        name="Asset",
        description="Entity to switch to",
        items=_build_asset_items,
    )

    def invoke(self, context, event):
        if not context.scene.ylos_project_path:
            self.report({"ERROR"}, "No active project.")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=340)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(context.scene, "ylos_context_type", text="Type")
        layout.prop(self, "entity_name", text="Entity")

    def execute(self, context):
        if not self.entity_name or self.entity_name == "NONE":
            self.report({"WARNING"}, "No entity selected.")
            return {"CANCELLED"}

        context.scene.ylos_current_asset = self.entity_name
        self.report({"INFO"}, f"Switched to: {self.entity_name}")
        return {"FINISHED"}
