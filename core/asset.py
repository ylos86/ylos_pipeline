# -*- coding: utf-8 -*-
# Ylos Pipeline - core/asset.py
# Asset and shot creation on disk, path resolution for wip/publish,
# version detection (manual control - no auto-increment).

import os
import re
from pathlib import Path
from datetime import datetime
from .project import (
    ASSET_STEPS,
    SHOT_STEPS,
    SET_STEPS,
    load_project,
)


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
    """
    Return wip or publish dir for a given step.
    sub: "wip" or "publish"
    """
    return entity_root / step / sub


# ---------------------------------------------------------------------------
# Asset creation
# ---------------------------------------------------------------------------

def create_asset(project_path: str, asset_name: str, steps: list = None) -> dict:
    """
    Create the folder structure for a new asset.

    Args:
        project_path: Absolute path to project root.
        asset_name:   Asset name in PascalCase (e.g. "HeroCharacter").
        steps:        List of steps to create. Defaults to all ASSET_STEPS.

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
            "message": f"Asset already exists: {asset_name}",
        }

    try:
        for step in steps:
            (asset_root / step / "wip").mkdir(parents=True, exist_ok=True)
            (asset_root / step / "publish").mkdir(parents=True, exist_ok=True)

        # Write a minimal asset manifest
        _write_asset_manifest(asset_root, asset_name, steps)

    except Exception as e:
        return {"success": False, "asset_path": str(asset_root), "message": str(e)}

    return {
        "success": True,
        "asset_path": str(asset_root),
        "message": f"Asset {asset_name} created with steps: {', '.join(steps)}",
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
            "message": f"Shot already exists: {shot_name}",
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
        "message": f"Shot {shot_name} created.",
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
            "message": f"Set already exists: {set_name}",
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
        "message": f"Set {set_name} created.",
    }


def _write_asset_manifest(root: Path, name: str, steps: list, entity_type: str = "asset"):
    """Write a minimal YAML manifest alongside the asset folders."""
    import json
    manifest = {
        "name": name,
        "type": entity_type,
        "steps": steps,
        "publishes": {step: [] for step in steps},
    }
    with open(root / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=4, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------

VERSION_PATTERN         = re.compile(r"_v(\d{3})\.")
VERSION_VARIANT_PATTERN = re.compile(r"_v(\d{3})(?:__([A-Za-z][A-Za-z0-9]*))?\.(usd[az]?)$")


def list_wip_versions(project_path: str, entity_name: str, step: str,
                      entity_type: str = "asset") -> list[dict]:
    """
    Return all existing WIP files for a given entity+step, sorted by version.
    Each entry: {"version": int, "filename": str, "path": str}
    """
    root = _get_entity_root(project_path, entity_name, entity_type)
    wip_dir = root / step / "wip"

    if not wip_dir.exists():
        return []

    results = []
    for f in sorted(wip_dir.iterdir()):
        m = VERSION_PATTERN.search(f.name)
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
                          entity_type: str = "asset") -> list[dict]:
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
                            entity_type: str = "asset") -> str | None:
    """Return the path of the highest-version published USD for a step, or None."""
    versions = list_publish_versions(project_path, entity_name, step, entity_type)
    if not versions:
        return None
    return versions[-1]["path"]


def build_wip_filename(entity_name: str, step: str, version: int) -> str:
    """
    Construct a WIP .blend filename.
    e.g. HeroCharacter_modeling_v001.blend
    """
    return f"{entity_name}_{step}_v{version:03d}.blend"


def build_publish_filename(entity_name: str, step: str, version: int,
                           ext: str = "usd", variant: str = "") -> str:
    """
    Construct a publish USD filename.
    Default:  HeroCharacter_lookdev_v001.usd
    Variant:  HeroCharacter_lookdev_v001__Dirty.usd
    """
    if variant and variant.lower() not in ("", "default"):
        return f"{entity_name}_{step}_v{version:03d}__{variant}.{ext}"
    return f"{entity_name}_{step}_v{version:03d}.{ext}"


def resolve_wip_save_path(project_path: str, entity_name: str, step: str,
                          version: int, entity_type: str = "asset") -> str:
    """
    Full absolute path for a WIP save, given explicit version number.
    Manual version control - caller decides the version.
    """
    root = _get_entity_root(project_path, entity_name, entity_type)
    filename = build_wip_filename(entity_name, step, version)
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
                           entity_type: str = "asset") -> int:
    """
    Return the highest existing WIP version number, or 0 if none exist.
    Useful for suggesting the next version in the UI (latest + 1).
    """
    versions = list_wip_versions(project_path, entity_name, step, entity_type)
    if not versions:
        return 0
    return versions[-1]["version"]


def get_latest_publish_version(project_path: str, entity_name: str, step: str,
                               entity_type: str = "asset") -> int:
    """Return the highest existing publish version number, or 0 if none."""
    versions = list_publish_versions(project_path, entity_name, step, entity_type)
    if not versions:
        return 0
    return versions[-1]["version"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_entity_root(project_path: str, entity_name: str,
                     entity_type: str) -> Path:
    """Return the root Path for an entity based on its type."""
    if entity_type == "asset":
        return get_asset_root(project_path, entity_name)
    elif entity_type == "shot":
        return get_shot_root(project_path, entity_name)
    elif entity_type == "set":
        return get_set_root(project_path, entity_name)
    raise ValueError(f"Unknown entity type: {entity_type}")


# ---------------------------------------------------------------------------
# Project-level entity listing (used by asset list panel)
# ---------------------------------------------------------------------------

import time as _time

_entity_cache: dict = {}
_CACHE_TTL = 4.0   # seconds before re-reading from disk


def list_project_entities(project_path: str,
                          entity_type: str = "asset") -> list[dict]:
    """
    List all entities (assets/shots/sets) in the project.
    Returns list of dicts: {name, type_label, type_icon, path}
    Caches results for _CACHE_TTL seconds to avoid hammering the filesystem.
    """
    cache_key = f"{project_path}:{entity_type}"
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
                    import json
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
    """
    Returns {step_id: bool} — True if at least one publish exists for that step.
    """
    from .project import ASSET_STEPS, SHOT_STEPS, SET_STEPS
    step_map = {"asset": ASSET_STEPS, "shot": SHOT_STEPS, "set": SET_STEPS}
    steps = step_map.get(entity_type, ASSET_STEPS)
    return {
        step: get_latest_publish_version(project_path, asset_name, step, entity_type) > 0
        for step in steps
    }
