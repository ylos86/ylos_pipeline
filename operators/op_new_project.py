# -*- coding: utf-8 -*-
# Ylos Pipeline - operators/op_new_project.py
# Creates a new project on disk and configures the Blender scene.

import bpy
from bpy.props import StringProperty, EnumProperty
from ..core.project import (
    create_project,
    apply_scene_preset,
    setup_scene_collections,
)


class YLOS_OT_NewProject(bpy.types.Operator):
    bl_idname = "ylos.new_project"
    bl_label = "New Project"
    bl_description = "Create a new Ylos project on disk and configure the scene"
    bl_options = {"REGISTER", "UNDO"}

    project_name: StringProperty(
        name="Project Name",
        description="PascalCase, no spaces (e.g. ColonialHouse)",
        default="MyProject",
    )

    root_path: StringProperty(
        name="Root Path",
        description="Parent folder where the project will be created",
        default="",
        subtype="NONE",
    )

    prod_type: EnumProperty(
        name="Production Type",
        items=[
            ("FILM", "Film", "24fps | 2K | Cycles | AgX"),
            ("AR", "AR",   "60fps | Quest res | EEVEE | sRGB"),
            ("VR", "VR",   "90fps | Stereo res | EEVEE | sRGB"),
        ],
        default="FILM",
    )

    def invoke(self, context, event):
        if not self.root_path and bpy.data.filepath:
            import os
            self.root_path = os.path.dirname(bpy.data.filepath)
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        col = layout.column(align=True)
        col.prop(self, "project_name")
        col.prop(self, "root_path")
        col.prop(self, "prod_type")

        if self.root_path and self.project_name:
            import os
            preview = os.path.join(self.root_path, f"YLOS_{self.project_name}")
            box = layout.box()
            box.label(text="Will create:", icon="FOLDER_REDIRECT")
            box.label(text=preview)

    def execute(self, context):
        if not self.project_name.strip():
            self.report({"ERROR"}, "Project name cannot be empty.")
            return {"CANCELLED"}

        if not self.root_path.strip():
            self.report({"ERROR"}, "Root path cannot be empty.")
            return {"CANCELLED"}

        if " " in self.project_name:
            self.report({"ERROR"}, "Project name must not contain spaces. Use PascalCase.")
            return {"CANCELLED"}

        result = create_project(self.root_path, self.project_name, self.prod_type)

        if not result["success"]:
            self.report({"ERROR"}, result["message"])
            return {"CANCELLED"}

        scene = context.scene
        scene.ylos_project_path = result["project_path"]
        scene.ylos_project_name = self.project_name
        scene.ylos_prod_type = self.prod_type

        apply_scene_preset(scene, self.prod_type)
        setup_scene_collections(scene)

        scene.name = f"SCENE_{self.project_name}"

        self.report({"INFO"}, result["message"])
        return {"FINISHED"}
