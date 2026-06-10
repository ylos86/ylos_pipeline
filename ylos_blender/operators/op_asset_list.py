# -*- coding: utf-8 -*-
# Ylos Pipeline - operators/op_asset_list.py
# Searchable asset browser popup (Option B).

import bpy
from bpy.props import StringProperty
from ylos_core.asset import list_project_entities, invalidate_entity_cache


class YLOS_OT_AssetBrowser(bpy.types.Operator):
    """Searchable popup listing all assets in the project."""
    bl_idname = "ylos.asset_browser"
    bl_label = "Switch Asset"
    bl_description = "Browse and switch to any asset in the project"
    bl_options = {"REGISTER"}

    search: StringProperty(
        name="Search",
        description="Filter by name",
        default="",
        options={"TEXTEDIT_UPDATE"},
    )

    def invoke(self, context, event):
        if not context.scene.ylos_project_path:
            self.report({"ERROR"}, "No active project.")
            return {"CANCELLED"}
        self.search = ""
        return context.window_manager.invoke_popup(self, width=260)

    def draw(self, context):
        scene  = context.scene
        layout = self.layout

        # Search bar
        row = layout.row(align=True)
        row.prop(self, "search", text="", icon="VIEWZOOM")

        layout.separator(factor=0.3)

        ctx_type = scene.ylos_context_type.lower()
        entities = list_project_entities(scene.ylos_project_path, ctx_type)

        search = self.search.lower()
        filtered = [e for e in entities if search in e["name"].lower()]

        if not filtered:
            layout.label(text="No results", icon="INFO")
            return

        for entity in filtered:
            is_active = (entity["name"] == scene.ylos_current_asset)
            row = layout.row(align=True)

            # Highlight active
            if is_active:
                row.alert = False
                op = row.operator(
                    "ylos.switch_asset_confirm",
                    text=entity["name"],
                    icon="CHECKMARK",
                    depress=True,
                )
            else:
                op = row.operator(
                    "ylos.switch_asset_confirm",
                    text=entity["name"],
                    icon=entity["type_icon"],
                    depress=False,
                )

            op.new_asset = entity["name"]

        layout.separator(factor=0.3)
        layout.label(
            text=f"{len(filtered)} / {len(entities)} assets",
            icon="NONE",
        )

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
        self.report({"INFO"}, "Asset list refreshed.")
        return {"FINISHED"}
