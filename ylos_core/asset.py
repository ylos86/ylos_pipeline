# -*- coding: utf-8 -*-
# ylos_core/asset.py
# Asset and shot creation on disk, path resolution for wip/publish,
# version detection (manual control -- no auto-increment).
# Pure stdlib -- no bpy, no hou, no pxr.

import os
import re
import json
import time as _time
from pathlib import Path
from datetime import datetime

from .project import (
    ASSET_STEPS,
    SHOT_STEPS,
    SET_STEPS,
    load_project,
)

# Re-export naming helpers so callers that import them from asset keep working.
from .naming import sanitize_entity_name, validate_entity_name


# ---------------------------------------------------------------------------
# Asset sub-type tables
# ---------------------------------------------------------------------------

# USD file naming prefix per asset sub-type (DOMAIN_AssetName_Variant.usd)
ASSET_TYPE_PREFIXES = {
    "PROP":        "PROP",
    "CHARACTER":   "CHAR",
    "ENVIRONMENT": "ENV",
}

# Parent collection in the Blender scene hierarchy per asset sub-type.
ASSET_TYPE_PARENT_COL = {
    "PROP":        "COL_ENV_Props",
    "CHARACTER":   "COL_CHAR",
    "ENVIRONMENT": "COL_ENV",
}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_asset_root(project_path: str, asset_name: str) -> Path:
    return Path(project_path) / "assets" / asset_name


def get_shot_root(project_path: str, shot_name: str) -> Path:
    return Path(project_path) / "shots" / shot_name


def get_set_root(project_path: str, set_name: str) -> Path:
    return Path(project_path) / "sets" / set_name


def get_step_path(entity_root: Path, step: str, sub: str) -> Path:
    """Return wip or publish dir for a given step. sub: 'wip' or 'publish'"""
    return entity_root / step / sub


# ---------------------------------------------------------------------------
# Asset creation
# ---------------------------------------------------------------------------

def create_asset(project_path: str, asset_name: str, steps: list = None,
                 asset_type: str = "PROP") -> dict:
    """
    Create the folder structure for a new asset.

    Args:
        project_path: Absolute path to project root.
        asset_name:   Asset name in PascalCase (e.g. "HeroCharacter").
        steps:        List of steps to create. Defaults to all ASSET_STEPS.
        asset_type:   Sub-type: "PROP" | "CHARACTER" | "ENVIRONMENT".

    Returns:
        dict with "success", "asset_path", "message".
    """
    if steps is None:
        steps = ASSET_STEPS

    asset_root = get_asset_root(project_path, asset_name)

    if asset_root.exists():
        return {
            "success": False,
            "asset_path": str(asset_root),
            "message": "Asset already exists: " + asset_name,
        }

    try:
        for step in steps:
            (asset_root / step / "wip").mkdir(parents=True, exist_ok=True)
            (asset_root / step / "publish").mkdir(parents=True, exist_ok=True)

        _write_asset_manifest(asset_root, asset_name, steps,
                              entity_type="asset", asset_subtype=asset_type)

    except Exception as e:
        return {"success": False, "asset_path": str(asset_root), "message": str(e)}

    return {
        "success": True,
        "asset_path": str(asset_root),
        "message": "Asset " + asset_name + " created with steps: " + ", ".join(steps),
    }


def create_shot(project_path: str, shot_name: str, steps: list = None) -> dict:
    """
    Create the folder structure for a new shot.
    shot_name convention: SQ010_SH0010
    """
    if steps is None:
        steps = SHOT_STEPS

    shot_root = get_shot_root(project_path, shot_name)

    if shot_root.exists():
        return {
            "success": False,
            "asset_path": str(shot_root),
            "message": "Shot already exists: " + shot_name,
        }

    try:
        for step in steps:
            (shot_root / step / "wip").mkdir(parents=True, exist_ok=True)
            (shot_root / step / "publish").mkdir(parents=True, exist_ok=True)

        _write_asset_manifest(shot_root, shot_name, steps, entity_type="shot")

    except Exception as e:
        return {"success": False, "asset_path": str(shot_root), "message": str(e)}

    return {
        "success": True,
        "asset_path": str(shot_root),
        "message": "Shot " + shot_name + " created.",
    }


def create_set(project_path: str, set_name: str, steps: list = None) -> dict:
    """Create the folder structure for a new set/environment."""
    if steps is None:
        steps = SET_STEPS

    set_root = get_set_root(project_path, set_name)

    if set_root.exists():
        return {
            "success": False,
            "asset_path": str(set_root),
            "message": "Set already exists: " + set_name,
        }

    try:
        for step in steps:
            (set_root / step / "wip").mkdir(parents=True, exist_ok=True)
            (set_root / step / "publish").mkdir(parents=True, exist_ok=True)

        _write_asset_manifest(set_root, set_name, steps, entity_type="set")

    except Exception as e:
        return {"success": False, "asset_path": str(set_root), "message": str(e)}

    return {
        "success": True,
        "asset_path": str(set_root),
        "message": "Set " + set_name + " created.",
    }


def _write_asset_manifest(root: Path, name: str, steps: list,
                           entity_type: str = "asset",
                           asset_subtype: str = "") -> None:
    """
    Write manifest.json alongside the asset folders.
    NOTE: this is the entity-level manifest (folder metadata).
    Publish sidecars are handled by ylos_core.manifest.
    """
    type_field = asset_subtype.upper() if asset_subtype else entity_type.upper()
    manifest = {
        "name":        name,
        "type":        type_field,
        "entity_type": entity_type,
        "steps":       steps,
        "publishes":   {step: [] for step in steps},
    }
    with open(root / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=4, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------

VERSION_PATTERN         = re.compile(r"_v(\d{3})\.blend$")
VERSION_VARIANT_PATTERN = re.compile(r"_v(\d{3})(?:__([A-Za-z][A-Za-z0-9]*))?\.(?:usd[az]?)$")

# Houdini saves .hip (commercial), .hiplc (indie), .hipnc (apprentice).
# All three are valid WIP extensions for the Houdini adapter.
HIP_EXTENSIONS = ("hip", "hiplc", "hipnc")


def _make_wip_pattern(exts):
    """Build a compiled regex matching versioned WIPs for the given extensions."""
    ext_alts = "|".join(re.escape(e.lstrip(".")) for e in exts)
    return re.compile(r"_v(\d{3})\.(?:" + ext_alts + r")$")


def list_wip_versions(project_path: str, entity_name: str, step: str,
                      entity_type: str = "asset",
                      exts: list = None) -> list:
    """
    Return all existing WIP files for a given entity+step, sorted by version.

    Args:
        exts: List of file extensions to include (without leading dot).
              Defaults to ["blend"]. Pass list(HIP_EXTENSIONS) for Houdini.

    Each entry: {"version": int, "filename": str, "path": str, "date": str}
    """
    if exts is None:
        exts = ["blend"]
    ext_set = {e.lower().lstrip(".") for e in exts}
    pattern = _make_wip_pattern(ext_set)

    root = _get_entity_root(project_path, entity_name, entity_type)
    wip_dir = root / step / "wip"

    if not wip_dir.exists():
        return []

    results = []
    for f in sorted(wip_dir.iterdir()):
        if f.suffix.lower().lstrip(".") not in ext_set:
            continue
        m = pattern.search(f.name)
        if m:
            mtime = f.stat().st_mtime
            date_str = datetime.fromtimestamp(mtime).strftime("%b %d, %H:%M")
            results.append({
                "version":  int(m.group(1)),
                "filename": f.name,
                "path":     str(f),
                "date":     date_str,
            })

    return sorted(results, key=lambda x: x["version"])


def list_publish_versions(project_path: str, entity_name: str, step: str,
                          entity_type: str = "asset") -> list:
    """Return all published USD files for a given entity+step."""
    root = _get_entity_root(project_path, entity_name, entity_type)
    pub_dir = root / step / "publish"

    if not pub_dir.exists():
        return []

    results = []
    for f in sorted(pub_dir.iterdir()):
        if f.suffix in (".usd", ".usda", ".usdc", ".usdz"):
            m = VERSION_VARIANT_PATTERN.search(f.name)
            if m:
                results.append({
                    "version":  int(m.group(1)),
                    "variant":  m.group(2) or "Default",
                    "filename": f.name,
                    "path":     str(f),
                })

    return sorted(results, key=lambda x: (x["version"], x["variant"]))


def get_latest_publish_path(project_path: str, entity_name: str, step: str,
                            entity_type: str = "asset"):
    """Return the path of the highest-version published USD for a step, or None."""
    versions = list_publish_versions(project_path, entity_name, step, entity_type)
    if not versions:
        return None
    return versions[-1]["path"]


def build_wip_filename(entity_name: str, step: str, version: int,
                       ext: str = "blend") -> str:
    """
    Construct a WIP filename.
    e.g. HeroCharacter_modeling_v001.blend  (Blender)
         HeroCharacter_modeling_v001.hipnc  (Houdini Apprentice)
    """
    return entity_name + "_" + step + "_v" + str(version).zfill(3) + "." + ext.lstrip(".")


def build_publish_filename(entity_name: str, step: str, version: int,
                           ext: str = "usd", variant: str = "") -> str:
    base = entity_name + "_" + step + "_v" + str(version).zfill(3)
    if variant and variant.lower() not in ("", "default"):
        return base + "__" + variant + "." + ext
    return base + "." + ext


def resolve_wip_save_path(project_path: str, entity_name: str, step: str,
                          version: int, entity_type: str = "asset",
                          ext: str = "blend") -> str:
    """Full absolute path for a WIP save, given explicit version and extension."""
    root = _get_entity_root(project_path, entity_name, entity_type)
    filename = build_wip_filename(entity_name, step, version, ext)
    return str(root / step / "wip" / filename)


def resolve_publish_path(project_path: str, entity_name: str, step: str,
                         version: int, ext: str = "usd",
                         entity_type: str = "asset",
                         variant: str = "") -> str:
    """Full absolute path for a USD publish file."""
    root = _get_entity_root(project_path, entity_name, entity_type)
    filename = build_publish_filename(entity_name, step, version, ext, variant)
    return str(root / step / "publish" / filename)


def get_latest_wip_version(project_path: str, entity_name: str, step: str,
                           entity_type: str = "asset",
                           exts: list = None) -> int:
    """Return the highest existing WIP version number, or 0 if none exist."""
    versions = list_wip_versions(project_path, entity_name, step, entity_type, exts)
    if not versions:
        return 0
    return versions[-1]["version"]


def get_latest_publish_version(project_path: str, entity_name: str, step: str,
                               entity_type: str = "asset") -> int:
    versions = list_publish_versions(project_path, entity_name, step, entity_type)
    if not versions:
        return 0
    return versions[-1]["version"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_entity_root(project_path: str, entity_name: str,
                     entity_type: str) -> Path:
    if entity_type == "asset":
        return get_asset_root(project_path, entity_name)
    elif entity_type == "shot":
        return get_shot_root(project_path, entity_name)
    elif entity_type == "set":
        return get_set_root(project_path, entity_name)
    raise ValueError("Unknown entity type: " + entity_type)


# ---------------------------------------------------------------------------
# Project-level entity listing (used by asset list panel)
# ---------------------------------------------------------------------------

_entity_cache = {}
_CACHE_TTL = 4.0


def list_project_entities(project_path: str,
                          entity_type: str = "asset") -> list:
    """
    List all entities (assets/shots/sets) in the project.
    Returns list of dicts: {name, type_label, type_icon, path}
    Caches results for _CACHE_TTL seconds.
    """
    cache_key = project_path + ":" + entity_type
    cached = _entity_cache.get(cache_key)
    if cached and (_time.time() - cached["ts"]) < _CACHE_TTL:
        return cached["data"]

    folder_map = {"asset": "assets", "shot": "shots", "set": "sets"}
    base = Path(project_path) / folder_map.get(entity_type, "assets")

    if not base.is_dir():
        return []

    results = []
    try:
        for d in sorted(base.iterdir()):
            if not d.is_dir():
                continue
            manifest_path = d / "manifest.json"
            asset_type = "PROP"
            if manifest_path.exists():
                try:
                    with open(manifest_path) as f:
                        mf = json.load(f)
                    asset_type = mf.get("type", "PROP").upper()
                except Exception:
                    pass

            type_map = {
                "PROP":        ("Prop",        "MESH_CUBE"),
                "CHARACTER":   ("Character",   "ARMATURE_DATA"),
                "ENVIRONMENT": ("Environment", "WORLD"),
                "SHOT":        ("Shot",        "SEQUENCE"),
                "SET":         ("Set",         "PACKAGE"),
            }
            label, icon = type_map.get(asset_type, ("Asset", "OBJECT_DATA"))

            results.append({
                "name":       d.name,
                "type":       asset_type,
                "type_label": label,
                "type_icon":  icon,
                "path":       str(d),
            })
    except Exception:
        pass

    _entity_cache[cache_key] = {"ts": _time.time(), "data": results}
    return results


def invalidate_entity_cache(project_path: str = None):
    """Call after creating a new asset to force a list refresh."""
    global _entity_cache
    if project_path:
        for key in list(_entity_cache.keys()):
            if key.startswith(project_path):
                del _entity_cache[key]
    else:
        _entity_cache.clear()


def get_asset_step_status(project_path: str, asset_name: str,
                          entity_type: str = "asset") -> dict:
    """Returns {step_id: bool} - True if at least one publish exists for that step."""
    step_map = {"asset": ASSET_STEPS, "shot": SHOT_STEPS, "set": SET_STEPS}
    steps = step_map.get(entity_type, ASSET_STEPS)
    return {
        step: get_latest_publish_version(project_path, asset_name, step, entity_type) > 0
        for step in steps
    }
