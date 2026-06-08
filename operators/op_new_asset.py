# -*- coding: utf-8 -*-
# Ylos Pipeline - operators/op_new_asset.py
# Creates a new asset, shot, or set inside the active project.

import bpy
from bpy.props import StringProperty, EnumProperty, BoolVectorProperty
from ..core.asset import create_asset, create_shot, create_set
from ..core.asset import ASSET_STEPS, SHOT_STEPS, SET_STEPS


# Steps exposed as toggles so the user can deselect what they don't need
_ASSET_STEP_LABELS = ["Modeling", "UVs", "Rigging", "LookDev", "FX"]
_SHOT_STEP_LABELS  = ["Layout", "Animation", "Lighting", "FX", "Render", "Composite"]
_SET_STEP_LABELS   = ["Modeling", "LookDev", "Lighting"]


class YLOS_OT_NewAsset(bpy.types.Operator):
    bl_idname = "ylos.new_asset"
    bl_label = "New Asset / Shot / Set"
    bl_description = "Create a new entity inside the active Ylos project"
    bl_options = {"REGISTER", "UNDO"}

    entity_name: StringProperty(
        name="Name",
        description="PascalCase for assets (HeroCharacter), SQ010_SH0010 for shots",
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
            ("PROP",       "Prop",        "Hard-surface object, furniture, tool, vehicle..."),
            ("CHARACTER",  "Character",   "Biped, creature, hero, NPC..."),
            ("ENVIRONMENT","Environment", "Terrain piece, modular kit, vegetation..."),
        ],
        default="PROP",
    )

    # Step toggles - one BoolVectorProperty per context type
    asset_steps: BoolVectorProperty(
        name="Asset Steps",
        size=5,
        default=(True, True, True, True, True),
    )

    shot_steps: BoolVectorProperty(
        name="Shot Steps",
        size=6,
        default=(True, True, True, True, True, True),
    )

    set_steps: BoolVectorProperty(
        name="Set Steps",
        size=3,
        default=(True, True, True),
    )

    def invoke(self, context, event):
        # Sync context_type with scene property
        self.context_type = context.scene.ylos_context_type
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.prop(self, "context_type")
        if self.context_type == "ASSET":
            layout.prop(self, "asset_type")
        layout.prop(self, "entity_name")
        layout.separator()

        # Show step toggles for the active type
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
        scene = context.scene
        project_path = scene.ylos_project_path

        if not project_path:
            self.report({"ERROR"}, "No active project. Create or load a project first.")
            return {"CANCELLED"}

        if not self.entity_name.strip():
            self.report({"ERROR"}, "Name cannot be empty.")
            return {"CANCELLED"}

        if self.context_type == "ASSET":
            steps = [s for i, s in enumerate(ASSET_STEPS) if self.asset_steps[i]]
            result = create_asset(project_path, self.entity_name, steps)

        elif self.context_type == "SHOT":
            steps = [s for i, s in enumerate(SHOT_STEPS) if self.shot_steps[i]]
            result = create_shot(project_path, self.entity_name, steps)

        else:
            steps = [s for i, s in enumerate(SET_STEPS) if self.set_steps[i]]
            result = create_set(project_path, self.entity_name, steps)

        if not result["success"]:
            self.report({"ERROR"}, result["message"])
            return {"CANCELLED"}

        # Update scene context
        scene.ylos_current_asset = self.entity_name
        scene.ylos_context_type  = self.context_type
        if self.context_type == "ASSET":
            scene.ylos_asset_type = self.asset_type

        self.report({"INFO"}, result["message"])
        return {"FINISHED"}
