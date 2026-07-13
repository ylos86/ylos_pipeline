# -*- coding: utf-8 -*-
import bpy
import os
import sys
from bpy.props import StringProperty, EnumProperty
from ..core.project import apply_scene_preset, setup_scene_collections
from ..core import vocab

# Inject REPO_ROOT (where create_project.py lives) into sys.path
REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


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

    # Vocabulaire = create_project via core/vocab.py (seul home). Ecrit dans
    # scene.ylos_prod_type (meme PROD_TYPE_ITEMS) et passe a create_project.create().
    prod_type: EnumProperty(
        name="Production Type",
        items=vocab.PROD_TYPE_ITEMS,
        default="FILM",
    )

    def invoke(self, context, event):
        if not self.root_path and bpy.data.filepath:
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
            preview = os.path.join(self.root_path, self.project_name)
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

        try:
            cp = _cp()
            info = cp.create(
                self.project_name,
                root=self.root_path,
                prod_type=self.prod_type,
            )
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        project_path = info["source"]

        scene = context.scene
        scene.ylos_project_path = project_path
        scene.ylos_project_name = self.project_name
        scene.ylos_prod_type    = self.prod_type

        apply_scene_preset(scene, self.prod_type)
        setup_scene_collections(scene)

        scene.name = f"SCENE_{self.project_name}"

        self.report({"INFO"}, f"Project '{self.project_name}' created at {project_path}")
        return {"FINISHED"}
