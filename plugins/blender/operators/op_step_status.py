# -*- coding: utf-8 -*-
# Per-step production status (schema 2.2, plan-usable-v1 Phase 2.1) - Blender side.
#
# Thin adapter, zero business logic: create_project.set_step_status is the SINGLE point that
# validates and persists a status (only STEP_STATUS_EXPLICIT is written; empty/wip/published
# stay derived from disk and are never stored). The enum domain comes from core/vocab.py
# (STEP_STATUS_ITEMS, a module-level tuple - bpy GC trap), never from a literal declared
# here (see the CLAUDE.md guard on hard-coded enum item lists outside vocab.py).
import bpy
import os
import sys
from bpy.props import EnumProperty, StringProperty

from ..core import asset as asset_core
from ..core import vocab

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


class YLOS_OT_SetStepStatus(bpy.types.Operator):
    """Set (or clear) the explicit production status of an entity's step."""
    bl_idname = "ylos.set_step_status"
    bl_label = "Set Step Status"
    bl_description = ("Mark this step Review / Approved in the entity manifest, or hand it "
                      "back to the status derived from disk")
    bl_options = {"REGISTER"}

    # Empty entity/step = "the scene's active entity/step" (the panel button case). An
    # explicit value lets any call-site (State Manager, launcher, future web bridge) target
    # another entity without touching the scene context.
    entity: StringProperty(name="Entity", default="")
    step: StringProperty(name="Step", default="")
    status: EnumProperty(
        name="Status",
        description="Explicit status to persist, or Auto to clear it",
        items=vocab.STEP_STATUS_ITEMS,
        default=_cp().STEP_STATUS_AUTO,
    )

    def execute(self, context):
        scene = context.scene
        project_path = scene.ylos_project_path
        entity = self.entity or scene.ylos_current_asset
        step = self.step or scene.ylos_current_step

        if not project_path or not entity or not step:
            self.report({"ERROR"}, "No active project, entity or step.")
            return {"CANCELLED"}

        try:
            result = _cp().set_step_status(project_path, entity, step, self.status)
        except (ValueError, FileNotFoundError) as e:
            # Business errors of the orchestrator (undeclared step, unknown status, unknown
            # entity) - reported as-is: same message everywhere (web / Blender / CLI).
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except OSError as e:
            self.report({"ERROR"}, f"Could not write the manifest: {e}")
            return {"CANCELLED"}

        # The panel reads a TTL cache - without this purge the new status would only show
        # up after the TTL, which reads as "the button did nothing".
        asset_core.invalidate_step_status_cache(project_path)

        shown = result.get("status", "?") if isinstance(result, dict) else "?"
        suffix = "" if (isinstance(result, dict) and result.get("explicit")) else " (derived)"
        self.report({"INFO"}, f"{entity} / {step}: {vocab.status_label(shown)}{suffix}")
        return {"FINISHED"}
