# -*- coding: utf-8 -*-
# Import / Export: the "panel that opens on demand" (ylos.open_io, popup). Two needs:
#   - import a published pipeline PRODUCT (Product Browser, Prism-style) -> delegates to
#     ylos.import_product (tagged collection, version tracking);
#   - import/export raw geo FILES (OBJ/USD/glTF/FBX) outside pipeline versioning,
#     via Blender's native operators (their own file browser).
#
# The fixed regression: consolidating the old Imports panel into the State Manager lost the
# "Available publishes -> import" list; this module restores it (+ the raw I/O).

import bpy
from bpy.props import StringProperty, EnumProperty

from ..core.asset import list_project_entities
from ..core import usd_convention

import os
import sys

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


# ---------------------------------------------------------------------------
# Product Browser: cache of published products (computed on invoke/refresh, never in draw -
# same pattern as op_scene_check/op_update_imports: a disk scan on every redraw would be
# costly). Restricted to the current context family (scene.ylos_context_type), consistent
# with ylos.import_product which resolves via this same ctx_type.
# ---------------------------------------------------------------------------

_product_cache = {"family": None, "rows": []}


def compute_products(project_path, ctx_type):
    """Latest 'complete' publish per (entity, step) of the ctx_type family. Never raises."""
    cp = _cp()
    rows = []
    if not project_path:
        return rows
    try:
        entities = list_project_entities(project_path, ctx_type)
    except Exception:
        entities = []
    for ent in entities:
        name = ent.get("name")
        if not name:
            continue
        resolved = cp.resolve_entity(project_path, name)
        steps = resolved["manifest"].get("steps", []) if resolved else []
        for step in steps:
            latest = cp.latest_publish_artifact(project_path, name, step, ctx_type)
            if latest and latest.get("abs_path") and latest.get("exists", True):
                rows.append({
                    "entity": name,
                    "step": step,
                    "version": latest.get("version", 0),
                    "abs_path": latest["abs_path"],
                    "type": ent.get("type", ""),
                })
    rows.sort(key=lambda r: (r["entity"], r["step"]))
    return rows


def refresh_products(project_path, ctx_type):
    global _product_cache
    _product_cache = {"family": ctx_type, "rows": compute_products(project_path, ctx_type)}
    return _product_cache["rows"]


def get_cached_products():
    return _product_cache["rows"]


class YLOS_OT_RefreshProducts(bpy.types.Operator):
    bl_idname = "ylos.refresh_products"
    bl_label = "Refresh Products"
    bl_description = "Rescan published products for the current context family"
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        rows = refresh_products(scene.ylos_project_path, scene.ylos_context_type.lower())
        self.report({"INFO"}, f"{len(rows)} published product(s) available.")
        return {"FINISHED"}


class YLOS_OT_OpenIO(bpy.types.Operator):
    """Opens the Import / Export panel (popup, on demand)."""
    bl_idname = "ylos.open_io"
    bl_label = "Import / Export"
    bl_description = "Open the Ylos Import / Export panel (products + raw files)"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        scene = context.scene
        refresh_products(scene.ylos_project_path, scene.ylos_context_type.lower())
        return context.window_manager.invoke_popup(self, width=460)

    def draw(self, context):
        from ..ui.io_panel import draw_io  # lazy - anti-cycle (see op_state_manager)
        draw_io(self.layout, context)

    def execute(self, context):
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Raw file I/O: delegates to Blender's native operators. Empty filepath -> INVOKE (the native
# file browser opens); filepath provided -> direct EXEC (programmatic use / tests).
# ---------------------------------------------------------------------------

_FMT_ITEMS = [
    ("OBJ",  "OBJ",   "Wavefront OBJ"),
    ("USD",  "USD",   "Universal Scene Description"),
    ("GLTF", "glTF",  "glTF / GLB"),
    ("FBX",  "FBX",   "Autodesk FBX (requires the FBX add-on)"),
]

# (bpy.ops group, name) for import.
_IMPORT_OPS = {
    "OBJ":  ("wm", "obj_import"),
    "USD":  ("wm", "usd_import"),
    "GLTF": ("import_scene", "gltf"),
    "FBX":  ("import_scene", "fbx"),
}
# (group, name, 'selection only' kwargs) for export.
_EXPORT_OPS = {
    "OBJ":  ("wm", "obj_export", {"export_selected_objects": True}),
    "USD":  ("wm", "usd_export", {"selected_objects_only": True}),
    "GLTF": ("export_scene", "gltf", {"use_selection": True}),
    "FBX":  ("export_scene", "fbx", {"use_selection": True}),
}


def _resolve_op(group, name):
    grp = getattr(bpy.ops, group, None)
    return getattr(grp, name, None) if grp is not None else None


def _convention_kwargs(fmt, direction):
    """USD is the pipeline's exchange format: even a RAW import/export follows
    docs/usd-convention.md (Y-up, metersPerUnit) - via core.usd_convention, the single
    translation of the convention into Blender's RNA. No root_prim_path is forced here: a
    raw export is not a pipeline publish, it must not be re-rooted under an entity name.
    Any other format gets {}."""
    if fmt != "USD":
        return {}
    if direction == "export":
        return usd_convention.export_kwargs()
    return usd_convention.import_kwargs()


class YLOS_OT_RawImport(bpy.types.Operator):
    bl_idname = "ylos.raw_import"
    bl_label = "Import File"
    bl_description = "Import a raw geometry file into the scene (outside pipeline versioning)"
    bl_options = {"REGISTER", "UNDO"}

    fmt: EnumProperty(items=_FMT_ITEMS, default="OBJ")
    filepath: StringProperty(subtype="FILE_PATH", default="")

    def execute(self, context):
        group, name = _IMPORT_OPS[self.fmt]
        op = _resolve_op(group, name)
        if op is None:
            self.report({"ERROR"}, f"{self.fmt} importer unavailable.")
            return {"CANCELLED"}
        extra = _convention_kwargs(self.fmt, "import")
        try:
            if self.filepath:
                result = op('EXEC_DEFAULT', filepath=self.filepath, **extra)
                return {"FINISHED"} if "FINISHED" in result else {"CANCELLED"}
            op('INVOKE_DEFAULT', **extra)  # native file browser opens (independent modal)
            return {"FINISHED"}
        except Exception as e:
            self.report({"ERROR"}, f"{self.fmt} import failed: {e}")
            return {"CANCELLED"}


class YLOS_OT_RawExport(bpy.types.Operator):
    bl_idname = "ylos.raw_export"
    bl_label = "Export File"
    bl_description = "Export the current selection to a raw geometry file (outside pipeline versioning)"
    bl_options = {"REGISTER"}

    fmt: EnumProperty(items=_FMT_ITEMS, default="OBJ")
    filepath: StringProperty(subtype="FILE_PATH", default="")

    def execute(self, context):
        group, name, kwargs = _EXPORT_OPS[self.fmt]
        op = _resolve_op(group, name)
        if op is None:
            self.report({"ERROR"}, f"{self.fmt} exporter unavailable.")
            return {"CANCELLED"}
        kwargs = dict(kwargs, **_convention_kwargs(self.fmt, "export"))
        try:
            if self.filepath:
                result = op('EXEC_DEFAULT', filepath=self.filepath, **kwargs)
                return {"FINISHED"} if "FINISHED" in result else {"CANCELLED"}
            op('INVOKE_DEFAULT', **kwargs)  # native file browser opens (independent modal)
            return {"FINISHED"}
        except Exception as e:
            self.report({"ERROR"}, f"{self.fmt} export failed: {e}")
            return {"CANCELLED"}
