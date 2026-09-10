# -*- coding: utf-8 -*-
"""Headless Blender test: the publish thumbnail renders on THIS Blender (regression of the
BLENDER_EEVEE_NEXT-removed-in-5.x bug - see CLAUDE.md, empirical Blender bugs #1).

Run:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  "$BLENDER" --background --python tools/blender/test_thumbnail_headless.py

Exit code != 0 on failure (assert / exception), 0 if everything passes.
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
    from blender.core import thumbnails  # package plugins/blender imported as 'blender'

    # 1. _pick_render_engine returns an ASSIGNABLE identifier on this Blender.
    scene = bpy.context.scene
    engine = thumbnails._pick_render_engine(scene)
    if not engine:
        _fail("_pick_render_engine returned an empty value")
    try:
        scene.render.engine = engine  # must be assignable (no TypeError)
    except TypeError as e:
        _fail(f"chosen engine {engine!r} not assignable", e)
    if engine == "BLENDER_EEVEE_NEXT":
        _fail("BLENDER_EEVEE_NEXT chosen: it is supposed to be removed in Blender 5.x")
    print(f"ok  _pick_render_engine -> {engine} (assignable)")

    # 2. Minimal scene + cube -> render_publish_thumbnail produces a non-empty thumb.png.
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 0))
    cube = bpy.context.active_object
    if cube is None:
        _fail("could not create the test cube")

    tmpdir = tempfile.mkdtemp(prefix="ylos_thumb_")
    result = thumbnails.render_publish_thumbnail([cube], tmpdir)
    if not result:
        _fail(f"render_publish_thumbnail failed - LAST_ERROR={thumbnails.LAST_ERROR!r}")

    thumb = os.path.join(tmpdir, "thumb.png")
    if not os.path.isfile(thumb):
        _fail(f"thumb.png missing at {thumb}")
    size = os.path.getsize(thumb)
    if size <= 0:
        _fail(f"thumb.png empty ({size} bytes)")
    print(f"ok  render_publish_thumbnail -> {thumb} ({size} bytes)")

    # --- Helper: pixel standard deviation. A "flat" thumbnail (solid background, subject
    # absent or black on black) has a near-zero standard deviation. It's the ONLY measure
    # that tells a successful render apart from one that raised no error but shows nothing:
    # the three historical bugs (lighting, clipping, hide_render) all produced a valid,
    # non-empty PNG file. Checking "the file exists" would never have caught them.
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
        _fail(f"cube thumbnail FLAT (std={std:.4f}): render with no visible content")
    print(f"ok  cube not flat (std={std:.4f})")

    # 3. clipping REGRESSION (real bug): a fresh camera has clip_end=1000 hard-coded. A
    #    subject several hundred units across is framed at a distance GREATER than that far
    #    plane -> the image contained only the world background, with no error raised.
    cube.scale = (250.0, 250.0, 250.0)
    bpy.context.view_layer.update()
    big_dir = tempfile.mkdtemp(prefix="ylos_thumb_big_")
    big = thumbnails.render_publish_thumbnail([cube], big_dir)
    if not big:
        _fail(f"render of a 500-unit subject failed - LAST_ERROR={thumbnails.LAST_ERROR!r}")
    big_std = _std(big)
    if big_std < FLAT:
        _fail(f"500-unit subject FLAT (std={big_std:.4f}): camera clipping regression")
    print(f"ok  500-unit subject not flat (std={big_std:.4f}) - clipping derived from framing")
    cube.scale = (1.0, 1.0, 1.0)
    bpy.context.view_layer.update()

    # 4. hide_render REGRESSION (real bug): the source meshes of a scattering are
    #    hide_render=True. Included in the bbox, they pushed the camera back onto empty space.
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(400, 400, 0))
    far = bpy.context.active_object
    far.hide_render = True
    framed = thumbnails.renderable_objects([cube, far])
    if far in framed:
        _fail("renderable_objects() keeps a hide_render=True object")
    if cube not in framed:
        _fail("renderable_objects() lost the visible object")
    print("ok  renderable_objects() excludes hide_render objects")

    mixed = tempfile.mkdtemp(prefix="ylos_thumb_mixed_")
    res_mixed = thumbnails.render_publish_thumbnail([cube, far], mixed)
    if not res_mixed:
        _fail(f"mixed render failed - LAST_ERROR={thumbnails.LAST_ERROR!r}")
    mixed_std = _std(res_mixed)
    if mixed_std < FLAT:
        _fail(f"mixed render FLAT (std={mixed_std:.4f}): hide_render still pollutes the framing")
    print(f"ok  mixed render not flat (std={mixed_std:.4f})")

    # 5. INTEGRITY (real gap found in production): a publish containing only an EMPTY
    #    (zero geometry) was marked 'complete' - _missing_artifacts only checks that
    #    "the file exists and is not empty". The thumbnail must FAIL so that the
    #    two-phase contract rejects this publish instead of silently committing it.
    bpy.ops.object.empty_add(location=(0, 0, 0))
    empty = bpy.context.active_object
    empty_dir = tempfile.mkdtemp(prefix="ylos_thumb_empty_")
    res_empty = thumbnails.render_publish_thumbnail([empty], empty_dir)
    if res_empty:
        _fail("a publish with no geometry produced a thumbnail: "
              "the integrity guard does not trigger")
    if "no renderable geometry" not in thumbnails.LAST_ERROR:
        _fail(f"failure cause not reported to the caller: LAST_ERROR={thumbnails.LAST_ERROR!r}")
    print(f"ok  publish with no geometry refused ({thumbnails.LAST_ERROR!r})")

    # 6. No temporary datablock survives (strict try/finally).
    leftovers = ([s.name for s in bpy.data.scenes if s.name.startswith("YLOS_thumb")]
                 + [o.name for o in bpy.data.objects if o.name.startswith("YLOS_thumb")]
                 + [w.name for w in bpy.data.worlds if w.name.startswith("YLOS_thumb")]
                 + [l.name for l in bpy.data.lights if l.name.startswith("YLOS_thumb")])
    if leftovers:
        _fail(f"temporary datablocks not purged: {leftovers}")
    print("ok  no residual YLOS_thumb_* datablock")

    print("\nPASS: thumbnail publish headless OK")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _fail("unexpected exception", e)
