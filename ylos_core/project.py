# -*- coding: utf-8 -*-
# ylos_core/project.py
# Project creation, project.json I/O, step/context validation.
# Pure stdlib -- no bpy, no hou, no pxr.
# Blender scene setup (apply_scene_preset, register_properties, etc.)
# lives in ylos_blender/core_bpy/project_bpy.py.

import os
import json
from pathlib import Path
from datetime import datetime

from . import SCHEMA_VERSION
from .locking import atomic_write_json


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

# Step-owner matrix (S-2.1 of architecture doc).
# Value is the DCC that owns authoring for that step.
# "any"    -- either DCC may publish; last-write-wins (solo assumption).
# Blender/Houdini adapters use this to grey/deprioritize foreign steps in their UI.
# Publishing a foreign step triggers a confirmable warning, not a hard block.
STEP_OWNERS = {
    "modeling":  "blender",
    "rigging":   "blender",
    "fx":        "blender",
    "lookdev":   "houdini",
    "layout":    "houdini",
    "lighting":  "any",
    "rendering": "any",
}

# Valid steps per context type (used to guard publish/save against
# steps that have no folder for the active entity type).
STEPS_BY_CONTEXT = {
    "asset": ASSET_STEPS,
    "shot":  SHOT_STEPS,
    "set":   SET_STEPS,
}


def get_step_owner(config: dict, step: str) -> str:
    """
    Return the DCC that owns the given step, from project.json step_owners.
    Returns "any" if the step is not in the matrix or config is missing.
    Possible return values: "blender", "houdini", "any".
    """
    return config.get("step_owners", STEP_OWNERS).get(step, "any")


def is_step_valid_for_context(step: str, context_type: str) -> bool:
    """
    Return True if `step` is a real production step for the given context type.

    The scene ylos_current_step enum lists every step across asset/shot/set
    for UI convenience, so it is possible to select e.g. 'composite' while
    the active context is an asset. That step has no folder for an asset, so
    callers must validate before resolving a save/publish path.
    """
    return step in STEPS_BY_CONTEXT.get(context_type.lower(), ASSET_STEPS)


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

    try:
        for folder in PROJECT_FOLDERS:
            (project_path / folder).mkdir(parents=True, exist_ok=True)

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
    """Build the project.json content dict (schema_version 2)."""
    return {
        "schema_version": SCHEMA_VERSION,
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
            "shot_steps":  SHOT_STEPS,
            "set_steps":   SET_STEPS,
            "usd_root_prim": "/ROOT",
        },
        "step_owners": STEP_OWNERS,
        "scene": SCENE_PRESETS[prod_type],
        "delivery": {
            "targets": DELIVERY_TARGETS[prod_type],
        },
    }


def _write_project_json(project_path: Path, config: dict) -> None:
    """Write project.json to the _pipeline folder via atomic write."""
    pipeline_dir = project_path / PIPELINE_DIR
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    config_path = pipeline_dir / PROJECT_CONFIG_FILE
    atomic_write_json(str(config_path), config)


# ---------------------------------------------------------------------------
# Project loading
# ---------------------------------------------------------------------------

def load_project(project_path: str) -> dict | None:
    """
    Load a project.json from a given project path.

    Validates schema_version: refuses with a clear error if the version is
    higher than what this code knows (forward compatibility guard).

    Returns the config dict on success, or None if not found / invalid.
    """
    config_path = Path(project_path) / PIPELINE_DIR / PROJECT_CONFIG_FILE

    if not config_path.exists():
        return None

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None

    # Schema version check -- lenient for v1 projects (no schema_version key).
    file_version = data.get("schema_version", 1)
    if file_version > SCHEMA_VERSION:
        raise RuntimeError(
            f"project.json schema_version {file_version} is newer than "
            f"this version of ylos_core (max supported: {SCHEMA_VERSION}). "
            f"Please update the Ylos Pipeline addon."
        )

    # Back-compat: inject missing keys with defaults on first read.
    # These are written back at the next atomic project.json update.
    if "schema_version" not in data:
        data["schema_version"] = 1
    if "step_owners" not in data:
        data["step_owners"] = STEP_OWNERS

    return data


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
