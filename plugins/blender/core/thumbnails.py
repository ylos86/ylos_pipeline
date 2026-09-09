# -*- coding: utf-8 -*-
# Viewport thumbnail generation on WIP save + preview loading for version picker.
# + headless publish thumbnail (render_publish_thumbnail, see bottom of file).

import math
import os
import bpy
from bpy.utils import previews
from mathutils import Euler, Vector
from pathlib import Path

_pcoll = None


def get_thumb_path(blend_path: str) -> str:
    """Return the expected thumbnail PNG path for a given .blend path."""
    p = Path(blend_path)
    return str(p.parent / (p.stem + "_thumb.png"))


def thumb_exists(blend_path: str) -> bool:
    return os.path.isfile(get_thumb_path(blend_path))


def generate_thumbnail(blend_path: str, context) -> str:
    """
    Render a 512×512 viewport thumbnail and save it as PNG next to the .blend.
    Returns the thumbnail path on success, empty string on failure.
    """
    thumb_path = get_thumb_path(blend_path)
    scene = context.scene

    orig = {
        "filepath":    scene.render.filepath,
        "res_x":       scene.render.resolution_x,
        "res_y":       scene.render.resolution_y,
        "res_pct":     scene.render.resolution_percentage,
        "file_format": scene.render.image_settings.file_format,
    }

    try:
        scene.render.filepath                   = thumb_path
        scene.render.resolution_x               = 512
        scene.render.resolution_y               = 512
        scene.render.resolution_percentage      = 100
        scene.render.image_settings.file_format = "PNG"

        bpy.ops.render.opengl(write_still=True, view_context=True)

    except Exception as e:
        print(f"[Ylos] Thumbnail generation failed: {e}")
        return ""

    finally:
        scene.render.filepath                   = orig["filepath"]
        scene.render.resolution_x               = orig["res_x"]
        scene.render.resolution_y               = orig["res_y"]
        scene.render.resolution_percentage      = orig["res_pct"]
        scene.render.image_settings.file_format = orig["file_format"]

    return thumb_path if os.path.isfile(thumb_path) else ""


def init_previews():
    global _pcoll
    if _pcoll is None:
        _pcoll = previews.new()


def clear_previews():
    global _pcoll
    if _pcoll is not None:
        previews.remove(_pcoll)
        _pcoll = None


def load_thumb_icon(blend_path: str) -> int:
    global _pcoll
    if _pcoll is None:
        init_previews()

    thumb_path = get_thumb_path(blend_path)
    if not os.path.isfile(thumb_path):
        return 0

    key = thumb_path
    if key not in _pcoll:
        try:
            _pcoll.load(key, thumb_path, "IMAGE")
        except Exception as e:
            print(f"[Ylos] Preview load failed for {thumb_path}: {e}")
            return 0

    return _pcoll[key].icon_id


def load_icon(abs_path: str) -> int:
    """Generic preview icon for an arbitrary absolute path (e.g. a publish thumb.png,
    which does not follow the '<stem>_thumb.png' convention of get_thumb_path/load_thumb_icon).
    Reuses the same _pcoll collection. Returns 0 if the file does not exist (never
    an exception - same convention as load_thumb_icon)."""
    global _pcoll
    if _pcoll is None:
        init_previews()

    if not abs_path or not os.path.isfile(abs_path):
        return 0

    key = abs_path
    if key not in _pcoll:
        try:
            _pcoll.load(key, abs_path, "IMAGE")
        except Exception as e:
            print(f"[Ylos] Preview load failed for {abs_path}: {e}")
            return 0

    preview = _pcoll[key]
    # Blender loads previews LAZILY: until something reads their pixels, a
    # template_icon(icon_value=...) shows the loading indicator instead of the image
    # (observed live in the N-panel). Touching image_size forces decoding right
    # away. We pay here, outside draw() (this module is called from the caches), never in
    # the redraw loop.
    try:
        _ = preview.image_size[:]   # full-size image (template_icon)
        _ = preview.icon_size[:]    # reduced icon (icon_value of an operator button)
    except Exception:
        pass
    return preview.icon_id


def reload_icon(abs_path: str) -> int:
    """Force reload of a preview for an arbitrary ABSOLUTE path.

    Needed because _pcoll memoizes by path: a thumb.png rewritten AT THE SAME PATH (which
    every republish of a step does) would keep showing the old image as long as the
    Blender session lives. Mirror of reload_thumb_icon for the '<stem>_thumb.png' convention."""
    global _pcoll
    if _pcoll is not None and abs_path in _pcoll:
        del _pcoll[abs_path]
    return load_icon(abs_path)


def reload_thumb_icon(blend_path: str) -> int:
    global _pcoll
    if _pcoll is None:
        return 0

    thumb_path = get_thumb_path(blend_path)
    key = thumb_path

    if key in _pcoll:
        del _pcoll[key]

    return load_thumb_icon(blend_path)


# ---------------------------------------------------------------------------
# Headless publish thumbnail (allocate/finalize contract - see create_project.py).
# Separate from generate_thumbnail() above (that one stays the viewport render for the
# WIP preview - different use, window context available). Here: never
# bpy.ops.render.opengl (requires a window context, breaks the headless/hython-like usage) -
# real EEVEE render on temporary scene/camera.
# ---------------------------------------------------------------------------

_BBOX_TYPES = {"MESH", "ARMATURE", "CURVE", "SURFACE", "META", "FONT", "VOLUME"}

# Last failure cause of render_publish_thumbnail() (text of the underlying exception),
# set module-level so the caller (op_publish) surfaces the CAUSE to the user without
# changing the "" return convention nor the public signature. "" = no failure recorded.
LAST_ERROR = ""

# Render engine candidates, newest to most universal. BLENDER_EEVEE_NEXT exists
# only in Blender 4.2-4.4 (removed in 5.x); BLENDER_EEVEE covers 5.x (and the historical EEVEE);
# BLENDER_WORKBENCH is the always-available net. The engine is a DYNAMIC enum: we probe it
# by assignment (try/except TypeError), never by a hard-coded list nor RNA
# introspection (see CLAUDE.md, Blender empirical bugs #1).
_ENGINE_CANDIDATES = ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH")


def _pick_render_engine(scene) -> str:
    """Return the first engine of _ENGINE_CANDIDATES assignable on THIS Blender, setting it
    on scene.render.engine (assignment is the reliable probe: the engines enum is
    dynamic, an absent identifier raises TypeError). If no candidate passes (very
    unlikely), leaves scene.render.engine unchanged and returns its current value."""
    for engine in _ENGINE_CANDIDATES:
        try:
            scene.render.engine = engine
            return engine
        except TypeError:
            continue
    return scene.render.engine


def renderable_objects(objects):
    """Subset of 'objects' that will ACTUALLY contribute to the thumbnail render.

    Real bug fixed: a layout set publishes its scattering source meshes with
    hide_render=True (they exist only as instancing geometry). They still entered
    _world_bbox() -> inflated bbox, camera pulled back onto emptiness, zero subject
    pixels. Framing must be about what the camera WILL SEE, not what the caller
    collected.

    Falls back to the full list if the filter empties everything (an imperfect framing beats
    no thumbnail: the two-phase contract REJECTS a publish without thumb.png)."""
    visible = [o for o in objects if not getattr(o, "hide_render", False)]
    return visible or list(objects)


def has_visible_geometry(objects) -> bool:
    """True if at least one object carries REAL geometry (and not just a transform).

    Serves as a publish integrity check, not a framing detail. Real gap found in
    production conditions: a GLB publish of CHARACTER_Sissa02_Default contained only an
    EMPTY — zero mesh — yet was marked 'complete'. The two-phase contract guard
    (_missing_artifacts) only checks "the file exists and is not empty":
    a .glb containing only an empty weighs a few kb, so it passes. The only signal was a
    flat thumbnail... which the old code produced anyway, hence unusable.

    By making the absence of geometry FATAL for the thumbnail, we make it fatal for the
    publish (thumbnail required everywhere -> finalize_publish_version refuses the commit and
    preserves the staging). The thumbnail stops being decorative: it becomes the publish smoke test.

    An EMPTY, a CAMERA, a LIGHT or a 0-vertex mesh do not count. An ARMATURE counts
    (an anim/rig publish is legitimate) as soon as it has bones."""
    for obj in objects:
        t = obj.type
        data = getattr(obj, "data", None)
        if t == "MESH":
            if data is not None and len(data.vertices) > 0:
                return True
        elif t == "ARMATURE":
            if data is not None and len(getattr(data, "bones", ())) > 0:
                return True
        elif t in _BBOX_TYPES:
            return True
    return False


def _world_bbox(objects):
    """Unified world bbox (min, max) of objects with real geometry. Falls back to all
    objects if none qualifies (e.g. only EMPTYs). Returns (None, None) if no usable
    corner - the caller decides, never inf/nan propagated to framing."""
    candidates = [o for o in objects if o.type in _BBOX_TYPES] or list(objects)
    mins = Vector((float("inf"),) * 3)
    maxs = Vector((float("-inf"),) * 3)
    seen = False
    for obj in candidates:
        for corner in obj.bound_box:
            world_co = obj.matrix_world @ Vector(corner)
            if any(math.isnan(c) or math.isinf(c) for c in world_co):
                continue
            mins = Vector(min(a, b) for a, b in zip(mins, world_co))
            maxs = Vector(max(a, b) for a, b in zip(maxs, world_co))
            seen = True
    if not seen:
        return None, None
    return mins, maxs


def _frame_camera(cam_obj, cam_data, mins, maxs,
                  azimuth_deg=45.0, elevation_deg=30.0, padding=1.4):
    """Three-quarter framing: places cam_obj to enclose (mins, maxs) with a margin, AND
    matches the clipping planes to the computed distance.

    Real bug fixed (2nd cause of flat thumbnails, independent of lighting): a new
    camera (bpy.data.cameras.new) has clip_end = 1000 HARD-CODED. The framing distance is
    radius * padding / sin(fov/2) - for a ~690-unit entity (a layout set with its
    terrain) that gives ~1420: the ENTIRE subject falls behind the far plane and the image
    contains only the world background, without a single error surfaced. Clipping must therefore be
    derived from the framing, never left at the default. Returns the camera-center distance."""
    center = (mins + maxs) / 2.0
    diagonal = (maxs - mins).length
    radius = max(diagonal / 2.0, 0.5)  # floor to avoid a degenerate framing (null bbox)
    fov = cam_data.angle if cam_data.angle else math.radians(50)
    distance = (radius * padding) / math.sin(fov / 2.0)

    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    offset = Vector((
        distance * math.cos(el) * math.sin(az),
        -distance * math.cos(el) * math.cos(az),
        distance * math.sin(el),
    ))
    cam_obj.location = center + offset
    direction = center - cam_obj.location
    cam_obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    # Clipping derived from framing: the subject occupies [distance - radius, distance + radius].
    # Wide margins to absorb an underestimated bbox (modifiers, instances) without ever
    # falling below the RNA limits (clip_start > 0).
    cam_data.clip_start = max(distance * 0.001, 1e-4)
    cam_data.clip_end = max((distance + radius * 4.0) * 1.5, 10.0)
    return distance


def _add_key_and_fill(tmp_scene, cam_obj, center):
    """Deterministic lighting: a camera-aligned key + a weaker side fill.

    Real bug fixed (1st cause of flat thumbnails): a flat world (use_nodes=False)
    lights a diffuse material UNIFORMLY regardless of the normal - without direct
    light, subject and background render at nearly identical luminance. The key (aligned on the
    camera axis, camera-mounted-flash style) guarantees that the visible faces are the lit
    faces; the fill (azimuth +75 deg, ~2.5x weaker) breaks the flat silhouette and gives
    volume - without it a cube seen head-on stays a uniform blob.

    Returns the list of created (object, data), to be purged by the caller."""
    created = []
    to_center = center - cam_obj.location
    key_dir = to_center.normalized() if to_center.length else Vector((0.0, 0.0, -1.0))

    key_data = bpy.data.lights.new("YLOS_thumb_key", type="SUN")
    key_data.energy = 4.0
    key_data.angle = math.radians(10.0)
    key_obj = bpy.data.objects.new("YLOS_thumb_key", key_data)
    tmp_scene.collection.objects.link(key_obj)
    key_obj.rotation_euler = key_dir.to_track_quat("-Z", "Y").to_euler()
    created.append((key_obj, key_data))

    # Fill: same direction rotated +75 deg around Z, elevation raised -> lights the
    # side left in shadow by the key, without ever going into full backlight.
    fill_dir = key_dir.copy()
    fill_dir.rotate(Euler((0.0, 0.0, math.radians(75.0)), "XYZ"))
    fill_dir.z = min(fill_dir.z + 0.25, -0.05)
    fill_data = bpy.data.lights.new("YLOS_thumb_fill", type="SUN")
    fill_data.energy = 1.6
    fill_data.angle = math.radians(25.0)
    fill_obj = bpy.data.objects.new("YLOS_thumb_fill", fill_data)
    tmp_scene.collection.objects.link(fill_obj)
    fill_obj.rotation_euler = fill_dir.normalized().to_track_quat("-Z", "Y").to_euler()
    created.append((fill_obj, fill_data))
    return created


def render_publish_thumbnail(objects: list, staging_dir: str, size: int = 512) -> str:
    """
    Render a headless thumbnail (512x512 EEVEE by default, three-quarter camera auto-framed on
    the bbox of 'objects', neutral background, key+fill) to staging_dir/thumb.png.

    Strict try/finally: temporary scene, camera (object + data), lights and temporary world
    are purged no matter what - never a residual datablock in the user's .blend.

    THREE real bugs fixed, cumulative (each was enough to produce a flat thumbnail):

    1. Targeting tmp_scene at render: bpy.ops.render.render() reads the scene to render via
       window.scene at the C level, NOT via bpy.context.scene - a bpy.context.temp_override(
       scene=tmp_scene) is SILENTLY ignored by this operator as soon as a window
       exists. Official pattern: real window.scene swap, restored right after. In
       pure --background (bpy.context.window is None) we fall back to temp_override, the only
       mechanism available and sufficient in that mode.
    2. Lighting: see _add_key_and_fill.
    3. Camera clipping: see _frame_camera (clip_end=1000 hard-coded on a new camera).

    Objects hidden from render (hide_render) excluded from framing AND from linking - see
    renderable_objects(). No visible geometry at all -> explicit FAILURE (see
    has_visible_geometry): the thumbnail is the publish smoke test, not a decoration.

    Color management PINNED (Standard, exposure 0, gamma 1): a new scene inherits the
    startup file's defaults - a user with an exotic view transform or a shifted
    exposure would get thumbnails inconsistent from one machine to another. A
    thumbnail is a pipeline datum, not an artistic render: it must be
    reproducible.

    Returns the thumb path on success, "" on failure (same convention as
    generate_thumbnail above) - the completeness guard already lives in
    finalize_publish_version() (_missing_artifacts): not duplicated here.
    """
    global LAST_ERROR
    LAST_ERROR = ""

    if not objects:
        LAST_ERROR = "no objects to frame"
        print("[Ylos] Publish thumbnail: no objects to frame")
        return ""

    framed = renderable_objects(objects)
    thumb_path = str(Path(staging_dir) / "thumb.png")

    tmp_scene = None
    tmp_world = None
    cam_obj = None
    cam_data = None
    lights = []
    engine = "?"

    try:
        tmp_scene = bpy.data.scenes.new("YLOS_thumb_tmp")
        # Engine probed by assignment (BLENDER_EEVEE_NEXT removed in Blender 5.x - see CLAUDE.md).
        engine = _pick_render_engine(tmp_scene)
        tmp_scene.render.resolution_x = size
        tmp_scene.render.resolution_y = size
        tmp_scene.render.resolution_percentage = 100
        tmp_scene.render.image_settings.file_format = "PNG"
        tmp_scene.render.filepath = thumb_path
        tmp_scene.render.film_transparent = False
        # Pinned color management - see docstring. try/except: view transform enums
        # depend on the OCIO config, 'Standard' exists everywhere but we don't bet on it.
        try:
            tmp_scene.view_settings.view_transform = "Standard"
            tmp_scene.view_settings.look = "None"
            tmp_scene.view_settings.exposure = 0.0
            tmp_scene.view_settings.gamma = 1.0
        except TypeError:
            pass
        # Low sampling: a 256px thumbnail does not need 64 samples (EEVEE only,
        # the attribute does not exist on all engines).
        try:
            tmp_scene.eevee.taa_render_samples = 16
        except AttributeError:
            pass

        tmp_world = bpy.data.worlds.new("YLOS_thumb_world")
        tmp_world.use_nodes = False
        tmp_world.color = (0.18, 0.18, 0.18)
        tmp_scene.world = tmp_world

        cam_data = bpy.data.cameras.new("YLOS_thumb_cam")
        cam_obj = bpy.data.objects.new("YLOS_thumb_cam", cam_data)
        tmp_scene.collection.objects.link(cam_obj)
        tmp_scene.camera = cam_obj

        for obj in framed:
            if obj.name not in tmp_scene.collection.objects:
                tmp_scene.collection.objects.link(obj)

        if not has_visible_geometry(framed):
            LAST_ERROR = ("no renderable geometry among the %d object(s) to frame "
                          "(a publish with no visible content would be committed silently)"
                          % len(framed))
            print("[Ylos] Publish thumbnail: " + LAST_ERROR)
            return ""

        mins, maxs = _world_bbox(framed)
        if mins is None:
            LAST_ERROR = "degenerate bounding box (no usable geometry)"
            print("[Ylos] Publish thumbnail: degenerate bbox")
            return ""
        _frame_camera(cam_obj, cam_data, mins, maxs)
        lights = _add_key_and_fill(tmp_scene, cam_obj, (mins + maxs) / 2.0)

        window = bpy.context.window
        if window is not None:
            # Interactive session: window.scene is what render.render() actually reads -
            # see docstring. Restore unconditionally, even if the render raises.
            orig_window_scene = window.scene
            try:
                window.scene = tmp_scene
                bpy.ops.render.render(write_still=True)
            finally:
                window.scene = orig_window_scene
        else:
            # Pure --background (no window): no window.scene to swap, the context
            # override is enough (see test_thumbnail_headless.py, --background).
            with bpy.context.temp_override(scene=tmp_scene):
                bpy.ops.render.render(write_still=True)

    except Exception as e:
        LAST_ERROR = str(e)
        print(f"[Ylos] Publish thumbnail render failed (engine={engine}): {e}")
        return ""

    finally:
        if tmp_scene is not None:
            for obj in framed:
                if obj.name in tmp_scene.collection.objects:
                    tmp_scene.collection.objects.unlink(obj)
            if cam_obj is not None and cam_obj.name in tmp_scene.collection.objects:
                tmp_scene.collection.objects.unlink(cam_obj)
            for lobj, _ldata in lights:
                if lobj.name in tmp_scene.collection.objects:
                    tmp_scene.collection.objects.unlink(lobj)
        if cam_obj is not None:
            bpy.data.objects.remove(cam_obj, do_unlink=True)
        if cam_data is not None:
            bpy.data.cameras.remove(cam_data)
        for lobj, ldata in lights:
            bpy.data.objects.remove(lobj, do_unlink=True)
            bpy.data.lights.remove(ldata)
        if tmp_world is not None:
            bpy.data.worlds.remove(tmp_world)
        if tmp_scene is not None:
            bpy.data.scenes.remove(tmp_scene)

    if os.path.isfile(thumb_path):
        print(f"[Ylos] Publish thumbnail OK (engine={engine}): {thumb_path}")
        return thumb_path
    LAST_ERROR = LAST_ERROR or f"render produced no file at {thumb_path} (engine={engine})"
    print(f"[Ylos] Publish thumbnail: no file produced (engine={engine})")
    return ""
