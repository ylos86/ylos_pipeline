# -*- coding: utf-8 -*-
# Ylos Pipeline - ui/panel_pipeline.py
# N-panel layout: Project, Asset context, Scene settings.

import bpy
import os
from ..core.asset import (
    list_wip_versions,
    list_publish_versions,
    get_latest_wip_version,
    get_latest_publish_version,
)
from ..core.project import SCENE_PRESETS


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _has_project(scene) -> bool:
    return bool(scene.ylos_project_path and scene.ylos_project_name)


def _wip_folder(scene) -> str:
    if not _has_project(scene) or not scene.ylos_current_asset:
        return ""
    ctx = scene.ylos_context_type.lower()
    base = {
        "asset": os.path.join(scene.ylos_project_path, "assets"),
        "shot":  os.path.join(scene.ylos_project_path, "shots"),
        "set":   os.path.join(scene.ylos_project_path, "sets"),
    }.get(ctx, "")
    return os.path.join(base, scene.ylos_current_asset,
                        scene.ylos_current_step, "wip")


def _pub_folder(scene) -> str:
    if not _has_project(scene) or not scene.ylos_current_asset:
        return ""
    ctx = scene.ylos_context_type.lower()
    base = {
        "asset": os.path.join(scene.ylos_project_path, "assets"),
        "shot":  os.path.join(scene.ylos_project_path, "shots"),
        "set":   os.path.join(scene.ylos_project_path, "sets"),
    }.get(ctx, "")
    return os.path.join(base, scene.ylos_current_asset,
                        scene.ylos_current_step, "publish")


# ---------------------------------------------------------------------------
# Panel: Project
# ---------------------------------------------------------------------------

class YLOS_PT_PipelinePanel(bpy.types.Panel):
    bl_label = "Project"
    bl_idname = "YLOS_PT_pipeline"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Ylos"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        if not _has_project(scene):
            # No project loaded
            col = layout.column(align=True)
            col.scale_y = 1.4
            col.operator("ylos.new_project", icon="ADD")
            col.operator("ylos.open_context", icon="FILE_FOLDER",
                         text="Load Project")
            return

        # Project info box
        box = layout.box()
        row = box.row()
        row.label(text=scene.ylos_project_name, icon="FUND")
        row.label(text=scene.ylos_prod_type)

        # Open project root folder
        op = box.operator("ylos.open_folder", text="Open Project Folder",
                          icon="FOLDER_REDIRECT")
        op.folder_path = scene.ylos_project_path

        layout.separator()

        # Entity actions
        col = layout.column(align=True)
        col.operator("ylos.new_asset",    icon="ADD",       text="New Asset / Shot / Set")
        col.operator("ylos.switch_asset", icon="ARROW_LEFTRIGHT", text="Switch Asset")


# ---------------------------------------------------------------------------
# Panel: Asset context
# ---------------------------------------------------------------------------

class YLOS_PT_AssetPanel(bpy.types.Panel):
    bl_label = "Asset Context"
    bl_idname = "YLOS_PT_asset"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Ylos"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return _has_project(context.scene)

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column(align=True)
        col.prop(scene, "ylos_context_type")
        if scene.ylos_context_type == "ASSET":
            col.prop(scene, "ylos_asset_type")

        layout.separator()

        if not scene.ylos_current_asset:
            layout.label(text="Create or switch to an asset to continue", icon="INFO")
            return

        # Current context display
        box = layout.box()
        col = box.column(align=True)
        col.label(text="Active Context", icon="CHECKMARK")

        row = col.row(align=True)
        row.label(text=f"Asset:  {scene.ylos_current_asset}", icon="OBJECT_DATA")
        op = row.operator("ylos.switch_asset_confirm", text="", icon="ARROW_LEFTRIGHT")
        op.new_asset = scene.ylos_current_asset

        row2 = col.row(align=True)
        row2.label(
            text=f"Step:   {scene.ylos_current_step}",
            icon="SEQUENCE",
        )
        row2.operator("ylos.switch_step_confirm", text="", icon="ARROW_LEFTRIGHT")

        # Dirty file warning
        if bpy.data.is_dirty:
            warn = layout.box()
            warn.label(text="Unsaved changes in current file", icon="ERROR")

        layout.separator()

        # WIP section
        box = layout.box()
        col = box.column(align=True)
        col.label(text="WIP", icon="FILE_BLEND")

        ctx_type = scene.ylos_context_type.lower()
        latest_wip = get_latest_wip_version(
            scene.ylos_project_path, scene.ylos_current_asset,
            scene.ylos_current_step, ctx_type
        )

        if latest_wip:
            col.label(text=f"Latest: v{latest_wip:03d}")
        else:
            col.label(text="No WIP saved yet")

        # Open actions
        row = col.row(align=True)
        row.scale_y = 1.3
        row.operator("ylos.open_latest_wip", icon="FILE_FOLDER", text="Open Latest")
        row.operator("ylos.open_wip",        icon="TRIA_DOWN",   text="Pick Version")

        col.separator()

        # Save
        row = col.row(align=True)
        row.scale_y = 1.3
        row.operator("ylos.save_wip", icon="FILE_TICK", text="Save WIP")

        # Open WIP folder shortcut
        op = col.operator("ylos.open_folder", text="Open WIP Folder",
                          icon="FOLDER_REDIRECT")
        op.folder_path = _wip_folder(scene)

        layout.separator()

        # Publish section
        box = layout.box()
        col = box.column(align=True)
        col.label(text="Publish", icon="EXPORT")

        latest_pub = get_latest_publish_version(
            scene.ylos_project_path, scene.ylos_current_asset,
            scene.ylos_current_step, ctx_type
        )

        if latest_pub:
            col.label(text=f"Latest publish: v{latest_pub:03d}")
        else:
            col.label(text="No publish yet")

        row = col.row(align=True)
        row.scale_y = 1.3
        row.operator("ylos.publish", icon="EXPORT", text="Publish Step")

        # Open publish folder shortcut
        op = col.operator("ylos.open_folder", text="Open Publish Folder",
                          icon="FOLDER_REDIRECT")
        op.folder_path = _pub_folder(scene)


# ---------------------------------------------------------------------------
# Panel: Scene Settings
# ---------------------------------------------------------------------------

class YLOS_PT_SceneSettingsPanel(bpy.types.Panel):
    bl_label = "Scene Settings"
    bl_idname = "YLOS_PT_scene_settings"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Ylos"
    bl_order = 2

    @classmethod
    def poll(cls, context):
        return _has_project(context.scene)

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        render = scene.render

        layout.use_property_split = True
        layout.use_property_decorate = False

        # Preset info from current prod type
        preset = SCENE_PRESETS.get(scene.ylos_prod_type, {})

        box = layout.box()
        col = box.column(align=True)
        col.label(text="Active Preset", icon="SETTINGS")
        col.label(text=f"FPS: {render.fps} / {render.fps_base:.1f}")
        col.label(text=f"Renderer: {render.engine}")
        col.label(text=f"Resolution: {render.resolution_x} x {render.resolution_y}")
        col.label(text=f"Color: {scene.view_settings.view_transform}")

        layout.separator()

        # Re-apply preset button (useful after manually tweaking settings)
        layout.operator(
            "ylos.new_project",
            text="Re-apply Scene Preset",
            icon="FILE_REFRESH",
        )
