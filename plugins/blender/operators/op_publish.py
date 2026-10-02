# -*- coding: utf-8 -*-
# Exports current step to USD via create_project.py's two-phase contract (allocate_publish_
# version/finalize_publish_version, kind=<step>) - single source of truth, thumbnail required.

import bpy
import os
import shutil
import sys
import tempfile
from bpy.props import BoolProperty, EnumProperty
from ..core.asset import get_latest_publish_version, list_publish_versions
from ..core.project import is_step_valid_for_context
from ..core import vocab
from ..core.scene_checker import get_asset_objects_for_publish
from ..core.thumbnails import render_publish_thumbnail
from ..core import thumbnails
from ..core import entity_thumbs
from ..core import asset as asset_core
from ..core import usd_convention
from ..core import publish_check
from .op_update_imports import tagged_import_collections, set_cached_downstream_impact

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


def _usd_export(filepath: str, context, objects: list, entity: str, family: str) -> tuple:
    """Export USD to an exact filepath (staging_dir target, cf. execute()) following
    docs/usd-convention.md: upAxis = Y, metersPerUnit from the orchestrator, stage rooted at
    /<Entity> (asset/set) or /ROOT (shot), defaultPrim set accordingly. The kwargs come from
    core.usd_convention - the single translation of the convention into Blender's RNA, never
    spelled out at a call-site (that is how half a project's layers end up Z-up).
    Returns (success, error_message).
    """
    scene = context.scene
    prev_selected = [o for o in scene.objects if o.select_get()]
    prev_active   = context.view_layer.objects.active

    try:
        kwargs = usd_convention.export_kwargs(
            entity_name=entity, family=family, selected_only=bool(objects),
        )
        if objects:
            for o in scene.objects:
                o.select_set(False)
            for o in objects:
                o.select_set(True)
            context.view_layer.objects.active = objects[0]
        try:
            bpy.ops.wm.usd_export(filepath=filepath, **kwargs)
            return True, ""
        except Exception as e:
            return False, str(e)

    finally:
        for o in scene.objects:
            o.select_set(False)
        for o in prev_selected:
            o.select_set(True)
        context.view_layer.objects.active = prev_active


def scene_dependencies(entity: str) -> list:
    """Dependencies (schema 2.2) of a publish made from THIS scene = the published products
    the scene currently holds, read from the TAGGED import collections
    (ylos_import_entity/step/version, written by ylos.import_product and kept in sync by
    ylos.update_import). Shape expected by create_project.finalize_publish_version:
    [{"entity", "step", "version"}].

    The entity being published is filtered out: an asset re-importing its own product is a
    working convenience, not a production dependency (and it would make every entity depend
    on itself in the index). Deduplicated on (entity, step): only one import state may exist
    per pair in a scene, but a stale duplicate must never double an edge."""
    seen = {}
    for col in tagged_import_collections():
        dep_entity = col.get("ylos_import_entity")
        dep_step = col.get("ylos_import_step")
        if not dep_entity or not dep_step or dep_entity == entity:
            continue
        version = col.get("ylos_import_version")
        seen[(str(dep_entity), str(dep_step))] = {
            "entity": str(dep_entity),
            "step": str(dep_step),
            "version": int(version) if version else None,
        }
    return [seen[key] for key in sorted(seen)]


def downstream_impact(cp, project_path: str, entity: str) -> list:
    """Entities CONSUMING 'entity' that are now outdated (they pin a version older than the
    latest complete publish) - read from create_project.entity_dependencies, the single
    index (manifest 'dependencies' + USD ASCII layer scan). Returns the raw 'used_in' edges
    filtered on dependency.outdated. Never raises: an index that cannot be read gives []
    (a reporting nicety must not fail a publish that already committed)."""
    try:
        deps = cp.entity_dependencies(project_path, entity)
    except Exception as e:                    # noqa: BLE001 - reporting must never fail
        print(f"[Ylos publish] dependency index unavailable for {entity!r}: {e}")
        return []
    return [edge for edge in deps.get("used_in", [])
            if edge.get("dependency", {}).get("outdated")]


def publish_entity_step(context, project_path, entity, step, *,
                        allow_full_scene=False, comment="", load_after=False):
    """REUSABLE publish core: the simple button (ylos.publish, current step) AND the
    State Manager batch (ylos.publish_states, stacked states) go through here - single
    logic, principle 5. The entity's family is resolved by create_project.resolve_entity
    (a state can target asset/set/shot, not only the current scene context). NEVER raises
    for a business case: returns a dict
    {ok, version, message, path, method, warning, dependencies, outdated}. Two-phase contract
    unchanged (allocate -> USD/GLB export per target -> thumbnail required -> finalize),
    now recording the scene's tagged imports as schema-2.2 'dependencies' and reporting the
    downstream entities the new version leaves outdated."""
    cp = _cp()
    scene = context.scene

    def _fail(msg, method="", warning=""):
        return {"ok": False, "version": 0, "message": msg, "path": "",
                "method": method, "warning": warning,
                "dependencies": [], "outdated": []}

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
    # Offline target -> '.usdc': docs/usd-convention.md §2 bans a bare '.usd' (ambiguous,
    # ASCII or crate depending on the writer) and pins geometry publishes to the binary
    # crate. Composition roots stay '.usda' (written by the orchestrator, not here).
    target = cp.get_pipeline_target(project_path)
    ext = ".glb" if target == "web" else ".usdc"

    # Pre-publish check, BEFORE a version is allocated: a scene that cannot re-import (no mesh,
    # empty mesh, missing texture file, object named like the USD root prim) must not burn a
    # version number nor leave a staging folder behind.
    pre_issues = publish_check.pre_publish_check(
        context, export_objects, entity, project_path, target)
    blocked = publish_check.summarize(pre_issues)
    if blocked:
        for i in pre_issues:
            print(f"[Ylos publish check] {i['severity']} {i['obj_name']}: {i['message']}")
        return _fail(f"Publish blocked by the pre-publish check - {blocked}", method=method)
    check_notes = [f"{i['obj_name']}: {i['message']}" for i in pre_issues]

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
        ok, err = _usd_export(art_path, context, objects, entity, ctx_type)
    if not ok:
        return _fail(f"{target} export failed: {err} (staging preserved: {staging_dir})",
                     method=method)

    # Re-import check: bring the artifact back into a throwaway collection and compare it with
    # what left the scene (rule: create_project.validate_publish_roundtrip). A publish that does
    # not come back as it went out is refused HERE, staging preserved for audit - the same
    # refuse-don't-commit stance as the thumbnail and expected_artifacts gates.
    round_trip = publish_check.verify_artifact(context, art_path, export_objects)
    for i in round_trip["issues"]:
        print(f"[Ylos publish check] {i['severity']} {i['obj_name']}: {i['message']}")
    if not round_trip["ok"]:
        return _fail(
            f"Publish blocked: the artifact does not re-import correctly - "
            f"{publish_check.summarize(round_trip['issues'])} (staging preserved: {staging_dir})",
            method=method)
    check_notes += [i["message"] for i in round_trip["issues"] if i["severity"] == "WARNING"]

    thumb_objects = objects or _fallback_objects(scene)
    thumb = render_publish_thumbnail(thumb_objects, str(staging_dir))
    warning = ""
    if not thumb:
        cause = thumbnails.LAST_ERROR or "unknown cause"
        warning = (f"Thumbnail render failed ({cause}) - publish will be rejected "
                   f"(staging preserved: {staging_dir})")

    # Schema 2.2: record WHAT this publish was built from (the scene's tagged imports).
    # Source of truth for the dependency index that the USD ASCII scan cannot provide -
    # a .usdc/.glb artifact is opaque to build_dependency_index.
    dependencies = scene_dependencies(entity)

    try:
        info = cp.finalize_publish_version(
            project_path, entity, staging_dir, final_dir, version,
            expected_artifacts=[stem, "thumb.png"],
            dependencies=dependencies,
        )
    except Exception as e:
        return _fail(str(e), method=method, warning=warning)

    # The publish just wrote a thumb.png: the entity's thumbnail changes (and goes from
    # 'wip' to 'publish'). Without this purge, the panel would keep the old one until the TTL.
    entity_thumbs.invalidate(project_path)
    # A publish moves the step from 'wip' to 'published' (derived status, schema 2.2).
    asset_core.invalidate_step_status_cache(project_path)

    # Who is now behind? Read the index AFTER the commit (the new version must be in the
    # manifest for 'outdated' to mean anything) and cache it for the panel - same pattern as
    # op_update_imports.get_cached_update_results: an explicit action computes, draw() reads.
    outdated = downstream_impact(cp, project_path, entity)
    set_cached_downstream_impact(entity, outdated)

    pub_path = os.path.join(info["final_dir"], stem + ext)
    message = (
        f"Published: {os.path.basename(pub_path)}  v{info['version']:03d}  [{method}] - "
        f"{n_renamed} datablocks renamed, {len(shared)} shared untouched (see console)"
    )
    message += "  |  re-import check OK"
    if check_notes:
        note = f"{len(check_notes)} check warning(s): " + "; ".join(check_notes[:3])
        warning = (warning + " | " if warning else "") + note
    if dependencies:
        message += f"  |  {len(dependencies)} dependency(ies) recorded"
    if outdated:
        names = sorted({e["consumer"]["entity"] for e in outdated})
        detail = ", ".join(names[:3]) + ("…" if len(names) > 3 else "")
        outdated_note = f"{len(names)} downstream entity(ies) now outdated: {detail}"
        warning = (warning + " | " if warning else "") + outdated_note
        print(f"[Ylos publish] {outdated_note}")

    if load_after:
        try:
            if ext == ".glb":
                bpy.ops.import_scene.gltf(filepath=pub_path)
            else:
                # Same convention module as the export: the stage comes back at its original
                # Blender orientation and scale (Y-up xform re-absorbed, metersPerUnit applied).
                bpy.ops.wm.usd_import(filepath=pub_path, **usd_convention.import_kwargs())
            message += f"  |  Loaded: {os.path.basename(pub_path)}"
        except Exception as e:
            warning = (warning + " | " if warning else "") + f"import failed: {e}"

    return {"ok": True, "version": info["version"], "message": message,
            "path": pub_path, "method": method, "warning": warning,
            "dependencies": dependencies, "outdated": outdated}


_check_cache = {"results": []}


def get_cached_publish_check():
    """Last 'Check Publish' results: [{entity, step, ok, issues}]. draw() reads this, an explicit
    action computes it (a re-import in draw() would be absurd)."""
    return _check_cache["results"]


def check_publish(context, project_path, entity, step, *, allow_full_scene=False):
    """DRY RUN of publish_entity_step: same pre-check, same export, same re-import verdict - but the
    export goes to a temp folder that is deleted, so no version is allocated and nothing is
    written to the project. NEVER raises for a business case. Returns
    {entity, step, ok, issues}."""
    cp = _cp()
    scene = context.scene
    out = {"entity": entity, "step": step, "ok": False, "issues": []}

    def _err(msg):
        out["issues"].append(publish_check._issue("ERROR", entity or "(none)", msg))
        return out

    if not project_path or not entity:
        return _err("No active project or entity.")
    resolved = cp.resolve_entity(project_path, entity)
    if resolved is None:
        return _err(f"Entity '{entity}' not found under project.")
    family = resolved["family"]
    if not is_step_valid_for_context(step, family):
        return _err(f"Step '{step}' is not valid for a {family}.")

    objects, _method = get_asset_objects_for_publish(scene, entity, step)
    if not objects and not allow_full_scene:
        return _err(f"No objects resolved for '{entity}' (step '{step}'): expected a collection "
                    f"named '{entity}' or objects named GEO_{entity}_*.")
    export_objects = objects if objects else list(scene.objects)

    target = cp.get_pipeline_target(project_path)
    out["issues"] = publish_check.pre_publish_check(
        context, export_objects, entity, project_path, target)
    if any(i["severity"] == "ERROR" for i in out["issues"]):
        return out

    tmp = tempfile.mkdtemp(prefix="ylos_publish_check_")
    try:
        ext = ".glb" if target == "web" else ".usdc"
        art = os.path.join(tmp, f"{entity}_{step}_check{ext}")
        if target == "web":
            ok, err = _glb_export(art, context, objects)
        else:
            ok, err = _usd_export(art, context, objects, entity, family)
        if not ok:
            out["issues"].append(publish_check._issue("ERROR", "export", f"{target} export failed: {err}"))
            return out
        verdict = publish_check.verify_artifact(context, art, export_objects)
        out["issues"] += verdict["issues"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out["ok"] = not any(i["severity"] == "ERROR" for i in out["issues"])
    return out


class YLOS_OT_CheckPublish(bpy.types.Operator):
    bl_idname = "ylos.check_publish"
    bl_label = "Check Publish"
    bl_description = ("Dry run: export to a temporary folder, re-import it and compare - tells you "
                      "whether the publish will come back correctly, without publishing anything")
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        project = scene.ylos_project_path
        if not project:
            self.report({"ERROR"}, "No active project.")
            return {"CANCELLED"}

        targets = [(s.entity, s.step, s.allow_full_scene)
                   for s in scene.ylos_export_states if s.enabled]
        if not targets and scene.ylos_current_asset:
            targets = [(scene.ylos_current_asset, scene.ylos_current_step, False)]
        if not targets:
            self.report({"WARNING"}, "Nothing to check: no active asset.")
            return {"CANCELLED"}

        results = [check_publish(context, project, e, st, allow_full_scene=full)
                   for e, st, full in targets]
        _check_cache["results"] = results
        n_err = sum(1 for r in results for i in r["issues"] if i["severity"] == "ERROR")
        n_warn = sum(1 for r in results for i in r["issues"] if i["severity"] == "WARNING")
        if n_err:
            self.report({"ERROR"}, f"Publish check: {n_err} error(s), {n_warn} warning(s) - "
                                   f"this would NOT publish.")
        else:
            self.report({"INFO"}, f"Publish check OK ({n_warn} warning(s)): safe to publish.")
        return {"FINISHED"}


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
    _ext: str = ".usdc"       # display-only: artifact extension derived from the target

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
        self._ext = ".glb" if self._target == "web" else ".usdc"
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
