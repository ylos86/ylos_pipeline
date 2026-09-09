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
from ..core import thumbnails
from ..core import entity_thumbs

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


def _fallback_objects(scene):
    """Objects for the thumbnail when no asset object was resolved (full-scene
    fallback) - the thumbnail is required even in that case."""
    return [o for o in scene.objects if o.type in ("MESH", "ARMATURE", "CURVE") and not o.hide_get()]


def _normalize_datablock_names(objects):
    """Name hygiene BEFORE export: aligns the datablock name with the object's when
    the datablock is single-user. Without this, an object renamed but whose data keeps
    'Cube.001' exports as USD prim 'Cube_001' / a wrong glTF node -> a Load Latest brings back
    an object no longer carrying the asset's name. A MULTI-user datablock is NEVER renamed
    (the rename would affect the other users) -> collected for a warning, never
    silent. Returns (n_renamed, [descriptions of the untouched shared ones])."""
    renamed = 0
    shared = []
    for obj in objects:
        data = getattr(obj, "data", None)
        if data is None or data.name == obj.name:
            continue
        if getattr(data, "users", 1) == 1:
            data.name = obj.name  # rename permanent (hygiene standard)
            renamed += 1
        else:
            shared.append(f"{obj.name} (data '{data.name}', {data.users} users)")
    return renamed, shared


def _glb_export(filepath: str, context, objects: list) -> tuple:
    """Binary glTF (GLB) export to an exact filepath (staging_dir, see execute()). Mirror of
    _usd_export: selection = gathered objects (use_selection) otherwise the whole scene. +Y up
    by the exporter default (correct for Three.js). Returns (success, error_message)."""
    scene = context.scene
    prev_selected = [o for o in scene.objects if o.select_get()]
    prev_active   = context.view_layer.objects.active
    use_sel = bool(objects)
    try:
        if use_sel:
            for o in scene.objects:
                o.select_set(False)
            for o in objects:
                o.select_set(True)
            context.view_layer.objects.active = objects[0]
        try:
            bpy.ops.export_scene.gltf(
                filepath=filepath,
                export_format='GLB',
                use_selection=use_sel,
                export_apply=True,
            )
            return True, ""
        except Exception as e:
            return False, str(e)
    finally:
        for o in scene.objects:
            o.select_set(False)
        for o in prev_selected:
            o.select_set(True)
        context.view_layer.objects.active = prev_active


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


def publish_entity_step(context, project_path, entity, step, *,
                        allow_full_scene=False, comment="", load_after=False):
    """REUSABLE publish core: the simple button (ylos.publish, current step) AND the
    State Manager batch (ylos.publish_states, stacked states) go through here - single
    logic, principle 5. The entity's family is resolved by create_project.resolve_entity
    (a state can target asset/set/shot, not only the current scene context). NEVER raises
    for a business case: returns a dict
    {ok, version, message, path, method, warning}. Two-phase contract unchanged (allocate ->
    USD/GLB export per target -> thumbnail required -> finalize)."""
    cp = _cp()
    scene = context.scene

    def _fail(msg, method="", warning=""):
        return {"ok": False, "version": 0, "message": msg, "path": "",
                "method": method, "warning": warning}

    if not project_path or not entity:
        return _fail("No active project or entity.")

    resolved = cp.resolve_entity(project_path, entity)
    if resolved is None:
        return _fail(f"Entity '{entity}' not found under project.")
    ctx_type = resolved["family"]

    if not is_step_valid_for_context(step, ctx_type):
        return _fail(f"Step '{step}' is not valid for a {ctx_type}.")

    objects, method = get_asset_objects_for_publish(scene, entity, step)
    if not objects and not allow_full_scene:
        return _fail(
            f"Publish aborted: no objects resolved for '{entity}' (step '{step}'). Expected "
            f"a collection named '{entity}' or objects named GEO_{entity}_*.",
            method=method,
        )

    # Name hygiene BEFORE export: a single-user datablock not renamed ('Cube.001') exports as a
    # wrong USD prim / glTF node. Never silent. Full-scene -> normalizes the whole scene.
    export_objects = objects if objects else list(scene.objects)
    n_renamed, shared = _normalize_datablock_names(export_objects)
    for s in shared:
        print(f"[Ylos publish] shared datablock not renamed (object name kept): {s}")

    # Artifact format = orchestrator decision (pipeline target), never the DCC's.
    target = cp.get_pipeline_target(project_path)
    ext = ".glb" if target == "web" else ".usd"

    try:
        staging_dir, final_dir = cp.allocate_publish_version(
            project_path, entity, comment=comment, kind=step,
        )
    except Exception as e:
        return _fail(str(e), method=method)

    version = cp.publish_version_from_dir(final_dir)
    stem = f"{entity}_{step}_v{version:03d}"
    art_path = os.path.join(str(staging_dir), stem + ext)

    if target == "web":
        ok, err = _glb_export(art_path, context, objects)
    else:
        ok, err = _usd_export(art_path, context, objects)
    if not ok:
        return _fail(f"{target} export failed: {err} (staging preserved: {staging_dir})",
                     method=method)

    thumb_objects = objects or _fallback_objects(scene)
    thumb = render_publish_thumbnail(thumb_objects, str(staging_dir))
    warning = ""
    if not thumb:
        cause = thumbnails.LAST_ERROR or "unknown cause"
        warning = (f"Thumbnail render failed ({cause}) - publish will be rejected "
                   f"(staging preserved: {staging_dir})")

    try:
        info = cp.finalize_publish_version(
            project_path, entity, staging_dir, final_dir, version,
            expected_artifacts=[stem, "thumb.png"],
        )
    except Exception as e:
        return _fail(str(e), method=method, warning=warning)

    # The publish just wrote a thumb.png: the entity's thumbnail changes (and goes from
    # 'wip' to 'publish'). Without this purge, the panel would keep the old one until the TTL.
    entity_thumbs.invalidate(project_path)

    pub_path = os.path.join(info["final_dir"], stem + ext)
    message = (
        f"Published: {os.path.basename(pub_path)}  v{info['version']:03d}  [{method}] - "
        f"{n_renamed} datablocks renamed, {len(shared)} shared untouched (see console)"
    )

    if load_after:
        try:
            if ext == ".glb":
                bpy.ops.import_scene.gltf(filepath=pub_path)
            else:
                bpy.ops.wm.usd_import(filepath=pub_path)
            message += f"  |  Loaded: {os.path.basename(pub_path)}"
        except Exception as e:
            warning = (warning + " | " if warning else "") + f"import failed: {e}"

    return {"ok": True, "version": info["version"], "message": message,
            "path": pub_path, "method": method, "warning": warning}


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

    # Round-trip with scene.ylos_current_step (STEP_ITEMS_ALL): read in invoke, written
    # in execute -> same full domain. Per-family filtering stays ensured at
    # execution by is_step_valid_for_context (semantic guard preserved).
    step: EnumProperty(
        name="Step",
        items=vocab.STEP_ITEMS_ALL,
        default="modeling",
    )

    _next_ver: int = 1        # display-only, computed in invoke
    _target: str = "offline"  # display-only: pipeline target (web|offline), computed in invoke
    _ext: str = ".usd"        # display-only: artifact extension derived from the target

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
        # Format = orchestrator decision (pipeline target), not the DCC's: the dialog shows it.
        self._target = _cp().get_pipeline_target(scene.ylos_project_path)
        self._ext = ".glb" if self._target == "web" else ".usd"
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
            text=f"{scene.ylos_current_asset}_{self.step}_v{self._next_ver:03d}"
                 f"{self._ext} ({self._target})"
        )
        box.label(text="Version assigned by create_project.py", icon="INFO")

    def execute(self, context):
        # Thin wrapper: all the logic lives in publish_entity_step (shared with the
        # State Manager batch, principle 5). Here: read the scene context, report, and
        # align scene.ylos_current_step with the published step (simple-button UX - the dialog
        # allows a step != current).
        scene = context.scene
        result = publish_entity_step(
            context,
            scene.ylos_project_path,
            scene.ylos_current_asset,
            self.step,
            allow_full_scene=self.allow_full_scene,
            comment="",
            load_after=self.load_after,
        )

        if result["warning"]:
            self.report({"WARNING"}, result["warning"])
        if not result["ok"]:
            self.report({"ERROR"}, result["message"])
            return {"CANCELLED"}

        scene.ylos_current_step = self.step
        self.report({"INFO"}, result["message"])
        return {"FINISHED"}
