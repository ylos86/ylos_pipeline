# -*- coding: utf-8 -*-
# Ylos Pipeline - operators/op_open_context.py
# Load an existing project from disk and restore scene context.
# Also provides a shortcut to open the WIP or publish folder in the OS file manager.

import bpy
import os
import subprocess
import platform
from bpy.props import StringProperty, EnumProperty
from ..core.project import load_project, apply_scene_preset


class YLOS_OT_OpenContext(bpy.types.Operator):
    """Load an existing Ylos project and restore scene context."""
    bl_idname = "ylos.open_context"
    bl_label = "Load Project"
    bl_description = "Load an existing Ylos project from a project.json"
    bl_options = {"REGISTER"}

    project_path: StringProperty(
        name="Project Path",
        description="Path to the YLOS_ProjectName folder",
        default="",
        subtype="NONE",   # DIR_PATH corrupts macOS paths (trailing '@')
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=400)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(self, "project_path")

    def execute(self, context):
        if not self.project_path.strip():
            self.report({"ERROR"}, "Please specify a project path.")
            return {"CANCELLED"}

        config = load_project(self.project_path)
        if config is None:
            self.report({"ERROR"}, f"No valid project.json found in: {self.project_path}")
            return {"CANCELLED"}

        scene = context.scene
        scene.ylos_project_path = self.project_path
        scene.ylos_project_name = config["project"]["name"]
        scene.ylos_prod_type    = config["project"]["prod_type"]

        apply_scene_preset(scene, config["project"]["prod_type"])

        self.report({"INFO"}, f"Project loaded: {config['project']['name']}")
        return {"FINISHED"}


class YLOS_OT_OpenFolder(bpy.types.Operator):
    """Open a pipeline folder in the OS file manager."""
    bl_idname = "ylos.open_folder"
    bl_label = "Open Folder"
    bl_description = "Open this folder in your file manager"
    bl_options = {"REGISTER"}

    folder_path: StringProperty(
        name="Folder Path",
        default="",
    )

    def execute(self, context):
        path = self.folder_path
        if not path or not os.path.isdir(path):
            self.report({"WARNING"}, f"Folder not found: {path}")
            return {"CANCELLED"}

        system = platform.system()
        try:
            if system == "Windows":
                os.startfile(path)
            elif system == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            self.report({"ERROR"}, f"Could not open folder: {e}")
            return {"CANCELLED"}

        return {"FINISHED"}
