# -*- coding: utf-8 -*-
# Import / Export : le "panel qui s'ouvre a la demande" (ylos.open_io, popup). Deux besoins :
#   - importer un PRODUCT pipeline publie (Product Browser, facon Prism) -> delegue a
#     ylos.import_product (collection taguee, suivi de version) ;
#   - importer/exporter des FICHIERS geo bruts (OBJ/USD/glTF/FBX) hors versioning pipeline,
#     via les operateurs natifs de Blender (leur propre file browser).
#
# La regression corrigee : en consolidant l'ancien panel Imports dans le State Manager, la
# liste "Available publishes -> import" avait ete perdue ; ce module la restaure (+ le raw I/O).

import bpy
from bpy.props import StringProperty, EnumProperty

from ..core.asset import list_project_entities

import os
import sys

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


# ---------------------------------------------------------------------------
# Product Browser : cache des products publies (calcule sur invoke/refresh, jamais en draw -
# meme pattern que op_scene_check/op_update_imports : un scan disque a chaque redraw serait
# couteux). Restreint a la famille de contexte courante (scene.ylos_context_type), coherent
# avec ylos.import_product qui resout via ce meme ctx_type.
# ---------------------------------------------------------------------------

_product_cache = {"family": None, "rows": []}


def compute_products(project_path, ctx_type):
    """Latest publish 'complete' par (entite, step) de la famille ctx_type. Ne leve jamais."""
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
    """Ouvre le panel Import / Export (popup, a la demande)."""
    bl_idname = "ylos.open_io"
    bl_label = "Import / Export"
    bl_description = "Open the Ylos Import / Export panel (products + raw files)"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        scene = context.scene
        refresh_products(scene.ylos_project_path, scene.ylos_context_type.lower())
        return context.window_manager.invoke_popup(self, width=460)

    def draw(self, context):
        from ..ui.io_panel import draw_io  # lazy - anti-cycle (cf. op_state_manager)
        draw_io(self.layout, context)

    def execute(self, context):
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Raw file I/O : delegue aux operateurs natifs de Blender. filepath vide -> INVOKE (le file
# browser natif s'ouvre) ; filepath fourni -> EXEC direct (usage programmatique / tests).
# ---------------------------------------------------------------------------

_FMT_ITEMS = [
    ("OBJ",  "OBJ",   "Wavefront OBJ"),
    ("USD",  "USD",   "Universal Scene Description"),
    ("GLTF", "glTF",  "glTF / GLB"),
    ("FBX",  "FBX",   "Autodesk FBX (requires the FBX add-on)"),
]

# (groupe bpy.ops, nom) pour l'import.
_IMPORT_OPS = {
    "OBJ":  ("wm", "obj_import"),
    "USD":  ("wm", "usd_import"),
    "GLTF": ("import_scene", "gltf"),
    "FBX":  ("import_scene", "fbx"),
}
# (groupe, nom, kwargs 'selection uniquement') pour l'export.
_EXPORT_OPS = {
    "OBJ":  ("wm", "obj_export", {"export_selected_objects": True}),
    "USD":  ("wm", "usd_export", {"selected_objects_only": True}),
    "GLTF": ("export_scene", "gltf", {"use_selection": True}),
    "FBX":  ("export_scene", "fbx", {"use_selection": True}),
}


def _resolve_op(group, name):
    grp = getattr(bpy.ops, group, None)
    return getattr(grp, name, None) if grp is not None else None


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
        try:
            if self.filepath:
                result = op('EXEC_DEFAULT', filepath=self.filepath)
                return {"FINISHED"} if "FINISHED" in result else {"CANCELLED"}
            op('INVOKE_DEFAULT')  # le file browser natif s'ouvre (modal independant)
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
        try:
            if self.filepath:
                result = op('EXEC_DEFAULT', filepath=self.filepath, **kwargs)
                return {"FINISHED"} if "FINISHED" in result else {"CANCELLED"}
            op('INVOKE_DEFAULT', **kwargs)  # le file browser natif s'ouvre (modal independant)
            return {"FINISHED"}
        except Exception as e:
            self.report({"ERROR"}, f"{self.fmt} export failed: {e}")
            return {"CANCELLED"}
