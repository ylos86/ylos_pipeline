# -*- coding: utf-8 -*-
import bpy
import os
import sys
from bpy.props import StringProperty, EnumProperty, BoolVectorProperty
from ..core.asset import (
    sanitize_entity_name,
    ASSET_TYPE_PARENT_COL, invalidate_entity_cache,
)
from ..core.project import ASSET_STEPS, SHOT_STEPS, SET_STEPS
from ..core import vocab

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
        description="Base name only (PascalCase, spaces/hyphens removed automatically). "
                    "The full entity name is composed as TYPE_Name_Default to match the "
                    "pipeline convention (cf. create_project.validate_entity_name).",
        default="",
    )

    # Vocabulaire (valeurs) = create_project via core/vocab.py, seul home. Tuples
    # *_ITEMS module-level (piege GC bpy). Defauts inchanges.
    context_type: EnumProperty(
        name="Type",
        items=vocab.CONTEXT_TYPE_ITEMS,
        default="ASSET",
    )
    asset_type: EnumProperty(
        name="Asset Type",
        items=vocab.ASSET_TYPE_ITEMS,
        default="PROP",
    )
    set_type: EnumProperty(
        name="Set Type",
        items=vocab.SET_TYPE_ITEMS,
        default="EXTERIOR",
    )
    shot_type: EnumProperty(
        name="Shot Type",
        items=vocab.SHOT_TYPE_ITEMS,
        default="LAYOUT",
    )

    asset_steps: BoolVectorProperty(name="Asset Steps", size=4, default=(True, True, True, True))
    shot_steps:  BoolVectorProperty(name="Shot Steps",  size=6, default=(True, True, True, True, True, True))
    set_steps:   BoolVectorProperty(name="Set Steps",   size=3, default=(True, True, True))

    def _sub_type(self):
        if self.context_type == "ASSET":
            return self.asset_type
        elif self.context_type == "SHOT":
            return self.shot_type
        return self.set_type

    def _full_name(self, sub_type):
        clean = sanitize_entity_name(self.entity_name)
        if not clean:
            return "", clean
        base = clean[:1].upper() + clean[1:]
        return f"{sub_type}_{base}_Default", clean

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
        elif self.context_type == "SHOT":
            layout.prop(self, "shot_type")
        else:
            layout.prop(self, "set_type")
        layout.prop(self, "entity_name")

        sub_type = self._sub_type()
        full_name, clean = self._full_name(sub_type)

        valid, err_msg = True, ""
        if full_name:
            try:
                _cp().validate_entity_name(full_name, self.context_type.lower(), sub_type)
            except ValueError as e:
                valid, err_msg = False, str(e)

        if full_name:
            row = layout.row()
            row.alert = not valid
            row.label(text="Will be created as: '" + full_name + "'", icon="INFO")

        if self.entity_name and not valid:
            row = layout.row()
            row.alert = True
            row.label(text=err_msg, icon="ERROR")

        if full_name and valid:
            col_target = _col_target_label(sub_type, self.context_type)
            layout.label(
                text="Collection: " + full_name + "  ->  " + col_target,
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

        sub_type = self._sub_type()
        full_name, clean = self._full_name(sub_type)
        if not full_name:
            self.report({"ERROR"}, "Name cannot be empty.")
            return {"CANCELLED"}

        try:
            _cp().validate_entity_name(full_name, self.context_type.lower(), sub_type)
        except ValueError as e:
            self.report({"ERROR"}, "Invalid name: " + str(e))
            return {"CANCELLED"}

        entity_name  = full_name
        context_type = self.context_type

        if context_type == "ASSET":
            steps = [s for i, s in enumerate(ASSET_STEPS) if self.asset_steps[i]]
        elif context_type == "SHOT":
            steps = [s for i, s in enumerate(SHOT_STEPS) if self.shot_steps[i]]
        else:
            steps = [s for i, s in enumerate(SET_STEPS) if self.set_steps[i]]

        try:
            cp = _cp()
            info = cp.create_asset(
                project_path,
                entity_name,
                entity_type=context_type.lower(),
                asset_type=sub_type,
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
            entity_name, sub_type if context_type == "ASSET" else "PROP",
            context_type, scene,
        )

        self.report(
            {"INFO"},
            f"{info['entity_type']} '{info['name']}' created  |  {entity_name} -> {parent_display}",
        )

        return {"FINISHED"}
