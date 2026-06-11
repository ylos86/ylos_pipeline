# -*- coding: utf-8 -*-
# ylos_core/manifest.py
# Publish sidecar writer (architecture doc S-5).
#
# Every USD publish gets an immutable JSON sidecar at:
#   {publish_path}.manifest.json
#
# The sidecar records provenance (which DCC, which wip, which timestamp)
# and the list of prim paths exported -- used by the prim-stability check
# (S-4) without requiring a pxr USD parse.
#
# Sidecars are IMMUTABLE: never rewritten, never deleted by pipeline code.
# If a publish is superseded, its sidecar remains as an audit trail.

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from .locking import atomic_write_json


SIDECAR_SCHEMA_VERSION = 1
SIDECAR_SUFFIX = ".manifest.json"


def sidecar_path(publish_path: str) -> str:
    """Return the sidecar path for a given publish USD path."""
    return publish_path + SIDECAR_SUFFIX


def write_publish_sidecar(
    publish_path: str,
    entity: str,
    step: str,
    version: int,
    dcc: str,
    dcc_version: str,
    prim_paths: list,
    variant: str = None,
    source_wip: str = None,
    frame_range: list = None,
) -> None:
    """
    Write the immutable sidecar JSON for a just-completed publish.

    Args:
        publish_path: Absolute path to the USD file just written.
        entity:       Entity name (e.g. "CHAR_Hero").
        step:         Pipeline step (e.g. "modeling").
        version:      Publish version number.
        dcc:          Authoring DCC identifier: "blender" | "houdini".
        dcc_version:  Version string of the DCC (e.g. "4.2.3" or "21.0.631").
        prim_paths:   List of top-level prim paths exported
                      (e.g. ["/ROOT/CHAR_Hero/GEO_Body"]).
                      Limited to first-level under the entity prim plus GEO
                      prims -- NOT the full hierarchy. See S-5 of arch doc.
        variant:      Variant name, or None for the default publish.
        source_wip:   Filename of the WIP file that produced this publish.
        frame_range:  [start_frame, end_frame] for time-sampled publishes,
                      or None for static.

    Raises:
        FileExistsError if the sidecar already exists (immutability guard).
        OSError on write failure.
    """
    target = sidecar_path(publish_path)

    if os.path.exists(target):
        raise FileExistsError(
            f"Publish sidecar already exists (immutable): {target}"
        )

    data = {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "entity":         entity,
        "step":           step,
        "version":        version,
        "variant":        variant,
        "dcc":            dcc,
        "dcc_version":    dcc_version,
        "source_wip":     source_wip,
        "timestamp":      datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "prim_paths":     prim_paths,
        "frame_range":    frame_range,
    }

    atomic_write_json(target, data, indent=2)


def read_publish_sidecar(publish_path: str) -> dict | None:
    """
    Read the sidecar for a given publish path.
    Returns the parsed dict, or None if the sidecar does not exist.
    """
    target = sidecar_path(publish_path)
    if not os.path.exists(target):
        return None
    try:
        with open(target, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def get_published_prim_paths(publish_path: str) -> list:
    """
    Return the prim_paths list from a publish's sidecar.
    Returns [] if the sidecar is missing or malformed.
    """
    data = read_publish_sidecar(publish_path)
    if data is None:
        return []
    return data.get("prim_paths", [])


def find_removed_prims(old_publish_path: str, new_prim_paths: list) -> list:
    """
    Compare new_prim_paths against the sidecar of old_publish_path.
    Returns a list of prim paths that were present in the old publish
    but are absent from new_prim_paths.

    Used by the prim-stability check (S-4): before publishing modeling
    version N, call this with the previous publish path and the list
    of prims about to be exported. Any returned paths mean lookdev overs
    in Houdini may be broken.
    """
    old_prims = set(get_published_prim_paths(old_publish_path))
    new_prims = set(new_prim_paths)
    return sorted(old_prims - new_prims)
