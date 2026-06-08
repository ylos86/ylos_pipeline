# -*- coding: utf-8 -*-
# Ylos Pipeline - Blender Production Pipeline Addon
# Compatible: Blender 4.2 LTS and 5.x

bl_info = {
    "name": "Ylos Pipeline",
    "author": "Ylos Prod",
    "version": (0, 1, 9),
    "blender": (4, 2, 0),
    "location": "View3D > N-Panel > Ylos",
    "description": "Production pipeline manager - USD publish, versioned WIP, thumbnail browser",
    "category": "Pipeline",
}

import bpy
from . import core
from .ui import panel_pipeline
from .operators import (
    op_new_project,
    op_new_asset,
    op_save_wip,
    op_publish,
    op_open_context,
    op_open_wip,
    op_switch_context,
    op_load_publish,
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
    panel_pipeline.YLOS_PT_PipelinePanel,
    panel_pipeline.YLOS_PT_AssetPanel,
    panel_pipeline.YLOS_PT_SceneSettingsPanel,
)


def register():
    from .core import project as proj_module
    proj_module.register_properties()

    from .core.thumbnails import init_previews
    init_previews()

    for cls in _classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)

    from .core import project as proj_module
    proj_module.unregister_properties()

    from .core.thumbnails import clear_previews
    clear_previews()


if __name__ == "__main__":
    register()
