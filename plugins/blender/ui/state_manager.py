# -*- coding: utf-8 -*-
# State Manager - SINGLE DRAW LOGIC, mounted at TWO points without duplication:
#   - N-panel section : YLOS_PT_StateManager.draw  (ui/panel.py)
#   - popup window    : YLOS_OT_OpenStateManager.draw (operators/op_state_manager.py)
# Duplicating this draw was precisely the flaw of the removed tabbed popup (op_popup.py).
# No business logic here: only layout wiring existing operators.

import bpy

from ..operators.op_update_imports import (
    tagged_import_collections, get_cached_update_results, get_cached_downstream_impact,
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

    _draw_downstream_impact(layout)


def _draw_downstream_impact(layout):
    """"Used in" of the LAST publish of this session: which entities consume what we just
    republished and still pin an older version. Read from the cache filled by
    op_publish.publish_entity_step (create_project.entity_dependencies) - draw() never walks
    the dependency index itself, it is a full-project scan.

    Answers the Prism question the plan asks for ("republishing an asset must surface every
    shot that references an older version") without leaving Blender."""
    impact = get_cached_downstream_impact()
    outdated = impact.get("outdated") or []
    if not impact.get("entity") or not outdated:
        return

    layout.separator(factor=0.4)
    box = layout.box().column(align=True)
    head = box.row(align=True)
    head.alert = True
    head.label(text=f"Outdated after {impact['entity']}", icon="ERROR")

    # One line per CONSUMER (an entity can reference several steps of the same dependency;
    # the actionable unit is the consumer, not the edge).
    seen = {}
    for edge in outdated:
        consumer = edge.get("consumer") or {}
        dependency = edge.get("dependency") or {}
        name = consumer.get("entity")
        if not name or name in seen:
            continue
        seen[name] = (dependency.get("version"), dependency.get("latest_version"))
    for name in sorted(seen):
        pinned, latest = seen[name]
        row = box.row(align=True)
        row.label(text=name, icon="OUTLINER_COLLECTION")
        right = row.row()
        right.alignment = "RIGHT"
        pinned_txt = f"v{pinned:03d}" if isinstance(pinned, int) else "unpinned"
        latest_txt = f"v{latest:03d}" if isinstance(latest, int) else "?"
        right.label(text=f"{pinned_txt} -> {latest_txt}")


def _draw_import_states(layout, context):
    header = layout.row(align=True)
    header.label(text="Import States", icon="IMPORT")
    # Entry point to import a new product / file (Import / Export panel).
    header.operator("ylos.open_io", text="Import…", icon="IMPORT")
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
