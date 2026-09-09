# -*- coding: utf-8 -*-
# State Manager (Prism-style) - data model of the "export states".
#
# A CollectionProperty on bpy.types.Scene is saved in the .blend: the export recipe
# (which steps of which entities to publish) persists WITH the scene, like Prism's
# State Manager. The vocabulary values (steps) come from vocab.py -> create_project (the only
# source, see CLAUDE.md "Centralized pipeline vocabulary") - never a hard-coded list.

import bpy
from bpy.props import (
    BoolProperty, StringProperty, EnumProperty, IntProperty, CollectionProperty,
)

from . import vocab


class YLOS_PG_ExportState(bpy.types.PropertyGroup):
    """An export state = one entry of the batch publish recipe. enabled/entity/step
    drive execution; allow_full_scene/comment are options; last_result/
    last_version are the display of the last run (never re-read by execution)."""

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
    # FULL domain (STEP_ITEMS_ALL, module-level tuple - bpy GC trap, see vocab.py): per-family
    # validity is checked at execution by publish_entity_step (same approach
    # as op_publish, where is_step_valid_for_context decides by the entity's family).
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
    # Display only: last result of ylos.publish_states (never read by execution).
    last_result: StringProperty(default="")
    last_version: IntProperty(default=0)


def register_properties():
    """To call AFTER bpy.utils.register_class(YLOS_PG_ExportState): CollectionProperty
    (type=...) requires the PropertyGroup to be already registered."""
    bpy.types.Scene.ylos_export_states = CollectionProperty(type=YLOS_PG_ExportState)
    bpy.types.Scene.ylos_export_states_index = IntProperty(
        name="Active Export State", default=0,
    )


def unregister_properties():
    for prop in ("ylos_export_states", "ylos_export_states_index"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
