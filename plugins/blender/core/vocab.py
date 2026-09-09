# -*- coding: utf-8 -*-
# Ylos Pipeline - core/vocab.py
# ============================================================================
# THE ONLY home of the pipeline vocabulary (types, steps, prod, context) for the
# addon's Blender EnumProperty. The VALUES come from create_project.py
# (the orchestrator owns identity AND vocabulary, see CLAUDE.md principle
# 5); only the human LABELS (label + description) live here, in
# PRESENTATION. No addon enum re-declares a list of values: it
# consumes a *_ITEMS from this module.
#
# GC / BPY TRAP (see CLAUDE.md): an EnumProperty items= callback must
# NEVER return a tuple built on the fly - Blender keeps no
# reference, the strings are collected by the GC (corrupted UI / crash).
# Two modes only:
#   (a) items=<MODULE_LEVEL_TUPLE>            (static);
#   (b) callback returning a pre-built module-level tuple
#       (context_type-dependent step case: return STEP_ITEMS[ctx]).
# All *_ITEMS below are module-level tuples built ONCE at
# import. The current call-sites all use mode (a) - see the refactor
# report for the rationale (each step enum round-trips with the
# context-agnostic Scene property ylos_current_step, hence STEP_ITEMS_ALL).
# ============================================================================

import os
import sys

# create_project.py lives at the repo root. __init__.py injects it into sys.path
# at register(), but operator class bodies (which carry static EnumProperty
# items=) are evaluated at addon IMPORT, BEFORE register().
# We therefore reproduce here the operators' os.path.realpath pattern to
# self-bootstrap (see CLAUDE.md). core/vocab.py -> core -> blender -> plugins ->
# repo root = 4 levels up (the first '..' strips the file name).
_REPO_ROOT = os.path.normpath(
    os.path.join(os.path.realpath(__file__), "..", "..", "..", "..")
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import create_project as _cp


# ----------------------------------------------------------------------------
# Presentation: human labels. THE ONLY place where labels/descriptions live.
# {domain: {value: (label, description)}}. Missing value -> fallback
# (value.replace("_", " ").title(), "").
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
    """(value, label, description) for a value, via PRESENTATION; fallback
    (value.replace('_',' ').title(), '') if the label is not declared."""
    label, desc = PRESENTATION.get(domain, {}).get(
        value, (value.replace("_", " ").title(), "")
    )
    return (value, label, desc)


def _items(domain, values):
    """Build a tuple of EnumProperty items ((value, label, desc), ...) ONCE,
    at import (never in a callback - see the GC trap at the top of the module)."""
    return tuple(_present(domain, v) for v in values)


def _ordered_union(*lists):
    """Ordered union without duplicates (preserves first-appearance order)."""
    seen = {}
    for lst in lists:
        for v in lst:
            seen.setdefault(v, None)
    return tuple(seen)


# ----------------------------------------------------------------------------
# Items built ONCE at import (module-level tuples). Values = the only
# source create_project; never a hard-coded list here.
# ----------------------------------------------------------------------------
ASSET_TYPE_ITEMS = _items("asset_type", _cp.ASSET_TYPES)
SET_TYPE_ITEMS   = _items("set_type",   _cp.SET_TYPES)
SHOT_TYPE_ITEMS  = _items("shot_type",  _cp.SHOT_TYPES)
PROD_TYPE_ITEMS  = _items("prod_type",  _cp.PROD_TYPES)

# Context types: DERIVED from ENTITY_DIR (asset/set/shot), no redundant
# constant on the create_project side (see task). ENTITY_DIR is an ordered dict.
CONTEXT_TYPES      = tuple(k.upper() for k in _cp.ENTITY_DIR)
CONTEXT_TYPE_ITEMS = _items("context_type", CONTEXT_TYPES)

# Steps per context_type (callback mode (b) allowed: return STEP_ITEMS[ctx]) -
# each value is a pre-built module-level tuple.
STEP_ITEMS = {
    "ASSET": _items("step", _cp.DEFAULT_ASSET_STEPS),
    "SET":   _items("step", _cp.DEFAULT_SET_STEPS),
    "SHOT":  _items("step", _cp.DEFAULT_SHOT_STEPS),
}

# Ordered union of all steps (context unknown at the call-site: Scene
# property, enums that round-trip with it). Order: asset -> shot -> set, dedup.
STEP_ITEMS_ALL = _items(
    "step",
    _ordered_union(
        _cp.DEFAULT_ASSET_STEPS, _cp.DEFAULT_SHOT_STEPS, _cp.DEFAULT_SET_STEPS
    ),
)


def values(items):
    """List of values of an items tuple - utility for tests / guards."""
    return [v for v, _label, _desc in items]
