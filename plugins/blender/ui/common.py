# -*- coding: utf-8 -*-
"""Shared helpers of the Ylos windows (Project Browser, Scenefile, Scene Check).

There is no N-panel any more (Prism model: top-bar menu + windows). The one thing every
window needs is the same answer to "where am I?" - project, entity, step - so it is drawn by
ONE function (draw_context_card) instead of being re-implemented per window.

draw() NEVER touches the disk beyond what core.asset already TTL-caches.
"""

import os
import sys

import bpy

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


def has_project(scene):
    return bool(scene.ylos_project_path and scene.ylos_project_name)


def has_asset(scene):
    return has_project(scene) and bool(scene.ylos_current_asset)


def step_folder(scene, sub):
    ctx = scene.ylos_context_type.lower()
    base = {"asset": "assets", "shot": "shots", "set": "sets"}.get(ctx, "assets")
    return os.path.join(
        scene.ylos_project_path, base,
        scene.ylos_current_asset, scene.ylos_current_step, sub,
    )


def draw_context_card(layout, scene):
    """Project / pipeline target / active entity + step / unsaved warning. Read-only: switching
    is the Project Browser's job, never done from here."""
    box = layout.box().column(align=True)

    top = box.row(align=True)
    top.label(text=scene.ylos_project_name, icon="FUND")
    badge = top.row()
    badge.alignment = "RIGHT"
    badge.label(text=f"{scene.ylos_prod_type}  ·  {cp().get_pipeline_target(scene.ylos_project_path)}")

    if scene.ylos_current_asset:
        line = box.row(align=True)
        line.label(text=scene.ylos_current_asset, icon="OBJECT_DATA")
        step = line.row()
        step.alignment = "RIGHT"
        step.label(text=scene.ylos_current_step.capitalize())
    else:
        box.label(text="No active asset", icon="INFO")

    if bpy.data.is_dirty:
        warn = box.row()
        warn.alert = True
        warn.label(text="Unsaved changes", icon="FILE_HIDDEN")
