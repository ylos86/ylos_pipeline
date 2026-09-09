# -*- coding: utf-8 -*-
"""Vignettes d'entites pour l'UI Blender - cache d'icones de preview.

Pourquoi un cache : un draw() de panel est rappele a CHAQUE redraw (survol, zoom viewport,
frame change...). Resoudre la vignette de chaque entite a chaque passe voudrait dire scanner
le disque des dizaines de fois par seconde. Meme pattern que op_scene_check /
op_update_imports / op_io._product_cache : on calcule hors draw, on invalide explicitement.

La RESOLUTION elle-meme n'est pas ici : elle vit dans create_project.resolve_entity_thumbnail
(point unique, principe 5 - partagee avec la web UI). Ce module ne fait que la memoiser et la
transformer en icon_id Blender.
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
    """Purge le cache (tout, ou seulement les entrees d'un projet). A appeler apres tout
    geste qui peut changer une vignette : save WIP, publish, capture de preview, refresh
    manuel de la liste d'assets."""
    global _cache
    if project_path is None:
        _cache = {}
        return
    for key in [k for k in _cache if k[0] == project_path]:
        del _cache[key]


def get_entity_thumbs(project_path: str, entity_names, ctx_type: str = "asset",
                      force: bool = False) -> dict:
    """{entity_name: (icon_id, source)} - icon_id 0 quand aucune vignette n'existe (convention
    de load_icon, jamais d'exception). 'source' vaut custom|publish|legacy|wip|none, expose
    tel quel par l'orchestrateur : l'UI peut dire qu'une vignette vient d'un WIP plutot que de
    laisser croire a un publish."""
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
