# -*- coding: utf-8 -*-
"""'Assets' section of the N-panel — VISUAL entity browser (Prism parity).

Rework: the previous version was a list of text buttons, with steps abbreviated to
2-3 letters ('M', 'Ly', 'Lt'). Two real problems, both named in
docs/ui-workstream.md as hypotheses — verified since:
  - No image. A 3D asset browser that does not show the assets forces reading names
    to recognize geometry. This is where Prism wins most clearly.
  - The abbreviations ('Sur' for surfacing, 'Ly' for layout) cost a decoding on every
    read, to save ~30px of width. A bad trade: the N-panel is narrow, but it is
    also resizable — the full name stays readable and truncates cleanly.

draw() NEVER touches the disk: the thumbnails come from core.entity_thumbs (TTL cache
+ explicit invalidation), the entity list from core.asset.list_project_entities (TTL 4s
cache already in place). Same rule as op_scene_check / op_io.
"""

import bpy

from ..core.asset import list_project_entities, get_entity_step_status
from ..core.project import ASSET_STEPS, SHOT_STEPS, SET_STEPS
from ..core import entity_thumbs
from ..core import vocab

_STEP_MAP = {
    "asset": ASSET_STEPS,
    "shot":  SHOT_STEPS,
    "set":   SET_STEPS,
}

_TYPE_ICONS = {
    "PROP":        "MESH_CUBE",
    "CHARACTER":   "ARMATURE_DATA",
    "ENVIRONMENT": "WORLD",
    "SHOT":        "SEQUENCE",
    "SET":         "PACKAGE",
}

# Thumbnail provenance label. A WIP preview does not carry the same confidence
# as a publish: we write it, we don't let it pass for a published one. Nothing for 'publish' —
# the normal case does not deserve noise.
_SOURCE_LABELS = {
    "custom": ("Manual preview", "PINNED"),
    "wip":    ("WIP thumbnail (no publish)", "FILE_BLEND"),
    "legacy": ("Legacy thumbnail", "FILE_IMAGE"),
    "none":   ("No thumbnail", "IMAGE_DATA"),
}

_PREVIEW_SCALE = {"OFF": 0.0, "S": 4.0, "M": 7.0, "L": 11.0}


class YLOS_PT_AssetListPanel(bpy.types.Panel):
    bl_label = "Assets"
    bl_idname = "YLOS_PT_asset_list"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Ylos"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        s = context.scene
        return bool(s.ylos_project_path and s.ylos_project_name)

    def draw_header(self, context):
        """Entity count of the active family in the header (TTL cache, no extra disk hit):
        tells whether the project is empty before the section is even unfolded."""
        scene = context.scene
        entities = list_project_entities(scene.ylos_project_path,
                                         scene.ylos_context_type.lower())
        if entities:
            self.layout.label(text=str(len(entities)))

    def draw(self, context):
        layout = self.layout
        scene  = context.scene

        ctx_type = scene.ylos_context_type.lower()
        entities = list_project_entities(scene.ylos_project_path, ctx_type)
        steps    = _STEP_MAP.get(ctx_type, ASSET_STEPS)
        thumbs   = entity_thumbs.get_entity_thumbs(
            scene.ylos_project_path, [e["name"] for e in entities], ctx_type
        )

        header = layout.row(align=True)
        header.prop(scene, "ylos_context_type", text="")
        header.prop(scene, "ylos_preview_size", text="", icon_only=True)
        header.operator("ylos.refresh_asset_list", text="", icon="FILE_REFRESH")
        header.operator("ylos.asset_browser",      text="", icon="VIEWZOOM")

        if not entities:
            layout.separator(factor=0.3)
            layout.label(text="No assets found", icon="INFO")
            layout.operator("ylos.new_asset", text="+ Create first asset", icon="ADD")
            return

        # ── Active entity preview ──────────────────────────────────────────────────
        scale = _PREVIEW_SCALE.get(scene.ylos_preview_size, 7.0)
        active = scene.ylos_current_asset
        icon_id, source = thumbs.get(active, (0, "none"))

        if scale > 0.0 and active:
            box = layout.box()
            col = box.column(align=True)
            if icon_id:
                col.template_icon(icon_value=icon_id, scale=scale)
            else:
                # Explicit placeholder rather than a hole: an unexplained blank reads
                # like a bug, whereas it is a legitimate state (nothing saved yet).
                ph = col.column(align=True)
                ph.scale_y = max(scale * 0.42, 1.0)
                ph.label(text="", icon="IMAGE_DATA")
                ph.label(text="No thumbnail")

            label, licon = _SOURCE_LABELS.get(source, (None, "NONE"))
            foot = col.row(align=True)
            foot.scale_y = 0.9
            if label:
                foot.label(text=label, icon=licon)
            else:
                foot.label(text="Latest publish thumbnail", icon="CHECKMARK")
            foot.operator("ylos.capture_preview", text="", icon="RESTRICT_RENDER_OFF")
            if source == "custom":
                foot.operator("ylos.capture_preview", text="", icon="X").clear = True

        # ── Entity list ────────────────────────────────────────────────────────────
        layout.separator(factor=0.3)

        for entity in entities:
            name = entity["name"]
            is_active = (name == active)
            e_icon, _e_src = thumbs.get(name, (0, "none"))

            row = layout.row(align=True)
            row.scale_y = 1.15

            kw = {"text": name, "depress": is_active}
            # icon_value (real thumbnail) when it exists, otherwise the type icon: a
            # button with no icon at all would shift the text from one line to another.
            if e_icon:
                kw["icon_value"] = e_icon
            else:
                kw["icon"] = _TYPE_ICONS.get(entity["type"], "OBJECT_DATA")
            row.operator("ylos.switch_asset_confirm", **kw).new_asset = name

        # ── Active entity's steps, in full, each with its status marker ────────────
        if active and any(e["name"] == active for e in entities):
            # Schema 2.2 status per step (explicit review/approved from the manifest,
            # otherwise empty/wip/published derived from disk) - TTL-cached read, single
            # source create_project.get_step_status.
            status = get_entity_step_status(scene.ylos_project_path, active)
            # The entity's DECLARED steps win over the family default: an entity created
            # with a subset of steps must not show departments it does not have.
            declared = [s for s in steps if s in status] or steps
            layout.separator(factor=0.4)
            step_col = layout.column(align=True)
            for step in declared:
                entry = status.get(step) or {}
                value = entry.get("status", "empty")
                row = step_col.row(align=True)
                row.scale_y = 1.05
                btn = row.operator(
                    "ylos.switch_step_confirm",
                    text=vocab.PRESENTATION["step"].get(step, (step.capitalize(),))[0],
                    # Status read at a glance, without opening the web UI. Replaces the
                    # published/not-published checkbox: 'review' and 'approved' are
                    # production facts a checkbox could not express.
                    icon=vocab.status_icon(value),
                    depress=(scene.ylos_current_step == step),
                )
                btn.new_step = step
                # An EXPLICIT status is a human decision, not a disk state: marked as such
                # so nobody reads "Approved" as "the exporter said so".
                if entry.get("explicit"):
                    tag = row.row()
                    tag.alignment = "RIGHT"
                    tag.scale_x = 0.55
                    tag.label(text=vocab.status_label(value))

        layout.separator(factor=0.3)

        foot = layout.row(align=True)
        foot.scale_y = 0.9
        foot.operator("ylos.new_asset", text="+ New", icon="ADD")
