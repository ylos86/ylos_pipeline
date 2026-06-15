# Ylos Prod Pipeline - interface Blender (N-sidebar).
#
# COUCHE UI UNIQUEMENT. Ne duplique aucune logique : importe create_project.py et
# migrate_to_2.0.py depuis le repo (source de verite unique) et appelle leurs fonctions.
# Respecte le principe "logique unique, jamais dupliquee".
#
# Installation : voir plugins/blender/README.md (lien symbolique vers ce fichier).

bl_info = {
    "name": "Ylos Prod Pipeline",
    "author": "Ylos Prod",
    "version": (2, 0, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar (N) > Ylos Prod",
    "description": "Creer des projets/assets et convertir les anciens projets vers le schema 2.0.",
    "category": "Pipeline",
}

import sys
import importlib.util
from pathlib import Path

import bpy
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import StringProperty, EnumProperty, BoolProperty, PointerProperty

# Emplacement du repo (source unique). Modifiable dans le panneau si le repo bouge.
_REPO_DEFAULT = "/Users/sebastiendeoliveirabispo/Desktop/Claude/YlosPipeline"


def _ensure_repo(repo):
    repo = repo or _REPO_DEFAULT
    if repo and repo not in sys.path and Path(repo).is_dir():
        sys.path.append(repo)
    return repo


def _create_project_module(repo):
    """Le module generateur (source unique)."""
    _ensure_repo(repo)
    import create_project as cp
    return cp


def _migrator_module(repo):
    """Charge migrate_to_2.0.py (nom de fichier non importable directement : point)."""
    _ensure_repo(repo)
    path = Path(repo) / "migrate_to_2.0.py"
    spec = importlib.util.spec_from_file_location("ylos_migrate", str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------------------
# Proprietes
# --------------------------------------------------------------------------------------

ENTITY_ITEMS = [
    ("asset", "Asset", "Entite asset (colonne vertebrale)"),
    ("set", "Set", "Assemblage"),
    ("shot", "Shot", "Plan"),
]
TYPE_ITEMS = [
    ("CHARACTER", "Character", ""),
    ("ENVIRONMENT", "Environment", ""),
    ("PROP", "Prop", ""),
    ("VEHICLE", "Vehicle", ""),
    ("FX", "FX", ""),
    ("OTHER", "Other", ""),
]


class YLOS_Props(PropertyGroup):
    repo: StringProperty(name="Repo", subtype="DIR_PATH", default=_REPO_DEFAULT,
                         description="Dossier des scripts (source unique)")
    root: StringProperty(name="$PROJ_ROOT", subtype="DIR_PATH", default="",
                         description="Racine source (vide = variable d'env / fallback)")
    cache: StringProperty(name="$PROJ_CACHE", subtype="DIR_PATH", default="",
                          description="Racine cache (vide = variable d'env / fallback)")
    # nouveau projet
    project_name: StringProperty(name="Nom", default="")
    prod_type: StringProperty(name="Type prod", default="FILM")
    # nouvel asset
    target_project: StringProperty(name="Projet", subtype="DIR_PATH", default="",
                                   description="Chemin d'un projet existant")
    asset_name: StringProperty(name="Nom", default="")
    entity_type: EnumProperty(name="Famille", items=ENTITY_ITEMS, default="asset")
    asset_type: EnumProperty(name="Type", items=TYPE_ITEMS, default="OTHER")
    steps: StringProperty(name="Steps", default="",
                          description="Steps separes par virgules (vide = defauts du projet)")
    # conversion
    convert_project: StringProperty(name="Projet legacy", subtype="DIR_PATH", default="",
                                    description="Projet a convertir vers 2.0")
    dry_run: BoolProperty(name="Simulation (dry-run)", default=True,
                          description="Rapport sans rien modifier")


# --------------------------------------------------------------------------------------
# Operateurs
# --------------------------------------------------------------------------------------

class YLOS_OT_create_project(Operator):
    bl_idname = "ylos.create_project"
    bl_label = "Creer le projet"
    bl_description = "Cree l'arborescence projet + manifeste 2.0"

    def execute(self, context):
        p = context.scene.ylos
        if not p.project_name.strip():
            self.report({'ERROR'}, "Nom de projet vide"); return {'CANCELLED'}
        try:
            cp = _create_project_module(p.repo)
            info = cp.create(p.project_name.strip(), root=(p.root or None), cache=(p.cache or None))
        except Exception as e:
            self.report({'ERROR'}, str(e)); return {'CANCELLED'}
        p.target_project = info["source"]        # pre-rempli pour la creation d'asset
        p.convert_project = info["source"]
        self.report({'INFO'}, f"Projet cree : {info['source']}")
        return {'FINISHED'}


class YLOS_OT_create_asset(Operator):
    bl_idname = "ylos.create_asset"
    bl_label = "Creer l'asset"
    bl_description = "Scaffolde une entite (asset/set/shot) dans le projet"

    def execute(self, context):
        p = context.scene.ylos
        if not p.target_project.strip():
            self.report({'ERROR'}, "Aucun projet cible"); return {'CANCELLED'}
        if not p.asset_name.strip():
            self.report({'ERROR'}, "Nom d'asset vide"); return {'CANCELLED'}
        steps = [s.strip() for s in p.steps.split(",") if s.strip()] or None
        try:
            cp = _create_project_module(p.repo)
            info = cp.create_asset(p.target_project, p.asset_name.strip(),
                                   entity_type=p.entity_type, asset_type=p.asset_type, steps=steps)
        except Exception as e:
            self.report({'ERROR'}, str(e)); return {'CANCELLED'}
        self.report({'INFO'}, f"{info['entity_type']} '{info['name']}' cree : {info['path']}")
        return {'FINISHED'}


class YLOS_OT_convert(Operator):
    bl_idname = "ylos.convert"
    bl_label = "Convertir vers 2.0"
    bl_description = "Migre un projet legacy vers le schema 2.0 (backup automatique, non destructif)"

    def execute(self, context):
        p = context.scene.ylos
        if not p.convert_project.strip():
            self.report({'ERROR'}, "Aucun projet a convertir"); return {'CANCELLED'}
        try:
            mig = _migrator_module(p.repo)
            report = mig.migrate(p.convert_project, dry=p.dry_run, backup=True)
        except Exception as e:
            self.report({'ERROR'}, str(e)); return {'CANCELLED'}
        mode = "DRY-RUN" if p.dry_run else "applique"
        n, r = len(report["entities"]), len(report["renames"])
        self.report({'INFO'}, f"Migration {mode} : {n} entites, {r} renommages")
        for w in report["warnings"]:
            self.report({'WARNING'}, w)
        return {'FINISHED'}


# --------------------------------------------------------------------------------------
# Panneau
# --------------------------------------------------------------------------------------

class YLOS_PT_panel(Panel):
    bl_label = "Ylos Prod Pipeline"
    bl_idname = "YLOS_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Ylos Prod"

    def draw(self, context):
        p = context.scene.ylos
        layout = self.layout

        box = layout.box()
        box.label(text="Reglages", icon="PREFERENCES")
        box.prop(p, "root")
        box.prop(p, "cache")

        box = layout.box()
        box.label(text="Nouveau projet", icon="FILE_NEW")
        box.prop(p, "project_name")
        box.prop(p, "prod_type")
        box.operator("ylos.create_project", icon="ADD")

        box = layout.box()
        box.label(text="Nouvel asset", icon="MESH_DATA")
        box.prop(p, "target_project")
        box.prop(p, "asset_name")
        row = box.row(align=True)
        row.prop(p, "entity_type", text="")
        row.prop(p, "asset_type", text="")
        box.prop(p, "steps")
        box.operator("ylos.create_asset", icon="ADD")

        box = layout.box()
        box.label(text="Convertir un ancien projet", icon="FILE_REFRESH")
        box.prop(p, "convert_project")
        box.prop(p, "dry_run")
        box.operator("ylos.convert", icon="PLAY")


# --------------------------------------------------------------------------------------
# Enregistrement
# --------------------------------------------------------------------------------------

_classes = (YLOS_Props, YLOS_OT_create_project, YLOS_OT_create_asset, YLOS_OT_convert, YLOS_PT_panel)


def register():
    _ensure_repo(_REPO_DEFAULT)
    for c in _classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.ylos = PointerProperty(type=YLOS_Props)


def unregister():
    if hasattr(bpy.types.Scene, "ylos"):
        del bpy.types.Scene.ylos
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
