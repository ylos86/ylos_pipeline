# -*- coding: utf-8 -*-
# Ylos Pipeline - operators/op_new_asset.py
# Creates a new asset, shot, or set inside the active project.
# On creation: disk structure, manifest.json, and Blender collection placement.

import bpy
from bpy.props import StringProperty, EnumProperty, BoolVectorProperty
from ..core.asset import (
    create_asset, create_shot, create_set, invalidate_entity_cache,
    sanitize_entity_name, validate_entity_name,
    ASSET_TYPE_PARENT_COL,
)
from ..core.asset import ASSET_STEPS, SHOT_STEPS, SET_STEPS


# Step labels - order MUST match ASSET_STEPS / SHOT_STEPS / SET_STEPS.
_ASSET_STEP_LABELS = ["Modeling", "Rigging", "LookDev", "FX"]          # 4
_SHOT_STEP_LABELS  = ["Layout", "Animation", "Lighting", "FX",
                      "Render", "Composite"]                            # 6
_SET_STEP_LABELS   = ["Modeling", "LookDev", "Lighting"]               # 3

assert len(_ASSET_STEP_LABELS) == len(ASSET_STEPS), \
    "Ylos: _ASSET_STEP_LABELS out of sync with ASSET_STEPS"
assert len(_SHOT_STEP_LABELS) == len(SHOT_STEPS), \
    "Ylos: _SHOT_STEP_LABELS out of sync with SHOT_STEPS"
assert len(_SET_STEP_LABELS) == len(SET_STEPS), \
    "Ylos: _SET_STEP_LABELS out of sync with SET_STEPS"


# ---------------------------------------------------------------------------
# Collection helpers
# ---------------------------------------------------------------------------

def _get_or_create_col(name):
    """Get an existing collection by name, or create a new (unlinked) one."""
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
    return col


def _link_if_missing(child, parent):
    """Link child to parent only if it is not already a direct child."""
    if child.name not in {c.name for c in parent.children}:
        parent.children.link(child)


def _resolve_parent_collection(asset_type, context_type, scene):
    """
    Return the correct parent collection for a new entity, creating any
    missing intermediate COL_ nodes as needed.

    Target hierarchy in the outliner:
      ASSET / CHARACTER  ->  scene root / COL_CHAR
      ASSET / PROP       ->  scene root / COL_ENV / COL_ENV_Props
      ASSET / ENV        ->  scene root / COL_ENV
      SHOT               ->  scene root / COL_SHOTS
      SET                ->  scene root / COL_ENV / COL_SETS

    NOTE: only creates the organizational COL_ collections.
    The asset working collection (bare name, no prefix) is created by
    _create_entity_collection() so the publish/scene-check system can
    still resolve it via bpy.data.collections.get(asset_name).

    Returns (parent_col, display_path_string).
    """
    root = scene.collection

    if context_type == "ASSET":
        parent_name = ASSET_TYPE_PARENT_COL.get(asset_type, "COL_ASSETS")

        if parent_name == "COL_ENV_Props":
            col_env = _get_or_create_col("COL_ENV")
            _link_if_missing(col_env, root)
            col_props = _get_or_create_col("COL_ENV_Props")
            _link_if_missing(col_props, col_env)
            return col_props, "COL_ENV / COL_ENV_Props"

        parent = _get_or_create_col(parent_name)
        _link_if_missing(parent, root)
        return parent, parent_name

    elif context_type == "SHOT":
        col = _get_or_create_col("COL_SHOTS")
        _link_if_missing(col, root)
        return col, "COL_SHOTS"

    else:  # SET
        col_env = _get_or_create_col("COL_ENV")
        _link_if_missing(col_env, root)
        col_sets = _get_or_create_col("COL_SETS")
        _link_if_missing(col_sets, col_env)
        return col_sets, "COL_ENV / COL_SETS"


def _create_entity_collection(entity_name, asset_type, context_type, scene):
    """
    Create (or retrieve) the entity working collection and nest it under
    the correct organizational parent.

    The collection name is the bare entity name (no COL_ prefix) so that
    all downstream systems - get_asset_objects_for_publish,
    check_collection_membership, etc. - can still find it via:
        bpy.data.collections.get(asset_name)

    Returns (collection, parent_display_string).
    """
    parent, parent_display = _resolve_parent_collection(
        asset_type, context_type, scene
    )
    col = _get_or_create_col(entity_name)
    _link_if_missing(col, parent)
    return col, parent_display


def _col_target_label(asset_type, context_type):
    """
    Pure function: return the expected parent path string for UI display.
    Called in draw() - must have no side effects.
    """
    if context_type == "ASSET":
        parent_name = ASSET_TYPE_PARENT_COL.get(asset_type, "COL_ASSETS")
        if parent_name == "COL_ENV_Props":
            return "COL_ENV -> COL_ENV_Props"
        return parent_name
    elif context_type == "SHOT":
        return "COL_SHOTS"
    return "COL_ENV -> COL_SETS"


# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------

class YLOS_OT_NewAsset(bpy.types.Operator):
    bl_idname = "ylos.new_asset"
    bl_label = "New Asset / Shot / Set"
    bl_description = "Create a new entity inside the active Ylos project"
    bl_options = {"REGISTER", "UNDO"}

    entity_name: StringProperty(
        name="Name",
        description=(
            "PascalCase (HeroCharacter, SQ010_SH0010). "
            "Spaces and hyphens are removed automatically."
        ),
        default="",
    )

    context_type: EnumProperty(
        name="Type",
        items=[
            ("ASSET", "Asset", "Character, prop, environment piece"),
            ("SHOT",  "Shot",  "Shot (e.g. SQ010_SH0010)"),
            ("SET",   "Set",   "Set / environment assembly"),
        ],
        default="ASSET",
    )

    asset_type: EnumProperty(
        name="Asset Type",
        items=[
            ("PROP",        "Prop",        "Hard-surface object, furniture, tool, vehicle..."),
            ("CHARACTER",   "Character",   "Biped, creature, hero, NPC..."),
            ("ENVIRONMENT", "Environment", "Terrain piece, modular kit, vegetation..."),
        ],
        default="PROP",
    )

    # Step toggles - one BoolVectorProperty per context type.
    asset_steps: BoolVectorProperty(
        name="Asset Steps", size=4, default=(True, True, True, True)
    )
    shot_steps: BoolVectorProperty(
        name="Shot Steps", size=6, default=(True, True, True, True, True, True)
    )
    set_steps: BoolVectorProperty(
        name="Set Steps", size=3, default=(True, True, True)
    )

    def invoke(self, context, event):
        self.context_type = context.scene.ylos_context_type
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        # ---- Type fields -------------------------------------------------------
        layout.prop(self, "context_type")
        if self.context_type == "ASSET":
            layout.prop(self, "asset_type")
        layout.prop(self, "entity_name")

        # ---- Name validation feedback ------------------------------------------
        raw   = self.entity_name
        clean = sanitize_entity_name(raw)
        valid, err_msg = validate_entity_name(clean) if clean else (False, "")

        # Warn if sanitization changed the name
        if raw and clean != raw:
            row = layout.row()
            row.alert = True
            row.label(
                text="Will be saved as: '" + clean + "'",
                icon="INFO",
            )

        # Show validation error
        if raw and not valid:
            row = layout.row()
            row.alert = True
            row.label(text=err_msg, icon="ERROR")

        # ---- Collection placement preview (read-only dict lookup) --------------
        if clean and valid:
            col_target = _col_target_label(self.asset_type, self.context_type)
            layout.label(
                text="Collection: " + clean + "  ->  " + col_target,
                icon="OUTLINER_COLLECTION",
            )

        layout.separator()

        # ---- Step toggles ------------------------------------------------------
        box = layout.box()
        box.label(text="Steps to create:", icon="CHECKMARK")
        row = box.row(align=True)

        if self.context_type == "ASSET":
            for i, label in enumerate(_ASSET_STEP_LABELS):
                row.prop(self, "asset_steps", index=i, text=label, toggle=True)
        elif self.context_type == "SHOT":
            col = box.column(align=True)
            for i, label in enumerate(_SHOT_STEP_LABELS):
                col.prop(self, "shot_steps", index=i, text=label, toggle=True)
        else:
            for i, label in enumerate(_SET_STEP_LABELS):
                row.prop(self, "set_steps", index=i, text=label, toggle=True)

    def execute(self, context):
        scene        = context.scene
        project_path = scene.ylos_project_path

        if not project_path:
            self.report({"ERROR"}, "No active project. Create or load a project first.")
            return {"CANCELLED"}

        # ---- Sanitize and validate name ----------------------------------------
        clean = sanitize_entity_name(self.entity_name)
        valid, err_msg = validate_entity_name(clean)
        if not valid:
            self.report({"ERROR"}, "Invalid name: " + err_msg)
            return {"CANCELLED"}

        entity_name = clean

        # ---- Create on disk ----------------------------------------------------
        if self.context_type == "ASSET":
            steps  = [s for i, s in enumerate(ASSET_STEPS) if self.asset_steps[i]]
            result = create_asset(
                project_path, entity_name, steps,
                asset_type=self.asset_type,
            )

        elif self.context_type == "SHOT":
            steps  = [s for i, s in enumerate(SHOT_STEPS) if self.shot_steps[i]]
            result = create_shot(project_path, entity_name, steps)

        else:
            steps  = [s for i, s in enumerate(SET_STEPS) if self.set_steps[i]]
            result = create_set(project_path, entity_name, steps)

        if not result["success"]:
            self.report({"ERROR"}, result["message"])
            return {"CANCELLED"}

        # ---- Update scene context ----------------------------------------------
        scene.ylos_current_asset = entity_name
        scene.ylos_context_type  = self.context_type
        if self.context_type == "ASSET":
            scene.ylos_asset_type = self.asset_type

        invalidate_entity_cache(scene.ylos_project_path)

        # ---- Create / place Blender collection ---------------------------------
        _, parent_display = _create_entity_collection(
            entity_name, self.asset_type, self.context_type, scene
        )

        self.report(
            {"INFO"},
            result["message"] + "  |  " + entity_name + " -> " + parent_display,
        )

        return {"FINISHED"}
