# -*- coding: utf-8 -*-
# State Manager (facon Prism) : operateurs de gestion des export states + le Publish unique.
#
# Le Publish batch itere les states 'enabled' et delegue CHAQUE publish a
# publish_entity_step (op_publish.py) - logique unique, principe 5. Aucune duplication de la
# logique de publish ici : cet operateur n'est qu'un orchestrateur de la recette.

import bpy
from bpy.props import EnumProperty

from .op_publish import publish_entity_step
# ui.state_manager est importe PARESSEUSEMENT dans draw() (pas au niveau module) : sinon
# cycle - ui.state_manager charge le package operators (op_update_imports), qui charge ce
# module, avant que draw_state_manager n'existe. Import differe = cycle casse.


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
        # scene.ylos_current_step et state.step partagent STEP_ITEMS_ALL -> assignation toujours valide.
        state.step = scene.ylos_current_step
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
    """Le bouton Publish UNIQUE du State Manager : execute tous les export states 'enabled'."""
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
            # Un echec n'interrompt PAS les suivants (le staging du state en echec reste
            # preserve pour audit/retry, cf. contrat deux-phases). Detail complet en console.
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
    """Ouvre le State Manager en fenetre (popup large), facon Prism - meme draw que la
    section N-panel (draw_state_manager, jamais duplique)."""
    bl_idname = "ylos.open_state_manager"
    bl_label = "State Manager"
    bl_description = "Open the Ylos State Manager (export states + imports)"
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=500)

    def draw(self, context):
        from ..ui.state_manager import draw_state_manager  # lazy - cf. note en tete de module
        draw_state_manager(self.layout, context)

    def execute(self, context):
        return {"FINISHED"}
