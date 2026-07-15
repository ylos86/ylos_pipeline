# -*- coding: utf-8 -*-
"""Regression : apply_scene_preset ne doit JAMAIS lever sur un prod_type EEVEE (AR/VR)
sous Blender 5.x - BLENDER_EEVEE_NEXT y est retire (cf. CLAUDE.md, bugs empiriques
Blender #1). Le renderer doit retomber sur un moteur affectable, jamais crasher l'operateur
op_new_project / op_open_context qui appellent apply_scene_preset.

Usage :
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

    # AR et VR portent BLENDER_EEVEE_NEXT dans SCENE_PRESETS (valide 4.2-4.4, absent 5.x).
    for prod_type in ("AR", "VR"):
        try:
            proj.apply_scene_preset(scene, prod_type)
        except TypeError as e:
            _fail(f"apply_scene_preset({prod_type!r}) a leve TypeError : {e}")
        engine = scene.render.engine
        if engine == "BLENDER_EEVEE_NEXT" and bpy.app.version[0] >= 5:
            _fail(f"{prod_type}: BLENDER_EEVEE_NEXT retenu sous Blender {bpy.app.version_string}")
        print(f"ok  apply_scene_preset({prod_type!r}) -> engine={engine} (sans TypeError)")

    # FILM = CYCLES, doit rester CYCLES (moteur non-EEVEE affecte tel quel).
    proj.apply_scene_preset(scene, "FILM")
    if scene.render.engine != "CYCLES":
        _fail(f"FILM: engine attendu CYCLES, obtenu {scene.render.engine}")
    print("ok  apply_scene_preset('FILM') -> engine=CYCLES (preserve)")

    # prod_type inconnu = no-op propre (pas d'exception, engine inchange).
    before = scene.render.engine
    proj.apply_scene_preset(scene, "ZZ_UNKNOWN")
    if scene.render.engine != before:
        _fail("prod_type inconnu a modifie l'engine (devrait etre no-op)")
    print("ok  apply_scene_preset('ZZ_UNKNOWN') -> no-op propre")

    print("PASS: apply_scene_preset renderer probe (Blender 5.x compat) OK")


if __name__ == "__main__":
    main()
