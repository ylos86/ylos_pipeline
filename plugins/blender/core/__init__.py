# -*- coding: utf-8 -*-
import os


def _parse_wip_path(filepath):
    """Extract (asset_name, step) from a .blend path using 'wip' as anchor."""
    parts = filepath.replace("\\", "/").split("/")
    try:
        idx = parts.index("wip")
        return parts[idx + 1], parts[idx + 2]
    except (ValueError, IndexError):
        return None, None


def _get_active_project():
    """Read project root from ~/.ylos/active_project, or None."""
    p = os.path.expanduser("~/.ylos/active_project")
    return open(p).read().strip() if os.path.exists(p) else None


def _next_version(project_root, asset_name, step):
    """Compute next publish version by scanning the publish dir (fallback if manifest unavailable)."""
    pub_dir = os.path.join(project_root, "assets", asset_name, step, "publish")
    if not os.path.exists(pub_dir):
        return "v001"
    vers = sorted(
        d for d in os.listdir(pub_dir)
        if os.path.isdir(os.path.join(pub_dir, d)) and d.startswith("v")
    )
    return f"v{int(vers[-1][1:]) + 1:03d}" if vers else "v001"
