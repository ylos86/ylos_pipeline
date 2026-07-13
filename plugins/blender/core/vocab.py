# -*- coding: utf-8 -*-
# Ylos Pipeline - core/vocab.py
# ============================================================================
# SEUL home du vocabulaire pipeline (types, steps, prod, context) pour les
# EnumProperty de l'addon Blender. Les VALEURS viennent de create_project.py
# (l'orchestrateur possede l'identite ET le vocabulaire, cf. CLAUDE.md principe
# 5) ; seuls les LIBELLES humains (label + description) vivent ici, dans
# PRESENTATION. Aucun enum de l'addon ne redeclare une liste de valeurs : il
# consomme un *_ITEMS de ce module.
#
# PIEGE GC / BPY (cf. CLAUDE.md) : un callback items= d'EnumProperty ne doit
# JAMAIS retourner un tuple construit a la volee - Blender ne garde pas de
# reference, les chaines sont collectees par le GC (UI corrompue / crash).
# Deux modes seulement :
#   (a) items=<TUPLE_MODULE_LEVEL>            (statique) ;
#   (b) callback retournant un tuple module-level pre-construit
#       (cas step dependant du context_type : return STEP_ITEMS[ctx]).
# Tous les *_ITEMS ci-dessous sont des tuples module-level construits UNE FOIS a
# l'import. Les call-sites actuels utilisent tous le mode (a) - voir le rapport
# de refactor pour la justification (chaque enum step round-trip avec la
# propriete Scene context-agnostique ylos_current_step, donc STEP_ITEMS_ALL).
# ============================================================================

import os
import sys

# create_project.py vit a la racine du repo. __init__.py l'injecte dans sys.path
# au register(), mais les corps de classe d'operateurs (qui portent des items=
# statiques d'EnumProperty) sont evalues a l'IMPORT de l'addon, AVANT register().
# On reproduit donc ici le pattern os.path.realpath des operateurs pour
# s'auto-amorcer (cf. CLAUDE.md). core/vocab.py -> core -> blender -> plugins ->
# repo root = 4 remontees (le premier '..' retire le nom de fichier).
_REPO_ROOT = os.path.normpath(
    os.path.join(os.path.realpath(__file__), "..", "..", "..", "..")
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import create_project as _cp


# ----------------------------------------------------------------------------
# Presentation : libelles humains. SEUL endroit ou vivent labels/descriptions.
# {domaine: {valeur: (label, description)}}. Valeur absente -> fallback
# (valeur.replace("_", " ").title(), "").
# ----------------------------------------------------------------------------
PRESENTATION = {
    "asset_type": {
        "CHARACTER":  ("Character",  "Biped, creature, hero, NPC..."),
        "PROP":       ("Prop",       "Hard-surface object, furniture, tool..."),
        "VEHICLE":    ("Vehicle",    "Car, ship, aircraft..."),
        "CREATURE":   ("Creature",   "Non-humanoid creature"),
        "FX_ELEMENT": ("FX Element", "Reusable FX asset (debris, particles rig...)"),
    },
    "set_type": {
        "EXTERIOR":    ("Exterior",    "Outdoor set"),
        "INTERIOR":    ("Interior",    "Indoor set"),
        "HERO_SET":    ("Hero Set",    "Main, camera-ready set"),
        "MODULAR_KIT": ("Modular Kit", "Reusable modular set pieces"),
    },
    "shot_type": {
        "LAYOUT":    ("Layout",    "Layout pass"),
        "ANIMATION": ("Animation", "Animation pass"),
        "FX":        ("FX",        "FX pass"),
        "LIGHTING":  ("Lighting",  "Lighting pass"),
        "COMP":      ("Comp",      "Composite pass"),
    },
    "prod_type": {
        "FILM":   ("Film",   "24fps | 2K | Cycles | AgX"),
        "SERIES": ("Series", "Episodic delivery"),
        "GAME":   ("Game",   "Real-time / game engine target"),
        "XR":     ("XR",     "Extended reality"),
        "AR":     ("AR",     "60fps | Quest res | EEVEE | sRGB"),
        "VR":     ("VR",     "90fps | Stereo res | EEVEE | sRGB"),
    },
    "context_type": {
        "ASSET": ("Asset", "Working on a character, prop, or environment asset"),
        "SET":   ("Set",   "Working on a set / environment assembly"),
        "SHOT":  ("Shot",  "Working on a specific shot"),
    },
    "step": {
        "modeling":  ("Modeling",  ""),
        "rigging":   ("Rigging",   ""),
        "lookdev":   ("LookDev",   ""),
        "fx":        ("FX",        ""),
        "animation": ("Animation", ""),
        "lighting":  ("Lighting",  ""),
        "comp":      ("Comp",      ""),
        "layout":    ("Layout",    ""),
    },
}


def _present(domain, value):
    """(value, label, description) pour une valeur, via PRESENTATION ; fallback
    (value.replace('_',' ').title(), '') si le libelle n'est pas declare."""
    label, desc = PRESENTATION.get(domain, {}).get(
        value, (value.replace("_", " ").title(), "")
    )
    return (value, label, desc)


def _items(domain, values):
    """Construit un tuple d'items EnumProperty ((value, label, desc), ...) UNE
    fois, a l'import (jamais dans un callback - cf. piege GC en tete de module)."""
    return tuple(_present(domain, v) for v in values)


def _ordered_union(*lists):
    """Union ordonnee sans doublons (preserve l'ordre de premiere apparition)."""
    seen = {}
    for lst in lists:
        for v in lst:
            seen.setdefault(v, None)
    return tuple(seen)


# ----------------------------------------------------------------------------
# Items construits UNE FOIS a l'import (tuples module-level). Valeurs = seule
# source create_project ; jamais de liste codee en dur ici.
# ----------------------------------------------------------------------------
ASSET_TYPE_ITEMS = _items("asset_type", _cp.ASSET_TYPES)
SET_TYPE_ITEMS   = _items("set_type",   _cp.SET_TYPES)
SHOT_TYPE_ITEMS  = _items("shot_type",  _cp.SHOT_TYPES)
PROD_TYPE_ITEMS  = _items("prod_type",  _cp.PROD_TYPES)

# Context types : DERIVES d'ENTITY_DIR (asset/set/shot), pas de constante
# redondante cote create_project (cf. tache). ENTITY_DIR est un dict ordonne.
CONTEXT_TYPES      = tuple(k.upper() for k in _cp.ENTITY_DIR)
CONTEXT_TYPE_ITEMS = _items("context_type", CONTEXT_TYPES)

# Steps par context_type (mode callback (b) autorise : return STEP_ITEMS[ctx]) -
# chaque valeur est un tuple module-level pre-construit.
STEP_ITEMS = {
    "ASSET": _items("step", _cp.DEFAULT_ASSET_STEPS),
    "SET":   _items("step", _cp.DEFAULT_SET_STEPS),
    "SHOT":  _items("step", _cp.DEFAULT_SHOT_STEPS),
}

# Union ordonnee de tous les steps (context inconnu au call-site : propriete
# Scene, enums qui round-trip avec elle). Ordre : asset -> shot -> set, dedup.
STEP_ITEMS_ALL = _items(
    "step",
    _ordered_union(
        _cp.DEFAULT_ASSET_STEPS, _cp.DEFAULT_SHOT_STEPS, _cp.DEFAULT_SET_STEPS
    ),
)


def values(items):
    """Liste des valeurs d'un tuple d'items - utilitaire pour tests / gardes."""
    return [v for v, _label, _desc in items]
