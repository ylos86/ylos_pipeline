# -*- coding: utf-8 -*-
# Scene naming checker + next-step readiness scanner.

import bpy
import re

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

STEP_ORDER = ["modeling", "rigging", "lookdev", "fx"]


def get_next_step(current_step: str):
    try:
        idx = STEP_ORDER.index(current_step)
        return STEP_ORDER[idx + 1] if idx + 1 < len(STEP_ORDER) else None
    except ValueError:
        return None


def _issue(severity, obj_name, message, fix_id=""):
    return {"severity": severity, "obj_name": obj_name,
            "message": message, "fix_id": fix_id}


def check_object_prefix(obj):
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


def check_datablock_name(obj):
    if obj.type not in ("MESH", "ARMATURE", "CURVE"):
        return None
    if obj.data and obj.data.name != obj.name:
        return _issue("WARNING", obj.name,
                      f"Datablock name mismatch: '{obj.data.name}' vs '{obj.name}'",
                      f"fix_datablock:{obj.name}")
    return None


def check_scale(obj):
    if obj.type not in ("MESH", "CURVE"):
        return None
    sx, sy, sz = obj.scale
    if abs(sx - 1.0) > 1e-4 or abs(sy - 1.0) > 1e-4 or abs(sz - 1.0) > 1e-4:
        return _issue("WARNING", obj.name,
                      f"Scale not applied: ({sx:.2f}, {sy:.2f}, {sz:.2f})", "")
    return None


def check_materials(obj) -> list:
    issues = []
    if obj.type != "MESH":
        return issues
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None:
            issues.append(_issue("WARNING", obj.name, "Empty material slot", ""))
            continue
        base = re.split(r"\.\d+$", mat.name)[0]
        if base in ("Material",) or not mat.name.startswith(MATERIAL_PREFIX):
            issues.append(_issue("WARNING", obj.name,
                                 f"Material '{mat.name}' missing MAT_ prefix",
                                 f"fix_material:{obj.name}:{mat.name}"))
    return issues


def check_uv_maps(obj) -> list:
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


def check_vertex_groups(obj):
    if obj.type != "MESH":
        return None
    if not obj.vertex_groups:
        return _issue("WARNING", obj.name,
                      "No vertex groups - needed if mesh will be skinned", "")
    return None


def check_armature_exists(scene):
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


def run_scene_check(context) -> dict:
    scene        = context.scene
    step         = scene.ylos_current_step
    next_step    = get_next_step(step)
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


def auto_fix(fix_id: str, context) -> str:
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


def _name_matches_asset(obj_name: str, prefix: str, asset_name: str) -> bool:
    expected = (prefix + asset_name).lower()
    low = obj_name.lower()
    if not low.startswith(expected):
        return False
    rest = low[len(expected):]
    return rest == "" or rest.startswith("_") or rest.startswith(".")


def get_asset_objects_for_publish(scene, asset_name: str, step: str) -> tuple:
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
        and any(_name_matches_asset(o.name, p, asset_name) for p in prefixes)
    ]

    if objects:
        return objects, f"name prefix ({', '.join(prefixes)})"

    return [], "none"


def check_collection_membership(scene, asset_name: str) -> list:
    issues = []
    coll = bpy.data.collections.get(asset_name)
    coll_object_names = (
        {o.name for o in coll.all_objects} if coll is not None else set()
    )

    for obj in scene.objects:
        if not _name_matches_asset(obj.name, "GEO_", asset_name):
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
