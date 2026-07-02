# -*- coding: utf-8 -*-
# Viewport thumbnail generation on WIP save + preview loading for version picker.
# + headless publish thumbnail (render_publish_thumbnail, cf. bas de fichier).

import math
import os
import bpy
from bpy.utils import previews
from mathutils import Vector
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
# Headless publish thumbnail (allocate/finalize contract - cf. create_project.py).
# Separe de generate_thumbnail() ci-dessus (celle-ci reste le rendu viewport pour la
# preview WIP - usage different, contexte fenetre disponible). Ici : jamais
# bpy.ops.render.opengl (exige un contexte fenetre, casse le headless/hython-like usage) -
# rendu EEVEE reel sur scene/camera temporaires.
# ---------------------------------------------------------------------------

_BBOX_TYPES = {"MESH", "ARMATURE", "CURVE"}


def _world_bbox(objects):
    """Bbox monde (min, max) unifiee des objets avec geometrie reelle (MESH/ARMATURE/CURVE).
    Retombe sur tous les objets si aucun ne qualifie (ex: que des EMPTY)."""
    candidates = [o for o in objects if o.type in _BBOX_TYPES] or list(objects)
    mins = Vector((float("inf"),) * 3)
    maxs = Vector((float("-inf"),) * 3)
    for obj in candidates:
        for corner in obj.bound_box:
            world_co = obj.matrix_world @ Vector(corner)
            mins = Vector(min(a, b) for a, b in zip(mins, world_co))
            maxs = Vector(max(a, b) for a, b in zip(maxs, world_co))
    return mins, maxs


def _frame_camera(cam_obj, cam_data, mins, maxs,
                  azimuth_deg=45.0, elevation_deg=30.0, padding=1.4):
    """Cadrage trois-quarts : place cam_obj pour englober (mins, maxs) avec une marge."""
    center = (mins + maxs) / 2.0
    diagonal = (maxs - mins).length
    radius = max(diagonal / 2.0, 0.5)  # plancher pour eviter un cadrage degenere (bbox nulle)
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


def render_publish_thumbnail(objects: list, staging_dir: str, size: int = 256) -> str:
    """
    Rend un thumbnail headless (256x256 EEVEE par defaut, camera trois-quarts auto-cadree
    sur la bbox de 'objects', fond neutre) dans staging_dir/thumb.png.

    try/finally strict : scene temporaire, camera (objet + data) et world temporaire sont
    purges quoi qu'il arrive - jamais de datablock residuel dans le .blend utilisateur, et
    bpy.context.scene n'est jamais modifie (bpy.context.temp_override(scene=...) le temps
    du rendu, pas de bascule de la scene active).

    Retourne le chemin du thumb en succes, "" en echec (meme convention que
    generate_thumbnail ci-dessus) - le garde-fou de completude vit deja dans
    finalize_publish_version() (_missing_artifacts) : pas duplique ici.
    """
    if not objects:
        print("[Ylos] Publish thumbnail: no objects to frame")
        return ""

    thumb_path = str(Path(staging_dir) / "thumb.png")

    tmp_scene = None
    tmp_world = None
    cam_obj = None
    cam_data = None

    try:
        tmp_scene = bpy.data.scenes.new("YLOS_thumb_tmp")
        tmp_scene.render.engine = "BLENDER_EEVEE_NEXT"
        tmp_scene.render.resolution_x = size
        tmp_scene.render.resolution_y = size
        tmp_scene.render.resolution_percentage = 100
        tmp_scene.render.image_settings.file_format = "PNG"
        tmp_scene.render.filepath = thumb_path
        tmp_scene.render.film_transparent = False

        tmp_world = bpy.data.worlds.new("YLOS_thumb_world")
        tmp_world.use_nodes = False
        tmp_world.color = (0.18, 0.18, 0.18)
        tmp_scene.world = tmp_world

        cam_data = bpy.data.cameras.new("YLOS_thumb_cam")
        cam_obj = bpy.data.objects.new("YLOS_thumb_cam", cam_data)
        tmp_scene.collection.objects.link(cam_obj)
        tmp_scene.camera = cam_obj

        for obj in objects:
            if obj.name not in tmp_scene.collection.objects:
                tmp_scene.collection.objects.link(obj)

        mins, maxs = _world_bbox(objects)
        _frame_camera(cam_obj, cam_data, mins, maxs)

        with bpy.context.temp_override(scene=tmp_scene):
            bpy.ops.render.render(write_still=True)

    except Exception as e:
        print(f"[Ylos] Publish thumbnail render failed: {e}")
        return ""

    finally:
        if tmp_scene is not None:
            for obj in objects:
                if obj.name in tmp_scene.collection.objects:
                    tmp_scene.collection.objects.unlink(obj)
            if cam_obj is not None and cam_obj.name in tmp_scene.collection.objects:
                tmp_scene.collection.objects.unlink(cam_obj)
        if cam_obj is not None:
            bpy.data.objects.remove(cam_obj, do_unlink=True)
        if cam_data is not None:
            bpy.data.cameras.remove(cam_data)
        if tmp_world is not None:
            bpy.data.worlds.remove(tmp_world)
        if tmp_scene is not None:
            bpy.data.scenes.remove(tmp_scene)

    return thumb_path if os.path.isfile(thumb_path) else ""
