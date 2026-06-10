# -*- coding: utf-8 -*-
# Ylos Pipeline - operators/op_publish.py
# Exports the current step to USD and updates the entity root composition.
#
# Phase 3 additions:
#   - Sidecar .manifest.json written after every successful publish (§5).
#   - Prim stability check before modeling publish N>1 (§4):
#     removed prims trigger a blocking-confirmable warning.
#   - FX step uses animated USD export with explicit frame range.
#   - all_objects safety rule preserved: no silent full-scene fallback.

import bpy
import os
from bpy.props import IntProperty, BoolProperty, EnumProperty, StringProperty
from ylos_core.asset import (
    resolve_publish_path,
    get_latest_publish_version,
    list_publish_versions,
)
from ylos_core.project import is_step_valid_for_context
from ylos_core.usd_composer import compose_asset_root, compose_set_root, FX_STEP
from ylos_core.manifest import write_publish_sidecar, find_removed_prims
from ..core_bpy.scene_checker import get_asset_objects_for_publish


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_prim_paths(objects: list, entity_name: str) -> list:
    """
    Derive expected USD prim paths from a list of Blender objects.
    Convention: /ROOT/{entity_name}/{obj.name}
    Only MESH, ARMATURE, CURVE objects are included (not EMPTY, LATTICE).
    Paths are sorted for stable sidecar comparison.
    """
    included = {"MESH", "ARMATURE", "CURVE"}
    return sorted(
        f"/ROOT/{entity_name}/{obj.name}"
        for obj in objects
        if obj.type in included
    )


def _resolve_objects(context, asset_name: str,
                     step: str) -> tuple:
    """
    Return (objects, method_str, error_msg).
    error_msg is non-empty only on hard failure (no objects found for a
    targeted asset with no allow_full_scene override).
    """
    if not asset_name:
        return [], "full scene", ""

    objects, method = get_asset_objects_for_publish(context.scene, asset_name, step)

    if not objects:
        return [], "none", (
            f"No objects resolved for asset '{asset_name}' (step '{step}'). "
            f"Expected a collection named '{asset_name}' or objects named "
            f"GEO_{asset_name}_*. Aborting to avoid publishing the full scene."
        )

    return objects, method, ""


def _do_usd_export(filepath: str, context, objects: list,
                   step: str,
                   export_animation: bool = False,
                   frame_start: int = 1,
                   frame_end: int = 250) -> tuple:
    """
    Select objects and call bpy.ops.wm.usd_export.

    Returns (success: bool, error_message: str).
    Never falls back to full-scene export silently.
    """
    scene = context.scene

    # Save + restore selection
    prev_selected = [o for o in scene.objects if o.select_get()]
    prev_active   = context.view_layer.objects.active

    try:
        if objects:
            for o in scene.objects:
                o.select_set(False)
            for o in objects:
                o.select_set(True)
            context.view_layer.objects.active = objects[0]

        kwargs = dict(filepath=filepath)
        if objects:
            kwargs["selected_objects_only"] = True
        if export_animation:
            kwargs["export_animation"] = True
            kwargs["start_frame"] = frame_start
            kwargs["end_frame"]   = frame_end

        try:
            bpy.ops.wm.usd_export(**kwargs)
            return True, ""
        except RuntimeError as e:
            return False, str(e)
        except Exception as e:
            return False, str(e)

    finally:
        for o in scene.objects:
            o.select_set(False)
        for o in prev_selected:
            o.select_set(True)
        context.view_layer.objects.active = prev_active


def _run_stability_check(project_path: str, asset_name: str,
                         step: str, ctx_type: str,
                         objects: list) -> list:
    """
    Compare prim paths about to be exported against the previous publish sidecar.
    Returns list of removed prim paths (empty = clean).

    Only meaningful for modeling step with an existing previous publish.
    """
    if step != "modeling" or ctx_type != "asset":
        return []

    prev_version = get_latest_publish_version(
        project_path, asset_name, step, ctx_type
    )
    if prev_version == 0:
        return []   # First publish -- nothing to compare against.

    prev_path = resolve_publish_path(
        project_path, asset_name, step, prev_version, "usd", ctx_type
    )
    new_prims = _get_prim_paths(objects, asset_name)
    return find_removed_prims(prev_path, new_prims)


# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------

class YLOS_OT_Publish(bpy.types.Operator):
    bl_idname  = "ylos.publish"
    bl_label   = "Publish Step"
    bl_description = "Export current step to USD and update the entity root composition"
    bl_options = {"REGISTER"}

    version: IntProperty(
        name="Version",
        description="Publish version number (e.g. 1 = v001)",
        min=1, max=999, default=1,
    )

    update_root: BoolProperty(
        name="Update Root USD",
        description="Recompose asset_root.usd after publish",
        default=True,
    )

    variant_name: StringProperty(
        name="Variant",
        description="Optional variant name (e.g. Dirty, Worn). Empty = default publish.",
        default="",
    )

    load_after: BoolProperty(
        name="Load in Scene",
        description="Import the published USD into the current scene after export",
        default=False,
    )

    step: EnumProperty(
        name="Step",
        description="Production step to publish",
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

    # FX animation range (shown only when step == "fx")
    fx_frame_start: IntProperty(
        name="Frame Start",
        description="First frame to export for FX cache",
        default=1,
    )
    fx_frame_end: IntProperty(
        name="Frame End",
        description="Last frame to export for FX cache",
        default=250,
    )

    # Prim stability state (populated in invoke, checked in execute)
    # Stored as newline-separated paths so they survive the props dialog.
    stability_message: StringProperty(
        name="Stability Warning",
        default="",
        options={"HIDDEN"},
    )
    confirm_stability: BoolProperty(
        name="Proceed despite missing prims",
        description=(
            "Publish anyway knowing that Houdini lookdev overs targeting "
            "the removed prims will be broken until lookdev is republished."
        ),
        default=False,
    )

    # ---------------------------------------------------------------------------
    def invoke(self, context, event):
        scene = context.scene
        if not scene.ylos_project_path or not scene.ylos_current_asset:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}

        self.step = scene.ylos_current_step
        ctx_type  = scene.ylos_context_type.lower()

        self.version = get_latest_publish_version(
            scene.ylos_project_path,
            scene.ylos_current_asset,
            self.step,
            ctx_type,
        ) + 1

        # Pre-populate FX frame range from scene
        self.fx_frame_start = scene.frame_start
        self.fx_frame_end   = scene.frame_end

        # Prim stability check (modeling only, N > 1)
        self.stability_message = ""
        self.confirm_stability = False
        if self.step == "modeling" and ctx_type == "asset" and self.version > 1:
            objects, _, err = _resolve_objects(context, scene.ylos_current_asset, "modeling")
            if not err and objects:
                removed = _run_stability_check(
                    scene.ylos_project_path,
                    scene.ylos_current_asset,
                    "modeling",
                    ctx_type,
                    objects,
                )
                if removed:
                    self.stability_message = "\n".join(removed)

        return context.window_manager.invoke_props_dialog(self, width=420)

    # ---------------------------------------------------------------------------
    def draw(self, context):
        scene  = context.scene
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.label(text=f"Asset: {scene.ylos_current_asset}", icon="OBJECT_DATA")
        layout.separator()
        layout.prop(self, "step")
        layout.prop(self, "version")
        layout.prop(self, "variant_name")

        # FX frame range (only for FX step)
        if self.step == FX_STEP:
            layout.separator()
            box = layout.box()
            box.label(text="FX Cache Range", icon="TIME")
            row = box.row(align=True)
            row.prop(self, "fx_frame_start", text="Start")
            row.prop(self, "fx_frame_end",   text="End")
            box.label(
                text="Exported as animated USD payload (deferred load).",
                icon="INFO"
            )

        layout.separator()
        layout.prop(self, "update_root")
        layout.prop(self, "load_after")

        # Prim stability warning
        if self.stability_message:
            layout.separator()
            box = layout.box()
            col = box.column(align=True)
            col.label(text="PRIM STABILITY WARNING", icon="ERROR")
            col.label(text="These prims were in the previous publish but are now missing:")
            for path in self.stability_message.splitlines()[:8]:
                col.label(text=f"  {path}", icon="DOT")
            if len(self.stability_message.splitlines()) > 8:
                col.label(text=f"  ...and {len(self.stability_message.splitlines()) - 8} more")
            col.separator()
            col.label(
                text="Houdini lookdev overs targeting these prims will be broken.",
                icon="ORPHAN_DATA",
            )
            box.prop(self, "confirm_stability")

        # Publish preview
        vname    = self.variant_name or "Default"
        ctx_type = scene.ylos_context_type.lower()
        pub_path = resolve_publish_path(
            scene.ylos_project_path,
            scene.ylos_current_asset,
            self.step,
            self.version,
            "usd",
            ctx_type,
            self.variant_name,
        )
        box = layout.box()
        box.label(text="Publish to:", icon="EXPORT")
        box.label(text=os.path.basename(pub_path))

        existing = [
            (v["version"], v.get("variant", "Default"))
            for v in list_publish_versions(
                scene.ylos_project_path,
                scene.ylos_current_asset,
                self.step,
                ctx_type,
            )
        ]
        if (self.version, vname) in existing:
            box.label(text="WARNING: will overwrite existing publish", icon="ERROR")

    # ---------------------------------------------------------------------------
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
                f"Step '{step}' is not valid for a {ctx_type}. "
                f"Pick a step that exists for this entity type.",
            )
            return {"CANCELLED"}

        # Stability gate: block if unacknowledged warnings
        if self.stability_message and not self.confirm_stability:
            self.report(
                {"ERROR"},
                "Publish blocked: missing prims detected. "
                "Enable 'Proceed despite missing prims' in the dialog to override."
            )
            return {"CANCELLED"}

        # Resolve objects for export
        objects, method, resolve_err = _resolve_objects(context, asset_name, step)
        if resolve_err:
            self.report({"ERROR"}, f"USD export aborted: {resolve_err}")
            return {"CANCELLED"}

        pub_path = resolve_publish_path(
            project_path, asset_name, step,
            self.version, "usd", ctx_type, self.variant_name,
        )
        os.makedirs(os.path.dirname(pub_path), exist_ok=True)

        # Export
        is_fx = (step == FX_STEP)
        ok, err = _do_usd_export(
            pub_path, context, objects, step,
            export_animation=is_fx,
            frame_start=self.fx_frame_start,
            frame_end=self.fx_frame_end,
        )
        if not ok:
            self.report({"ERROR"}, f"USD export failed: {err}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Published: {os.path.basename(pub_path)} [{method}]")
        scene.ylos_current_step = step

        # Write publish sidecar (§5)
        prim_paths = _get_prim_paths(objects, asset_name) if objects else []
        try:
            write_publish_sidecar(
                pub_path,
                entity       = asset_name,
                step         = step,
                version      = self.version,
                dcc          = "blender",
                dcc_version  = bpy.app.version_string,
                prim_paths   = prim_paths,
                variant      = self.variant_name or None,
                source_wip   = os.path.basename(bpy.data.filepath) if bpy.data.filepath else None,
                frame_range  = [self.fx_frame_start, self.fx_frame_end] if is_fx else None,
            )
        except FileExistsError:
            # Sidecar already exists (republish of same version): skip silently.
            pass
        except Exception as e:
            self.report({"WARNING"}, f"Publish OK - sidecar write failed: {e}")

        # Load after publish
        if self.load_after:
            try:
                bpy.ops.wm.usd_import(filepath=pub_path)
                self.report({"INFO"}, f"Loaded: {os.path.basename(pub_path)}")
            except Exception as e:
                self.report({"WARNING"}, f"Publish OK - USD import failed: {e}")

        # Update root USD
        if self.update_root:
            if ctx_type == "asset":
                result = compose_asset_root(project_path, asset_name)
            elif ctx_type == "set":
                result = compose_set_root(project_path, asset_name)
            else:
                result = {"success": True, "message": "Shot - no root USD to update."}

            if result["success"]:
                self.report({"INFO"}, result["message"])
            else:
                self.report({"WARNING"}, f"Publish OK - root USD failed: {result['message']}")

        return {"FINISHED"}
