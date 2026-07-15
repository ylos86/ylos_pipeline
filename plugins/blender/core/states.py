# -*- coding: utf-8 -*-
# State Manager (facon Prism) - modele de donnees des "export states".
#
# Une CollectionProperty sur bpy.types.Scene est sauvee dans le .blend : la recette d'export
# (quels steps de quelles entites publier) persiste AVEC la scene, comme le State Manager de
# Prism. Les valeurs de vocabulaire (steps) viennent de vocab.py -> create_project (seule
# source, cf. CLAUDE.md "Vocabulaire pipeline centralise") - jamais de liste codee en dur.

import bpy
from bpy.props import (
    BoolProperty, StringProperty, EnumProperty, IntProperty, CollectionProperty,
)

from . import vocab


class YLOS_PG_ExportState(bpy.types.PropertyGroup):
    """Un export state = une entree de la recette de publish batch. enabled/entity/step
    pilotent l'execution ; allow_full_scene/comment sont des options ; last_result/
    last_version sont l'affichage du dernier run (jamais relus par l'execution)."""

    enabled: BoolProperty(
        name="Enabled",
        description="Include this state when running Publish",
        default=True,
    )
    entity: StringProperty(
        name="Entity",
        description="Target entity to publish to (asset / set / shot)",
        default="",
    )
    # Domaine COMPLET (STEP_ITEMS_ALL, tuple module-level - piege GC bpy, cf. vocab.py) : la
    # validite par famille est verifiee a l'execution par publish_entity_step (meme approche
    # qu'op_publish, ou is_step_valid_for_context tranche selon la famille de l'entite).
    step: EnumProperty(
        name="Step",
        description="Pipeline step this state publishes",
        items=vocab.STEP_ITEMS_ALL,
        default="modeling",
    )
    allow_full_scene: BoolProperty(
        name="Full Scene",
        description="If no asset objects are resolved, export the whole scene instead of skipping",
        default=False,
    )
    comment: StringProperty(
        name="Comment",
        description="Optional note recorded with the publish",
        default="",
    )
    # Affichage seul : dernier resultat de ylos.publish_states (jamais lu par l'execution).
    last_result: StringProperty(default="")
    last_version: IntProperty(default=0)


def register_properties():
    """A appeler APRES bpy.utils.register_class(YLOS_PG_ExportState) : CollectionProperty
    (type=...) exige que le PropertyGroup soit deja enregistre."""
    bpy.types.Scene.ylos_export_states = CollectionProperty(type=YLOS_PG_ExportState)
    bpy.types.Scene.ylos_export_states_index = IntProperty(
        name="Active Export State", default=0,
    )


def unregister_properties():
    for prop in ("ylos_export_states", "ylos_export_states_index"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
