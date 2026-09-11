# -*- coding: utf-8 -*-
"""Headless Blender test: the addon's WIP listing is a THIN ADAPTER over the orchestrator.

core/asset.py used to carry its own wip/ scan (a '.blend'-only regex + its own sidecar
reader). Two consequences, both fixed here: the logic was duplicated (a third copy after
create_project and ylos_houdini), and a Houdini '.hip*' WIP of the same step was invisible
from Blender - making a step that someone else is actively working on look empty.

What is asserted:
  A. list_scenefiles() returns EVERY DCC's scenefiles of the step (create_project.
     SCENEFILE_EXTENSIONS), with the orchestrator's row shape (version/filename/path/dcc/
     comment/user/date/dcc_version) and its 'dcc' tag.
  B. list_wip_versions() returns only what Blender can actually OPEN (dcc == 'blender') -
     a Houdini WIP is visible through list_scenefiles but never offered as openable.
  C. get_latest_wip_version() agrees with create_project._latest_wip(..., 'blender') - the
     same scan the orchestrator would use to allocate the next version, so Save Version can
     never disagree with it. A Houdini WIP numbered higher does NOT bump it.
  D. The sidecar written by ylos.save_wip is merged (comment/user), and a scenefile with no
     sidecar still gets a display 'date' (mtime fallback).
  E. list_project_entities() goes through create_project.list_entities and SURFACES an
     orphan folder (no manifest) instead of silently listing it as a normal PROP.

Run:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --python tools/blender/test_scenefiles_headless.py

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
    from blender.core import asset as asset_core

    try:
        addon.register()
    except Exception as e:
        _fail("addon.register() raised", e)

    work = tempfile.mkdtemp(prefix="ylos_scenefiles_")
    try:
        root = os.path.join(work, "src")
        cache = os.path.join(work, "cache")
        os.makedirs(root)
        os.makedirs(cache)

        proj = cp.create("SceneFilesProj", root=root, cache=cache, prod_type="FILM")
        project_dir = str(proj["source"])
        entity = "PROP_Cube_Default"
        cp.create_asset(project_dir, entity, entity_type="asset", asset_type="PROP")
        manifest_path = os.path.join(project_dir, "assets", entity, "manifest.json")
        step = json.loads(open(manifest_path, encoding="utf-8").read())["steps"][0]

        scene = bpy.context.scene
        scene.ylos_project_path = project_dir
        scene.ylos_project_name = "SceneFilesProj"
        scene.ylos_prod_type = "FILM"
        scene.ylos_context_type = "ASSET"
        scene.ylos_asset_type = "PROP"
        scene.ylos_current_asset = entity
        scene.ylos_current_step = step

        # Empty step: both helpers must answer, never raise.
        if asset_core.list_scenefiles(project_dir, entity, step) != []:
            _fail("list_scenefiles on an empty step must be []")
        if asset_core.get_latest_wip_version(project_dir, entity, step, "asset") != 0:
            _fail("get_latest_wip_version on an empty step must be 0")
        # An entity that does not exist is a business case, not a crash.
        if asset_core.list_scenefiles(project_dir, "PROP_Ghost_Default", step) != []:
            _fail("list_scenefiles on an unknown entity must be [] (never raise)")
        print("ok  empty step / unknown entity answer without raising")

        # ---- Two real Blender WIPs through ylos.save_wip (sidecars included) ------------
        for version, comment in ((1, "blockout"), (2, "second pass")):
            scene.ylos_wip_comment = comment
            res = bpy.ops.ylos.save_wip('EXEC_DEFAULT', step=step, version=version,
                                        comment=comment)
            if res != {"FINISHED"}:
                _fail(f"ylos.save_wip v{version} returned {res}")

        wip_dir = os.path.join(project_dir, "assets", entity, step, "wip")

        # ---- A Houdini WIP of the SAME step, numbered HIGHER ----------------------------
        # ('.hipnc' = Apprentice extension, see CLAUDE.md - it must be recognised too.)
        hip_name = f"{entity}_{step}_v007.hipnc"
        with open(os.path.join(wip_dir, hip_name), "wb") as fh:
            fh.write(b"HIP-not-really")

        # ---- A. Every DCC listed, orchestrator row shape --------------------------------
        rows = asset_core.list_scenefiles(project_dir, entity, step, "asset")
        by_dcc = {}
        for r in rows:
            by_dcc.setdefault(r["dcc"], []).append(r)
        if sorted(by_dcc) != ["blender", "houdini"]:
            _fail(f"list_scenefiles must show every DCC, got {sorted(by_dcc)} ({rows!r})")
        if len(by_dcc["blender"]) != 2 or len(by_dcc["houdini"]) != 1:
            _fail(f"expected 2 blender + 1 houdini scenefiles, got "
                  f"{ {k: len(v) for k, v in by_dcc.items()} }")
        required = {"version", "filename", "path", "dcc", "comment", "user", "date",
                    "dcc_version", "blender_version"}
        missing = required - set(rows[0])
        if missing:
            _fail(f"orchestrator row shape not preserved, missing keys: {sorted(missing)}")
        # Same rows as the orchestrator itself: the adapter must add nothing but 'date'.
        direct = cp.list_scenefiles(project_dir, entity, step).get(step, [])
        if [r["filename"] for r in rows] != [r["filename"] for r in direct]:
            _fail("the adapter diverges from create_project.list_scenefiles")
        print(f"ok  list_scenefiles: {len(by_dcc['blender'])} blender + "
              f"{len(by_dcc['houdini'])} houdini, orchestrator row shape preserved")

        # ---- B. Only Blender WIPs are offered as openable -------------------------------
        openable = asset_core.list_wip_versions(project_dir, entity, step, "asset")
        if len(openable) != 2 or any(r["dcc"] != "blender" for r in openable):
            _fail(f"list_wip_versions must be blender-only, got "
                  f"{[(r['filename'], r['dcc']) for r in openable]}")
        if any(r["filename"] == hip_name for r in openable):
            _fail("the Houdini WIP is offered as openable in Blender")
        print("ok  list_wip_versions: Houdini WIP visible but NOT openable from Blender")

        # ---- C. Agreement with the orchestrator's allocation scan -----------------------
        latest = asset_core.get_latest_wip_version(project_dir, entity, step, "asset")
        _path, orch_latest = cp._latest_wip(
            os.path.join(project_dir, "assets", entity), step, "blender")
        if latest != orch_latest or latest != 2:
            _fail(f"get_latest_wip_version = {latest}, orchestrator = {orch_latest} "
                  f"(expected 2 - the v007 Houdini WIP must not bump the Blender numbering)")
        print(f"ok  get_latest_wip_version = {latest} = create_project._latest_wip(...,"
              f" 'blender') (v007 .hipnc ignored)")

        # ---- D. Sidecars merged; a sidecar-less file still gets a display date ----------
        comments = [r["comment"] for r in openable]
        if comments != ["blockout", "second pass"]:
            _fail(f"sidecar comments not merged in order: {comments}")
        if not all(r["user"] for r in openable):
            _fail(f"sidecar 'user' not merged: {[r['user'] for r in openable]}")
        hip_row = by_dcc["houdini"][0]
        if hip_row["comment"] or hip_row["user"]:
            _fail("a scenefile with no sidecar must have empty comment/user, not garbage")
        if not hip_row["date"]:
            _fail("a scenefile with no sidecar must still get a display date (mtime fallback)")
        print(f"ok  sidecars merged ({comments}); sidecar-less file falls back to mtime "
              f"date ({hip_row['date']})")

        # ---- E. Entity listing goes through the orchestrator and flags orphans ----------
        os.makedirs(os.path.join(project_dir, "assets", "lecube", "modeling", "wip"))
        asset_core.invalidate_entity_cache(project_dir)
        entities = asset_core.list_project_entities(project_dir, "asset")
        names = {e["name"] for e in entities}
        if names != {entity, "lecube"}:
            _fail(f"list_project_entities = {sorted(names)} (expected the asset + the orphan)")
        orphan = [e for e in entities if e["name"] == "lecube"][0]
        if not orphan.get("broken"):
            _fail("an orphan folder (no manifest.json) must be FLAGGED, not listed as a "
                  "normal entity - a ghost the tool hides is worse than a flagged one")
        real = [e for e in entities if e["name"] == entity][0]
        if real.get("broken") or real["type"] != "PROP" or not real.get("steps"):
            _fail(f"a healthy entity must carry its type and steps, got {real!r}")
        print(f"ok  list_project_entities via create_project.list_entities: orphan flagged "
              f"({orphan['broken'][:40]}...)")

        try:
            addon.unregister()
        except Exception as e:
            _fail("addon.unregister() raised", e)

        print("\nPASS: WIP / entity listing are thin adapters over the orchestrator, "
              "multi-DCC aware")
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
