# -*- coding: utf-8 -*-
"""Test headless Blender : le State Manager (facon Prism). Verifie la SEMANTIQUE nouvelle -
plusieurs export states empiles, executes par UN seul Publish (ylos.publish_states), plus les
operateurs add/remove/reorder et le skip d'un state desactive.

Scenario exact :
  - projet FILM (offline -> publish USD) + asset a steps par defaut (modeling/lookdev present).
  - add 2 export states (modeling, lookdev) ; reorder (UP/DOWN) ; add+remove un 3e (test remove).
  - ylos.publish_states -> 2 publishes 'complete' finalises sur disque (un par step).
  - desactiver le state lookdev -> re-Publish -> seul modeling monte en v2 (lookdev reste v1).
  - last_result / last_version des states renseignes.

Lancer :
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --factory-startup --python tools/blender/test_state_manager_headless.py

Exit code != 0 en cas d'echec, 0 si tout passe. Hors CI stdlib (exige Blender).
"""
import os
import shutil
import sys
import tempfile
import traceback

_THIS = os.path.realpath(__file__)
REPO_ROOT = os.path.normpath(os.path.join(_THIS, "..", "..", ".."))
PLUGINS = os.path.join(REPO_ROOT, "plugins")
for p in (REPO_ROOT, PLUGINS):
    if p not in sys.path:
        sys.path.insert(0, p)


def _fail(msg, exc=None):
    print("FAIL:", msg)
    if exc is not None:
        traceback.print_exc()
    sys.exit(1)


def _complete_versions(cp, project_dir, entity, step):
    return sorted(
        p["version"] for p in cp.list_publishes(project_dir, entity, step, "asset")
        if p.get("status") == "complete"
    )


def main():
    import bpy
    import create_project as cp
    import blender as addon

    work = tempfile.mkdtemp(prefix="ylos_state_mgr_test_")
    try:
        root = os.path.join(work, "src")
        cache = os.path.join(work, "cache")
        os.makedirs(root)
        os.makedirs(cache)

        proj = cp.create("StateMgrTest", root=root, cache=cache, prod_type="FILM")
        project_dir = str(proj["source"])
        entity = "PROP_Box_Default"
        cp.create_asset(project_dir, entity, entity_type="asset", asset_type="PROP")

        try:
            addon.register()
        except Exception as e:
            _fail("addon.register() a leve", e)

        scene = bpy.context.scene
        scene.ylos_project_path = project_dir
        scene.ylos_project_name = "StateMgrTest"
        scene.ylos_prod_type = "FILM"
        scene.ylos_context_type = "ASSET"
        scene.ylos_asset_type = "PROP"
        scene.ylos_current_asset = entity

        bpy.ops.mesh.primitive_cube_add(size=2.0)

        states = scene.ylos_export_states

        # --- add : modeling puis lookdev ---
        scene.ylos_current_step = "modeling"
        bpy.ops.ylos.state_add_export()
        scene.ylos_current_step = "lookdev"
        bpy.ops.ylos.state_add_export()
        if len(states) != 2:
            _fail(f"apres 2 add : {len(states)} states (attendu 2)")
        if states[0].step != "modeling" or states[1].step != "lookdev":
            _fail(f"steps inattendus : {[s.step for s in states]}")
        print("ok  add x2 -> [modeling, lookdev]")

        # --- reorder : UP sur l'index 1 -> lookdev remonte ---
        scene.ylos_export_states_index = 1
        bpy.ops.ylos.state_move_export(direction="UP")
        if scene.ylos_export_states_index != 0 or states[0].step != "lookdev":
            _fail(f"apres move UP : index={scene.ylos_export_states_index}, "
                  f"steps={[s.step for s in states]}")
        bpy.ops.ylos.state_move_export(direction="DOWN")
        if states[0].step != "modeling" or states[1].step != "lookdev":
            _fail(f"apres move DOWN : steps={[s.step for s in states]}")
        print("ok  reorder UP/DOWN")

        # --- remove : ajouter un 3e jetable puis le retirer ---
        bpy.ops.ylos.state_add_export()  # 3e (lookdev, current_step)
        if len(states) != 3:
            _fail(f"apres add 3e : {len(states)} states (attendu 3)")
        scene.ylos_export_states_index = 2
        bpy.ops.ylos.state_remove_export()
        if len(states) != 2:
            _fail(f"apres remove : {len(states)} states (attendu 2)")
        print("ok  remove (3e retire)")

        # --- Publish unique : les 2 states -> 2 publishes 'complete' ---
        for s in states:
            s.allow_full_scene = True  # objets non nommes par convention -> full scene
        res = bpy.ops.ylos.publish_states('EXEC_DEFAULT')
        if res != {"FINISHED"}:
            _fail(f"ylos.publish_states a retourne {res} (attendu FINISHED)")

        mod_v = _complete_versions(cp, project_dir, entity, "modeling")
        lkd_v = _complete_versions(cp, project_dir, entity, "lookdev")
        if mod_v != [1] or lkd_v != [1]:
            _fail(f"apres 1er Publish : modeling={mod_v}, lookdev={lkd_v} (attendu [1], [1])")
        for s in states:
            if s.last_version != 1 or not s.last_result:
                _fail(f"state {s.step} : last_version={s.last_version}, "
                      f"last_result={s.last_result!r}")
        print("ok  1 Publish -> 2 publishes complete (modeling v1, lookdev v1) + last_result renseigne")

        # --- Skip d'un state desactive : lookdev off -> seul modeling monte en v2 ---
        states[1].enabled = False  # lookdev
        res = bpy.ops.ylos.publish_states('EXEC_DEFAULT')
        if res != {"FINISHED"}:
            _fail(f"2e ylos.publish_states a retourne {res} (attendu FINISHED)")
        mod_v = _complete_versions(cp, project_dir, entity, "modeling")
        lkd_v = _complete_versions(cp, project_dir, entity, "lookdev")
        if mod_v != [1, 2] or lkd_v != [1]:
            _fail(f"apres Publish avec lookdev off : modeling={mod_v}, lookdev={lkd_v} "
                  f"(attendu [1,2], [1])")
        print("ok  state desactive saute : modeling v2, lookdev reste v1")

        # --- Aucun state enabled -> CANCELLED, rien publie ---
        states[0].enabled = False
        res = bpy.ops.ylos.publish_states('EXEC_DEFAULT')
        if res != {"CANCELLED"}:
            _fail(f"publish_states sans state enabled a retourne {res} (attendu CANCELLED)")
        print("ok  aucun state enabled -> CANCELLED")

        try:
            addon.unregister()
        except Exception as e:
            _fail("addon.unregister() a leve", e)
        print("ok  addon.unregister() sans exception")

        print("\nPASS: State Manager (add/remove/reorder + Publish batch + skip) headless OK")
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
