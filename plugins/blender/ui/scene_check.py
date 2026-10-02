# -*- coding: utf-8 -*-
"""Scene Check window body. Moved out of the removed N-panel section (YLOS_PT_SceneCheck);
the operators (ylos.run_scene_check / fix_all / auto_fix) are unchanged - this is only layout.
Mounted by operators/op_windows.py (ylos.open_scene_check)."""

from ..operators.op_scene_check import get_cached_results
from .common import draw_context_card, has_asset

_SEVERITY_ICONS = {
    "ERROR":   "CANCEL",
    "WARNING": "ERROR",
    "OK":      "CHECKMARK",
}


def draw_scene_check(layout, context):
    scene = context.scene
    if not has_asset(scene):
        layout.label(text="Pick an asset in the Project Browser first", icon="INFO")
        return
    draw_context_card(layout, scene)
    layout.separator(factor=0.4)

    actions = layout.row(align=True)
    actions.scale_y = 1.2
    actions.operator("ylos.run_scene_check", text="Scan Scene", icon="VIEWZOOM")
    actions.operator("ylos.fix_all",         text="Fix All",    icon="CHECKMARK")

    results = get_cached_results()
    if not results:
        layout.separator(factor=0.3)
        layout.box().label(text="Scan the scene to check naming and readiness.", icon="INFO")
        return

    layout.separator(factor=0.4)
    err  = results["error_count"]
    warn = results["warning_count"]
    summary = layout.box().row(align=True)
    summary.label(text=f"Step: {results['current_step']}", icon="SEQUENCE")
    counts = summary.row(align=True)
    counts.alignment = "RIGHT"
    e = counts.row()
    e.alert = err > 0
    e.label(text=str(err), icon="CANCEL")
    counts.label(text=str(warn), icon="ERROR")

    _draw_issue_group(layout, "This step", results.get("current_issues", []),
                      ok_text="Naming looks clean.")
    next_step = results.get("next_step")
    if next_step:
        _draw_issue_group(layout, f"Ready for {next_step}?",
                          results.get("next_issues", []),
                          ok_text="Scene is ready for the next step.")


def _draw_issue_group(layout, title, issues, ok_text):
    layout.separator(factor=0.3)
    box = layout.box()
    head = box.row(align=True)
    head.label(text=title, icon="DOT")
    tag = head.row()
    tag.alignment = "RIGHT"
    if not issues:
        tag.label(text="OK", icon="CHECKMARK")
        box.label(text=ok_text)
        return
    blocking = sum(1 for i in issues if i["severity"] == "ERROR")
    if blocking:
        t = tag.row()
        t.alert = True
        t.label(text=f"{blocking} blocking", icon="CANCEL")
    else:
        tag.label(text=f"{len(issues)} to review", icon="ERROR")
    for issue in issues:
        _draw_issue(box, issue)


def _draw_issue(parent, issue):
    cell = parent.column(align=True)
    cell.separator(factor=0.2)
    is_error = issue["severity"] == "ERROR"
    line1 = cell.row(align=True)
    line1.alert = is_error
    line1.label(text=issue["obj_name"] or "(scene-level)",
                icon=_SEVERITY_ICONS.get(issue["severity"], "DOT"))
    if issue.get("fix_id"):
        fixr = line1.row()
        fixr.alignment = "RIGHT"
        op = fixr.operator("ylos.auto_fix", text="Fix", icon="TOOL_SETTINGS")
        op.fix_id = issue["fix_id"]
    msg = cell.row(align=True)
    msg.label(text="    " + issue["message"])
