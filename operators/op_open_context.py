# -*- coding: utf-8 -*-
# Ylos Pipeline - operators/op_open_context.py
# Load an existing project from disk and restore scene context.
# Also provides a shortcut to open the WIP or publish folder in the OS file manager.

import bpy
import os
import subprocess
import platform
from bpy.props import StringProperty, BoolProperty
from ..core.project import (
    load_project,
    apply_scene_preset,
    find_project_root,
    PIPELINE_DIR,
    PROJECT_CONFIG_FILE,
)


def _sanitize_path(raw: str) -> str:
    """
    Clean a path coming from Blender's file browser / a pasted string.

    Strips surrounding whitespace and the trailing '@' that the inline
    DIR_PATH widget can append on macOS. Resolves Blender's '//' relative
    prefix and a trailing separator.
    """
    if not raw:
        return ""
    p = raw.strip().rstrip("@").strip()
    if p.startswith("//"):
        p = bpy.path.abspath(p)
    return os.path.normpath(p)


def _resolve_project_dir(path: str) -> str | None:
    """
    Given a folder the user navigated to, return the actual project root.

    Accepts:
      - the project folder itself (has _pipeline/project.json)
      - any folder inside the project (walk up via find_project_root)
      - the parent folder that contains exactly one project (walk down once)
    Returns the project root path, or None if nothing valid is found.
    """
    if not path or not os.path.isdir(path):
        return None

    # Case 1 + 2: this folder, or an ancestor, is a project root.
    root = find_project_root(path)
    if root:
        return root

    # Case 3: user stopped on the parent - look one level down.
    matches = []
    try:
        for name in sorted(os.listdir(path)):
            sub = os.path.join(path, name)
            if os.path.isdir(sub) and os.path.isfile(
                os.path.join(sub, PIPELINE_DIR, PROJECT_CONFIG_FILE)
            ):
                matches.append(sub)
    except OSError:
        return None

    # Only auto-pick when unambiguous.
    return matches[0] if len(matches) == 1 else None


class YLOS_OT_OpenContext(bpy.types.Operator):
    """Load an existing Ylos project and restore scene context."""
    bl_idname = "ylos.open_context"
    bl_label = "Load Project"
    bl_description = "Browse to an existing Ylos project folder and load it"
    bl_options = {"REGISTER"}

    # Filled by Blender's native file browser (fileselect_add).
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN"})
    filter_folder: BoolProperty(default=True, options={"HIDDEN"})

    def invoke(self, context, event):
        # Open the native file browser in folder-select mode. This avoids the
        # inline DIR_PATH widget (macOS trailing '@') while letting the user
        # navigate instead of pasting a path.
        if context.scene.ylos_project_path:
            self.directory = context.scene.ylos_project_path
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        path = _sanitize_path(self.directory)
        if not path:
            self.report({"ERROR"}, "No folder selected.")
            return {"CANCELLED"}

        project_path = _resolve_project_dir(path)
        if project_path is None:
            self.report(
                {"ERROR"},
                "No Ylos project here. Navigate into the project folder "
                "(the one containing _pipeline/project.json) and try again.",
            )
            return {"CANCELLED"}

        config = load_project(project_path)
        if config is None:
            self.report({"ERROR"}, f"Could not read project.json in: {project_path}")
            return {"CANCELLED"}

        scene = context.scene
        scene.ylos_project_path = project_path
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
