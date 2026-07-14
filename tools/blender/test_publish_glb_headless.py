# -*- coding: utf-8 -*-
"""Test headless Blender : publish d'un projet en target 'web' -> artifact GLB propre.

Couvre CC#2 :
  A. Hygiene des noms : un objet renomme dont la donnee garde 'Cube' voit son datablock
     realigne sur le nom d'objet AVANT export (prims/nodes = noms d'objets).
  B. Format par cible : un projet prod_type=XR (-> pipeline_target 'web') publie du .glb
     via op_publish, pas du .usd. Manifest complete + thumbnail inchanges (contrat agnostique
     a l'extension). Le cas offline/.usd reste couvert par les tests stdlib existants.

Lancer :
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --factory-startup --python tools/blender/test_publish_glb_headless.py

Exit code != 0 en cas d'echec (assert / exception), 0 si tout passe.
"""
import json
import os
import sys
import tempfile
import traceback

_THIS = os.path.realpath(__file__)
REPO_ROOT = os.path.normpath(os.path.join(_THIS, "..", "..", ".."))  # tools/blender/.. -> repo
PLUGINS = os.path.join(REPO_ROOT, "plugins")
for p in (REPO_ROOT, PLUGINS):
    if p not in sys.path:
        sys.path.insert(0, p)


def _fail(msg, exc=None):
    print("FAIL:", msg)
    if exc is not None:
        traceback.print_exc()
    sys.exit(1)


def main():
    import bpy
    import create_project as cp
    import blender as addon

    try:
        addon.register()
    except Exception as e:
        _fail("addon.register() a leve", e)

    work = tempfile.mkdtemp(prefix="ylos_pub_glb_")
    root = os.path.join(work, "src")
    cache = os.path.join(work, "cache")
    os.makedirs(root)
    os.makedirs(cache)

    # 1. Projet web (prod_type XR -> pipeline_target 'web' ecrit a la creation).
    proj = cp.create("WebProj", root=root, cache=cache, prod_type="XR")
    project_dir = str(proj["source"])

    pj = json.loads(open(os.path.join(project_dir, "_pipeline", "project.json"),
                         encoding="utf-8").read())
    if pj.get("pipeline_target") != "web":
        _fail(f"project.json pipeline_target attendu 'web', obtenu {pj.get('pipeline_target')!r}")
    if cp.get_pipeline_target(project_dir) != "web":
        _fail(f"get_pipeline_target != 'web' (obtenu {cp.get_pipeline_target(project_dir)!r})")
    print("ok  create() ecrit pipeline_target='web' (XR) + get_pipeline_target concordent")

    entity = "PROP_Cube_Default"
    cp.create_asset(project_dir, entity, entity_type="asset", asset_type="PROP")
    ent_manifest = json.loads(open(os.path.join(project_dir, "assets", entity, "manifest.json"),
                                   encoding="utf-8").read())
    step = ent_manifest["steps"][0]

    # 2. Scene : cube renomme (objet != donnee) dans une collection au nom de l'asset.
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    cube = bpy.context.active_object
    cube.name = f"{entity}_geo"       # nom d'objet propre
    cube.data.name = "Cube"           # datablock volontairement desaligne (mono-user)
    if cube.data.users != 1:
        _fail("prerequis test invalide : datablock cube multi-user")

    coll = bpy.data.collections.new(entity)
    bpy.context.scene.collection.children.link(coll)
    for c in list(cube.users_collection):
        c.objects.unlink(cube)
    coll.objects.link(cube)

    scene = bpy.context.scene
    scene.ylos_project_path = project_dir
    scene.ylos_current_asset = entity
    scene.ylos_context_type = "ASSET"
    scene.ylos_current_step = step

    # 3. Publish (EXEC_DEFAULT : execute() directement, sans dialog).
    res = bpy.ops.ylos.publish("EXEC_DEFAULT", step=step, allow_full_scene=False,
                               load_after=False)
    if res != {"FINISHED"}:
        _fail(f"ylos.publish n'a pas FINISHED : {res}")

    # A. Hygiene des noms : datablock realigne sur l'objet (mono-user).
    if cube.data.name != cube.name:
        _fail(f"hygiene noms : data '{cube.data.name}' != objet '{cube.name}' apres publish")
    print(f"ok  hygiene noms : datablock realigne sur l'objet ({cube.name})")

    # B. Manifest : entree 'complete', artifact .glb non vide, thumbnail present.
    ent_manifest = json.loads(open(os.path.join(project_dir, "assets", entity, "manifest.json"),
                                   encoding="utf-8").read())
    entries = ent_manifest.get("step_publishes", {}).get(step, [])
    complete = [e for e in entries if e.get("status") == "complete"]
    if not complete:
        _fail(f"aucune entree 'complete' dans step_publishes[{step!r}] : {entries}")
    entry = complete[-1]

    artifact_rel = entry.get("artifact")
    if not artifact_rel or not artifact_rel.endswith(".glb"):
        _fail(f"artifact attendu .glb, obtenu {artifact_rel!r}")
    entity_dir = os.path.join(project_dir, "assets", entity)
    artifact_abs = os.path.join(entity_dir, artifact_rel)
    if not os.path.isfile(artifact_abs) or os.path.getsize(artifact_abs) <= 0:
        _fail(f"artifact GLB absent ou vide : {artifact_abs}")
    print(f"ok  artifact .glb complete non vide : {artifact_rel} "
          f"({os.path.getsize(artifact_abs)} octets)")

    thumb_rel = entry.get("thumbnail")
    if not thumb_rel:
        _fail(f"thumbnail absent de l'entree manifest : {entry}")
    thumb_abs = os.path.join(entity_dir, thumb_rel)
    if not os.path.isfile(thumb_abs) or os.path.getsize(thumb_abs) <= 0:
        _fail(f"thumbnail absent ou vide : {thumb_abs}")
    print(f"ok  thumbnail present ({thumb_rel})")

    try:
        addon.unregister()
    except Exception as e:
        _fail("addon.unregister() a leve", e)

    print("\nPASS: publish GLB (target web) + hygiene des noms OK")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _fail("exception inattendue", e)
