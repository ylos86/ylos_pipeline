# -*- coding: utf-8 -*-
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
    if not raw:
        return ""
    p = raw.strip().rstrip("@").strip()
    if p.startswith("//"):
        p = bpy.path.abspath(p)
    return os.path.normpath(p)


def _resolve_project_dir(path: str):
    if not path or not os.path.isdir(path):
        return None

    root = find_project_root(path)
    if root:
        return root

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

    return matches[0] if len(matches) == 1 else None


class YLOS_OT_OpenContext(bpy.types.Operator):
    """Load an existing Ylos project and restore scene context."""
    bl_idname = "ylos.open_context"
    bl_label = "Load Project"
    bl_description = "Browse to an existing Ylos project folder and load it"
    bl_options = {"REGISTER"}

    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN"})
    filter_folder: BoolProperty(default=True, options={"HIDDEN"})

    def invoke(self, context, event):
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

    folder_path: StringProperty(name="Folder Path", default="")

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


class YLOS_OT_ConvertLegacy(bpy.types.Operator):
    """Migrate a legacy project to schema 2.0 via migrate_to_2.0.py."""
    bl_idname = "ylos.convert_legacy"
    bl_label = "Convert Legacy Project"
    bl_description = "Migrate an old project to schema 2.0 (auto-backup, non-destructive)"
    bl_options = {"REGISTER"}

    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN"})
    filter_folder: BoolProperty(default=True, options={"HIDDEN"})

    dry_run: bpy.props.BoolProperty(
        name="Dry Run",
        description="Report only, do not modify anything",
        default=True,
    )

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        import importlib.util
        repo_root = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..")
        )
        migrate_path = os.path.join(repo_root, "migrate_to_2.0.py")
        if not os.path.isfile(migrate_path):
            self.report({"ERROR"}, f"migrate_to_2.0.py not found at: {migrate_path}")
            return {"CANCELLED"}

        project_path = _sanitize_path(self.directory)
        if not project_path:
            self.report({"ERROR"}, "No folder selected.")
            return {"CANCELLED"}

        try:
            spec = importlib.util.spec_from_file_location("ylos_migrate", migrate_path)
            mod  = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            report = mod.migrate(project_path, dry=self.dry_run, backup=True)
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        mode = "DRY-RUN" if self.dry_run else "applied"
        n, r = len(report.get("entities", [])), len(report.get("renames", []))
        self.report({"INFO"}, f"Migration {mode}: {n} entities, {r} renames")
        for w in report.get("warnings", []):
            self.report({"WARNING"}, w)
        return {"FINISHED"}
