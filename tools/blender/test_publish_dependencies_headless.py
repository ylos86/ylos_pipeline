# -*- coding: utf-8 -*-
"""Headless Blender test: a publish RECORDS what the scene had imported (schema 2.2
'dependencies'), and the orchestrator's dependency index links the two entities.

Why this matters (and why the manifest is the only usable source here): a Blender publish
writes a .usdc / .glb artifact. create_project.build_dependency_index() can only scan USD
ASCII layers for @references@ - a crate or a GLB is opaque to it. Without the DCC recording
its imports at publish time, "which shot uses this asset" is simply blind for everything
Blender produces.

Scenario (real operator path end to end, temp project, nothing mocked):
  1. publish asset A (modeling)                        -> A v001
  2. import A into a TAGGED collection (ylos.import_product)
  3. publish asset B (modeling) from that same scene   -> B v001, dependencies = [A/modeling/v001]
  4. assert B's manifest entry carries 'dependencies'
  5. assert create_project.build_dependency_index()/entity_dependencies() link B -> A
  6. republish A (v002), then assert B is reported OUTDATED (it still pins A v001), and that
     op_publish cached that impact for the panel.

Run:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --python tools/blender/test_publish_dependencies_headless.py

Exit code != 0 on failure (assert / exception), 0 if everything passes.
"""
import json
import os
import shutil
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


def _manifest(project_dir, entity):
    path = os.path.join(project_dir, "assets", entity, "manifest.json")
    return json.loads(open(path, encoding="utf-8").read())


def _complete_entries(project_dir, entity, step):
    entries = _manifest(project_dir, entity).get("step_publishes", {}).get(step, [])
    return [e for e in entries if e.get("status") == "complete"]


def _clear_scene():
    import bpy
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()


def _cube_in_collection(entity, size, location):
    """A cube inside a collection NAMED AFTER the entity - that is how
    scene_checker.get_asset_objects_for_publish resolves an entity's objects."""
    import bpy
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.active_object
    obj.name = f"GEO_{entity}_body"
    coll = bpy.data.collections.get(entity) or bpy.data.collections.new(entity)
    if coll.name not in {c.name for c in bpy.context.scene.collection.children}:
        bpy.context.scene.collection.children.link(coll)
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    coll.objects.link(obj)
    return obj


def main():
    import bpy
    import create_project as cp
    import blender as addon
    from blender.operators import op_publish, op_update_imports

    try:
        addon.register()
    except Exception as e:
        _fail("addon.register() raised", e)

    work = tempfile.mkdtemp(prefix="ylos_pub_deps_")
    try:
        root = os.path.join(work, "src")
        cache = os.path.join(work, "cache")
        os.makedirs(root)
        os.makedirs(cache)

        # FILM -> pipeline target 'offline' -> USD artifacts (the case where the ASCII layer
        # scan is blind and the manifest source is the only one that works).
        proj = cp.create("DepsProj", root=root, cache=cache, prod_type="FILM")
        project_dir = str(proj["source"])

        entity_a = "PROP_Rock_Default"
        entity_b = "PROP_Cairn_Default"
        for name in (entity_a, entity_b):
            cp.create_asset(project_dir, name, entity_type="asset", asset_type="PROP")
        step = "modeling"

        scene = bpy.context.scene
        scene.ylos_project_path = project_dir
        scene.ylos_project_name = "DepsProj"
        scene.ylos_prod_type = "FILM"
        scene.ylos_context_type = "ASSET"
        scene.ylos_asset_type = "PROP"

        # ---- 1. Publish A ---------------------------------------------------------------
        _clear_scene()
        _cube_in_collection(entity_a, 2.0, (0, 0, 0))
        scene.ylos_current_asset = entity_a
        scene.ylos_current_step = step
        res = bpy.ops.ylos.publish('EXEC_DEFAULT', step=step, allow_full_scene=False)
        if res != {"FINISHED"}:
            _fail(f"publish of {entity_a} did not FINISH: {res}")

        entry_a = _complete_entries(project_dir, entity_a, step)[-1]
        if not str(entry_a.get("artifact", "")).endswith(".usdc"):
            _fail(f"offline target must publish .usdc, got {entry_a.get('artifact')!r}")
        print(f"ok  {entity_a} published v{entry_a['version']:03d} ({entry_a['artifact']})")

        # A published alone imports nothing -> no dependency recorded (and NOT a
        # self-dependency: the scene still holds A's own objects).
        if entry_a.get("dependencies"):
            _fail(f"{entity_a} v001 should carry no dependency, got {entry_a['dependencies']}")
        print("ok  a publish with no import records no dependency (no self-dependency either)")

        # ---- 2. Import A into a tagged collection ---------------------------------------
        _clear_scene()
        res = bpy.ops.ylos.import_product('EXEC_DEFAULT', entity=entity_a, step=step, version=0)
        if res != {"FINISHED"}:
            _fail(f"ylos.import_product({entity_a}) did not FINISH: {res}")
        tagged = op_update_imports.tagged_import_collections()
        if len(tagged) != 1 or tagged[0].get("ylos_import_entity") != entity_a:
            _fail(f"expected exactly one tagged import of {entity_a}, got "
                  f"{[(c.name, c.get('ylos_import_entity')) for c in tagged]}")
        print(f"ok  {entity_a} imported into tagged collection '{tagged[0].name}'")

        # scene_dependencies() must see it, and must NOT list the entity being published.
        deps_seen = op_publish.scene_dependencies(entity_b)
        if deps_seen != [{"entity": entity_a, "step": step, "version": 1}]:
            _fail(f"scene_dependencies({entity_b}) = {deps_seen!r}")
        if op_publish.scene_dependencies(entity_a) != []:
            _fail("scene_dependencies() must filter out the entity being published")
        print("ok  scene_dependencies() reads the tagged imports and drops the self-reference")

        # ---- 3. Publish B from that scene ------------------------------------------------
        _cube_in_collection(entity_b, 1.0, (4, 0, 0))
        scene.ylos_current_asset = entity_b
        scene.ylos_current_step = step
        res = bpy.ops.ylos.publish('EXEC_DEFAULT', step=step, allow_full_scene=False)
        if res != {"FINISHED"}:
            _fail(f"publish of {entity_b} did not FINISH: {res}")

        # ---- 4. B's manifest entry carries the dependency --------------------------------
        entry_b = _complete_entries(project_dir, entity_b, step)[-1]
        recorded = entry_b.get("dependencies")
        if recorded != [{"entity": entity_a, "step": step, "version": 1}]:
            _fail(f"{entity_b} manifest 'dependencies' = {recorded!r} "
                  f"(expected [{{'entity': {entity_a!r}, 'step': {step!r}, 'version': 1}}])")
        print(f"ok  {entity_b} manifest entry carries dependencies = {recorded}")

        # ---- 5. The orchestrator index links them ----------------------------------------
        index = cp.build_dependency_index(project_dir)
        used_in_a = index["used_in"].get(entity_a, [])
        consumers = {e["consumer"]["entity"] for e in used_in_a}
        if entity_b not in consumers:
            _fail(f"dependency index: {entity_a} 'used_in' = {sorted(consumers)} "
                  f"(expected to contain {entity_b})")
        uses_b = {e["dependency"]["entity"] for e in index["uses"].get(entity_b, [])}
        if entity_a not in uses_b:
            _fail(f"dependency index: {entity_b} 'uses' = {sorted(uses_b)}")
        edge = [e for e in used_in_a if e["consumer"]["entity"] == entity_b][0]
        if edge["source"] != "manifest":
            _fail(f"edge source expected 'manifest' (the .usdc is opaque to the layer scan), "
                  f"got {edge['source']!r}")
        if edge["dependency"]["outdated"]:
            _fail("the edge must not be outdated yet (A is still at v001)")
        print(f"ok  dependency index links {entity_b} -> {entity_a} (source='manifest', current)")

        # ---- 6. Republish A -> B becomes outdated, and the impact is cached --------------
        _clear_scene()
        _cube_in_collection(entity_a, 3.0, (0, 0, 0))
        scene.ylos_current_asset = entity_a
        scene.ylos_current_step = step
        res = bpy.ops.ylos.publish('EXEC_DEFAULT', step=step, allow_full_scene=False)
        if res != {"FINISHED"}:
            _fail(f"republish of {entity_a} did not FINISH: {res}")

        outdated = [e for e in cp.entity_dependencies(project_dir, entity_a)["used_in"]
                    if e["dependency"]["outdated"]]
        names = {e["consumer"]["entity"] for e in outdated}
        if entity_b not in names:
            _fail(f"after republishing {entity_a} v002, outdated consumers = {sorted(names)} "
                  f"(expected to contain {entity_b})")
        print(f"ok  republishing {entity_a} marks {entity_b} outdated "
              f"(pins v{outdated[0]['dependency']['version']:03d}, latest "
              f"v{outdated[0]['dependency']['latest_version']:03d})")

        cached = op_update_imports.get_cached_downstream_impact()
        if cached.get("entity") != entity_a:
            _fail(f"downstream impact cache entity = {cached.get('entity')!r} "
                  f"(expected {entity_a!r})")
        cached_names = {e["consumer"]["entity"] for e in cached.get("outdated", [])}
        if cached_names != names:
            _fail(f"downstream impact cache = {sorted(cached_names)} != index {sorted(names)}")
        print("ok  op_publish cached the downstream impact for the panel "
              f"({len(cached['outdated'])} edge(s))")

        try:
            addon.unregister()
        except Exception as e:
            _fail("addon.unregister() raised", e)

        print("\nPASS: publish records schema-2.2 dependencies and the index links them")
        sys.exit(0)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _fail("unexpected exception", e)
