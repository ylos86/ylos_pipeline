# -*- coding: utf-8 -*-
# Ylos Pipeline - core/project.py
# Project constants, scene setup, Blender properties.
# Creation logic removed — use create_project.py (source of truth).

import bpy
import os
import json
from pathlib import Path

from . import vocab


PIPELINE_DIR = "_pipeline"
PROJECT_CONFIG_FILE = "project.json"

SCENE_PRESETS = {
    "FILM": {
        "fps": 24,
        "fps_base": 1.0,
        "unit_scale": 1.0,
        "color_management": "AgX",
        "renderer": "CYCLES",
        "resolution_x": 2048,
        "resolution_y": 1152,
        "color_space": "Linear Rec.709",
    },
    "AR": {
        "fps": 60,
        "fps_base": 1.0,
        "unit_scale": 1.0,
        "color_management": "sRGB",
        "renderer": "BLENDER_EEVEE_NEXT",
        "resolution_x": 2064,
        "resolution_y": 2096,
        "color_space": "sRGB",
    },
    "VR": {
        "fps": 90,
        "fps_base": 1.0,
        "unit_scale": 1.0,
        "color_management": "sRGB",
        "renderer": "BLENDER_EEVEE_NEXT",
        "resolution_x": 4128,
        "resolution_y": 2096,
        "color_space": "sRGB",
    },
}

DELIVERY_TARGETS = {
    "FILM": ["usd", "exr"],
    "AR": ["usdz", "gltf"],
    "VR": ["usd", "gltf"],
}

# Valeurs derivees de vocab.STEP_ITEMS (donc de create_project.DEFAULT_*_STEPS, seule
# source - cf. CLAUDE.md principe 5). Avant : listes recopiees a la main ici, driftees vs
# vocab (ex SHOT_STEPS incluait 'layout'/'render'/'composite', absents de
# DEFAULT_SHOT_STEPS ; SET_STEPS avait 'modeling' au lieu de 'layout') - purge INC-2.
ASSET_STEPS = vocab.values(vocab.STEP_ITEMS["ASSET"])
SHOT_STEPS  = vocab.values(vocab.STEP_ITEMS["SHOT"])
SET_STEPS   = vocab.values(vocab.STEP_ITEMS["SET"])

STEPS_BY_CONTEXT = {
    "asset": ASSET_STEPS,
    "shot":  SHOT_STEPS,
    "set":   SET_STEPS,
}


def is_step_valid_for_context(step: str, context_type: str) -> bool:
    return step in STEPS_BY_CONTEXT.get(context_type.lower(), ASSET_STEPS)


def load_project(project_path: str) -> dict | None:
    """
    Load project.json from a given project path.
    Normalizes both schema 1.x (legacy core.project) and schema 2.x (create_project.py).
    Always returns a dict with a 'project' sub-key for caller compatibility.
    """
    config_path = Path(project_path) / PIPELINE_DIR / PROJECT_CONFIG_FILE

    if not config_path.exists():
        return None

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return None

    # Schema 2.x from create_project.py — wrap into expected format
    if "schema_version" in raw or ("project" not in raw and "name" in raw):
        return {
            "project": {
                "name":      raw.get("name", ""),
                "prod_type": raw.get("prod_type", "FILM"),
                "path":      project_path,
            },
            "pipeline": raw.get("pipeline", {}),
            "scene":    raw.get("scene", {}),
            "delivery": raw.get("delivery", {}),
        }

    # Schema 1.x — return as-is
    return raw


def find_project_root(start_path: str) -> str | None:
    p = Path(start_path).resolve()
    for parent in [p, *p.parents]:
        candidate = parent / PIPELINE_DIR / PROJECT_CONFIG_FILE
        if candidate.exists():
            return str(parent)
    return None


def apply_scene_preset(scene: bpy.types.Scene, prod_type: str) -> None:
    if prod_type not in SCENE_PRESETS:
        return

    preset = SCENE_PRESETS[prod_type]

    scene.render.fps       = preset["fps"]
    scene.render.fps_base  = preset["fps_base"]

    scene.unit_settings.system       = "METRIC"
    scene.unit_settings.scale_length = preset["unit_scale"]
    scene.unit_settings.length_unit  = "METERS"

    scene.render.resolution_x          = preset["resolution_x"]
    scene.render.resolution_y          = preset["resolution_y"]
    scene.render.resolution_percentage = 100

    scene.render.engine = preset["renderer"]

    if prod_type == "FILM":
        scene.view_settings.view_transform = "AgX"
        scene.view_settings.look           = "None"
    else:
        scene.view_settings.view_transform = "Standard"


def setup_scene_collections(scene: bpy.types.Scene) -> None:
    root = scene.collection

    base_collections = [
        "COL_WORLD", "COL_ENV", "COL_CHAR", "COL_FX", "COL_CAM", "COL_GUIDES",
    ]

    sub_collections = {
        "COL_WORLD": ["COL_LGT_Key", "COL_LGT_Fill", "COL_LGT_Rim"],
        "COL_ENV":   ["COL_ENV_Terrain", "COL_ENV_Vegetation", "COL_ENV_Props"],
    }

    existing = {col.name for col in bpy.data.collections}

    for col_name in base_collections:
        if col_name not in existing:
            col = bpy.data.collections.new(col_name)
            root.children.link(col)
        else:
            col = bpy.data.collections[col_name]

        if col_name in sub_collections:
            for sub_name in sub_collections[col_name]:
                if sub_name not in existing:
                    sub = bpy.data.collections.new(sub_name)
                    col.children.link(sub)

    if "COL_GUIDES" in bpy.data.collections:
        guides = bpy.data.collections["COL_GUIDES"]
        for vl in scene.view_layers:
            layer_col = _find_layer_collection(vl.layer_collection, "COL_GUIDES")
            if layer_col:
                layer_col.exclude = True


def _find_layer_collection(layer_col, name: str):
    if layer_col.collection.name == name:
        return layer_col
    for child in layer_col.children:
        result = _find_layer_collection(child, name)
        if result:
            return result
    return None


# ---------------------------------------------------------------------------
# Collection hierarchy - partagee entre op_new_asset.py (creation) et
# op_import_product.py (import d'un product publie, INC-5) : les deux doivent ranger
# une entite au MEME endroit selon son type, une seule fois (principe 5). Deplace ici
# (etait duplique dans op_new_asset.py) car ce module possede deja la logique de
# collections de scene (setup_scene_collections/_find_layer_collection ci-dessus).
# ---------------------------------------------------------------------------

def get_or_create_collection(name: str):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
    return col


def link_collection(child, parent) -> None:
    if child.name not in {c.name for c in parent.children}:
        parent.children.link(child)


def resolve_parent_collection(asset_type: str, context_type: str, scene: bpy.types.Scene):
    """Collection parent adaptee au type d'entite (COL_CHAR/COL_ENV_Props pour un asset
    selon ASSET_TYPE_PARENT_COL, COL_SHOTS pour un shot, COL_ENV/COL_SETS pour un set) -
    meme convention que la creation (op_new_asset.py) et l'import (op_import_product.py).
    Retourne (collection, libelle_affichage). Import paresseux de core.asset : core/asset.py
    importe deja depuis ce module au niveau module (ASSET_STEPS...), un import top-level
    dans l'autre sens serait circulaire."""
    from .asset import ASSET_TYPE_PARENT_COL
    root = scene.collection

    if context_type == "ASSET":
        parent_name = ASSET_TYPE_PARENT_COL.get(asset_type, "COL_ASSETS")

        if parent_name == "COL_ENV_Props":
            col_env = get_or_create_collection("COL_ENV")
            link_collection(col_env, root)
            col_props = get_or_create_collection("COL_ENV_Props")
            link_collection(col_props, col_env)
            return col_props, "COL_ENV / COL_ENV_Props"

        parent = get_or_create_collection(parent_name)
        link_collection(parent, root)
        return parent, parent_name

    elif context_type == "SHOT":
        col = get_or_create_collection("COL_SHOTS")
        link_collection(col, root)
        return col, "COL_SHOTS"

    else:
        col_env = get_or_create_collection("COL_ENV")
        link_collection(col_env, root)
        col_sets = get_or_create_collection("COL_SETS")
        link_collection(col_sets, col_env)
        return col_sets, "COL_ENV / COL_SETS"


def collection_target_label(asset_type: str, context_type: str) -> str:
    from .asset import ASSET_TYPE_PARENT_COL
    if context_type == "ASSET":
        parent_name = ASSET_TYPE_PARENT_COL.get(asset_type, "COL_ASSETS")
        if parent_name == "COL_ENV_Props":
            return "COL_ENV -> COL_ENV_Props"
        return parent_name
    elif context_type == "SHOT":
        return "COL_SHOTS"
    return "COL_ENV -> COL_SETS"


def set_active_collection(context, collection):
    """Bascule active_layer_collection du view_layer courant sur 'collection' - les
    operateurs d'import (usd_import/import_scene.gltf) lient toujours leurs objets a la
    collection active, jamais a une collection passee en parametre. Retourne la
    layer_collection PRECEDENTE (a restaurer par l'appelant : ne jamais laisser un import
    changer l'etat actif de facon permanente et surprenante pour l'utilisateur)."""
    view_layer = context.view_layer
    previous = view_layer.active_layer_collection
    target = _find_layer_collection(view_layer.layer_collection, collection.name)
    if target is not None:
        view_layer.active_layer_collection = target
    return previous


def register_properties():
    bpy.types.Scene.ylos_project_path = bpy.props.StringProperty(
        name="Project Path",
        description="Absolute path to the Ylos project root",
        default="",
        subtype="NONE",
    )
    bpy.types.Scene.ylos_project_name = bpy.props.StringProperty(
        name="Project Name",
        description="Active Ylos project name",
        default="",
    )
    # Vocabulaire (valeurs) = create_project via core/vocab.py, seul home. Les tuples
    # *_ITEMS sont module-level (piege GC bpy, cf. vocab.py). Defauts inchanges.
    bpy.types.Scene.ylos_prod_type = bpy.props.EnumProperty(
        name="Prod Type",
        items=vocab.PROD_TYPE_ITEMS,
        default="FILM",
    )
    bpy.types.Scene.ylos_current_asset = bpy.props.StringProperty(
        name="Current Asset",
        default="",
    )
    # ylos_current_step : propriete Scene sans context d'operateur -> vocabulaire
    # complet (STEP_ITEMS_ALL, union ordonnee des steps de toutes les familles). Les
    # enums step des operateurs round-trip avec cette propriete (cf. op_publish,
    # op_switch_context, op_save_wip) : ils utilisent le meme STEP_ITEMS_ALL.
    bpy.types.Scene.ylos_current_step = bpy.props.EnumProperty(
        name="Current Step",
        items=vocab.STEP_ITEMS_ALL,
        default="modeling",
    )
    bpy.types.Scene.ylos_context_type = bpy.props.EnumProperty(
        name="Context",
        items=vocab.CONTEXT_TYPE_ITEMS,
        default="ASSET",
    )
    bpy.types.Scene.ylos_asset_type = bpy.props.EnumProperty(
        name="Asset Type",
        items=vocab.ASSET_TYPE_ITEMS,
        default="PROP",
    )
    # Prepare pour INC-4 (op_save_wip n'ecrit rien avec ceci pour l'instant - le champ
    # panel.py est affiche desactive tant que ce n'est pas cable).
    bpy.types.Scene.ylos_wip_comment = bpy.props.StringProperty(
        name="Comment",
        description="Note for the next Save Version - not yet persisted (wired in INC-4)",
        default="",
    )


def unregister_properties():
    props = [
        "ylos_project_path", "ylos_project_name", "ylos_prod_type",
        "ylos_current_asset", "ylos_current_step", "ylos_context_type",
        "ylos_asset_type", "ylos_wip_comment",
    ]
    for prop in props:
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
