# -*- coding: utf-8 -*-
import bpy
import os
import sys
from bpy.props import StringProperty, EnumProperty, BoolVectorProperty
from ..core.asset import (
    sanitize_entity_name, validate_entity_name,
    ASSET_TYPE_PARENT_COL, invalidate_entity_cache,
)
from ..core.project import ASSET_STEPS, SHOT_STEPS, SET_STEPS

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


_ASSET_STEP_LABELS = ["Modeling", "Rigging", "LookDev", "FX"]
_SHOT_STEP_LABELS  = ["Layout", "Animation", "Lighting", "FX", "Render", "Composite"]
_SET_STEP_LABELS   = ["Modeling", "LookDev", "Lighting"]

assert len(_ASSET_STEP_LABELS) == len(ASSET_STEPS)
assert len(_SHOT_STEP_LABELS)  == len(SHOT_STEPS)
assert len(_SET_STEP_LABELS)   == len(SET_STEPS)


# ---------------------------------------------------------------------------
# Collection helpers
# ---------------------------------------------------------------------------

def _get_or_create_col(name):
    col = bpy.data.collections.get(name)
    if col is None:
        col = bpy.data.collections.new(name)
    return col


def _link_if_missing(child, parent):
    if child.name not in {c.name for c in parent.children}:
        parent.children.link(child)


def _resolve_parent_collection(asset_type, context_type, scene):
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

    else:
        col_env = _get_or_create_col("COL_ENV")
        _link_if_missing(col_env, root)
        col_sets = _get_or_create_col("COL_SETS")
        _link_if_missing(col_sets, col_env)
        return col_sets, "COL_ENV / COL_SETS"


def _create_entity_collection(entity_name, asset_type, context_type, scene):
    parent, parent_display = _resolve_parent_collection(asset_type, context_type, scene)
    col = _get_or_create_col(entity_name)
    _link_if_missing(col, parent)
    return col, parent_display


def _col_target_label(asset_type, context_type):
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
        description="PascalCase. Spaces and hyphens are removed automatically.",
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

    asset_steps: BoolVectorProperty(name="Asset Steps", size=4, default=(True, True, True, True))
    shot_steps:  BoolVectorProperty(name="Shot Steps",  size=6, default=(True, True, True, True, True, True))
    set_steps:   BoolVectorProperty(name="Set Steps",   size=3, default=(True, True, True))

    def invoke(self, context, event):
        self.context_type = context.scene.ylos_context_type
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.prop(self, "context_type")
        if self.context_type == "ASSET":
            layout.prop(self, "asset_type")
        layout.prop(self, "entity_name")

        raw   = self.entity_name
        clean = sanitize_entity_name(raw)
        valid, err_msg = validate_entity_name(clean) if clean else (False, "")

        if raw and clean != raw:
            row = layout.row()
            row.alert = True
            row.label(text="Will be saved as: '" + clean + "'", icon="INFO")

        if raw and not valid:
            row = layout.row()
            row.alert = True
            row.label(text=err_msg, icon="ERROR")

        if clean and valid:
            col_target = _col_target_label(self.asset_type, self.context_type)
            layout.label(
                text="Collection: " + clean + "  ->  " + col_target,
                icon="OUTLINER_COLLECTION",
            )

        layout.separator()

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

        clean = sanitize_entity_name(self.entity_name)
        valid, err_msg = validate_entity_name(clean)
        if not valid:
            self.report({"ERROR"}, "Invalid name: " + err_msg)
            return {"CANCELLED"}

        entity_name  = clean
        context_type = self.context_type

        if context_type == "ASSET":
            steps = [s for i, s in enumerate(ASSET_STEPS) if self.asset_steps[i]]
            a_type = self.asset_type
        elif context_type == "SHOT":
            steps = [s for i, s in enumerate(SHOT_STEPS) if self.shot_steps[i]]
            a_type = "OTHER"
        else:
            steps = [s for i, s in enumerate(SET_STEPS) if self.set_steps[i]]
            a_type = "OTHER"

        try:
            cp = _cp()
            info = cp.create_asset(
                project_path,
                entity_name,
                entity_type=context_type.lower(),
                asset_type=a_type,
                steps=steps,
            )
        except FileExistsError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        scene.ylos_current_asset = entity_name
        scene.ylos_context_type  = context_type
        if context_type == "ASSET":
            scene.ylos_asset_type = self.asset_type

        invalidate_entity_cache(project_path)

        _, parent_display = _create_entity_collection(
            entity_name, self.asset_type if context_type == "ASSET" else "PROP",
            context_type, scene,
        )

        self.report(
            {"INFO"},
            f"{info['entity_type']} '{info['name']}' created  |  {entity_name} -> {parent_display}",
        )

        return {"FINISHED"}
