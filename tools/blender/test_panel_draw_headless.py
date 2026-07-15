# -*- coding: utf-8 -*-
"""Test headless Blender : les sections du N-panel unifie (plugins/blender/ui/panel.py -
Context/Scenefile/State Manager/Scene Check ; State Manager via ui/state_manager.py) executent
REELLEMENT leur draw() sans exception, sur un projet/entite fixture, dans les etats vide (rien
publie/sauve) ET peuple (WIP + publish reels). Blender '--background' n'a pas de fenetre -> pas de vrai bpy.types.UILayout
disponible : draw() est appele avec un layout FACTICE (duck-type, accepte tout appel/attribut)
qui n'attrape PAS les erreurs d'API Blender (mauvaise signature de widget) mais attrape tout le
reste (cle de dict fautive, faute de frappe, exception d'un appel a create_project.py) - c'est
la seule verification headless possible pour ces methodes ; la fidelite visuelle reste manuelle
(cf. CLAUDE.md, meme limite que les autres popups/panels de l'addon).

Lancer :
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --python tools/blender/test_panel_draw_headless.py

Exit code != 0 en cas d'echec (assert / exception), 0 si tout passe.
"""
import os
import shutil
import sys
import tempfile
import traceback
import types

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


class _FakeOp:
    """bpy row.operator(...) renvoie un proxy sur lequel on affecte des props (op.x = y)."""
    pass


class _FakeLayout:
    """Duck-type d'un bpy.types.UILayout : tout attribut/appel manquant renvoie soit un
    nouveau _FakeLayout (row/column/box/split...), soit un _FakeOp (operator()). Les
    affectations (layout.use_property_split = True) passent par le __dict__ normal."""

    def __getattr__(self, name):
        if name == "operator":
            return lambda *a, **k: _FakeOp()
        return lambda *a, **k: _FakeLayout()


def _draw(panel_cls, context):
    fake_self = types.SimpleNamespace(layout=_FakeLayout())
    panel_cls.draw(fake_self, context)


def main():
    import bpy
    import create_project as cp
    import blender as addon
    from blender.ui import panel
    from blender.core import thumbnails

    work = tempfile.mkdtemp(prefix="ylos_panel_draw_test_")
    try:
        root = os.path.join(work, "src")
        cache = os.path.join(work, "cache")
        os.makedirs(root)
        os.makedirs(cache)

        proj = cp.create("PanelDrawTest", root=root, cache=cache, prod_type="XR")
        project_dir = str(proj["source"])
        entity = "PROP_Cube_Default"
        cp.create_asset(project_dir, entity, entity_type="asset", asset_type="PROP")

        try:
            addon.register()
        except Exception as e:
            _fail("addon.register() a leve", e)

        scene = bpy.context.scene
        scene.ylos_project_path = project_dir
        scene.ylos_project_name = "PanelDrawTest"
        scene.ylos_prod_type = "XR"
        scene.ylos_context_type = "ASSET"
        scene.ylos_asset_type = "PROP"

        context = bpy.context

        # --- Etat 1 : pas de projet charge (bpy.context.scene fraiche, avant assignation) ---
        empty_scene_ns = types.SimpleNamespace(scene=bpy.data.scenes.new("YLOS_empty_ctx"))
        try:
            _draw(panel.YLOS_PT_Context, empty_scene_ns)
        except Exception as e:
            _fail("YLOS_PT_Context.draw() a leve (etat: pas de projet)", e)
        finally:
            bpy.data.scenes.remove(empty_scene_ns.scene)
        print("ok  YLOS_PT_Context.draw() : etat 'pas de projet' sans exception")

        # --- Etat 2 : projet charge, pas d'asset actif ---
        try:
            _draw(panel.YLOS_PT_Context, context)
        except Exception as e:
            _fail("YLOS_PT_Context.draw() a leve (etat: projet sans asset actif)", e)
        print("ok  YLOS_PT_Context.draw() : etat 'projet, pas d'asset actif' sans exception")

        scene.ylos_current_asset = entity
        scene.ylos_current_step = "modeling"

        # --- Etat 3 : asset actif, rien sauve/publie (branches 'none yet' / 'no publish') ---
        try:
            _draw(panel.YLOS_PT_Context, context)
            _draw(panel.YLOS_PT_Scenefile, context)
            _draw(panel.YLOS_PT_StateManager, context)  # subsume Publish + Imports
            _draw(panel.YLOS_PT_SceneCheck, context)
        except Exception as e:
            _fail("draw() a leve (etat: asset actif, rien sauve/publie)", e)
        print("ok  sections draw() : etat 'asset actif, rien sauve/publie' sans exception")

        # --- Fixture reelle : WIP + publish (branches peuplees) ---
        res = bpy.ops.ylos.save_wip('EXEC_DEFAULT', step="modeling", version=1)
        if res != {"FINISHED"}:
            _fail(f"ylos.save_wip a retourne {res} (attendu FINISHED)")

        bpy.ops.mesh.primitive_cube_add(size=2.0)
        res = bpy.ops.ylos.publish(
            'EXEC_DEFAULT', step="modeling", allow_full_scene=True, load_after=False,
        )
        if res != {"FINISHED"}:
            _fail(f"ylos.publish a retourne {res} (attendu FINISHED) - "
                  f"thumbnails.LAST_ERROR={thumbnails.LAST_ERROR!r}")

        # --- Etat 4 : asset actif, WIP + publish presents + un export state actif ---
        # Un export state peuple la branche 'reglages du state actif' du State Manager.
        st = scene.ylos_export_states.add()
        st.entity = entity
        st.step = "modeling"
        scene.ylos_export_states_index = 0
        try:
            _draw(panel.YLOS_PT_Context, context)
            _draw(panel.YLOS_PT_Scenefile, context)
            _draw(panel.YLOS_PT_StateManager, context)
            _draw(panel.YLOS_PT_SceneCheck, context)
        except Exception as e:
            _fail("draw() a leve (etat: WIP + publish presents)", e)
        print("ok  sections draw() : etat 'WIP + publish + export state' sans exception "
              f"(target=web -> .glb, LAST_ERROR={thumbnails.LAST_ERROR!r})")

        # --- Etat 5 : import taggue present + mise a jour disponible (INC-5) ---
        res = bpy.ops.ylos.import_product(
            'EXEC_DEFAULT', entity=entity, step="modeling", version=0,
        )
        if res != {"FINISHED"}:
            _fail(f"ylos.import_product a retourne {res} (attendu FINISHED)")

        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(3, 0, 0))
        res = bpy.ops.ylos.publish(
            'EXEC_DEFAULT', step="modeling", allow_full_scene=True, load_after=False,
        )
        if res != {"FINISHED"}:
            _fail(f"ylos.publish v2 a retourne {res} (attendu FINISHED)")

        res = bpy.ops.ylos.check_updates('EXEC_DEFAULT')
        if res != {"FINISHED"}:
            _fail(f"ylos.check_updates a retourne {res} (attendu FINISHED)")

        try:
            _draw(panel.YLOS_PT_StateManager, context)
        except Exception as e:
            _fail("YLOS_PT_StateManager.draw() a leve (branche import taggue + update dispo)", e)
        print("ok  YLOS_PT_StateManager.draw() : import taggue + update disponible sans exception")

        try:
            addon.unregister()
        except Exception as e:
            _fail("addon.unregister() a leve", e)
        print("ok  addon.unregister() sans exception")

        print("\nPASS: draw() des sections du panel Ylos (Context/Scenefile/State Manager/Scene Check) headless OK")
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
