# -*- coding: utf-8 -*-
# Ylos Pipeline - operators/op_load_publish.py
# Load a published USD (any version / variant) into the current scene.

import bpy
import os
from bpy.props import StringProperty, EnumProperty, BoolProperty
from ylos_core.asset import list_publish_versions, get_latest_publish_path


# ---------------------------------------------------------------------------
# Load a specific publish (called from the version/variant picker)
# ---------------------------------------------------------------------------

class YLOS_OT_LoadPublishFile(bpy.types.Operator):
    """Import a published USD file into the current scene."""
    bl_idname = "ylos.load_publish_file"
    bl_label = "Load USD"
    bl_options = {"REGISTER", "UNDO"}

    filepath: StringProperty(default="")

    def execute(self, context):
        if not self.filepath or not os.path.isfile(self.filepath):
            self.report({"ERROR"}, f"File not found: {self.filepath}")
            return {"CANCELLED"}

        try:
            bpy.ops.wm.usd_import(filepath=self.filepath)
        except Exception as e:
            self.report({"ERROR"}, f"USD import failed: {e}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Loaded: {os.path.basename(self.filepath)}")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Load latest published USD (one-click)
# ---------------------------------------------------------------------------

class YLOS_OT_LoadLatestPublish(bpy.types.Operator):
    """Load the latest published USD for the active asset/step into the scene."""
    bl_idname = "ylos.load_latest_publish"
    bl_label = "Load Latest Publish"
    bl_description = "Import the latest published USD into the current scene"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        if not scene.ylos_project_path or not scene.ylos_current_asset:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}

        path = get_latest_publish_path(
            scene.ylos_project_path,
            scene.ylos_current_asset,
            scene.ylos_current_step,
            scene.ylos_context_type.lower(),
        )

        if not path:
            self.report({"WARNING"}, "No published USD found for this asset/step.")
            return {"CANCELLED"}

        try:
            bpy.ops.wm.usd_import(filepath=path)
        except Exception as e:
            self.report({"ERROR"}, f"USD import failed: {e}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Loaded: {os.path.basename(path)}")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Version + variant picker
# ---------------------------------------------------------------------------

class YLOS_OT_LoadPublish(bpy.types.Operator):
    """Browse published USD versions and variants, then load into scene."""
    bl_idname = "ylos.load_publish"
    bl_label = "Load Published USD"
    bl_description = "Pick a version and variant of the published USD to load"
    bl_options = {"REGISTER", "UNDO"}

    _publishes = []   # list of dicts from list_publish_versions

    def invoke(self, context, event):
        scene = context.scene
        if not scene.ylos_project_path or not scene.ylos_current_asset:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}

        self._publishes = list_publish_versions(
            scene.ylos_project_path,
            scene.ylos_current_asset,
            scene.ylos_current_step,
            scene.ylos_context_type.lower(),
        )

        if not self._publishes:
            self.report({"WARNING"}, "No published USD files found.")
            return {"CANCELLED"}

        return context.window_manager.invoke_popup(self, width=460)

    def draw(self, context):
        scene  = context.scene
        layout = self.layout

        # Header
        box = layout.box()
        row = box.row()
        row.label(
            text=f"{scene.ylos_current_asset}  /  {scene.ylos_current_step}",
            icon="EXPORT",
        )
        row.label(text=f"{len(self._publishes)} publish(es)")

        layout.separator(factor=0.5)

        # Group by version
        versions_seen = {}
        for p in reversed(self._publishes):
            v = p["version"]
            if v not in versions_seen:
                versions_seen[v] = []
            versions_seen[v].append(p)

        for ver, items in versions_seen.items():
            ver_box = layout.box()
            ver_col = ver_box.column(align=True)

            # Version header
            ver_row = ver_col.row()
            ver_row.label(text=f"v{ver:03d}", icon="FILE")
            if ver == max(versions_seen.keys()):
                ver_row.label(text="latest")

            ver_col.separator(factor=0.3)

            # One row per variant
            for item in items:
                item_row = ver_col.row(align=True)
                item_row.label(
                    text=item["variant"],
                    icon="CHECKMARK" if item["variant"] == "Default" else "NONE",
                )
                item_row.label(text=os.path.basename(item["filename"]))
                op = item_row.operator(
                    "ylos.load_publish_file",
                    text="Load",
                    icon="IMPORT",
                )
                op.filepath = item["path"]

    def execute(self, context):
        return {"FINISHED"}
