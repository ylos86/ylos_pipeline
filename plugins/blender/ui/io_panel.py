# -*- coding: utf-8 -*-
# Draws the Import / Export panel (opened on demand via ylos.open_io, popup). Three blocks:
#   - Import Product: Product Browser of pipeline publishes (filter + thumbnail) -> import.
#   - Import File   : raw geo files (OBJ/USD/glTF/FBX) via the native importers.
#   - Export Selection: the current selection to a raw file (outside versioning).
# No business logic here: layout wiring existing operators (see ui/state_manager.py).

import os
import bpy

from ..core.thumbnails import load_icon
from ..operators.op_io import get_cached_products

_RAW_FORMATS = (("OBJ", "OBJ"), ("USD", "USD"), ("GLTF", "glTF"), ("FBX", "FBX"))
_MAX_ROWS = 12


def draw_io(layout, context):
    scene = context.scene

    # --- Import Product (pipeline) ---
    box = layout.box()
    hdr = box.row(align=True)
    hdr.label(text="Import Product", icon="IMPORT")
    hdr.prop(scene, "ylos_io_all_entities", text="All assets", toggle=True)
    hdr.operator("ylos.refresh_products", text="", icon="FILE_REFRESH")

    if not (scene.ylos_project_path and scene.ylos_project_name):
        box.label(text="No project loaded", icon="INFO")
    else:
        # Default scope = the asset being worked on: its own earlier steps are what you import
        # (modeling into lookdev, ...). "All assets" is for assembling a set / shot, and only
        # then does a search box earn its place.
        active = scene.ylos_current_asset
        all_assets = scene.ylos_io_all_entities
        products = get_cached_products()
        if all_assets:
            box.prop(scene, "ylos_io_search", text="", icon="VIEWZOOM")
            search = scene.ylos_io_search.lower()
            rows = [p for p in products
                    if search in p["entity"].lower() or search in p["step"].lower()]
        else:
            rows = [p for p in products if p["entity"] == active]

        if not all_assets and not active:
            box.label(text="No active asset - pick one, or use All assets", icon="INFO")
        elif not products:
            box.label(text="No published products for this family", icon="INFO")
        elif not rows:
            box.label(text=("No match" if all_assets
                            else f"Nothing published yet for {active}"), icon="INFO")
        else:
            col = box.column(align=True)
            for p in rows[:_MAX_ROWS]:
                row = col.row(align=True)
                icon_id = load_icon(os.path.join(os.path.dirname(p["abs_path"]), "thumb.png"))
                if icon_id:
                    row.template_icon(icon_value=icon_id, scale=2.0)
                info = row.column(align=True)
                info.label(text=p["entity"], icon="OBJECT_DATA")
                info.label(text=f"{p['step']}  v{p['version']:03d}")
                op = row.operator("ylos.import_product", text="Import", icon="IMPORT")
                op.entity = p["entity"]
                op.step = p["step"]
                op.version = p["version"]
            if len(rows) > _MAX_ROWS:
                foot = box.row()
                foot.alignment = "RIGHT"
                foot.label(text=f"+{len(rows) - _MAX_ROWS} more (use search)")

    # --- Import File (raw) ---
    box2 = layout.box()
    box2.label(text="Import File", icon="IMPORT")
    r = box2.row(align=True)
    for fmt, lbl in _RAW_FORMATS:
        op = r.operator("ylos.raw_import", text=lbl)
        op.fmt = fmt
        op.filepath = ""

    # --- Export Selection (raw) ---
    box3 = layout.box()
    box3.label(text="Export Selection (raw file)", icon="EXPORT")
    r2 = box3.row(align=True)
    for fmt, lbl in _RAW_FORMATS:
        op = r2.operator("ylos.raw_export", text=lbl)
        op.fmt = fmt
        op.filepath = ""
    box3.label(text="Raw files, outside pipeline versioning "
                    "(to publish the asset, use Publish in the State Manager).", icon="INFO")
