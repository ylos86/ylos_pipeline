# -*- coding: utf-8 -*-
# ylos_core/naming.py
# Pure naming conventions, sanitizers and validators.
# No bpy, no hou, no pxr -- used by both the Blender and Houdini adapters.

import re


# ---------------------------------------------------------------------------
# Object-type prefix table (mirrors Blender types for the Blender adapter,
# referenced by the Houdini adapter for cross-DCC naming consistency).
# ---------------------------------------------------------------------------

PREFIXES = {
    "MESH":     "GEO_",
    "ARMATURE": "RIG_",
    "LIGHT":    "LGT_",
    "CAMERA":   "CAM_",
    "EMPTY":    "CTRL_",
    "CURVE":    "CRV_",
    "LATTICE":  "LAT_",
}

MATERIAL_PREFIX = "MAT_"

UV_DEFAULT_NAMES = {"UVMap", "UVChannel_1", "uv0"}

DEFAULT_BLENDER_NAMES = {
    "Cube", "Plane", "Sphere", "UV Sphere", "Ico Sphere",
    "Cylinder", "Cone", "Torus", "Circle", "Grid", "Monkey",
    "Camera", "Light", "Point", "Sun", "Spot", "Area",
    "Armature", "Empty", "Curve", "BezierCurve", "Text",
    "Lattice", "Metaball",
}

# Steps in asset pipeline order (used by scene_checker for next-step logic).
STEP_ORDER = ["modeling", "rigging", "lookdev", "fx"]


# ---------------------------------------------------------------------------
# Entity name helpers
# ---------------------------------------------------------------------------

def sanitize_entity_name(raw: str) -> str:
    """
    Normalize a raw user-typed name into a pipeline-safe identifier.

    Rules:
    - Strips leading/trailing whitespace
    - Collapses spaces and hyphens (PascalCase join: 'Hero Char' -> 'HeroChar')
    - Drops any character outside [A-Za-z0-9_]
    - Strips leading underscores and digits

    Returns '' if nothing remains (caller should reject).
    """
    name = raw.strip()
    name = re.sub(r'[\s\-]+', '', name)
    name = re.sub(r'[^A-Za-z0-9_]', '', name)
    name = name.lstrip('_0123456789')
    return name


def validate_entity_name(name: str) -> tuple:
    """
    Validate a (sanitized) entity name.

    Returns (is_valid: bool, error_message: str).
    Requirements: non-empty, at least 2 chars, starts with uppercase (PascalCase).
    """
    if not name:
        return False, "Name cannot be empty."
    if len(name) < 2:
        return False, "Name must be at least 2 characters."
    if not name[0].isupper():
        return (
            False,
            "Name must start with an uppercase letter (PascalCase). Got: '" + name + "'",
        )
    return True, ""


# ---------------------------------------------------------------------------
# Object-name helpers (used by scene_checker and the Houdini adapter)
# ---------------------------------------------------------------------------

def name_matches_asset(obj_name: str, prefix: str, asset_name: str) -> bool:
    """
    True if obj_name follows PREFIX_AssetName(_...) for the given asset.

    Requires the asset name to be a whole field:
      'GEO_Hero' or 'GEO_Hero_A' match asset 'Hero',
      'GEO_HeroSword' does NOT (avoids false positives).
    """
    expected = (prefix + asset_name).lower()
    low = obj_name.lower()
    if not low.startswith(expected):
        return False
    rest = low[len(expected):]
    return rest == "" or rest.startswith("_") or rest.startswith(".")


# ---------------------------------------------------------------------------
# Step ordering
# ---------------------------------------------------------------------------

def get_next_step(current_step: str) -> str | None:
    """
    Return the next step after current_step in STEP_ORDER, or None if last.
    """
    try:
        idx = STEP_ORDER.index(current_step)
        return STEP_ORDER[idx + 1] if idx + 1 < len(STEP_ORDER) else None
    except ValueError:
        return None
