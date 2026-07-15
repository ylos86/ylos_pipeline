# -*- coding: utf-8 -*-
# Draw du panel Import / Export (ouvert a la demande via ylos.open_io, popup). Trois blocs :
#   - Import Product : Product Browser des publishes pipeline (filtre + vignette) -> import.
#   - Import File    : fichiers geo bruts (OBJ/USD/glTF/FBX) via les importeurs natifs.
#   - Export Selection : la selection courante vers un fichier brut (hors versioning).
# Aucune logique metier ici : layout reliant des operateurs existants (cf. ui/state_manager.py).

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
    fam = hdr.row()
    fam.alignment = "RIGHT"
    fam.label(text=scene.ylos_context_type.title())
    hdr.operator("ylos.refresh_products", text="", icon="FILE_REFRESH")

    if not (scene.ylos_project_path and scene.ylos_project_name):
        box.label(text="No project loaded", icon="INFO")
    else:
        box.prop(scene, "ylos_io_search", text="", icon="VIEWZOOM")
        products = get_cached_products()
        search = scene.ylos_io_search.lower()
        rows = [p for p in products
                if search in p["entity"].lower() or search in p["step"].lower()]

        if not products:
            box.label(text="No published products for this family", icon="INFO")
        elif not rows:
            box.label(text="No match", icon="INFO")
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
    box3.label(text="Export Selection", icon="EXPORT")
    r2 = box3.row(align=True)
    for fmt, lbl in _RAW_FORMATS:
        op = r2.operator("ylos.raw_export", text=lbl)
        op.fmt = fmt
        op.filepath = ""
    box3.label(text="Raw files, outside pipeline versioning "
                    "(use the State Manager to publish).", icon="INFO")
