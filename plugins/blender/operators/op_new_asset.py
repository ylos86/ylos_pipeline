# -*- coding: utf-8 -*-
import bpy
import os
import sys
from bpy.props import StringProperty, EnumProperty, BoolProperty, CollectionProperty
from ..core.asset import (
    sanitize_entity_name,
    invalidate_entity_cache,
)
from ..core.project import (
    get_or_create_collection, link_collection,
    resolve_parent_collection, collection_target_label,
)
from ..core import vocab

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


# Steps to check at creation: (value, label) generated from vocab.STEP_ITEMS[ctx] (hence
# create_project.DEFAULT_*_STEPS, the only source), never a hard-coded list/size - purged in
# INC-2. Before: three FIXED-size BoolVectorProperty (e.g. Shot=6) that drifted from the
# real vocabulary (DEFAULT_SHOT_STEPS has 4 today): a bool vector cannot
# change size without re-declaring the class, which IS the bug. Replaced by a
# CollectionProperty (dynamic size) rebuilt from vocab.STEP_ITEMS[ctx] on every
# context_type change (see YLOS_PG_StepToggle below) - can no longer drift.
_STEP_ITEMS_BY_CTX = {
    "ASSET": vocab.STEP_ITEMS["ASSET"],
    "SHOT":  vocab.STEP_ITEMS["SHOT"],
    "SET":   vocab.STEP_ITEMS["SET"],
}


class YLOS_PG_StepToggle(bpy.types.PropertyGroup):
    step:    StringProperty()
    label:   StringProperty()
    enabled: BoolProperty(default=True)


def _rebuild_steps(op, context_type):
    items = _STEP_ITEMS_BY_CTX.get(context_type, _STEP_ITEMS_BY_CTX["ASSET"])
    op.steps_to_create.clear()
    for value, label, _desc in items:
        item = op.steps_to_create.add()
        item.step = value
        item.label = label
        item.enabled = True


def _update_context_type(self, context):
    _rebuild_steps(self, self.context_type)


# Collection hierarchy (get_or_create_collection/link_collection/resolve_parent_collection/
# collection_target_label): moved into core/project.py (INC-5) - shared with
# op_import_product.py, which must place a published import in the same location as an entity
# created locally. See core/project.py for the logic.

def _create_entity_collection(entity_name, asset_type, context_type, scene):
    parent, parent_display = resolve_parent_collection(asset_type, context_type, scene)
    col = get_or_create_collection(entity_name)
    link_collection(col, parent)
    return col, parent_display


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

    # Vocabulary (values) = create_project via core/vocab.py, the only home. Module-level
    # *_ITEMS tuples (bpy GC trap). Defaults unchanged.
    context_type: EnumProperty(
        name="Type",
        items=vocab.CONTEXT_TYPE_ITEMS,
        default="ASSET",
        update=_update_context_type,
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

    steps_to_create: CollectionProperty(type=YLOS_PG_StepToggle)

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
        _rebuild_steps(self, self.context_type)
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
            col_target = collection_target_label(sub_type, self.context_type)
            layout.label(
                text="Collection: " + full_name + "  ->  " + col_target,
                icon="OUTLINER_COLLECTION",
            )

        layout.separator()

        box = layout.box()
        box.label(text="Steps to create:", icon="CHECKMARK")
        row = box.row(align=True)
        for item in self.steps_to_create:
            row.prop(item, "enabled", text=item.label, toggle=True)

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

        if not self.steps_to_create:
            # invoke() populates steps_to_create from vocab (dialog path). A scripted
            # call that skips invoke() (bpy.ops.ylos.new_asset(...) in EXEC context -
            # automation agent, see CLAUDE.md) would otherwise see an empty collection ->
            # zero step created silently. Default: all steps enabled (same semantics
            # as the old BoolVectorProperty default).
            _rebuild_steps(self, context_type)

        steps = [item.step for item in self.steps_to_create if item.enabled]

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

        # ylos_current_step is a SINGLE Scene property shared across families
        # (STEP_ITEMS_ALL, see vocab.py): it never updates on its own at a
        # context change. Real observed bug: creating a Set after working on
        # an Asset leaves the step at 'modeling' (invalid for a Set) -> the State Manager
        # copies it as-is into an export state (state_add_export) -> Publish rejects it
        # far from the symptom ("Step 'modeling' is not valid for a set."). We clamp here to
        # the family's first valid step if the current one is not (same domain
        # as is_step_valid_for_context, see core/project.py).
        valid_steps = vocab.values(vocab.STEP_ITEMS.get(context_type, vocab.STEP_ITEMS["ASSET"]))
        if valid_steps and scene.ylos_current_step not in valid_steps:
            scene.ylos_current_step = valid_steps[0]

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
