# -*- coding: utf-8 -*-
"""Headless Blender test: ylos.new_asset (plugins/blender/operators/op_new_asset.py) creates
entities with the REAL steps from vocab.STEP_ITEMS[ctx] (i.e. create_project.DEFAULT_*_STEPS)
after the INC-2 purge - no more BoolVectorProperty with a hard-coded size, replaced by a
CollectionProperty (YLOS_PG_StepToggle) rebuilt from vocab. Also checks execute()'s safety
net: a scripted call via 'EXEC_DEFAULT' (which skips invoke(), so it never populates
steps_to_create through the normal dialog path - a real case for an n8n automation agent,
see CLAUDE.md) must still create ALL the steps, never a 0-step entity silently.

Run:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --python tools/blender/test_new_asset_steps_headless.py

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


def main():
    import bpy
    import create_project as cp
    import blender as addon  # package plugins/blender imported as 'blender'
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
            _fail("addon.register() raised", e)
        print("ok  addon.register() without exception (PropertyGroup before CollectionProperty)")

        scene = bpy.context.scene
        scene.ylos_project_path = project_dir
        scene.ylos_project_name = "StepsTest"

        cases = [
            ("ASSET", "PROP_Foo_Default", vocab.values(vocab.STEP_ITEMS["ASSET"])),
            ("SHOT",  "LAYOUT_Bar_Default", vocab.values(vocab.STEP_ITEMS["SHOT"])),
            ("SET",   "EXTERIOR_Baz_Default", vocab.values(vocab.STEP_ITEMS["SET"])),
        ]

        for ctx_type, full_name, expected_steps in cases:
            base_name = full_name.split("_")[1]  # e.g. 'Foo' from 'PROP_Foo_Default'
            kwargs = {"context_type": ctx_type, "entity_name": base_name}
            if ctx_type == "ASSET":
                kwargs["asset_type"] = "PROP"
            elif ctx_type == "SHOT":
                kwargs["shot_type"] = "LAYOUT"
            else:
                kwargs["set_type"] = "EXTERIOR"

            # EXEC_DEFAULT skips invoke() (so the normal population of steps_to_create) -
            # exercises precisely execute()'s safety net.
            result = bpy.ops.ylos.new_asset('EXEC_DEFAULT', **kwargs)
            if result != {"FINISHED"}:
                _fail(f"{ctx_type}: ylos.new_asset returned {result} (expected FINISHED)")

            folder_map = {"ASSET": "assets", "SHOT": "shots", "SET": "sets"}
            manifest_path = os.path.join(
                project_dir, folder_map[ctx_type], full_name, "manifest.json"
            )
            if not os.path.isfile(manifest_path):
                _fail(f"{ctx_type}: manifest not found at {manifest_path}")

            with open(manifest_path, encoding="utf-8") as fh:
                manifest = json.load(fh)

            got_steps = manifest.get("steps", [])
            if got_steps != expected_steps:
                _fail(
                    f"{ctx_type}: created steps {got_steps!r} != vocab.STEP_ITEMS[{ctx_type!r}] "
                    f"{expected_steps!r} (drift or broken safety net)"
                )
            print(f"ok  {ctx_type}: EXEC_DEFAULT (without invoke) -> steps {got_steps} "
                  f"(== vocab, all enabled by default)")

        try:
            addon.unregister()
        except Exception as e:
            _fail("addon.unregister() raised", e)
        print("ok  addon.unregister() without exception")

        print("\nPASS: ylos.new_asset steps (BoolVectorProperty purge) headless OK")
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
