#!/usr/bin/env hython
"""test_shot_workflow_e2e.py - scenario shot COMPLET de bout en bout (Increment 7 du plan
docs/plan-houdini-shots.md). Exerce la chaine entiere sur une fixture jetable :

    create shot (+ frame_range 2.1)
      -> save WIP animation (ylos_houdini.save_wip, .hip versionne)
      -> publish animation (HDA ylos::publish::0.2, mode step)          -> shot_root recompose
      -> publish lighting  (HDA ylos::publish::0.2, mode step)          -> shot_root recompose
      -> load shot en LOP sublayer (ylos_houdini.sublayer_shot)
      -> render Karma -> $PROJ_CACHE/.../render/<shot>/lighting/v001/   (tier cache)
      -> deliver_render -> delivery/render/<shot>/lighting/v001/        (copie explicite)

A CHAQUE etape on verifie le livrable REEL sur disque / dans le manifeste, pas seulement le
retour de la fonction. Le point cle du scenario : apres les deux publishes, shot_root.usda
doit lister le layer *lighting* AVANT *animation* (SHOT_DOWNSTREAM_ORDER : sur un shot le
lighting override l'animation - l'inverse d'un asset).

MANUEL / HYTHON UNIQUEMENT - hors CI (necessite Houdini, une licence, et le HDA installe via
le package plugins/houdini/ylos.json). La CI (`python3 -m unittest`, sans Houdini) ne charge
jamais ce fichier : `import hou` top-level. Les fonctions PURES de ylos_houdini.py (versioning
de rendu, resolution de chemins) sont deja couvertes sans hou par tests/test_ylos_houdini.py ;
ce script couvre la plomberie qui EXIGE Houdini (HDA, LOPs, rendu husk).

Distinct de test_publish_hda_e2e.py : celui-la reproduit fidelement un noeud HDA du TAB menu
(defauts non pre-remplis, bugs de default_expression). Celui-ci n'y revient pas - il pose
project_root explicitement sur le noeud (comme l'artiste tape un chemin) et se concentre sur
l'enchainement des outils de shot. Les deux sont complementaires, aucun ne duplique l'autre.

Usage :
    cd /un/repertoire/neutre
    hython /chemin/absolu/vers/tools/houdini/test_shot_workflow_e2e.py
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import hou

# Chemin absolu et resolu (jamais dirname(__file__) nu - meme raison symlink que le reste du
# repo) : ce script doit fonctionner peu importe le cwd. 3 dirname : ce fichier -> houdini
# -> tools -> REPO.
_HERE = os.path.realpath(__file__)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
BRIDGE_DIR = os.path.join(REPO_ROOT, "plugins", "houdini", "python")

TYPE_NAME = "ylos::publish::0.2"

# Shot fixture : le prefixe DOIT etre un SHOT_TYPES (validation de nommage a la creation).
# LAYOUT est un SHOT_TYPES valide et distinct des steps publies (animation / lighting) - on
# evite la confusion du type "ANIMATION" utilise ailleurs, qui est aussi un nom de step.
SHOT_NAME = "LAYOUT_Sq010_Default"
SHOT_TYPE = "LAYOUT"

# Deux steps publies, dans l'ordre chronologique du workflow. lighting est plus FORT que
# animation dans SHOT_DOWNSTREAM_ORDER -> doit finir en premier dans les subLayers.
STEP_ANIM = "animation"
STEP_LIGHT = "lighting"

# frame_range minuscule : le rendu Karma ne doit couvrir que 2 frames (le defaut 1001-1100 =
# 100 frames de husk, inutile pour valider la plomberie).
FRAME_START = 1001
FRAME_END = 1002


def fail(msg):
    print("[FAIL] {}".format(msg))
    sys.exit(1)


def _publish_step_via_hda(project_source, shot_name, step):
    """Cree un noeud HDA ylos::publish::0.2 dans /stage, le configure en mode step (asset_name
    = le shot, publish_kind = le step, project_root pose explicitement) et declenche le
    callback publish EXACTEMENT comme un clic (pressButton). Verifie que le menu publish_kind
    expose bien le step (lu du manifeste) et que le status final commence par 'OK'. Retourne
    le noeud (a detruire par l'appelant)."""
    stage = hou.node("/stage")
    try:
        node = stage.createNode(TYPE_NAME)
    except hou.OperationFailed as exc:
        fail("type '{}' introuvable (package Houdini non charge ? cf. "
             "plugins/houdini/ylos.json) : {}".format(TYPE_NAME, exc))

    # project_root pose a la main (l'artiste tape le chemin) - on ne teste pas ici la
    # default_expression ~/.ylos/active_project (couverte par test_publish_hda_e2e.py).
    node.parm("project_root").set(project_source)
    node.parm("asset_name").set(shot_name)

    # Le menu publish_kind doit exposer le step (kind_menu_items lit le manifeste du shot).
    node.parm("publish_kind").set(step)
    kind_items = node.parm("publish_kind").menuItems()
    if step not in kind_items:
        fail("publish_kind ne propose pas {!r} (menu = {!r} ; manifeste du shot non lu par "
             "kind_menu_items ?)".format(step, kind_items))
    if node.evalParm("publish_kind") != step:
        fail("publish_kind = {!r}, attendu {!r}".format(node.evalParm("publish_kind"), step))

    try:
        node.parm("publish").pressButton()
    except hou.OperationFailed as exc:
        print("[warn] pressButton ({}) a leve : {}".format(step, exc))

    status = node.evalParm("status")
    if not status.startswith("OK"):
        fail("callback publish ({}) en erreur - status = {!r}".format(step, status))
    return node


def _assert_step_published(cp, project_source, shot_name, step):
    """Verifie sur disque + manifeste qu'un step vient bien d'etre publie : layer USD + thumb
    dans <shot>/<step>/publish/v###/, entree step_publishes[step] statut 'complete', et
    lop_publishes toujours vide (mode step ne l'alimente jamais)."""
    publish_dir = Path(project_source) / "shots" / shot_name / step / "publish"
    if not publish_dir.is_dir():
        fail("aucun repertoire de publish pour le step {} : {}".format(step, publish_dir))
    versions = sorted(p.name for p in publish_dir.iterdir() if p.is_dir())
    if not versions:
        fail("repertoire de publish vide pour le step {} : {}".format(step, publish_dir))
    version_dir = publish_dir / versions[-1]
    produced = sorted(p.name for p in version_dir.iterdir())
    if not [n for n in produced if n != cp.LOP_THUMB_NAME]:
        fail("aucun layer USD dans {} (produced={!r})".format(version_dir, produced))
    if cp.LOP_THUMB_NAME not in produced:
        fail("aucun thumbnail dans {} (produced={!r})".format(version_dir, produced))

    manifest_path = Path(project_source) / "shots" / shot_name / cp.ASSET_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    step_entries = manifest.get(cp.STEP_PUBLISHES_KEY, {}).get(step)
    if not step_entries:
        fail("step_publishes[{!r}] absent/vide (kind mal route ?) : {}".format(
            step, manifest.get(cp.STEP_PUBLISHES_KEY)))
    if step_entries[-1].get("status") != "complete":
        fail("derniere entree step_publishes[{!r}] non 'complete' : {!r}".format(
            step, step_entries[-1]))
    if manifest.get(cp.LOP_PUBLISHES_KEY):
        fail("lop_publishes non vide apres un publish step {} : {!r}".format(
            step, manifest.get(cp.LOP_PUBLISHES_KEY)))
    print("[ok] step {:<10} publie : {}".format(step, produced))


def main():
    # Bridge Houdini importe par chemin (plugins/houdini/python n'est pas un package) ; il
    # ajoute lui-meme REPO_ROOT a sys.path et importe create_project (logique unique).
    if BRIDGE_DIR not in sys.path:
        sys.path.insert(0, BRIDGE_DIR)
    import ylos_houdini as yh
    import create_project as cp

    tmp_base = tempfile.mkdtemp(prefix="ylos_shot_workflow_e2e_")
    created = []  # noeuds a detruire en fin de scenario
    try:
        # --- 1. Projet + shot + frame_range (schema 2.1) -----------------------------------
        info = cp.create(
            "ShotWorkflowE2E",
            root=os.path.join(tmp_base, "projects"),
            cache=os.path.join(tmp_base, "cache"),
        )
        project_source = info["source"]
        cp.create_asset(project_source, SHOT_NAME, entity_type="shot", asset_type=SHOT_TYPE)
        fr = cp.set_frame_range(project_source, SHOT_NAME, FRAME_START, FRAME_END)
        manifest_path = Path(project_source) / "shots" / SHOT_NAME / cp.ASSET_MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != cp.SCHEMA_VERSION:
            fail("schema_version du shot = {!r}, attendu {!r}".format(
                manifest.get("schema_version"), cp.SCHEMA_VERSION))
        if manifest.get("frame_range") != {"start": FRAME_START, "end": FRAME_END,
                                           "fps": fr["fps"]}:
            fail("frame_range mal ecrit au manifeste : {!r}".format(manifest.get("frame_range")))
        print("[ok] shot cree, frame_range = {}".format(fr))

        # --- 2. Save WIP animation (.hip versionne) ----------------------------------------
        # Scene vierge : le WIP versionne est le livrable teste ici, pas son contenu (le
        # publish HDA plus bas ne relit pas ce hip - il serialise le stage courant, comme
        # test_step_publish_shot). save_wip valide le step contre le manifeste et pose la
        # bonne extension de licence.
        wip = yh.save_wip(SHOT_NAME, STEP_ANIM, project_root=project_source)
        if wip["version"] != 1:
            fail("premiere version WIP = {}, attendu 1".format(wip["version"]))
        if not Path(wip["path"]).is_file():
            fail("fichier WIP non ecrit sur disque : {}".format(wip["path"]))
        expected_wip_dir = Path(project_source) / "shots" / SHOT_NAME / STEP_ANIM / "wip"
        if Path(wip["path"]).parent != expected_wip_dir:
            fail("WIP hors du sous-arbre attendu {} : {}".format(expected_wip_dir, wip["path"]))
        print("[ok] WIP animation v{:03d} : {}".format(wip["version"], wip["path"]))

        # --- 3. Publish animation (HDA mode step) + shot_root recompose --------------------
        created.append(_publish_step_via_hda(project_source, SHOT_NAME, STEP_ANIM))
        _assert_step_published(cp, project_source, SHOT_NAME, STEP_ANIM)
        shot_root = Path(project_source) / "shots" / SHOT_NAME / cp.SHOT_ROOT_NAME
        if not shot_root.is_file():
            fail("shot_root.usda non recompose apres le publish animation : {}".format(shot_root))
        text = shot_root.read_text(encoding="utf-8")
        # frame_range 2.1 -> timecodes dans le header du stage.
        for tc in ("startTimeCode = {}".format(FRAME_START),
                   "endTimeCode = {}".format(FRAME_END)):
            if tc not in text:
                fail("timecode absent du shot_root apres frame_range : '{}' manquant".format(tc))
        if 'defaultPrim = "ROOT"' not in text:
            fail("shot_root sans defaultPrim = \"ROOT\" (root prim de shot attendu) :\n{}".format(text))
        print("[ok] shot_root recompose (animation seule) + timecodes")

        # --- 4. Publish lighting (HDA mode step) + shot_root recompose (ordre !) -----------
        created.append(_publish_step_via_hda(project_source, SHOT_NAME, STEP_LIGHT))
        _assert_step_published(cp, project_source, SHOT_NAME, STEP_LIGHT)
        text = shot_root.read_text(encoding="utf-8")
        idx_light = text.find("/{}/publish".format(STEP_LIGHT))
        idx_anim = text.find("/{}/publish".format(STEP_ANIM))
        if idx_light < 0 or idx_anim < 0:
            fail("un des deux steps absent des subLayers de shot_root :\n{}".format(text))
        # SHOT_DOWNSTREAM_ORDER : lighting (plus fort) doit apparaitre AVANT animation.
        if idx_light > idx_anim:
            fail("ordre des subLayers incorrect : lighting doit preceder animation "
                 "(SHOT_DOWNSTREAM_ORDER) - shot_root :\n{}".format(text))
        print("[ok] shot_root recompose : lighting AVANT animation (ordre shot correct)")

        # --- 5. Load shot en LOP sublayer --------------------------------------------------
        sub = yh.sublayer_shot(SHOT_NAME, project_root=project_source)
        created.append(sub)
        filepath = sub.evalParm("filepath1")
        # Le chemin doit etre ecrit en $PROJ_ROOT (relocalisable), pas en absolu resolu.
        if "$" + cp.ENV_ROOT not in filepath:
            fail("sublayer_shot n'a pas ecrit le chemin en ${} : {!r}".format(
                cp.ENV_ROOT, filepath))
        print("[ok] Load Shot : LOP sublayer -> {}".format(filepath))

        # --- 6. Camera de shot (convention /ROOT/cameras/) puis rendu Karma -> cache -------
        # Les layers publies par le HDA (sans input) ne portent pas de camera. On en injecte
        # une sous /ROOT/cameras/ (convention docs/usd-convention.md) pour que le rendu soit
        # viable et que render_shot l'auto-selectionne (premier prim Camera sous /ROOT/cameras/).
        cam = hou.node("/stage").createNode("camera", "cam_main")
        cam.setInput(0, sub)
        cam.parm("primpath").set("/ROOT/cameras/cam_main")
        cam.setDisplayFlag(True)
        created.append(cam)

        version = yh.next_render_version(project_source, SHOT_NAME, STEP_LIGHT)
        if version != 1:
            fail("premiere version de rendu = {}, attendu 1".format(version))
        rop = yh.render_shot(SHOT_NAME, STEP_LIGHT, project_root=project_source)
        created.append(rop)
        # La convention de sortie (expression $PROJ_CACHE litterale + $F4) est deterministe,
        # verifiable sans lancer husk.
        expected_out = yh.render_output_expression(project_source, SHOT_NAME, STEP_LIGHT, version)
        if rop.evalParm("outputimage") != expected_out:
            fail("outputimage = {!r}, attendu {!r}".format(
                rop.evalParm("outputimage"), expected_out))
        if rop.evalParm("f1") != FRAME_START or rop.evalParm("f2") != FRAME_END:
            fail("plage de rendu = {}-{}, attendu {}-{} (frame_range du manifeste)".format(
                rop.evalParm("f1"), rop.evalParm("f2"), FRAME_START, FRAME_END))
        print("[ok] render_shot configure : {} (frames {}-{})".format(
            expected_out, FRAME_START, FRAME_END))

        # Rendu reel (soho_foreground=1 -> bloque jusqu'a la fin, cf. gotcha CLAUDE.md).
        rop.render()
        render_v = yh.render_dir(project_source, SHOT_NAME, STEP_LIGHT) / "v{:03d}".format(version)
        exrs = sorted(p.name for p in render_v.glob("*.exr")) if render_v.is_dir() else []
        if not exrs:
            fail("aucun EXR rendu dans {} (husk a-t-il abouti ? Apprentice = watermark, "
                 "pas un blocage).".format(render_v))
        print("[ok] rendu Karma -> cache : {} ({} frame(s))".format(render_v, len(exrs)))

        # --- 7. Deliver : copie explicite cache -> delivery/ -------------------------------
        delivered = yh.deliver_render(project_source, SHOT_NAME, STEP_LIGHT, version)
        if not delivered.is_dir():
            fail("deliver_render n'a pas cree le dossier de livraison : {}".format(delivered))
        delivered_exrs = sorted(p.name for p in delivered.glob("*.exr"))
        if delivered_exrs != exrs:
            fail("delivery incomplete : cache={!r} vs delivery={!r}".format(exrs, delivered_exrs))
        expected_delivery = (Path(project_source) / "delivery" / "render" / SHOT_NAME
                             / STEP_LIGHT / "v{:03d}".format(version))
        if delivered != expected_delivery:
            fail("delivery hors convention : {} (attendu {})".format(delivered, expected_delivery))
        print("[ok] deliver_render -> {} ({} frame(s))".format(delivered, len(delivered_exrs)))

        print("[PASS] scenario shot complet (create -> WIP -> 2 publishes -> load -> render "
              "-> deliver)")

    finally:
        for node in created:
            try:
                node.destroy()
            except hou.ObjectWasDeleted:
                pass
        shutil.rmtree(tmp_base, ignore_errors=True)


if __name__ == "__main__":
    main()
