# -*- coding: utf-8 -*-
# Exports current step to USD via create_project.publish_asset() (single source of truth).

import bpy
import os
import sys
import tempfile
from bpy.props import BoolProperty, EnumProperty
from ..core.asset import get_latest_publish_version, list_publish_versions
from ..core.project import is_step_valid_for_context
from ..core.scene_checker import get_asset_objects_for_publish

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


def _usd_export(filepath: str, context,
                asset_name: str = "", step: str = "",
                allow_full_scene: bool = False) -> tuple:
    """
    Export USD for the current asset.
    Returns (success, error_message, method_used).
    """
    scene   = context.scene
    objects = []
    method  = "full scene"

    if asset_name:
        objects, method = get_asset_objects_for_publish(scene, asset_name, step)

    if asset_name and not objects and not allow_full_scene:
        return (
            False,
            (f"No objects resolved for asset '{asset_name}' (step '{step}'). "
             f"Expected a collection named '{asset_name}' or objects named "
             f"GEO_{asset_name}_*. Aborting to avoid publishing the full scene."),
            "none",
        )

    prev_selected = [o for o in scene.objects if o.select_get()]
    prev_active   = context.view_layer.objects.active

    try:
        if objects:
            for o in scene.objects:
                o.select_set(False)
            for o in objects:
                o.select_set(True)
            context.view_layer.objects.active = objects[0]
            try:
                bpy.ops.wm.usd_export(filepath=filepath, selected_objects_only=True)
                return True, "", method
            except Exception as e:
                return False, str(e), method

        try:
            bpy.ops.wm.usd_export(filepath=filepath)
            return True, "", "full scene"
        except Exception as e:
            return False, str(e), "full scene"

    finally:
        for o in scene.objects:
            o.select_set(False)
        for o in prev_selected:
            o.select_set(True)
        context.view_layer.objects.active = prev_active


class YLOS_OT_Publish(bpy.types.Operator):
    bl_idname  = "ylos.publish"
    bl_label   = "Publish Step"
    bl_description = "Export current step to USD and update the asset root composition"
    bl_options = {"REGISTER"}

    load_after: BoolProperty(
        name="Load in Scene",
        description="Import the published USD into the current scene after export",
        default=False,
    )

    allow_full_scene: BoolProperty(
        name="Allow Full-Scene Export",
        description="If no asset objects are resolved, export the whole scene instead of aborting",
        default=False,
    )

    step: EnumProperty(
        name="Step",
        items=[
            ("modeling",  "Modeling",  ""),
            ("rigging",   "Rigging",   ""),
            ("lookdev",   "LookDev",   ""),
            ("fx",        "FX",        ""),
            ("layout",    "Layout",    ""),
            ("animation", "Animation", ""),
            ("lighting",  "Lighting",  ""),
            ("render",    "Render",    ""),
            ("composite", "Composite", ""),
        ],
        default="modeling",
    )

    _next_ver: int = 1  # display-only, computed in invoke

    def invoke(self, context, event):
        scene = context.scene
        if not scene.ylos_project_path or not scene.ylos_current_asset:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}

        self.step = scene.ylos_current_step
        latest = get_latest_publish_version(
            scene.ylos_project_path,
            scene.ylos_current_asset,
            self.step,
            scene.ylos_context_type.lower(),
        )
        self._next_ver = latest + 1
        return context.window_manager.invoke_props_dialog(self, width=380)

    def draw(self, context):
        scene  = context.scene
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.label(text=f"Asset: {scene.ylos_current_asset}", icon="OBJECT_DATA")
        layout.separator()
        layout.prop(self, "step")
        layout.separator()
        layout.prop(self, "load_after")
        layout.prop(self, "allow_full_scene")

        box = layout.box()
        box.label(text="Publish to:", icon="EXPORT")
        box.label(
            text=f"{scene.ylos_current_asset}_{self.step}_v{self._next_ver:03d}.usd"
        )
        box.label(text="Version assigned by create_project.py", icon="INFO")

    def execute(self, context):
        scene        = context.scene
        project_path = scene.ylos_project_path
        asset_name   = scene.ylos_current_asset
        ctx_type     = scene.ylos_context_type.lower()
        step         = self.step

        if not project_path or not asset_name:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}

        if not is_step_valid_for_context(step, ctx_type):
            self.report(
                {"ERROR"},
                f"Step '{step}' is not valid for a {ctx_type}.",
            )
            return {"CANCELLED"}

        # Export USD to a temp file, then publish via create_project.py
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".usd")
        os.close(tmp_fd)

        try:
            ok, err, method = _usd_export(
                tmp_path, context, asset_name, step,
                allow_full_scene=self.allow_full_scene,
            )
            if not ok:
                self.report({"ERROR"}, f"USD export failed: {err}")
                return {"CANCELLED"}

            cp   = _cp()
            info = cp.publish_asset(project_path, asset_name, step, tmp_path)

        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        pub_path = info["publish_path"]
        scene.ylos_current_step = step

        self.report(
            {"INFO"},
            f"Published: {os.path.basename(pub_path)}  v{info['version']:03d}  [{method}]",
        )

        if info.get("asset_root"):
            self.report({"INFO"}, f"asset_root.usda updated: {os.path.basename(info['asset_root'])}")

        if self.load_after:
            try:
                bpy.ops.wm.usd_import(filepath=pub_path)
                self.report({"INFO"}, f"Loaded: {os.path.basename(pub_path)}")
            except Exception as e:
                self.report({"WARNING"}, f"Publish OK - USD import failed: {e}")

        return {"FINISHED"}
