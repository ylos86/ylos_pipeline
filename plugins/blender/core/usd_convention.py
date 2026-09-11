# -*- coding: utf-8 -*-
# Ylos Pipeline - core/usd_convention.py
# ============================================================================
# SINGLE translation of the pipeline's USD convention (docs/usd-convention.md) into
# Blender's wm.usd_export / wm.usd_import keyword arguments. Every USD write the addon
# performs goes through here - op_publish (pipeline publishes) and op_io (raw export).
# Duplicating these kwargs at each call-site is exactly how a project ends up with half its
# layers Z-up (the flaw the convention doc records for the real 'Lina' / 'lecube' projects).
#
# The VALUES are NOT decided here: create_project.USD_UP_AXIS / USD_METERS_PER_UNIT /
# USD_ROOT_PRIM own the convention (principle 5). This module only maps them onto the RNA
# of the running Blender.
#
# VERIFIED ON BLENDER 5.2.0 LTS (headless probe, see the workstream report):
#   * convert_orientation=True + export_global_up_selection="Y" +
#     export_global_forward_selection="NEGATIVE_Z"  ->  header 'upAxis = "Y"', the
#     conversion baked as xformOp:rotateXYZ = (-90, 0, 0) on the root prim (mesh points stay
#     in Blender space - this is how Blender does it, and it round-trips exactly).
#   * root_prim_path="/PROP_Box_Default"            ->  header 'defaultPrim = "PROP_Box_Default"'.
#   * convert_scene_units="METERS"                  ->  header 'metersPerUnit = 1'.
#   * wm.usd_import DEFAULTS already invert all of it (merge_parent_xform=True,
#     apply_unit_conversion_scale=True): a Y-up meters stage re-imports with an identical
#     world bounding box. No import-side kwarg is needed for orientation - we still pass the
#     two explicitly so a future default change cannot silently rotate imports.
#
# Blender 4.2 compatibility: convert_scene_units / meters_per_unit / merge_parent_xform do
# not exist on every supported build. Every kwarg is therefore FILTERED against the running
# operator's RNA (supported_kwargs) - an unknown one is dropped with a console note rather
# than raising TypeError mid-publish.
# ============================================================================

import os
import sys

import bpy

# core/usd_convention.py -> core -> blender -> plugins -> repo root = 4 levels up
# (same self-bootstrap as core/vocab.py: this module is imported before register()).
_REPO_ROOT = os.path.normpath(
    os.path.join(os.path.realpath(__file__), "..", "..", "..", "..")
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import create_project as _cp

# Forward axis paired with the convention's up axis. USD's Y-up convention is Y-up /
# -Z-forward; kept next to the up axis so the pair is never split.
USD_FORWARD_AXIS = "NEGATIVE_Z"

# Scene-unit enum of wm.usd_export whose metersPerUnit matches the convention exactly.
# Anything else goes through CUSTOM + meters_per_unit.
_UNIT_ENUM_BY_MPU = {
    1.0: "METERS",
    1000.0: "KILOMETERS",
    0.01: "CENTIMETERS",
    0.001: "MILLIMETERS",
}


def supported_kwargs(op, kwargs):
    """Drop the kwargs the running Blender's operator does not declare (RNA introspection,
    same discipline as the render-engine probing of core/project.py: never hard-code what a
    Blender version supports). Returns a NEW dict; the dropped names are printed once so a
    silently degraded export is still visible in the console."""
    try:
        known = {p.identifier for p in op.get_rna_type().properties}
    except Exception:                      # noqa: BLE001 - RNA unavailable: pass through
        return dict(kwargs)
    kept, dropped = {}, []
    for key, value in kwargs.items():
        if key in known:
            kept[key] = value
        else:
            dropped.append(key)
    if dropped:
        print(f"[Ylos USD] kwargs unsupported by this Blender build, ignored: "
              f"{', '.join(sorted(dropped))}")
    return kept


def root_prim_path(entity_name, family):
    """Root prim every publish of an entity must author under (docs/usd-convention.md §3):
      * asset / set -> '/<EntityName>'  (defaultPrim '<EntityName>': referenced into a set
        it lands correctly named, and the step subLayers stack on the same prim);
      * shot        -> create_project.USD_ROOT_PRIM ('/ROOT'), because a shot IS the
        assembly stage - its shot_root.usda authors /ROOT and the step layers must stack there.
    An unknown/empty family falls back to the asset rule (the safe one: a named prim)."""
    if family == "shot":
        return _cp.USD_ROOT_PRIM
    return "/" + str(entity_name).lstrip("/")


def export_kwargs(entity_name=None, family="asset", selected_only=False):
    """Convention kwargs for bpy.ops.wm.usd_export - Y-up, metersPerUnit from the
    orchestrator, rooted at root_prim_path(). 'entity_name' None -> no root_prim_path is
    forced (raw export, ylos.raw_export: orientation and units still follow the convention,
    but the user's file is not re-rooted under a pipeline entity)."""
    kwargs = {
        "convert_orientation": True,
        "export_global_up_selection": _cp.USD_UP_AXIS,
        "export_global_forward_selection": USD_FORWARD_AXIS,
    }
    mpu = float(_cp.USD_METERS_PER_UNIT)
    unit_enum = _UNIT_ENUM_BY_MPU.get(mpu)
    if unit_enum:
        kwargs["convert_scene_units"] = unit_enum
    else:
        kwargs["convert_scene_units"] = "CUSTOM"
        kwargs["meters_per_unit"] = mpu
    if selected_only:
        kwargs["selected_objects_only"] = True
    if entity_name:
        kwargs["root_prim_path"] = root_prim_path(entity_name, family)
    return supported_kwargs(bpy.ops.wm.usd_export, kwargs)


def import_kwargs():
    """Convention kwargs for bpy.ops.wm.usd_import. Both are already Blender 5.2 defaults;
    passing them explicitly pins the round-trip (a stage exported Y-up / metersPerUnit comes
    back at its original Blender orientation and scale) against a future default change."""
    return supported_kwargs(bpy.ops.wm.usd_import, {
        "merge_parent_xform": True,          # re-absorb the root's Y-up xformOp
        "apply_unit_conversion_scale": True,  # honour the stage's metersPerUnit
    })
