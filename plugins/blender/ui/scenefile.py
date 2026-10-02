# -*- coding: utf-8 -*-
"""Scenefile window body: step status, WIP, Save Version + comment, playblast / render, open.
Moved out of the removed N-panel section (YLOS_PT_Scenefile) - same operators, same logic,
now mounted by operators/op_windows.py (ylos.open_scenefile). Layout only: no business logic."""

from ..core.asset import get_latest_wip_version, list_scenefiles, get_entity_step_status
from ..core import vocab
from ..operators.op_update_imports import (
    count_available_updates, has_checked_updates, tagged_import_collections,
)
from .common import draw_context_card, has_asset, step_folder


def _active_step_status(scene):
    """{'status','explicit','derived'} of the active entity+step (schema 2.2), or None when the
    step is not declared for that entity. Reads core.asset's TTL cache: draw() runs on every
    redraw and must never walk a manifest itself."""
    if not has_asset(scene):
        return None
    status = get_entity_step_status(scene.ylos_project_path, scene.ylos_current_asset)
    return status.get(scene.ylos_current_step)


def _draw_step_status(layout, scene):
    """Production status of the active entity+step + the three explicit actions
    (Review / Approved / Auto), all through ylos.set_step_status -> create_project.set_step_status
    (the single point that validates and persists). A DERIVED status is displayed, never written."""
    entry = _active_step_status(scene)
    box = layout.box().column(align=True)

    head = box.row(align=True)
    head.label(text="Status", icon="INFO")
    value = head.row()
    value.alignment = "RIGHT"
    if entry is None:
        # Not an error to hide: a step outside the entity's declared steps cannot carry a
        # status, and set_step_status would rightly refuse it.
        value.label(text="step not declared", icon="ERROR")
        return
    value.label(text=vocab.status_label(entry["status"]),
                icon=vocab.status_icon(entry["status"]))

    if not entry["explicit"]:
        note = box.row(align=True)
        note.scale_y = 0.8
        note.label(text="derived from disk")

    actions = box.row(align=True)
    for value_id, label, _desc in vocab.STEP_STATUS_ITEMS:
        btn = actions.operator(
            "ylos.set_step_status", text=label,
            # 'auto' is depressed when nothing explicit is stored: it IS the current mode.
            depress=(entry["status"] == value_id
                     or (value_id == "auto" and not entry["explicit"])),
        )
        btn.status = value_id


def _draw_updates_line(layout):
    """Compact 'Updates available' line + ylos.check_updates. Never claims "up to date" before a
    check actually ran (silence and freshness are not the same thing)."""
    imported = tagged_import_collections()
    if not imported:
        return
    row = layout.row(align=True)
    n = count_available_updates()
    if not has_checked_updates():
        row.label(text=f"{len(imported)} import(s) - not checked", icon="QUESTION")
    elif n:
        warn = row.row()
        warn.alert = True
        warn.label(text=f"{n} update(s) available", icon="ERROR")
    else:
        row.label(text=f"{len(imported)} import(s) up to date", icon="CHECKMARK")
    right = row.row(align=True)
    right.alignment = "RIGHT"
    right.operator("ylos.check_updates", text="", icon="FILE_REFRESH")


def draw_scenefile(layout, context):
    scene = context.scene
    if not has_asset(scene):
        layout.label(text="Pick an asset in the Project Browser first", icon="INFO")
        layout.operator("ylos.asset_browser", text="Project Browser", icon="VIEWZOOM")
        return

    draw_context_card(layout, scene)
    layout.separator(factor=0.4)

    # "Where is this task" comes before "which file am I on".
    _draw_step_status(layout, scene)
    layout.separator(factor=0.4)

    latest_wip = get_latest_wip_version(
        scene.ylos_project_path, scene.ylos_current_asset,
        scene.ylos_current_step, scene.ylos_context_type.lower(),
    )
    # Every DCC's scenefiles for this step (create_project.list_scenefiles): a Houdini .hip* WIP
    # is COUNTED and shown, but never openable from Blender.
    scenefiles = list_scenefiles(
        scene.ylos_project_path, scene.ylos_current_asset,
        scene.ylos_current_step, scene.ylos_context_type.lower(),
    )
    blend_files = [s for s in scenefiles if s.get("dcc") == "blender"]
    other_dccs = [s for s in scenefiles if s.get("dcc") != "blender"]

    header = layout.row(align=True)
    header.label(text=f"WIP  ({len(blend_files)})", icon="FILE_BLEND")
    ver = header.row()
    ver.alignment = "RIGHT"
    ver.label(text=f"v{latest_wip:03d}" if latest_wip else "none yet")

    # Scene Builder: emphasised when nothing is saved yet (removes the "Save Version before
    # anything exists" gap).
    new_row = layout.row(align=True)
    new_row.scale_y = 1.2 if not latest_wip else 1.0
    new_row.operator("ylos.create_scene", text="New Scene", icon="FILE_NEW")

    if blend_files:
        last_comment = blend_files[-1].get("comment")
        if last_comment:
            layout.box().label(text=last_comment, icon="TEXT")

    if other_dccs:
        # Visible, explicitly NOT openable: hiding another DCC's work makes the step look empty.
        by_dcc = {}
        for row in other_dccs:
            by_dcc[row["dcc"]] = by_dcc.get(row["dcc"], 0) + 1
        summary = ", ".join(f"{n} {dcc}" for dcc, n in sorted(by_dcc.items()))
        info = layout.row(align=True)
        info.scale_y = 0.9
        info.label(text=f"{summary} WIP (open in its DCC)", icon="FILE_HIDDEN")

    layout.separator(factor=0.4)
    layout.use_property_split = True
    layout.use_property_decorate = False
    layout.prop(scene, "ylos_wip_comment", text="Comment")
    layout.use_property_split = False

    layout.separator(factor=0.3)
    save_row = layout.row(align=True)
    save_row.scale_y = 1.2
    save_row.operator("ylos.save_wip", text="Save Version", icon="FILE_TICK")

    media_row = layout.row(align=True)
    media_row.operator("ylos.playblast", text="Playblast", icon="RENDER_ANIMATION")
    media_row.operator("ylos.render", text="Render", icon="RENDER_STILL")

    layout.separator(factor=0.4)
    open_row = layout.row(align=True)
    open_row.operator("ylos.open_latest_wip", text="Open Latest", icon="IMPORT")
    open_row.operator("ylos.open_wip", text="", icon="TRIA_DOWN")

    op = layout.operator("ylos.open_folder", text="Open WIP Folder", icon="FOLDER_REDIRECT")
    op.folder_path = step_folder(scene, "wip")

    layout.separator(factor=0.4)
    _draw_updates_line(layout)
