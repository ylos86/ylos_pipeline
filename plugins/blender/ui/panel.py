# -*- coding: utf-8 -*-
# N-panel unifie "Ylos" (categorie sidebar). Remplace panel_pipeline.py (4 panels empiles
# Project / Asset Context / Scene Settings / Tools) par 4 sections alignees sur le cycle de
# production : Context, Scenefile, Publish, Imports. Scene Settings (infos deja visibles
# dans les Properties natives de Blender) et Tools/migration (ex-Increment 3 abandonne, cf.
# CLAUDE.md) ne sont pas reconduites ici.

import os
import sys
import bpy

from ..core.asset import get_latest_wip_version, get_latest_publish_version
from ..core.thumbnails import load_icon
from ..core import thumbnails, vocab

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


def _has_project(scene):
    return bool(scene.ylos_project_path and scene.ylos_project_name)


def _has_asset(scene):
    return _has_project(scene) and bool(scene.ylos_current_asset)


def _step_folder(scene, sub):
    ctx = scene.ylos_context_type.lower()
    base = {"asset": "assets", "shot": "shots", "set": "sets"}.get(ctx, "assets")
    return os.path.join(
        scene.ylos_project_path, base,
        scene.ylos_current_asset, scene.ylos_current_step, sub,
    )


def _abbrev(label):
    return label[:3]


# ---------------------------------------------------------------------------
# Section: Context
# ---------------------------------------------------------------------------

class YLOS_PT_Context(bpy.types.Panel):
    bl_label = "Context"
    bl_idname = "YLOS_PT_context"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Ylos"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        scene  = context.scene

        if not _has_project(scene):
            col = layout.column(align=True)
            col.scale_y = 1.4
            col.operator("ylos.new_project", icon="ADD", text="New Project")
            col.operator("ylos.open_context", icon="FILE_FOLDER", text="Load Project")
            return

        target = _cp().get_pipeline_target(scene.ylos_project_path)

        head = layout.box().column(align=True)
        top = head.row(align=True)
        top.label(text=scene.ylos_project_name, icon="FUND")
        badge = top.row()
        badge.alignment = "RIGHT"
        badge.label(text=f"{scene.ylos_prod_type}  ·  {target}")

        actions = head.row(align=True)
        op = actions.operator("ylos.open_folder", text="", icon="FOLDER_REDIRECT")
        op.folder_path = scene.ylos_project_path
        actions.operator("ylos.new_asset", icon="ADD", text="New")
        actions.operator("ylos.asset_browser", icon="VIEWZOOM", text="Browse")

        layout.separator(factor=0.5)

        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(scene, "ylos_context_type")
        if scene.ylos_context_type == "ASSET":
            layout.prop(scene, "ylos_asset_type")
        layout.use_property_split = False

        if not scene.ylos_current_asset:
            layout.separator(factor=0.3)
            layout.label(text="No active asset", icon="INFO")
            return

        layout.separator(factor=0.3)

        if bpy.data.is_dirty:
            warn = layout.box().row()
            warn.alert = True
            warn.label(text="Unsaved changes", icon="FILE_HIDDEN")

        box = layout.box()
        col = box.column(align=True)

        name_row = col.row(align=False)
        name_row.label(text=scene.ylos_current_asset, icon="OBJECT_DATA")
        op = name_row.operator("ylos.switch_asset_confirm", text="Switch",
                               icon="ARROW_LEFTRIGHT")
        op.new_asset = scene.ylos_current_asset

        col.separator(factor=0.3)
        col.label(text="Step:", icon="SEQUENCE")
        steps = vocab.STEP_ITEMS.get(scene.ylos_context_type, vocab.STEP_ITEMS["ASSET"])
        step_row = col.row(align=True)
        step_row.scale_y = 1.1
        for value, label, _desc in steps:
            b = step_row.operator("ylos.switch_step_confirm", text=_abbrev(label),
                                  depress=(scene.ylos_current_step == value))
            b.new_step = value


# ---------------------------------------------------------------------------
# Section: Scenefile
# ---------------------------------------------------------------------------

class YLOS_PT_Scenefile(bpy.types.Panel):
    bl_label = "Scenefile"
    bl_idname = "YLOS_PT_scenefile"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Ylos"
    bl_order = 2

    @classmethod
    def poll(cls, context):
        return _has_asset(context.scene)

    def draw(self, context):
        layout = self.layout
        scene  = context.scene

        latest_wip = get_latest_wip_version(
            scene.ylos_project_path, scene.ylos_current_asset,
            scene.ylos_current_step, scene.ylos_context_type.lower(),
        )

        header = layout.row(align=True)
        header.label(text="WIP", icon="FILE_BLEND")
        ver = header.row()
        ver.alignment = "RIGHT"
        ver.label(text=f"v{latest_wip:03d}" if latest_wip else "none yet")

        layout.separator(factor=0.3)
        save_row = layout.row(align=True)
        save_row.scale_y = 1.2
        save_row.operator("ylos.save_wip", text="Save Version", icon="FILE_TICK")

        layout.separator(factor=0.4)
        comment_box = layout.box()
        comment_row = comment_box.row()
        comment_row.enabled = False
        comment_row.prop(scene, "ylos_wip_comment", text="Comment")
        comment_box.label(text="Prepared for INC-4 (not saved yet).", icon="INFO")

        layout.separator(factor=0.4)
        open_row = layout.row(align=True)
        open_row.operator("ylos.open_latest_wip", text="Open Latest", icon="IMPORT")
        open_row.operator("ylos.open_wip", text="", icon="TRIA_DOWN")

        op = layout.operator("ylos.open_folder", text="Open WIP Folder",
                             icon="FOLDER_REDIRECT")
        op.folder_path = _step_folder(scene, "wip")


# ---------------------------------------------------------------------------
# Section: Publish
# ---------------------------------------------------------------------------

class YLOS_PT_Publish(bpy.types.Panel):
    bl_label = "Publish"
    bl_idname = "YLOS_PT_publish"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Ylos"
    bl_order = 3

    @classmethod
    def poll(cls, context):
        return _has_asset(context.scene)

    def draw(self, context):
        layout = self.layout
        scene  = context.scene
        cp = _cp()

        project_path = scene.ylos_project_path
        asset_name   = scene.ylos_current_asset
        step         = scene.ylos_current_step
        ctx_type     = scene.ylos_context_type.lower()

        target = cp.get_pipeline_target(project_path)
        ext = ".glb" if target == "web" else ".usd"
        next_ver = get_latest_publish_version(project_path, asset_name, step, ctx_type) + 1

        box = layout.box()
        box.label(text="Next publish:", icon="EXPORT")
        box.label(text=f"{asset_name}_{step}_v{next_ver:03d}{ext} ({target})")

        layout.separator(factor=0.3)
        pub_row = layout.row(align=True)
        pub_row.scale_y = 1.2
        pub_row.operator("ylos.publish", text="Publish Step", icon="EXPORT")

        if thumbnails.LAST_ERROR:
            err = layout.box().row()
            err.alert = True
            err.label(text=thumbnails.LAST_ERROR, icon="ERROR")

        layout.separator(factor=0.4)

        latest = cp.latest_publish_artifact(project_path, asset_name, step, ctx_type)
        last_box = layout.box()
        if latest:
            row = last_box.row(align=True)
            icon_id = 0
            abs_path = latest.get("abs_path")
            if abs_path:
                icon_id = load_icon(os.path.join(os.path.dirname(abs_path), "thumb.png"))
            if icon_id:
                row.template_icon(icon_value=icon_id, scale=4.0)
            info = row.column(align=True)
            info.label(text=f"v{latest.get('version', 0):03d}", icon="FILE")
            info.label(text=os.path.basename(latest.get("artifact") or ""))
        else:
            last_box.label(text="No publish yet for this step", icon="INFO")

        layout.separator(factor=0.3)
        load_row = layout.row(align=True)
        load_row.operator("ylos.load_latest_publish", text="Load Latest", icon="IMPORT")
        load_row.operator("ylos.load_publish", text="", icon="TRIA_DOWN")

        op = layout.operator("ylos.open_folder", text="Open Publish Folder",
                             icon="FOLDER_REDIRECT")
        op.folder_path = _step_folder(scene, "publish")


# ---------------------------------------------------------------------------
# Section: Imports (read-only - prepare INC-5)
# ---------------------------------------------------------------------------

class YLOS_PT_Imports(bpy.types.Panel):
    bl_label = "Imports"
    bl_idname = "YLOS_PT_imports"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Ylos"
    bl_order = 4

    @classmethod
    def poll(cls, context):
        return _has_asset(context.scene)

    def draw(self, context):
        layout = self.layout
        scene  = context.scene
        cp = _cp()

        project_path = scene.ylos_project_path
        asset_name   = scene.ylos_current_asset
        ctx_type     = scene.ylos_context_type.lower()

        steps = vocab.values(vocab.STEP_ITEMS.get(scene.ylos_context_type, vocab.STEP_ITEMS["ASSET"]))

        rows = []
        for step in steps:
            for entry in cp.list_publishes(project_path, asset_name, step, ctx_type):
                if entry.get("status") == "complete":
                    rows.append((step, entry))

        if not rows:
            layout.label(text="No publishes yet for this entity", icon="INFO")
            return

        rows.sort(key=lambda pair: pair[1].get("published_utc") or "", reverse=True)

        col = layout.column(align=True)
        for step, entry in rows[:8]:
            artifact = entry.get("artifact") or ""
            ext = os.path.splitext(artifact)[1]
            row = col.row(align=True)
            row.label(text=step, icon="SEQUENCE")
            right = row.row()
            right.alignment = "RIGHT"
            right.label(text=f"v{entry.get('version', 0):03d}{ext}")

        if len(rows) > 8:
            foot = layout.row()
            foot.alignment = "RIGHT"
            foot.label(text=f"+{len(rows) - 8} more")
