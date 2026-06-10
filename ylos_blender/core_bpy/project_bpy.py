# -*- coding: utf-8 -*-
# ylos_blender/core_bpy/project_bpy.py
# Blender scene setup helpers and custom property registration.
# Pure pipeline logic (project creation, JSON I/O) lives in ylos_core.project.

import bpy
from ylos_core.project import SCENE_PRESETS


# ---------------------------------------------------------------------------
# Scene preset application
# ---------------------------------------------------------------------------

def apply_scene_preset(scene: bpy.types.Scene, prod_type: str) -> None:
    """Apply Ylos scene settings to the given Blender scene based on prod type."""
    if prod_type not in SCENE_PRESETS:
        return

    preset = SCENE_PRESETS[prod_type]

    scene.render.fps = preset["fps"]
    scene.render.fps_base = preset["fps_base"]

    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = preset["unit_scale"]
    scene.unit_settings.length_unit = "METERS"

    scene.render.resolution_x = preset["resolution_x"]
    scene.render.resolution_y = preset["resolution_y"]
    scene.render.resolution_percentage = 100

    scene.render.engine = preset["renderer"]

    if prod_type == "FILM":
        scene.view_settings.view_transform = "AgX"
        scene.view_settings.look = "None"
    else:
        scene.view_settings.view_transform = "Standard"


# ---------------------------------------------------------------------------
# Collection hierarchy setup
# ---------------------------------------------------------------------------

def setup_scene_collections(scene: bpy.types.Scene) -> None:
    """
    Create Ylos scene collection hierarchy if not already present.
    Follows Black Kite naming conventions.
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
        subtype="NONE",
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
            ("modeling",   "Modeling",   ""),
            ("rigging",    "Rigging",    ""),
            ("lookdev",    "LookDev",    ""),
            ("fx",         "FX",         ""),
            ("layout",     "Layout",     ""),
            ("animation",  "Animation",  ""),
            ("lighting",   "Lighting",   ""),
            ("render",     "Render",     ""),
            ("composite",  "Composite",  ""),
        ],
        default="modeling",
    )

    bpy.types.Scene.ylos_context_type = bpy.props.EnumProperty(
        name="Context",
        description="What kind of entity is being worked on",
        items=[
            ("ASSET", "Asset", "Working on a character, prop, or environment asset"),
            ("SHOT",  "Shot",  "Working on a specific shot"),
            ("SET",   "Set",   "Working on a set / environment assembly"),
        ],
        default="ASSET",
    )

    bpy.types.Scene.ylos_asset_type = bpy.props.EnumProperty(
        name="Asset Type",
        description="Sub-type of the asset (only relevant when Context = Asset)",
        items=[
            ("PROP",        "Prop",        "Hard-surface object, furniture, tool, vehicle..."),
            ("CHARACTER",   "Character",   "Biped, creature, hero, NPC..."),
            ("ENVIRONMENT", "Environment", "Terrain piece, modular kit, vegetation..."),
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
