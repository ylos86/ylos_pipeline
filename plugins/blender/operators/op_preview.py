# -*- coding: utf-8 -*-
"""Entity preview capture (Prism 'set preview' parity).

Writes '<entity>/preview.png' - the MANUAL override at the top of the resolution cascade
(see create_project.resolve_entity_thumbnail). Consumed as-is by the web UI AND the Blender
panel: a single gesture, both browsers follow.

Why an explicit override when a thumbnail is already auto-resolved: the
latest published version is not always the image that REPRESENTS the asset (a rig
publish frames badly, a WIP mid-blocking, a lookdev not yet done). An artist must be able
to lock in the right image without depending on the publish order.
"""

import os
import sys

import bpy
from bpy.props import BoolProperty

from ..core.thumbnails import generate_thumbnail, reload_icon
from ..core import entity_thumbs

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


class YLOS_OT_CapturePreview(bpy.types.Operator):
    bl_idname = "ylos.capture_preview"
    bl_label = "Capture Preview"
    bl_description = ("Capture the current viewport as this entity's preview image "
                      "(overrides the auto-resolved thumbnail everywhere)")
    bl_options = {"REGISTER"}

    clear: BoolProperty(
        name="Remove preview",
        description="Delete the manual preview instead of capturing a new one "
                    "(falls back to the auto-resolved thumbnail)",
        default=False,
    )

    def execute(self, context):
        scene = context.scene
        project_path = scene.ylos_project_path
        entity = scene.ylos_current_asset
        if not project_path or not entity:
            self.report({"ERROR"}, "No active project or entity.")
            return {"CANCELLED"}

        cp = _cp()
        resolved = cp.resolve_entity(project_path, entity)
        if resolved is None:
            self.report({"ERROR"}, f"Entity '{entity}' not found under project.")
            return {"CANCELLED"}

        target = os.path.join(resolved["dir"], cp.ENTITY_PREVIEW_NAME)

        if self.clear:
            if os.path.isfile(target):
                try:
                    os.remove(target)
                except OSError as e:
                    self.report({"ERROR"}, f"Could not remove preview: {e}")
                    return {"CANCELLED"}
                reload_icon(target)
                entity_thumbs.invalidate(project_path)
                self.report({"INFO"}, f"Preview removed for {entity}.")
                return {"FINISHED"}
            self.report({"INFO"}, "No manual preview to remove.")
            return {"CANCELLED"}

        # generate_thumbnail writes '<stem>_thumb.png' next to the given path: we pass it a
        # dummy stem in the entity's folder, then rename. Reuse the existing viewport render
        # rather than writing a second one (principle 5) - and the VIEWPORT is indeed what
        # we want here: the artist frames themselves what represents their asset.
        stem_path = os.path.join(resolved["dir"], "_ylos_preview.blend")
        produced = generate_thumbnail(stem_path, context)
        if not produced:
            self.report({"ERROR"}, "Viewport capture failed (needs a 3D viewport context).")
            return {"CANCELLED"}
        try:
            os.replace(produced, target)
        except OSError as e:
            self.report({"ERROR"}, f"Could not write preview: {e}")
            return {"CANCELLED"}

        reload_icon(target)
        entity_thumbs.invalidate(project_path)
        self.report({"INFO"}, f"Preview captured: {entity}/{cp.ENTITY_PREVIEW_NAME}")
        return {"FINISHED"}
