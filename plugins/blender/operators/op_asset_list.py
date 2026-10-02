# -*- coding: utf-8 -*-
import bpy
from bpy.props import StringProperty
from ..core.asset import invalidate_entity_cache
from ..core import entity_thumbs
from ..ui.browser import draw_project_browser


class YLOS_OT_AssetBrowser(bpy.types.Operator):
    """Project Browser window: thumbnails grid of the project's entities (Prism model)."""
    bl_idname = "ylos.asset_browser"
    bl_label = "Project Browser"
    bl_description = "Browse the project's entities, switch asset and step"
    bl_options = {"REGISTER"}

    search: StringProperty(
        name="Search",
        description="Filter by name",
        default="",
        options={"TEXTEDIT_UPDATE"},
    )

    def invoke(self, context, event):
        # No 'No active project' error: the window itself offers New / Load Project.
        self.search = ""
        return context.window_manager.invoke_popup(self, width=560)

    def draw(self, context):
        layout = self.layout
        if context.scene.ylos_project_path:
            layout.prop(self, "search", text="", icon="VIEWZOOM")
        draw_project_browser(layout, context, self.search)

    def execute(self, context):
        return {"FINISHED"}


class YLOS_OT_RefreshAssetList(bpy.types.Operator):
    """Force-refresh the asset list cache."""
    bl_idname = "ylos.refresh_asset_list"
    bl_label = "Refresh"
    bl_description = "Refresh the asset list from disk"
    bl_options = {"REGISTER"}

    def execute(self, context):
        invalidate_entity_cache(context.scene.ylos_project_path)
        entity_thumbs.invalidate(context.scene.ylos_project_path)
        self.report({"INFO"}, "Asset list refreshed.")
        return {"FINISHED"}
