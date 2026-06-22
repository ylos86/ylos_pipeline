# -*- coding: utf-8 -*-
# Viewport thumbnail generation on WIP save + preview loading for version picker.

import os
import bpy
from bpy.utils import previews
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
