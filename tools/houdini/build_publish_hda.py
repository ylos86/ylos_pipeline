#!/usr/bin/env hython
"""build_publish_hda.py - construit ylos::publish::0.1 (Lop) par code, hython uniquement.

Rejouable : detruit/reconstruit le noeud de build a chaque run, ecrase le .hdanc cible.
La definition du HDA vit en git via CE script, pas comme un blob binaire d'edition GUI.

Usage :
    hython tools/houdini/build_publish_hda.py
"""

import os
import sys

import hou

TYPE_NAME = "ylos::publish::0.1"
TYPE_LABEL = "Ylos Publish"
BUILD_NODE_NAME = "ylos_publish_build"
OTL_REL_PATH = os.path.join("plugins", "houdini", "otls", "ylos_publish.hdanc")
THUMB_CAM_PRIMPATH = "/cameras/ylos_thumb_cam"
THUMB_RES = 512


def _repo_root():
    # os.path.realpath (jamais dirname(__file__) nu) : meme fix que le bug symlink Blender,
    # applique ici au script de build lui-meme (pas seulement au module embarque du HDA).
    here = os.path.realpath(__file__)
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def _python_module_source():
    return '''\
"""Callback ylos::publish. Importe create_project.py comme source de verite unique pour
le locking/versioning/manifest - ne reimplemente jamais cette logique ici (cf. contrat
pipeline Ylos)."""

import os
import shutil
import sys
import tempfile

import hou


def _repo_root(node):
    # os.path.realpath sur le chemin de definition HDA (via l'API hou, pas __file__) :
    # un module embarque n'a pas de __file__ fiable, et meme s'il en avait un, le meme
    # fix symlink que Blender s'applique.
    # otl_path = REPO/plugins/houdini/otls/ylos_publish.hdanc -> 4 dirname() pour REPO
    # (1 pour le nom de fichier + 3 pour otls/houdini/plugins). Bug reel observe en GUI :
    # avec 3 dirname() on atterrit sur REPO/plugins (pas de create_project.py dedans),
    # ModuleNotFoundError. Masque en hython car le cwd du shell etait deja REPO (fallback
    # d'import via sys.path[0]='' qui cachait le bug) - jamais fiable, corrige ici.
    otl_path = os.path.realpath(node.type().definition().libraryFilePath())
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(otl_path))))


def _cp(node):
    root = _repo_root(node)
    if root not in sys.path:
        sys.path.insert(0, root)
    import create_project
    return create_project


def publish(kwargs):
    node = kwargs["node"]
    status_parm = node.parm("status")
    cp = _cp(node)

    project_root = node.evalParm("project_root")
    asset_name = node.evalParm("asset_name")
    asset_type = node.evalParm("asset_type")
    comment = node.evalParm("version_comment")

    try:
        cp.validate_publish_asset_name(asset_name, asset_type)

        staging_dir, final_dir = cp.allocate_publish_version(
            project_root, asset_name, asset_type, comment=comment
        )
        version = cp.publish_version_from_dir(final_dir)

        layer_path = os.path.join(
            str(staging_dir), "{}_lop_v{:03d}.usd".format(asset_name, version)
        )
        thumb_path = os.path.join(str(staging_dir), cp.LOP_THUMB_NAME)

        # Configure Layer : marque le save path calcule depuis staging_dir. Le ROP USD
        # exporte ensuite CE layer precisement (savestyle='separate', pas de flatten) :
        # Houdini ne serialise que son propre layer authored, jamais geo.usd.
        # Une instance de HDA verrouille interdit d'ecrire directement sur les parametres
        # des noeuds internes (hou.PermissionError) : on passe par les parametres promus
        # au niveau du noeud (_layer_savepath / _thumb_outputimage), relies aux noeuds
        # internes par expression channel-reference posee au build (cf. build_publish_hda.py).
        node.parm("_layer_savepath").set(layer_path)
        node.parm("_thumb_outputimage").set(thumb_path)

        # savestyle='flattenimplicitlayers' ecrit aussi un layer racine jetable (subLayers
        # -> notre fichier cible, deja autonome) : le confiner hors de staging_dir pour que
        # finalize_publish_version() n'y voie que le vrai layer + le thumb.
        scratch_dir = tempfile.mkdtemp(prefix="ylos_publish_scratch_")
        try:
            node.parm("_publish_scratch_output").set(
                os.path.join(scratch_dir, "root.usd")
            )

            publish_rop = node.node("publish_rop")
            publish_rop.render()

            thumb_rop = node.node("thumb_rop")
            thumb_rop.render()
        finally:
            shutil.rmtree(scratch_dir, ignore_errors=True)

        result = cp.finalize_publish_version(
            project_root, asset_name, staging_dir, final_dir, version, comment=comment
        )

        status_parm.set("OK - v{:03d} - {}".format(version, result["final_dir"]))
    except Exception as exc:
        status_parm.set("ERREUR: {}".format(exc))
        raise
'''


def _build_parm_template_group(cp):
    g = hou.ParmTemplateGroup()

    project_root = hou.StringParmTemplate(
        "project_root", "Project Root", 1,
        default_expression=(
            "import os\n"
            "p = os.path.expanduser('~/.ylos/active_project')\n"
            "try:\n"
            "    with open(p) as f:\n"
            "        return f.read().strip()\n"
            "except OSError:\n"
            "    return ''\n",
        ),
        default_expression_language=(hou.scriptLanguage.Python,),
    )
    g.append(project_root)

    g.append(hou.StringParmTemplate("asset_name", "Asset Name", 1))

    g.append(hou.StringParmTemplate(
        "asset_type", "Asset Type", 1,
        menu_items=list(cp.ASSET_TYPES),
        menu_labels=list(cp.ASSET_TYPES),
    ))

    g.append(hou.StringParmTemplate("version_comment", "Version Comment", 1))

    # Parametres promus (plomberie) : les noeuds internes d'une instance de HDA verrouillee
    # ne sont pas editables directement (hou.PermissionError). Ces deux parms au niveau du
    # noeud sont relies aux noeuds internes par channel-reference (cf. build()) ; le callback
    # ecrit ici, jamais sur configure_publish_layer/savepath ou thumb_rop/outputimage.
    for name, label in (
        ("_layer_savepath", "Layer Save Path (internal)"),
        ("_thumb_outputimage", "Thumb Output Image (internal)"),
        ("_publish_scratch_output", "Publish Scratch Output (internal)"),
    ):
        pt = hou.StringParmTemplate(name, label, 1)
        pt.hide(True)
        g.append(pt)

    g.append(hou.ButtonParmTemplate(
        "publish", "Publish",
        script_callback="hou.phm().publish(kwargs)",
        script_callback_language=hou.scriptLanguage.Python,
    ))

    status = hou.StringParmTemplate("status", "Status", 1)
    status.setDefaultValue(("",))
    status.setDisableWhen("{ 1 == 1 }")
    g.append(status)

    return g


def build():
    repo_root = _repo_root()
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    import create_project as cp

    otl_path = os.path.join(repo_root, OTL_REL_PATH)
    os.makedirs(os.path.dirname(otl_path), exist_ok=True)

    stage = hou.node("/stage")

    existing = stage.node(BUILD_NODE_NAME)
    if existing:
        existing.destroy()

    subnet = stage.createNode("subnet", BUILD_NODE_NAME)
    indirect_in = subnet.indirectInputs()[0]

    cfg = subnet.createNode("configurelayer", "configure_publish_layer")
    cfg.setInput(0, indirect_in)
    cfg.parm("startnewlayer").set(1)
    cfg.parm("setsavepath").set(1)

    output0 = subnet.node("output0")
    output0.setInput(0, cfg)

    # Camera dediee au thumb, sur une branche separee (jamais mergee dans output0) :
    # le layer publie ne doit JAMAIS contenir cette camera.
    cam = subnet.createNode("camera", "thumb_cam")
    cam.setInput(0, cfg)
    cam.parm("primpath").set(THUMB_CAM_PRIMPATH)
    cam.parm("tx").set(0)
    cam.parm("ty").set(1.5)
    cam.parm("tz").set(6)
    cam.parm("lookatenable").set(1)
    cam.parm("lookatpositionx").set(0)
    cam.parm("lookatpositiony").set(0)
    cam.parm("lookatpositionz").set(0)

    # ROPs "detaches" : lisent un LOP via loppath (chemin relatif), ne sont jamais wires
    # dans le flux reseau principal. Declenches par le callback Python, pas par le cook.
    publish_rop = subnet.createNode("usd_rop", "publish_rop")
    publish_rop.parm("loppath").set(publish_rop.relativePathTo(cfg))
    # 'flattenimplicitlayers' (PAS 'flattenstage'/'flattenalllayers') : collapse les
    # sous-couches anonymes/en-memoire du reseau LOP amont (le sphere de test, les edits
    # de Configure Layer...) dans le fichier unique vise par savepath, mais preserve intactes
    # les references deja file-backees (ex: un geo.usd sublayer-e depuis un step precedent -
    # jamais touche, jamais re-serialise). 'separate' echoue des qu'un noeud upstream produit
    # un layer anonyme sans savepath explicite (verifie empiriquement : hou.OperationFailed
    # "Layer saved to a location generated from a node path").
    publish_rop.parm("savestyle").set("flattenimplicitlayers")
    # Sans ce toggle a 0, le ROP erreure des qu'un noeud amont produit un layer anonyme sans
    # savepath explicite (verifie empiriquement), au lieu de le flattener silencieusement
    # comme le nom du savestyle le promet. A 0 : un seul fichier ecrit, confirme par un
    # `find` sur toute l'arborescence de test (cf. investigation build_publish_hda).
    publish_rop.parm("errorsavingimplicitpaths").set(0)
    publish_rop.parm("trange").set(0)                # frame courante uniquement

    thumb_rop = subnet.createNode("usdrender_rop", "thumb_rop")
    thumb_rop.parm("loppath").set(thumb_rop.relativePathTo(cam))
    thumb_rop.parm("trange").set(0)
    thumb_rop.parm("override_camera").set(THUMB_CAM_PRIMPATH)
    thumb_rop.parm("override_res").set("specific")
    thumb_rop.parm("res_user1").set(THUMB_RES)
    thumb_rop.parm("res_user2").set(THUMB_RES)

    subnet.layoutChildren()

    hda_node = subnet.createDigitalAsset(
        name=TYPE_NAME,
        hda_file_name=otl_path,
        description=TYPE_LABEL,
        min_num_inputs=1,
        max_num_inputs=1,
        ignore_external_references=True,
    )

    hda_def = hda_node.type().definition()
    hda_def.setParmTemplateGroup(_build_parm_template_group(cp))
    hda_def.addSection("PythonModule", _python_module_source())

    # Channel-reference : "../<parm>" resout, pour un noeud interne, vers le parametre du
    # noeud qui le contient (l'instance de HDA elle-meme une fois promu). Pose APRES
    # createDigitalAsset pour que ce soit bien le nom de parm promu qui existe deja.
    hda_node.node("configure_publish_layer").parm("savepath").setExpression(
        'chs("../_layer_savepath")'
    )
    hda_node.node("thumb_rop").parm("outputimage").setExpression(
        'chs("../_thumb_outputimage")'
    )
    # savestyle='flattenimplicitlayers' ecrit aussi un layer racine "de reliure" (subLayers
    # -> notre fichier cible) sur lopoutput, en plus du fichier cible lui-meme (verifie
    # empiriquement via pxr.Usd : le fichier cible est deja autonome, sans subLayers - ce
    # layer racine est un artefact jetable, jamais utile en aval). Sans ce wiring il retombe
    # sur le nommage $HIP par defaut et pollue le repo (verifie : plugins/../geo/untitled...).
    # Redirige vers un repertoire scratch temporaire (jamais staging_dir : finalize_publish_
    # version() ne doit voir QUE le vrai layer + le thumb dans staging_dir).
    hda_node.node("publish_rop").parm("lopoutput").setExpression(
        'chs("../_publish_scratch_output")'
    )
    # template_node=hda_node est OBLIGATOIRE : hda_def.save() sans template_node ne remonte
    # PAS l'etat live du noeud dans la definition (verifie empiriquement - les deux
    # setExpression() ci-dessus etaient sinon silencieusement perdus, rawValue() vide apres
    # rechargement). Cf. doc hou.HDADefinition.save : "If None, this method does not update
    # the definition's contents."
    hda_def.save(otl_path, template_node=hda_node)

    hda_node.destroy()

    print("[ok] HDA construit : {}".format(otl_path))
    return otl_path


if __name__ == "__main__":
    build()
