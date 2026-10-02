# -*- coding: utf-8 -*-
"""Open an entity's work file from the Project Browser.

Until now a click in the browser only SWITCHED the context (ylos_current_asset) - nothing was
opened, which read as "nothing happened". Opening is a different gesture from selecting, so it
is a different operator: the browser card carries both.

Which file to open is NOT decided here: create_project.resolve_open_target (principle 5, same
resolver as the web UI's open-blender verb) returns the latest WIP -> default scene -> latest
publish. This operator only realises the one case that is a plain open (a .blend WIP). When
there is no WIP yet it switches the context and says so, rather than guessing an import: the
launcher's import/create verbs and the Scenefile window's New Scene own that path.
"""

import os
import sys

import bpy
from bpy.props import StringProperty

from ..core import vocab
from ..core.asset import list_wip_versions
from ..core.thumbnails import load_thumb_icon
from ..ui.browser import browser_window_open

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


def _fmt_date(raw):
    """Version dates come in two shapes (legacy 'Jul 15, 01:14' and ISO-8601 from the manifest,
    microseconds + offset included). Show one short form; keep anything unparseable as is."""
    if not raw:
        return ""
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(raw)).strftime("%d %b %H:%M")
    except (ValueError, TypeError):
        return str(raw)


class YLOS_OT_OpenEntity(bpy.types.Operator):
    bl_idname = "ylos.open_entity"
    bl_label = "Open"
    bl_description = "Open this entity's WIP: latest version, or pick an earlier one"
    bl_options = {"REGISTER"}

    entity: StringProperty(name="Entity", default="")
    # Empty = keep the current step when the entity is the active one, else the entity's first
    # declared step (resolve_open_target's fallback).
    step: StringProperty(name="Step", default="")

    _versions = []
    _resolved_step = ""

    def _step(self, context):
        scene = context.scene
        step = self.step or (scene.ylos_current_step
                             if self.entity == scene.ylos_current_asset else None)
        if step:
            return step
        # Entity other than the active one, no step asked: the orchestrator's fallback (first
        # declared step of its manifest).
        return _cp().resolve_open_target(
            self.entity, dcc="blender", step=None,
            project_root=scene.ylos_project_path).get("step") or ""

    def invoke(self, context, event):
        if not self.entity.strip():
            self.report({"ERROR"}, "Entity name cannot be empty.")
            return {"CANCELLED"}
        scene = context.scene
        self._resolved_step = self._step(context)
        self._versions = list_wip_versions(
            scene.ylos_project_path, self.entity, self._resolved_step,
            scene.ylos_context_type.lower()) if self._resolved_step else []
        if not self._versions:
            # Nothing to choose between: execute() switches the context and explains.
            return self.execute(context)
        for v in self._versions:
            load_thumb_icon(v["path"])
        return context.window_manager.invoke_popup(self, width=520)

    def draw(self, context):
        layout = self.layout
        newest = self._versions[-1]

        head = layout.box().row()
        head.label(text=f"{self.entity}  /  {self._resolved_step}", icon="FILE_BLEND")
        head.label(text=f"{len(self._versions)} version(s)")

        if bpy.data.is_dirty:
            # open_mainfile through Python discards unsaved work silently: say so, loudly.
            warn = layout.row()
            warn.alert = True
            warn.label(text="Unsaved changes in the current file will be lost", icon="ERROR")

        layout.separator(factor=0.4)
        top = layout.row()
        top.scale_y = 1.6
        op = top.operator("ylos.open_wip_version",
                          text=f"Open Latest  -  v{newest['version']:03d}", icon="IMPORT")
        op.version_path = newest["path"]

        older = list(reversed(self._versions[:-1]))
        if not older:
            return
        layout.separator(factor=0.4)
        layout.label(text="Earlier versions", icon="RECOVER_LAST")
        shown = older[:12]
        grid = layout.grid_flow(row_major=True, columns=3, even_columns=True, align=False)
        for v in shown:
            col = grid.box().column(align=True)
            icon_id = load_thumb_icon(v["path"])
            if icon_id:
                col.template_icon(icon_value=icon_id, scale=4.0)
            else:
                col.label(text="no preview", icon="IMAGE_DATA")
            info = col.row()
            info.label(text=f"v{v['version']:03d}")
            if v.get("date"):
                info.label(text=_fmt_date(v["date"]))
            if v.get("comment"):
                small = col.row()
                small.scale_y = 0.8
                small.label(text=v["comment"])
            col.operator("ylos.open_wip_version", text="Open", icon="IMPORT").version_path = v["path"]
        if len(older) > len(shown):
            layout.label(text=f"+ {len(older) - len(shown)} older (Scenefile > Pick Version)")

    def execute(self, context):
        scene = context.scene
        project = scene.ylos_project_path
        if not project:
            self.report({"ERROR"}, "No active project.")
            return {"CANCELLED"}

        step = self.step or (scene.ylos_current_step
                             if self.entity == scene.ylos_current_asset else None)
        target = _cp().resolve_open_target(
            self.entity, dcc="blender", step=step, project_root=project)

        if target.get("exists") and target.get("kind") == "wip" and target.get("path"):
            if not os.path.isfile(target["path"]):
                self.report({"ERROR"}, f"File not found: {target['path']}")
                return {"CANCELLED"}
            kwargs = {"filepath": target["path"]}
            if browser_window_open():
                # Keep the floating browser alive: the opened file's own window layout would
                # otherwise replace the current one and close it.
                kwargs["load_ui"] = False
            bpy.ops.wm.open_mainfile(**kwargs)
            return {"FINISHED"}

        # Nothing to plainly open (no WIP yet, or only a default scene / publish): select the
        # entity+step so the Scenefile window is ready, and say what is missing.
        scene.ylos_current_asset = self.entity
        resolved_step = target.get("step") or step
        if resolved_step and resolved_step in {v for v, _l, _d in vocab.STEP_ITEMS_ALL}:
            scene.ylos_current_step = resolved_step
        reason = target.get("reason") or "no WIP file yet"
        self.report({"WARNING"},
                    f"{self.entity}: {reason}. Context switched - use Scenefile > New Scene.")
        return {"FINISHED"}
