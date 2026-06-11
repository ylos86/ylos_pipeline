# -*- coding: utf-8 -*-
# Ylos Pipeline - Blender Production Pipeline Addon
# Compatible: Blender 4.2 LTS and 5.x
#
# Architecture: monorepo ylos_pipeline
#   ylos_core/       -- pure stdlib, shared with Houdini adapter
#   ylos_blender/    -- this addon (bpy-dependent layer)
#   _vendor/ylos_core/ -- vendored copy of ylos_core, populated by build.py
#
# sys.path/sys.modules management (C3):
#   - _vendor/ is inserted at position 0 at module load time so our vendored
#     core always wins over any other ylos_core on the path.
#   - If ylos_core is already in sys.modules but points elsewhere (stale from
#     a previous install or a different addon), it is purged before we inject
#     our path, so our version is loaded cleanly.
#   - On unregister, both sys.path and sys.modules are cleaned to allow
#     disable -> zip-replace -> enable without a Blender restart.
#   - Assumption: only one Ylos Pipeline addon active per Blender session.
#     Running multiple versions simultaneously is unsupported; the last one
#     to register wins the sys.modules slot.

bl_info = {
    "name": "Ylos Pipeline",
    "author": "Ylos Prod",
    "version": (0, 3, 1),
    "blender": (4, 2, 0),
    "location": "3D Viewport Header > Ylos button",
    "description": "Production pipeline - USD publish, WIP versioning, scene naming checker",
    "category": "Pipeline",
}

import sys
from pathlib import Path

_vendor_path = str(Path(__file__).parent / "_vendor")

# --- Purge stale ylos_core from sys.modules if it doesn't point to our vendor.
# This defends against a stale core loaded from a previous addon version or
# from a system-wide install.
if "ylos_core" in sys.modules:
    existing_file = getattr(sys.modules["ylos_core"], "__file__", "") or ""
    if _vendor_path not in existing_file:
        _stale = [k for k in list(sys.modules) if k == "ylos_core" or k.startswith("ylos_core.")]
        for _k in _stale:
            del sys.modules[_k]

# --- Inject vendor path so "from ylos_core.xxx import yyy" works in submodules.
if _vendor_path not in sys.path:
    sys.path.insert(0, _vendor_path)

import bpy
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
    layout = self.layout
    scene  = context.scene
    layout.separator()
    row = layout.row(align=True)
    row.operator("ylos.open_popup", text="Ylos", icon="FUND")
    if scene.ylos_project_name and scene.ylos_current_asset:
        row.label(text=f"{scene.ylos_current_asset}  -  {scene.ylos_current_step}")


def register():
    from .core_bpy.project_bpy import register_properties
    register_properties()

    bpy.types.Scene.ylos_popup_tab = bpy.props.EnumProperty(
        name="Tab",
        items=[
            ("PIPELINE", "Pipeline", ""),
            ("ASSETS",   "Assets",   ""),
            ("SCENE",    "Scene",    ""),
        ],
        default="PIPELINE",
    )

    from .core_bpy.thumbnails import init_previews
    init_previews()

    for cls in _classes:
        bpy.utils.register_class(cls)

    bpy.types.VIEW3D_HT_header.append(_draw_header_button)


def unregister():
    bpy.types.VIEW3D_HT_header.remove(_draw_header_button)

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)

    from .core_bpy.project_bpy import unregister_properties
    unregister_properties()

    if hasattr(bpy.types.Scene, "ylos_popup_tab"):
        del bpy.types.Scene.ylos_popup_tab

    from .core_bpy.thumbnails import clear_previews
    clear_previews()

    # Purge ylos_core from sys.modules so the next enable loads a fresh copy.
    _stale = [k for k in list(sys.modules) if k == "ylos_core" or k.startswith("ylos_core.")]
    for _k in _stale:
        del sys.modules[_k]

    if _vendor_path in sys.path:
        sys.path.remove(_vendor_path)


if __name__ == "__main__":
    register()
