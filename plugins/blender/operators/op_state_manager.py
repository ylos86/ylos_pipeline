# -*- coding: utf-8 -*-
# State Manager (Prism-style): export-state management operators + the single Publish.
#
# The batch Publish iterates the 'enabled' states and delegates EACH publish to
# publish_entity_step (op_publish.py) - single logic, principle 5. No duplication of the
# publish logic here: this operator is only an orchestrator of the recipe.

import os
import sys
import bpy
from bpy.props import EnumProperty

from .op_publish import publish_entity_step
from ..core.project import is_step_valid_for_context
from ..core import vocab
# ui.state_manager is imported LAZILY in draw() (not at module level): otherwise a
# cycle - ui.state_manager loads the operators package (op_update_imports), which loads this
# module, before draw_state_manager exists. Deferred import = cycle broken.

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    # Same pattern as op_publish.py/op_new_asset.py - create_project lives at the repo root.
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


class YLOS_UL_ExportStates(bpy.types.UIList):
    bl_idname = "YLOS_UL_export_states"

    def draw_item(self, context, layout, data, item, icon, active_data, active_prop,
                  index, flt_flag):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        label = item.entity or "(no entity)"
        row.label(text=f"{label}  ·  {item.step}", icon="EXPORT")
        if item.last_version:
            tag = row.row()
            tag.alignment = "RIGHT"
            tag.label(text=f"v{item.last_version:03d}")


class YLOS_OT_StateAddExport(bpy.types.Operator):
    bl_idname = "ylos.state_add_export"
    bl_label = "Add Export State"
    bl_description = "Add an export state to the publish recipe (pre-filled with the current asset/step)"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        state = scene.ylos_export_states.add()
        state.entity = scene.ylos_current_asset or ""
        # scene.ylos_current_step and state.step share STEP_ITEMS_ALL -> assignment ALWAYS
        # valid at the Blender level (same enum domain), but not necessarily valid for the
        # entity's real FAMILY (ylos_current_step never realigns on its own at a
        # context change - see op_new_asset.py fix). Without this: a stale step (e.g.
        # 'modeling' copied from a previous Asset) lands in the state, Publish rejects it
        # later with a message that says nothing about the origin of the problem. We revalidate here
        # against resolve_entity() - the same authoritative disk source as at publish
        # (op_publish.py::publish_entity_step) - and fall back to the family's first valid
        # step on a mismatch, never a silent failure later.
        step = scene.ylos_current_step
        if state.entity and scene.ylos_project_path:
            resolved = _cp().resolve_entity(scene.ylos_project_path, state.entity)
            if resolved is not None and not is_step_valid_for_context(step, resolved["family"]):
                fallback = vocab.values(
                    vocab.STEP_ITEMS.get(resolved["family"].upper(), vocab.STEP_ITEMS["ASSET"])
                )
                if fallback:
                    old_step = step
                    step = fallback[0]
                    self.report(
                        {"WARNING"},
                        f"Step '{old_step}' invalid for {resolved['family']} '{state.entity}' "
                        f"- defaulted to '{step}'.",
                    )
        state.step = step
        state.enabled = True
        scene.ylos_export_states_index = len(scene.ylos_export_states) - 1
        self.report({"INFO"}, f"Added export state: {state.entity or '(no entity)'} / {state.step}")
        return {"FINISHED"}


class YLOS_OT_StateRemoveExport(bpy.types.Operator):
    bl_idname = "ylos.state_remove_export"
    bl_label = "Remove Export State"
    bl_description = "Remove the active export state"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        scene = context.scene
        idx = scene.ylos_export_states_index
        if not (0 <= idx < len(scene.ylos_export_states)):
            self.report({"WARNING"}, "No export state selected.")
            return {"CANCELLED"}
        scene.ylos_export_states.remove(idx)
        scene.ylos_export_states_index = min(idx, len(scene.ylos_export_states) - 1)
        return {"FINISHED"}


class YLOS_OT_StateMoveExport(bpy.types.Operator):
    bl_idname = "ylos.state_move_export"
    bl_label = "Move Export State"
    bl_description = "Reorder the active export state"
    bl_options = {"REGISTER", "UNDO"}

    direction: EnumProperty(
        items=[("UP", "Up", ""), ("DOWN", "Down", "")],
        default="UP",
    )

    def execute(self, context):
        scene = context.scene
        states = scene.ylos_export_states
        idx = scene.ylos_export_states_index
        if not (0 <= idx < len(states)):
            return {"CANCELLED"}
        new_idx = idx - 1 if self.direction == "UP" else idx + 1
        if not (0 <= new_idx < len(states)):
            return {"CANCELLED"}
        states.move(idx, new_idx)
        scene.ylos_export_states_index = new_idx
        return {"FINISHED"}


class YLOS_OT_PublishStates(bpy.types.Operator):
    """The State Manager's SINGLE Publish button: runs all 'enabled' export states."""
    bl_idname = "ylos.publish_states"
    bl_label = "Publish"
    bl_description = "Run every enabled export state in one go (Prism-style single Publish)"
    bl_options = {"REGISTER"}

    def execute(self, context):
        scene = context.scene
        project_path = scene.ylos_project_path
        if not project_path:
            self.report({"ERROR"}, "No active project.")
            return {"CANCELLED"}

        states = [s for s in scene.ylos_export_states if s.enabled]
        if not states:
            self.report({"WARNING"}, "No enabled export state to publish.")
            return {"CANCELLED"}

        n_ok = n_fail = 0
        for s in states:
            # A failure does NOT interrupt the following ones (the failed state's staging stays
            # preserved for audit/retry, see two-phase contract). Full detail in the console.
            result = publish_entity_step(
                context, project_path, s.entity, s.step,
                allow_full_scene=s.allow_full_scene, comment=s.comment,
            )
            s.last_result = result["message"]
            s.last_version = result["version"]
            if result["ok"]:
                n_ok += 1
            else:
                n_fail += 1
                print(f"[Ylos State Manager] FAIL {s.entity}/{s.step}: {result['message']}")
            if result["warning"]:
                print(f"[Ylos State Manager] WARN {s.entity}/{s.step}: {result['warning']}")

        level = {"INFO"} if n_fail == 0 else {"WARNING"}
        self.report(level, f"Publish: {n_ok} ok, {n_fail} failed (see console for details).")
        return {"FINISHED"} if n_ok else {"CANCELLED"}


class YLOS_OT_OpenStateManager(bpy.types.Operator):
    """Opens the State Manager in a window (large popup), Prism-style - same draw as the
    N-panel section (draw_state_manager, never duplicated)."""
    bl_idname = "ylos.open_state_manager"
    bl_label = "State Manager"
    bl_description = "Open the Ylos State Manager (export states + imports)"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=500)

    def draw(self, context):
        from ..ui.state_manager import draw_state_manager  # lazy - see note at the top of the module
        draw_state_manager(self.layout, context)

    def execute(self, context):
        return {"FINISHED"}
