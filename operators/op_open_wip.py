# -*- coding: utf-8 -*-
# Ylos Pipeline - operators/op_open_wip.py
# Version picker with thumbnail previews + asset browser.

import bpy
import os
from bpy.props import EnumProperty, StringProperty
from ..core.asset import list_wip_versions, _get_entity_root
from ..core.thumbnails import load_thumb_icon, get_thumb_path


# ---------------------------------------------------------------------------
# Open a specific WIP version (called from the version picker)
# ---------------------------------------------------------------------------

class YLOS_OT_OpenWipVersion(bpy.types.Operator):
    """Open a specific WIP version file."""
    bl_idname = "ylos.open_wip_version"
    bl_label = "Open"
    bl_options = {"REGISTER"}

    version_path: StringProperty(default="")

    def execute(self, context):
        if not self.version_path or not os.path.isfile(self.version_path):
            self.report({"ERROR"}, f"File not found: {self.version_path}")
            return {"CANCELLED"}
        bpy.ops.wm.open_mainfile(filepath=self.version_path)
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Version picker with thumbnail grid
# ---------------------------------------------------------------------------

class YLOS_OT_OpenWip(bpy.types.Operator):
    bl_idname = "ylos.open_wip"
    bl_label = "Pick Version"
    bl_description = "Browse WIP versions with thumbnail previews"
    bl_options = {"REGISTER"}

    # Internal state (not properties — stored on instance during dialog)
    _versions = []

    def invoke(self, context, event):
        scene = context.scene
        if not scene.ylos_project_path or not scene.ylos_current_asset:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}

        self._versions = list_wip_versions(
            scene.ylos_project_path,
            scene.ylos_current_asset,
            scene.ylos_current_step,
            scene.ylos_context_type.lower(),
        )

        if not self._versions:
            self.report({"WARNING"}, "No WIP files found for this asset/step.")
            return {"CANCELLED"}

        # Pre-load all thumbnail icons into the preview collection
        for v in self._versions:
            load_thumb_icon(v["path"])

        return context.window_manager.invoke_popup(self, width=500)

    def draw(self, context):
        scene  = context.scene
        layout = self.layout

        # Header
        header = layout.box()
        row = header.row()
        row.label(
            text=f"{scene.ylos_current_asset}  /  {scene.ylos_current_step}",
            icon="FILE_BLEND",
        )
        row.label(text=f"{len(self._versions)} version(s)")

        layout.separator(factor=0.5)

        # Thumbnail grid — 3 columns
        grid = layout.column_flow(columns=3, align=False)

        for v in reversed(self._versions):   # most recent first
            box = grid.box()
            col = box.column(align=True)

            # Thumbnail
            icon_id = load_thumb_icon(v["path"])
            if icon_id:
                col.template_icon(icon_value=icon_id, scale=6.5)
            else:
                col.label(text="no preview", icon="IMAGE_DATA")

            col.separator(factor=0.3)

            # Version info
            info_row = col.row()
            info_row.label(text=f"v{v['version']:03d}")
            if v.get("date"):
                info_row.label(text=v["date"])

            # Open button
            op = col.operator(
                "ylos.open_wip_version",
                text="Open",
                icon="IMPORT",
            )
            op.version_path = v["path"]

    def execute(self, context):
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Open latest WIP (one-click shortcut)
# ---------------------------------------------------------------------------

class YLOS_OT_OpenLatestWip(bpy.types.Operator):
    bl_idname = "ylos.open_latest_wip"
    bl_label = "Open Latest"
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
            self.report({"WARNING"}, "No WIP files found.")
            return {"CANCELLED"}

        bpy.ops.wm.open_mainfile(filepath=versions[-1]["path"])
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Asset browser — switch active asset from project
# ---------------------------------------------------------------------------

def _build_asset_items(self, context):
    scene = context.scene
    if not scene.ylos_project_path:
        return [("NONE", "No project loaded", "")]

    folder_map = {"asset": "assets", "shot": "shots", "set": "sets"}
    base = os.path.join(
        scene.ylos_project_path,
        folder_map.get(scene.ylos_context_type.lower(), "assets"),
    )

    if not os.path.isdir(base):
        return [("NONE", "Folder not found", "")]

    try:
        entries = sorted([
            d for d in os.listdir(base)
            if os.path.isdir(os.path.join(base, d))
        ])
    except Exception:
        return [("NONE", "Could not read folder", "")]

    return [(e, e, "") for e in entries] if entries else [("NONE", "No entities found", "")]


class YLOS_OT_SwitchAsset(bpy.types.Operator):
    bl_idname = "ylos.switch_asset"
    bl_label = "Browse Assets"
    bl_description = "Switch the active asset to another entity in the project"
    bl_options = {"REGISTER"}

    entity_name: EnumProperty(
        name="Asset",
        items=_build_asset_items,
    )

    def invoke(self, context, event):
        if not context.scene.ylos_project_path:
            self.report({"ERROR"}, "No active project.")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(context.scene, "ylos_context_type")
        layout.prop(self, "entity_name")

    def execute(self, context):
        if not self.entity_name or self.entity_name == "NONE":
            self.report({"WARNING"}, "No entity selected.")
            return {"CANCELLED"}
        context.scene.ylos_current_asset = self.entity_name
        self.report({"INFO"}, f"Switched to: {self.entity_name}")
        return {"FINISHED"}
