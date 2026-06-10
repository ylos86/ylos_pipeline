# -*- coding: utf-8 -*-
# ylos_blender/core_bpy/scene_checker.py
# Scene naming checker + next-step readiness scanner.
# Returns structured issue lists -- no bpy.ops calls here, pure logic.
#
# Pure naming constants and helpers (PREFIXES, sanitize_entity_name, etc.)
# live in ylos_core.naming. Only functions that require bpy objects or
# bpy.data live here.

import bpy
import re

from ylos_core.naming import (
    PREFIXES,
    MATERIAL_PREFIX,
    UV_DEFAULT_NAMES,
    DEFAULT_BLENDER_NAMES,
    STEP_ORDER,
    get_next_step,
    name_matches_asset,
)


# ---------------------------------------------------------------------------
# Issue factory
# ---------------------------------------------------------------------------

def _issue(severity, obj_name, message, fix_id=""):
    return {"severity": severity, "obj_name": obj_name,
            "message": message, "fix_id": fix_id}


# ---------------------------------------------------------------------------
# Per-object checks (take bpy objects as arguments)
# ---------------------------------------------------------------------------

def check_object_prefix(obj) -> dict | None:
    """Check that object has the correct type prefix."""
    prefix = PREFIXES.get(obj.type)
    if prefix is None:
        return None

    if not obj.name.startswith(prefix):
        base = re.split(r"\.\d+$", obj.name)[0]
        if base in DEFAULT_BLENDER_NAMES:
            return _issue("ERROR", obj.name,
                          f"Default Blender name - rename to {prefix}<name>",
                          f"fix_prefix:{obj.name}")
        return _issue("WARNING", obj.name,
                      f"Missing prefix - should start with {prefix}",
                      f"fix_prefix:{obj.name}")
    return None


def check_datablock_name(obj) -> dict | None:
    """Check that the mesh/armature datablock name matches the object name."""
    if obj.type not in ("MESH", "ARMATURE", "CURVE"):
        return None
    if obj.data and obj.data.name != obj.name:
        return _issue("WARNING", obj.name,
                      f"Datablock name mismatch: '{obj.data.name}' vs '{obj.name}'",
                      f"fix_datablock:{obj.name}")
    return None


def check_scale(obj) -> dict | None:
    """Check for unapplied scale (causes issues in USD + rigging)."""
    if obj.type not in ("MESH", "CURVE"):
        return None
    sx, sy, sz = obj.scale
    if abs(sx - 1.0) > 1e-4 or abs(sy - 1.0) > 1e-4 or abs(sz - 1.0) > 1e-4:
        return _issue("WARNING", obj.name,
                      f"Scale not applied: ({sx:.2f}, {sy:.2f}, {sz:.2f})",
                      "")
    return None


def check_materials(obj) -> list:
    """Check that all materials have MAT_ prefix."""
    issues = []
    if obj.type != "MESH":
        return issues
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None:
            issues.append(_issue("WARNING", obj.name,
                                 "Empty material slot", ""))
            continue
        base = re.split(r"\.\d+$", mat.name)[0]
        if base in ("Material",) or not mat.name.startswith(MATERIAL_PREFIX):
            issues.append(_issue("WARNING", obj.name,
                                 f"Material '{mat.name}' missing MAT_ prefix",
                                 f"fix_material:{obj.name}:{mat.name}"))
    return issues


def check_uv_maps(obj) -> list:
    """Check that mesh has at least one UV map with a meaningful name."""
    if obj.type != "MESH" or not obj.data:
        return []
    uvs = obj.data.uv_layers
    if not uvs:
        return [_issue("ERROR", obj.name,
                       "No UV map - required for LookDev and USD export", "")]
    issues = []
    for uv in uvs:
        if uv.name in UV_DEFAULT_NAMES:
            issues.append(_issue("WARNING", obj.name,
                                 f"UV map has default name '{uv.name}' - rename to e.g. UV0",
                                 f"fix_uv:{obj.name}:{uv.name}"))
    return issues


def check_vertex_groups(obj) -> dict | None:
    """Check that a mesh has vertex groups (needed for rigging step)."""
    if obj.type != "MESH":
        return None
    if not obj.vertex_groups:
        return _issue("WARNING", obj.name,
                      "No vertex groups - needed if mesh will be skinned", "")
    return None


def check_armature_exists(scene) -> dict | None:
    """Check that scene has at least one RIG_ armature."""
    rigs = [o for o in scene.objects if o.type == "ARMATURE"]
    if not rigs:
        return _issue("WARNING", "",
                      "No armature in scene - add RIG_<name> for rigging step", "")
    named = [o for o in rigs if o.name.startswith("RIG_")]
    if not named:
        return _issue("WARNING", "",
                      f"Armature(s) found but none named RIG_* ({rigs[0].name})",
                      f"fix_prefix:{rigs[0].name}")
    return None


# ---------------------------------------------------------------------------
# Step-aware full scan
# ---------------------------------------------------------------------------

def run_scene_check(context) -> dict:
    """
    Run all relevant checks for the current step and compute next-step readiness.

    Returns:
        {
          "current_step":   str,
          "next_step":      str | None,
          "current_issues": [issue, ...],
          "next_issues":    [issue, ...],
          "error_count":    int,
          "warning_count":  int,
        }
    """
    scene     = context.scene
    step      = scene.ylos_current_step
    next_step = get_next_step(step)
    current_issues = []
    next_issues    = []

    visible_objects = [o for o in scene.objects if not o.hide_get()
                       and o.type in PREFIXES]

    asset_name = scene.ylos_current_asset if hasattr(scene, "ylos_current_asset") else ""

    for obj in visible_objects:
        issue = check_object_prefix(obj)
        if issue:
            current_issues.append(issue)

        issue = check_datablock_name(obj)
        if issue:
            current_issues.append(issue)

        if step in ("modeling", "rigging", "lookdev", "fx"):
            issue = check_scale(obj)
            if issue:
                current_issues.append(issue)

    if asset_name:
        current_issues.extend(check_collection_membership(scene, asset_name))

    if next_step == "rigging":
        for obj in visible_objects:
            if obj.type == "MESH":
                issue = check_vertex_groups(obj)
                if issue:
                    next_issues.append(issue)
        issue = check_armature_exists(scene)
        if issue:
            next_issues.append(issue)

    elif next_step == "lookdev":
        for obj in visible_objects:
            next_issues.extend(check_uv_maps(obj))

    elif next_step == "fx" or next_step is None:
        for obj in visible_objects:
            next_issues.extend(check_materials(obj))

    errors   = sum(1 for i in current_issues if i["severity"] == "ERROR")
    warnings = sum(1 for i in current_issues if i["severity"] == "WARNING")

    return {
        "current_step":   step,
        "next_step":      next_step,
        "current_issues": current_issues,
        "next_issues":    next_issues,
        "error_count":    errors,
        "warning_count":  warnings,
    }


# ---------------------------------------------------------------------------
# Auto-fix helpers
# ---------------------------------------------------------------------------

def auto_fix(fix_id: str, context) -> str:
    """Attempt to auto-fix an issue by its fix_id. Returns a result message."""
    scene = context.scene

    if fix_id.startswith("fix_prefix:"):
        obj_name = fix_id.split(":", 1)[1]
        obj = scene.objects.get(obj_name)
        if not obj:
            return f"Object '{obj_name}' not found"
        prefix = PREFIXES.get(obj.type, "OBJ_")
        clean = re.sub(r"^[A-Z]+_", "", obj.name)
        new_name = f"{prefix}{clean}"
        obj.name = new_name
        if obj.data:
            obj.data.name = new_name
        return f"Renamed to '{new_name}'"

    if fix_id.startswith("fix_datablock:"):
        obj_name = fix_id.split(":", 1)[1]
        obj = scene.objects.get(obj_name)
        if obj and obj.data:
            obj.data.name = obj.name
            return f"Datablock renamed to '{obj.name}'"
        return f"Object '{obj_name}' not found"

    if fix_id.startswith("fix_material:"):
        _, obj_name, mat_name = fix_id.split(":", 2)
        mat = bpy.data.materials.get(mat_name)
        if mat:
            clean = re.sub(r"^[A-Z]+_", "", mat.name)
            mat.name = f"{MATERIAL_PREFIX}{clean}"
            return f"Material renamed to '{mat.name}'"
        return f"Material '{mat_name}' not found"

    if fix_id.startswith("fix_uv:"):
        parts = fix_id.split(":", 2)
        obj_name, uv_name = parts[1], parts[2]
        obj = scene.objects.get(obj_name)
        if obj and obj.data:
            uv = obj.data.uv_layers.get(uv_name)
            if uv:
                uv.name = "UV0"
                return "UV map renamed to 'UV0'"
        return f"Could not fix UV on '{obj_name}'"

    return f"No auto-fix for: {fix_id}"


# ---------------------------------------------------------------------------
# Asset collection helpers
# ---------------------------------------------------------------------------

def get_asset_objects_for_publish(scene, asset_name: str,
                                   step: str) -> tuple:
    """
    Find the objects to export for this asset/step.

    Priority:
      1. Collection named exactly {asset_name}
      2. Objects named with a step-relevant prefix + asset_name (whole field)

    Returns (objects, method_description).
    """
    coll = bpy.data.collections.get(asset_name)
    if coll:
        objects = [
            o for o in coll.all_objects
            if o.type in ("MESH", "ARMATURE", "CURVE", "EMPTY", "LATTICE")
            and not o.hide_get()
        ]
        if objects:
            return objects, f"collection '{asset_name}'"

    prefixes_by_step = {
        "modeling": ("GEO_",),
        "rigging":  ("GEO_", "RIG_"),
        "lookdev":  ("GEO_",),
        "fx":       ("GEO_", "FX_"),
    }
    prefixes = prefixes_by_step.get(step, ("GEO_",))

    objects = [
        o for o in scene.objects
        if not o.hide_get()
        and any(name_matches_asset(o.name, p, asset_name) for p in prefixes)
    ]

    if objects:
        return objects, f"name prefix ({', '.join(prefixes)})"

    return [], "none"


def check_collection_membership(scene, asset_name: str) -> list:
    """Check that GEO_{asset_name}* objects live inside the asset collection."""
    issues = []
    coll = bpy.data.collections.get(asset_name)
    coll_object_names = (
        {o.name for o in coll.all_objects} if coll is not None else set()
    )

    for obj in scene.objects:
        if not name_matches_asset(obj.name, "GEO_", asset_name):
            continue
        if coll is None:
            issues.append(_issue(
                "WARNING", obj.name,
                f"No collection '{asset_name}' - create it and move asset objects inside",
                "",
            ))
            break
        elif obj.name not in coll_object_names:
            issues.append(_issue(
                "WARNING", obj.name,
                f"Not in collection '{asset_name}' - move here for a clean publish",
                "",
            ))

    return issues
