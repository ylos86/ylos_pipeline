# -*- coding: utf-8 -*-
# Detection + application des mises a jour d'import states (State-Manager-lite, INC-5).
import bpy
import os
import sys
from bpy.props import StringProperty
from ..core.asset import resolve_publish_entry
from ..core.project import set_active_collection
from .op_import_product import import_artifact

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


# Cache du dernier ylos.check_updates - meme pattern que op_scene_check.py
# (get_cached_results()) : un operateur explicite calcule une fois, le panel lit le cache
# au redraw plutot que d'interroger le disque a chaque frame d'UI.
_update_cache = {}


def get_cached_update_results() -> dict:
    """{collection_name: {'current': int, 'latest': int, 'has_update': bool}} - vide tant
    que ylos.check_updates n'a jamais tourne dans cette session."""
    return _update_cache


def tagged_import_collections() -> list:
    return [c for c in bpy.data.collections if "ylos_import_entity" in c]


class YLOS_OT_CheckUpdates(bpy.types.Operator):
    """Compare each tagged import collection's version against the latest publish."""
    bl_idname = "ylos.check_updates"
    bl_label = "Check Updates"
    bl_description = "Compare imported products against the latest published version"
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        project_path = scene.ylos_project_path
        if not project_path:
            self.report({"ERROR"}, "No active project.")
            return {"CANCELLED"}

        cp = _cp()
        results = {}
        n_updates = 0

        for col in tagged_import_collections():
            entity  = col["ylos_import_entity"]
            step    = col["ylos_import_step"]
            current = col["ylos_import_version"]
            latest_entry = cp.latest_publish_artifact(project_path, entity, step)
            latest = latest_entry["version"] if latest_entry else current
            has_update = latest > current
            results[col.name] = {
                "current": current, "latest": latest, "has_update": has_update,
            }
            if has_update:
                n_updates += 1

        global _update_cache
        _update_cache = results

        self.report(
            {"INFO"},
            f"Checked {len(results)} import(s): {n_updates} update(s) available.",
        )
        return {"FINISHED"}


class YLOS_OT_UpdateImport(bpy.types.Operator):
    """Replace a tagged import collection's content with the latest published version.

    v1 : remplacement pur (retire tout, reimporte a neuf) - AUCUN remap d'overrides
    (materiaux/contraintes/animation ajoutes manuellement sur les objets importes) : la
    perte est signalee explicitement dans le rapport, jamais silencieuse."""
    bl_idname = "ylos.update_import"
    bl_label = "Update Import"
    bl_description = ("Replace this import's content with the latest published version "
                      "(no override remap in v1)")
    bl_options = {"REGISTER", "UNDO"}

    collection_name: StringProperty()

    def execute(self, context):
        scene = context.scene
        project_path = scene.ylos_project_path
        collection = bpy.data.collections.get(self.collection_name)
        if collection is None or "ylos_import_entity" not in collection:
            self.report({"ERROR"}, f"'{self.collection_name}' is not a tagged import.")
            return {"CANCELLED"}

        entity      = collection["ylos_import_entity"]
        step        = collection["ylos_import_step"]
        old_version = collection["ylos_import_version"]
        n_before    = len(collection.objects)

        entry = resolve_publish_entry(
            project_path, entity, step, None, scene.ylos_context_type.lower(),
        )
        if not entry or not entry.get("abs_path"):
            self.report({"ERROR"}, f"No publish found for {entity}/{step}.")
            return {"CANCELLED"}

        if entry["version"] == old_version:
            self.report({"INFO"}, f"'{self.collection_name}' already at v{old_version:03d} "
                                  f"(latest).")
            return {"FINISHED"}

        for obj in list(collection.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        for child in list(collection.children):
            collection.children.unlink(child)

        previous_active = set_active_collection(context, collection)
        try:
            import_artifact(entry["abs_path"])
        except Exception as e:
            self.report(
                {"ERROR"},
                f"Update failed - '{self.collection_name}' is now empty: {e}",
            )
            return {"CANCELLED"}
        finally:
            context.view_layer.active_layer_collection = previous_active

        collection["ylos_import_version"] = entry["version"]
        collection["ylos_import_path"] = entry["abs_path"]

        n_after = len(collection.objects)
        self.report(
            {"WARNING"},
            f"Updated {entity}/{step}: v{old_version:03d} -> v{entry['version']:03d} "
            f"({n_before} -> {n_after} objects). No override remap in v1 - re-apply "
            f"materials/constraints/animation manually if needed.",
        )
        return {"FINISHED"}
