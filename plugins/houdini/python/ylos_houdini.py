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
import shutil
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

# Rendus de shot : tier cache regenerable, sous $PROJ_CACHE/<projet>/render/ (miroir de
# CACHE_TREE). Un rendu est jetable et son suivi releve de la gestion de prod (principe 4,
# CLAUDE.md), pas du pipeline technique : versioning par scan disque (v<NNN>), aucun
# manifeste. Seul deliver_render() copie explicitement un take valide vers delivery/.
RENDER_SUBDIR = "render"
RENDER_VERSION_RE = re.compile(r"^v(\d{3})$")


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


def latest_step_publish(project_root, entity_name, step):
    """Chemin (Path) de l'artefact du dernier publish 'complete' du step <step> de
    l'entite, ou None si aucun. Miroir de latest_lop_publish sur manifest.step_publishes
    (contrat ecrit par finalize_publish_version en kind=<step>, cle 'artifact') - alimente
    la composition shot_root et le sublayer manuel d'un step precis."""
    entity_dir, manifest_path = cp._find_asset_entity(project_root, entity_name)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = [e for e in manifest.get(cp.STEP_PUBLISHES_KEY, {}).get(step, [])
               if e.get("status") == "complete" and e.get("artifact")]
    if not entries:
        return None
    best = max(entries, key=lambda e: e["version"])
    return entity_dir / best["artifact"]


def shot_root_path(project_root, shot_name):
    """Chemin (Path) du shot_root.usda d'un shot. FileNotFoundError explicite si absent :
    tant qu'aucun step n'est publie, refresh_entity_root() ne l'a pas encore compose."""
    entity_dir, _ = cp._find_asset_entity(project_root, shot_name)
    path = entity_dir / cp.SHOT_ROOT_NAME
    if not path.is_file():
        raise FileNotFoundError(
            f"Pas de {cp.SHOT_ROOT_NAME} pour '{shot_name}' ({path}) - publier au moins "
            f"un step du shot pour composer le shot_root."
        )
    return path


def cache_dir_expression(project_root, entity_name, step, env_name=cp.ENV_CACHE):
    """Expression LITTERALE (variable non resolue) du dossier de cache scratch d'un step :
    '$PROJ_CACHE/<projet>/houdini/<entite>/<step>/'. Posee telle quelle sur le 'basedir' d'un
    filecache SOP - miroir cote cache de env_relative() : le chemin reste relocalisable (le
    NVMe interne peut changer, le projet se deplace) plutot que resolu en dur. Le versioning
    v1/v2 des caches jetables reste celui natif du filecache (aucun manifeste). La resolution
    reelle du chemin vit dans create_project.entity_cache_dir (logique unique). Pure (sans hou)."""
    return f"${env_name}/{Path(project_root).name}/houdini/{entity_name}/{step}/"


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


def render_dir(project_root, shot_name, step):
    """Dossier de rendu (resolu sur disque) d'un step de shot :
    $PROJ_CACHE/<projet>/render/<shot>/<step>/ (tier regenerable, cf. CLAUDE.md). Resolution
    de $PROJ_CACHE via create_project.resolve_cache (logique unique, meme source que
    entity_cache_dir) - 'project_root' est la racine SOURCE, seul son basename sert a nommer
    le sous-arbre cache. Ne cree rien (lecture/scan) ; les versions sont creees par le rendu."""
    return (cp.resolve_cache() / Path(project_root).name / RENDER_SUBDIR
            / shot_name / step)


def list_render_versions(project_root, shot_name, step):
    """[int, ...] tries des versions de rendu (dossiers v<NNN>) presentes sur disque pour
    <shot>/<step>. Vide si aucun rendu (dossier absent). Scan pur, aucun manifeste."""
    rdir = render_dir(project_root, shot_name, step)
    if not rdir.is_dir():
        return []
    out = [int(m.group(1)) for d in rdir.iterdir()
           if d.is_dir() and (m := RENDER_VERSION_RE.match(d.name))]
    return sorted(out)


def next_render_version(project_root, shot_name, step):
    """Prochaine version de rendu (max des v<NNN> sur disque + 1) pour <shot>/<step>, ou 1
    si aucun rendu. Pas de manifeste : un rendu est regenerable (tier cache) et son suivi
    releve de la gestion de prod, hors pipeline technique (principe 4, CLAUDE.md). Pure."""
    versions = list_render_versions(project_root, shot_name, step)
    return (max(versions) + 1) if versions else 1


def render_output_expression(project_root, shot_name, step, version, env_name=cp.ENV_CACHE):
    """Expression LITTERALE (variable non resolue) du fichier de sortie EXR d'un rendu :
    '$PROJ_CACHE/<projet>/render/<shot>/<step>/v<NNN>/<shot>_<step>_v<NNN>.$F4.exr'. Posee
    telle quelle sur 'outputimage' du usdrender_rop - miroir de cache_dir_expression : le
    chemin reste relocalisable (tier jetable, $PROJ_CACHE peut changer) plutot que resolu en
    dur. '$F4' = numero de frame Houdini zero-padde 4 chiffres (une image par frame). Pure."""
    stem = f"{shot_name}_{step}_v{version:03d}"
    return (f"${env_name}/{Path(project_root).name}/{RENDER_SUBDIR}/{shot_name}/{step}"
            f"/v{version:03d}/{stem}.$F4.exr")


def deliver_render(project_root, shot_name, step, version):
    """Copie explicite d'un take de rendu valide (v<NNN> du tier cache) vers
    delivery/render/<shot>/<step>/v<NNN>/ (permanent). Le <step> est dans le chemin : deux
    steps livres a la meme version ne doivent PAS fusionner (copytree dirs_exist_ok=True les
    ecraserait silencieusement). SEUL chemin qui ecrit dans delivery/ : les rendus n'y
    arrivent que par validation humaine, jamais automatiquement (cf. plan). Refuse si la
    source est absente ou vide (rien a livrer). Pas de manifeste (gestion de prod + source
    regenerable). Retourne le Path du dossier livre. Testable (shutil, sans hou)."""
    project_root = Path(project_root)
    src = render_dir(project_root, shot_name, step) / f"v{version:03d}"
    if not src.is_dir() or not any(src.iterdir()):
        raise FileNotFoundError(
            f"Rien a livrer : {src} absent ou vide (rendre le step avant de livrer)."
        )
    dst = project_root / "delivery" / RENDER_SUBDIR / shot_name / step / f"v{version:03d}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    return dst


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


def _create_sublayer(node_name, path):
    """Cree un LOP 'sublayer' dans /stage sur 'path' (ecrit en $PROJ_ROOT via env_relative).
    Helper commun a sublayer_shot / sublayer_step_publish - un sublayer empile le layer sur
    le stage (contrairement a 'reference' qui le greffe sous un prim). Retourne le noeud."""
    import hou
    stage = hou.node("/stage")
    node = stage.createNode("sublayer", node_name)
    # 'sublayer' porte une liste de fichiers (multiparm 'num_files') - un seul ici.
    num = node.parm("num_files")
    if num is not None:
        num.set(1)
    node.parm("filepath1").set(env_relative(path))
    node.moveToGoodPosition()
    return node


def sublayer_shot(shot_name, project_root=None):
    """Cree un LOP 'sublayer' dans /stage sur le shot_root.usda du shot. sublayer et NON
    reference : le shot EST le stage (root prim /ROOT, cf. docs/usd-convention.md), on ne le
    greffe pas sous un prim. Retourne le noeud."""
    if project_root is None:
        project_root = active_project()
    if project_root is None:
        raise ValueError("Aucun projet actif (~/.ylos/active_project).")
    path = shot_root_path(project_root, shot_name)
    return _create_sublayer(shot_name, path)


def sublayer_step_publish(entity_name, step, project_root=None):
    """Cree un LOP 'sublayer' dans /stage sur le latest publish 'complete' du step de
    l'entite - pour composer manuellement un step precis (ex. lighting qui ne veut que
    l'anim) sans passer par tout le shot_root. Retourne le noeud."""
    if project_root is None:
        project_root = active_project()
    if project_root is None:
        raise ValueError("Aucun projet actif (~/.ylos/active_project).")
    path = latest_step_publish(project_root, entity_name, step)
    if path is None:
        raise FileNotFoundError(
            f"Aucun publish 'complete' pour le step '{step}' de '{entity_name}'."
        )
    return _create_sublayer(f"{entity_name}_{step}", path)


def _first_shot_camera(node):
    """Prim path de la premiere camera sous /ROOT/cameras/ dans le stage d'entree de 'node',
    ou None. Convention shot (docs/usd-convention.md) : les cameras publiees vivent sous
    /ROOT/cameras/ (distinctes de /cameras/ylos_thumb_cam du thumbnail HDA). Requiert hou."""
    try:
        inputs = node.inputs()
        stage = inputs[0].stage() if inputs and inputs[0] is not None else node.stage()
    except (AttributeError, IndexError):
        return None
    if stage is None:
        return None
    cams = stage.GetPrimAtPath("/ROOT/cameras")
    if not cams or not cams.IsValid():
        return None
    for child in cams.GetChildren():
        if child.GetTypeName() == "Camera":
            return child.GetPath().pathString
    return None


def render_shot(shot_name, step, camera=None, project_root=None):
    """Cree et configure (ne lance PAS) un usdrender_rop dans /stage pour rendre <shot>/<step>
    vers le tier cache ($PROJ_CACHE/.../render/<shot>/<step>/v<NNN>/, version =
    next_render_version). Entree = display node courant du /stage (le stage compose). trange
    sur la frame_range du manifeste (schema 2.1), fallback range du hip avec warning si
    absente. 'camera' = prim path (None -> premiere sous /ROOT/cameras/). 'outputimage' en
    expression $PROJ_CACHE litterale + $F4 (relocalisable). soho_foreground=1 : sinon
    node.render() en GUI rend la main a la soumission de husk, pas a la fin du rendu (gotcha
    CLAUDE.md). Retourne le noeud."""
    import hou
    if project_root is None:
        project_root = active_project()
    if project_root is None:
        raise ValueError("Aucun projet actif (~/.ylos/active_project).")
    project_root = Path(project_root)
    _, manifest_path = cp._find_asset_entity(project_root, shot_name)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = next_render_version(project_root, shot_name, step)

    stage = hou.node("/stage")
    node = stage.createNode("usdrender_rop", f"render_{shot_name}_{step}")
    display = stage.displayNode()
    if display is not None and display is not node:
        node.setInput(0, display)

    fr = manifest.get("frame_range")
    if fr:
        f1, f2 = int(fr["start"]), int(fr["end"])
    else:
        rng = hou.playbar.frameRange()
        f1, f2 = int(rng[0]), int(rng[1])
        sys.stderr.write(
            f"[warn] {shot_name} sans frame_range au manifeste - fallback range du hip "
            f"({f1}-{f2}). Poser create_project.set_frame_range() pour figer la plage.\n"
        )
    for name, val in (("trange", 1), ("f1", f1), ("f2", f2), ("f3", 1),
                      ("soho_foreground", 1)):
        parm = node.parm(name)
        if parm is not None:
            parm.set(val)

    if camera is None:
        camera = _first_shot_camera(node)
    if camera:
        # Sur usdrender_rop le parm camera s'appelle 'override_camera' (verifie par
        # enumeration hython - node.parm("camera") est None ; meme parm que le build du
        # HDA sur ce type de node). Warning explicite si introuvable (jamais silencieux :
        # une camera demandee mais non posee doit se voir, pas disparaitre).
        parm = node.parm("override_camera")
        if parm is not None:
            parm.set(camera)
        else:
            sys.stderr.write(
                f"[warn] usdrender_rop sans parametre 'override_camera' - camera "
                f"{camera!r} non posee (verifier la version de Houdini).\n"
            )

    out = node.parm("outputimage")
    if out is not None:
        out.set(render_output_expression(project_root, shot_name, step, version))
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


def tool_load_shot():
    """Shelf 'Load Shot' : sublayer le shot_root.usda d'un shot dans /stage (le shot EST le
    stage, root prim /ROOT) - compose tous les steps publies du shot."""
    import hou
    project_root = active_project()
    if project_root is None:
        hou.ui.displayMessage(
            "Aucun projet actif - definir le projet via l'UI web (ylos_ui.py).",
            severity=hou.severityType.Warning)
        return
    shots = [e for e in list_entities(project_root)
             if e["family"] == cp.ENTITY_DIR["shot"]]
    entity = _pick_entity(project_root, "Load Shot", shots)
    if entity is None:
        return
    try:
        node = sublayer_shot(entity["name"], project_root)
        hou.ui.displayMessage(f"Sublayer cree : {node.path()}")
    except (ValueError, FileNotFoundError, OSError) as exc:
        hou.ui.displayMessage(str(exc), severity=hou.severityType.Error)


def tool_setup_filecache():
    """Shelf 'Setup File Cache' : pose 'basedir' du/des filecache SOP selectionne(s) a
    l'expression $PROJ_CACHE/<projet>/houdini/<entite>/<step>/ (relocalisable, cf.
    cache_dir_expression). Contexte deduit du hip courant (parse_wip_context) - un cache est
    toujours ecrit dans le contexte de l'entite/step ouvert. Le versioning v1/v2 reste celui
    natif du filecache (donnee jetable, aucun manifeste)."""
    import hou
    ctx = parse_wip_context(hou.hipFile.path())
    if ctx is None:
        hou.ui.displayMessage(
            "Hip courant hors pipeline (.../<entite>/<step>/wip/) - impossible de deduire "
            "l'entite/step du cache. Ouvrir un WIP d'abord.",
            severity=hou.severityType.Warning)
        return
    project_root, entity_name, step = ctx
    selected = hou.selectedNodes()
    if not selected:
        hou.ui.displayMessage(
            "Selectionner le noeud 'filecache' SOP a configurer.",
            severity=hou.severityType.Warning)
        return
    expr = cache_dir_expression(project_root, entity_name, step)
    done = []
    for node in selected:
        parm = node.parm("basedir")
        if parm is not None:
            parm.set(expr)
            done.append(node)
    if not done:
        hou.ui.displayMessage(
            "Aucun noeud selectionne n'a de parametre 'basedir' (filecache SOP attendu).",
            severity=hou.severityType.Warning)
        return
    hou.ui.displayMessage(f"basedir pose sur {len(done)} noeud(s) :\n{expr}")


def tool_load_step_publish():
    """Shelf 'Load Step Publish' : sublayer le latest publish d'un step (entite + step au
    choix) - composer manuellement quand on ne veut pas tout le shot_root."""
    import hou
    project_root = active_project()
    if project_root is None:
        hou.ui.displayMessage(
            "Aucun projet actif - definir le projet via l'UI web (ylos_ui.py).",
            severity=hou.severityType.Warning)
        return
    entity = _pick_entity(project_root, "Load Step Publish")
    if entity is None:
        return
    steps = entity["steps"]
    idx = _pick_from_list("Load Step Publish", steps, "Step :")
    if idx is None:
        return
    try:
        node = sublayer_step_publish(entity["name"], steps[idx], project_root)
        hou.ui.displayMessage(f"Sublayer cree : {node.path()}")
    except (ValueError, FileNotFoundError, OSError) as exc:
        hou.ui.displayMessage(str(exc), severity=hou.severityType.Error)


def tool_render_shot():
    """Shelf 'Render Shot' : configure un usdrender_rop pour rendre un step de shot vers le
    tier cache (choix shot + step), sur la frame_range du manifeste, camera auto
    (/ROOT/cameras/), output relocalisable. Propose de lancer le rendu (soho_foreground : il
    bloque jusqu'a la fin)."""
    import hou
    project_root = active_project()
    if project_root is None:
        hou.ui.displayMessage(
            "Aucun projet actif - definir le projet via l'UI web (ylos_ui.py).",
            severity=hou.severityType.Warning)
        return
    shots = [e for e in list_entities(project_root)
             if e["family"] == cp.ENTITY_DIR["shot"]]
    entity = _pick_entity(project_root, "Render Shot", shots)
    if entity is None:
        return
    steps = entity["steps"]
    idx = _pick_from_list("Render Shot", steps, "Step :")
    if idx is None:
        return
    try:
        node = render_shot(entity["name"], steps[idx], project_root=project_root)
    except (ValueError, FileNotFoundError, OSError) as exc:
        hou.ui.displayMessage(str(exc), severity=hou.severityType.Error)
        return
    launch = hou.ui.displayConfirmation(
        f"usdrender_rop configure : {node.path()}\nLancer le rendu maintenant ?")
    if launch:
        try:
            node.render()
            hou.ui.displayMessage("Rendu termine (voir $PROJ_CACHE/.../render/).")
        except hou.OperationFailed as exc:
            hou.ui.displayMessage(str(exc), severity=hou.severityType.Error)


def tool_deliver_render():
    """Shelf 'Deliver Render' : copie un take de rendu valide du cache vers delivery/ (choix
    shot + step + version). SEUL chemin qui ecrit dans delivery/ (validation humaine, cf.
    deliver_render)."""
    import hou
    project_root = active_project()
    if project_root is None:
        hou.ui.displayMessage(
            "Aucun projet actif - definir le projet via l'UI web (ylos_ui.py).",
            severity=hou.severityType.Warning)
        return
    shots = [e for e in list_entities(project_root)
             if e["family"] == cp.ENTITY_DIR["shot"]]
    entity = _pick_entity(project_root, "Deliver Render", shots)
    if entity is None:
        return
    steps = entity["steps"]
    idx = _pick_from_list("Deliver Render", steps, "Step :")
    if idx is None:
        return
    step = steps[idx]
    versions = list_render_versions(project_root, entity["name"], step)
    if not versions:
        hou.ui.displayMessage(
            f"Aucun rendu pour {entity['name']}/{step} - rendre le step avant de livrer.",
            severity=hou.severityType.Warning)
        return
    labels = [f"v{v:03d}" for v in versions]
    vidx = _pick_from_list("Deliver Render", labels, "Version a livrer :")
    if vidx is None:
        return
    try:
        dst = deliver_render(project_root, entity["name"], step, versions[vidx])
        hou.ui.displayMessage(f"Rendu livre :\n{dst}")
    except (ValueError, FileNotFoundError, OSError) as exc:
        hou.ui.displayMessage(str(exc), severity=hou.severityType.Error)
