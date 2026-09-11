# -*- coding: utf-8 -*-
# Ylos Pipeline - Blender Production Pipeline Addon
# Compatible: Blender 4.2 LTS and 5.x

bl_info = {
    "name": "Ylos Pipeline",
    "author": "Ylos Prod",
    "version": (0, 4, 0),
    "blender": (4, 2, 0),
    "location": "3D Viewport Header > Ylos button / Sidebar > Ylos",
    "description": "Production pipeline - State Manager, USD/GLB publish, WIP versioning, scene checker",
    "category": "Pipeline",
}

import bpy
import os
import sys

# Marker read by _twin_modules(): identifies a module object in sys.modules as an
# INSTANCE OF THIS ADDON (see the "dual identity" note on register/unregister below).
_YLOS_ADDON_MODULE = True

# Make create_project.py importable everywhere in the addon
_REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", ".."))


def _purge_create_project_module():
    """Purge 'create_project' from sys.modules if present. Defensive at register() (stale
    module from a previous session / another path) and systematic at unregister()
    (so that a disable -> edit -> enable in the same Blender session reloads the real
    file rather than the cached version - same class of bug as the ylos_core purge of
    the v0.4-monorepo branch, adapted to main's single module, without vendoring)."""
    for key in list(sys.modules):
        if key == "create_project":
            del sys.modules[key]

from . import core
from .core import states
from .ui import panel, panel_asset_list, menu
from .operators import (
    op_new_project, op_new_asset, op_save_wip, op_create_scene, op_playblast, op_render,
    op_publish, op_open_context, op_open_wip, op_switch_context,
    op_import_product, op_update_imports, op_asset_list, op_scene_check, op_step_status,
    op_state_manager, op_io, op_preview,
)

_classes = (
    op_new_project.YLOS_OT_NewProject,
    # PropertyGroups before anything referencing them via CollectionProperty(type=...) -
    # Blender requires the registration order (see op_new_asset.py + states.py, purge INC-2).
    op_new_asset.YLOS_PG_StepToggle,
    states.YLOS_PG_ExportState,
    op_new_asset.YLOS_OT_NewAsset,
    op_save_wip.YLOS_OT_SaveWip,
    op_create_scene.YLOS_OT_CreateScene,
    op_playblast.YLOS_OT_Playblast,
    op_render.YLOS_OT_Render,
    op_publish.YLOS_OT_Publish,
    op_open_context.YLOS_OT_OpenContext,
    op_open_context.YLOS_OT_OpenFolder,
    op_open_context.YLOS_OT_ConvertLegacy,
    op_open_wip.YLOS_OT_OpenWipVersion,
    op_open_wip.YLOS_OT_OpenWip,
    op_open_wip.YLOS_OT_OpenLatestWip,
    op_switch_context.YLOS_OT_SwitchAsset,
    op_switch_context.YLOS_OT_SwitchStep,
    op_step_status.YLOS_OT_SetStepStatus,
    op_import_product.YLOS_OT_ImportProduct,
    op_update_imports.YLOS_OT_CheckUpdates,
    op_update_imports.YLOS_OT_UpdateImport,
    op_preview.YLOS_OT_CapturePreview,
    op_asset_list.YLOS_OT_AssetBrowser,
    op_asset_list.YLOS_OT_RefreshAssetList,
    op_scene_check.YLOS_OT_RunSceneCheck,
    op_scene_check.YLOS_OT_AutoFix,
    op_scene_check.YLOS_OT_FixAll,
    # State Manager (Prism-style) - UIList before the rest (referenced by bl_idname at use).
    op_state_manager.YLOS_UL_ExportStates,
    op_state_manager.YLOS_OT_StateAddExport,
    op_state_manager.YLOS_OT_StateRemoveExport,
    op_state_manager.YLOS_OT_StateMoveExport,
    op_state_manager.YLOS_OT_PublishStates,
    op_state_manager.YLOS_OT_OpenStateManager,
    # Import / Export (Product Browser + raw file I/O) - on-demand panel (ylos.open_io).
    op_io.YLOS_OT_RefreshProducts,
    op_io.YLOS_OT_OpenIO,
    op_io.YLOS_OT_RawImport,
    op_io.YLOS_OT_RawExport,
    menu.YLOS_OT_OpenProjectBrowser,
    menu.YLOS_OT_ReloadPipeline,
    menu.YLOS_OT_About,
    menu.YLOS_MT_TopbarMenu,
    panel.YLOS_PT_Context,
    panel_asset_list.YLOS_PT_AssetListPanel,
    panel.YLOS_PT_Scenefile,
    panel.YLOS_PT_StateManager,
    panel.YLOS_PT_SceneCheck,
)


def _draw_header_button(self, context):
    layout = self.layout
    scene  = context.scene

    layout.separator()

    row = layout.row(align=True)
    # Repurpose: the header button now opens the State Manager (window), replaces
    # the old tabbed popup (op_popup.py) that was removed - same draw as the N-panel section.
    row.operator("ylos.open_state_manager", text="Ylos", icon="PRESET")

    if scene.ylos_project_name and scene.ylos_current_asset:
        row.label(text=f"{scene.ylos_current_asset}  -  {scene.ylos_current_step}")


# ---------------------------------------------------------------------------------------
# Registration state + the DUAL IDENTITY problem (root cause of the quit-time traceback)
# ---------------------------------------------------------------------------------------
# The very same file is reachable under TWO import identities:
#   - 'ylos_pipeline'          -> the addons symlink (what Preferences > Add-ons enables);
#   - 'blender' / 'plugins.blender' -> the repo path (what tools/blender/test_*_headless.py
#     import, and what a bare `--python script.py` run does).
# Each identity builds its OWN class objects, sharing the same bl_idname. Registering the
# second one makes Blender IMPLICITLY unregister the first ("has been registered before,
# unregistering previous") - the first identity's class objects silently become
# is_registered == False WITHOUT its _classes tuple knowing. When Blender then disables the
# userpref addon at quit, its unregister() walked its own (now stale) class objects and
# bpy.utils.unregister_class raised:
#     RuntimeError: unregister_class(...): missing bl_rna attribute from '_RNAMeta'
#     instance (may not be registered)
# ...followed by "Exception in module unregister()". Verified live on 5.2 (probe_unreg.py):
# unregister_class() on a not-registered class RAISES (it does not no-op), whereas
# VIEW3D_HT_header.remove(<absent function>) does NOT raise.
#
# Two complementary defenses, both required:
#   1. STATE-DRIVEN un/registration: we only undo what THIS module identity actually did
#      (_registered_classes / _ui_hooks_installed / _properties_installed), and every
#      unregister_class is guarded by cls.is_registered. A second unregister() is a no-op.
#   2. TWIN RELEASE: register() first asks any other live identity of this same file to
#      unregister cleanly, instead of letting Blender implicitly steal its classes. This
#      keeps the console free of the 40 "registered before" Info lines AND leaves the twin
#      in a consistent state for its own later unregister().
_registered_classes = []      # classes THIS identity registered, in registration order
_ui_hooks_installed = False   # header button + topbar menu appended by THIS identity
_properties_installed = False  # Scene properties installed by THIS identity


def _twin_modules():
    """Other module objects in sys.modules loaded from THIS very file (the addon imported
    under a second identity - see the note above). Never returns this module itself.
    Compares os.path.realpath so the addons symlink and the repo path resolve equal."""
    me = os.path.realpath(__file__)
    myself = sys.modules.get(__name__)
    twins = []
    for mod in list(sys.modules.values()):
        if mod is None or mod is myself:
            continue
        if not getattr(mod, "_YLOS_ADDON_MODULE", False):
            continue
        path = getattr(mod, "__file__", None)
        if not path:
            continue
        try:
            same = os.path.realpath(path) == me
        except OSError:
            continue
        if same:
            twins.append(mod)
    return twins


def _release_twins():
    """Ask every other live identity of this addon to unregister cleanly. Best effort:
    a twin that fails is reported on the console and skipped - it must never prevent THIS
    identity from registering. Returns the number of twins released."""
    released = 0
    for twin in _twin_modules():
        if not (getattr(twin, "_registered_classes", None)
                or getattr(twin, "_ui_hooks_installed", False)
                or getattr(twin, "_properties_installed", False)):
            continue  # that identity holds no registration - nothing to release
        try:
            twin.unregister()
            released += 1
        except Exception as exc:      # noqa: BLE001 - defensive, never fatal
            print(f"[Ylos] twin addon identity {twin.__name__!r} failed to unregister: {exc}")
    return released


def _safe_unregister_class(cls):
    """bpy.utils.unregister_class guarded by cls.is_registered. A class whose bl_idname was
    taken over by another identity is NOT registered any more (is_registered False) and
    unregistering it raises - so we skip it. Returns True if it was actually unregistered."""
    if not getattr(cls, "is_registered", False):
        return False
    try:
        bpy.utils.unregister_class(cls)
        return True
    except (RuntimeError, ValueError) as exc:   # never let a quit / disable raise
        print(f"[Ylos] unregister_class({getattr(cls, '__name__', cls)!r}) skipped: {exc}")
        return False


def _safe_remove_draw(ui_type, func):
    """<UIType>.remove(func) that never raises (a draw function already removed by a twin,
    or a UI type that does not exist in this Blender build)."""
    try:
        ui_type.remove(func)
    except Exception as exc:          # noqa: BLE001 - defensive, never fatal
        print(f"[Ylos] {getattr(ui_type, '__name__', ui_type)}.remove() skipped: {exc}")


def register():
    global _ui_hooks_installed, _properties_installed

    # Idempotent: a double register() (reload, script run twice) undoes this identity's
    # own registration first rather than stacking a second one on top of it.
    if _registered_classes or _ui_hooks_installed or _properties_installed:
        unregister()
    _release_twins()

    _purge_create_project_module()
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)

    from .core import project as proj_module
    proj_module.register_properties()
    _properties_installed = True

    from .core.thumbnails import init_previews
    init_previews()

    for cls in _classes:
        bpy.utils.register_class(cls)
        _registered_classes.append(cls)

    # AFTER registering the classes: CollectionProperty(type=YLOS_PG_ExportState) requires
    # the PropertyGroup to be already registered.
    states.register_properties()

    bpy.types.VIEW3D_HT_header.append(_draw_header_button)
    bpy.types.TOPBAR_MT_editor_menus.append(menu.draw_topbar_menu)
    _ui_hooks_installed = True


def unregister():
    """Undo exactly what THIS module identity registered, and nothing else. Safe to call
    twice, safe to call on an identity whose classes were taken over by a twin, safe at
    Blender quit - it must NEVER raise (see the dual-identity note above)."""
    global _ui_hooks_installed, _properties_installed

    if _ui_hooks_installed:
        _safe_remove_draw(bpy.types.TOPBAR_MT_editor_menus, menu.draw_topbar_menu)
        _safe_remove_draw(bpy.types.VIEW3D_HT_header, _draw_header_button)
        _ui_hooks_installed = False

    if _properties_installed:
        # BEFORE unregistering the classes: remove the CollectionProperty before the
        # PropertyGroup it references.
        states.unregister_properties()

    for cls in reversed(_registered_classes):
        _safe_unregister_class(cls)
    del _registered_classes[:]

    if _properties_installed:
        from .core import project as proj_module
        proj_module.unregister_properties()
        _properties_installed = False

    from .core.thumbnails import clear_previews
    clear_previews()

    _purge_create_project_module()


if __name__ == "__main__":
    register()
