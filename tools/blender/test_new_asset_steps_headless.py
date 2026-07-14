# -*- coding: utf-8 -*-
"""Test headless Blender : ylos.new_asset (plugins/blender/operators/op_new_asset.py) cree
les entites avec les steps REELS de vocab.STEP_ITEMS[ctx] (donc create_project.DEFAULT_*_STEPS)
apres la purge INC-2 - plus de BoolVectorProperty a taille codee en dur, remplace par une
CollectionProperty (YLOS_PG_StepToggle) reconstruite depuis vocab. Verifie aussi le filet de
securite d'execute() : un appel scripte via 'EXEC_DEFAULT' (qui saute invoke(), donc ne peuple
jamais steps_to_create par le chemin dialog normal - cas reel pour un agent d'automatisation
n8n, cf. CLAUDE.md) doit quand meme creer TOUS les steps, jamais une entite a 0 step en silence.

Lancer :
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --python tools/blender/test_new_asset_steps_headless.py

Exit code != 0 en cas d'echec (assert / exception), 0 si tout passe.
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


def main():
    import bpy
    import create_project as cp
    import blender as addon  # package plugins/blender importe comme 'blender'
    from blender.core import vocab

    work = tempfile.mkdtemp(prefix="ylos_new_asset_test_")
    try:
        root = os.path.join(work, "src")
        cache = os.path.join(work, "cache")
        os.makedirs(root)
        os.makedirs(cache)

        proj = cp.create("StepsTest", root=root, cache=cache, prod_type="FILM")
        project_dir = str(proj["source"])

        try:
            addon.register()
        except Exception as e:
            _fail("addon.register() a leve", e)
        print("ok  addon.register() sans exception (PropertyGroup avant CollectionProperty)")

        scene = bpy.context.scene
        scene.ylos_project_path = project_dir
        scene.ylos_project_name = "StepsTest"

        cases = [
            ("ASSET", "PROP_Foo_Default", vocab.values(vocab.STEP_ITEMS["ASSET"])),
            ("SHOT",  "LAYOUT_Bar_Default", vocab.values(vocab.STEP_ITEMS["SHOT"])),
            ("SET",   "EXTERIOR_Baz_Default", vocab.values(vocab.STEP_ITEMS["SET"])),
        ]

        for ctx_type, full_name, expected_steps in cases:
            base_name = full_name.split("_")[1]  # ex 'Foo' depuis 'PROP_Foo_Default'
            kwargs = {"context_type": ctx_type, "entity_name": base_name}
            if ctx_type == "ASSET":
                kwargs["asset_type"] = "PROP"
            elif ctx_type == "SHOT":
                kwargs["shot_type"] = "LAYOUT"
            else:
                kwargs["set_type"] = "EXTERIOR"

            # EXEC_DEFAULT saute invoke() (donc le peuplement normal de steps_to_create) -
            # exerce precisement le filet de securite d'execute().
            result = bpy.ops.ylos.new_asset('EXEC_DEFAULT', **kwargs)
            if result != {"FINISHED"}:
                _fail(f"{ctx_type}: ylos.new_asset a retourne {result} (attendu FINISHED)")

            folder_map = {"ASSET": "assets", "SHOT": "shots", "SET": "sets"}
            manifest_path = os.path.join(
                project_dir, folder_map[ctx_type], full_name, "manifest.json"
            )
            if not os.path.isfile(manifest_path):
                _fail(f"{ctx_type}: manifest introuvable a {manifest_path}")

            with open(manifest_path, encoding="utf-8") as fh:
                manifest = json.load(fh)

            got_steps = manifest.get("steps", [])
            if got_steps != expected_steps:
                _fail(
                    f"{ctx_type}: steps crees {got_steps!r} != vocab.STEP_ITEMS[{ctx_type!r}] "
                    f"{expected_steps!r} (drift ou filet de securite casse)"
                )
            print(f"ok  {ctx_type}: EXEC_DEFAULT (sans invoke) -> steps {got_steps} "
                  f"(== vocab, tous actives par defaut)")

        try:
            addon.unregister()
        except Exception as e:
            _fail("addon.unregister() a leve", e)
        print("ok  addon.unregister() sans exception")

        print("\nPASS: ylos.new_asset steps (purge BoolVectorProperty) headless OK")
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
