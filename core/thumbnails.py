# -*- coding: utf-8 -*-
# Ylos Pipeline - core/thumbnails.py
# Viewport thumbnail generation on WIP save + preview loading for version picker.
# Thumbnails are stored as PNG next to the .blend : asset_step_v001_thumb.png

import os
import bpy
import bpy.utils.previews
from pathlib import Path
from datetime import datetime

# Global preview collection — one instance for the lifetime of the addon
_pcoll = None


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def get_thumb_path(blend_path: str) -> str:
    """Return the expected thumbnail PNG path for a given .blend path."""
    p = Path(blend_path)
    return str(p.parent / (p.stem + "_thumb.png"))


def thumb_exists(blend_path: str) -> bool:
    return os.path.isfile(get_thumb_path(blend_path))


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def generate_thumbnail(blend_path: str, context) -> str:
    """
    Render a small viewport thumbnail and save it as PNG next to the .blend.
    Uses bpy.ops.render.opengl with view_context=True to capture the 3D view.
    Render settings are saved and restored after.

    Returns the thumbnail path on success, empty string on failure.
    """
    thumb_path = get_thumb_path(blend_path)
    scene = context.scene

    # Store original render settings
    orig = {
        "filepath":    scene.render.filepath,
        "res_x":       scene.render.resolution_x,
        "res_y":       scene.render.resolution_y,
        "res_pct":     scene.render.resolution_percentage,
        "file_format": scene.render.image_settings.file_format,
    }

    try:
        scene.render.filepath          = thumb_path
        scene.render.resolution_x      = 480
        scene.render.resolution_y      = 300
        scene.render.resolution_percentage = 100
        scene.render.image_settings.file_format = "PNG"

        bpy.ops.render.opengl(write_still=True, view_context=True)

    except Exception as e:
        print(f"[Ylos] Thumbnail generation failed: {e}")
        return ""

    finally:
        scene.render.filepath          = orig["filepath"]
        scene.render.resolution_x      = orig["res_x"]
        scene.render.resolution_y      = orig["res_y"]
        scene.render.resolution_percentage = orig["res_pct"]
        scene.render.image_settings.file_format = orig["file_format"]

    return thumb_path if os.path.isfile(thumb_path) else ""


# ---------------------------------------------------------------------------
# Preview loading
# ---------------------------------------------------------------------------

def init_previews():
    """Create the global preview collection. Call once on addon register."""
    global _pcoll
    if _pcoll is None:
        _pcoll = bpy.utils.previews.new()


def clear_previews():
    """Remove the global preview collection. Call on addon unregister."""
    global _pcoll
    if _pcoll is not None:
        bpy.utils.previews.remove(_pcoll)
        _pcoll = None


def load_thumb_icon(blend_path: str) -> int:
    """
    Load the thumbnail for a .blend file into the preview collection.
    Returns the icon_id (int) to pass to layout.template_icon() or label().
    Returns 0 if no thumbnail exists.
    """
    global _pcoll
    if _pcoll is None:
        init_previews()

    thumb_path = get_thumb_path(blend_path)
    if not os.path.isfile(thumb_path):
        return 0

    # Use the thumb_path as the unique key so different versions get different icons
    key = thumb_path
    if key not in _pcoll:
        try:
            _pcoll.load(key, thumb_path, "IMAGE")
        except Exception as e:
            print(f"[Ylos] Preview load failed for {thumb_path}: {e}")
            return 0

    return _pcoll[key].icon_id


def reload_thumb_icon(blend_path: str) -> int:
    """Force-reload a thumbnail (e.g. after a new save overwrites it)."""
    global _pcoll
    if _pcoll is None:
        return 0

    thumb_path = get_thumb_path(blend_path)
    key = thumb_path

    # Remove existing entry so it gets re-read from disk
    if key in _pcoll:
        del _pcoll[key]

    return load_thumb_icon(blend_path)
