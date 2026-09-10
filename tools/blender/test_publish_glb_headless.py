# -*- coding: utf-8 -*-
"""Headless Blender test: publish of a 'web' target project -> clean GLB artifact.

Covers CC#2:
  A. Name hygiene: a renamed object whose data still holds 'Cube' has its datablock
     realigned to the object name BEFORE export (prims/nodes = object names).
  B. Format per target: a prod_type=XR project (-> pipeline_target 'web') publishes .glb
     via op_publish, not .usd. Complete manifest + thumbnail unchanged (contract agnostic
     to the extension). The offline/.usd case stays covered by the existing stdlib tests.

Run:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --factory-startup --python tools/blender/test_publish_glb_headless.py

Exit code != 0 on failure (assert / exception), 0 if everything passes.
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
        _fail("addon.register() raised", e)

    work = tempfile.mkdtemp(prefix="ylos_pub_glb_")
    root = os.path.join(work, "src")
    cache = os.path.join(work, "cache")
    os.makedirs(root)
    os.makedirs(cache)

    # 1. Web project (prod_type XR -> pipeline_target 'web' written at creation).
    proj = cp.create("WebProj", root=root, cache=cache, prod_type="XR")
    project_dir = str(proj["source"])

    pj = json.loads(open(os.path.join(project_dir, "_pipeline", "project.json"),
                         encoding="utf-8").read())
    if pj.get("pipeline_target") != "web":
        _fail(f"project.json pipeline_target expected 'web', got {pj.get('pipeline_target')!r}")
    if cp.get_pipeline_target(project_dir) != "web":
        _fail(f"get_pipeline_target != 'web' (got {cp.get_pipeline_target(project_dir)!r})")
    print("ok  create() writes pipeline_target='web' (XR) + get_pipeline_target agree")

    entity = "PROP_Cube_Default"
    cp.create_asset(project_dir, entity, entity_type="asset", asset_type="PROP")
    ent_manifest = json.loads(open(os.path.join(project_dir, "assets", entity, "manifest.json"),
                                   encoding="utf-8").read())
    step = ent_manifest["steps"][0]

    # 2. Scene: renamed cube (object != data) in a collection named after the asset.
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    bpy.ops.mesh.primitive_cube_add(size=2.0)
    cube = bpy.context.active_object
    cube.name = f"{entity}_geo"       # clean object name
    cube.data.name = "Cube"           # datablock deliberately misaligned (single-user)
    if cube.data.users != 1:
        _fail("invalid test prerequisite: multi-user cube datablock")

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

    # 3. Publish (EXEC_DEFAULT: execute() directly, without dialog).
    res = bpy.ops.ylos.publish("EXEC_DEFAULT", step=step, allow_full_scene=False,
                               load_after=False)
    if res != {"FINISHED"}:
        _fail(f"ylos.publish did not FINISH: {res}")

    # A. Name hygiene: datablock realigned to the object (single-user).
    if cube.data.name != cube.name:
        _fail(f"name hygiene: data '{cube.data.name}' != object '{cube.name}' after publish")
    print(f"ok  name hygiene: datablock realigned to the object ({cube.name})")

    # B. Manifest: 'complete' entry, non-empty .glb artifact, thumbnail present.
    ent_manifest = json.loads(open(os.path.join(project_dir, "assets", entity, "manifest.json"),
                                   encoding="utf-8").read())
    entries = ent_manifest.get("step_publishes", {}).get(step, [])
    complete = [e for e in entries if e.get("status") == "complete"]
    if not complete:
        _fail(f"no 'complete' entry in step_publishes[{step!r}]: {entries}")
    entry = complete[-1]

    artifact_rel = entry.get("artifact")
    if not artifact_rel or not artifact_rel.endswith(".glb"):
        _fail(f"artifact expected .glb, got {artifact_rel!r}")
    entity_dir = os.path.join(project_dir, "assets", entity)
    artifact_abs = os.path.join(entity_dir, artifact_rel)
    if not os.path.isfile(artifact_abs) or os.path.getsize(artifact_abs) <= 0:
        _fail(f"GLB artifact missing or empty: {artifact_abs}")
    print(f"ok  .glb artifact complete non-empty: {artifact_rel} "
          f"({os.path.getsize(artifact_abs)} bytes)")

    thumb_rel = entry.get("thumbnail")
    if not thumb_rel:
        _fail(f"thumbnail missing from manifest entry: {entry}")
    thumb_abs = os.path.join(entity_dir, thumb_rel)
    if not os.path.isfile(thumb_abs) or os.path.getsize(thumb_abs) <= 0:
        _fail(f"thumbnail missing or empty: {thumb_abs}")
    print(f"ok  thumbnail present ({thumb_rel})")

    try:
        addon.unregister()
    except Exception as e:
        _fail("addon.unregister() raised", e)

    print("\nPASS: publish GLB (web target) + name hygiene OK")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _fail("unexpected exception", e)
