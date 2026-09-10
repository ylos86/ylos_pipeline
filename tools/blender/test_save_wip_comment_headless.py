# -*- coding: utf-8 -*-
"""Headless Blender test: ylos.save_wip (plugins/blender/operators/op_save_wip.py, INC-4)
automatic version-up + '<wip>.blend.json' sidecar {comment, user, date, blender_version}
written atomically next to the WIP. Two successive Save Version calls (the same
auto-increment mechanism as invoke() - get_latest_wip_version() + 1 - since invoke_props_dialog
is not testable in --background, see CLAUDE.md) must produce v001 then v002, each with
its own conforming and distinct sidecar (never overwritten by the next one).

Run:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --python tools/blender/test_save_wip_comment_headless.py

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
    import blender as addon
    from blender.core.asset import get_latest_wip_version, list_wip_versions

    work = tempfile.mkdtemp(prefix="ylos_save_wip_test_")
    try:
        root = os.path.join(work, "src")
        cache = os.path.join(work, "cache")
        os.makedirs(root)
        os.makedirs(cache)

        proj = cp.create("SaveWipTest", root=root, cache=cache, prod_type="FILM")
        project_dir = str(proj["source"])
        entity = "PROP_Foo_Default"
        cp.create_asset(project_dir, entity, entity_type="asset", asset_type="PROP")

        try:
            addon.register()
        except Exception as e:
            _fail("addon.register() raised", e)
        print("ok  addon.register() without exception")

        scene = bpy.context.scene
        scene.ylos_project_path  = project_dir
        scene.ylos_project_name  = "SaveWipTest"
        scene.ylos_current_asset = entity
        scene.ylos_current_step  = "modeling"
        scene.ylos_context_type  = "ASSET"

        def _save(comment):
            # EXEC_DEFAULT skips invoke() (invoke_props_dialog needs a window, absent in
            # --background) - here we reproduce exactly its version computation (same
            # function, get_latest_wip_version), so the auto-increment mechanism is
            # actually exercised, just not the dialog itself.
            latest = get_latest_wip_version(project_dir, entity, "modeling", "asset")
            next_ver = latest + 1
            res = bpy.ops.ylos.save_wip(
                'EXEC_DEFAULT', step="modeling", version=next_ver, comment=comment,
            )
            if res != {"FINISHED"}:
                _fail(f"ylos.save_wip returned {res} (expected FINISHED)")
            return next_ver

        v1 = _save("first pass, blocking only")
        if v1 != 1:
            _fail(f"first save: version {v1} != 1")
        print(f"ok  first Save Version -> v{v1:03d}")

        v2 = _save("second pass, lighting tweaks")
        if v2 != 2:
            _fail(f"second save: version {v2} != 2 (auto-increment broken?)")
        print(f"ok  second Save Version -> v{v2:03d} (auto-increment vNNN+1 confirmed)")

        latest_after = get_latest_wip_version(project_dir, entity, "modeling", "asset")
        if latest_after != 2:
            _fail(f"get_latest_wip_version after 2 saves = {latest_after} != 2")

        versions = list_wip_versions(project_dir, entity, "modeling", "asset")
        if len(versions) != 2:
            _fail(f"list_wip_versions: {len(versions)} entries != 2: {versions}")

        by_ver = {v["version"]: v for v in versions}
        if by_ver[1]["comment"] != "first pass, blocking only":
            _fail(f"v001 unexpected comment: {by_ver[1]!r}")
        if by_ver[2]["comment"] != "second pass, lighting tweaks":
            _fail(f"v002 unexpected comment (overwritten by the next save?): {by_ver[2]!r}")
        if not by_ver[1]["user"] or not by_ver[2]["user"]:
            _fail(f"empty 'user' field: v1={by_ver[1]!r} v2={by_ver[2]!r}")
        print("ok  list_wip_versions() : v001/v002 comments distinct and not overwritten")

        # Sidecar on disk, exact shape {comment, user, date, blender_version}.
        wip_dir = os.path.join(project_dir, "assets", entity, "modeling", "wip")
        sidecar_v2 = os.path.join(wip_dir, f"{entity}_modeling_v002.blend.json")
        if not os.path.isfile(sidecar_v2):
            _fail(f"sidecar missing: {sidecar_v2}")
        with open(sidecar_v2, encoding="utf-8") as fh:
            data = json.load(fh)
        for key in ("comment", "user", "date", "blender_version"):
            if key not in data:
                _fail(f"sidecar v002: key {key!r} missing: {data!r}")
        if data["comment"] != "second pass, lighting tweaks":
            _fail(f"sidecar v002 comment: {data['comment']!r}")
        if data["blender_version"] != bpy.app.version_string:
            _fail(f"sidecar v002 blender_version: {data['blender_version']!r} != "
                  f"{bpy.app.version_string!r}")
        print(f"ok  sidecar {os.path.basename(sidecar_v2)} conforming: {data}")

        # The note is consumed (emptied) after the save, not sticky for the next one.
        if scene.ylos_wip_comment != "":
            _fail(f"scene.ylos_wip_comment not empty after save: {scene.ylos_wip_comment!r}")
        print("ok  scene.ylos_wip_comment empty after save (not sticky)")

        try:
            addon.unregister()
        except Exception as e:
            _fail("addon.unregister() raised", e)
        print("ok  addon.unregister() without exception")

        print("\nPASS: save_wip version-up + comment sidecar headless OK")
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
