# -*- coding: utf-8 -*-
# Ylos Pipeline - ui/panel_pipeline.py
# Redesigned N-panel: compact project header, step buttons grid,
# asset context box, WIP + Publish sections.

import bpy
import os
from ..core.asset import get_latest_wip_version, get_latest_publish_version
from ..core.project import SCENE_PRESETS

# Steps per context type with abbreviated labels
_ASSET_STEPS = [
    ("modeling",  "Mod"),
    ("rigging",   "Rig"),
    ("lookdev",   "LDv"),
    ("fx",        "FX"),
]
_SHOT_STEPS = [
    ("layout",    "Lay"),
    ("animation", "Anim"),
    ("lighting",  "Lgt"),
    ("fx",        "FX"),
    ("render",    "Rndr"),
    ("composite", "Comp"),
]
_SET_STEPS = [
    ("modeling",  "Mod"),
    ("lookdev",   "LDv"),
    ("lighting",  "Lgt"),
]

_STEP_MAP = {"ASSET": _ASSET_STEPS, "SHOT": _SHOT_STEPS, "SET": _SET_STEPS}


def _has_project(scene):
    return bool(scene.ylos_project_path and scene.ylos_project_name)


def _wip_folder(scene):
    if not _has_project(scene) or not scene.ylos_current_asset:
        return ""
    ctx = scene.ylos_context_type.lower()
    base = {"asset": "assets", "shot": "shots", "set": "sets"}.get(ctx, "assets")
    return os.path.join(
        scene.ylos_project_path, base,
        scene.ylos_current_asset, scene.ylos_current_step, "wip"
    )


def _pub_folder(scene):
    if not _has_project(scene) or not scene.ylos_current_asset:
        return ""
    ctx = scene.ylos_context_type.lower()
    base = {"asset": "assets", "shot": "shots", "set": "sets"}.get(ctx, "assets")
    return os.path.join(
        scene.ylos_project_path, base,
        scene.ylos_current_asset, scene.ylos_current_step, "publish"
    )


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
        scene  = context.scene

        if not _has_project(scene):
            col = layout.column(align=True)
            col.scale_y = 1.4
            col.operator("ylos.new_project", icon="ADD", text="New Project")
            col.operator("ylos.open_context", icon="FILE_FOLDER", text="Load Project")
            return

        # Project header
        row = layout.row(align=False)
        row.label(text=scene.ylos_project_name, icon="FUND")
        row.label(text=scene.ylos_prod_type)

        row2 = layout.row(align=True)
        op = row2.operator("ylos.open_folder", text="", icon="FOLDER_REDIRECT")
        op.folder_path = scene.ylos_project_path
        row2.operator("ylos.new_asset", icon="ADD", text="New")
        row2.operator("ylos.switch_asset", icon="ASSET_MANAGER", text="Browse")


# ---------------------------------------------------------------------------
# Panel: Asset Context
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
        scene  = context.scene

        # Context type + asset type selectors
        row = layout.row(align=True)
        row.prop(scene, "ylos_context_type", text="")
        if scene.ylos_context_type == "ASSET":
            row.prop(scene, "ylos_asset_type", text="")

        layout.separator(factor=0.5)

        if not scene.ylos_current_asset:
            layout.label(text="No active asset", icon="INFO")
            return

        # Unsaved changes warning
        if bpy.data.is_dirty:
            warn = layout.box()
            warn.label(text="Unsaved changes", icon="ERROR")

        # Asset identity box
        box = layout.box()
        col = box.column(align=True)

        # Asset name row
        name_row = col.row(align=False)
        name_row.label(text=scene.ylos_current_asset, icon="OBJECT_DATA")
        op = name_row.operator(
            "ylos.switch_asset_confirm",
            text="Switch",
            icon="ARROW_LEFTRIGHT",
        )
        op.new_asset = scene.ylos_current_asset

        col.separator(factor=0.3)

        # Step buttons grid
        col.label(text="Step:", icon="SEQUENCE")
        steps = _STEP_MAP.get(scene.ylos_context_type, _ASSET_STEPS)
        step_row = col.row(align=True)
        step_row.scale_y = 1.1

        for step_id, step_abbrev in steps:
            is_active = (scene.ylos_current_step == step_id)
            op = step_row.operator(
                "ylos.switch_step_confirm",
                text=step_abbrev,
                depress=is_active,
            )
            op.new_step = step_id

        layout.separator(factor=0.5)

        # WIP section
        wip_box = layout.box()
        wip_col = wip_box.column(align=True)

        latest_wip = get_latest_wip_version(
            scene.ylos_project_path,
            scene.ylos_current_asset,
            scene.ylos_current_step,
            scene.ylos_context_type.lower(),
        )

        wip_header = wip_col.row()
        wip_header.label(text="WIP", icon="FILE_BLEND")
        if latest_wip:
            wip_header.label(text=f"v{latest_wip:03d}")
        else:
            wip_header.label(text="—")

        wip_col.separator(factor=0.3)

        # Open buttons
        open_row = wip_col.row(align=True)
        open_row.operator("ylos.open_latest_wip", text="Open Latest", icon="IMPORT")
        open_row.operator("ylos.open_wip", text="", icon="TRIA_DOWN")

        wip_col.separator(factor=0.3)

        # Save
        save_row = wip_col.row(align=True)
        save_row.scale_y = 1.2
        save_row.operator("ylos.save_wip", text="Save WIP", icon="FILE_TICK")

        op_wip = wip_col.operator(
            "ylos.open_folder", text="Open WIP Folder", icon="FOLDER_REDIRECT"
        )
        op_wip.folder_path = _wip_folder(scene)

        layout.separator(factor=0.5)

        # Publish section
        pub_box = layout.box()
        pub_col = pub_box.column(align=True)

        latest_pub = get_latest_publish_version(
            scene.ylos_project_path,
            scene.ylos_current_asset,
            scene.ylos_current_step,
            scene.ylos_context_type.lower(),
        )

        pub_header = pub_col.row()
        pub_header.label(text="Publish", icon="EXPORT")
        if latest_pub:
            pub_header.label(text=f"v{latest_pub:03d}")
        else:
            pub_header.label(text="—")

        pub_col.separator(factor=0.3)

        pub_row = pub_col.row(align=True)
        pub_row.scale_y = 1.2
        pub_row.operator("ylos.publish", text="Publish Step", icon="EXPORT")

        op_pub = pub_col.operator(
            "ylos.open_folder", text="Open Publish Folder", icon="FOLDER_REDIRECT"
        )
        op_pub.folder_path = _pub_folder(scene)


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
        scene  = context.scene
        render = scene.render

        layout.use_property_split = True
        layout.use_property_decorate = False

        box = layout.box()
        col = box.column(align=True)
        col.label(text="Active Preset", icon="SETTINGS")
        col.label(text=f"FPS: {render.fps} / {render.fps_base:.1f}")
        col.label(text=f"Renderer: {render.engine}")
        col.label(text=f"Resolution: {render.resolution_x} x {render.resolution_y}")
        col.label(text=f"Color: {scene.view_settings.view_transform}")
