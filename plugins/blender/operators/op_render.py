# -*- coding: utf-8 -*-
# Render state (Prism-parity, feature 3) — a real engine render of the active entity+step
# over the frame range, versioned in the cache render tier (same place Houdini writes), as
# EXR frames. Farm submission (Deadline etc.) can be layered on later.
#
# The orchestrator (create_project.render_spec) ALLOCATES the versioned output path in the
# cache tier; this operator only RENDERS into it with the scene's real engine (unlike the
# WORKBENCH playblast). Renders never auto-deliver — delivery is an explicit, validated copy.
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


# Output-related render state the operator overrides; the scene ENGINE is deliberately NOT
# touched (a render uses whatever the artist set). Snapshotted and restored so a render
# never leaks its output settings into the scene.
_R_FIELDS = ("filepath", "use_file_extension")


def _snapshot(scene):
    r = scene.render
    return {
        "filepath": r.filepath,
        "use_file_extension": r.use_file_extension,
        "file_format": r.image_settings.file_format,
        "color_depth": r.image_settings.color_depth,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
    }


def _restore(scene, snap):
    r = scene.render
    try:
        r.filepath = snap["filepath"]
        r.use_file_extension = snap["use_file_extension"]
        r.image_settings.file_format = snap["file_format"]
        r.image_settings.color_depth = snap["color_depth"]
        scene.frame_start = snap["frame_start"]
        scene.frame_end = snap["frame_end"]
    except Exception:
        pass


class YLOS_OT_Render(bpy.types.Operator):
    """Render the active entity+step over the frame range, versioned in the cache render
    tier (EXR frames), with the scene's real engine."""
    bl_idname = "ylos.render"
    bl_label = "Render"
    bl_description = ("Render the active entity + step over the frame range to a versioned "
                      "EXR sequence in the cache render tier (same location as Houdini)")
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

        if scene.camera is None:
            self.report({"ERROR"}, "No camera in the scene — a render needs one. "
                                   "Add a camera (or use Playblast for a quick capture).")
            return {"CANCELLED"}

        cp = _cp()
        spec = cp.render_spec(entity, step, project_root=project, ext="exr")
        if not spec.get("ok"):
            self.report({"ERROR"}, f"Cannot render: {spec.get('reason', 'unknown')}")
            return {"CANCELLED"}

        vdir = spec["version_dir"]
        os.makedirs(vdir, exist_ok=True)

        fr = spec.get("frame_range")
        start = self.start or (fr["start"] if fr else scene.frame_start)
        end   = self.end or (fr["end"] if fr else scene.frame_end)
        if end < start:
            self.report({"ERROR"}, f"Invalid range {start}-{end}.")
            return {"CANCELLED"}

        snap = _snapshot(scene)
        try:
            r = scene.render
            r.image_settings.file_format = "OPEN_EXR"
            r.image_settings.color_depth = "16"      # half float — standard render output
            r.use_file_extension = True
            r.filepath = spec["output_prefix"]       # frames land as <stem>.####.exr
            scene.frame_start = int(start)
            scene.frame_end = int(end)
            bpy.ops.render.render(animation=True)    # real engine (Cycles/EEVEE), headless-ok
        except Exception as e:
            self.report({"ERROR"}, f"Render failed: {e}")
            return {"CANCELLED"}
        finally:
            _restore(scene, snap)

        frames = sorted(glob.glob(os.path.join(vdir, f"{spec['stem']}.*.exr")))
        if not frames:
            self.report({"ERROR"}, "Render produced no frames.")
            return {"CANCELLED"}

        self.report(
            {"INFO"},
            f"Render: {spec['stem']} ({len(frames)} frame{'s' if len(frames) != 1 else ''}, "
            f"v{spec['version']:03d}, {int(start)}-{int(end)}) -> cache/render",
        )
        return {"FINISHED"}
