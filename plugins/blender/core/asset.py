# -*- coding: utf-8 -*-
# Ylos Pipeline - core/asset.py
# Read-only helpers: path resolution, version detection, entity listing.
# Creation logic removed — use create_project.py (source of truth).

import json
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

# create_project.py (racine du repo) porte la logique UNIQUE de lecture des publishes ; cet
# addon n'en est qu'un consommateur mince. Import paresseux (le repo root est injecte dans
# sys.path au register() de l'addon, mais ces helpers peuvent etre appeles hors de ce chemin).
# core/asset.py -> core -> blender -> plugins -> repo = 4 remontees.
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

VERSION_PATTERN         = re.compile(r"_v(\d{3})\.blend$")
VERSION_VARIANT_PATTERN = re.compile(r"_v(\d{3})(?:__([A-Za-z][A-Za-z0-9]*))?\.(?:usd[az]?)$")


def _read_wip_sidecar(blend_path: Path) -> dict:
    """Sidecar '<wip>.blend.json' (comment/user/date/blender_version, ecrit par
    ylos.save_wip) - {} si absent ou illisible, jamais d'exception (meme convention
    tolerante que le reste du module)."""
    sidecar = blend_path.with_name(blend_path.name + ".json")
    if not sidecar.is_file():
        return {}
    try:
        return json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def list_wip_versions(project_path: str, entity_name: str, step: str,
                      entity_type: str = "asset") -> list:
    root = _get_entity_root(project_path, entity_name, entity_type)
    wip_dir = root / step / "wip"

    if not wip_dir.exists():
        return []

    results = []
    for f in sorted(wip_dir.iterdir()):
        if f.suffix.lower() != ".blend":
            continue
        m = VERSION_PATTERN.search(f.name)
        if m:
            mtime = f.stat().st_mtime
            date_str = datetime.fromtimestamp(mtime).strftime("%b %d, %H:%M")
            sidecar = _read_wip_sidecar(f)
            results.append({
                "version":  int(m.group(1)),
                "filename": f.name,
                "path":     str(f),
                "date":     date_str,
                "comment":  sidecar.get("comment", ""),
                "user":     sidecar.get("user", ""),
            })

    return sorted(results, key=lambda x: x["version"])


_USD_PUBLISH_EXTS = (".usd", ".usda", ".usdc", ".usdz", ".usdnc")


def list_publish_versions(project_path: str, entity_name: str, step: str,
                          entity_type: str = "asset") -> list:
    """Adaptateur mince sur create_project.list_publishes (logique unique) : publishes USD
    du step (contrat deux-phases niche + fichiers plats legacy fusionnes), forme historique
    {version, variant, filename, path} conservee (consommee par op_load_publish). Le scan a
    plat local d'antan (qui rendait invisibles les publishes deux-phases en DOSSIER) est
    supprime - c'etait la cause du 'No published USD found'."""
    results = []
    for e in _cp().list_publishes(project_path, entity_name, step, entity_type):
        artifact = e.get("artifact")
        abs_path = e.get("abs_path")
        if not artifact or not abs_path:
            continue  # entree 'pending' (pas encore d'artefact)
        if not str(artifact).lower().endswith(_USD_PUBLISH_EXTS):
            continue  # op_load_publish importe de l'USD ; un GLB/cache n'y a pas sa place
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
    """Dernier publish USD du step (chemin absolu) ou None - via list_publish_versions
    ci-dessus (donc via l'orchestrateur). Corrige le Load Latest casse."""
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
    versions = list_wip_versions(project_path, entity_name, step, entity_type)
    if not versions:
        return 0
    return versions[-1]["version"]


def get_latest_publish_version(project_path: str, entity_name: str, step: str,
                               entity_type: str = "asset") -> int:
    """Version max PUBLIEE ('complete', tout type d'artefact + legacy) du step, ou 0 - via
    create_project.latest_publish_artifact. Avant : le scan a plat ne voyait pas les dossiers
    deux-phases -> retournait 0 et faussait l'estimation de prochaine version du dialog publish
    (ainsi que get_asset_step_status). Un artefact non-USD (GLB) compte ici : les versions sont
    partagees par step (cf. allocate_publish_version)."""
    latest = _cp().latest_publish_artifact(project_path, entity_name, step, entity_type)
    return latest["version"] if latest else 0


# ---------------------------------------------------------------------------
# Project-level entity listing
# ---------------------------------------------------------------------------

import time as _time

_entity_cache = {}
_CACHE_TTL = 4.0


def list_project_entities(project_path: str, entity_type: str = "asset") -> list:
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
    global _entity_cache
    if project_path:
        for key in list(_entity_cache.keys()):
            if key.startswith(project_path):
                del _entity_cache[key]
    else:
        _entity_cache.clear()


def get_asset_step_status(project_path: str, asset_name: str,
                          entity_type: str = "asset") -> dict:
    step_map = {"asset": ASSET_STEPS, "shot": SHOT_STEPS, "set": SET_STEPS}
    steps = step_map.get(entity_type, ASSET_STEPS)
    return {
        step: get_latest_publish_version(project_path, asset_name, step, entity_type) > 0
        for step in steps
    }
