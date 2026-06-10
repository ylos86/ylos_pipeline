# -*- coding: utf-8 -*-
# ylos_core/usd_composer.py
# Assembles the USD root files for each asset/set.
# Writes plain-text USDA so we do not need pxr in either DCC.
#
# Convention:
#   - defaultPrim is always "ROOT".
#   - Entity prim lives at /ROOT/{EntityName}.
#   - Per-step publishes are composed as subLayers (strongest opinion last).
#   - When a step has variants, a variantSet "{step}Variant" is written on
#     the entity prim, each variant carrying a references arc to its publish.
#
# THIS IS THE SINGLE AUTHORITATIVE WRITER for entity root files.
# Houdini's lookdev/layout layers (authored via pxr/Solaris) are NOT roots --
# they are per-step publishes that this writer references.

from pathlib import Path

from .project import ASSET_STEPS, SET_STEPS
from .asset import (
    get_latest_publish_path,
    get_asset_root,
    get_set_root,
    list_publish_versions,
)

ROOT_PRIM = "ROOT"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _relpath(target: str, root_dir: Path) -> str:
    """Path of target relative to root_dir, POSIX separators for USDA."""
    try:
        return str(Path(target).relative_to(root_dir)).replace("\\", "/")
    except ValueError:
        return str(Path(target)).replace("\\", "/")


# ---------------------------------------------------------------------------
# USDA writers (no pxr dependency)
# ---------------------------------------------------------------------------

def write_usda_root(filepath: str, sublayers: list) -> None:
    """
    Write a minimal USDA file that sublayers the given paths under /ROOT.
    sublayers: paths to USD files, strongest opinion FIRST (we reverse for USD).
    """
    root_dir   = Path(filepath).parent
    rel_layers = [_relpath(p, root_dir) for p in sublayers]

    lines = ['#usda 1.0\n(\n']
    lines.append(f'    defaultPrim = "{ROOT_PRIM}"\n')
    lines.append('    upAxis = "Y"\n')
    lines.append('    metersPerUnit = 1\n')

    if rel_layers:
        lines.append('    subLayers = [\n')
        for rl in reversed(rel_layers):
            lines.append(f'        @{rl}@,\n')
        lines.append('    ]\n')

    lines.append(')\n\n')
    lines.append(f'def Xform "{ROOT_PRIM}"\n{{\n}}\n')

    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(lines)


def write_usda_with_variants(filepath: str, sublayers: list,
                             variant_blocks: dict, entity_name: str) -> None:
    """
    Write a root USDA with subLayers plus a variantSet per step that has variants.

    sublayers:      list of USD paths for single/default steps (no variants)
    variant_blocks: {step: {variant_name: abs_path}}
    entity_name:    name of the entity prim under /ROOT

    Variant arcs use references so each variant pulls in its own publish.
    Syntax follows canonical USDA (variantSets / variants / variantSet blocks).
    """
    root_dir = Path(filepath).parent

    lines = ['#usda 1.0\n(\n']
    lines.append(f'    defaultPrim = "{ROOT_PRIM}"\n')
    lines.append('    upAxis = "Y"\n')
    lines.append('    metersPerUnit = 1\n')

    if sublayers:
        lines.append('    subLayers = [\n')
        for p in reversed(sublayers):
            lines.append(f'        @{_relpath(p, root_dir)}@,\n')
        lines.append('    ]\n')

    lines.append(')\n\n')
    lines.append(f'def Xform "{ROOT_PRIM}"\n{{\n')

    if not variant_blocks:
        lines.append(f'    def Xform "{entity_name}"\n    {{\n    }}\n')
        lines.append('}\n')
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return

    set_names = [f"{step}Variant" for step in variant_blocks]

    lines.append(f'    def Xform "{entity_name}" (\n')
    lines.append('        prepend variantSets = [' +
                 ', '.join(f'"{s}"' for s in set_names) + ']\n')

    lines.append('        variants = {\n')
    for step, variants in variant_blocks.items():
        set_name  = f"{step}Variant"
        default_v = "Default" if "Default" in variants else next(iter(variants))
        lines.append(f'            string {set_name} = "{default_v}"\n')
    lines.append('        }\n')
    lines.append('    )\n')
    lines.append('    {\n')

    for step, variants in variant_blocks.items():
        set_name = f"{step}Variant"
        lines.append(f'        variantSet "{set_name}" = {{\n')
        for vname, vpath in variants.items():
            lines.append(f'            "{vname}" (\n')
            lines.append(f'                prepend references = @{_relpath(vpath, root_dir)}@\n')
            lines.append('            ) {\n')
            lines.append('            }\n')
        lines.append('        }\n')

    lines.append('    }\n')
    lines.append('}\n')

    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ---------------------------------------------------------------------------
# Asset root composer
# ---------------------------------------------------------------------------

def compose_asset_root(project_path: str, asset_name: str,
                       steps_override: list = None) -> dict:
    """
    Build or rebuild asset_root.usd from the latest publish of each step.
    If multiple variants exist for a step, writes a USD variantSet block.

    Layer order (strongest opinion last in USD): modeling < rigging < lookdev
    """
    asset_root = get_asset_root(project_path, asset_name)
    root_usd   = asset_root / "asset_root.usd"

    steps = steps_override if steps_override else list(reversed(ASSET_STEPS))

    layers_used    = []
    variant_blocks = {}

    for step in steps:
        versions = list_publish_versions(project_path, asset_name, step, "asset")
        if not versions:
            continue

        latest_ver = versions[-1]["version"]
        latest = [v for v in versions if v["version"] == latest_ver]

        if len(latest) == 1 and latest[0]["variant"] == "Default":
            layers_used.append(latest[0]["path"])
        else:
            variant_blocks[step] = {v["variant"]: v["path"] for v in latest}

    if not layers_used and not variant_blocks:
        return {
            "success": False,
            "root_path": str(root_usd),
            "layers_used": [],
            "message": f"No published layers found for asset: {asset_name}",
        }

    try:
        write_usda_with_variants(str(root_usd), layers_used, variant_blocks, asset_name)
    except Exception as e:
        return {
            "success": False,
            "root_path": str(root_usd),
            "layers_used": layers_used,
            "message": str(e),
        }

    total = len(layers_used) + sum(len(v) for v in variant_blocks.values())
    return {
        "success": True,
        "root_path": str(root_usd),
        "layers_used": layers_used,
        "message": f"asset_root.usd updated ({total} publish(es), {len(variant_blocks)} variantSet(s)).",
    }


# ---------------------------------------------------------------------------
# Set root composer
# ---------------------------------------------------------------------------

def compose_set_root(project_path: str, set_name: str) -> dict:
    """Build or rebuild set_root.usd from the latest publish of each set step."""
    set_root = get_set_root(project_path, set_name)
    root_usd = set_root / "set_root.usd"

    steps = list(reversed(SET_STEPS))

    layers_used = []
    for step in steps:
        pub_path = get_latest_publish_path(project_path, set_name, step, "set")
        if pub_path:
            layers_used.append(pub_path)

    if not layers_used:
        return {
            "success": False,
            "root_path": str(root_usd),
            "layers_used": [],
            "message": f"No published layers found for set: {set_name}",
        }

    try:
        write_usda_root(str(root_usd), layers_used)
    except Exception as e:
        return {
            "success": False,
            "root_path": str(root_usd),
            "layers_used": layers_used,
            "message": str(e),
        }

    return {
        "success": True,
        "root_path": str(root_usd),
        "layers_used": layers_used,
        "message": f"set_root.usd updated with {len(layers_used)} layer(s).",
    }


# ---------------------------------------------------------------------------
# Inspect existing root
# ---------------------------------------------------------------------------

def read_root_sublayers(root_usd_path: str) -> list:
    """Parse an existing root USDA and return its subLayer paths (no pxr)."""
    path = Path(root_usd_path)
    if not path.exists():
        return []

    layers = []
    in_sublayers = False
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if "subLayers" in stripped:
                in_sublayers = True
                continue
            if in_sublayers:
                if stripped.startswith("]"):
                    break
                if stripped.startswith("@"):
                    layers.append(stripped.strip("@,").strip())
    return layers


def read_root_variants(root_usd_path: str) -> dict:
    """
    Parse variantSet blocks from a root USDA written by write_usda_with_variants.
    Returns {set_name: {variant_name: referenced_path}}.
    Plain-text parse that round-trips the subset this module writes.
    """
    path = Path(root_usd_path)
    if not path.exists():
        return {}

    result = {}
    current_set = None
    current_variant = None

    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()

        if s.startswith('variantSet "') and s.endswith('= {'):
            current_set = s.split('"')[1]
            result[current_set] = {}
            current_variant = None
            continue

        if current_set is not None:
            if s.startswith('"') and "(" in s:
                current_variant = s.split('"')[1]
                result[current_set].setdefault(current_variant, "")
            elif "references" in s and "@" in s and current_variant is not None:
                ref = s[s.find("@") + 1:s.rfind("@")]
                result[current_set][current_variant] = ref
            elif s == "}" and current_variant is not None:
                current_variant = None

    return result
