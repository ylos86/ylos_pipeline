# -*- coding: utf-8 -*-
# ylos_core/usd_composer.py
# Assembles the USD root files for each asset/set.
# Writes plain-text USDA -- no pxr dependency required.
#
# Composition model (arch doc §3):
#   modeling < rigging < lookdev  -- composed as subLayers (strongest last)
#   lookdev with variants         -- variantSet block on the entity prim
#   fx                            -- payload arc on /ROOT/{Entity}/fx scope (§3.2)
#
# FX as payload means the cache is NOT loaded at open time until the consumer
# activates the payload. This is the correct pattern for heavy time-sampled
# mesh caches and VDB volumes.
#
# THIS IS THE SINGLE AUTHORITATIVE WRITER for entity root files.

from pathlib import Path

from .project import ASSET_STEPS, SET_STEPS
from .asset import (
    get_latest_publish_path,
    get_asset_root,
    get_set_root,
    list_publish_versions,
)

ROOT_PRIM = "ROOT"
FX_STEP   = "fx"           # always payload, never sublayer


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _relpath(target: str, root_dir: Path) -> str:
    """Path of target relative to root_dir, POSIX separators for USDA."""
    try:
        return str(Path(target).relative_to(root_dir)).replace("\\", "/")
    except ValueError:
        return str(Path(target)).replace("\\", "/")


def _fx_scope_lines(rel_path: str, indent: str = "        ") -> list:
    """
    Return the USDA lines for the /fx payload scope child prim.
    Callers pass the correct indentation level.
    """
    return [
        f'{indent}def Scope "fx" (\n',
        f'{indent}    prepend payload = @{rel_path}@\n',
        f'{indent}) {{\n',
        f'{indent}}}\n',
    ]


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
                             variant_blocks: dict, entity_name: str,
                             fx_payload_path: str | None = None) -> None:
    """
    Write a root USDA with subLayers, optional variantSets, and optional FX payload.

    Args:
        sublayers:       USD paths for single/default steps (no variants).
                         Strongest opinion FIRST in this list (reversed for USD).
        variant_blocks:  {step: {variant_name: abs_path}}
        entity_name:     Name of the entity prim under /ROOT.
        fx_payload_path: Absolute path to the FX publish USD.
                         Written as a payload arc on /ROOT/{entity_name}/fx.
                         NOT a sublayer -- the cache is deferred until activated.
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
        # Simple entity prim -- may still carry FX payload scope.
        if fx_payload_path:
            lines.append(f'    def Xform "{entity_name}"\n    {{\n')
            lines.extend(_fx_scope_lines(_relpath(fx_payload_path, root_dir), "        "))
            lines.append('    }\n')
        else:
            lines.append(f'    def Xform "{entity_name}"\n    {{\n    }}\n')
        lines.append('}\n')
        with open(filepath, "w", encoding="utf-8") as f:
            f.writelines(lines)
        return

    # Entity prim carrying variantSets.
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

    # FX payload scope as child of entity prim (after variantSet blocks).
    if fx_payload_path:
        lines.append('\n')
        lines.extend(_fx_scope_lines(_relpath(fx_payload_path, root_dir), "        "))

    lines.append('    }\n')   # close entity prim
    lines.append('}\n')       # close ROOT

    with open(filepath, "w", encoding="utf-8") as f:
        f.writelines(lines)


# ---------------------------------------------------------------------------
# Asset root composer
# ---------------------------------------------------------------------------

def compose_asset_root(project_path: str, asset_name: str,
                       steps_override: list = None) -> dict:
    """
    Build or rebuild asset_root.usd from the latest publish of each step.

    FX is always emitted as a payload arc (not a sublayer) so heavy caches
    are deferred until the consumer explicitly loads the payload.
    All other steps follow the existing sublayer/variantSet pattern.

    Layer strength order (weakest -> strongest): modeling < rigging < lookdev
    """
    asset_root = get_asset_root(project_path, asset_name)
    root_usd   = asset_root / "asset_root.usd"

    # Determine step iteration order (strongest-first so later steps dominate).
    steps = steps_override if steps_override else list(reversed(ASSET_STEPS))

    layers_used    = []
    variant_blocks = {}
    fx_payload_path = None

    for step in steps:
        versions = list_publish_versions(project_path, asset_name, step, "asset")
        if not versions:
            continue

        latest_ver = versions[-1]["version"]
        latest = [v for v in versions if v["version"] == latest_ver]

        if step == FX_STEP:
            # FX is always a payload -- never sublayered regardless of variants.
            # Phase 3 supports a single FX publish path (no FX variant support).
            fx_payload_path = latest[0]["path"]
        elif len(latest) == 1 and latest[0]["variant"] == "Default":
            layers_used.append(latest[0]["path"])
        else:
            variant_blocks[step] = {v["variant"]: v["path"] for v in latest}

    if not layers_used and not variant_blocks and fx_payload_path is None:
        return {
            "success": False,
            "root_path": str(root_usd),
            "layers_used": [],
            "message": f"No published layers found for asset: {asset_name}",
        }

    try:
        write_usda_with_variants(
            str(root_usd), layers_used, variant_blocks, asset_name,
            fx_payload_path=fx_payload_path,
        )
    except Exception as e:
        return {
            "success": False,
            "root_path": str(root_usd),
            "layers_used": layers_used,
            "message": str(e),
        }

    total = len(layers_used) + sum(len(v) for v in variant_blocks.values())
    fx_note = "  + FX payload" if fx_payload_path else ""
    return {
        "success": True,
        "root_path": str(root_usd),
        "layers_used": layers_used,
        "fx_payload_path": fx_payload_path,
        "message": (
            f"asset_root.usd updated ({total} publish(es), "
            f"{len(variant_blocks)} variantSet(s)){fx_note}."
        ),
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


def read_root_fx_payload(root_usd_path: str) -> str | None:
    """
    Parse the FX payload path from a root USDA written by this module.
    Returns the relative path string, or None if no FX payload is present.
    """
    path = Path(root_usd_path)
    if not path.exists():
        return None

    in_fx_scope = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if 'def Scope "fx"' in s:
            in_fx_scope = True
            continue
        if in_fx_scope:
            if "payload" in s and "@" in s:
                return s[s.find("@") + 1:s.rfind("@")]
            if s.startswith("}"):
                break

    return None
