# -*- coding: utf-8 -*-
"""Section 'Assets' du N-panel — browser d'entites VISUEL (parite Prism).

Refonte : la version precedente etait une liste de boutons texte, avec des steps abreges a
2-3 lettres ('M', 'Ly', 'Lt'). Deux problemes reels, tous deux nommes dans
docs/ui-workstream.md comme hypotheses — verifies depuis :
  - Aucune image. Un browser d'assets 3D qui ne montre pas les assets oblige a lire des noms
    pour reconnaitre de la geometrie. C'est le point ou Prism gagne le plus nettement.
  - Les abreviations ('Sur' pour surfacing, 'Ly' pour layout) coutent un decodage a chaque
    lecture, pour gagner ~30px de large. Mauvais echange : le N-panel est etroit, mais il est
    aussi redimensionnable — le nom complet reste lisible et se tronque proprement.

Le draw() ne touche JAMAIS le disque : les vignettes viennent de core.entity_thumbs (cache
TTL + invalidation explicite), la liste d'entites de core.asset.list_project_entities (cache
TTL 4s deja en place). Meme regle que op_scene_check / op_io.
"""

import bpy

from ..core.asset import list_project_entities, get_asset_step_status
from ..core.project import ASSET_STEPS, SHOT_STEPS, SET_STEPS
from ..core import entity_thumbs

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

# Libelle de provenance de la vignette. Une preview de WIP n'engage pas la meme confiance
# qu'un publish : on l'ecrit, on ne laisse pas croire a un publie. Rien pour 'publish' —
# le cas normal ne merite pas de bruit.
_SOURCE_LABELS = {
    "custom": ("Preview manuelle", "PINNED"),
    "wip":    ("Vignette du WIP (aucun publish)", "FILE_BLEND"),
    "legacy": ("Vignette legacy", "FILE_IMAGE"),
    "none":   ("Aucune vignette", "IMAGE_DATA"),
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

        # ── Preview de l'entite active ─────────────────────────────────────────────
        scale = _PREVIEW_SCALE.get(scene.ylos_preview_size, 7.0)
        active = scene.ylos_current_asset
        icon_id, source = thumbs.get(active, (0, "none"))

        if scale > 0.0 and active:
            box = layout.box()
            col = box.column(align=True)
            if icon_id:
                col.template_icon(icon_value=icon_id, scale=scale)
            else:
                # Placeholder explicite plutot qu'un trou : un vide non explique se lit
                # comme un bug, alors que c'est un etat legitime (rien encore sauve).
                ph = col.column(align=True)
                ph.scale_y = max(scale * 0.42, 1.0)
                ph.label(text="", icon="IMAGE_DATA")
                ph.label(text="Aucune vignette")

            label, licon = _SOURCE_LABELS.get(source, (None, "NONE"))
            foot = col.row(align=True)
            foot.scale_y = 0.9
            if label:
                foot.label(text=label, icon=licon)
            else:
                foot.label(text="Vignette du dernier publish", icon="CHECKMARK")
            foot.operator("ylos.capture_preview", text="", icon="RESTRICT_RENDER_OFF")
            if source == "custom":
                foot.operator("ylos.capture_preview", text="", icon="X").clear = True

        # ── Liste des entites ──────────────────────────────────────────────────────
        layout.separator(factor=0.3)

        for entity in entities:
            name = entity["name"]
            is_active = (name == active)
            e_icon, _e_src = thumbs.get(name, (0, "none"))

            row = layout.row(align=True)
            row.scale_y = 1.15

            kw = {"text": name, "depress": is_active}
            # icon_value (vignette reelle) quand elle existe, sinon l'icone de type : un
            # bouton sans icone du tout deplacerait le texte d'une ligne a l'autre.
            if e_icon:
                kw["icon_value"] = e_icon
            else:
                kw["icon"] = _TYPE_ICONS.get(entity["type"], "OBJECT_DATA")
            row.operator("ylos.switch_asset_confirm", **kw).new_asset = name

        # ── Steps de l'entite active, en toutes lettres ───────────────────────────
        if active and any(e["name"] == active for e in entities):
            status = get_asset_step_status(scene.ylos_project_path, active, ctx_type)
            layout.separator(factor=0.4)
            step_col = layout.column(align=True)
            for step in steps:
                published = status.get(step, False)
                row = step_col.row(align=True)
                row.scale_y = 1.05
                btn = row.operator(
                    "ylos.switch_step_confirm",
                    text=step.capitalize(),
                    # L'etat de publish se lit d'un coup d'oeil, sans ouvrir la web UI :
                    # coche pleine = au moins un publish, cercle vide = rien encore.
                    icon="CHECKBOX_HLT" if published else "CHECKBOX_DEHLT",
                    depress=(scene.ylos_current_step == step),
                )
                btn.new_step = step

        layout.separator(factor=0.3)

        foot = layout.row(align=True)
        foot.scale_y = 0.9
        foot.operator("ylos.new_asset", text="+ New", icon="ADD")
