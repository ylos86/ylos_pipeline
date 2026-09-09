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

    # --- Helper : ecart-type des pixels. Un thumbnail "plat" (fond uni, sujet absent ou
    # noir sur noir) a un ecart-type quasi nul. C'est la SEULE mesure qui distingue un rendu
    # reussi d'un rendu qui n'a leve aucune erreur mais ne montre rien : les trois bugs
    # historiques (eclairage, clipping, hide_render) produisaient tous un fichier PNG valide
    # et non vide. Verifier "le fichier existe" ne les aurait jamais attrapes.
    def _std(path):
        img = bpy.data.images.load(path)
        try:
            px = list(img.pixels)
            rgb = px[0::4] + px[1::4] + px[2::4]
            mean = sum(rgb) / len(rgb)
            return (sum((v - mean) ** 2 for v in rgb) / len(rgb)) ** 0.5
        finally:
            bpy.data.images.remove(img)

    FLAT = 0.02

    std = _std(thumb)
    if std < FLAT:
        _fail(f"thumbnail du cube PLAT (std={std:.4f}) : rendu sans contenu visible")
    print(f"ok  cube non plat (std={std:.4f})")

    # 3. REGRESSION clipping (bug reel) : une camera neuve a clip_end=1000 en dur. Un sujet
    #    de plusieurs centaines d'unites est cadre a une distance SUPERIEURE a ce far plane
    #    -> l'image ne contenait que le fond du world, sans la moindre erreur remontee.
    cube.scale = (250.0, 250.0, 250.0)
    bpy.context.view_layer.update()
    big_dir = tempfile.mkdtemp(prefix="ylos_thumb_big_")
    big = thumbnails.render_publish_thumbnail([cube], big_dir)
    if not big:
        _fail(f"rendu d'un sujet de 500 unites echoue - LAST_ERROR={thumbnails.LAST_ERROR!r}")
    big_std = _std(big)
    if big_std < FLAT:
        _fail(f"sujet de 500 unites PLAT (std={big_std:.4f}) : regression du clipping camera")
    print(f"ok  sujet 500 unites non plat (std={big_std:.4f}) - clipping derive du cadrage")
    cube.scale = (1.0, 1.0, 1.0)
    bpy.context.view_layer.update()

    # 4. REGRESSION hide_render (bug reel) : les meshes sources d'un scattering sont
    #    hide_render=True. Incluses dans la bbox, elles reculaient la camera sur du vide.
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(400, 400, 0))
    far = bpy.context.active_object
    far.hide_render = True
    framed = thumbnails.renderable_objects([cube, far])
    if far in framed:
        _fail("renderable_objects() garde un objet hide_render=True")
    if cube not in framed:
        _fail("renderable_objects() a perdu l'objet visible")
    print("ok  renderable_objects() exclut les objets hide_render")

    mixed = tempfile.mkdtemp(prefix="ylos_thumb_mixed_")
    res_mixed = thumbnails.render_publish_thumbnail([cube, far], mixed)
    if not res_mixed:
        _fail(f"rendu mixte echoue - LAST_ERROR={thumbnails.LAST_ERROR!r}")
    mixed_std = _std(res_mixed)
    if mixed_std < FLAT:
        _fail(f"rendu mixte PLAT (std={mixed_std:.4f}) : le hide_render pollue encore le cadrage")
    print(f"ok  rendu mixte non plat (std={mixed_std:.4f})")

    # 5. INTEGRITE (trou reel trouve en production) : un publish ne contenant qu'un EMPTY
    #    (zero geometrie) etait marque 'complete' - _missing_artifacts ne verifie que
    #    "le fichier existe et n'est pas vide". Le thumbnail doit ECHOUER pour que le
    #    contrat deux-phases rejette ce publish au lieu de le commiter en silence.
    bpy.ops.object.empty_add(location=(0, 0, 0))
    empty = bpy.context.active_object
    empty_dir = tempfile.mkdtemp(prefix="ylos_thumb_empty_")
    res_empty = thumbnails.render_publish_thumbnail([empty], empty_dir)
    if res_empty:
        _fail("un publish sans aucune geometrie a produit un thumbnail : "
              "le garde-fou d'integrite ne se declenche pas")
    if "no renderable geometry" not in thumbnails.LAST_ERROR:
        _fail(f"cause d'echec non remontee a l'appelant : LAST_ERROR={thumbnails.LAST_ERROR!r}")
    print(f"ok  publish sans geometrie refuse ({thumbnails.LAST_ERROR!r})")

    # 6. Aucun datablock temporaire ne survit (try/finally strict).
    leftovers = ([s.name for s in bpy.data.scenes if s.name.startswith("YLOS_thumb")]
                 + [o.name for o in bpy.data.objects if o.name.startswith("YLOS_thumb")]
                 + [w.name for w in bpy.data.worlds if w.name.startswith("YLOS_thumb")]
                 + [l.name for l in bpy.data.lights if l.name.startswith("YLOS_thumb")])
    if leftovers:
        _fail(f"datablocks temporaires non purges : {leftovers}")
    print("ok  aucun datablock YLOS_thumb_* residuel")

    print("\nPASS: thumbnail publish headless OK")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _fail("exception inattendue", e)
