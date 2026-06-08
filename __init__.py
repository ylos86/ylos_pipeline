# -*- coding: utf-8 -*-
# Ylos Pipeline - Blender Production Pipeline Addon
# Compatible: Blender 4.2 LTS and 5.x

bl_info = {
    "name": "Ylos Pipeline",
    "author": "Ylos Prod",
    "version": (0, 2, 2),
    "blender": (4, 2, 0),
    "location": "3D Viewport Header > Ylos button",
    "description": "Production pipeline — USD publish, WIP versioning, scene naming checker",
    "category": "Pipeline",
}

import bpy
from . import core
from .ui import panel_pipeline, panel_asset_list
from .operators import (
    op_new_project, op_new_asset, op_save_wip, op_publish,
    op_open_context, op_open_wip, op_switch_context,
    op_load_publish, op_asset_list, op_scene_check, op_popup,
)

_classes = (
    op_new_project.YLOS_OT_NewProject,
    op_new_asset.YLOS_OT_NewAsset,
    op_save_wip.YLOS_OT_SaveWip,
    op_publish.YLOS_OT_Publish,
    op_open_context.YLOS_OT_OpenContext,
    op_open_context.YLOS_OT_OpenFolder,
    op_open_wip.YLOS_OT_OpenWipVersion,
    op_open_wip.YLOS_OT_OpenWip,
    op_open_wip.YLOS_OT_OpenLatestWip,
    op_open_wip.YLOS_OT_SwitchAsset,
    op_switch_context.YLOS_OT_SwitchAsset,
    op_switch_context.YLOS_OT_SwitchStep,
    op_load_publish.YLOS_OT_LoadPublishFile,
    op_load_publish.YLOS_OT_LoadLatestPublish,
    op_load_publish.YLOS_OT_LoadPublish,
    op_asset_list.YLOS_OT_AssetBrowser,
    op_asset_list.YLOS_OT_RefreshAssetList,
    op_scene_check.YLOS_OT_RunSceneCheck,
    op_scene_check.YLOS_OT_AutoFix,
    op_scene_check.YLOS_OT_FixAll,
    op_popup.YLOS_OT_OpenPopup,
    panel_pipeline.YLOS_PT_PipelinePanel,
    panel_pipeline.YLOS_PT_AssetPanel,
    panel_pipeline.YLOS_PT_SceneSettingsPanel,
    panel_asset_list.YLOS_PT_AssetListPanel,
)


def _draw_header_button(self, context):
    """Ylos button injected into the 3D Viewport header."""
    layout = self.layout
    scene  = context.scene

    layout.separator()

    row = layout.row(align=True)
    row.operator("ylos.open_popup", text="Ylos", icon="FUND")

    # Show active context info next to the button
    if scene.ylos_project_name and scene.ylos_current_asset:
        row.label(
            text=f"{scene.ylos_current_asset}  ·  {scene.ylos_current_step}"
        )


def register():
    from .core import project as proj_module
    proj_module.register_properties()

    # Register ylos_popup_tab on scene
    bpy.types.Scene.ylos_popup_tab = bpy.props.EnumProperty(
        name="Tab",
        items=[
            ("PIPELINE", "Pipeline", ""),
            ("ASSETS",   "Assets",   ""),
            ("SCENE",    "Scene",    ""),
        ],
        default="PIPELINE",
    )

    from .core.thumbnails import init_previews
    init_previews()

    for cls in _classes:
        bpy.utils.register_class(cls)

    # Inject header button
    bpy.types.VIEW3D_HT_header.append(_draw_header_button)


def unregister():
    bpy.types.VIEW3D_HT_header.remove(_draw_header_button)

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)

    from .core import project as proj_module
    proj_module.unregister_properties()

    if hasattr(bpy.types.Scene, "ylos_popup_tab"):
        del bpy.types.Scene.ylos_popup_tab

    from .core.thumbnails import clear_previews
    clear_previews()


if __name__ == "__main__":
    register()
