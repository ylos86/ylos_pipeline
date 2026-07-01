#!/usr/bin/env hython
"""test_publish_hda_e2e.py - reproduit un noeud ylos::publish::0.1 EXACTEMENT comme le TAB
menu de Houdini le ferait, puis appelle le callback publish tel quel.

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

import os
import shutil
import sys
import tempfile

import hou

# Chemin absolu et resolu (jamais dirname(__file__) nu, meme raison que le fix symlink
# documente dans build_publish_hda.py) : ce script doit fonctionner peu importe le cwd.
_HERE = os.path.realpath(__file__)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))

TYPE_NAME = "ylos::publish::0.1"
ASSET_TYPE = "CHARACTER"          # doit matcher ASSET_TYPES[0] (default_value reel du parm)
ASSET_NAME = "CHARACTER_Test_Default"


def fail(msg):
    print("[FAIL] {}".format(msg))
    sys.exit(1)


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


if __name__ == "__main__":
    main()
