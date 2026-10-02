# -*- coding: utf-8 -*-
"""Project Browser window body (Prism model): the ONLY place that lists the project's other
entities. The workspace itself (3D viewport, sidebar) never shows them.

Replaces three overlapping entry points of the removed N-panel: the 'Switch' button (Context),
the full list (Assets section) and the text-only 'Browse' popup. Mounted as a popup by
operators/op_asset_list.py (ylos.asset_browser).

draw() NEVER touches the disk: thumbnails come from core.entity_thumbs (TTL cache + explicit
invalidation), the entity list from core.asset.list_project_entities (TTL cache), step status
from core.asset.get_entity_step_status (TTL cache).
"""

import bpy

from ..core.asset import list_project_entities, get_entity_step_status
from ..core.project import ASSET_STEPS, SHOT_STEPS, SET_STEPS
from ..core import entity_thumbs
from ..core import vocab
from .common import draw_context_card, has_project

_STEP_MAP = {"asset": ASSET_STEPS, "shot": SHOT_STEPS, "set": SET_STEPS}

_TYPE_ICONS = {
    "PROP":        "MESH_CUBE",
    "CHARACTER":   "ARMATURE_DATA",
    "ENVIRONMENT": "WORLD",
    "SHOT":        "SEQUENCE",
    "SET":         "PACKAGE",
}

# Thumbnail provenance: a WIP preview does not carry the same confidence as a publish. Nothing
# for 'publish' - the normal case does not deserve noise.
_SOURCE_LABELS = {
    "custom": "Manual preview",
    "wip":    "WIP thumbnail",
    "legacy": "Legacy thumbnail",
}

# Name given to the screen of the floating Project Browser window: the host panel's poll and
# browser_window_open() recognise the window by it (a Screen is an ID, its name is writable).
BROWSER_SCREEN = "YLOS_ProjectBrowser"


def browser_window_open():
    return any(w.screen.name.startswith(BROWSER_SCREEN)
               for w in bpy.context.window_manager.windows)


# --- Hiding Blender's own Scene-tab panels in the browser window -----------------------------
# The window is a Properties editor on the Scene tab (an addon cannot register a new editor
# type), so Blender would list Scene / Units / Keying Sets / Audio / Rigid Body ... under the
# browser. Verified live on 5.2: wrapping each native panel's poll so it returns False on the
# BROWSER_SCREEN screen leaves only our panel (16 natives hidden) - BUT only once re-registered, see _reregister. Other Properties editors are
# untouched. Two C-drawn rows (context path + scene selector) stay: Python cannot reach them.
# Restored on unregister; idempotent; any failure just leaves the natives visible.
_poll_backup = {}   # panel class -> its OWN raw 'poll' entry (None when absent/inherited)


def _make_hidden_poll(orig):
    def poll(cls, context):
        screen = getattr(context, "screen", None)
        if screen is not None and screen.name.startswith(BROWSER_SCREEN):
            return False
        return orig(context) if orig is not None else True
    return classmethod(poll)


def _reregister(classes):
    """Unregister then register panel classes (children first out, parents first in).

    WHY: Blender decides at REGISTRATION time whether a panel has a poll callback (the C-side
    pointer is only set when the class owns a 'poll' attribute then). Native Scene panels such as
    Scene / Units / Gravity define none, so wrapping their poll afterwards is silently ignored
    until they are registered again. Verified failure mode: those three kept showing up in the
    Project Browser window."""
    kids = [c for c in classes if getattr(c, "bl_parent_id", "")]
    tops = [c for c in classes if not getattr(c, "bl_parent_id", "")]
    for cls in kids + tops:
        try:
            bpy.utils.unregister_class(cls)
        except Exception:   # noqa: BLE001
            pass
    for cls in tops + kids:
        try:
            bpy.utils.register_class(cls)
        except Exception:   # noqa: BLE001
            pass


def hide_native_panels():
    """Wrap the poll of every native Properties > Scene panel and re-register them so Blender
    sees the poll. Returns how many were patched."""
    patched = []
    for cls in list(bpy.types.Panel.__subclasses__()):
        try:
            if (getattr(cls, "bl_space_type", None) != "PROPERTIES"
                    or getattr(cls, "bl_context", None) != "scene"
                    or cls.__name__.startswith("YLOS") or cls in _poll_backup):
                continue
            orig = getattr(cls, "poll", None)
            _poll_backup[cls] = cls.__dict__.get("poll")
            cls.poll = _make_hidden_poll(orig)
            patched.append(cls)
        except Exception:   # noqa: BLE001 - cosmetic, never fatal
            continue
    if patched:
        _reregister(patched)
    return len(patched)


def restore_native_panels():
    classes = list(_poll_backup.keys())
    for cls, raw in list(_poll_backup.items()):
        try:
            if raw is not None:
                cls.poll = raw
            else:
                delattr(cls, "poll")
        except Exception:   # noqa: BLE001
            pass
    _poll_backup.clear()
    if classes:
        _reregister(classes)   # drop the C-side poll pointer set at the previous registration


# ylos_preview_size -> (template_icon scale, grid columns). OFF = compact list, no thumbnails.
_PREVIEW = {"OFF": (0.0, 1), "S": (3.5, 4), "M": (5.5, 3), "L": (8.0, 2)}


def draw_project_browser(layout, context, search="", columns=None):
    """columns=None -> from ylos_preview_size (popup); 0 -> auto-fit the width (window)."""
    scene = context.scene

    if not has_project(scene):
        col = layout.column(align=True)
        col.scale_y = 1.4
        col.operator("ylos.new_project", icon="ADD", text="New Project")
        col.operator("ylos.open_context", icon="FILE_FOLDER", text="Load Project")
        return

    draw_context_card(layout, scene)
    layout.separator(factor=0.4)

    ctx_type = scene.ylos_context_type.lower()
    entities = list_project_entities(scene.ylos_project_path, ctx_type)

    bar = layout.row(align=True)
    bar.prop(scene, "ylos_context_type", text="")
    bar.prop(scene, "ylos_preview_size", text="", icon_only=True)
    bar.operator("ylos.refresh_asset_list", text="", icon="FILE_REFRESH")
    bar.operator("ylos.new_asset", text="New", icon="ADD")
    bar.operator("ylos.open_folder", text="", icon="FOLDER_REDIRECT").folder_path = \
        scene.ylos_project_path

    if not entities:
        layout.separator(factor=0.3)
        layout.label(text="No assets found", icon="INFO")
        layout.operator("ylos.new_asset", text="+ Create first asset", icon="ADD")
        return

    needle = (search or "").lower()
    shown = [e for e in entities if needle in e["name"].lower()]

    active = scene.ylos_current_asset
    scale, preset_columns = _PREVIEW.get(scene.ylos_preview_size, _PREVIEW["M"])
    if columns is None:
        columns = preset_columns
    elif scale == 0.0:
        columns = 1
    thumbs = entity_thumbs.get_entity_thumbs(
        scene.ylos_project_path, [e["name"] for e in entities], ctx_type
    ) if scale > 0.0 else {}

    layout.separator(factor=0.3)
    if not shown:
        layout.label(text="No results", icon="INFO")
    else:
        grid = layout.grid_flow(row_major=True, columns=columns, even_columns=True,
                                even_rows=False, align=False)
        for entity in shown:
            _draw_card(grid, entity, entity["name"] == active, thumbs, scale)

    foot = layout.row(align=True)
    foot.scale_y = 0.9
    foot.label(text=f"{len(shown)} / {len(entities)}")

    if active and any(e["name"] == active for e in entities):
        _draw_steps(layout, scene, active, ctx_type)


def _draw_card(grid, entity, is_active, thumbs, scale):
    name = entity["name"]
    cell = grid.box().column(align=True)

    if scale > 0.0:
        icon_id, source = thumbs.get(name, (0, "none"))
        if icon_id:
            cell.template_icon(icon_value=icon_id, scale=scale)
        else:
            # Explicit placeholder rather than a hole: an unexplained blank reads like a bug,
            # whereas it is a legitimate state (nothing saved yet).
            ph = cell.column(align=True)
            ph.scale_y = max(scale * 0.4, 1.0)
            ph.label(text="", icon="IMAGE_DATA")
        tag = _SOURCE_LABELS.get(source)
        if tag:
            small = cell.row()
            small.scale_y = 0.7
            small.label(text=tag)

    # Two gestures, two buttons: the name SELECTS (context switch), the icon OPENS the latest
    # WIP. A click that only switched the context read as "nothing happened".
    row = cell.row(align=True)
    btn = row.operator("ylos.switch_asset_confirm", text=name, depress=is_active,
                       icon=_TYPE_ICONS.get(entity["type"], "OBJECT_DATA"))
    btn.new_asset = name
    row.operator("ylos.open_entity", text="", icon="IMPORT").entity = name

    if entity.get("broken"):
        warn = cell.row()
        warn.alert = True
        warn.label(text="no manifest", icon="ERROR")


def _draw_steps(layout, scene, active, ctx_type):
    """The active entity's steps, in full, each with its status marker (schema 2.2: explicit
    review/approved from the manifest, otherwise empty/wip/published derived from disk)."""
    steps = _STEP_MAP.get(ctx_type, ASSET_STEPS)
    status = get_entity_step_status(scene.ylos_project_path, active)
    # The entity's DECLARED steps win over the family default.
    declared = [s for s in steps if s in status] or steps

    layout.separator(factor=0.4)
    layout.label(text=f"{active} - steps", icon="SEQUENCE")
    step_col = layout.column(align=True)
    for step in declared:
        entry = status.get(step) or {}
        value = entry.get("status", "empty")
        row = step_col.row(align=True)
        row.scale_y = 1.05
        btn = row.operator(
            "ylos.switch_step_confirm",
            text=vocab.PRESENTATION["step"].get(step, (step.capitalize(),))[0],
            icon=vocab.status_icon(value),
            depress=(scene.ylos_current_step == step),
        )
        btn.new_step = step
        opener = row.operator("ylos.open_entity", text="", icon="IMPORT")
        opener.entity = active
        opener.step = step
        # An EXPLICIT status is a human decision, not a disk state: marked as such.
        if entry.get("explicit"):
            tag = row.row()
            tag.alignment = "RIGHT"
            tag.scale_x = 0.55
            tag.label(text=vocab.status_label(value))


class YLOS_PT_BrowserWindow(bpy.types.Panel):
    """Host of the Project Browser in its own, movable Blender window (ylos.browser_window).
    Blender cannot register a new editor type from an addon, so the window is a Properties
    editor on the Scene tab, and this panel only shows up on the screen named BROWSER_SCREEN -
    in every other Properties editor its poll is False."""
    bl_label = "Ylos Project Browser"
    bl_idname = "YLOS_PT_browser_window"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "scene"
    bl_order = -1000
    bl_options = {"HIDE_HEADER"}

    @classmethod
    def poll(cls, context):
        screen = getattr(context, "screen", None)
        return screen is not None and screen.name.startswith(BROWSER_SCREEN)

    def draw(self, context):
        scene = context.scene
        if has_project(scene):
            self.layout.prop(scene, "ylos_browser_search", text="", icon="VIEWZOOM")
        draw_project_browser(self.layout, context, scene.ylos_browser_search, columns=0)
