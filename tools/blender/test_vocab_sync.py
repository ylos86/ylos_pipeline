# -*- coding: utf-8 -*-
"""Test headless Blender : le vocabulaire de plugins/blender/core/vocab.py est bien
DERIVE de create_project.py (valeurs ET ordre), et l'addon s'active sans exception - y
compris en lisant un prod_type qui crashait avant (ex 'XR').

Lancer :
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --python tools/blender/test_vocab_sync.py

Exit code != 0 en cas d'echec (assert / exception), 0 si tout passe.
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
    from blender.core import vocab  # package plugins/blender importe comme 'blender'

    # 1. Chaque *_ITEMS == constante create_project (valeurs ET ordre).
    checks = [
        ("ASSET_TYPE_ITEMS", _values(vocab.ASSET_TYPE_ITEMS), list(cp.ASSET_TYPES)),
        ("SET_TYPE_ITEMS",   _values(vocab.SET_TYPE_ITEMS),   list(cp.SET_TYPES)),
        ("SHOT_TYPE_ITEMS",  _values(vocab.SHOT_TYPE_ITEMS),  list(cp.SHOT_TYPES)),
        ("PROD_TYPE_ITEMS",  _values(vocab.PROD_TYPE_ITEMS),  list(cp.PROD_TYPES)),
        ("STEP_ITEMS[ASSET]", _values(vocab.STEP_ITEMS["ASSET"]), list(cp.DEFAULT_ASSET_STEPS)),
        ("STEP_ITEMS[SET]",   _values(vocab.STEP_ITEMS["SET"]),   list(cp.DEFAULT_SET_STEPS)),
        ("STEP_ITEMS[SHOT]",  _values(vocab.STEP_ITEMS["SHOT"]),  list(cp.DEFAULT_SHOT_STEPS)),
    ]
    for name, got, expected in checks:
        if got != expected:
            _fail(f"{name}: {got!r} != create_project {expected!r} (valeur/ordre)")
        print(f"ok  {name} == {expected}")

    # context types DERIVES d'ENTITY_DIR (pas de constante redondante cote create_project).
    ctx_expected = [k.upper() for k in cp.ENTITY_DIR]
    if list(vocab.CONTEXT_TYPES) != ctx_expected:
        _fail(f"CONTEXT_TYPES {list(vocab.CONTEXT_TYPES)!r} != ENTITY_DIR {ctx_expected!r}")
    if _values(vocab.CONTEXT_TYPE_ITEMS) != ctx_expected:
        _fail(f"CONTEXT_TYPE_ITEMS values != {ctx_expected!r}")
    print(f"ok  CONTEXT_TYPES == {ctx_expected} (derives d'ENTITY_DIR)")

    # STEP_ITEMS_ALL == union ordonnee sans doublons des trois familles.
    union, seen = [], set()
    for lst in (cp.DEFAULT_ASSET_STEPS, cp.DEFAULT_SHOT_STEPS, cp.DEFAULT_SET_STEPS):
        for s in lst:
            if s not in seen:
                seen.add(s)
                union.append(s)
    if _values(vocab.STEP_ITEMS_ALL) != union:
        _fail(f"STEP_ITEMS_ALL {_values(vocab.STEP_ITEMS_ALL)!r} != union {union!r}")
    print(f"ok  STEP_ITEMS_ALL == {union}")

    # 2. L'addon s'active sans exception.
    import bpy
    import blender as addon
    try:
        addon.register()
    except Exception as e:
        _fail("addon.register() a leve", e)
    print("ok  addon.register() sans exception")

    # 3. La propriete Scene ylos_prod_type accepte desormais une valeur qui crashait avant
    #    (ex 'XR' d'un projet reel comme Pachamama) : preuve que l'enum est bien la source
    #    unifiee (PROD_TYPE_ITEMS), plus FILM/AR/VR en dur.
    scene = bpy.context.scene
    for val in ("XR", "SERIES", "GAME", "FILM", "AR", "VR"):
        try:
            scene.ylos_prod_type = val
        except Exception as e:
            _fail(f"scene.ylos_prod_type = {val!r} a leve (enum non unifie ?)", e)
        if scene.ylos_prod_type != val:
            _fail(f"scene.ylos_prod_type != {val!r} apres affectation")
    print("ok  scene.ylos_prod_type accepte XR/SERIES/GAME/FILM/AR/VR")

    # ylos_current_step accepte toute valeur de STEP_ITEMS_ALL (dont 'comp', 'layout').
    for val in union:
        try:
            scene.ylos_current_step = val
        except Exception as e:
            _fail(f"scene.ylos_current_step = {val!r} a leve", e)
    print("ok  scene.ylos_current_step accepte tous les steps de STEP_ITEMS_ALL")

    try:
        addon.unregister()
    except Exception as e:
        _fail("addon.unregister() a leve", e)
    print("ok  addon.unregister() sans exception")

    print("\nPASS: vocab sync + addon register/unregister OK")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _fail("exception inattendue", e)
