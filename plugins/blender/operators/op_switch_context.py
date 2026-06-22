# -*- coding: utf-8 -*-
import bpy
from bpy.props import StringProperty, EnumProperty, BoolProperty


class YLOS_OT_SwitchAsset(bpy.types.Operator):
    bl_idname = "ylos.switch_asset_confirm"
    bl_label = "Switch Asset"
    bl_description = "Switch active asset with unsaved-changes warning"
    bl_options = {"REGISTER"}

    new_asset: StringProperty(name="Asset Name", default="")
    confirmed: BoolProperty(default=False, options={"HIDDEN"})

    def invoke(self, context, event):
        scene = context.scene

        if not self.new_asset.strip():
            self.report({"ERROR"}, "Asset name cannot be empty.")
            return {"CANCELLED"}

        if self.new_asset == scene.ylos_current_asset:
            return {"CANCELLED"}

        if bpy.data.is_dirty and not self.confirmed:
            return context.window_manager.invoke_props_dialog(self, width=400)

        return self.execute(context)

    def draw(self, context):
        scene = context.scene
        layout = self.layout

        col = layout.column(align=True)
        col.label(text="Unsaved changes in current file.", icon="ERROR")
        col.separator()
        col.label(
            text=f"Current : {scene.ylos_current_asset}  /  {scene.ylos_current_step}",
            icon="FILE_BLEND",
        )
        col.label(text=f"Switch to : {self.new_asset}", icon="FORWARD")
        col.separator()
        col.label(text="Unsaved WIP will NOT be saved automatically.")
        col.label(text="Click OK to switch anyway, or Cancel to go back.")

    def execute(self, context):
        context.scene.ylos_current_asset = self.new_asset
        self.report({"INFO"}, f"Switched to asset: {self.new_asset}")
        return {"FINISHED"}


class YLOS_OT_SwitchStep(bpy.types.Operator):
    bl_idname = "ylos.switch_step_confirm"
    bl_label = "Switch Step"
    bl_description = "Switch production step with unsaved-changes warning"
    bl_options = {"REGISTER"}

    new_step: EnumProperty(
        name="Step",
        items=[
            ("modeling",   "Modeling",   ""),
            ("rigging",    "Rigging",    ""),
            ("lookdev",    "LookDev",    ""),
            ("fx",         "FX",         ""),
            ("layout",     "Layout",     ""),
            ("animation",  "Animation",  ""),
            ("lighting",   "Lighting",   ""),
            ("render",     "Render",     ""),
            ("composite",  "Composite",  ""),
        ],
        default="modeling",
    )

    confirmed: BoolProperty(default=False, options={"HIDDEN"})

    def invoke(self, context, event):
        self.new_step = context.scene.ylos_current_step

        if bpy.data.is_dirty and not self.confirmed:
            return context.window_manager.invoke_props_dialog(self, width=400)

        return self.execute(context)

    def draw(self, context):
        scene = context.scene
        layout = self.layout

        col = layout.column(align=True)
        col.label(text="Unsaved changes in current file.", icon="ERROR")
        col.separator()
        col.label(text=f"Asset : {scene.ylos_current_asset}", icon="OBJECT_DATA")
        col.label(text=f"Current step : {scene.ylos_current_step}", icon="FILE_BLEND")
        col.separator()
        col.prop(self, "new_step", text="Switch to")
        col.separator()
        col.label(text="Unsaved WIP will NOT be saved automatically.")
        col.label(text="Click OK to switch anyway, or Cancel to go back.")

    def execute(self, context):
        old = context.scene.ylos_current_step
        context.scene.ylos_current_step = self.new_step
        if old != self.new_step:
            self.report({"INFO"}, f"Step: {old} -> {self.new_step}")
        return {"FINISHED"}
