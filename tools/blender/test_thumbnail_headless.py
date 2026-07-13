# -*- coding: utf-8 -*-
"""Test headless Blender : le thumbnail de publish se rend sur CE Blender (regression du bug
BLENDER_EEVEE_NEXT retire en 5.x - cf. CLAUDE.md, bugs empiriques Blender #1).

Lancer :
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --python tools/blender/test_thumbnail_headless.py

Exit code != 0 en cas d'echec (assert / exception), 0 si tout passe.
"""
import os
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
    from blender.core import thumbnails  # package plugins/blender importe comme 'blender'

    # 1. _pick_render_engine retourne un identifiant AFFECTABLE sur ce Blender.
    scene = bpy.context.scene
    engine = thumbnails._pick_render_engine(scene)
    if not engine:
        _fail("_pick_render_engine a retourne une valeur vide")
    try:
        scene.render.engine = engine  # doit etre affectable (pas de TypeError)
    except TypeError as e:
        _fail(f"moteur retenu {engine!r} non affectable", e)
    if engine == "BLENDER_EEVEE_NEXT":
        _fail("BLENDER_EEVEE_NEXT retenu : il est cense etre retire en Blender 5.x")
    print(f"ok  _pick_render_engine -> {engine} (affectable)")

    # 2. Scene minimale + cube -> render_publish_thumbnail produit un thumb.png non vide.
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 0))
    cube = bpy.context.active_object
    if cube is None:
        _fail("impossible de creer le cube de test")

    tmpdir = tempfile.mkdtemp(prefix="ylos_thumb_")
    result = thumbnails.render_publish_thumbnail([cube], tmpdir)
    if not result:
        _fail(f"render_publish_thumbnail a echoue - LAST_ERROR={thumbnails.LAST_ERROR!r}")

    thumb = os.path.join(tmpdir, "thumb.png")
    if not os.path.isfile(thumb):
        _fail(f"thumb.png absent a {thumb}")
    size = os.path.getsize(thumb)
    if size <= 0:
        _fail(f"thumb.png vide ({size} octets)")
    print(f"ok  render_publish_thumbnail -> {thumb} ({size} octets)")

    print("\nPASS: thumbnail publish headless OK")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _fail("exception inattendue", e)
