# -*- coding: utf-8 -*-
# Import states (State-Manager-lite, INC-5). Absorbe l'ancien op_load_publish.py (USD-only,
# import a plat par chemin, aucun suivi de ce qui a ete importe) : un import cree desormais
# une collection TAGUEE (custom props ylos_import_entity/step/version/path) rangee sous le
# meme parent que la creation d'entite (core.project.resolve_parent_collection), routee par
# extension (GLB -> import_scene.gltf, USD -> wm.usd_import). La collection est l'identite
# stable de l'import ('<entity>_<step>') : ylos.update_import (op_update_imports.py) en
# remplace le CONTENU sans en changer le nom, pour que les references externes (cameras,
# animation) restent valides d'une version a l'autre.
import bpy
import os
import sys
from bpy.props import StringProperty, IntProperty
from ..core.asset import resolve_publish_entry, read_entity_manifest
from ..core.project import (
    resolve_parent_collection, get_or_create_collection, link_collection,
    set_active_collection,
)

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


# Miroir de launch_context.py (routage par extension identique) : USD -> merge via
# wm.usd_import, GLB/GLTF -> merge via import_scene.gltf. Extensions inconnues -> erreur
# explicite, jamais un import silencieusement ignore.
USD_IMPORT_EXTS = (".usd", ".usda", ".usdc", ".usdz", ".usdnc")
GLB_IMPORT_EXTS = (".glb", ".gltf")


def import_state_collection_name(entity: str, step: str) -> str:
    """Identite stable d'un import : '<entity>_<step>'. Une seule 'state' par (entity,
    step) dans une scene - re-importer le meme couple doit passer par Update, pas creer un
    doublon (cf. YLOS_OT_ImportProduct.execute)."""
    return f"{entity}_{step}"


def import_artifact(abs_path: str) -> None:
    """Importe 'abs_path' dans la collection ACTIVE (jamais un chemin resolu ici - deja
    fourni par l'appelant via l'orchestrateur). Leve ValueError sur extension inconnue :
    l'appelant convertit en report d'erreur, jamais de crash silencieux ni de mainfile
    ouvert par erreur sur un artefact non gere."""
    ext = os.path.splitext(abs_path)[1].lower()
    if ext in GLB_IMPORT_EXTS:
        bpy.ops.import_scene.gltf(filepath=abs_path)
    elif ext in USD_IMPORT_EXTS:
        bpy.ops.wm.usd_import(filepath=abs_path)
    else:
        raise ValueError(f"unsupported artifact extension for import: {ext!r}")


def tag_import_collection(collection, entity: str, step: str, version: int, abs_path: str) -> None:
    collection["ylos_import_entity"] = entity
    collection["ylos_import_step"] = step
    collection["ylos_import_version"] = version
    collection["ylos_import_path"] = abs_path


class YLOS_OT_ImportProduct(bpy.types.Operator):
    """Import a published product (USD or GLB) into a tagged, versioned collection."""
    bl_idname = "ylos.import_product"
    bl_label = "Import Product"
    bl_description = "Import a published product into a tagged import collection"
    bl_options = {"REGISTER", "UNDO"}

    entity: StringProperty(name="Entity")
    step: StringProperty(name="Step")
    # 0 = derniere version publiee (pas de sentinelle string 'latest' - cf. spec
    # 'version|latest', un IntProperty=0 est plus sur cote UI/RNA qu'une chaine magique).
    version: IntProperty(name="Version", default=0, min=0)

    def execute(self, context):
        scene = context.scene
        project_path = scene.ylos_project_path
        if not project_path or not self.entity or not self.step:
            self.report({"ERROR"}, "Missing project/entity/step.")
            return {"CANCELLED"}

        ctx_type = scene.ylos_context_type.lower()

        col_name = import_state_collection_name(self.entity, self.step)
        if bpy.data.collections.get(col_name):
            self.report(
                {"ERROR"},
                f"'{col_name}' is already imported in this scene - use Update Import instead.",
            )
            return {"CANCELLED"}

        entry = resolve_publish_entry(
            project_path, self.entity, self.step, self.version or None, ctx_type,
        )
        if not entry or not entry.get("abs_path"):
            target = f"v{self.version:03d}" if self.version else "latest"
            self.report(
                {"ERROR"}, f"No publish found for {self.entity}/{self.step} ({target}).",
            )
            return {"CANCELLED"}

        abs_path = entry["abs_path"]

        manifest = read_entity_manifest(project_path, self.entity, ctx_type)
        asset_type = manifest.get("type", "PROP")
        entity_ctx = manifest.get("entity_type", ctx_type).upper()

        parent, _label = resolve_parent_collection(asset_type, entity_ctx, scene)
        collection = get_or_create_collection(col_name)
        link_collection(collection, parent)

        previous_active = set_active_collection(context, collection)
        try:
            import_artifact(abs_path)
        except Exception as e:
            # Collection vide/tagable creee pour rien - la retirer plutot que laisser un
            # "import" fantome sans contenu dans l'outliner.
            bpy.data.collections.remove(collection)
            self.report({"ERROR"}, f"Import failed: {e}")
            return {"CANCELLED"}
        finally:
            context.view_layer.active_layer_collection = previous_active

        tag_import_collection(collection, self.entity, self.step, entry["version"], abs_path)

        n_objects = len(collection.objects)
        self.report(
            {"INFO"},
            f"Imported {self.entity}/{self.step} v{entry['version']:03d} "
            f"({n_objects} object(s)) -> '{col_name}'",
        )
        return {"FINISHED"}
