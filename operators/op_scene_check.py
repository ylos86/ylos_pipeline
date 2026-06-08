# -*- coding: utf-8 -*-
# Ylos Pipeline - operators/op_scene_check.py
# Run scene check + individual fix operators.

import bpy
from bpy.props import StringProperty
from ..core.scene_checker import run_scene_check, auto_fix

# Module-level cache — populated by YLOS_OT_RunSceneCheck
_results: dict = {}


def get_cached_results() -> dict:
    return _results


class YLOS_OT_RunSceneCheck(bpy.types.Operator):
    bl_idname = "ylos.run_scene_check"
    bl_label = "Scan Scene"
    bl_description = "Check object naming and next-step readiness"
    bl_options = {"REGISTER"}

    def execute(self, context):
        global _results
        _results = run_scene_check(context)
        n_err  = _results["error_count"]
        n_warn = _results["warning_count"]
        self.report({"INFO"}, f"Scene check: {n_err} error(s), {n_warn} warning(s)")
        return {"FINISHED"}


class YLOS_OT_AutoFix(bpy.types.Operator):
    bl_idname = "ylos.auto_fix"
    bl_label = "Fix"
    bl_description = "Apply automatic fix for this issue"
    bl_options = {"REGISTER", "UNDO"}

    fix_id: StringProperty(default="")

    def execute(self, context):
        if not self.fix_id:
            return {"CANCELLED"}
        msg = auto_fix(self.fix_id, context)
        self.report({"INFO"}, msg)
        # Refresh results after fix
        bpy.ops.ylos.run_scene_check()
        return {"FINISHED"}


class YLOS_OT_FixAll(bpy.types.Operator):
    bl_idname = "ylos.fix_all"
    bl_label = "Fix All Auto"
    bl_description = "Apply all available automatic fixes"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        results = get_cached_results()
        if not results:
            bpy.ops.ylos.run_scene_check()
            results = get_cached_results()

        fixed = 0
        all_issues = (
            results.get("current_issues", []) +
            results.get("next_issues", [])
        )
        for issue in all_issues:
            fix_id = issue.get("fix_id", "")
            if fix_id:
                auto_fix(fix_id, context)
                fixed += 1

        bpy.ops.ylos.run_scene_check()
        self.report({"INFO"}, f"Fixed {fixed} issue(s) automatically.")
        return {"FINISHED"}
