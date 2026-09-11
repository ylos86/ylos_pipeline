# -*- coding: utf-8 -*-
"""
launch_context.py - Versioned launcher: open Blender in a Ylos pipeline context.

Invoked by ylos_ui.py ("Open in Blender" button) and usable from the CLI:

    blender --python tools/blender/launch_context.py -- \
        --project <root> [--entity <name>] [--step <step>] [--path <file>] \
        [--kind wip|publish|scene_default|create]

`--kind create` is the 'New Scene' verb (plan-usable-v1 Phase 1.4): no file is opened,
the launcher sets the pipeline context then hands over to the existing operator
`ylos.create_scene`, which reads create_project.scene_starter_spec() and allocates the
WIP version itself. It is the ONLY kind that takes no --path.

PIPELINE RULE (see CLAUDE.md): EVERY DCC launch goes through this versioned launcher.
No more inline `--python-expr`. Diagnosis CC#1d: in GUI, an op launched via
`--python-expr` runs during boot (context not ready) and fails silently ->
the user gets an empty instance. This launcher defers execution to the first
tick of a timer (context ready) and logs each step.

- GUI (bpy.app.background == False): ops NEVER run at parse time.
  bpy.app.timers.register(callback, first_interval=0.2) -> execution at the 1st tick.
- Background (--background): immediate execution + sys.exit(code) (for CI/tests).

Imposed opening order:
  * .blend -> wm.open_mainfile FIRST (the open replaces the scene), then context.
  * USD    -> context FIRST (open_context + enums), then wm.usd_import (merge).

Observability: each step is logged to ~/.ylos/launch.log (timestamp, argv,
success/failure + full traceback) AND printed. No exception leaves the timer.
The log path is overridable via $YLOS_LAUNCH_LOG (test isolation).
"""
import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import bpy

# --- Repo location (CLAUDE.md pattern: realpath + parent walk-up) ---------------------
_THIS = os.path.realpath(__file__)
REPO_ROOT = os.path.normpath(os.path.join(_THIS, "..", "..", ".."))  # tools/blender/.. -> repo
for _p in (REPO_ROOT, os.path.join(REPO_ROOT, "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Overridable log ($YLOS_LAUNCH_LOG): tests isolate their own file.
LOG_PATH = os.environ.get("YLOS_LAUNCH_LOG") or str(Path.home() / ".ylos" / "launch.log")

# Extensions opened by USD import (the rest = .blend mainfile). Mirror of ylos_ui.py.
USD_OPEN_EXTS = (".usd", ".usda", ".usdc", ".usdz", ".usdnc")
# 'web' pipeline target (see create_project.PROD_TYPE_TO_TARGET): publish = .glb, imported via
# import_scene.gltf (same merge-into-the-scene as USD, never an open_mainfile).
GLB_OPEN_EXTS = (".glb", ".gltf")


# --------------------------------------------------------------------------------------
# Observability
# --------------------------------------------------------------------------------------

def _log(msg, exc=False):
    """Timestamped append to LOG_PATH AND print. Never raises (write failure swallowed)."""
    line = f"[{datetime.now(timezone.utc).isoformat()}] {msg}"
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            if exc:
                fh.write(traceback.format_exc() + "\n")
    except OSError:
        pass
    if exc:
        traceback.print_exc()


def _set_enum_safe(obj, prop, value, items):
    """Assign an enum value WITHOUT ever crashing: a value absent from the items (legacy/
    unknown value read from a manifest or invalid name prefix) -> logged warning +
    fallback (current value kept), never an exception. Mirror of the
    op_open_context._set_enum_safe pattern, launcher-side (no operator -> log instead of report)."""
    valid = {v for v, _label, _desc in items}
    if value in valid:
        try:
            setattr(obj, prop, value)
            _log(f"context: {prop} = {value!r}")
            return True
        except Exception:
            _log(f"context: setattr {prop}={value!r} failed", exc=True)
            return False
    _log(f"context: {prop} = {value!r} ignored (outside enum {sorted(valid)}) - value kept")
    return False


# --------------------------------------------------------------------------------------
# Addon + resolution
# --------------------------------------------------------------------------------------

def _addon_registered():
    """True if the ylos.open_context op is registered. `"op" in dir(bpy.ops.ylos)` is the
    only reliable check in BOTH states: `hasattr(bpy.types, "YLOS_OT_OpenContext")` always
    returns False (operators are not exposed that way) and `hasattr(bpy.ops.ylos, "op")`
    always True (lazy stub) - both lead to a wrong decision (re-register in
    GUI or never register in CI)."""
    return "open_context" in dir(bpy.ops.ylos)


def _ensure_addon():
    """The ylos.open_context op and the scene properties only exist if the addon is
    registered. In GUI the user enabled it (do not re-register: double register ->
    RuntimeError); in background (CI, --factory-startup) we register it ourselves. Non-
    fatal: a failure degrades the context, never the file opening."""
    if _addon_registered():
        return True
    try:
        import blender as addon  # package plugins/blender imported as 'blender'
        addon.register()
        ok = _addon_registered()
        _log(f"addon: register() {'OK' if ok else 'without ylos.open_context'}")
        return ok
    except Exception:
        _log("addon: register() failed - pipeline context unavailable", exc=True)
        return False


def _apply_session_env(project):
    """PER-SESSION $PROJ_ROOT/$PROJ_CACHE (plan-usable-v1 Phase 0.2, tension #1 of
    CLAUDE.md) — applied to THIS process only (os.environ of the Blender instance),
    never an export to the user's shell.

    ylos_ui.py already hands the child a prepared env (_launch_env); doing it again here
    covers the CLI launch too and, above all, makes the value OBSERVABLE in the log — the
    acceptance test of Phase 0.2 is 'two Blenders on two projects have distinct
    PROJ_ROOT, checked at the first tick'.

    PROJ_ROOT = the PARENT of the project folder ($PROJ_ROOT/<project> == project, the
    contract of create_project._expand_proj_root / ylos_houdini.env_relative): a session
    on project B must never inherit the shell's value pointing at project A's disk.
    PROJ_CACHE is left untouched when already set (regenerable tier, shared on purpose);
    absent, create_project.resolve_cache() keeps its documented fallback."""
    try:
        root = str(Path(project).expanduser().resolve().parent)
    except OSError:
        _log(f"env: cannot resolve --project {project!r} - PROJ_ROOT left unchanged", exc=True)
        return
    previous = os.environ.get("PROJ_ROOT")
    os.environ["PROJ_ROOT"] = root
    if previous and previous != root:
        _log(f"env: PROJ_ROOT overridden for this session {previous!r} -> {root!r}")
    else:
        _log(f"env: PROJ_ROOT = {root!r}")
    _log(f"env: PROJ_CACHE = {os.environ.get('PROJ_CACHE')!r}")


def _entity_type(project, entity):
    """Family of the entity (asset|set|shot) read from the manifest, tolerantly. None if
    unreadable -> the context_type stays unchanged (set kept)."""
    try:
        import create_project as cp
        _edir, mpath = cp._find_asset_entity(Path(project), entity)
        manifest = json.loads(Path(mpath).read_text(encoding="utf-8"))
        return manifest.get("entity_type", "asset")
    except Exception:
        return None


def _resolve_path(args):
    """File to open: explicit --path, otherwise resolution via the orchestrator (single
    logic, reusable by Houdini). resolve_open_target NEVER RAISES -> dict exists=False."""
    if args.path:
        return args.path
    if not args.entity:
        _log("resolve: neither --path nor --entity - nothing to resolve")
        return None
    import create_project as cp
    target = cp.resolve_open_target(args.entity, "blender", args.step, project_root=args.project)
    if target.get("exists"):
        _log(f"resolve: {args.entity!r} (step={args.step!r}) -> [{target['kind']}] {target['path']}")
        return target["path"]
    _log(f"resolve: no target for {args.entity!r}: {target.get('reason', '')}")
    return None


def _apply_context(project, entity, step):
    """Loads the project (prod_type, scene preset) via the addon's op, then sets the
    entity context on the current scene. Each enum assignment is guarded."""
    _ensure_addon()
    # 1. load the project - never fatal (the file opening takes priority).
    try:
        bpy.ops.ylos.open_context('EXEC_DEFAULT', directory=str(project))
        _log(f"context: open_context(directory={str(project)!r}) OK")
    except Exception:
        _log("context: open_context failed (non-fatal)", exc=True)

    if not entity:
        return
    scene = bpy.context.scene
    from blender.core import vocab  # after _ensure_addon: plugins on sys.path

    # ylos_current_asset: StringProperty (never an enum).
    try:
        scene.ylos_current_asset = entity
        _log(f"context: ylos_current_asset = {entity!r}")
    except Exception:
        _log(f"context: set ylos_current_asset={entity!r} failed", exc=True)

    if step:
        _set_enum_safe(scene, "ylos_current_step", step, vocab.STEP_ITEMS_ALL)

    etype = _entity_type(project, entity)
    if etype:
        _set_enum_safe(scene, "ylos_context_type", etype.upper(), vocab.CONTEXT_TYPE_ITEMS)

    # Asset type = the name's TYPE_ prefix (TYPE_Name_Variant convention) if valid.
    prefix = entity.split("_", 1)[0]
    _set_enum_safe(scene, "ylos_asset_type", prefix, vocab.ASSET_TYPE_ITEMS)


# --------------------------------------------------------------------------------------
# Opening (callback)
# --------------------------------------------------------------------------------------

def _do_create_scene(args):
    """`--kind create` — 'New Scene': build a fresh, department-contextualized authoring
    scene instead of opening an existing file. Context FIRST (_apply_context: open_context
    + the scene enums the operator reads back), then the EXISTING operator
    `ylos.create_scene` in 'EXEC_DEFAULT' — which skips its GUI confirm dialog and runs the
    orchestrator spec (create_project.scene_starter_spec) itself: the launcher never
    allocates a WIP version nor re-derives a department rule. Returns an exit code.

    --entity is required (the operator reads scene.ylos_current_asset); --step is
    optional but a starter without one lands on the scene's default step.
    """
    if not args.entity:
        _log("LAUNCH FAILURE: --kind create requires --entity")
        return 1
    _apply_context(args.project, args.entity, args.step)
    if "create_scene" not in dir(bpy.ops.ylos):
        _log("LAUNCH FAILURE: operator ylos.create_scene unavailable (addon not registered?)")
        return 1
    try:
        result = bpy.ops.ylos.create_scene('EXEC_DEFAULT')
    except Exception:
        _log("LAUNCH FAILURE: ylos.create_scene raised", exc=True)
        return 1
    if "FINISHED" not in result:
        _log(f"LAUNCH FAILURE: ylos.create_scene returned {result!r}")
        return 1
    created = bpy.data.filepath or "<unsaved>"
    _log(f"create: ylos.create_scene OK -> {created}")
    _log(f"LAUNCH SUCCESS: [create] {created} objects={len(bpy.data.objects)}")
    return 0


def _do_launch(args):
    """Opens the resolved file in the imposed order, contextualizes, logs. Returns an
    exit code (0 success, 1 failure) for background mode. Never raises."""
    # Per-session env FIRST: every resolution below (orchestrator, USD layers written by
    # the session) must see this project's roots, not the shell's globals.
    _apply_session_env(args.project)

    if args.kind == "create":
        return _do_create_scene(args)

    path = _resolve_path(args)
    if not path:
        _log("LAUNCH FAILURE: no file to open (neither --path nor resolution)")
        return 1

    ext = os.path.splitext(path)[1].lower()
    is_usd = ext in USD_OPEN_EXTS
    is_glb = ext in GLB_OPEN_EXTS
    try:
        if is_usd or is_glb:
            # USD/GLB: context FIRST (open_context + enums), then import (merge into the
            # scene) - same order for both, only the import operator differs.
            _apply_context(args.project, args.entity, args.step)
            if is_usd:
                bpy.ops.wm.usd_import(filepath=path)
                _log(f"open: usd_import({path!r}) OK")
            else:
                bpy.ops.import_scene.gltf(filepath=path)
                _log(f"open: import_scene.gltf({path!r}) OK")
        else:
            # .blend: open_mainfile FIRST (replaces the scene), then context.
            bpy.ops.wm.open_mainfile(filepath=path)
            _log(f"open: open_mainfile({path!r}) OK")
            _apply_context(args.project, args.entity, args.step)
    except Exception:
        _log(f"LAUNCH FAILURE: opening {path!r} failed", exc=True)
        return 1

    n_objects = len(bpy.data.objects)
    mode = "usd_import" if is_usd else ("gltf_import" if is_glb else "mainfile")
    _log(f"LAUNCH SUCCESS: [{mode}] {path} objects={n_objects}")
    return 0


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------

def _parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser(prog="launch_context.py",
                                description="Launch Blender in a Ylos pipeline context.")
    p.add_argument("--project", required=True, help="Ylos project root.")
    p.add_argument("--entity", help="Entity name (asset/set/shot).")
    p.add_argument("--step", help="Targeted step (default: 1st step declared in the manifest).")
    p.add_argument("--path", help="File to open; absent -> resolve_open_target.")
    p.add_argument("--kind", choices=("wip", "publish", "scene_default", "create"),
                   help="Nature of the launch: 'create' BUILDS a new starter scene "
                        "(ylos.create_scene, no --path); the others are a hint about the "
                        "opened file's nature (metadata, logged).")
    return p.parse_args(argv)


def main():
    try:
        args = _parse_args()
    except SystemExit:
        _log("LAUNCH FAILURE: invalid arguments (see argv above)")
        raise
    _log(f"launch argv={sys.argv!r} kind={args.kind!r}")

    if bpy.app.background:
        # Background: the context is ready, immediate execution + exit code.
        sys.exit(_do_launch(args))
    else:
        # GUI: NEVER execute at parse time (context not ready during boot).
        # Defer to the 1st tick of a timer; no exception must leave it.
        def _tick():
            try:
                _do_launch(args)
            except Exception:
                _log("LAUNCH FAILURE: uncaught exception in the timer", exc=True)
            return None  # None -> do not re-arm the timer
        bpy.app.timers.register(_tick, first_interval=0.2)
        _log("GUI: deferred launch via bpy.app.timers (first_interval=0.2)")


if __name__ == "__main__":
    main()
