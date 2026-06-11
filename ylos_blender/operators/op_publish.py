# -*- coding: utf-8 -*-
# Ylos Pipeline - operators/op_publish.py
# Exports the current step to USD and updates the entity root composition.
#
# Phase 3 additions:
#   - Sidecar .manifest.json written after every successful publish (arch doc S-5).
#   - Prim stability check before modeling publish N>1 (arch doc S-4):
#     removed prims trigger a blocking-confirmable warning.
#   - FX step uses animated USD export with explicit frame range.
#   - No silent full-scene fallback: abort if objects cannot be resolved.
#
# C2: publishes are immutable -- execute() finds the first free version slot.
# C4: step_owner warning when publishing a step owned by another DCC.

import bpy
import os
from bpy.props import IntProperty, BoolProperty, EnumProperty, StringProperty
from ylos_core.asset import (
    resolve_publish_path,
    get_latest_publish_version,
    list_publish_versions,
)
from ylos_core.project import is_step_valid_for_context, load_project, get_step_owner
from ylos_core.usd_composer import compose_asset_root, compose_set_root, FX_STEP
from ylos_core.manifest import (
    write_publish_sidecar,
    find_removed_prims,
    sidecar_path,
)
from ..core_bpy.scene_checker import get_asset_objects_for_publish


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_prim_paths(objects: list, entity_name: str) -> list:
    """
    Derive expected USD prim paths from a list of Blender objects.
    Convention: /ROOT/{entity_name}/{obj.name}
    Only MESH, ARMATURE, CURVE included. Paths sorted for stable diff.
    """
    included = {"MESH", "ARMATURE", "CURVE"}
    return sorted(
        f"/ROOT/{entity_name}/{obj.name}"
        for obj in objects
        if obj.type in included
    )


def _resolve_objects(context, asset_name: str, step: str) -> tuple:
    """
    Return (objects, method_str, error_msg).
    error_msg non-empty only when no objects found for a targeted asset.
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


def _do_usd_export(filepath: str, context, objects: list, step: str,
                   export_animation: bool = False,
                   frame_start: int = 1,
                   frame_end: int = 250) -> tuple:
    """
    Select objects, call bpy.ops.wm.usd_export, restore selection.
    Returns (success: bool, error_message: str).
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
    Returns list of removed prim paths. Only meaningful for modeling step, N>1.
    """
    if step != "modeling" or ctx_type != "asset":
        return []
    prev_version = get_latest_publish_version(project_path, asset_name, step, ctx_type)
    if prev_version == 0:
        return []
    prev_path = resolve_publish_path(
        project_path, asset_name, step, prev_version, "usd", ctx_type
    )
    return find_removed_prims(prev_path, _get_prim_paths(objects, asset_name))


def _find_free_version(project_path: str, asset_name: str, step: str,
                       ctx_type: str, start_version: int,
                       variant_name: str) -> int:
    """
    Starting from start_version, return the first version for which neither
    the USD publish nor its sidecar exists. Implements C2 immutability.
    """
    v = start_version
    while v <= 999:
        pp = resolve_publish_path(project_path, asset_name, step, v, "usd",
                                  ctx_type, variant_name)
        if not os.path.exists(pp) and not os.path.exists(sidecar_path(pp)):
            return v
        v += 1
    return -1  # no free slot


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
        description="Publish version number (starting point; auto-increments if slot occupied)",
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

    # FX animation range
    fx_frame_start: IntProperty(name="Frame Start", description="First frame for FX cache", default=1)
    fx_frame_end:   IntProperty(name="Frame End",   description="Last frame for FX cache",  default=250)

    # Prim stability state (arch doc S-4)
    stability_message: StringProperty(
        name="Stability Warning", default="", options={"HIDDEN"},
    )
    confirm_stability: BoolProperty(
        name="Proceed despite missing prims",
        description=(
            "Publish anyway knowing that Houdini lookdev overs targeting "
            "the removed prims will be broken until lookdev is republished."
        ),
        default=False,
    )

    # Step-owner warning (arch doc S-2.1 / C4)
    step_owner: StringProperty(
        name="Step Owner", default="any", options={"HIDDEN"},
    )
    confirm_foreign_step: BoolProperty(
        name="Publish anyway (step owned by another DCC)",
        description="This step is assigned to another DCC. Publish from Blender as a solo override.",
        default=False,
    )

    # ---------------------------------------------------------------------------
    def invoke(self, context, event):
        scene    = context.scene
        ctx_type = scene.ylos_context_type.lower()

        if not scene.ylos_project_path or not scene.ylos_current_asset:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}

        self.step = scene.ylos_current_step

        self.version = get_latest_publish_version(
            scene.ylos_project_path, scene.ylos_current_asset,
            self.step, ctx_type,
        ) + 1

        # FX frame range from scene
        self.fx_frame_start = scene.frame_start
        self.fx_frame_end   = scene.frame_end

        # Step-owner check (C4)
        self.confirm_foreign_step = False
        config = load_project(scene.ylos_project_path)
        self.step_owner = get_step_owner(config, self.step) if config else "any"

        # Prim stability check for modeling N>1 (arch doc S-4)
        self.stability_message = ""
        self.confirm_stability  = False
        if self.step == "modeling" and ctx_type == "asset" and self.version > 1:
            objects, _, err = _resolve_objects(context, scene.ylos_current_asset, "modeling")
            if not err and objects:
                removed = _run_stability_check(
                    scene.ylos_project_path, scene.ylos_current_asset,
                    "modeling", ctx_type, objects,
                )
                if removed:
                    self.stability_message = "\n".join(removed)

        return context.window_manager.invoke_props_dialog(self, width=430)

    # ---------------------------------------------------------------------------
    def draw(self, context):
        scene    = context.scene
        ctx_type = scene.ylos_context_type.lower()
        layout   = self.layout
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
            box.label(text="Exported as animated USD payload (deferred load).", icon="INFO")

        layout.separator()
        layout.prop(self, "update_root")
        layout.prop(self, "load_after")

        # Step-owner warning (C4)
        if self.step_owner not in ("blender", "any"):
            box = layout.box()
            box.label(text=f"Step '{self.step}' is owned by '{self.step_owner}'", icon="ERROR")
            box.label(text="Publishing from Blender overrides the DCC assignment.")
            box.prop(self, "confirm_foreign_step")

        # Prim stability warning (arch doc S-4)
        if self.stability_message:
            layout.separator()
            box = layout.box()
            col = box.column(align=True)
            col.label(text="PRIM STABILITY WARNING", icon="ERROR")
            col.label(text="These prims were in the previous publish but are now missing:")
            lines = self.stability_message.splitlines()
            for path in lines[:8]:
                col.label(text=f"  {path}", icon="DOT")
            if len(lines) > 8:
                col.label(text=f"  ...and {len(lines) - 8} more")
            col.separator()
            col.label(text="Houdini lookdev overs targeting these prims will be broken.", icon="ORPHAN_DATA")
            box.prop(self, "confirm_stability")

        # Publish preview (C2: show info if slot occupied)
        vname    = self.variant_name or "Default"
        pub_path = resolve_publish_path(
            scene.ylos_project_path, scene.ylos_current_asset,
            self.step, self.version, "usd", ctx_type, self.variant_name,
        )
        box = layout.box()
        box.label(text="Publish to:", icon="EXPORT")
        box.label(text=os.path.basename(pub_path))

        existing = [
            (v["version"], v.get("variant", "Default"))
            for v in list_publish_versions(
                scene.ylos_project_path, scene.ylos_current_asset,
                self.step, ctx_type,
            )
        ]
        if (self.version, vname) in existing:
            free = _find_free_version(
                scene.ylos_project_path, scene.ylos_current_asset,
                self.step, ctx_type, self.version, self.variant_name,
            )
            if free > 0 and free != self.version:
                box.label(
                    text=f"v{self.version:03d} exists -- will publish as v{free:03d}",
                    icon="INFO",
                )

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
                f"Step '{step}' is not valid for a {ctx_type}.",
            )
            return {"CANCELLED"}

        # C4: step-owner gate
        if self.step_owner not in ("blender", "any") and not self.confirm_foreign_step:
            self.report(
                {"ERROR"},
                f"Step '{step}' is owned by '{self.step_owner}'. "
                f"Enable 'Publish anyway' in the dialog to override.",
            )
            return {"CANCELLED"}

        # Stability gate (arch doc S-4)
        if self.stability_message and not self.confirm_stability:
            self.report(
                {"ERROR"},
                "Publish blocked: missing prims detected. "
                "Enable 'Proceed despite missing prims' in the dialog to override.",
            )
            return {"CANCELLED"}

        # Resolve objects
        objects, method, resolve_err = _resolve_objects(context, asset_name, step)
        if resolve_err:
            self.report({"ERROR"}, f"USD export aborted: {resolve_err}")
            return {"CANCELLED"}

        # C2: Find first free version slot (immutable publishes)
        original_version = self.version
        free_version = _find_free_version(
            project_path, asset_name, step, ctx_type,
            self.version, self.variant_name,
        )
        if free_version < 0:
            self.report({"ERROR"}, "No free publish slot (v001-v999 all occupied).")
            return {"CANCELLED"}
        if free_version != original_version:
            self.report(
                {"WARNING"},
                f"v{original_version:03d} already exists -- publishing as v{free_version:03d}.",
            )
        self.version = free_version

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

        # Write sidecar (arch doc S-5) -- with C2 guarantee, slot is always free
        prim_paths = _get_prim_paths(objects, asset_name) if objects else []
        try:
            write_publish_sidecar(
                pub_path,
                entity      = asset_name,
                step        = step,
                version     = self.version,
                dcc         = "blender",
                dcc_version = bpy.app.version_string,
                prim_paths  = prim_paths,
                variant     = self.variant_name or None,
                source_wip  = os.path.basename(bpy.data.filepath) if bpy.data.filepath else None,
                frame_range = [self.fx_frame_start, self.fx_frame_end] if is_fx else None,
            )
        except Exception as e:
            self.report({"WARNING"}, f"Publish OK - sidecar write failed: {e}")

        # Load after
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
                result = {"success": True, "message": "Shot -- no root USD to update."}

            if result["success"]:
                self.report({"INFO"}, result["message"])
            else:
                self.report({"WARNING"}, f"Publish OK - root USD failed: {result['message']}")

        return {"FINISHED"}
