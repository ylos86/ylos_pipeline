# -*- coding: utf-8 -*-
# Playblast (Prism-parity, feature 2) — an animated viewport capture over the frame range,
# saved as a versioned PNG image sequence in the source tree so the web browser can show it.
#
# The orchestrator (create_project.playblast_spec) ALLOCATES the versioned output folder;
# this operator only RENDERS the frames into it. GUI uses an OpenGL viewport capture (fast,
# with overlays); headless/farm falls back to an engine render (WORKBENCH, solid) so it also
# runs in --background. PNG (not a movie) because some Blender builds ship without FFMPEG.
import bpy
import glob
import os
import sys
from bpy.props import IntProperty

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


# Render-state the playblast overrides; snapshotted and restored so it never leaks its
# settings into the artist's scene (a real footgun otherwise).
_R_FIELDS = ("engine", "filepath", "resolution_percentage", "use_file_extension")


def _snapshot_render(scene):
    r = scene.render
    snap = {f: getattr(r, f) for f in _R_FIELDS}
    snap["file_format"] = r.image_settings.file_format
    snap["frame_start"] = scene.frame_start
    snap["frame_end"] = scene.frame_end
    return snap


def _restore_render(scene, snap):
    r = scene.render
    for f in _R_FIELDS:
        try:
            setattr(r, f, snap[f])
        except Exception:
            pass
    try:
        r.image_settings.file_format = snap["file_format"]
        scene.frame_start = snap["frame_start"]
        scene.frame_end = snap["frame_end"]
    except Exception:
        pass


def _remove_temp_cam(scene, cam_obj):
    """Remove the temporary playblast camera and its data-block; never raises."""
    try:
        if scene.camera == cam_obj:
            scene.camera = None
        data = cam_obj.data
        for coll in list(cam_obj.users_collection):
            coll.objects.unlink(cam_obj)
        bpy.data.objects.remove(cam_obj, do_unlink=True)
        if data and data.users == 0:
            bpy.data.cameras.remove(data)
    except Exception:
        pass


class YLOS_OT_Playblast(bpy.types.Operator):
    """Render a versioned playblast (viewport capture) of the active entity+step over the
    frame range, as a PNG sequence in the source tree for review."""
    bl_idname = "ylos.playblast"
    bl_label = "Playblast"
    bl_description = ("Capture the viewport over the frame range to a versioned PNG "
                      "sequence (review media, browser-visible)")
    bl_options = {"REGISTER"}

    # Optional explicit range; 0 = use the spec's frame_range (shots) or the scene's.
    start: IntProperty(name="Start", default=0, options={"HIDDEN"})
    end:   IntProperty(name="End", default=0, options={"HIDDEN"})

    def execute(self, context):
        scene = context.scene
        project = scene.ylos_project_path
        entity  = scene.ylos_current_asset
        step    = scene.ylos_current_step
        if not project or not entity:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}

        cp = _cp()
        spec = cp.playblast_spec(entity, step, project_root=project)
        if not spec.get("ok"):
            self.report({"ERROR"}, f"Cannot playblast: {spec.get('reason', 'unknown')}")
            return {"CANCELLED"}

        seq_dir = spec["path"]              # per-version sequence folder
        stem = spec["frame_prefix"]
        os.makedirs(seq_dir, exist_ok=True)

        # Frame range: explicit props > spec frame_range (shot) > current scene range.
        fr = spec.get("frame_range")
        start = self.start or (fr["start"] if fr else scene.frame_start)
        end   = self.end or (fr["end"] if fr else scene.frame_end)
        if end < start:
            self.report({"ERROR"}, f"Invalid range {start}-{end}.")
            return {"CANCELLED"}

        snap = _snapshot_render(scene)
        temp_cam = None
        try:
            r = scene.render
            r.engine = "BLENDER_WORKBENCH"          # fast, headless-capable, solid shading
            r.resolution_percentage = 50
            r.image_settings.file_format = "PNG"
            r.use_file_extension = True
            # Frames land as '<stem>.####.png' inside the versioned sequence folder.
            r.filepath = os.path.join(seq_dir, stem + ".")
            scene.frame_start = int(start)
            scene.frame_end = int(end)

            # An engine render needs a camera; add a temporary one when the scene has none
            # (asset/turntable review), removed in finally so the scene is untouched.
            if scene.camera is None:
                cam_data = bpy.data.cameras.new("YLOS_playblast_cam")
                temp_cam = bpy.data.objects.new("YLOS_playblast_cam", cam_data)
                scene.collection.objects.link(temp_cam)
                temp_cam.location = (7.36, -6.93, 4.96)
                temp_cam.rotation_euler = (1.109, 0.0, 0.815)
                scene.camera = temp_cam

            # GUI: OpenGL viewport capture (fast, overlays). Headless: engine render.
            did = False
            if not bpy.app.background:
                try:
                    bpy.ops.render.opengl(animation=True)
                    did = True
                except Exception as e:
                    self.report({"WARNING"},
                                f"OpenGL capture unavailable ({e}); using engine render.")
            if not did:
                bpy.ops.render.render(animation=True)
        except Exception as e:
            self.report({"ERROR"}, f"Playblast render failed: {e}")
            return {"CANCELLED"}
        finally:
            _restore_render(scene, snap)
            if temp_cam is not None:
                _remove_temp_cam(scene, temp_cam)

        frames = sorted(glob.glob(os.path.join(seq_dir, f"{stem}.*.png")))
        if not frames:
            self.report({"ERROR"}, "Playblast produced no frames.")
            return {"CANCELLED"}

        self.report({"INFO"},
                    f"Playblast: {os.path.basename(seq_dir)}/ "
                    f"({len(frames)} frame{'s' if len(frames) != 1 else ''}, "
                    f"v{spec['version']:03d}, {int(start)}-{int(end)})")
        return {"FINISHED"}
