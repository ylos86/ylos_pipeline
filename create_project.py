#!/usr/bin/env python3
"""
create_project.py - Project & asset creator, Ylos Prod pipeline (schema 2.0).

Single source of truth for the creation logic. Importable by DCC plugins
(Houdini/hython, Blender): no dependency outside the stdlib.

Applied principles:
  - Relocatable root: everything goes through $PROJ_ROOT (source) and $PROJ_CACHE (cache).
    No absolute path is stored in the manifests.
  - Cache / source separation: source on the external disk, regenerable cache on the internal one.
    The cache lives under $PROJ_CACHE/<project>, NEVER co-located with the source.
  - project.json = manifest, source of truth (schema_version 2.x, see project.schema.json).
  - ASSET-CENTRIC topology: assets/ is the backbone; sets/ and shots/ are optional
    scaffolding (created empty).
  - Single logic: this module is imported, never duplicated.
  - Production != pipeline: the manifest does NOT handle production tracking (client, deadlines).

CLI usage:
    python create_project.py project "my_project"
    python create_project.py project "my_project" --root /Volumes/EXT/3D --cache ~/cache --force
    python create_project.py asset  "/Volumes/EXT/3D/my_project" "Lina" --type CHARACTER
    python create_project.py asset  "<project>" "decor" --entity-type set --steps modeling,lookdev
    python create_project.py clean-staging "<project>"            # dry-run (report only)
    python create_project.py clean-staging "<project>" --apply    # removes orphans

Import usage (DCC plugin):
    import create_project
    info  = create_project.create("my_project")
    asset = create_project.create_asset(info["source"], "Lina", asset_type="CHARACTER")
    manifest = create_project.read_manifest(info["source"])
    create_project.validate_manifest(manifest)
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------------------
# Constants - contract
# --------------------------------------------------------------------------------------

SCHEMA_VERSION = "2.1.0"          # contract version (project.json AND asset manifest).
                                  # Bump on EVERY schema change (= migration).
                                  # 2.1.0: added 'frame_range' (shots) - additive, no
                                  # 2.0 manifest invalidated (see docs/migration-2.0-to-2.1.md).
MANIFEST_NAME = "project.json"
ASSET_MANIFEST_NAME = "manifest.json"
ASSET_ROOT_NAME = "asset_root.usda"   # USD composition of an asset/set (ASCII, see convention)
SHOT_ROOT_NAME = "shot_root.usda"     # USD composition of a shot (root prim /ROOT, timecodes)
PIPELINE_DIR = "_pipeline"        # config/manifest folder (renamed from _config in 2.0)
SPOTLIGHT_MARKER = ".metadata_never_index"
GITIGNORE_NAME = ".gitignore"

TOPOLOGY = "asset-centric"

# Environment variable names (never a hard-coded absolute path in DCC scenes)
ENV_ROOT = "PROJ_ROOT"            # SOURCE root  - external disk, permanent
ENV_CACHE = "PROJ_CACHE"          # CACHE root   - internal disk, regenerable

# Fallbacks if the env vars are not set (with a warning)
FALLBACK_ROOT = Path.home() / "Ylos" / "projects"
FALLBACK_CACHE = Path.home() / "Ylos" / "cache"

# --- Pipeline defaults (step taxonomy + USD assembly) ---------------------------------
DEFAULT_ASSET_STEPS = ["modeling", "rigging", "lookdev", "fx"]
DEFAULT_SHOT_STEPS  = ["animation", "fx", "lighting", "comp"]
DEFAULT_SET_STEPS   = ["layout", "lookdev", "lighting"]
USD_ROOT_PRIM = "/ROOT"           # root prim of ASSEMBLY stages (sets/shots).
                                  # Assets anchor under /<AssetName> (see usd-convention.md).

# --- Scene defaults (consumed by DCC plugins on open) ---------------------------------
DEFAULT_SCENE = {
    "fps": 24,
    "fps_base": 1.0,
    "unit_scale": 1.0,
    "color_management": "AgX",
    "renderer": "CYCLES",
    "resolution_x": 2048,
    "resolution_y": 1152,
    "color_space": "Linear Rec.709",
}

DEFAULT_DELIVERY = {"targets": ["usd", "exr"]}

# --- USD convention (see docs/usd-convention.md) --------------------------------------
USD_UP_AXIS = "Y"                 # USD exchange axis (Z<->Y conversion handled by the DCCs)
USD_METERS_PER_UNIT = 1.0         # aligned with scene.unit_scale

# Entity family -> parent folder in the source
ENTITY_DIR = {"asset": "assets", "set": "sets", "shot": "shots"}
_DEFAULT_STEPS = {"asset": DEFAULT_ASSET_STEPS, "set": DEFAULT_SET_STEPS, "shot": DEFAULT_SHOT_STEPS}
_STEPS_KEY = {"asset": "asset_steps", "set": "set_steps", "shot": "shot_steps"}

# --- LOP publish (Solaris) - complete asset version, outside the step taxonomy -----------
# A LOP publish (see HDA ylos::publish) is NOT a pipeline step (modeling/rigging/...):
# it's a complete snapshot of the LOP network (USD layer + thumb). Lives in its own
# reserved folder, never enters the subLayers composition of asset_root.usda.
ASSET_TYPES = ["CHARACTER", "PROP", "VEHICLE", "CREATURE", "FX_ELEMENT"]

# Sub-types per family, naming convention TYPE_Name_Variant (see validate_entity_name).
# SET_TYPES/SHOT_TYPES mirror app.html::FAMILY_CONFIG (the only source already decided for
# these two families - create_project.py was the only place that did not know them).
SET_TYPES = ["EXTERIOR", "INTERIOR", "HERO_SET", "MODULAR_KIT"]
SHOT_TYPES = ["LAYOUT", "ANIMATION", "FX", "LIGHTING", "COMP"]
_TYPES_BY_ENTITY = {"asset": ASSET_TYPES, "set": SET_TYPES, "shot": SHOT_TYPES}

# Production types (project.json["prod_type"], default of build_manifest()). BACKWARD-
# COMPATIBLE union of the values actually emitted, never invented nor removed (re-reading
# an existing project.json wins): app.html (FILM/SERIES/GAME/XR), Blender addon
# (FILM/AR/VR) and existing manifests (e.g. Pachamama = 'XR'). Only vocab source for
# prod_type - before, create_project did not know it and the Blender enum (FILM/AR/VR)
# CRASHED when reading an 'XR'/'SERIES'/'GAME' manifest (see plugins/blender/core/vocab.py,
# op_open_context). Implies no scene preset logic (that stays DCC-side and no-ops
# cleanly for a type it does not know).
PROD_TYPES = ["FILM", "SERIES", "GAME", "XR", "AR", "VR"]
# Pipeline target per prod type: decides the artifact FORMAT of the publish. ORCHESTRATOR
# decision, never the DCC's (principle 5): bridges consume the target, don't compute it.
# 'web' -> GLB (Three.js); 'offline' -> USD. Single source.
PROD_TYPE_TO_TARGET = {
    "XR": "web", "AR": "web", "VR": "web", "GAME": "web",
    "FILM": "offline", "SERIES": "offline",
}
DEFAULT_PIPELINE_TARGET = "offline"
LOP_DIR_NAME = "lop"
LOP_PUBLISH_DIR_NAME = "publish"
LOP_STAGING_DIR_NAME = ".staging"
LOP_THUMB_NAME = "thumb.png"
# Composable USD extensions (assembly layer). '.usdnc' = Apprentice watermark, never
# assumed in advance (see extensions gotcha, LOP HDA). A consumable cache or a GLB (see
# PUBLISH_ARTIFACT_EXTENSIONS) is NOT part of it: it passes the two-phase contract but
# NEVER enters the subLayers composition of asset_root/shot_root (see _is_usd_layer).
USD_LAYER_EXTENSIONS = (".usd", ".usdc", ".usda", ".usdnc")
# Artifact extensions accepted by the two-phase contract (_missing_artifacts): the USD
# layers + '.glb' (Blender/Three.js bridge) + consumable FX caches published as kind=step
# ('.vdb', '.bgeo.sc' double suffix, '.abc'). Those last four are NOT USD layers.
PUBLISH_ARTIFACT_EXTENSIONS = USD_LAYER_EXTENSIONS + (".glb", ".vdb", ".bgeo.sc", ".abc")
LOP_PUBLISHES_KEY = "lop_publishes"
# DCC publishes per step (Blender USD/GLB...), generalization of the LOP two-phase contract to
# any 'kind' != 'lop' (see allocate_publish_version). {step: [version-entry, ...]} - key
# distinct from 'publishes' (list of paths, written by the legacy publish_asset()) so the
# two entry forms are never mixed in the same list.
STEP_PUBLISHES_KEY = "step_publishes"
_DIR_VER_RE = re.compile(r"_v(\d+)$")

# Step strength order for the subLayers stack (strongest / downstream first).
# USD: the first sublayer in the list is the strongest.
DOWNSTREAM_ORDER = ["fx", "lookdev", "rigging", "uvs", "modeling",
                    "layout", "animation", "lighting", "render", "composite"]

# SHOT-specific strength order (distinct from DOWNSTREAM_ORDER, which is correct for an asset
# but wrong for a shot): on a shot lighting overrides animation, the opposite of an asset.
# 'comp' is declared for ordering but never produces a USD layer (2D) - simply never
# present in the publishes. Do NOT reuse DOWNSTREAM_ORDER here (see Increment 1 plan).
SHOT_DOWNSTREAM_ORDER = ["comp", "lighting", "fx", "animation", "layout"]

_VER_RE = re.compile(r"_v(\d+)\.")

# SOURCE tree (under $PROJ_ROOT/<project>) - permanent, versioned. Asset-centric.
SOURCE_TREE = [
    PIPELINE_DIR,                 # project.json (manifest)
    "assets",                     # BACKBONE (asset-centric)
    "sets",                       # assembly - optional (empty at scaffold)
    "shots",                      # shots - optional (empty at scaffold)
    "references/ai",              # AI references (Midjourney / NanoBanana) + metadata
    "references/photo",           # photo references
    "references/board",           # moodboards / boards
    "resources/hdri",             # reusable intra-project resources
    "resources/textures",
    "delivery",                   # masters / final outputs
    "edit",                       # editorial
]

# CACHE tree (under $PROJ_CACHE/<project>) - disposable, outside Git, internal NVMe.
CACHE_PER_PROJECT = True
CACHE_TREE = [
    "houdini",                    # Houdini caches (.bgeo.sc, sims, flip...)
    "blender",                    # Blender caches (bake, sims)
    "render",                     # regenerable renders / AOVs
    "alembic",                    # .abc caches
    "sim",                        # simulations
    "tmp",
]

GITIGNORE_CONTENT = """\
# --- Ylos Prod pipeline: regenerable / heavy, outside Git ---
# The cache lives under $PROJ_CACHE (outside the source tree): nothing to ignore here for that.

# Heavy renders / masters
delivery/**/render/
*.exr
*.ass

# Heavy binary USD geo: outside Git. The composition (.usda) is versioned, the geo (.usdc)
# is heavy/regenerated. Default to refine per project.
*.usdc

# DCC caches written into the source by mistake
*.bgeo.sc
*.sim

# DCC backups
*.hip.bak
*.hiplc.bak
*.blend1
*.blend2

# macOS
.DS_Store
"""


# --------------------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------------------

def _now():
    return datetime.now(timezone.utc).isoformat()


def _validate_segment(name):
    """A name = a single path segment, no edge whitespace, no separator."""
    if not name or "/" in name or "\\" in name or name.strip() != name:
        raise ValueError(f"Invalid name (single segment, no /): {name!r}")


def _resolve(explicit, env_name, fallback):
    """Resolve a root: explicit argument > env variable > fallback (with a warning)."""
    if explicit:
        return Path(explicit).expanduser().resolve()
    env_val = os.environ.get(env_name)
    if env_val:
        return Path(env_val).expanduser().resolve()
    sys.stderr.write(
        f"[warn] ${env_name} not set - falling back to {fallback}. "
        f"Set ${env_name} for a relocatable design.\n"
    )
    return fallback.expanduser().resolve()


def resolve_root(explicit=None):
    return _resolve(explicit, ENV_ROOT, FALLBACK_ROOT)


def resolve_cache(explicit=None):
    return _resolve(explicit, ENV_CACHE, FALLBACK_CACHE)


def _make_tree(base, tree):
    base.mkdir(parents=True, exist_ok=True)
    for rel in tree:
        (base / rel).mkdir(parents=True, exist_ok=True)


def entity_cache_dir(project_root, entity_name, step, label):
    """Scratch cache folder for a step: $PROJ_CACHE/<project>/houdini/<entity>/<step>/
    <label>/ (regenerable tier, see CLAUDE.md - 3-tier storage). SINGLE resolution
    logic (principle 5): the Houdini bridge sets on the filecache node the literal
    EXPRESSION '$PROJ_CACHE/...' (relocatable, see ylos_houdini.cache_dir_expression),
    while the resolved path (this function) lives here. Creates the parents (mkdir), returns
    the Path. No manifest trace: a scratch cache is disposable, its versioning is
    the filecache's native one (v1/v2...), not a two-phase contract."""
    _validate_segment(label)
    cache_dir = (resolve_cache() / Path(project_root).name / "houdini"
                 / entity_name / step / label)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _ver(path):
    """Extract the version number of a publish path (e.g. 'step/publish/A_step_v002.usdc' -> 2)."""
    m = _VER_RE.search(str(path))
    return int(m.group(1)) if m else 0


@contextlib.contextmanager
def acquire_lock(path):
    """Exclusive lock (fcntl.flock) for the duration of a read-modify-write critical section on
    'path' (typically a manifest.json, but generic - not specific to manifests).
    The lock lives in a '.lock' file next to 'path' (never on 'path' itself) so it
    never interferes with its read/write. Blocking: a second concurrent call
    waits for the release rather than risking a collision (e.g. version, corrupted manifest).

    SINGLE centralization point for fcntl.flock in this module (see CLAUDE.md: advisory,
    POSIX-only, unreliable on NFS/SMB - to be evolved here alone if the storage changes tier)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _atomic_write_text(path, content, encoding="utf-8"):
    """Write 'content' to 'path' atomically (tmp + os.replace, the same pattern
    finalize_publish_version() already uses for the staging->final rename). Protects against
    a truncated/corrupted file if the process crashes mid-write - acquire_lock()
    protects concurrency between processes, not a mid-write crash; the two are
    complementary."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding=encoding)
    os.replace(tmp, path)


def _atomic_write_json(path, data, indent=2):
    """Serialize 'data' to JSON and write it via _atomic_write_text (see its docstring)."""
    _atomic_write_text(path, json.dumps(data, indent=indent, ensure_ascii=False) + "\n")


# --------------------------------------------------------------------------------------
# Project manifest (project.json) - machine-readable contract
# --------------------------------------------------------------------------------------

def build_manifest(name, display_name=None, prod_type="FILM"):
    """Build the project manifest dict (schema 2.x). Stores NO absolute path: the
    project is relocatable, it resolves via $PROJ_ROOT / $PROJ_CACHE at runtime. A
    launcher / plugin reads this manifest and sets the env vars PER SESSION (which avoids the
    collision of a global env var between two DCCs open on two projects)."""
    now = _now()
    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "display_name": display_name or name,
        "prod_type": prod_type,
        # Pipeline target = artifact FORMAT of the publish (derived from prod_type, single source
        # PROD_TYPE_TO_TARGET). Written at creation; read tolerantly by get_pipeline_target.
        "pipeline_target": PROD_TYPE_TO_TARGET.get(prod_type, DEFAULT_PIPELINE_TARGET),
        "topology": TOPOLOGY,
        "created_utc": now,
        "modified_utc": now,
        # Which env vars this project expects
        "env": {"root": f"${ENV_ROOT}", "cache": f"${ENV_CACHE}"},
        # Trace of the created structure (audit / migration)
        "structure": {"source": list(SOURCE_TREE), "cache": list(CACHE_TREE)},
        "cache_per_project": CACHE_PER_PROJECT,
        # Step taxonomy + USD assembly
        "pipeline": {
            "asset_steps": list(DEFAULT_ASSET_STEPS),
            "shot_steps": list(DEFAULT_SHOT_STEPS),
            "set_steps": list(DEFAULT_SET_STEPS),
            "usd_root_prim": USD_ROOT_PRIM,
        },
        # Default scene settings (read by DCC plugins)
        "scene": dict(DEFAULT_SCENE),
        "delivery": dict(DEFAULT_DELIVERY),
        # Reserved for per-DCC settings (filled by the plugins)
        "dcc": {"houdini": {}, "blender": {}},
        # Minimal 'status'. The REAL production tracking (deadlines, client) lives elsewhere.
        "status": "created",
    }


def write_manifest(config_dir, manifest):
    path = config_dir / MANIFEST_NAME
    _atomic_write_json(path, manifest)
    return path


def read_manifest(project_dir):
    """Read project.json from <project>/_pipeline. Useful to plugins / launchers."""
    path = Path(project_dir) / PIPELINE_DIR / MANIFEST_NAME
    return json.loads(path.read_text(encoding="utf-8"))


def get_pipeline_target(project_root):
    """Pipeline target of a project: 'web' (GLB artifacts, Three.js) or 'offline' (USD).
    The artifact FORMAT is an ORCHESTRATOR decision (principle 5), never the DCC's: the
    bridges (Blender op_publish) consume this target, they don't compute it. TOLERANT
    read (never raises for a business case): 'pipeline_target' field of the manifest if it
    is valid, otherwise derived from prod_type (PROD_TYPE_TO_TARGET), default 'offline' -> a
    2.0 project without the field (or an unreadable manifest) degrades cleanly."""
    try:
        manifest = read_manifest(project_root)
    except (OSError, ValueError):
        return DEFAULT_PIPELINE_TARGET
    target = manifest.get("pipeline_target")
    if target in ("web", "offline"):
        return target
    return PROD_TYPE_TO_TARGET.get(manifest.get("prod_type"), DEFAULT_PIPELINE_TARGET)


def read_active_project(path=None):
    """Active project of the machine (Path) or None. Contract: ~/.ylos/active_project, one
    line, absolute path - written by the web UI (POST /api/set-project). SINGLE reader,
    shared by ylos_ui and the Houdini module (the HDA ylos::publish default_expression
    keeps its inline copy: an embedded parameter expression cannot depend on
    an import). Path.home() resolved at call time, not at import (the hython tests switch
    HOME mid-session, see test_publish_hda_e2e)."""
    if path is None:
        path = Path.home() / ".ylos" / "active_project"
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(text) if text else None


def validate_manifest(manifest):
    """Stdlib validation (no jsonschema dependency). Raises ValueError if invalid.
    Checks MAJOR schema version compatibility (otherwise: migration required)."""
    required = ("schema_version", "name", "created_utc", "env", "structure", "pipeline", "scene")
    missing = [k for k in required if k not in manifest]
    if missing:
        raise ValueError(f"project.json invalid - missing keys: {missing}")
    major = str(manifest["schema_version"]).split(".")[0]
    if major != SCHEMA_VERSION.split(".")[0]:
        raise ValueError(
            f"Schema incompatibility: project={manifest['schema_version']} "
            f"vs tool={SCHEMA_VERSION}. Migration required."
        )
    return True


# --------------------------------------------------------------------------------------
# Entity manifest (asset/set/shot) + USD stub
# --------------------------------------------------------------------------------------

def build_asset_manifest(name, entity_type, asset_type, steps):
    """Per-entity manifest (see asset.schema.json). 'entity_type' = family (asset/set/
    shot); 'type' = business sub-type (CHARACTER, ENVIRONMENT, PROP...)."""
    now = _now()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "entity_type": entity_type,
        "type": asset_type,
        "steps": list(steps),
        "publishes": {s: [] for s in steps},
        "created_utc": now,
        "modified_utc": now,
    }
    # A shot carries its frame range (schema 2.1). Default editable via set_frame_range().
    # Other families have no frame_range (key absent = no timecodes).
    if entity_type == "shot":
        manifest["frame_range"] = {
            "start": 1001, "end": 1100, "fps": DEFAULT_SCENE["fps"],
        }
    return manifest


def _meters_per_unit_str():
    mpu = USD_METERS_PER_UNIT
    return str(int(mpu)) if float(mpu).is_integer() else str(mpu)


def asset_root_usda(name):
    """USD assembly stub for an asset/set (see docs/usd-convention.md).
    defaultPrim = <EntityName>; steps stack as subLayers (filled at publish,
    strongest/downstream to weakest). Y-up, metersPerUnit aligned with scene."""
    return (
        "#usda 1.0\n"
        "(\n"
        f'    defaultPrim = "{name}"\n'
        f'    upAxis = "{USD_UP_AXIS}"\n'
        f"    metersPerUnit = {_meters_per_unit_str()}\n"
        "    # subLayers: strongest (downstream) to weakest. Filled at publish.\n"
        "    subLayers = [\n"
        "    ]\n"
        ")\n"
        "\n"
        f'def Xform "{name}"\n'
        "{\n"
        "}\n"
    )


def build_asset_root(name, latest):
    """Rebuild asset_root.usda from {step: relative_path_of_latest_publish}.
    subLayers in the stage header, downstream-strong-first order (see usd-convention.md)."""
    ordered = [s for s in DOWNSTREAM_ORDER if s in latest]
    ordered += [s for s in latest if s not in DOWNSTREAM_ORDER]
    lines = [
        "#usda 1.0",
        "(",
        f'    defaultPrim = "{name}"',
        f'    upAxis = "{USD_UP_AXIS}"',
        f"    metersPerUnit = {_meters_per_unit_str()}",
        "    subLayers = [",
    ]
    for s in ordered:
        lines.append(f"        @{latest[s]}@,")
    lines += [
        "    ]",
        ")",
        "",
        f'def Xform "{name}"',
        "{",
        "}",
    ]
    return "\n".join(lines) + "\n"


def _num_str(value):
    """Serialize a USD number without a spurious '.0' (24 rather than 24.0), float otherwise."""
    return str(int(value)) if float(value).is_integer() else str(value)


def build_shot_root(name, latest, frame_range=None):
    """Rebuild shot_root.usda from {step: relative_path_of_latest_publish}. Mirror of
    build_asset_root for a SHOT: root prim /ROOT (defaultPrim "ROOT", see USD_ROOT_PRIM),
    subLayers ordered by SHOT_DOWNSTREAM_ORDER (strongest/downstream first - lighting
    overrides anim). 'frame_range' ({start, end, fps}) present (schema 2.1) -> timecodes
    in the stage header; absent -> no timecodes (see docs/usd-convention.md)."""
    ordered = [s for s in SHOT_DOWNSTREAM_ORDER if s in latest]
    ordered += [s for s in latest if s not in SHOT_DOWNSTREAM_ORDER]
    prim = USD_ROOT_PRIM.lstrip("/")
    lines = [
        "#usda 1.0",
        "(",
        f'    defaultPrim = "{prim}"',
        f'    upAxis = "{USD_UP_AXIS}"',
        f"    metersPerUnit = {_meters_per_unit_str()}",
    ]
    if frame_range:
        lines += [
            f"    startTimeCode = {int(frame_range['start'])}",
            f"    endTimeCode = {int(frame_range['end'])}",
            f"    timeCodesPerSecond = {_num_str(frame_range['fps'])}",
        ]
    lines.append("    subLayers = [")
    for s in ordered:
        lines.append(f"        @{latest[s]}@,")
    lines += [
        "    ]",
        ")",
        "",
        f'def Xform "{prim}"',
        "{",
        "}",
    ]
    return "\n".join(lines) + "\n"


def _is_usd_layer(path):
    """True if 'path' points to a composable USD layer (USD extension, Apprentice watermark
    included). A consumable cache (.vdb/.bgeo.sc/.abc) or a GLB published as kind=step passes the
    two-phase contract but is NOT a USD layer: it never enters the subLayers
    composition (asset_root/shot_root) - explicit filter in _latest_by_step (Increment 5 plan).
    A sequence folder (artifact = folder name, no extension) is not USD either."""
    return str(path).endswith(USD_LAYER_EXTENSIONS)


def _latest_from_publishes(publishes):
    """Return {step: latest_path} from manifest.publishes (dict step -> [paths])."""
    return {step: max(paths, key=_ver) for step, paths in publishes.items() if paths}


def _latest_by_step(manifest):
    """{step: relative_path_of_latest_'complete'_publish} for composing a root,
    MERGING the two sources of a manifest:
    - legacy 'publishes' (dict step -> [paths], written by the deprecated publish_asset());
    - 'step_publishes' (two-phase contract, dict step -> [entries], key 'artifact',
      status 'complete').
    On an equal step, the two-phase contract wins (live source). LOP publishes
    (lop_publishes) are NEVER read here: a LOP is a complete snapshot outside the
    step taxonomy, it does not enter the subLayers composition."""
    latest = _latest_from_publishes(manifest.get("publishes", {}))
    for step, entries in manifest.get(STEP_PUBLISHES_KEY, {}).items():
        # Only USD layers enter composition: a consumable cache (.vdb/.bgeo.sc/
        # .abc) or a GLB published as kind=step is filtered BEFORE the max (a step with a newer
        # VDB but an older USD still composes its latest USD, not the VDB).
        complete = [e for e in entries
                    if e.get("status") == "complete" and e.get("artifact")
                    and _is_usd_layer(e["artifact"])]
        if complete:
            latest[step] = max(complete, key=lambda e: e["version"])["artifact"]
    return latest


def _compose_entity_root(entity_dir, manifest, entity_name):
    """Write the assembly root file from an ALREADY-loaded manifest - SINGLE composer
    (principle 5, CLAUDE.md). Called UNDER the manifest flock, by both entry points:
    refresh_entity_root() (public, takes the flock) and finalize_publish_version() (already
    in its flock). Returns the written Path. Does NOT take the flock itself (acquire_lock
    opens a new blocking fd on each call: re-locking here = deadlock)."""
    latest = _latest_by_step(manifest)
    name = manifest.get("name", entity_name)
    if manifest.get("entity_type") == "shot":
        content = build_shot_root(name, latest, manifest.get("frame_range"))
        root_path = entity_dir / SHOT_ROOT_NAME
    else:
        content = build_asset_root(name, latest)
        root_path = entity_dir / ASSET_ROOT_NAME
    _atomic_write_text(root_path, content)
    return root_path


def refresh_entity_root(project_root, entity_name):
    """Recompose an entity's assembly root file from its 'complete' publishes
    (latest per step) - public entry point, takes the manifest flock:
    - asset/set -> asset_root.usda (defaultPrim <Name>, DOWNSTREAM_ORDER order);
    - shot      -> shot_root.usda  (root prim /ROOT, SHOT_DOWNSTREAM_ORDER order, timecodes
                   from frame_range if present in the manifest).
    subLayers paths relative to the entity (the root lives at its root). Returns the written Path.
    finalize_publish_version() calls _compose_entity_root() directly (already under flock)."""
    entity_dir, manifest_path = _find_asset_entity(project_root, entity_name)
    with acquire_lock(manifest_path):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return _compose_entity_root(entity_dir, manifest, entity_name)


def set_frame_range(project_root, shot_name, start, end, fps=None):
    """Set / update a SHOT's frame range (schema 2.1) then recompose its
    shot_root.usda (timecodes). 'start' < 'end' required; the entity must be a shot. 'fps'
    None -> keeps the manifest's existing fps, otherwise the scene default. Atomic write
    under acquire_lock; recomposition is done AFTER releasing the flock (via
    refresh_entity_root, which takes its own flock again - acquire_lock is not reentrant,
    see CLAUDE.md). Returns the written frame_range."""
    start, end = int(start), int(end)
    if start >= end:
        raise ValueError(f"frame_range invalid: start ({start}) must be < end ({end})")
    entity_dir, manifest_path = _find_asset_entity(project_root, shot_name)
    with acquire_lock(manifest_path):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("entity_type") != "shot":
            raise ValueError(
                f"frame_range is reserved for shots: '{shot_name}' is of type "
                f"{manifest.get('entity_type')!r}."
            )
        if fps is None:
            fps = manifest.get("frame_range", {}).get("fps", DEFAULT_SCENE["fps"])
        frame_range = {"start": start, "end": end, "fps": fps}
        manifest["frame_range"] = frame_range
        manifest["modified_utc"] = _now()
        _atomic_write_json(manifest_path, manifest)
    refresh_entity_root(project_root, shot_name)
    return frame_range


# --------------------------------------------------------------------------------------
# Resolution of the file to OPEN for an entity+step (consumed by the DCC bridges)
# --------------------------------------------------------------------------------------

_WIP_VER_RE = re.compile(r"_v(\d+)\.blend$")


def _latest_wip(entity_dir, step):
    """Latest .blend WIP of a Blender step: entity_dir/<step>/wip/<name>_<step>_vNNN.blend
    (highest number). Returns (Path, version) or (None, 0). Never raises - an absent
    folder (unscaffolded step) simply returns (None, 0)."""
    wip_dir = Path(entity_dir) / step / "wip"
    if not wip_dir.is_dir():
        return None, 0
    best, best_ver = None, -1
    for f in wip_dir.iterdir():
        if not f.is_file() or f.suffix.lower() != ".blend":
            continue
        m = _WIP_VER_RE.search(f.name)
        if m and int(m.group(1)) > best_ver:
            best, best_ver = f, int(m.group(1))
    return best, (best_ver if best is not None else 0)


def _latest_step_publish_rel(manifest, step):
    """RELATIVE path (to the entity) of the step's latest 'complete' USD publish - two-phase
    contract (step_publishes[step], key 'artifact'). CORRECT resolution of the nested
    per-version folder (entity_dir/<step>/publish/<versioned_name>/<file>), where the Blender
    addon wrongly scanned FLAT files and thus never found a two-phase
    publish (see resolve_open_target, op_open_context fix). Filtered to composable USD layers
    (_is_usd_layer) - a consumable cache/GLB is not a file to open as a scene."""
    entries = manifest.get(STEP_PUBLISHES_KEY, {}).get(step, [])
    complete = [e for e in entries
                if e.get("status") == "complete" and e.get("artifact")
                and _is_usd_layer(e["artifact"])]
    if not complete:
        return None
    return max(complete, key=lambda e: e.get("version", 0))["artifact"]


# USD extensions recognized as a legacy flat publish (the deprecated publish_asset() wrote
# <step>/publish/<name>_<step>_vNNN.<ext>). '.usdz' included (deliverable); the others = composable
# layers. A two-phase publish is a FOLDER, never a file -> never confused.
_LEGACY_PUBLISH_EXTS = USD_LAYER_EXTENSIONS + (".usdz",)


def list_publishes(project_root, entity_name, step, entity_type="asset"):
    """Public READ API for a step's publishes (the logic lives in the orchestrator,
    the DCC/UI consumers are thin - principle 5). NEVER raises for a business case
    (entity/step not found): returns []. Merges two sources, with no version duplicate:

    - **manifest-first**: two-phase contract (manifest['step_publishes'][step]). Each
      entry is returned as-is (copy) + enriched with 'abs_path' (absolute path of
      'artifact', or None) and 'exists' (bool). 'legacy'=False.
    - **legacy flat-file fallback**: disk scan of <entity>/<step>/publish/ for the
      versioned FILES (pattern '_vNNN.<ext>' USD, see _LEGACY_PUBLISH_EXTS - a two-phase
      folder has `f.is_file()` False, never caught). Entries {version, status:'complete',
      artifact (rel), abs_path, exists:True, legacy:True}. A number already present on the
      two-phase side is NOT overwritten (the live contract wins).

    Result sorted by ascending version."""
    project_root = Path(project_root)
    try:
        entity_dir, manifest_path = _find_asset_entity(project_root, entity_name)
    except FileNotFoundError:
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}

    by_version = {}  # version -> enriched entry

    for e in manifest.get(STEP_PUBLISHES_KEY, {}).get(step, []):
        ver = e.get("version")
        if ver is None:
            continue
        entry = dict(e)
        artifact = e.get("artifact")
        abs_path = (entity_dir / artifact) if artifact else None
        entry["abs_path"] = str(abs_path) if abs_path is not None else None
        entry["exists"] = bool(abs_path is not None and abs_path.exists())
        entry["legacy"] = False
        by_version[ver] = entry

    pub_dir = entity_dir / step / "publish"
    if pub_dir.is_dir():
        for f in sorted(pub_dir.iterdir()):
            if not f.is_file() or f.suffix.lower() not in _LEGACY_PUBLISH_EXTS:
                continue
            m = _VER_RE.search(f.name)
            if not m:
                continue
            ver = int(m.group(1))
            if ver in by_version:
                continue  # the two-phase side wins on an equal version
            by_version[ver] = {
                "version": ver,
                "status": "complete",
                "artifact": f"{step}/{LOP_PUBLISH_DIR_NAME}/{f.name}",
                "abs_path": str(f),
                "exists": True,
                "legacy": True,
            }

    return [by_version[v] for v in sorted(by_version)]


# ---------------------------------------------------------------------------
# Entity thumbnail resolution - SINGLE POINT (principle 5).
#
# Lives here, in the orchestrator, and not in a consumer: the web UI (ylos_ui) AND the
# Blender panel AND a future Houdini/n8n panel have exactly the same need. Writing the
# cascade in the HTTP server would have made it invisible to the DCCs, which would have
# reimplemented it - this is exactly the drift pattern that principle 5 forbids (same
# reason as refresh_entity_root or resolve_entity).
# ---------------------------------------------------------------------------

THUMB_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
ENTITY_PREVIEW_NAME = "preview.png"

# '<stem>_v<NNN>_thumb.<ext>' - convention set by the Blender WIP save
# (plugins/blender/core/thumbnails.py::get_thumb_path).
_WIP_THUMB_RE = re.compile(r"_v(\d{3})_thumb\.(?:png|jpg|jpeg|webp)$", re.IGNORECASE)


def _latest_publish_thumb_rel(manifest):
    """Thumb of the max-version 'complete' publish, across all steps (two-phase contract).
    Path relative to the entity, or None."""
    best = None  # (version, rel)
    for entries in (manifest.get("step_publishes") or {}).values():
        for e in entries:
            if e.get("status") != "complete":
                continue
            rel = e.get("thumbnail") or e.get("thumb")
            if not rel:
                continue
            if best is None or e.get("version", 0) > best[0]:
                best = (e.get("version", 0), rel)
    return best[1] if best else None


def _latest_wip_thumb_rel(entity_dir):
    """Thumbnail of the most recent WIP, across all steps ('<step>/wip/<stem>_v<NNN>_thumb.png',
    written by ylos.save_wip). Sorted by (mtime, version): the last-saved WIP best represents
    the current state, version breaks ties on an equal mtime. Relative to the entity, or None."""
    best = None  # (mtime, version, rel)
    try:
        step_dirs = sorted(d for d in entity_dir.iterdir() if d.is_dir())
    except OSError:
        return None
    for step_dir in step_dirs:
        wip = step_dir / "wip"
        if not wip.is_dir():
            continue
        try:
            files = list(wip.iterdir())
        except OSError:
            continue
        for f in files:
            if not f.is_file() or f.suffix.lower() not in THUMB_EXTENSIONS:
                continue
            m = _WIP_THUMB_RE.search(f.name)
            if not m:
                continue
            try:
                key = (f.stat().st_mtime, int(m.group(1)))
            except OSError:
                continue
            if best is None or key > best[:2]:
                best = (key[0], key[1], f"{step_dir.name}/wip/{f.name}")
    return best[2] if best else None


def resolve_entity_thumbnail(project_root, entity_name):
    """Representative thumbnail of an entity (asset/set/shot), for ANY UI consumer.

    Returns {"rel": <path relative to the entity or None>, "path": <absolute path or None>,
    "source": "custom"|"publish"|"legacy"|"wip"|"none"}. NEVER raises for a business case
    (same convention as resolve_entity / pin_web_asset): entity absent -> source 'none'.

    Cascade, from most intentional to most automatic:
      1. custom  - '<entity>/preview.png', explicit human override (Prism
                   'set preview' pattern). Always wins: a human gesture beats a heuristic.
      2. publish - latest 'complete' publish of the two-phase contract (no disk scan, the
                   manifest already carries the path).
      3. legacy  - flat scan of '<step>/publish/*.png' (pre-two-phase projects).
      4. wip     - latest WIP thumbnail. Fills the main UX gap: between the CREATION
                   of an entity and its first successful publish, no publish thumb
                   exists - the entity therefore showed as a gray placeholder while a
                   preview of its WIP already existed on disk. This is the most frequent state
                   for an artist: what they just created is not published yet.

    The SOURCE is part of the contract: a WIP thumb does not carry the same confidence as a
    publish, a consumer must be able to flag it rather than let it pass for a
    published one."""
    none = {"rel": None, "path": None, "source": "none"}
    try:
        entity_dir, manifest_path = _find_asset_entity(Path(project_root), entity_name)
    except (FileNotFoundError, ValueError, OSError):
        return none
    if entity_dir is None:
        return none

    def _hit(rel, source):
        return {"rel": rel, "path": str(entity_dir / rel), "source": source}

    if (entity_dir / ENTITY_PREVIEW_NAME).is_file():
        return _hit(ENTITY_PREVIEW_NAME, "custom")

    manifest = {}
    if manifest_path is not None and Path(manifest_path).is_file():
        try:
            manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            manifest = {}

    rel = _latest_publish_thumb_rel(manifest)
    if rel and (entity_dir / rel).is_file():
        return _hit(rel, "publish")

    try:
        step_dirs = sorted(d for d in entity_dir.iterdir() if d.is_dir())
    except OSError:
        step_dirs = []
    for step_dir in step_dirs:
        pub = step_dir / "publish"
        if not pub.is_dir():
            continue
        try:
            flat = sorted(f for f in pub.iterdir()
                          if f.is_file() and f.suffix.lower() in THUMB_EXTENSIONS)
        except OSError:
            continue
        if flat:
            return _hit(f"{step_dir.name}/publish/{flat[0].name}", "legacy")

    rel = _latest_wip_thumb_rel(entity_dir)
    if rel:
        return _hit(rel, "wip")
    return none


def latest_publish_artifact(project_root, entity_name, step, entity_type="asset"):
    """Max-version 'complete' publish entry for the step (two-phase + legacy merged,
    see list_publishes), enriched with 'abs_path'/'exists'/'legacy'. dict or None (no
    'complete' publish). Never raises for a business case. Disk-aware generalization of
    _latest_step_publish_rel() (which operates on an already-in-memory manifest and filters
    to USD layers only for composition/opening)."""
    complete = [e for e in list_publishes(project_root, entity_name, step, entity_type)
                if e.get("status") == "complete"]
    if not complete:
        return None
    return max(complete, key=lambda e: e.get("version", 0))


def resolve_open_target(entity_name, dcc="blender", step=None, project_root=None):
    """Resolve WHICH file a DCC must open for an entity+step. The logic lives in
    the orchestrator (principle 5): reusable by Blender AND Houdini, the addon only
    consumes. NEVER raises for a business case (project/entity/step not found, unknown
    enum value read from the manifest, no candidate file): returns a dict exists=False
    with 'reason'. The only possible exceptions would be programming bugs.

    Parameters:
      entity_name  : entity name (asset/set/shot) - located via _find_asset_entity.
      dcc          : target DCC ('blender' by default). Only 'blender' resolves .blend WIPs.
      step         : targeted step; None -> first step declared in the manifest (fallback).
      project_root : project root; None -> active project (read_active_project(), contract
                     ~/.ylos/active_project).

    Resolution order (dcc='blender'):
      1. latest .blend WIP of the step                                 -> kind='wip'
      2. entity's default scene = its assembly root
         (shot_root.usda / asset_root.usda, which already references the latest
         publishes as subLayers). No per-step .blend template at
         scaffold: the composed root is the default scene to open.         -> kind='scene_default'
      3. latest 'complete' USD publish of the step (correct nested path) -> kind='publish'
      4. explicit failure                                               -> exists=False

    Return: {"path": str|None, "kind": "wip"|"scene_default"|"publish"|None,
              "step": str|None, "exists": bool, "reason": str (present if exists=False)}."""
    if project_root is None:
        project_root = read_active_project()
    if project_root is None:
        return {"path": None, "kind": None, "step": step, "exists": False,
                "reason": "no active project (project_root=None and ~/.ylos/active_project missing)"}

    project_root = Path(project_root)
    try:
        entity_dir, manifest_path = _find_asset_entity(project_root, entity_name)
    except FileNotFoundError as exc:
        return {"path": None, "kind": None, "step": step, "exists": False, "reason": str(exc)}

    # Manifest read TOLERANTLY: an unknown enum value (legacy prod_type/type,
    # e.g. 'XR', 'ZZ_UNKNOWN') must never raise - we validate nothing, we resolve
    # paths. An unreadable manifest degrades cleanly (empty dict).
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    entity_type = manifest.get("entity_type", "asset")
    steps = manifest.get("steps", [])
    if step is None:
        step = steps[0] if steps else None
    # step may remain None (manifest without steps / corrupted): the WIP and publish
    # branches are step-dependent and therefore skipped, but the default scene (assembly
    # root, step-agnostic) stays resolvable -> clean degradation, never an exception.

    # 1. latest WIP (Blender only, step required)
    if dcc == "blender" and step:
        wip, _wver = _latest_wip(entity_dir, step)
        if wip is not None:
            return {"path": str(wip), "kind": "wip", "step": step, "exists": True}

    # 2. default scene = entity's assembly root (step-agnostic)
    root_name = SHOT_ROOT_NAME if entity_type == "shot" else ASSET_ROOT_NAME
    default_path = entity_dir / root_name
    if default_path.is_file():
        return {"path": str(default_path), "kind": "scene_default", "step": step, "exists": True}

    # 3. latest USD publish of the step (correct nested path, never a flat-file scan)
    if step:
        rel = _latest_step_publish_rel(manifest, step)
        if rel is not None:
            pub = entity_dir / rel
            if pub.is_file():
                return {"path": str(pub), "kind": "publish", "step": step, "exists": True}

    # 4. explicit failure (business case, not an exception)
    return {"path": None, "kind": None, "step": step, "exists": False,
            "reason": (f"no WIP for step {step!r}, no default scene ({root_name}), "
                       f"no USD publish for '{entity_name}'")}


def _project_steps(project_dir, entity_type):
    """Default steps for this family: pipeline from the project manifest if readable,
    otherwise the module defaults."""
    key = _STEPS_KEY[entity_type]
    try:
        steps = read_manifest(project_dir).get("pipeline", {}).get(key)
        if steps:
            return list(steps)
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        pass
    return list(_DEFAULT_STEPS[entity_type])


# --------------------------------------------------------------------------------------
# Scene Creator (Phase 1) - starter spec
# --------------------------------------------------------------------------------------
# Per-department starter rules (plan-usable-v1 Phase 1.2). Single source of truth for
# WHAT a fresh authoring scene contains; the DCC only REALIZES the returned spec. Steps
# are the orchestrator's own vocabulary (DEFAULT_*_STEPS); adding a step here is the only
# edit needed to give it a starter behaviour.
#
# reference the entity's built assembly (shot_root/asset_root USD) for the steps that
# CONSUME the model rather than author it (modeling/rigging build it, so no reference):
_STARTER_ASSEMBLY_STEPS = {
    "shot":  {"layout", "animation", "lighting", "fx", "comp"},
    "set":   {"lookdev", "surfacing", "lighting"},
    "asset": {"lookdev", "surfacing", "lighting"},
}
# a camera is created when absent for the steps that lay a shot out / animate it:
_STARTER_CAMERA_STEPS = {"layout", "animation"}
# a minimal lighting/turntable hint for look steps:
_STARTER_LIGHTING_STEPS = {"lookdev", "surfacing", "lighting"}


def scene_starter_spec(entity_name, step, dcc="blender", project_root=None):
    """PURE, serializable description of a NEW authoring scene for entity+step in a DCC
    (plan-usable-v1 Phase 1). The orchestrator decides WHAT the starter contains; the DCC
    (op_create_scene in Blender, ylos_houdini in Houdini) REALIZES it. Never returns bpy/hou.

    NEVER raises for a business case: returns {"ok": False, "reason": ...} for an unknown
    entity, an unreadable manifest, or a step not declared for the entity.

    On success returns {"ok": True, ...} with:
      entity, family ('asset'|'set'|'shot'), entity_type (sub-type), step, dcc
      wip:        {"dir", "version", "stem"[, "filename", "path" for blender]} - the next
                  free WIP version (v001 if none): a starter is a versioned, non-destructive
                  authoring file, never an overwrite.
      prod_type:  read from project.json, for the DCC's scene preset (apply_scene_preset).
      frame_range:{"start","end","fps"}|None - shots only (schema 2.1 manifest).
      references: [{"path","rel","kind":"usd","role":"assembly","mode":"import"}] - the
                  built assembly to pull in (empty until it exists on disk).
      camera:     bool - create one if the scene has none (layout/animation).
      lighting:   bool - minimal light/turntable hint (look steps).
      context:    scene-context props for the DCC to stamp (project/entity/step/type).
    """
    if project_root is None:
        return {"ok": False, "reason": "project_root is required"}
    resolved = resolve_entity(project_root, entity_name)
    if resolved is None:
        return {"ok": False, "reason": f"entity {entity_name!r} not found (no manifest.json)"}

    family      = resolved["family"]          # 'asset' | 'set' | 'shot'
    entity_type = resolved["entity_type"]     # sub-type (CHARACTER/PROP/...)
    entity_dir  = Path(resolved["dir"])
    manifest    = resolved["manifest"]

    declared = manifest.get("steps") or []
    if declared and step not in declared:
        return {"ok": False,
                "reason": (f"step {step!r} not declared for {entity_name!r} "
                           f"(manifest steps: {', '.join(declared)})")}

    # Next free WIP version (never overwrite an existing authoring file).
    _wip, latest_ver = _latest_wip(entity_dir, step)
    version = latest_ver + 1
    stem = f"{entity_name}_{step}_v{version:03d}"
    wip = {"dir": str(entity_dir / step / "wip"), "version": version, "stem": stem}
    if dcc == "blender":
        wip["filename"] = stem + ".blend"
        wip["path"] = str(entity_dir / step / "wip" / (stem + ".blend"))

    # Prod type (scene preset key) - tolerant read, never fatal.
    try:
        prod_type = read_manifest(project_root).get("prod_type", "FILM")
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        prod_type = "FILM"

    # Assembly reference: the built root USD of the entity, for steps that consume it.
    references = []
    if step in _STARTER_ASSEMBLY_STEPS.get(family, set()):
        root_name = SHOT_ROOT_NAME if family == "shot" else ASSET_ROOT_NAME
        root_path = entity_dir / root_name
        if root_path.is_file():
            references.append({
                "path": str(root_path), "rel": root_name, "kind": "usd",
                "role": "assembly", "mode": "import",
            })

    frame_range = manifest.get("frame_range") if family == "shot" else None

    return {
        "ok": True,
        "entity": entity_name,
        "family": family,
        "entity_type": entity_type,
        "step": step,
        "dcc": dcc,
        "wip": wip,
        "prod_type": prod_type,
        "frame_range": frame_range,
        "references": references,
        "camera": step in _STARTER_CAMERA_STEPS and family == "shot",
        "lighting": step in _STARTER_LIGHTING_STEPS,
        "context": {
            "project_root": str(project_root),
            "entity": entity_name,
            "step": step,
            "context_type": family.upper(),   # ASSET | SET | SHOT
            "asset_type": entity_type,        # sub-type (CHARACTER/PROP/...)
            "prod_type": prod_type,
        },
    }


# --------------------------------------------------------------------------------------
# Playblast (review media) - versioned output spec
# --------------------------------------------------------------------------------------
# Output is a PNG image SEQUENCE in a per-version folder rather than a movie: PNG is
# universally available (some Blender builds ship without the FFMPEG encoder - observed on
# macOS 5.2), the naming stays clean, and it mirrors the pipeline's existing sequence-folder
# convention. A movie container can be layered on later, gated on ffmpeg availability.
_PLAYBLAST_DIRNAME = "playblast"
_PLAYBLAST_VER_RE = re.compile(r"_v(\d+)(?:\.|$)")


def playblast_spec(entity_name, step, project_root=None):
    """PURE, serializable spec for a review PLAYBLAST of entity+step (a viewport capture
    over the frame range). Same split as scene_starter_spec: the orchestrator ALLOCATES the
    versioned output - in the SOURCE tree (<entity>/<step>/playblast/<stem>/), so the web
    Project Browser can serve it - and the DCC RENDERS the frames. NEVER raises for a
    business case: {"ok": False, "reason": ...} for an unknown entity or an undeclared step.

    On success: {"ok": True, entity, step, family, dir, version, stem, sequence:True,
    ext:"png", path (the per-version sequence FOLDER), frame_prefix (frames are
    <frame_prefix>.####.png inside path), frame_range}. version is the next free one (a
    playblast never overwrites); frame_range comes from the shot manifest (schema 2.1) or is
    None (the DCC then uses its scene range).
    """
    if project_root is None:
        return {"ok": False, "reason": "project_root is required"}
    resolved = resolve_entity(project_root, entity_name)
    if resolved is None:
        return {"ok": False, "reason": f"entity {entity_name!r} not found (no manifest.json)"}
    declared = resolved["manifest"].get("steps") or []
    if declared and step not in declared:
        return {"ok": False,
                "reason": (f"step {step!r} not declared for {entity_name!r} "
                           f"(manifest steps: {', '.join(declared)})")}

    pdir = Path(resolved["dir"]) / step / _PLAYBLAST_DIRNAME

    best = 0
    if pdir.is_dir():
        for f in pdir.iterdir():
            m = _PLAYBLAST_VER_RE.search(f.name)
            if m:
                best = max(best, int(m.group(1)))
    version = best + 1
    stem = f"{entity_name}_{step}_v{version:03d}"
    frame_range = resolved["manifest"].get("frame_range") if resolved["family"] == "shot" else None

    return {
        "ok": True,
        "entity": entity_name,
        "step": step,
        "family": resolved["family"],
        "dir": str(pdir),
        "version": version,
        "stem": stem,
        "sequence": True,
        "ext": "png",
        "path": str(pdir / stem),          # per-version sequence folder
        "frame_prefix": stem,              # frames: <stem>.####.png inside path
        "frame_range": frame_range,
    }


# --------------------------------------------------------------------------------------
# Render (regenerable cache tier) - versioned output spec
# --------------------------------------------------------------------------------------
# A render lives in the CACHE tier, exactly where Houdini writes (ylos_houdini.render_dir):
# $PROJ_CACHE/<project>/render/<entity>/<step>/v<NNN>/. Single convention, so a Blender and
# a Houdini render of the same shot/step land side by side and version together. No manifest:
# a render is regenerable and its tracking is production management, not the technical
# pipeline (principle 4) - delivery is an explicit, human-validated copy (Houdini
# deliver_render), never automatic.
_RENDER_SUBDIR = "render"
_RENDER_VER_RE = re.compile(r"^v(\d{3,})$")


def render_spec(entity_name, step, project_root=None, ext="exr"):
    """PURE, serializable spec for a versioned RENDER of entity+step, in the cache render
    tier ($PROJ_CACHE/<project>/render/<entity>/<step>/v<NNN>/). Same split as the other
    specs: the orchestrator ALLOCATES the next-free version + output path; the DCC RENDERS
    into it (real engine, unlike the WORKBENCH playblast). NEVER raises for a business case:
    {"ok": False, "reason": ...} for an unknown entity or an undeclared step.

    On success: {"ok": True, entity, step, family, dir, version_dir, version, stem, ext,
    output_prefix (frames land as <stem>.####.<ext> - filepath prefix for the DCC),
    frame_range}. $PROJ_CACHE is resolved via resolve_cache() (same source as the Houdini
    tier); set the env in tests as production does.
    """
    if project_root is None:
        return {"ok": False, "reason": "project_root is required"}
    resolved = resolve_entity(project_root, entity_name)
    if resolved is None:
        return {"ok": False, "reason": f"entity {entity_name!r} not found (no manifest.json)"}
    declared = resolved["manifest"].get("steps") or []
    if declared and step not in declared:
        return {"ok": False,
                "reason": (f"step {step!r} not declared for {entity_name!r} "
                           f"(manifest steps: {', '.join(declared)})")}

    rdir = resolve_cache() / Path(project_root).name / _RENDER_SUBDIR / entity_name / step
    best = 0
    if rdir.is_dir():
        for d in rdir.iterdir():
            if d.is_dir():
                m = _RENDER_VER_RE.match(d.name)
                if m:
                    best = max(best, int(m.group(1)))
    version = best + 1
    vdir = rdir / f"v{version:03d}"
    stem = f"{entity_name}_{step}_v{version:03d}"
    frame_range = resolved["manifest"].get("frame_range") if resolved["family"] == "shot" else None

    return {
        "ok": True,
        "entity": entity_name,
        "step": step,
        "family": resolved["family"],
        "dir": str(rdir),
        "version_dir": str(vdir),
        "version": version,
        "stem": stem,
        "ext": ext,
        "output_prefix": str(vdir / (stem + ".")),   # frames: <stem>.####.<ext>
        "frame_range": frame_range,
    }


# --------------------------------------------------------------------------------------
# Creation - project
# --------------------------------------------------------------------------------------

def create(name, root=None, cache=None, force=False, prod_type="FILM", display_name=None):
    """Create a complete project (asset-centric shell). Returns {name, source, cache,
    manifest}. Non-destructive: 'force' only lifts the existence guard, it never
    deletes anything (folders are created with exist_ok)."""
    _validate_segment(name)

    root_dir = resolve_root(root)
    cache_root = resolve_cache(cache)

    source = root_dir / name
    cache_dir = (cache_root / name) if CACHE_PER_PROJECT else cache_root

    if source.exists() and not force:
        raise FileExistsError(
            f"Project already exists: {source} (pass force=True to force)"
        )

    # 1. source tree (external, permanent)
    _make_tree(source, SOURCE_TREE)
    # 2. cache tree (separate tier, internal disk)
    _make_tree(cache_dir, CACHE_TREE)

    config_dir = source / PIPELINE_DIR   # created by SOURCE_TREE

    # 3. manifest (source of truth)
    manifest = build_manifest(name, display_name=display_name, prod_type=prod_type)
    validate_manifest(manifest)
    manifest_path = write_manifest(config_dir, manifest)

    # 4. Spotlight anti-indexing marker (on the heavy source)
    (source / SPOTLIGHT_MARKER).touch()

    # 5. .gitignore (cache + renders + heavy geo outside Git)
    (source / GITIGNORE_NAME).write_text(GITIGNORE_CONTENT, encoding="utf-8")

    return {
        "name": name,
        "source": str(source),
        "cache": str(cache_dir),
        "manifest": str(manifest_path),
    }


# --------------------------------------------------------------------------------------
# Creation - entity (asset / set / shot)
# --------------------------------------------------------------------------------------

def create_asset(project_dir, name, entity_type="asset", asset_type="OTHER",
                 steps=None, force=False):
    """Scaffold an entity in an existing project. Creates <family>/<name>/ with one folder
    per step (+ wip/ + publish/), a manifest.json and, for asset/set, an asset_root.usda stub.
    Returns {name, entity_type, path, manifest, asset_root}. Non-destructive."""
    project_dir = Path(project_dir)
    if entity_type not in ENTITY_DIR:
        raise ValueError(f"invalid entity_type: {entity_type!r} (asset|set|shot)")
    _validate_segment(name)
    # Name validation at creation - single point (see validate_entity_name): covers
    # web UI, Blender, CLI, future. _validate_segment protects the path, this protects the
    # TYPE_Name_Variant business convention.
    validate_entity_name(name, entity_type, asset_type)

    if steps is None:
        steps = _project_steps(project_dir, entity_type)

    entity_dir = project_dir / ENTITY_DIR[entity_type] / name
    if entity_dir.exists() and not force:
        raise FileExistsError(
            f"Entity already exists: {entity_dir} (pass force=True to force)"
        )

    # 1. step folders generated from the declared steps: wip/ (DCC work) +
    #    publish/ (versioned USD outputs), like the real workflow.
    entity_dir.mkdir(parents=True, exist_ok=True)
    for step in steps:
        (entity_dir / step / "wip").mkdir(parents=True, exist_ok=True)
        (entity_dir / step / "publish").mkdir(parents=True, exist_ok=True)

    # 2. entity manifest
    manifest = build_asset_manifest(name, entity_type, asset_type, steps)
    manifest_path = entity_dir / ASSET_MANIFEST_NAME
    _atomic_write_json(manifest_path, manifest)

    # 3. USD assembly stub (asset/set; a shot composes differently)
    asset_root_path = None
    if entity_type in ("asset", "set"):
        asset_root_path = entity_dir / ASSET_ROOT_NAME
        _atomic_write_text(asset_root_path, asset_root_usda(name))

    return {
        "name": name,
        "entity_type": entity_type,
        "path": str(entity_dir),
        "manifest": str(manifest_path),
        "asset_root": str(asset_root_path) if asset_root_path else None,
    }


# --------------------------------------------------------------------------------------
# Publish - version a file into an entity step
# --------------------------------------------------------------------------------------

def publish_asset(project_root, asset_name, step, source_file):
    """DEPRECATED - publishes source_file into <asset>/<step>/publish/ with automatic
    versioning, direct write (no staging, no thumbnail required).

    Replaced by the two-phase contract allocate_publish_version()/finalize_publish_version()
    (kind=<step>), adopted by all DCC bridges (Houdini LOP, Blender USD/GLB) - guarantees
    a thumbnail and an atomic commit via staging_dir. Kept for compatibility
    (no caller remaining in this repo since the Blender migration), do not use for
    new code.

    - Scans manifest.publishes[step] to determine the next version (v001, v002...).
    - Copies source_file -> <step>/publish/<asset>_<step>_v<NNN><ext> (never overwrites).
    - Updates manifest.json (publishes[step] and modified_utc).
    - Rebuilds asset_root.usda (subLayers) for asset/set entities.

    Returns {name, step, version, publish_path, manifest, asset_root}.
    Non-destructive: raises FileExistsError if the target version already exists.
    """
    import warnings
    warnings.warn(
        "publish_asset() is deprecated - use allocate_publish_version()/"
        "finalize_publish_version() (kind=<step>), the two-phase contract with a required "
        "thumbnail adopted by all DCC bridges.",
        DeprecationWarning,
        stacklevel=2,
    )
    project_root = Path(project_root)
    source_file = Path(source_file)

    if not source_file.is_file():
        raise FileNotFoundError(f"Source file not found: {source_file}")

    # Locate the entity in assets/ sets/ shots/
    entity_dir = None
    for family in ("assets", "sets", "shots"):
        candidate = project_root / family / asset_name
        if candidate.is_dir() and (candidate / ASSET_MANIFEST_NAME).is_file():
            entity_dir = candidate
            break
    if entity_dir is None:
        raise FileNotFoundError(
            f"Entity '{asset_name}' not found in {project_root} (assets/, sets/, shots/)."
        )

    manifest_path = entity_dir / ASSET_MANIFEST_NAME

    # Critical section: read manifest -> allocate version -> copy -> write
    # manifest -> rebuild asset_root.usda. Locked end-to-end (fcntl.flock)
    # so a second concurrent publish can never read a stale 'publishes' and
    # collide on the same version number (see acquire_lock).
    with acquire_lock(manifest_path):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        valid_steps = manifest.get("steps", [])
        if step not in valid_steps:
            raise ValueError(
                f"Step '{step}' invalid for '{asset_name}' (declared steps: {valid_steps})."
            )

        # Next version number
        existing = manifest.get("publishes", {}).get(step, [])
        next_ver = max((_ver(p) for p in existing), default=0) + 1

        # Versioned target path
        ext = source_file.suffix
        versioned_name = f"{asset_name}_{step}_v{next_ver:03d}{ext}"
        publish_dir = entity_dir / step / "publish"
        publish_dir.mkdir(parents=True, exist_ok=True)
        target = publish_dir / versioned_name

        if target.exists():
            raise FileExistsError(
                f"Version already present, not overwritten: {target}"
            )

        shutil.copy2(source_file, target)

        # Update manifest.json
        publishes = manifest.setdefault("publishes", {})
        publishes.setdefault(step, [])
        publishes[step].append(f"{step}/publish/{versioned_name}")
        manifest["modified_utc"] = _now()
        _atomic_write_json(manifest_path, manifest)

        # Rebuild asset_root.usda (asset/set only)
        asset_root_path = None
        entity_type = manifest.get("entity_type", "asset")
        if entity_type in ("asset", "set"):
            content = build_asset_root(manifest.get("name", asset_name),
                                       _latest_from_publishes(publishes))
            asset_root_path = entity_dir / ASSET_ROOT_NAME
            _atomic_write_text(asset_root_path, content)

    return {
        "name": asset_name,
        "step": step,
        "version": next_ver,
        "publish_path": str(target),
        "manifest": str(manifest_path),
        "asset_root": str(asset_root_path) if asset_root_path else None,
    }


# --------------------------------------------------------------------------------------
# LOP publish (Solaris) - complete asset version (USD layer + thumb), staging + replace
# --------------------------------------------------------------------------------------

def _suggested_entity_name(name, sub_type):
    """Suggest a compliant name from an invalid raw name: capitalizes, strips a
    possible existing (malformed) prefix, 'Default' variant by default."""
    base = name.split("_")[-1] if "_" in name else name
    base = base[:1].upper() + base[1:] if base else base
    return f"{sub_type}_{base}_Default"


def validate_entity_name(name, entity_type, sub_type):
    """Validate 'name' against the TYPE_Name_Variant convention (TYPE = sub_type, restricted to
    the valid list for 'entity_type' - asset/set/shot, see _TYPES_BY_ENTITY). Match by
    exact prefix (not a naive split('_')) because some types already contain an
    underscore (FX_ELEMENT): 'FX_ELEMENT_Drone_Default' has 4 '_' segments, not 3.

    Single name-validation point, called by create_asset() at creation (covers web
    UI, Blender, CLI, future) - and by allocate_publish_version() at LOP publish (unchanged
    historical contract, see validate_publish_asset_name)."""
    valid_types = _TYPES_BY_ENTITY.get(entity_type)
    if valid_types is None:
        raise ValueError(f"invalid entity_type: {entity_type!r} (asset|set|shot)")
    if sub_type not in valid_types:
        raise ValueError(
            f"invalid type: {sub_type!r} (expected one of {valid_types} for entity_type={entity_type!r})"
        )
    prefix = f"{sub_type}_"
    valid = False
    if name.startswith(prefix):
        remainder = name[len(prefix):]
        parts = remainder.split("_")
        valid = len(parts) == 2 and all(parts)
    if not valid:
        suggestion = _suggested_entity_name(name, sub_type)
        raise ValueError(
            f"{name!r} invalid - suggestion: {suggestion!r}. "
            f"Convention: TYPE_Name_Variant, valid families: {', '.join(valid_types)}."
        )
    return True


def validate_publish_asset_name(asset_name, asset_type):
    """Historical alias of validate_entity_name(asset_name, 'asset', asset_type) - kept
    for compatibility (Houdini HDA, tests, allocate_publish_version)."""
    return validate_entity_name(asset_name, "asset", asset_type)


def _find_asset_entity(project_root, asset_name):
    """Locate an already-created entity (assets|sets|shots/<name>/manifest.json), whatever
    its family (same scan as publish_asset()). A publish (LOP or per-step) is
    never an entity creator: create_asset() must have been called first."""
    project_root = Path(project_root)
    for family in ENTITY_DIR.values():
        candidate = project_root / family / asset_name
        manifest_path = candidate / ASSET_MANIFEST_NAME
        if manifest_path.is_file():
            return candidate, manifest_path
    raise FileNotFoundError(
        f"Entity '{asset_name}' not found under {project_root} (assets/, sets/, shots/) "
        f"(must be created via create_asset() before any publish)."
    )


def resolve_entity(project_root, name):
    """Resolve an already-created entity by its name, whatever its family (assets/sets/
    shots) - PUBLIC wrapper of _find_asset_entity for DCC consumers (the Blender State
    Manager must know the family of a targeted entity without storing it on the
    state; an n8n bridge will need it too). Principle 5: entity resolution lives
    in the orchestrator, not in the plugin. NEVER raises for a business case: returns
    None if the entity is not found OR its manifest is unreadable. Return:
    {"name","family","entity_type","dir","manifest"} - 'family' = ENTITY_DIR key
    ('asset'|'set'|'shot', for is_step_valid_for_context), 'entity_type' = sub-type
    (CHARACTER/PROP/...) from the manifest."""
    try:
        entity_dir, manifest_path = _find_asset_entity(project_root, name)
    except FileNotFoundError:
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    family = manifest.get("entity_type", "asset")
    if family not in ENTITY_DIR:
        # Inconsistent manifest -> the disk folder is authoritative (principle: readable source).
        parent = entity_dir.parent.name
        family = next((k for k, v in ENTITY_DIR.items() if v == parent), "asset")
    return {
        "name": name,
        "family": family,
        "entity_type": manifest.get("type", ""),
        "dir": str(entity_dir),
        "manifest": manifest,
    }


def publish_version_from_dir(final_dir):
    """Extract the version number of a final_dir returned by allocate_publish_version()
    (e.g. 'CHARACTER_Lina_Default_lop_v003' -> 3). Distinct from _ver(): a final_dir is a
    directory name without extension (the number ends the name), _ver() expects a versioned
    file name with an extension (see publish_asset)."""
    m = _DIR_VER_RE.search(Path(final_dir).name)
    if not m:
        raise ValueError(f"final_dir has no version suffix: {final_dir!r}")
    return int(m.group(1))


def _publish_dirs(entity_dir, kind):
    """Publish subtree for 'kind': 'lop' (whole-asset Houdini LOP, historical) or a
    step name (per-step Blender/DCC, e.g. 'modeling') - reuses entity_dir/<step> already
    scaffolded by create_asset() (wip/, publish/). Returns (publish_root, staging_root)."""
    base = entity_dir / (LOP_DIR_NAME if kind == "lop" else kind)
    return base / LOP_PUBLISH_DIR_NAME, base / LOP_STAGING_DIR_NAME


def _publish_entries(manifest, kind):
    """List of version entries for 'kind' in the manifest (created if absent).
    kind='lop' -> manifest[LOP_PUBLISHES_KEY] (flat list, unchanged historical contract).
    Any other kind -> manifest[STEP_PUBLISHES_KEY][kind] (dict step -> list, same
    entries) - distinct key to never collide with 'publishes' (legacy)."""
    if kind == "lop":
        return manifest.setdefault(LOP_PUBLISHES_KEY, [])
    return manifest.setdefault(STEP_PUBLISHES_KEY, {}).setdefault(kind, [])


def allocate_publish_version(project_root, asset_name, asset_type=None, comment=None, kind="lop"):
    """Atomically reserve (fcntl.flock) the next publish version number for an
    existing asset, and create an empty staging directory. Touches no artifact:
    the caller (HDA callback or Blender operator) writes them into staging_dir, then calls
    finalize_publish_version() to commit (atomic os.replace, same filesystem as
    staging_dir since both live under entity_dir/<kind>/) and finalize the manifest.

    'kind' (keyword, default 'lop' for Houdini compatibility): 'lop' for a LOP publish
    (complete snapshot, outside the step taxonomy - unchanged historical contract, 'asset_type'
    required + validated via validate_publish_asset_name); or a step name (e.g. 'modeling',
    'lookdev') for a per-step DCC publish (Blender USD/GLB...) - naming is already
    guaranteed by create_asset() (see validate_entity_name), no revalidation here and
    'asset_type' is ignored.

    Returns (staging_dir, final_dir) as pathlib.Path. staging_dir already exists (empty);
    final_dir does not exist yet (it is the target of the future replace).
    """
    if kind == "lop":
        validate_publish_asset_name(asset_name, asset_type)

    project_root = Path(project_root)
    entity_dir, manifest_path = _find_asset_entity(project_root, asset_name)

    with acquire_lock(manifest_path):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        if kind == "lop":
            declared_type = manifest.get("type")
            if declared_type != asset_type:
                raise ValueError(
                    f"asset_type {asset_type!r} does not match the declared type of "
                    f"'{asset_name}' in manifest.json ({declared_type!r})."
                )

        existing = _publish_entries(manifest, kind)
        next_ver = max((e.get("version", 0) for e in existing), default=0) + 1

        versioned_name = f"{asset_name}_{kind}_v{next_ver:03d}"
        publish_root, staging_root = _publish_dirs(entity_dir, kind)
        final_dir = publish_root / versioned_name
        staging_dir = staging_root / f"{versioned_name}.staging-{os.getpid()}"

        if final_dir.exists():
            raise FileExistsError(f"Version already present, not overwritten: {final_dir}")

        # publish_root must exist so the future os.replace() has a valid parent;
        # final_dir itself must NOT exist (it is the target of the replace).
        publish_root.mkdir(parents=True, exist_ok=True)
        staging_dir.mkdir(parents=True, exist_ok=False)

        # Reservation: 'pending' entry to block any reassignment of this number while
        # finalize_publish_version() has not committed (otherwise two concurrent publishes
        # could both compute the same next_ver).
        existing.append({
            "version": next_ver,
            "status": "pending",
            "comment": comment or "",
            "reserved_utc": _now(),
        })
        manifest["modified_utc"] = _now()
        _atomic_write_json(manifest_path, manifest)

    return staging_dir, final_dir


def _missing_artifacts(staging_dir, expected_artifacts):
    """Check that each entry of expected_artifacts exists and is non-empty in staging_dir.
    An entry with a '.' is an exact name (e.g. 'thumb.png') - PRIORITY branch, never
    interpreted as a folder. An entry without a '.' is either an artifact stem (USD layer, GLB
    or cache .vdb/.bgeo.sc/.abc), matched against PUBLISH_ARTIFACT_EXTENSIONS (no extension
    assumed in advance - Apprentice writes '.usdnc', commercial '.usd'/'.usdc'/'.usda', Blender
    '.glb'), or a sequence folder (multi-frame sim) - accepted if it exists and is non-empty.

    Returns the list of missing/empty entries (empty list = everything present)."""
    missing = []
    for artifact in expected_artifacts:
        if "." in artifact:
            candidate = staging_dir / artifact
            if not candidate.is_file() or candidate.stat().st_size == 0:
                missing.append(artifact)
        else:
            matches = [
                staging_dir / f"{artifact}{ext}" for ext in PUBLISH_ARTIFACT_EXTENSIONS
                if (staging_dir / f"{artifact}{ext}").is_file()
                and (staging_dir / f"{artifact}{ext}").stat().st_size > 0
            ]
            seq_dir = staging_dir / artifact
            seq_ok = seq_dir.is_dir() and any(seq_dir.iterdir())
            if not matches and not seq_ok:
                missing.append(artifact)
    return missing


def finalize_publish_version(project_root, asset_name, staging_dir, final_dir, version,
                             expected_artifacts, comment=None):
    """Atomic commit of a publish previously reserved by allocate_publish_version():
    os.replace(staging_dir, final_dir) - single commit point for EVERYTHING the staging
    contains (artifact + thumb.png) - then manifest update under flock (entry
    'pending' -> 'complete'). To be called once the caller (HDA callback, Blender
    operator) has written the artifact and the thumbnail into staging_dir.

    'kind' (lop or step name) is NOT a separate parameter: it is recovered from the
    structure of final_dir (entity_dir/<kind>/publish/<versioned_name>, see
    allocate_publish_version/_publish_dirs) - signature unchanged so as not to break existing
    callers (build_publish_hda.py, test_publish_hda_e2e.py).

    expected_artifacts: list of names required in staging_dir before the commit (e.g.
    ['CHARACTER_Lina_Default_lop_v003', 'thumb.png'] - the artifact by its stem, resolved against
    the known extensions (PUBLISH_ARTIFACT_EXTENSIONS); the thumb by its exact name). The
    thumbnail is REQUIRED everywhere. If an artifact is missing or empty: raises ValueError, does
    NOT touch staging_dir, does NOT call os.replace, writes NOTHING to the manifest (the
    reservation stays 'pending').

    Returns {name, version, final_dir, manifest}.
    """
    project_root = Path(project_root)
    entity_dir, manifest_path = _find_asset_entity(project_root, asset_name)
    staging_dir = Path(staging_dir)
    final_dir = Path(final_dir)

    if not staging_dir.is_dir():
        raise FileNotFoundError(f"staging_dir not found: {staging_dir}")

    missing = _missing_artifacts(staging_dir, expected_artifacts)
    if missing:
        raise ValueError(
            f"Incomplete publish for '{asset_name}' v{version:03d} - missing or empty "
            f"artifact(s) in {staging_dir}: {missing}. staging_dir preserved, nothing committed."
        )

    kind_dirname = final_dir.parent.parent.name
    kind = "lop" if kind_dirname == LOP_DIR_NAME else kind_dirname

    os.replace(staging_dir, final_dir)

    with acquire_lock(manifest_path):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        existing = _publish_entries(manifest, kind)
        entry = next((e for e in existing if e.get("version") == version), None)
        if entry is None:
            raise ValueError(
                f"No 'pending' reservation found for version {version} of "
                f"'{asset_name}' (was allocate_publish_version() called?)."
            )
        entry["status"] = "complete"
        # Discover the files actually written rather than an assumed extension: under
        # an Apprentice license, Houdini writes '.usdnc' (watermarked) and not '.usd' (see
        # hython/license context). Trusting the disk avoids a manifest pointing to a file
        # that does not exist depending on the license/the DCC that published.
        # Files AND folders: a sequence artifact (multi-frame sim, see
        # _missing_artifacts folder mode) is a sub-folder, never a file - ignoring it
        # would leave 'artifact' None in the manifest. The entry then points to the folder.
        produced = sorted(p.name for p in final_dir.iterdir() if p.is_file() or p.is_dir())
        thumbs = [n for n in produced if n == LOP_THUMB_NAME]
        artifacts = [n for n in produced if n != LOP_THUMB_NAME]
        rel_dir = f"{kind_dirname}/{LOP_PUBLISH_DIR_NAME}/{final_dir.name}"
        # 'layer' kept for kind='lop' (contract read by tools/houdini/*.py); 'artifact'
        # for everything else (generic - USD or GLB depending on the calling DCC).
        artifact_key = "layer" if kind == "lop" else "artifact"
        entry[artifact_key] = f"{rel_dir}/{artifacts[0]}" if artifacts else None
        thumb_rel = f"{rel_dir}/{thumbs[0]}" if thumbs else None
        entry["thumb"] = thumb_rel
        # 'thumbnail': same entity-relative path as 'thumb', filled when thumb.png
        # exists in the finalized folder (the field was absent -> read as None by
        # consumers that expect it). 'thumb' kept for compat (existing readers).
        entry["thumbnail"] = thumb_rel
        entry["published_utc"] = _now()
        if comment:
            entry["comment"] = comment
        manifest["modified_utc"] = _now()
        _atomic_write_json(manifest_path, manifest)

        # Recompose the assembly root (asset_root.usda / shot_root.usda) for a
        # STEP publish (kind != 'lop'): a step feeds the subLayers composition, a
        # LOP is a complete snapshot outside the taxonomy (never composed). In the same flock,
        # from the already-updated manifest - _compose_entity_root does not re-lock.
        if kind != "lop":
            _compose_entity_root(entity_dir, manifest, asset_name)

    return {
        "name": asset_name,
        "version": version,
        "final_dir": str(final_dir),
        "manifest": str(manifest_path),
    }


# --------------------------------------------------------------------------------------
# Sweep of orphan allocations - a staging_dir survives on disk ONLY if
# finalize_publish_version() was never called (it consumes it via os.replace):
# a present staging_dir = abandoned allocation (crash, kill -9...) OR publish in progress
# (process still alive). Distinguishes the two via the PID encoded in the folder name
# (see allocate_publish_version: '<versioned_name>.staging-<pid>').
# --------------------------------------------------------------------------------------

_STAGING_PID_RE = re.compile(r"\.staging-(\d+)$")


def _staging_pid(dirname):
    """Extract the PID from a staging_dir name. None if the name does not match the pattern
    (defensive - a mis-named staging_dir is never touched by clean_stale_staging)."""
    m = _STAGING_PID_RE.search(dirname)
    return int(m.group(1)) if m else None


def _pid_alive(pid):
    """True if a process with this PID exists (kill(pid, 0), not a real signal)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # the process exists, we just aren't allowed to signal it
    return True


def clean_stale_staging(project_root, dry_run=False):
    """Sweep all staging_dirs (entity_dir/<kind>/.staging/*, LOP or step) of the project.

    Removes (unless dry_run=True) those whose creator PID is no longer alive - never a
    staging_dir whose process is still running (publish potentially in progress). Reports
    separately (never removes, even without dry_run) the manifest.json entries left at
    'status': 'pending' with no matching staging_dir on disk - an inconsistency to
    investigate manually (the manifest is not disposable data like staging_dir;
    see CLAUDE.md on project.json as a contract).

    Returns {"removed_staging": [str, ...], "pending_without_staging": [
        {"entity", "kind", "version", "manifest"}, ...]}.
    """
    project_root = Path(project_root)
    removed = []
    pending_without_staging = []

    for family in ENTITY_DIR.values():
        family_dir = project_root / family
        if not family_dir.is_dir():
            continue
        for entity_dir in sorted(family_dir.iterdir()):
            if not entity_dir.is_dir():
                continue
            manifest_path = entity_dir / ASSET_MANIFEST_NAME
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

            # 1. Orphan stagings: scan each <kind>/.staging/ subtree.
            for kind_dir in sorted(entity_dir.iterdir()):
                if not kind_dir.is_dir():
                    continue
                staging_root = kind_dir / LOP_STAGING_DIR_NAME
                if not staging_root.is_dir():
                    continue
                for staging_dir in sorted(staging_root.iterdir()):
                    if not staging_dir.is_dir():
                        continue
                    pid = _staging_pid(staging_dir.name)
                    if pid is not None and _pid_alive(pid):
                        continue  # publish potentially in progress - never touched
                    removed.append(str(staging_dir))
                    if not dry_run:
                        shutil.rmtree(staging_dir)

            # 2. Report (never removes): 'pending' entries with no staging on disk -
            #    computed after the sweep above, so it reflects the post-purge state in a single
            #    call (an entry just orphan-purged appears here immediately).
            all_entries = [("lop", e) for e in manifest.get(LOP_PUBLISHES_KEY, [])]
            for step, entries in manifest.get(STEP_PUBLISHES_KEY, {}).items():
                all_entries += [(step, e) for e in entries]

            for kind, entry in all_entries:
                if entry.get("status") != "pending":
                    continue
                version = entry.get("version")
                versioned_name = f"{entity_dir.name}_{kind}_v{version:03d}"
                staging_root = entity_dir / (LOP_DIR_NAME if kind == "lop" else kind) / LOP_STAGING_DIR_NAME
                matches = list(staging_root.glob(f"{versioned_name}.staging-*")) if staging_root.is_dir() else []
                if not matches:
                    pending_without_staging.append({
                        "entity": entity_dir.name,
                        "kind": kind,
                        "version": version,
                        "manifest": str(manifest_path),
                    })

    return {"removed_staging": removed, "pending_without_staging": pending_without_staging}


# --------------------------------------------------------------------------------------
# Web consumption (sync to a Three.js project) - the web project NEVER reads the
# pipeline structure, only public/assets/assets.json (see CLAUDE.md).
# --------------------------------------------------------------------------------------

WEB_ASSETS_DIRNAME = "assets"
_SYNCED_GLB_RE = re.compile(r"_v(\d+)\.glb$")


def _known_entity_names(project_root):
    """Names of all existing entities of the project (assets/sets/shots), used by
    sync_web_assets() to never touch a public/assets/ file that matches
    no known entity (see its docstring)."""
    project_root = Path(project_root)
    names = set()
    for family in ENTITY_DIR.values():
        family_dir = project_root / family
        if not family_dir.is_dir():
            continue
        for d in family_dir.iterdir():
            if d.is_dir() and (d / ASSET_MANIFEST_NAME).is_file():
                names.add(d.name)
    return names


# --- Web pinning API (principle 5: the logic lives in the orchestrator, IMPORTABLE by a
# DCC plugin or n8n - not only by the ylos_ui HTTP server). None EVER raise for
# a business case (unknown asset/version, pinning a non-GLB publish...): they return a dict
# {"ok": bool, ...}. project.json['web'] has the shape {target_dir, pinned_assets: {<asset>:
# {step, version}}} - 'target_dir' stores the target web_project_dir (consumed by
# sync_web_assets; INC-6 names it 'project_dir', we keep 'target_dir' already in the contract).

def _update_web(project_root, mutate):
    """Read-modify-write of project.json['web'] under flock (the multi-threaded HTTP server AND
    the DCC plugins write the same manifest - same discipline as the rest of the module). 'mutate'
    receives the web dict (created if absent, shape {target_dir, pinned_assets}) and modifies it in
    place; atomic write via write_manifest (_atomic_write_json internally)."""
    project_root = Path(project_root)
    manifest_path = project_root / PIPELINE_DIR / MANIFEST_NAME
    with acquire_lock(manifest_path):
        manifest = read_manifest(project_root)
        web = manifest.setdefault("web", {"target_dir": None, "pinned_assets": {}})
        mutate(web)
        manifest["modified_utc"] = _now()
        write_manifest(project_root / PIPELINE_DIR, manifest)


def _pinnable_glb_versions(project_root, entity_name, step):
    """Versions (sorted) of 'complete' publishes with a .glb artifact for this step - the ONLY
    ones pinnable for the web (sync_web_assets resolves the GLB via (step, version)). A USD publish
    never appears. [] if entity/step not found (list_publishes never raises)."""
    return sorted(
        e["version"] for e in list_publishes(project_root, entity_name, step)
        if e.get("status") == "complete" and (e.get("artifact") or "").endswith(".glb")
    )


def pin_web_asset(project_root, asset, step, version):
    """Pin a published GLB for the web sync: writes project.json['web']['pinned_assets'][asset]
    = {step, version}, after validating that a 'complete' publish with a .glb artifact exists for
    (asset, step, version). The pin is a contract consumed AS-IS by sync_web_assets (a broken
    pin would only produce a late warning there): we refuse it here, with the list of what
    exists. NEVER raises for a business case: returns
    {"ok": True, "asset", "step", "version"} or {"ok": False, "error": <str>}."""
    project_root = Path(project_root)
    asset = (asset or "").strip()
    step = (step or "").strip()
    # bool is an int in Python: version=True would match version 1 - we exclude it.
    if not asset or not step or not isinstance(version, int) or isinstance(version, bool):
        return {"ok": False, "error": "asset (str), step (str) and version (int) required."}
    available = _pinnable_glb_versions(project_root, asset, step)
    if version not in available:
        return {"ok": False, "error": (
            f"No 'complete' GLB publish for {asset!r} in {step} v{version:03d}. "
            f"Available: {available or 'none'}")}
    _update_web(project_root, lambda web: web.setdefault("pinned_assets", {}).__setitem__(
        asset, {"step": step, "version": version}))
    return {"ok": True, "asset": asset, "step": step, "version": version}


def unpin_web_asset(project_root, asset):
    """Remove an asset's web pin. Idempotent: un-pinning an unpinned asset is a success.
    NEVER raises: {"ok": True, "asset", "was_pinned": bool} or {"ok": False, "error"} if
    'asset' is empty."""
    asset = (asset or "").strip()
    if not asset:
        return {"ok": False, "error": "asset (str) required."}
    removed = []
    _update_web(project_root, lambda web: removed.append(
        web.setdefault("pinned_assets", {}).pop(asset, None)))
    return {"ok": True, "asset": asset, "was_pinned": bool(removed and removed[0] is not None)}


def set_web_target(project_root, target_dir):
    """Store the target web_project_dir in project.json['web']['target_dir'] (consumed by
    sync_web_assets without passing it again). '' -> None (clears the target). NEVER raises:
    {"ok": True, "target_dir": <str|None>}."""
    target_dir = (target_dir or "").strip() or None
    _update_web(project_root, lambda web: web.__setitem__("target_dir", target_dir))
    return {"ok": True, "target_dir": target_dir}


def sync_web_assets(project_root, web_project_dir):
    """Synchronize the PINNED GLBs (project.json['web']['pinned_assets'], never 'latest')
    to {web_project_dir}/public/assets/. The web project is a passive consumer: it never
    reads the pipeline structure, only the assets.json generated here.

    pinned_assets: {"<asset_name>": {"step": <step>, "version": <int>}} - the step is
    needed to locate the GLB unambiguously (an asset may have independent GLB publishes
    per step, see allocate_publish_version/kind).

    Behavior (mirror):
      1. Copy each pinned GLB to <ASSET_NAME>_v<VERSION:03d>.glb (cache-busting).
      2. Generate assets.json ({"assets": {...}, "generated": <ISO>}), sha256 per asset,
         written atomically (_atomic_write_json).
      3. Remove the <ASSET>_v*.glb of KNOWN assets (see _known_entity_names) whose
         version no longer matches the current pin (or whose asset is no longer pinned at
         all). A file matching no known entity is never touched.

    Returns {"assets_dir", "synced", "warnings"} - 'warnings' lists the unresolved
    pins (asset/GLB not found) without failing the rest of the synchronization.
    """
    project_root = Path(project_root)
    web_project_dir = Path(web_project_dir)
    manifest = read_manifest(project_root)
    pinned = manifest.get("web", {}).get("pinned_assets", {})

    assets_dir = web_project_dir / "public" / WEB_ASSETS_DIRNAME
    assets_dir.mkdir(parents=True, exist_ok=True)

    known_names = _known_entity_names(project_root)
    synced = {}
    warnings = []
    wanted_filenames = {}  # asset_name -> currently pinned file name

    for asset_name, pin in pinned.items():
        step = pin.get("step")
        version = pin.get("version")
        if not step or not isinstance(version, int):
            warnings.append(f"Invalid pin for {asset_name!r}: {pin!r}")
            continue
        try:
            entity_dir, _ = _find_asset_entity(project_root, asset_name)
        except FileNotFoundError:
            warnings.append(f"Pinned asset not found: {asset_name!r}")
            continue

        stem = f"{asset_name}_{step}_v{version:03d}"
        src = entity_dir / step / LOP_PUBLISH_DIR_NAME / stem / f"{stem}.glb"
        if not src.is_file():
            warnings.append(f"Pinned GLB not found for {asset_name!r}: {src}")
            continue

        dest_filename = f"{asset_name}_v{version:03d}.glb"
        shutil.copy2(src, assets_dir / dest_filename)
        synced[asset_name] = {
            "file": dest_filename,
            "version": version,
            "sha256": hashlib.sha256(src.read_bytes()).hexdigest(),
        }
        wanted_filenames[asset_name] = dest_filename

    # Mirror: purge old versions (or assets removed from the pin) of known entities.
    for f in list(assets_dir.iterdir()):
        if not f.is_file() or f.suffix != ".glb":
            continue
        m = _SYNCED_GLB_RE.search(f.name)
        if not m:
            continue
        candidate_name = f.name[: m.start()]
        if candidate_name not in known_names:
            continue  # file foreign to any known entity - never touched
        if wanted_filenames.get(candidate_name) != f.name:
            f.unlink()

    _atomic_write_json(assets_dir / "assets.json", {"assets": synced, "generated": _now()})

    return {"assets_dir": str(assets_dir), "synced": synced, "warnings": warnings}


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def _cli(argv=None):
    p = argparse.ArgumentParser(description="Project & asset creator - Ylos pipeline.")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("project", help="Create a project (asset-centric shell).")
    pp.add_argument("name", help="Project name (single segment, no /)")
    pp.add_argument("--root", help=f"Source root (default ${ENV_ROOT})")
    pp.add_argument("--cache", help=f"Cache root (default ${ENV_CACHE})")
    pp.add_argument("--prod-type", default="FILM", help="Production type (default FILM)")
    pp.add_argument("--display-name", help="Display name (default = name)")
    pp.add_argument("--force", action="store_true", help="Override if the project exists")

    pa = sub.add_parser("asset", help="Create an entity (asset/set/shot) in a project.")
    pa.add_argument("project", help="Path of the existing project")
    pa.add_argument("name", help="Entity name (single segment, no /)")
    pa.add_argument("--entity-type", default="asset", choices=["asset", "set", "shot"],
                    help="Entity family (default asset)")
    pa.add_argument("--type", dest="asset_type", default="OTHER",
                    help="Business sub-type - required to satisfy the TYPE_Name_Variant convention "
                         "(asset: CHARACTER/PROP/VEHICLE/CREATURE/FX_ELEMENT, set: EXTERIOR/INTERIOR/"
                         "HERO_SET/MODULAR_KIT, shot: LAYOUT/ANIMATION/FX/LIGHTING/COMP ; default OTHER, "
                         "always invalid - create_asset() explains the convention if omitted)")
    pa.add_argument("--steps", help="Comma-separated steps (default: project pipeline)")
    pa.add_argument("--force", action="store_true", help="Override if the entity exists")

    pub = sub.add_parser("publish", help="Publish a USD file into an entity step.")
    pub.add_argument("project", help="Path of the existing project")
    pub.add_argument("asset", help="Entity name")
    pub.add_argument("step", help="Publish step (e.g. modeling, lookdev)")
    pub.add_argument("file", help="Source file to publish (.usda or .usdc)")

    pfr = sub.add_parser("set-frame-range",
                         help="Set a shot's frame range (schema 2.1) and recompose "
                              "its shot_root.usda (timecodes).")
    pfr.add_argument("project", help="Path of the existing project")
    pfr.add_argument("shot", help="Shot name")
    pfr.add_argument("start", type=int, help="First frame (inclusive)")
    pfr.add_argument("end", type=int, help="Last frame (inclusive), > start")
    pfr.add_argument("--fps", type=float, default=None,
                     help="Frames per second (default: shot's existing fps or scene default)")

    pcs = sub.add_parser("clean-staging",
                         help="Purge orphan staging_dirs (dead process) + report "
                              "manifest 'pending' entries with no matching staging.")
    pcs.add_argument("project", help="Path of the existing project")
    pcs.add_argument("--apply", action="store_true",
                     help="Actually delete (default: dry-run, reports without deleting anything)")

    args = p.parse_args(argv)

    try:
        if args.cmd == "project":
            info = create(args.name, root=args.root, cache=args.cache, force=args.force,
                          prod_type=args.prod_type, display_name=args.display_name)
            print(f"[ok] project '{info['name']}' created")
            print(f"  source   : {info['source']}")
            print(f"  cache    : {info['cache']}")
            print(f"  manifest : {info['manifest']}")
        elif args.cmd == "asset":
            steps = [s.strip() for s in args.steps.split(",") if s.strip()] if args.steps else None
            info = create_asset(args.project, args.name, entity_type=args.entity_type,
                                asset_type=args.asset_type, steps=steps, force=args.force)
            print(f"[ok] {info['entity_type']} '{info['name']}' created")
            print(f"  path      : {info['path']}")
            print(f"  manifest  : {info['manifest']}")
            if info["asset_root"]:
                print(f"  asset_root: {info['asset_root']}")
        elif args.cmd == "publish":
            info = publish_asset(args.project, args.asset, args.step, args.file)
            print(f"[ok] publish {info['name']} / {info['step']} v{info['version']:03d}")
            print(f"  publish  : {info['publish_path']}")
            print(f"  manifest : {info['manifest']}")
            if info["asset_root"]:
                print(f"  asset_root: {info['asset_root']}")
        elif args.cmd == "set-frame-range":
            fr = set_frame_range(args.project, args.shot, args.start, args.end, fps=args.fps)
            print(f"[ok] frame_range {args.shot} : {fr['start']}-{fr['end']} @ {fr['fps']} fps")
            print("  shot_root.usda recomposed (timecodes)")
        else:  # clean-staging
            info = clean_stale_staging(args.project, dry_run=not args.apply)
            verb = "removed" if args.apply else "to remove (dry-run - pass --apply to execute)"
            print(f"[ok] {len(info['removed_staging'])} staging_dir(s) {verb}")
            for path in info["removed_staging"]:
                print(f"  - {path}")
            if info["pending_without_staging"]:
                print(f"[report] {len(info['pending_without_staging'])} manifest 'pending' "
                      f"entry(ies) with no matching staging (left unchanged):")
                for e in info["pending_without_staging"]:
                    print(f"  - {e['entity']} / {e['kind']} v{e['version']:03d}  ({e['manifest']})")
    except (ValueError, FileExistsError, FileNotFoundError) as e:
        sys.stderr.write(f"[error] {e}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
