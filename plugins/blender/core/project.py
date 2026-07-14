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
