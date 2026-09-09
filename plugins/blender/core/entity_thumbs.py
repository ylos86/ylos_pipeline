# -*- coding: utf-8 -*-
"""Entity thumbnails for the Blender UI - preview icon cache.

Why a cache: a panel draw() is called back on EVERY redraw (hover, viewport zoom,
frame change...). Resolving each entity's thumbnail on every pass would mean scanning
the disk dozens of times per second. Same pattern as op_scene_check /
op_update_imports / op_io._product_cache: compute outside draw, invalidate explicitly.

The RESOLUTION itself is not here: it lives in create_project.resolve_entity_thumbnail
(single point, principle 5 - shared with the web UI). This module only memoizes it and
turns it into a Blender icon_id.
"""

import os
import sys
import time

_REPO_ROOT = os.path.normpath(
    os.path.join(os.path.realpath(__file__), "..", "..", "..", "..")
)

# (project_path, ctx_type) -> {"ts": float, "data": {entity_name: (icon_id, source)}}
_cache = {}
_TTL = 8.0


def _cp():
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    import create_project
    return create_project


def invalidate(project_path: str = None):
    """Purge the cache (all, or only a project's entries). To call after any
    gesture that may change a thumbnail: save WIP, publish, preview capture, manual
    refresh of the asset list."""
    global _cache
    if project_path is None:
        _cache = {}
        return
    for key in [k for k in _cache if k[0] == project_path]:
        del _cache[key]


def get_entity_thumbs(project_path: str, entity_names, ctx_type: str = "asset",
                      force: bool = False) -> dict:
    """{entity_name: (icon_id, source)} - icon_id 0 when no thumbnail exists (load_icon
    convention, never an exception). 'source' is custom|publish|legacy|wip|none, exposed
    as-is by the orchestrator: the UI can say a thumbnail comes from a WIP rather than
    letting it pass for a publish."""
    key = (project_path or "", ctx_type)
    hit = _cache.get(key)
    if hit and not force and (time.time() - hit["ts"]) < _TTL:
        return hit["data"]

    from .thumbnails import load_icon
    cp = _cp()
    data = {}
    for name in entity_names:
        try:
            info = cp.resolve_entity_thumbnail(project_path, name)
        except Exception:
            info = {"path": None, "source": "none"}
        path = info.get("path")
        data[name] = (load_icon(path) if path else 0, info.get("source", "none"))

    _cache[key] = {"ts": time.time(), "data": data}
    return data
