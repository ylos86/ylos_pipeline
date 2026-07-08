"""ylos_houdini.py - workflow pipeline Ylos dans Houdini : WIP .hip versionnes, creation
d'entites, chargement des publishes/asset_root en LOPs.

Importable SANS hou : les imports hou vivent dans les fonctions qui en ont besoin, les
fonctions pures (parsing de contexte, versioning, resolution de chemins) sont testables en
python3 nu (cf. tests/test_ylos_houdini.py) et la CI ne depend d'aucune licence Houdini.
Toute la logique de creation/versioning/manifeste vient de create_project.py (logique
unique, cf. CLAUDE.md) - ce module n'est que le pont Houdini, comme plugins/blender/ l'est
pour Blender.

Charge par le package plugins/houdini/ylos.json (PYTHONPATH) ; les outils du shelf
plugins/houdini/toolbar/ylos_pipeline.shelf appellent les fonctions tool_*().

Contexte : le contexte Houdini EST le chemin du hip courant
(<projet>/<famille>/<entite>/<step>/wip/<fichier>) - pas d'etat de session a maintenir ni
a desynchroniser. Blender stocke le sien dans la scene (proprietes), meme idee : le
contexte vit dans le document, jamais dans un etat global du process.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# create_project importe depuis la racine du repo, derivee du chemin REEL de ce fichier
# (os.path.realpath, jamais __file__ nu - meme fix symlink que Blender et le module
# embarque du HDA, cf. build_publish_hda.py). 4 dirname : ylos_houdini.py -> python ->
# houdini -> plugins -> REPO.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.realpath(__file__)))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
import create_project as cp  # noqa: E402

# .hip commercial / .hiplc Indie / .hipnc Apprentice+Education : hou.hipFile.save refuse
# une extension d'une autre licence - meme classe de gotcha que .usdnc/.hdanc (cf.
# CLAUDE.md, extensions Apprentice, jamais supposees a l'avance).
HIP_EXTENSIONS = (".hip", ".hiplc", ".hipnc")
_HIP_EXT_BY_LICENSE = {"Commercial": ".hip", "Indie": ".hiplc"}  # tout le reste: .hipnc

# Detection de version agnostique au nom (seul le suffixe _vNNN.ext compte) - meme
# philosophie que Blender core/asset.py::VERSION_PATTERN : un fichier renomme/migre garde
# sa place dans la continuite de versions.
WIP_VERSION_RE = re.compile(r"_v(\d{3})\.hip(?:lc|nc)?$")


# --------------------------------------------------------------------------------------
# Fonctions pures (testables sans hou)
# --------------------------------------------------------------------------------------

def hip_extension(license_category=None):
    """Extension de sauvegarde selon la licence. 'license_category' (str, ex 'Commercial')
    injectable pour les tests ; sinon lue de hou.licenseCategory() a l'appel."""
    if license_category is None:
        import hou
        license_category = hou.licenseCategory().name()
    return _HIP_EXT_BY_LICENSE.get(license_category, ".hipnc")


def active_project():
    """Projet actif (Path) ou None - meme source que l'UI web et le HDA ylos::publish
    (~/.ylos/active_project, cf. create_project.read_active_project)."""
    return cp.read_active_project()


def parse_wip_context(hip_path):
    """(project_root, entity_name, step) si hip_path a la forme canonique
    <projet>/<famille>/<entite>/<step>/wip/<fichier> d'un vrai projet (project.json
    present), sinon None. Purement lexical + verification du manifeste projet : un chemin
    qui a la bonne forme hors d'un projet reel ne constitue pas un contexte."""
    parts = Path(hip_path).parts
    if len(parts) < 6 or parts[-2] != "wip":
        return None
    family, entity_name, step = parts[-5], parts[-4], parts[-3]
    if family not in cp.ENTITY_DIR.values():
        return None
    project_root = Path(*parts[:-5])
    if not (project_root / cp.PIPELINE_DIR / cp.MANIFEST_NAME).is_file():
        return None
    return project_root, entity_name, step


def list_wip_versions(project_root, entity_name, step):
    """[{'version', 'filename', 'path'}, ...] tries par version, toutes extensions .hip*
    confondues (une meme lignee de WIP peut melanger les licences)."""
    entity_dir, _ = cp._find_asset_entity(project_root, entity_name)
    wip_dir = entity_dir / step / "wip"
    if not wip_dir.is_dir():
        return []
    out = []
    for f in sorted(wip_dir.iterdir()):
        m = WIP_VERSION_RE.search(f.name)
        if f.is_file() and m:
            out.append({"version": int(m.group(1)), "filename": f.name, "path": str(f)})
    return sorted(out, key=lambda e: e["version"])


def next_wip_path(project_root, entity_name, step, license_category=None):
    """(path, version) de la PROCHAINE version WIP : max disque + 1, jamais deduite du nom
    du hip courant (deux sessions sur le meme step ne se marchent pas dessus sur la
    numerotation). Valide 'step' contre le manifeste de l'entite (meme message que
    publish_asset) - attrape la typo avant qu'elle cree une arborescence fantome."""
    entity_dir, manifest_path = cp._find_asset_entity(project_root, entity_name)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    declared = manifest.get("steps", [])
    if declared and step not in declared:
        raise ValueError(
            f"Step '{step}' invalide pour '{entity_name}' (steps declares : {declared})."
        )
    versions = list_wip_versions(project_root, entity_name, step)
    version = (versions[-1]["version"] if versions else 0) + 1
    filename = f"{entity_name}_{step}_v{version:03d}{hip_extension(license_category)}"
    return entity_dir / step / "wip" / filename, version


def list_entities(project_root):
    """[{'name', 'family', 'type', 'steps'}, ...] des entites du projet (manifeste
    lisible), pour les dialogues des outils shelf."""
    project_root = Path(project_root)
    out = []
    for family in cp.ENTITY_DIR.values():
        family_dir = project_root / family
        if not family_dir.is_dir():
            continue
        for d in sorted(family_dir.iterdir()):
            manifest_path = d / cp.ASSET_MANIFEST_NAME
            if not (d.is_dir() and manifest_path.is_file()):
                continue
            try:
                m = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            out.append({"name": d.name, "family": family,
                        "type": m.get("type"), "steps": m.get("steps", [])})
    return out


def asset_root_path(project_root, entity_name):
    """Chemin du asset_root.usda d'une entite - la compo subLayers a referencer dans un
    set/shot. FileNotFoundError si absent (un shot n'en a pas, cf. create_asset)."""
    entity_dir, _ = cp._find_asset_entity(project_root, entity_name)
    path = entity_dir / cp.ASSET_ROOT_NAME
    if not path.is_file():
        raise FileNotFoundError(
            f"Pas de {cp.ASSET_ROOT_NAME} pour '{entity_name}' ({path}) - les shots n'ont "
            f"pas d'asset_root, referencer un publish LOP a la place."
        )
    return path


def latest_lop_publish(project_root, entity_name):
    """Chemin (Path) du layer USD du dernier publish LOP 'complete' de l'entite, ou None
    si aucun. Lit manifest.lop_publishes (contrat ecrit par finalize_publish_version)."""
    entity_dir, manifest_path = cp._find_asset_entity(project_root, entity_name)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = [e for e in manifest.get(cp.LOP_PUBLISHES_KEY, [])
               if e.get("status") == "complete" and e.get("layer")]
    if not entries:
        return None
    best = max(entries, key=lambda e: e["version"])
    return entity_dir / best["layer"]


def env_relative(path, env_name=cp.ENV_ROOT):
    """'$PROJ_ROOT/<relatif>' si 'path' vit sous $PROJ_ROOT - les scenes referencent via
    env, jamais un chemin absolu en dur (principe 1, CLAUDE.md) : le projet reste
    relocalisable entre disque interne et externe. Chemin absolu sinon (projet hors
    racine : on n'invente pas une relocalisabilite qui n'existe pas)."""
    root = os.environ.get(env_name)
    if root:
        try:
            rel = Path(path).resolve().relative_to(Path(root).expanduser().resolve())
            return f"${env_name}/{rel.as_posix()}"
        except (ValueError, OSError):
            pass
    return str(Path(path))


# --------------------------------------------------------------------------------------
# Actions Houdini (hou requis)
# --------------------------------------------------------------------------------------

def save_wip(entity_name=None, step=None, project_root=None):
    """Sauvegarde le hip courant comme prochaine version WIP de <entite>/<step>. Sans
    arguments : contexte deduit du chemin du hip courant (parse_wip_context). Retourne
    {'path', 'version'}."""
    import hou
    if entity_name is None or step is None:
        ctx = parse_wip_context(hou.hipFile.path())
        if ctx is None:
            raise ValueError(
                "Hip courant hors pipeline (attendu .../<entite>/<step>/wip/) - "
                "preciser entity_name et step."
            )
        project_root, entity_name, step = ctx
    if project_root is None:
        project_root = active_project()
    if project_root is None:
        raise ValueError(
            "Aucun projet actif (~/.ylos/active_project) et project_root non fourni."
        )
    path, version = next_wip_path(project_root, entity_name, step)
    path.parent.mkdir(parents=True, exist_ok=True)
    hou.hipFile.save(str(path))
    return {"path": str(path), "version": version}


def reference_asset(entity_name, project_root=None):
    """Cree un LOP 'reference' dans /stage pointant sur l'asset_root de l'entite (compose
    sous /<entite>, defaultPrim aligne - cf. docs/usd-convention.md). Le chemin est ecrit
    en $PROJ_ROOT quand c'est possible (env_relative). Retourne le noeud cree."""
    import hou
    if project_root is None:
        project_root = active_project()
    if project_root is None:
        raise ValueError("Aucun projet actif (~/.ylos/active_project).")
    path = asset_root_path(project_root, entity_name)
    stage = hou.node("/stage")
    node = stage.createNode("reference", entity_name)
    node.parm("primpath").set(f"/{entity_name}")
    node.parm("filepath1").set(env_relative(path))
    node.moveToGoodPosition()
    return node


# --------------------------------------------------------------------------------------
# Outils shelf (dialogues hou.ui, appeles par ylos_pipeline.shelf)
# --------------------------------------------------------------------------------------

def _pick_from_list(title, items, message):
    """Index choisi dans une liste (hou.ui.selectFromList exclusif), ou None si annule."""
    import hou
    if not items:
        return None
    sel = hou.ui.selectFromList(items, title=title, message=message,
                                exclusive=True, clear_on_cancel=True)
    return sel[0] if sel else None


def _pick_entity(project_root, title, entities=None):
    """Entite choisie parmi celles du projet (dict de list_entities), ou None si annule."""
    import hou
    entities = entities if entities is not None else list_entities(project_root)
    if not entities:
        hou.ui.displayMessage("Aucune entite dans ce projet - creer un asset d'abord.",
                              severity=hou.severityType.Warning)
        return None
    labels = [f"{e['name']}  ({e['family']}, {e['type']})" for e in entities]
    idx = _pick_from_list(title, labels, "Entite :")
    return entities[idx] if idx is not None else None


def tool_save_wip():
    """Shelf 'Save WIP' : version++ dans le contexte du hip courant, ou choix
    entite/step si le hip est hors pipeline."""
    import hou
    try:
        if parse_wip_context(hou.hipFile.path()) is not None:
            info = save_wip()
        else:
            project_root = active_project()
            if project_root is None:
                hou.ui.displayMessage(
                    "Aucun projet actif - definir le projet via l'UI web (ylos_ui.py).",
                    severity=hou.severityType.Warning)
                return
            entity = _pick_entity(project_root, "Save WIP")
            if entity is None:
                return
            steps = entity["steps"]
            idx = _pick_from_list("Save WIP", steps, "Step :")
            if idx is None:
                return
            info = save_wip(entity["name"], steps[idx], project_root)
        hou.ui.displayMessage(
            f"WIP v{info['version']:03d} sauvegarde :\n{info['path']}")
    except (ValueError, FileNotFoundError, OSError) as exc:
        hou.ui.displayMessage(str(exc), severity=hou.severityType.Error)


def tool_new_asset():
    """Shelf 'New Asset' : cree une entite dans le projet actif via create_asset()
    (validation TYPE_Nom_Variant a la creation - meme message d'erreur, avec suggestion,
    que l'UI web et Blender, car meme fonction)."""
    import hou
    project_root = active_project()
    if project_root is None:
        hou.ui.displayMessage(
            "Aucun projet actif - definir le projet via l'UI web (ylos_ui.py).",
            severity=hou.severityType.Warning)
        return
    families = list(cp.ENTITY_DIR)  # asset / set / shot
    idx = _pick_from_list("New Asset", families, "Famille :")
    if idx is None:
        return
    family = families[idx]
    types = cp._TYPES_BY_ENTITY[family]
    idx = _pick_from_list("New Asset", types, "Type :")
    if idx is None:
        return
    sub_type = types[idx]
    ok, name = hou.ui.readInput(
        f"Nom de l'entite (convention {sub_type}_Nom_Variant) :",
        buttons=("Creer", "Annuler"), close_choice=1,
        initial_contents=f"{sub_type}_Nom_Default")
    if ok != 0 or not name.strip():
        return
    try:
        info = cp.create_asset(project_root, name.strip(),
                               entity_type=family, asset_type=sub_type)
        hou.ui.displayMessage(f"{family} '{info['name']}' cree :\n{info['path']}")
    except (ValueError, FileExistsError, OSError) as exc:
        hou.ui.displayMessage(str(exc), severity=hou.severityType.Error)


def tool_load_asset():
    """Shelf 'Load Asset' : reference l'asset_root d'une entite dans /stage."""
    import hou
    project_root = active_project()
    if project_root is None:
        hou.ui.displayMessage(
            "Aucun projet actif - definir le projet via l'UI web (ylos_ui.py).",
            severity=hou.severityType.Warning)
        return
    # Seules les entites a asset_root sont proposees (les shots n'en ont pas).
    entities = [e for e in list_entities(project_root)
                if (Path(project_root) / e["family"] / e["name"] / cp.ASSET_ROOT_NAME).is_file()]
    entity = _pick_entity(project_root, "Load Asset", entities)
    if entity is None:
        return
    try:
        node = reference_asset(entity["name"], project_root)
        hou.ui.displayMessage(f"Reference creee : {node.path()}")
    except (ValueError, FileNotFoundError, OSError) as exc:
        hou.ui.displayMessage(str(exc), severity=hou.severityType.Error)
