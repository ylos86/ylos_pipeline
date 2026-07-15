# -*- coding: utf-8 -*-
# Ylos Pipeline - Blender Production Pipeline Addon
# Compatible: Blender 4.2 LTS and 5.x

bl_info = {
    "name": "Ylos Pipeline",
    "author": "Ylos Prod",
    "version": (0, 3, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport Header > Ylos button",
    "description": "Production pipeline - USD publish, WIP versioning, scene naming checker",
    "category": "Pipeline",
}

import bpy
import os
import sys

# Make create_project.py importable everywhere in the addon
_REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", ".."))


def _purge_create_project_module():
    """Purge 'create_project' de sys.modules si present. Defensif au register() (module
    stale d'une session precedente / d'un autre chemin) et systematique a l'unregister()
    (pour qu'un disable -> edit -> enable dans la meme session Blender recharge le vrai
    fichier plutot que la version en cache - meme classe de bug que la purge ylos_core de
    la branche v0.4-monorepo, adaptee au module unique de main, sans vendoring)."""
    for key in list(sys.modules):
        if key == "create_project":
            del sys.modules[key]

from . import core
from .ui import panel, panel_asset_list, menu
from .operators import (
    op_new_project, op_new_asset, op_save_wip, op_publish,
    op_open_context, op_open_wip, op_switch_context,
    op_import_product, op_update_imports, op_asset_list, op_scene_check, op_popup,
)

_classes = (
    op_new_project.YLOS_OT_NewProject,
    # PropertyGroup avant l'operateur qui le reference via CollectionProperty(type=...) -
    # Blender exige l'ordre d'enregistrement (cf. op_new_asset.py, purge INC-2).
    op_new_asset.YLOS_PG_StepToggle,
    op_new_asset.YLOS_OT_NewAsset,
    op_save_wip.YLOS_OT_SaveWip,
    op_publish.YLOS_OT_Publish,
    op_open_context.YLOS_OT_OpenContext,
    op_open_context.YLOS_OT_OpenFolder,
    op_open_context.YLOS_OT_ConvertLegacy,
    op_open_wip.YLOS_OT_OpenWipVersion,
    op_open_wip.YLOS_OT_OpenWip,
    op_open_wip.YLOS_OT_OpenLatestWip,
    op_switch_context.YLOS_OT_SwitchAsset,
    op_switch_context.YLOS_OT_SwitchStep,
    op_import_product.YLOS_OT_ImportProduct,
    op_update_imports.YLOS_OT_CheckUpdates,
    op_update_imports.YLOS_OT_UpdateImport,
    op_asset_list.YLOS_OT_AssetBrowser,
    op_asset_list.YLOS_OT_RefreshAssetList,
    op_scene_check.YLOS_OT_RunSceneCheck,
    op_scene_check.YLOS_OT_AutoFix,
    op_scene_check.YLOS_OT_FixAll,
    op_popup.YLOS_OT_OpenPopup,
    menu.YLOS_OT_OpenProjectBrowser,
    menu.YLOS_OT_ReloadPipeline,
    menu.YLOS_OT_About,
    menu.YLOS_MT_TopbarMenu,
    panel.YLOS_PT_Context,
    panel_asset_list.YLOS_PT_AssetListPanel,
    panel.YLOS_PT_Scenefile,
    panel.YLOS_PT_Publish,
    panel.YLOS_PT_Imports,
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
    _purge_create_project_module()
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)

    from .core import project as proj_module
    proj_module.register_properties()

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

    bpy.types.VIEW3D_HT_header.append(_draw_header_button)
    bpy.types.TOPBAR_MT_editor_menus.append(menu.draw_topbar_menu)


def unregister():
    bpy.types.TOPBAR_MT_editor_menus.remove(menu.draw_topbar_menu)
    bpy.types.VIEW3D_HT_header.remove(_draw_header_button)

    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)

    from .core import project as proj_module
    proj_module.unregister_properties()

    if hasattr(bpy.types.Scene, "ylos_popup_tab"):
        del bpy.types.Scene.ylos_popup_tab

    from .core.thumbnails import clear_previews
    clear_previews()

    _purge_create_project_module()


if __name__ == "__main__":
    register()
