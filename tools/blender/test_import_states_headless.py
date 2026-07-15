# -*- coding: utf-8 -*-
"""Test headless Blender : import states (State-Manager-lite, INC-5) - op_import_product.py
(absorbe op_load_publish.py) + op_update_imports.py (ylos.check_updates/ylos.update_import).

Scenario exact de la spec : publier un cube v1, importer (props taguees posees), publier
v2 (avec un objet de plus, pour verifier le rapport N objets avant/apres), check_updates
detecte la mise a jour disponible, update remplace le contenu de la collection.

Lancer :
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --python tools/blender/test_import_states_headless.py

Exit code != 0 en cas d'echec (assert / exception), 0 si tout passe.
"""
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


def _move_to_collection(obj, target_collection):
    """Deplace 'obj' dans 'target_collection', quelle que soit sa collection d'origine
    (jamais d'hypothese sur 'scene.collection' specifiquement - la collection active au
    moment du primitive_add depend de l'etat du view_layer, pas garanti)."""
    for coll in list(obj.users_collection):
        coll.objects.unlink(obj)
    target_collection.objects.link(obj)


def main():
    import bpy
    import create_project as cp
    import blender as addon
    from blender.operators.op_import_product import import_state_collection_name
    from blender.operators.op_update_imports import get_cached_update_results

    work = tempfile.mkdtemp(prefix="ylos_import_states_test_")
    try:
        root = os.path.join(work, "src")
        cache = os.path.join(work, "cache")
        os.makedirs(root)
        os.makedirs(cache)

        # Cible web (GLB) - complementaire du parcours USD deja teste ailleurs
        # (test_publish_glb_headless / test_launch_context invocation A).
        proj = cp.create("ImportStatesTest", root=root, cache=cache, prod_type="XR")
        project_dir = str(proj["source"])
        entity = "PROP_Cube_Default"
        cp.create_asset(project_dir, entity, entity_type="asset", asset_type="PROP")

        try:
            addon.register()
        except Exception as e:
            _fail("addon.register() a leve", e)
        print("ok  addon.register() sans exception")

        scene = bpy.context.scene
        scene.ylos_project_path  = project_dir
        scene.ylos_project_name  = "ImportStatesTest"
        scene.ylos_current_asset = entity
        scene.ylos_current_step  = "modeling"
        scene.ylos_context_type  = "ASSET"

        # --- 1. Publier v1 (un seul cube) ---
        # Les objets a publier vivent dans une collection nommee EXACTEMENT comme l'entite
        # (cf. core/scene_checker.get_asset_objects_for_publish) : publish reste scope a
        # cette collection, jamais 'allow_full_scene' - qui balaierait aussi la collection
        # d'import (nom different, cree a l'etape 2) lors du publish v2.
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()
        src_collection = bpy.data.collections.new(entity)
        scene.collection.children.link(src_collection)

        bpy.ops.mesh.primitive_cube_add(size=2.0)
        _move_to_collection(bpy.context.active_object, src_collection)

        res = bpy.ops.ylos.publish('EXEC_DEFAULT', step="modeling", load_after=False)
        if res != {"FINISHED"}:
            _fail(f"publish v1 a retourne {res}")
        print("ok  publish v1 (1 objet, collection source scopee)")

        # --- 2. Importer (props taguees posees) ---
        res = bpy.ops.ylos.import_product(
            'EXEC_DEFAULT', entity=entity, step="modeling", version=0,  # 0 = latest
        )
        if res != {"FINISHED"}:
            _fail(f"import_product a retourne {res}")

        col_name = import_state_collection_name(entity, "modeling")
        collection = bpy.data.collections.get(col_name)
        if collection is None:
            _fail(f"collection '{col_name}' non creee")

        expected_tags = {
            "ylos_import_entity": entity,
            "ylos_import_step": "modeling",
            "ylos_import_version": 1,
        }
        for key, val in expected_tags.items():
            if collection.get(key) != val:
                _fail(f"tag {key!r} : {collection.get(key)!r} != {val!r}")
        if not collection.get("ylos_import_path", "").endswith(".glb"):
            _fail(f"ylos_import_path inattendu : {collection.get('ylos_import_path')!r}")
        n_v1 = len(collection.objects)
        if n_v1 != 1:
            _fail(f"collection v1 : {n_v1} objet(s) != 1")
        print(f"ok  import_product : collection '{col_name}' taguee v001, {n_v1} objet(s)")

        # Re-importer le meme entity/step doit echouer (deja importe -> Update Import).
        # bpy.ops (contrairement a un appel direct de execute()) leve RuntimeError quand
        # l'operateur reporte {'ERROR'} + retourne {'CANCELLED'} - comportement natif de
        # l'API, pas une exception a avaler silencieusement.
        try:
            bpy.ops.ylos.import_product(
                'EXEC_DEFAULT', entity=entity, step="modeling", version=0,
            )
            _fail("re-import sur collection existante n'a pas leve (devrait etre refuse)")
        except RuntimeError as e:
            if "already imported" not in str(e):
                _fail(f"re-import : message inattendu : {e}")
        print("ok  re-import sur collection existante refuse (utiliser Update Import)")

        # --- 3. Publier v2 (deux objets dans la collection source, pour un rapport
        #        avant/apres non-trivial - la collection d'import cree a l'etape 2 reste
        #        hors scope, nom different) ---
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(3, 0, 0))
        _move_to_collection(bpy.context.active_object, src_collection)

        res = bpy.ops.ylos.publish('EXEC_DEFAULT', step="modeling", load_after=False)
        if res != {"FINISHED"}:
            _fail(f"publish v2 a retourne {res}")
        print("ok  publish v2 (2 objets, collection source scopee)")

        # --- 4. check_updates detecte la mise a jour ---
        res = bpy.ops.ylos.check_updates('EXEC_DEFAULT')
        if res != {"FINISHED"}:
            _fail(f"check_updates a retourne {res}")

        cache = get_cached_update_results()
        status = cache.get(col_name)
        if status is None:
            _fail(f"check_updates : '{col_name}' absent du cache : {cache!r}")
        if not status["has_update"] or status["current"] != 1 or status["latest"] != 2:
            _fail(f"check_updates : etat inattendu {status!r}")
        print(f"ok  check_updates : {status!r}")

        # --- 5. update_import remplace le contenu ---
        res = bpy.ops.ylos.update_import('EXEC_DEFAULT', collection_name=col_name)
        if res != {"FINISHED"}:
            _fail(f"update_import a retourne {res}")

        if collection.get("ylos_import_version") != 2:
            _fail(f"apres update, version taguee = {collection.get('ylos_import_version')!r} != 2")
        n_v2 = len(collection.objects)
        if n_v2 != 2:
            _fail(f"collection apres update : {n_v2} objet(s) != 2 (v1 avait {n_v1})")
        print(f"ok  update_import : v001 -> v002, {n_v1} -> {n_v2} objets")

        # Update alors qu'on est deja a la derniere version -> no-op FINISHED (pas d'erreur).
        res = bpy.ops.ylos.update_import('EXEC_DEFAULT', collection_name=col_name)
        if res != {"FINISHED"}:
            _fail(f"update_import (deja a jour) a retourne {res}")
        if len(collection.objects) != 2:
            _fail("update_import (deja a jour) a modifie la collection")
        print("ok  update_import (deja a jour) : no-op propre")

        try:
            addon.unregister()
        except Exception as e:
            _fail("addon.unregister() a leve", e)
        print("ok  addon.unregister() sans exception")

        print("\nPASS: import states (import_product + check_updates + update_import) headless OK")
        sys.exit(0)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _fail("exception inattendue", e)
