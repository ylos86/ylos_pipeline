# -*- coding: utf-8 -*-
# Exports current step to USD via create_project.py's two-phase contract (allocate_publish_
# version/finalize_publish_version, kind=<step>) - single source of truth, thumbnail required.

import bpy
import os
import sys
from bpy.props import BoolProperty, EnumProperty
from ..core.asset import get_latest_publish_version, list_publish_versions
from ..core.project import is_step_valid_for_context
from ..core import vocab
from ..core.scene_checker import get_asset_objects_for_publish
from ..core.thumbnails import render_publish_thumbnail

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


def _fallback_objects(scene):
    """Objets pour le thumbnail quand aucun objet d'asset n'a ete resolu (fallback
    full-scene) - le thumbnail est requis meme dans ce cas."""
    return [o for o in scene.objects if o.type in ("MESH", "ARMATURE", "CURVE") and not o.hide_get()]


def _usd_export(filepath: str, context, objects: list) -> tuple:
    """
    Export USD to an exact filepath (staging_dir target, cf. execute()).
    Returns (success, error_message).
    """
    scene = context.scene
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
                return True, ""
            except Exception as e:
                return False, str(e)

        try:
            bpy.ops.wm.usd_export(filepath=filepath)
            return True, ""
        except Exception as e:
            return False, str(e)

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

    # Round-trip avec scene.ylos_current_step (STEP_ITEMS_ALL) : lu en invoke, ecrit
    # en execute -> meme domaine complet. Le filtrage par famille reste assure a
    # l'execution par is_step_valid_for_context (garde semantique conservee).
    step: EnumProperty(
        name="Step",
        items=vocab.STEP_ITEMS_ALL,
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

        objects = []
        if asset_name:
            objects, method = get_asset_objects_for_publish(scene, asset_name, step)
        else:
            method = "full scene"

        if asset_name and not objects and not self.allow_full_scene:
            self.report(
                {"ERROR"},
                f"USD export aborted: no objects resolved for asset '{asset_name}' (step "
                f"'{step}'). Expected a collection named '{asset_name}' or objects named "
                f"GEO_{asset_name}_*.",
            )
            return {"CANCELLED"}

        cp = _cp()
        try:
            staging_dir, final_dir = cp.allocate_publish_version(
                project_path, asset_name, comment="", kind=step,
            )
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        version = cp.publish_version_from_dir(final_dir)
        stem = f"{asset_name}_{step}_v{version:03d}"
        usd_path = os.path.join(str(staging_dir), stem + ".usd")

        ok, err = _usd_export(usd_path, context, objects)
        if not ok:
            self.report(
                {"ERROR"},
                f"USD export failed: {err} (staging preserved: {staging_dir})",
            )
            return {"CANCELLED"}

        thumb_objects = objects or _fallback_objects(scene)
        thumb = render_publish_thumbnail(thumb_objects, str(staging_dir))
        if not thumb:
            self.report(
                {"WARNING"},
                f"Thumbnail render failed - publish will be rejected (staging preserved: {staging_dir})",
            )

        try:
            info = cp.finalize_publish_version(
                project_path, asset_name, staging_dir, final_dir, version,
                expected_artifacts=[stem, "thumb.png"],
            )
        except Exception as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}

        pub_path = os.path.join(info["final_dir"], stem + ".usd")
        scene.ylos_current_step = step

        self.report(
            {"INFO"},
            f"Published: {os.path.basename(pub_path)}  v{info['version']:03d}  [{method}]",
        )

        if self.load_after:
            try:
                bpy.ops.wm.usd_import(filepath=pub_path)
                self.report({"INFO"}, f"Loaded: {os.path.basename(pub_path)}")
            except Exception as e:
                self.report({"WARNING"}, f"Publish OK - USD import failed: {e}")

        return {"FINISHED"}
