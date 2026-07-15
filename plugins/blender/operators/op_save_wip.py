# -*- coding: utf-8 -*-
import bpy
import getpass
import os
import sys
from bpy.props import IntProperty, EnumProperty, StringProperty
from ..core.asset import (
    resolve_wip_save_path,
    get_latest_wip_version,
    list_wip_versions,
)
from ..core import vocab
from ..core.thumbnails import generate_thumbnail, reload_thumb_icon

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


class YLOS_OT_SaveWip(bpy.types.Operator):
    bl_idname = "ylos.save_wip"
    bl_label = "Save WIP"
    bl_description = "Save versioned .blend + generate viewport thumbnail"
    bl_options = {"REGISTER"}

    version: IntProperty(
        name="Version",
        description="Version number (e.g. 1 = v001)",
        min=1, max=999, default=1,
    )

    # Round-trip avec scene.ylos_current_step (STEP_ITEMS_ALL) : recopie en invoke,
    # reecrit en execute -> meme domaine complet.
    step: EnumProperty(
        name="Step",
        items=vocab.STEP_ITEMS_ALL,
        default="modeling",
    )

    comment: StringProperty(
        name="Comment",
        description="Note for this version (Prism-style), saved to a sidecar "
                    "<wip>.blend.json next to the file",
        default="",
    )

    def invoke(self, context, event):
        scene = context.scene
        if not scene.ylos_project_path or not scene.ylos_current_asset:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}

        self.step = scene.ylos_current_step
        latest = get_latest_wip_version(
            scene.ylos_project_path, scene.ylos_current_asset,
            self.step, scene.ylos_context_type.lower(),
        )
        self.version = latest + 1
        # Reprend la note deja tapee dans le panel (Scenefile), editable ici.
        self.comment = scene.ylos_wip_comment
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        scene = context.scene
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.label(text=f"Asset: {scene.ylos_current_asset}", icon="OBJECT_DATA")
        layout.separator()
        layout.prop(self, "step")
        layout.prop(self, "version")
        layout.prop(self, "comment")

        save_path = resolve_wip_save_path(
            scene.ylos_project_path, scene.ylos_current_asset,
            self.step, self.version, scene.ylos_context_type.lower(),
        )
        box = layout.box()
        box.label(text="Save to:", icon="FILE_BLEND")
        box.label(text=os.path.basename(save_path))

        versions = list_wip_versions(
            scene.ylos_project_path, scene.ylos_current_asset,
            self.step, scene.ylos_context_type.lower(),
        )
        if self.version in [v["version"] for v in versions]:
            box.label(text="WARNING: will overwrite", icon="ERROR")

        box.label(text="A viewport thumbnail will be generated.", icon="IMAGE_DATA")

    def execute(self, context):
        scene        = context.scene
        project_path = scene.ylos_project_path
        asset_name   = scene.ylos_current_asset
        ctx_type     = scene.ylos_context_type.lower()

        if not project_path or not asset_name:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}

        save_path = resolve_wip_save_path(
            project_path, asset_name, self.step, self.version, ctx_type
        )

        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        try:
            bpy.ops.wm.save_as_mainfile(filepath=save_path, copy=False)
        except Exception as e:
            self.report({"ERROR"}, f"Save failed: {e}")
            return {"CANCELLED"}

        # Sidecar Prism-style '<wip>.blend.json' - ecriture atomique (meme motif que
        # create_project.py pour tout fichier de metadonnees, cf. CLAUDE.md). Best-effort :
        # une erreur d'ecriture ne doit jamais faire perdre le .blend deja sauve.
        try:
            sidecar = {
                "comment": self.comment,
                "user": getpass.getuser(),
                "date": _cp()._now(),
                "blender_version": bpy.app.version_string,
            }
            _cp()._atomic_write_json(save_path + ".json", sidecar)
        except Exception as e:
            self.report({"WARNING"}, f"Sidecar (comment) not saved: {e}")

        thumb = generate_thumbnail(save_path, context)
        if thumb:
            reload_thumb_icon(save_path)
            self.report({"INFO"}, f"Saved: {os.path.basename(save_path)} + thumbnail")
        else:
            self.report({"INFO"}, f"Saved: {os.path.basename(save_path)} (no thumbnail)")

        scene.ylos_current_step = self.step
        scene.name = f"SCENE_{asset_name}_{self.step}"
        # La note s'applique a CETTE version, pas aux suivantes (pattern Prism) - vide le
        # champ du panel une fois consomme.
        scene.ylos_wip_comment = ""

        return {"FINISHED"}
