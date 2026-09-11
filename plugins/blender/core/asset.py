# -*- coding: utf-8 -*-
# Ylos Pipeline - core/asset.py
# Read-only helpers: path resolution, version detection, entity listing.
# Creation logic removed — use create_project.py (source of truth).

import os
import sys
import re
from pathlib import Path
from datetime import datetime
from .project import (
    ASSET_STEPS,
    SHOT_STEPS,
    SET_STEPS,
    load_project,
)

# create_project.py (repo root) holds the SINGLE publish-reading logic; this
# addon is only a thin consumer of it. Lazy import (the repo root is injected into
# sys.path at the addon's register(), but these helpers may be called outside that path).
# core/asset.py -> core -> blender -> plugins -> repo = 4 levels up.
_REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    import create_project
    return create_project

# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------

ASSET_TYPE_PREFIXES = {
    "PROP":        "PROP",
    "CHARACTER":   "CHAR",
    "VEHICLE":     "VEH",
    "CREATURE":    "CREA",
    "FX_ELEMENT":  "FX",
    "ENVIRONMENT": "ENV",
}

ASSET_TYPE_PARENT_COL = {
    "PROP":        "COL_ENV_Props",
    "CHARACTER":   "COL_CHAR",
    "VEHICLE":     "COL_ENV_Props",
    "CREATURE":    "COL_CHAR",
    "FX_ELEMENT":  "COL_ENV_Props",
    "ENVIRONMENT": "COL_ENV",
}


def sanitize_entity_name(raw: str) -> str:
    name = raw.strip()
    name = re.sub(r'[\s\-]+', '', name)
    name = re.sub(r'[^A-Za-z0-9_]', '', name)
    name = name.lstrip('_0123456789')
    return name


# NOTE: the naming-convention gate (TYPE_Nom_Variant) lives in create_project.py
# (validate_entity_name) - single source of truth, called by op_new_asset.py before
# create_asset(). sanitize_entity_name() above is a pure input-cleanup helper, orthogonal
# to that gate (kept here since it's UI-input specific, not a pipeline contract).


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
    return entity_root / step / sub


def _get_entity_root(project_path: str, entity_name: str, entity_type: str) -> Path:
    if entity_type == "asset":
        return get_asset_root(project_path, entity_name)
    elif entity_type == "shot":
        return get_shot_root(project_path, entity_name)
    elif entity_type == "set":
        return get_set_root(project_path, entity_name)
    raise ValueError("Unknown entity type: " + entity_type)


# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------

# The WIP version regex and the sidecar reader used to live here (VERSION_PATTERN /
# _read_wip_sidecar). Both are gone: create_project owns the scenefile scan
# (_SCENEFILE_VER_RE + sidecar merge, every DCC at once) - see list_scenefiles below.
VERSION_VARIANT_PATTERN = re.compile(r"_v(\d{3})(?:__([A-Za-z][A-Za-z0-9]*))?\.(?:usd[az]?)$")


def _fallback_date(path: str) -> str:
    """Display date derived from the file's mtime ('Jun 15, 14:02'), used ONLY when the
    Prism-style sidecar carries none. Presentation detail, deliberately not pushed into the
    orchestrator (which returns the sidecar's raw ISO 'date')."""
    try:
        return datetime.fromtimestamp(os.path.getmtime(path)).strftime("%b %d, %H:%M")
    except OSError:
        return ""


def list_scenefiles(project_path: str, entity_name: str, step: str,
                    entity_type: str = "asset") -> list:
    """Thin adapter over create_project.list_scenefiles - EVERY DCC's versioned scenefiles
    of one step, ascending version. Single logic: the wip/ scan, the version regex and the
    sidecar merge all live in the orchestrator (SCENEFILE_EXTENSIONS). The local .blend-only
    duplicate scan is gone - it also made a Houdini WIP of the same step invisible here.
    'entity_type' is accepted for call-site compatibility but ignored: resolve_entity finds
    the family by itself.

    Row = the orchestrator's scenefile dict {version, filename, path, dcc, comment, user,
    date, dcc_version, blender_version}, with 'date' filled from the mtime when the sidecar
    has none (historical display shape of this module)."""
    rows = _cp().list_scenefiles(project_path, entity_name, step).get(step, [])
    for row in rows:
        if not row.get("date"):
            row["date"] = _fallback_date(row["path"])
    return rows


def list_wip_versions(project_path: str, entity_name: str, step: str,
                      entity_type: str = "asset") -> list:
    """Blender-OPENABLE WIP versions of a step = list_scenefiles filtered to dcc ==
    'blender'. A Houdini .hip* WIP of the same step is listed by list_scenefiles (visible in
    the panel, tagged with its DCC) but never here: Blender cannot open it, and an 'Open'
    button that fails is worse than an entry marked non-openable."""
    return [r for r in list_scenefiles(project_path, entity_name, step, entity_type)
            if r.get("dcc") == "blender"]


_USD_PUBLISH_EXTS = (".usd", ".usda", ".usdc", ".usdz", ".usdnc")


def list_publish_versions(project_path: str, entity_name: str, step: str,
                          entity_type: str = "asset") -> list:
    """Thin adapter over create_project.list_publishes (single logic): USD publishes
    of the step (nested two-phase contract + legacy flat files merged), historical shape
    {version, variant, filename, path} kept (USD only - a generic product import,
    GLB included, goes through resolve_publish_entry below, see INC-5). The old local flat
    scan (which made two-phase FOLDER publishes invisible) is
    removed - it was the cause of the 'No published USD found'."""
    results = []
    for e in _cp().list_publishes(project_path, entity_name, step, entity_type):
        artifact = e.get("artifact")
        abs_path = e.get("abs_path")
        if not artifact or not abs_path:
            continue  # 'pending' entry (no artifact yet)
        if not str(artifact).lower().endswith(_USD_PUBLISH_EXTS):
            continue  # this function is USD-only; a GLB/cache has no place here
        name = os.path.basename(abs_path)
        m = VERSION_VARIANT_PATTERN.search(name)
        variant = m.group(2) if (m and m.group(2)) else "Default"
        results.append({
            "version":  e.get("version"),
            "variant":  variant,
            "filename": name,
            "path":     abs_path,
        })
    return sorted(results, key=lambda x: (x["version"], x["variant"]))


def get_latest_publish_path(project_path: str, entity_name: str, step: str,
                            entity_type: str = "asset"):
    """Latest USD publish of the step (absolute path) or None - via list_publish_versions
    above (hence via the orchestrator). Fixes the broken Load Latest."""
    versions = list_publish_versions(project_path, entity_name, step, entity_type)
    if not versions:
        return None
    return versions[-1]["path"]


def build_wip_filename(entity_name: str, step: str, version: int) -> str:
    return entity_name + "_" + step + "_v" + str(version).zfill(3) + ".blend"


def resolve_wip_save_path(project_path: str, entity_name: str, step: str,
                          version: int, entity_type: str = "asset") -> str:
    root = _get_entity_root(project_path, entity_name, entity_type)
    filename = build_wip_filename(entity_name, step, version)
    return str(root / step / "wip" / filename)


def get_latest_wip_version(project_path: str, entity_name: str, step: str,
                           entity_type: str = "asset") -> int:
    """Highest BLENDER WIP version of the step, or 0 - thin wrapper over
    create_project._latest_wip(entity_dir, step, 'blender'), the single scan shared with
    resolve_open_target and scene_starter_spec (so the next Save Version can never disagree
    with the version the orchestrator would allocate). A Houdini .hip* WIP of the same step
    does NOT bump this number: WIP numbering is per DCC."""
    cp = _cp()
    resolved = cp.resolve_entity(project_path, entity_name)
    if resolved is None:
        return 0
    _path, version = cp._latest_wip(resolved["dir"], step, "blender")
    return version


def get_latest_publish_version(project_path: str, entity_name: str, step: str,
                               entity_type: str = "asset") -> int:
    """Max PUBLISHED version ('complete', any artifact type + legacy) of the step, or 0 - via
    create_project.latest_publish_artifact. Before: the flat scan did not see the two-phase
    folders -> returned 0 and skewed the publish dialog's next-version estimate
    (as well as get_asset_step_status). A non-USD artifact (GLB) counts here: versions are
    shared per step (see allocate_publish_version)."""
    latest = _cp().latest_publish_artifact(project_path, entity_name, step, entity_type)
    return latest["version"] if latest else 0


def read_entity_manifest(project_path: str, entity_name: str, entity_type: str = "asset") -> dict:
    """Raw entity manifest (type/entity_type/steps...) - {} if absent/unreadable, never an
    exception (same tolerance as the rest of the module).

    Thin wrapper over create_project.resolve_entity, which finds the entity in WHATEVER
    family it belongs to. 'entity_type' is accepted for call-site compatibility and ignored:
    passing the scene's context enum here was a real bug (an asset imported while the
    context said SHOT resolved to shots/<asset>/manifest.json, i.e. nothing)."""
    try:
        resolved = _cp().resolve_entity(project_path, entity_name)
    except Exception:
        return {}
    return (resolved or {}).get("manifest") or {}


def resolve_publish_entry(project_path: str, entity_name: str, step: str, version: int = None,
                          entity_type: str = "asset") -> dict | None:
    """'complete' publish entry for (entity, step[, version]) - INC-5 (import states).
    version None/0 -> the latest (create_project.latest_publish_artifact); explicit
    version -> EXACTLY that one, never a fallback to the latest (via
    create_project.list_publishes). 'abs_path' is already provided by the orchestrator - no
    path reconstruction here (same discipline as ylos_ui.py, see CLAUDE.md)."""
    cp = _cp()
    if not version:
        return cp.latest_publish_artifact(project_path, entity_name, step, entity_type)
    for entry in cp.list_publishes(project_path, entity_name, step, entity_type):
        if entry.get("version") == version and entry.get("status") == "complete":
            return entry
    return None


# ---------------------------------------------------------------------------
# Project-level entity listing
# ---------------------------------------------------------------------------

import time as _time

_entity_cache = {}
_CACHE_TTL = 4.0


_TYPE_PRESENTATION = {
    "PROP":        ("Prop",        "MESH_CUBE"),
    "CHARACTER":   ("Character",   "ARMATURE_DATA"),
    "ENVIRONMENT": ("Environment", "WORLD"),
    "SHOT":        ("Shot",        "SEQUENCE"),
    "SET":         ("Set",         "PACKAGE"),
}


def list_project_entities(project_path: str, entity_type: str = "asset") -> list:
    """Thin adapter over create_project.list_entities(project_root, family) - the local
    folder scan + manifest read is gone (it was a third copy of the same walk, and it
    silently swallowed an unreadable manifest instead of flagging it).

    Row = {name, type (sub-type), type_label, type_icon, path, steps, broken}. 'broken' is
    the orchestrator's orphan reason (folder with no readable manifest.json) - surfaced,
    never dropped. TTL cache kept: draw() is called on every UI redraw."""
    cache_key = project_path + ":" + entity_type
    cached = _entity_cache.get(cache_key)
    if cached and (_time.time() - cached["ts"]) < _CACHE_TTL:
        return cached["data"]

    results = []
    for e in _cp().list_entities(project_path, entity_type):
        asset_type = (e.get("entity_type") or "PROP").upper()
        label, icon = _TYPE_PRESENTATION.get(asset_type, ("Asset", "OBJECT_DATA"))
        results.append({
            "name":       e["name"],
            "type":       asset_type,
            "type_label": label,
            "type_icon":  icon,
            "path":       e["dir"],
            "steps":      e.get("steps") or [],
            "broken":     e.get("broken"),
        })

    _entity_cache[cache_key] = {"ts": _time.time(), "data": results}
    return results


def invalidate_entity_cache(project_path: str = None):
    global _entity_cache
    if project_path:
        for key in list(_entity_cache.keys()):
            if key.startswith(project_path):
                del _entity_cache[key]
    else:
        _entity_cache.clear()
    invalidate_step_status_cache(project_path)


# ---------------------------------------------------------------------------
# Per-step status (schema 2.2) - read-only view of create_project.get_step_status
# ---------------------------------------------------------------------------

_status_cache = {}


def get_entity_step_status(project_path: str, entity_name: str) -> dict:
    """{step: {"status", "explicit", "derived"}} for every declared step - thin adapter over
    create_project.get_step_status (single source: the EXPLICIT manifest value wins,
    otherwise empty/wip/published is derived from disk).

    TTL-cached like the entity list: a Blender panel draw() runs on every redraw and must
    not walk the manifest each frame. Invalidated by invalidate_entity_cache() (called by
    ylos.refresh_asset_list) and by ylos.set_step_status. Never raises: an unknown entity or
    an unreadable project returns {}."""
    cache_key = project_path + "::" + entity_name
    cached = _status_cache.get(cache_key)
    if cached and (_time.time() - cached["ts"]) < _CACHE_TTL:
        return cached["data"]
    try:
        data = _cp().get_step_status(project_path, entity_name) or {}
    except Exception:
        data = {}
    _status_cache[cache_key] = {"ts": _time.time(), "data": data}
    return data


def invalidate_step_status_cache(project_path: str = None):
    global _status_cache
    if project_path:
        for key in list(_status_cache):
            if key.startswith(project_path):
                del _status_cache[key]
    else:
        _status_cache.clear()


def get_asset_step_status(project_path: str, asset_name: str,
                          entity_type: str = "asset") -> dict:
    """{step: is_published} - historical boolean shape kept for existing call-sites, now
    read from get_entity_step_status (schema 2.2) instead of one latest_publish_artifact()
    call per step. Deliberately built on the DERIVED status: an explicit 'review'/'approved'
    is a production decision, not proof that an artifact exists on disk."""
    step_map = {"asset": ASSET_STEPS, "shot": SHOT_STEPS, "set": SET_STEPS}
    steps = step_map.get(entity_type, ASSET_STEPS)
    status = get_entity_step_status(project_path, asset_name)
    return {
        step: status.get(step, {}).get("derived") == "published"
        for step in steps
    }
