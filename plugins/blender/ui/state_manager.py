# -*- coding: utf-8 -*-
# State Manager - LOGIQUE DE DRAW UNIQUE, montee en DEUX points sans duplication :
#   - section N-panel  : YLOS_PT_StateManager.draw  (ui/panel.py)
#   - fenetre popup     : YLOS_OT_OpenStateManager.draw (operators/op_state_manager.py)
# Dupliquer ce draw etait precisement le defaut du popup a onglets retire (op_popup.py).
# Aucune logique metier ici : uniquement du layout reliant des operateurs existants.

import bpy

from ..operators.op_update_imports import (
    tagged_import_collections, get_cached_update_results,
)


def _has_project(scene):
    return bool(scene.ylos_project_path and scene.ylos_project_name)


def draw_state_manager(layout, context):
    scene = context.scene
    if not _has_project(scene):
        layout.label(text="No project loaded", icon="INFO")
        return
    _draw_export_states(layout, scene)
    layout.separator()
    _draw_import_states(layout, context)


def _draw_export_states(layout, scene):
    layout.label(text="Export States", icon="EXPORT")

    row = layout.row()
    row.template_list(
        "YLOS_UL_export_states", "",
        scene, "ylos_export_states",
        scene, "ylos_export_states_index",
        rows=3,
    )
    side = row.column(align=True)
    side.operator("ylos.state_add_export", text="", icon="ADD")
    side.operator("ylos.state_remove_export", text="", icon="REMOVE")
    side.separator()
    up = side.operator("ylos.state_move_export", text="", icon="TRIA_UP")
    up.direction = "UP"
    down = side.operator("ylos.state_move_export", text="", icon="TRIA_DOWN")
    down.direction = "DOWN"

    states = scene.ylos_export_states
    idx = scene.ylos_export_states_index
    if 0 <= idx < len(states):
        state = states[idx]
        box = layout.box()
        box.use_property_split = True
        box.use_property_decorate = False
        box.prop(state, "entity")
        box.prop(state, "step")
        box.prop(state, "allow_full_scene")
        box.prop(state, "comment")
        if state.last_result:
            box.separator(factor=0.3)
            box.label(text=state.last_result, icon="INFO")

    layout.separator(factor=0.4)
    pub = layout.row(align=True)
    pub.scale_y = 1.4
    pub.enabled = any(s.enabled for s in states)
    pub.operator("ylos.publish_states", text="Publish", icon="EXPORT")


def _draw_import_states(layout, context):
    header = layout.row(align=True)
    header.label(text="Import States", icon="IMPORT")
    header.operator("ylos.check_updates", text="", icon="FILE_REFRESH")

    tagged = tagged_import_collections()
    if not tagged:
        layout.label(text="Nothing imported yet", icon="INFO")
        return

    cache = get_cached_update_results()
    col = layout.column(align=True)
    for coll in sorted(tagged, key=lambda c: c.name):
        entity  = coll.get("ylos_import_entity", "?")
        istep   = coll.get("ylos_import_step", "?")
        version = coll.get("ylos_import_version", 0)

        line = col.row(align=True)
        line.label(text=f"{entity} / {istep}", icon="OUTLINER_COLLECTION")
        right = line.row()
        right.alignment = "RIGHT"
        right.label(text=f"v{version:03d}")

        status = cache.get(coll.name)
        if status and status.get("has_update"):
            upd = col.row(align=True)
            warn = upd.row()
            warn.alert = True
            warn.label(text=f"Update available: v{status['latest']:03d}", icon="ERROR")
            op = upd.operator("ylos.update_import", text="Update", icon="FILE_REFRESH")
            op.collection_name = coll.name
        col.separator(factor=0.2)
