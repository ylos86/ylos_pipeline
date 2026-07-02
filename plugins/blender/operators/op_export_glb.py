# -*- coding: utf-8 -*-
import bpy
import os
import sys

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


class YLOS_OT_ExportGLB(bpy.types.Operator):
    bl_idname  = "ylos.export_glb"
    bl_label   = "Export glTF"
    bl_description = "Exporte l'asset courant en .glb via le contrat deux-phases (allocate/finalize)"

    def execute(self, context):
        from ..core import _parse_wip_path, _get_active_project
        from ..core.thumbnails import render_publish_thumbnail

        if REPO_ROOT not in sys.path:
            sys.path.insert(0, REPO_ROOT)
        import create_project as cp

        filepath     = bpy.data.filepath
        project_root = _get_active_project()
        asset_name, step = _parse_wip_path(filepath)

        if not all([filepath, project_root, asset_name]):
            self.report({'ERROR'}, "Contexte incomplet — sauvegarde le fichier d'abord.")
            return {'CANCELLED'}

        try:
            staging_dir, final_dir = cp.allocate_publish_version(
                project_root, asset_name, comment="", kind=step,
            )
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        version = cp.publish_version_from_dir(final_dir)
        stem = f"{asset_name}_{step}_v{version:03d}"
        glb_path = os.path.join(str(staging_dir), stem + ".glb")

        try:
            bpy.ops.export_scene.gltf(
                filepath=glb_path,
                export_format='GLB',
                export_selected=False,
                export_apply=True,
                export_texcoords=True,
                export_normals=True,
                export_materials='EXPORT',
                export_colors=True,
                export_cameras=False,
                export_lights=False,
            )
        except Exception as e:
            self.report({'ERROR'}, f"glTF export failed: {e} (staging preserved: {staging_dir})")
            return {'CANCELLED'}

        objects = [
            o for o in context.scene.objects
            if o.type in ("MESH", "ARMATURE", "CURVE") and not o.hide_get()
        ]
        thumb = render_publish_thumbnail(objects, str(staging_dir))
        if not thumb:
            self.report(
                {'WARNING'},
                f"Thumbnail render failed - publish will be rejected (staging preserved: {staging_dir})",
            )

        try:
            info = cp.finalize_publish_version(
                project_root, asset_name, staging_dir, final_dir, version,
                expected_artifacts=[stem, "thumb.png"],
            )
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        self.report(
            {'INFO'},
            f"{asset_name} / {step} / v{info['version']:03d} — glb exporté ✓",
        )
        return {'FINISHED'}
