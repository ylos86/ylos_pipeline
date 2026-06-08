# -*- coding: utf-8 -*-
# Ylos Pipeline - core/usd_composer.py
# Assembles the USD "matryoshka" (poupee russe) for each asset.
# Writes and updates asset_root.usd and set_root.usd using plain text USDA
# so we don't need pxr installed in Blender's Python.
# For more complex stage edits (variants, payloads), use an external
# Python environment with usd-core installed.

from pathlib import Path
from .project import ASSET_STEPS, SET_STEPS
from .asset import (
    get_latest_publish_path,
    get_asset_root,
    get_set_root,
)


# ---------------------------------------------------------------------------
# USDA writer (no pxr dependency)
# ---------------------------------------------------------------------------

USDA_HEADER = '#usda 1.0\n(\n    defaultPrim = "ROOT"\n    upAxis = "Y"\n    metersPerUnit = 1\n)\n\n'


def _make_sublayer_block(layer_paths: list[str]) -> str:
    """Build the subLayers block for a USDA root file."""
    if not layer_paths:
        return ""
    lines = ["(\n    subLayers = [\n"]
    for p in reversed(layer_paths):     # strongest opinion last in USD
        rel = p.replace("\\", "/")
        lines.append(f'        @{rel}@,\n')
    lines.append("    ]\n)\n\n")
    return "".join(lines)


def write_usda_root(filepath: str, sublayers: list[str],
                    root_prim: str = "ROOT") -> None:
    """
    Write a minimal USDA file that sublayers the given paths.
    sublayers: absolute or relative paths to USD files, strongest opinion FIRST.
    """
    # Convert to relative paths from the root file's directory
    root_dir = Path(filepath).parent
    rel_layers = []
    for p in sublayers:
        try:
            rel = Path(p).relative_to(root_dir)
            rel_layers.append(str(rel).replace("\\", "/"))
        except ValueError:
            rel_layers.append(str(Path(p)).replace("\\", "/"))

    content = '#usda 1.0\n(\n'
    content += f'    defaultPrim = "{root_prim}"\n'
    content += '    upAxis = "Y"\n'
    content += '    metersPerUnit = 1\n'

    if rel_layers:
        content += '    subLayers = [\n'
        for rl in reversed(rel_layers):     # strongest opinion last
            content += f'        @{rl}@,\n'
        content += '    ]\n'

    content += ')\n\n'
    content += f'def Xform "{root_prim}" ()\n{{\n}}\n'

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# USDA variantSet writer
# ---------------------------------------------------------------------------

def write_usda_with_variants(filepath: str, sublayers: list[str],
                              variant_blocks: dict, asset_name: str) -> None:
    """
    Write asset_root.usd with both sublayers and variantSet blocks.

    sublayers:      list of USD paths (default/single-variant steps)
    variant_blocks: {step: {variant_name: abs_path}}
    """
    root_dir = Path(filepath).parent

    def rel(p):
        try:
            return str(Path(p).relative_to(root_dir)).replace("\\", "/")
        except ValueError:
            return str(Path(p)).replace("\\", "/")

    lines = ['#usda 1.0\n(\n']
    lines.append(f'    defaultPrim = "{asset_name}"\n')
    lines.append('    upAxis = "Y"\n')
    lines.append('    metersPerUnit = 1\n')

    if sublayers:
        lines.append('    subLayers = [\n')
        for p in reversed(sublayers):
            lines.append(f'        @{rel(p)}@,\n')
        lines.append('    ]\n')

    lines.append(')\n\n')

    # Root prim
    lines.append(f'def Xform "{asset_name}"\n{{\n')

    # One variantSet per step that has variants
    for step, variants in variant_blocks.items():
        set_name = f"{step}Variant"
        default_v = "Default" if "Default" in variants else list(variants.keys())[0]
        lines.append(f'    string variants.{set_name} = "{default_v}"\n')
        lines.append(f'    prepend variantSets = "{set_name}"\n')

    if variant_blocks:
        lines.append('\n')
        for step, variants in variant_blocks.items():
            set_name = f"{step}Variant"
            lines.append(f'    variantSet "{set_name}" = {{\n')
            for vname, vpath in variants.items():
                lines.append(f'        "{vname}" (\n')
                lines.append(f'            prepend references = @{rel(vpath)}@\n')
                lines.append(f'        ) {{}}\n')
            lines.append('    }\n')

    lines.append('}\n')

    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ---------------------------------------------------------------------------
# Asset root composer
# ---------------------------------------------------------------------------

def compose_asset_root(project_path: str, asset_name: str,
                       steps_override: list[str] = None) -> dict:
    """
    Build or rebuild asset_root.usd from the latest publish of each step.
    If multiple variants exist for a step, writes a USD variantSet block.

    Layer order (strongest opinion on top):
        lookdev > rigging > modeling
    """
    from .asset import list_publish_versions

    asset_root = get_asset_root(project_path, asset_name)
    root_usd   = asset_root / "asset_root.usd"

    steps = steps_override if steps_override else list(reversed(ASSET_STEPS))

    layers_used    = []
    variant_blocks = {}   # step -> {variant_name: rel_path}

    for step in steps:
        versions = list_publish_versions(project_path, asset_name, step, "asset")
        if not versions:
            continue

        # Group by version (use latest version only)
        latest_ver = versions[-1]["version"]
        latest = [v for v in versions if v["version"] == latest_ver]

        if len(latest) == 1 and latest[0]["variant"] == "Default":
            # Single default publish — add as sublayer
            layers_used.append(latest[0]["path"])
        else:
            # Multiple variants — record for variantSet block
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
    """
    Build or rebuild set_root.usd from the latest publish of each set step.
    """
    set_root = get_set_root(project_path, set_name)
    root_usd = set_root / "set_root.usd"

    steps = list(reversed(SET_STEPS))  # lighting > lookdev > modeling

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

def read_root_sublayers(root_usd_path: str) -> list[str]:
    """
    Parse an existing asset_root.usd or set_root.usd and return
    the list of sublayer paths as strings. Plain text parse — no pxr needed.
    """
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
                if stripped == "]":
                    break
                # Lines look like: @path/to/file.usd@,
                if stripped.startswith("@") and stripped.endswith(("@,", "@")):
                    p = stripped.strip("@,").strip()
                    layers.append(p)

    return layers
