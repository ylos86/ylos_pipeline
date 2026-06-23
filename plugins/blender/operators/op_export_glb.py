# -*- coding: utf-8 -*-
import bpy
import os
import sys
import tempfile

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


class YLOS_OT_ExportGLB(bpy.types.Operator):
    bl_idname  = "ylos.export_glb"
    bl_label   = "Export glTF"
    bl_description = "Exporte l'asset courant en .glb et publie via create_project.py"

    def execute(self, context):
        from ..core import _parse_wip_path, _get_active_project

        if REPO_ROOT not in sys.path:
            sys.path.insert(0, REPO_ROOT)
        import create_project as cp

        filepath     = bpy.data.filepath
        project_root = _get_active_project()
        asset_name, step = _parse_wip_path(filepath)

        if not all([filepath, project_root, asset_name]):
            self.report({'ERROR'}, "Contexte incomplet — sauvegarde le fichier d'abord.")
            return {'CANCELLED'}

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".glb")
        os.close(tmp_fd)

        try:
            bpy.ops.export_scene.gltf(
                filepath=tmp_path,
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

            info        = cp.publish_asset(project_root, asset_name, step, tmp_path)
            publish_dir = os.path.dirname(info["publish_path"])

            # Thumbnail — generate_thumbnail retourne "" en cas d'échec, ne lève pas
            from ..core.thumbnails import generate_thumbnail
            thumb_src = generate_thumbnail(filepath, context)
            if thumb_src:
                import shutil
                try:
                    shutil.copy2(thumb_src, os.path.join(publish_dir, "thumb.png"))
                except Exception as e:
                    self.report({'WARNING'}, f"Export OK — copie thumb échouée : {e}")
            else:
                self.report({'WARNING'}, "Export OK — thumb échoué (viewport render)")

            self.report(
                {'INFO'},
                f"{asset_name} / {step} / v{info['version']:03d} — glb exporté ✓",
            )
            return {'FINISHED'}

        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
