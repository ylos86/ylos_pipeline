# -*- coding: utf-8 -*-
"""Headless Blender test: register/unregister of the addon must be SILENT and idempotent,
including when the addon is live under TWO module identities at once.

Root cause this pins down (observed on every headless run before the fix, at Blender quit):

    RuntimeError: unregister_class(...): missing bl_rna attribute from '_RNAMeta'
    instance (may not be registered)
    Exception in module unregister(): .../scripts/addons/ylos_pipeline/__init__.py

The same file is reachable as 'ylos_pipeline' (addons symlink, enabled from userprefs) AND
as 'blender' / 'plugins.blender' (repo path, what these test scripts import). Each identity
builds its own class objects sharing the same bl_idname; registering the second made Blender
implicitly unregister the first, whose _classes tuple then held classes with no bl_rna. At
quit, Blender disabled the userpref addon and its unregister() raised.

What is asserted here:
  A. register() then unregister() on ONE identity: silent, and every class really gone.
  B. A SECOND identity registering while the first holds the classes: the first is released
     cleanly, and calling the first's unregister() afterwards (exactly what Blender does at
     quit) raises NOTHING.
  C. unregister() is idempotent - calling it twice raises nothing.
  D. register() is idempotent - calling it twice leaves ONE registration, and the following
     unregister() leaves no ylos operator behind.
  E. When Blender started with the userpref addon enabled (the normal battery case), the
     'ylos_pipeline' identity is released by our register() and its unregister() is silent.

Run:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --python tools/blender/test_addon_registration_headless.py

Exit code != 0 on failure (assert / exception), 0 if everything passes.
"""
import importlib
import os
import sys
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


def _ylos_ops():
    """Registered ylos.* operator names, or [] when the whole namespace is gone."""
    import bpy
    if not hasattr(bpy.ops, "ylos"):
        return []
    return sorted(o for o in dir(bpy.ops.ylos) if not o.startswith("_"))


def _call_silently(label, func):
    """Run func(); any exception is a test failure - these paths run at Blender QUIT, where
    an exception is exactly the bug we are fixing."""
    try:
        func()
    except Exception as e:                     # noqa: BLE001 - that IS the assertion
        _fail(f"{label} raised {type(e).__name__}: {e}", e)


def main():
    import bpy
    import blender as addon_repo          # identity #1: the repo path

    # --------------------------------------------------------------------------------
    # E (part 1). State on arrival: without --factory-startup the userpref addon
    # 'ylos_pipeline' is already enabled and holds the classes. That is the real-world
    # configuration, and the one that used to blow up at quit.
    # --------------------------------------------------------------------------------
    userpref = sys.modules.get("ylos_pipeline")
    if userpref is not None:
        held = list(getattr(userpref, "_registered_classes", []))
        if not held:
            _fail("'ylos_pipeline' is imported but holds no registered class - the test "
                  "cannot exercise the dual-identity path it exists for")
        print(f"ok  userpref identity 'ylos_pipeline' present, {len(held)} classes registered")
    else:
        print("note  no 'ylos_pipeline' userpref identity in this run "
              "(--factory-startup?): the twin-release assertions below still run on a "
              "second repo identity")

    # --------------------------------------------------------------------------------
    # A. register() on the repo identity. This RELEASES the userpref twin (that is the fix)
    #    and must leave the operators available.
    # --------------------------------------------------------------------------------
    _call_silently("addon.register() [repo identity]", addon_repo.register)
    ops_after_register = _ylos_ops()
    if "publish" not in ops_after_register or "set_step_status" not in ops_after_register:
        _fail(f"expected ylos operators missing after register(): {ops_after_register}")
    if not addon_repo._registered_classes:
        _fail("register() did not record its classes in _registered_classes")
    print(f"ok  register() [repo identity]: {len(ops_after_register)} ylos operators live")

    if userpref is not None:
        if getattr(userpref, "_registered_classes", None):
            _fail("the userpref twin still claims registered classes after our register() "
                  "- the twin release did not run")
        print("ok  twin release: 'ylos_pipeline' handed its registration over cleanly")

        # E (part 2). THE regression: Blender calls this at quit. It must not raise.
        _call_silently("userpref twin unregister() (what Blender does at quit)",
                       userpref.unregister)
        print("ok  userpref twin unregister() is silent (was: RuntimeError missing bl_rna)")

    # --------------------------------------------------------------------------------
    # B. A SECOND repo identity registers while the first holds the classes.
    #    'plugins.blender' and 'blender' are two distinct module objects of the same file.
    # --------------------------------------------------------------------------------
    addon_twin = importlib.import_module("plugins.blender")
    if addon_twin is addon_repo:
        _fail("'plugins.blender' resolved to the same module object as 'blender' - the "
              "dual-identity scenario cannot be exercised")
    _call_silently("twin addon.register() [plugins.blender]", addon_twin.register)
    if addon_repo._registered_classes:
        _fail("the first repo identity still claims registered classes after the twin "
              "registered - twin release failed between two repo identities")
    if "publish" not in _ylos_ops():
        _fail("ylos operators disappeared after the twin registered")
    print("ok  second identity 'plugins.blender' registered, first one released")

    # The released identity's unregister() - the exact quit-time call - must be silent.
    _call_silently("released identity unregister()", addon_repo.unregister)
    if "publish" not in _ylos_ops():
        _fail("the released identity's unregister() tore down the LIVE twin's operators")
    print("ok  released identity unregister() silent and harmless to the live twin")

    # --------------------------------------------------------------------------------
    # C. unregister() is idempotent.
    # --------------------------------------------------------------------------------
    _call_silently("twin unregister() #1", addon_twin.unregister)
    if _ylos_ops():
        _fail(f"ylos operators still registered after unregister(): {_ylos_ops()}")
    _call_silently("twin unregister() #2 (idempotence)", addon_twin.unregister)
    print("ok  unregister() is idempotent and leaves no ylos operator behind")

    # Scene properties must be gone too (they are installed/removed as a pair).
    if hasattr(bpy.types.Scene, "ylos_project_path"):
        _fail("Scene.ylos_project_path survived unregister()")
    print("ok  Scene properties removed with the classes")

    # --------------------------------------------------------------------------------
    # D. register() is idempotent: twice in a row, then a single unregister() cleans up.
    # --------------------------------------------------------------------------------
    _call_silently("register() #1", addon_twin.register)
    n_first = len(addon_twin._registered_classes)
    _call_silently("register() #2 (idempotence)", addon_twin.register)
    if len(addon_twin._registered_classes) != n_first:
        _fail(f"double register() stacked registrations: {n_first} -> "
              f"{len(addon_twin._registered_classes)}")
    _call_silently("unregister() after double register()", addon_twin.unregister)
    if _ylos_ops():
        _fail(f"ylos operators left after double register + unregister: {_ylos_ops()}")
    print("ok  register() is idempotent (no stacked registration)")

    print("\nPASS: addon register/unregister silent, idempotent and dual-identity safe")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _fail("unexpected exception", e)
