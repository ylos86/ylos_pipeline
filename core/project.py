# -*- coding: utf-8 -*-
# Ylos Pipeline - core/project.py
# Handles project creation on disk, project.json read/write,
# and Blender scene property registration.

import bpy
import os
import json
from pathlib import Path
from datetime import datetime


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PIPELINE_DIR = "_pipeline"
PROJECT_CONFIG_FILE = "project.json"

# Production types and their scene settings
SCENE_PRESETS = {
    "FILM": {
        "fps": 24,
        "fps_base": 1.0,
        "unit_scale": 1.0,                 # metres
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

# Delivery targets per production type
DELIVERY_TARGETS = {
    "FILM": ["usd", "exr"],
    "AR": ["usdz", "gltf"],
    "VR": ["usd", "gltf"],
}

# Top-level folder structure
PROJECT_FOLDERS = [
    "_pipeline",
    "assets",
    "shots",
    "sets",
    "edit",
    "cache",
    "cache/alembic",
    "cache/simulations",
    "delivery",
    "delivery/film",
    "delivery/ar",
    "delivery/vr",
    "references",
    "resources",
    "resources/hdri",
    "resources/textures",
]

# Asset production steps (each gets its own wip/ publish/ folders)
ASSET_STEPS = [
    "modeling",
    "uvs",
    "rigging",
    "lookdev",
    "fx",
]

# Shot production steps
SHOT_STEPS = [
    "layout",
    "animation",
    "lighting",
    "fx",
    "render",
    "composite",
]

# Set production steps
SET_STEPS = [
    "modeling",
    "lookdev",
    "lighting",
]


# ---------------------------------------------------------------------------
# Project creation
# ---------------------------------------------------------------------------

def create_project(root_path: str, project_name: str, prod_type: str) -> dict:
    """
    Create full project folder structure on disk and write project.json.

    Args:
        root_path:    Parent directory where the project folder will be created.
        project_name: Clean project name (PascalCase recommended, no spaces).
        prod_type:    One of "FILM", "AR", "VR".

    Returns:
        dict with keys "success" (bool), "project_path" (str), "message" (str).
    """
    if prod_type not in SCENE_PRESETS:
        return {"success": False, "project_path": "", "message": f"Unknown prod type: {prod_type}"}

    project_path = Path(root_path) / f"YLOS_{project_name}"

    if project_path.exists():
        return {
            "success": False,
            "project_path": str(project_path),
            "message": f"Project already exists at: {project_path}",
        }

    # Create all project folders
    try:
        for folder in PROJECT_FOLDERS:
            (project_path / folder).mkdir(parents=True, exist_ok=True)

        # Write project.json
        config = _build_project_config(project_name, prod_type, str(project_path))
        _write_project_json(project_path, config)

    except Exception as e:
        return {"success": False, "project_path": str(project_path), "message": str(e)}

    return {
        "success": True,
        "project_path": str(project_path),
        "message": f"Project YLOS_{project_name} created at {project_path}",
    }


def _build_project_config(name: str, prod_type: str, path: str) -> dict:
    """Build the project.json content dict."""
    return {
        "project": {
            "name": name,
            "display_name": name,
            "prod_type": prod_type,
            "created": datetime.now().strftime("%Y-%m-%d"),
            "version": "0.1.0",
            "path": path,
        },
        "pipeline": {
            "asset_steps": ASSET_STEPS,
            "shot_steps": SHOT_STEPS,
            "set_steps": SET_STEPS,
            "usd_root_prim": "/ROOT",
        },
        "scene": SCENE_PRESETS[prod_type],
        "delivery": {
            "targets": DELIVERY_TARGETS[prod_type],
        },
    }


def _write_project_json(project_path: Path, config: dict) -> None:
    """Write project.json to the _pipeline folder."""
    pipeline_dir = project_path / PIPELINE_DIR
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    config_path = pipeline_dir / PROJECT_CONFIG_FILE

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Project loading
# ---------------------------------------------------------------------------

def load_project(project_path: str) -> dict | None:
    """
    Load a project.json from a given project path.

    Returns the config dict, or None if not found / invalid.
    """
    config_path = Path(project_path) / PIPELINE_DIR / PROJECT_CONFIG_FILE

    if not config_path.exists():
        return None

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def find_project_root(start_path: str) -> str | None:
    """
    Walk up from start_path to find a _pipeline/project.json.
    Useful for resolving project root from any file inside it.
    """
    p = Path(start_path).resolve()
    for parent in [p, *p.parents]:
        candidate = parent / PIPELINE_DIR / PROJECT_CONFIG_FILE
        if candidate.exists():
            return str(parent)
    return None


# ---------------------------------------------------------------------------
# Scene setup
# ---------------------------------------------------------------------------

def apply_scene_preset(scene: bpy.types.Scene, prod_type: str) -> None:
    """
    Apply Ylos scene settings to the given Blender scene based on prod type.
    """
    if prod_type not in SCENE_PRESETS:
        return

    preset = SCENE_PRESETS[prod_type]

    # Timing
    scene.render.fps = preset["fps"]
    scene.render.fps_base = preset["fps_base"]

    # Units
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = preset["unit_scale"]
    scene.unit_settings.length_unit = "METERS"

    # Resolution
    scene.render.resolution_x = preset["resolution_x"]
    scene.render.resolution_y = preset["resolution_y"]
    scene.render.resolution_percentage = 100

    # Renderer
    scene.render.engine = preset["renderer"]

    # Color management
    view_settings = scene.view_settings
    display_settings = scene.display_settings

    if prod_type == "FILM":
        scene.view_settings.view_transform = "AgX"
        scene.view_settings.look = "None"
    else:
        scene.view_settings.view_transform = "Standard"


def setup_scene_collections(scene: bpy.types.Scene) -> None:
    """
    Create Ylos scene collection hierarchy if not already present.
    Follows Ylos naming conventions (based on Black Kite).
    """
    root = scene.collection

    base_collections = [
        "COL_WORLD",
        "COL_ENV",
        "COL_CHAR",
        "COL_FX",
        "COL_CAM",
        "COL_GUIDES",
    ]

    sub_collections = {
        "COL_WORLD": ["COL_LGT_Key", "COL_LGT_Fill", "COL_LGT_Rim"],
        "COL_ENV": ["COL_ENV_Terrain", "COL_ENV_Vegetation", "COL_ENV_Props"],
    }

    existing = {col.name for col in bpy.data.collections}

    for col_name in base_collections:
        if col_name not in existing:
            col = bpy.data.collections.new(col_name)
            root.children.link(col)
        else:
            col = bpy.data.collections[col_name]

        # Sub-collections
        if col_name in sub_collections:
            for sub_name in sub_collections[col_name]:
                if sub_name not in existing:
                    sub = bpy.data.collections.new(sub_name)
                    col.children.link(sub)

    # COL_GUIDES: exclude from render
    if "COL_GUIDES" in bpy.data.collections:
        guides = bpy.data.collections["COL_GUIDES"]
        # Mark as excluded from render via view layer
        for vl in scene.view_layers:
            layer_col = _find_layer_collection(vl.layer_collection, "COL_GUIDES")
            if layer_col:
                layer_col.exclude = True


def _find_layer_collection(layer_col, name: str):
    """Recursive search for a LayerCollection by name."""
    if layer_col.collection.name == name:
        return layer_col
    for child in layer_col.children:
        result = _find_layer_collection(child, name)
        if result:
            return result
    return None


# ---------------------------------------------------------------------------
# Blender scene properties
# ---------------------------------------------------------------------------

def register_properties():
    """Register Ylos pipeline custom properties on bpy.types.Scene."""

    bpy.types.Scene.ylos_project_path = bpy.props.StringProperty(
        name="Project Path",
        description="Absolute path to the Ylos project root",
        default="",
        subtype="DIR_PATH",
    )

    bpy.types.Scene.ylos_project_name = bpy.props.StringProperty(
        name="Project Name",
        description="Active Ylos project name",
        default="",
    )

    bpy.types.Scene.ylos_prod_type = bpy.props.EnumProperty(
        name="Prod Type",
        description="Production type, drives scene settings and delivery targets",
        items=[
            ("FILM", "Film", "24fps, 2K, Cycles, AgX"),
            ("AR", "AR", "60fps, Quest res, EEVEE, sRGB"),
            ("VR", "VR", "90fps, Stereo res, EEVEE, sRGB"),
        ],
        default="FILM",
    )

    bpy.types.Scene.ylos_current_asset = bpy.props.StringProperty(
        name="Current Asset",
        description="Name of the asset currently being worked on",
        default="",
    )

    bpy.types.Scene.ylos_current_step = bpy.props.EnumProperty(
        name="Current Step",
        description="Current production step for this session",
        items=[
            ("modeling", "Modeling", ""),
            ("uvs", "UVs", ""),
            ("rigging", "Rigging", ""),
            ("lookdev", "LookDev", ""),
            ("fx", "FX", ""),
            ("layout", "Layout", ""),
            ("animation", "Animation", ""),
            ("lighting", "Lighting", ""),
            ("render", "Render", ""),
            ("composite", "Composite", ""),
        ],
        default="modeling",
    )

    bpy.types.Scene.ylos_context_type = bpy.props.EnumProperty(
        name="Context",
        description="What kind of entity is being worked on",
        items=[
            ("ASSET", "Asset", "Working on a character, prop, or environment asset"),
            ("SHOT", "Shot", "Working on a specific shot"),
            ("SET", "Set", "Working on a set / environment assembly"),
        ],
        default="ASSET",
    )

    bpy.types.Scene.ylos_asset_type = bpy.props.EnumProperty(
        name="Asset Type",
        description="Sub-type of the asset (only relevant when Context = Asset)",
        items=[
            ("PROP",      "Prop",        "Hard-surface object, furniture, tool, vehicle..."),
            ("CHARACTER", "Character",   "Biped, creature, hero, NPC..."),
            ("ENVIRONMENT","Environment","Terrain piece, modular kit, vegetation..."),
        ],
        default="PROP",
    )


def unregister_properties():
    """Remove Ylos custom properties from bpy.types.Scene."""
    props = [
        "ylos_project_path",
        "ylos_project_name",
        "ylos_prod_type",
        "ylos_current_asset",
        "ylos_current_step",
        "ylos_context_type",
        "ylos_asset_type",
    ]
    for prop in props:
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
