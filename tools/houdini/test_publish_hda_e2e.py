#!/usr/bin/env hython
"""test_publish_hda_e2e.py - reproduit un noeud ylos::publish::0.2 EXACTEMENT comme le TAB
menu de Houdini le ferait, puis appelle le callback publish tel quel.

MANUEL / HYTHON UNIQUEMENT - hors CI (necessite Houdini + le HDA installe). La CI
(`python3 -m unittest`, sans Houdini) ne charge jamais ce fichier (`import hou` top-level).

Difference deliberee avec un test "pratique" : on ne pre-remplit AUCUN parametre a la main,
sauf `asset_name` (le seul que l'utilisateur renseigne manuellement dans le workflow reel).
Tout le reste (project_root via default_expression, asset_type via default_value, les parms
promus caches) doit venir des VRAIES valeurs par defaut de la definition installee. C'est le
seul moyen de reproduire les deux bugs (663f5c8, d51682e) qui n'existaient QUE sur un noeud
fraichement cree - un test qui pose les parms a la main les masque, comme cela s'est produit.

A lancer depuis un repertoire NEUTRE (jamais la racine du repo) : un bug d'import qui ne se
declare que quand le cwd n'est pas la racine (cf. 663f5c8, masque par sys.path[0]='' quand le
shell etait deja dans REPO) doit pouvoir se reproduire ici.

Usage :
    cd /un/repertoire/neutre
    hython /chemin/absolu/vers/tools/houdini/test_publish_hda_e2e.py
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import hou

# Chemin absolu et resolu (jamais dirname(__file__) nu, meme raison que le fix symlink
# documente dans build_publish_hda.py) : ce script doit fonctionner peu importe le cwd.
_HERE = os.path.realpath(__file__)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))

TYPE_NAME = "ylos::publish::0.2"
ASSET_TYPE = "CHARACTER"          # doit matcher ASSET_TYPES[0] (default_value reel du parm)
ASSET_NAME = "CHARACTER_Test_Default"

# Mode step (0.2) : fixture shot + publish d'un step qui produit un layer USD.
SHOT_NAME = "ANIMATION_Test_Default"   # prefixe = un SHOT_TYPES ; distinct du step publie
SHOT_TYPE = "ANIMATION"
SHOT_STEP = "lighting"                 # un DEFAULT_SHOT_STEPS, produit bien un layer (pas comp/2D)


def fail(msg):
    print("[FAIL] {}".format(msg))
    sys.exit(1)


def test_finalize_rejects_missing_thumb():
    """finalize_publish_version() doit refuser de committer si le thumbnail manque du staging -
    contrat de completude (cf. bug staging_dir orphelin : thumb.png ecrit ~4s apres l'os.replace
    en session GUI Houdini, husk/usdrender_rop asynchrone). Pur create_project.py, pas besoin de
    hou/HDA - reproduit juste l'etat d'un staging_dir incomplet a la main."""
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project as cp

    tmp_base = tempfile.mkdtemp(prefix="ylos_finalize_contract_")
    try:
        info = cp.create(
            "FinalizeContractTest",
            root=os.path.join(tmp_base, "projects"),
            cache=os.path.join(tmp_base, "cache"),
        )
        project_source = info["source"]
        asset_name = "CHARACTER_ContractTest_Default"
        cp.create_asset(project_source, asset_name, entity_type="asset", asset_type="CHARACTER")

        staging_dir, final_dir = cp.allocate_publish_version(
            project_source, asset_name, "CHARACTER", comment="finalize contract test"
        )
        version = cp.publish_version_from_dir(final_dir)

        # Ecrit SEULEMENT le layer, jamais le thumb - simule exactement le staging_dir capture
        # par le bug (render du thumb pas encore termine au moment du finalize).
        layer_stem = "{}_lop_v{:03d}".format(asset_name, version)
        (staging_dir / (layer_stem + ".usd")).write_text("#usda 1.0\n", encoding="utf-8")

        expected_artifacts = [layer_stem, cp.LOP_THUMB_NAME]

        raised = False
        try:
            cp.finalize_publish_version(
                project_source, asset_name, staging_dir, final_dir, version,
                expected_artifacts, comment="finalize contract test"
            )
        except ValueError:
            raised = True

        if not raised:
            fail("finalize_publish_version() n'a PAS leve alors que thumb.png manquait du staging")
        if not staging_dir.is_dir():
            fail("staging_dir a disparu alors que le finalize aurait du echouer avant tout replace")
        if final_dir.exists():
            fail("final_dir existe alors que le finalize aurait du echouer avant tout replace")

        entity_manifest_path = (
            Path(project_source) / "assets" / asset_name / cp.ASSET_MANIFEST_NAME
        )
        entity_manifest = json.loads(entity_manifest_path.read_text(encoding="utf-8"))
        entry = next(
            e for e in entity_manifest[cp.LOP_PUBLISHES_KEY] if e["version"] == version
        )
        if entry["status"] != "pending":
            fail(
                "l'entree de version est passee a {!r} alors que le finalize a echoue - "
                "attendu 'pending' (jamais commit)".format(entry["status"])
            )

        print("[ok] finalize_publish_version() rejette bien un staging_dir sans thumb")
        print("[PASS] test_finalize_rejects_missing_thumb")
    finally:
        shutil.rmtree(tmp_base, ignore_errors=True)


def main():
    # Import de create_project pour le SETUP du test (scaffolder un projet + un asset) -
    # distinct de l'import que fait le module Python embarque du HDA lui-meme (celui-la est
    # sous test, pas contourne : on ne touche pas sys.path pour lui, on verifie juste que son
    # propre _repo_root() s'en sort avec le HOME reel deja charge par le package Houdini).
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project as cp

    tmp_base = tempfile.mkdtemp(prefix="ylos_publish_e2e_")
    fake_home = os.path.join(tmp_base, "fake_home")
    os.makedirs(os.path.join(fake_home, ".ylos"), exist_ok=True)

    node = None
    real_home = os.environ.get("HOME")
    try:
        # 1. Projet + asset reels sur disque (fixture de test, pas un parm pre-rempli : c'est
        #    l'equivalent d'un projet deja scaffolde avant que l'utilisateur ouvre Houdini).
        info = cp.create(
            "E2ETest",
            root=os.path.join(tmp_base, "projects"),
            cache=os.path.join(tmp_base, "cache"),
        )
        project_source = info["source"]
        cp.create_asset(project_source, ASSET_NAME, entity_type="asset", asset_type=ASSET_TYPE)

        # 2. Fichier ~/.ylos/active_project - c'est CE fichier que le default_expression du
        #    parm project_root lit. On ne pose jamais project_root a la main sur le noeud.
        active_project_file = os.path.join(fake_home, ".ylos", "active_project")
        with open(active_project_file, "w", encoding="utf-8") as f:
            f.write(project_source)

        # 3. Noeud EXACTEMENT comme le TAB menu : aucun kwarg de parm, type resolu depuis le
        #    .hdanc installe via le package Houdini (HOUDINI_OTLSCAN_PATH), pas depuis un
        #    build local. Si le type est introuvable, le package n'est pas charge - on le
        #    signale distinctement d'un echec du callback.
        stage = hou.node("/stage")
        try:
            node = stage.createNode(TYPE_NAME)
        except hou.OperationFailed as exc:
            fail(
                "type '{}' introuvable (package Houdini non charge ? "
                "cf. plugins/houdini/ylos.json) : {}".format(TYPE_NAME, exc)
            )

        # 4. HOME bascule sur le fake home UNIQUEMENT maintenant : Houdini a deja demarre et
        #    scanne HOUDINI_OTLSCAN_PATH avec le vrai HOME (le package resout $YLOS_REPO =
        #    $HOME/Desktop/Claude/YlosPipeline a ce moment-la, avant ce script). Le seul code
        #    qui doit voir le HOME bascule est le default_expression de project_root (lu a
        #    l'eval du parm) et l'import du module embarque du HDA (qui, lui, ne depend pas
        #    de HOME - il derive son repo root de son propre chemin de definition installee).
        os.environ["HOME"] = fake_home

        # 5. Seul parm pose a la main : asset_name (le seul que l'utilisateur remplit).
        node.parm("asset_name").set(ASSET_NAME)

        # 6. Sanity-check des VRAIS defauts de la definition, AVANT tout clic - c'est
        #    precisement ce que le test precedent ne verifiait pas.
        asset_type_default = node.evalParm("asset_type")
        if not asset_type_default:
            fail(
                "asset_type est vide sur un noeud fraichement cree (regression du fix "
                "d51682e - default_value manquant sur le ParmTemplate menu)."
            )
        if asset_type_default != ASSET_TYPE:
            fail(
                "asset_type par defaut = {!r}, attendu {!r} (ASSET_TYPES[0] a-t-il change "
                "sans mettre a jour ce test ?)".format(asset_type_default, ASSET_TYPE)
            )

        project_root_default = node.evalParm("project_root")
        if project_root_default != project_source:
            fail(
                "project_root par defaut = {!r}, attendu {!r} (default_expression cassee ou "
                "~/.ylos/active_project non lu).".format(project_root_default, project_source)
            )

        status_before = node.evalParm("status")
        if status_before:
            fail("status non vide avant tout publish : {!r}".format(status_before))

        # 7. Declenche le callback EXACTEMENT comme un clic utilisateur (pressButton execute
        #    le script_callback du parm, pas un appel direct a hou.phm().publish()).
        try:
            node.parm("publish").pressButton()
        except hou.OperationFailed as exc:
            # Le callback re-leve apres avoir ecrit "ERREUR: ..." dans status - le message
            # status (verifie juste apres) est la source de verite, celle-ci n'est qu'un filet.
            print("[warn] pressButton a leve : {}".format(exc))

        status_after = node.evalParm("status")
        if not status_after.startswith("OK"):
            fail("callback publish en erreur - status = {!r}".format(status_after))

        # 8. Verification sur disque, pas seulement le texte du status : le vrai livrable.
        publish_dir = os.path.join(project_source, "assets", ASSET_NAME, "lop", "publish")
        if not os.path.isdir(publish_dir):
            fail("aucun repertoire de publish sur disque : {}".format(publish_dir))
        versions = sorted(os.listdir(publish_dir))
        if not versions:
            fail("repertoire de publish vide : {}".format(publish_dir))
        version_dir = os.path.join(publish_dir, versions[-1])
        produced = sorted(os.listdir(version_dir))
        layers = [n for n in produced if n != cp.LOP_THUMB_NAME]
        thumbs = [n for n in produced if n == cp.LOP_THUMB_NAME]
        if not layers:
            fail("aucun layer USD ecrit dans {} (produced={!r})".format(version_dir, produced))
        if not thumbs:
            fail("aucun thumbnail ecrit dans {} (produced={!r})".format(version_dir, produced))

        print("[ok] status         : {}".format(status_after))
        print("[ok] version publiee: {}".format(version_dir))
        print("[ok] fichiers       : {}".format(produced))
        print("[PASS]")

    finally:
        if real_home is not None:
            os.environ["HOME"] = real_home
        else:
            os.environ.pop("HOME", None)
        if node is not None:
            node.destroy()
        shutil.rmtree(tmp_base, ignore_errors=True)


def test_step_publish_shot():
    """Mode step (0.2) : sur une fixture shot, publier `publish_kind=lighting` doit deposer
    le layer dans shots/<shot>/lighting/publish/, ecrire step_publishes['lighting'] au
    manifeste (statut complete) et declencher la recomposition de shot_root.usda
    (refresh_entity_root, kind != 'lop'). Meme reproduction fidele du TAB menu que main() :
    seuls asset_name (le shot) et publish_kind sont poses a la main (les deux valeurs que
    l'utilisateur choisit dans le workflow step reel) ; asset_type garde son defaut et est
    ignore par le callback en mode step."""
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project as cp

    tmp_base = tempfile.mkdtemp(prefix="ylos_publish_step_e2e_")
    fake_home = os.path.join(tmp_base, "fake_home")
    os.makedirs(os.path.join(fake_home, ".ylos"), exist_ok=True)

    node = None
    real_home = os.environ.get("HOME")
    try:
        info = cp.create(
            "E2EStepTest",
            root=os.path.join(tmp_base, "projects"),
            cache=os.path.join(tmp_base, "cache"),
        )
        project_source = info["source"]
        cp.create_asset(project_source, SHOT_NAME, entity_type="shot", asset_type=SHOT_TYPE)

        active_project_file = os.path.join(fake_home, ".ylos", "active_project")
        with open(active_project_file, "w", encoding="utf-8") as f:
            f.write(project_source)

        stage = hou.node("/stage")
        try:
            node = stage.createNode(TYPE_NAME)
        except hou.OperationFailed as exc:
            fail(
                "type '{}' introuvable (package Houdini non charge ? "
                "cf. plugins/houdini/ylos.json) : {}".format(TYPE_NAME, exc)
            )

        os.environ["HOME"] = fake_home

        # Les deux seuls parms poses a la main dans le workflow step : le shot et le step.
        node.parm("asset_name").set(SHOT_NAME)
        node.parm("publish_kind").set(SHOT_STEP)

        # Le menu publish_kind doit exposer le step (lu du manifeste du shot par
        # kind_menu_items) - sinon la valeur posee ne matcherait aucun item.
        kind_items = node.parm("publish_kind").menuItems()
        if SHOT_STEP not in kind_items:
            fail(
                "publish_kind ne propose pas {!r} (menu genere = {!r} ; manifeste du shot "
                "non lu par kind_menu_items ?)".format(SHOT_STEP, kind_items)
            )
        if node.evalParm("publish_kind") != SHOT_STEP:
            fail("publish_kind = {!r}, attendu {!r}".format(
                node.evalParm("publish_kind"), SHOT_STEP))

        try:
            node.parm("publish").pressButton()
        except hou.OperationFailed as exc:
            print("[warn] pressButton a leve : {}".format(exc))

        status_after = node.evalParm("status")
        if not status_after.startswith("OK"):
            fail("callback publish (step) en erreur - status = {!r}".format(status_after))

        # 1. Le layer atterrit sous le sous-arbre du step, PAS sous lop/.
        publish_dir = os.path.join(
            project_source, "shots", SHOT_NAME, SHOT_STEP, "publish"
        )
        if not os.path.isdir(publish_dir):
            fail("aucun repertoire de publish step sur disque : {}".format(publish_dir))
        versions = sorted(os.listdir(publish_dir))
        if not versions:
            fail("repertoire de publish step vide : {}".format(publish_dir))
        version_dir = os.path.join(publish_dir, versions[-1])
        produced = sorted(os.listdir(version_dir))
        if not [n for n in produced if n != cp.LOP_THUMB_NAME]:
            fail("aucun layer USD ecrit dans {} (produced={!r})".format(version_dir, produced))
        if cp.LOP_THUMB_NAME not in produced:
            fail("aucun thumbnail dans {} (produced={!r})".format(version_dir, produced))

        # 2. Le manifeste enregistre l'entree sous step_publishes[step], statut complete.
        manifest_path = Path(project_source) / "shots" / SHOT_NAME / cp.ASSET_MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        step_entries = manifest.get(cp.STEP_PUBLISHES_KEY, {}).get(SHOT_STEP)
        if not step_entries:
            fail(
                "step_publishes[{!r}] absent/vide au manifeste (kind mal route ?) : {}".format(
                    SHOT_STEP, manifest.get(cp.STEP_PUBLISHES_KEY))
            )
        if step_entries[-1].get("status") != "complete":
            fail("derniere entree step_publishes[{!r}] non 'complete' : {!r}".format(
                SHOT_STEP, step_entries[-1]))
        # Le publish LOP ne doit PAS avoir ete alimente en mode step.
        if manifest.get(cp.LOP_PUBLISHES_KEY):
            fail("lop_publishes non vide apres un publish step : {!r}".format(
                manifest.get(cp.LOP_PUBLISHES_KEY)))

        # 3. shot_root.usda recompose par finalize (kind != 'lop' -> refresh_entity_root).
        shot_root = Path(project_source) / "shots" / SHOT_NAME / cp.SHOT_ROOT_NAME
        if not shot_root.is_file():
            fail("shot_root.usda non recompose apres le publish step : {}".format(shot_root))

        print("[ok] status         : {}".format(status_after))
        print("[ok] version publiee: {}".format(version_dir))
        print("[ok] step_publishes : {}".format(step_entries[-1]))
        print("[ok] shot_root.usda : {}".format(shot_root))
        print("[PASS] test_step_publish_shot")

    finally:
        if real_home is not None:
            os.environ["HOME"] = real_home
        else:
            os.environ.pop("HOME", None)
        if node is not None:
            node.destroy()
        shutil.rmtree(tmp_base, ignore_errors=True)


if __name__ == "__main__":
    # Contrat de completude d'abord : rapide, pas de rendu Houdini reel, feedback immediat.
    # Puis l'e2e complet avec le vrai HDA/rendu (mode lop, puis mode step). Fail-fast
    # (cf. fail()) : si un test echoue, les suivants ne se lancent pas.
    test_finalize_rejects_missing_thumb()
    main()
    test_step_publish_shot()
