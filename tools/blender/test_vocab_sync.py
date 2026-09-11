# -*- coding: utf-8 -*-
"""Headless Blender test: the vocabulary in plugins/blender/core/vocab.py is DERIVED
from create_project.py (values AND order), and the addon registers without exception -
including when reading a prod_type that used to crash (e.g. 'XR').

Run:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --python tools/blender/test_vocab_sync.py

Exit code != 0 on failure (assert / exception), 0 if everything passes.
"""
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


def _values(items):
    return [v for v, _label, _desc in items]


def main():
    import create_project as cp
    from blender.core import vocab  # package plugins/blender imported as 'blender'

    # 1. Each *_ITEMS == create_project constant (values AND order).
    checks = [
        ("ASSET_TYPE_ITEMS", _values(vocab.ASSET_TYPE_ITEMS), list(cp.ASSET_TYPES)),
        ("SET_TYPE_ITEMS",   _values(vocab.SET_TYPE_ITEMS),   list(cp.SET_TYPES)),
        ("SHOT_TYPE_ITEMS",  _values(vocab.SHOT_TYPE_ITEMS),  list(cp.SHOT_TYPES)),
        ("PROD_TYPE_ITEMS",  _values(vocab.PROD_TYPE_ITEMS),  list(cp.PROD_TYPES)),
        ("STEP_ITEMS[ASSET]", _values(vocab.STEP_ITEMS["ASSET"]), list(cp.DEFAULT_ASSET_STEPS)),
        ("STEP_ITEMS[SET]",   _values(vocab.STEP_ITEMS["SET"]),   list(cp.DEFAULT_SET_STEPS)),
        ("STEP_ITEMS[SHOT]",  _values(vocab.STEP_ITEMS["SHOT"]),  list(cp.DEFAULT_SHOT_STEPS)),
        # Schema 2.2 step status: the SETTABLE domain only - the 'auto' sentinel plus the
        # explicit statuses. empty/wip/published are derived by get_step_status and must
        # never appear in a settable enum.
        ("STEP_STATUS_ITEMS", _values(vocab.STEP_STATUS_ITEMS),
         [cp.STEP_STATUS_AUTO] + list(cp.STEP_STATUS_EXPLICIT)),
    ]
    for name, got, expected in checks:
        if got != expected:
            _fail(f"{name}: {got!r} != create_project {expected!r} (value/order)")
        print(f"ok  {name} == {expected}")

    # context types DERIVED from ENTITY_DIR (no redundant constant on the create_project side).
    ctx_expected = [k.upper() for k in cp.ENTITY_DIR]
    if list(vocab.CONTEXT_TYPES) != ctx_expected:
        _fail(f"CONTEXT_TYPES {list(vocab.CONTEXT_TYPES)!r} != ENTITY_DIR {ctx_expected!r}")
    if _values(vocab.CONTEXT_TYPE_ITEMS) != ctx_expected:
        _fail(f"CONTEXT_TYPE_ITEMS values != {ctx_expected!r}")
    print(f"ok  CONTEXT_TYPES == {ctx_expected} (derived from ENTITY_DIR)")

    # STEP_ITEMS_ALL == ordered, de-duplicated union of the three families.
    union, seen = [], set()
    for lst in (cp.DEFAULT_ASSET_STEPS, cp.DEFAULT_SHOT_STEPS, cp.DEFAULT_SET_STEPS):
        for s in lst:
            if s not in seen:
                seen.add(s)
                union.append(s)
    if _values(vocab.STEP_ITEMS_ALL) != union:
        _fail(f"STEP_ITEMS_ALL {_values(vocab.STEP_ITEMS_ALL)!r} != union {union!r}")
    print(f"ok  STEP_ITEMS_ALL == {union}")

    # 2. The addon registers without exception.
    import bpy
    import blender as addon
    try:
        addon.register()
    except Exception as e:
        _fail("addon.register() raised", e)
    print("ok  addon.register() without exception")

    # 3. The Scene property ylos_prod_type now accepts a value that used to crash
    #    (e.g. 'XR' from a real project like Pachamama): proof that the enum is indeed the
    #    unified source (PROD_TYPE_ITEMS), no longer FILM/AR/VR hard-coded.
    scene = bpy.context.scene
    for val in ("XR", "SERIES", "GAME", "FILM", "AR", "VR"):
        try:
            scene.ylos_prod_type = val
        except Exception as e:
            _fail(f"scene.ylos_prod_type = {val!r} raised (enum not unified?)", e)
        if scene.ylos_prod_type != val:
            _fail(f"scene.ylos_prod_type != {val!r} after assignment")
    print("ok  scene.ylos_prod_type accepts XR/SERIES/GAME/FILM/AR/VR")

    # ylos_current_step accepts any value from STEP_ITEMS_ALL (including 'comp', 'layout').
    for val in union:
        try:
            scene.ylos_current_step = val
        except Exception as e:
            _fail(f"scene.ylos_current_step = {val!r} raised", e)
    print("ok  scene.ylos_current_step accepts every step in STEP_ITEMS_ALL")

    try:
        addon.unregister()
    except Exception as e:
        _fail("addon.unregister() raised", e)
    print("ok  addon.unregister() without exception")

    print("\nPASS: vocab sync + addon register/unregister OK")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _fail("unexpected exception", e)
