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


def _normalize_datablock_names(objects):
    """Hygiene des noms AVANT export : aligne le nom du datablock sur celui de l'objet quand
    le datablock est mono-utilisateur. Sans ca, un objet renomme mais dont la donnee garde
    'Cube.001' sort en prim USD 'Cube_001' / node glTF errone -> un Load Latest ramene un
    objet qui ne porte plus le nom de l'asset. Un datablock MULTI-user n'est JAMAIS renomme
    (le rename affecterait les autres utilisateurs) -> collecte pour warning, jamais
    silencieux. Retourne (n_renommes, [descriptions des partages non touches])."""
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
    """Export glTF binaire (GLB) vers un filepath exact (staging_dir, cf. execute()). Miroir de
    _usd_export : selection = objets gather (use_selection) sinon scene entiere. +Y up par
    defaut de l'exporter (correct pour Three.js). Retourne (success, error_message)."""
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
    """Coeur de publish REUTILISABLE : le bouton simple (ylos.publish, step courant) ET le
    batch du State Manager (ylos.publish_states, states empiles) passent par ici - logique
    unique, principe 5. La famille de l'entite est resolue par create_project.resolve_entity
    (un state peut cibler asset/set/shot, pas seulement le contexte scene courant). Ne leve
    JAMAIS pour un cas metier : retourne un dict
    {ok, version, message, path, method, warning}. Contrat deux-phases inchange (allocate ->
    export USD/GLB selon la cible -> thumbnail requis -> finalize)."""
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

    # Hygiene des noms AVANT export : un datablock mono-user non renomme ('Cube.001') sort en
    # prim USD / node glTF errone. Jamais silencieux. Full-scene -> normalise toute la scene.
    export_objects = objects if objects else list(scene.objects)
    n_renamed, shared = _normalize_datablock_names(export_objects)
    for s in shared:
        print(f"[Ylos publish] datablock partage non renomme (nom d'objet conserve): {s}")

    # Format d'artifact = decision d'orchestrateur (cible pipeline), jamais du DCC.
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

    pub_path = os.path.join(info["final_dir"], stem + ext)
    message = (
        f"Published: {os.path.basename(pub_path)}  v{info['version']:03d}  [{method}] - "
        f"{n_renamed} datablocks renommes, {len(shared)} partages non touches (voir console)"
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

    # Round-trip avec scene.ylos_current_step (STEP_ITEMS_ALL) : lu en invoke, ecrit
    # en execute -> meme domaine complet. Le filtrage par famille reste assure a
    # l'execution par is_step_valid_for_context (garde semantique conservee).
    step: EnumProperty(
        name="Step",
        items=vocab.STEP_ITEMS_ALL,
        default="modeling",
    )

    _next_ver: int = 1        # display-only, computed in invoke
    _target: str = "offline"  # display-only : cible pipeline (web|offline), calculee en invoke
    _ext: str = ".usd"        # display-only : extension d'artifact deduite de la cible

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
        # Format = decision d'orchestrateur (cible pipeline), pas du DCC : le dialog l'affiche.
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
        # Wrapper mince : toute la logique vit dans publish_entity_step (partagee avec le
        # State Manager batch, principe 5). Ici : lire le contexte scene, reporter, et
        # aligner scene.ylos_current_step sur le step publie (UX du bouton simple - le dialog
        # autorise un step != courant).
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
