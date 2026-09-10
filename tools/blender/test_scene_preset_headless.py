# -*- coding: utf-8 -*-
"""Regression: apply_scene_preset must NEVER raise on an EEVEE prod_type (AR/VR)
under Blender 5.x - BLENDER_EEVEE_NEXT is removed there (see CLAUDE.md, empirical
Blender bugs #1). The renderer must fall back to an assignable engine, never crash the
op_new_project / op_open_context operator that call apply_scene_preset.

Usage:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --python tools/blender/test_scene_preset_headless.py
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import bpy


def _fail(msg):
    print(f"FAIL: {msg}")
    sys.exit(1)


def main():
    from plugins.blender.core import project as proj

    scene = bpy.context.scene

    # AR and VR carry BLENDER_EEVEE_NEXT in SCENE_PRESETS (valid 4.2-4.4, absent in 5.x).
    for prod_type in ("AR", "VR"):
        try:
            proj.apply_scene_preset(scene, prod_type)
        except TypeError as e:
            _fail(f"apply_scene_preset({prod_type!r}) raised TypeError: {e}")
        engine = scene.render.engine
        if engine == "BLENDER_EEVEE_NEXT" and bpy.app.version[0] >= 5:
            _fail(f"{prod_type}: BLENDER_EEVEE_NEXT kept under Blender {bpy.app.version_string}")
        print(f"ok  apply_scene_preset({prod_type!r}) -> engine={engine} (without TypeError)")

    # FILM = CYCLES, must stay CYCLES (a non-EEVEE engine is assigned as-is).
    proj.apply_scene_preset(scene, "FILM")
    if scene.render.engine != "CYCLES":
        _fail(f"FILM: expected engine CYCLES, got {scene.render.engine}")
    print("ok  apply_scene_preset('FILM') -> engine=CYCLES (preserved)")

    # Unknown prod_type = clean no-op (no exception, engine unchanged).
    before = scene.render.engine
    proj.apply_scene_preset(scene, "ZZ_UNKNOWN")
    if scene.render.engine != before:
        _fail("unknown prod_type modified the engine (should be a no-op)")
    print("ok  apply_scene_preset('ZZ_UNKNOWN') -> clean no-op")

    print("PASS: apply_scene_preset renderer probe (Blender 5.x compat) OK")


if __name__ == "__main__":
    main()
