#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# create_hdas.py
#
# Run this script ONCE from the Houdini Python Shell to generate the
# Ylos Import Asset HDA into ylos_houdini/otls/.
#
# Usage (inside Houdini):
#   import sys; sys.path.insert(0, "/path/to/ylos_pipeline")
#   exec(open("/path/to/ylos_pipeline/create_hdas.py").read())
#
# Or via File > Run Script... in Houdini.
#
# The generated .hda file is saved to:
#   ylos_houdini/otls/ylos_import_asset.hda
#
# After running, the HDA is available in any LOP network as
#   "ylos::import_asset::1.0"

import hou
import os
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.resolve()
_OTL_DIR   = _REPO_ROOT / "ylos_houdini" / "otls"
_OTL_DIR.mkdir(parents=True, exist_ok=True)
_HDA_PATH  = str(_OTL_DIR / "ylos_import_asset.hda")


# ---------------------------------------------------------------------------
# HDA embedded scripts
# ---------------------------------------------------------------------------

_COOK_SCRIPT = '''\
# Ylos Import Asset -- cook script
# Runs inside the LOP stage cook; uses pxr to add a Reference arc.
from ylos_houdini.lop_utils import hda_import_asset_cook
hda_import_asset_cook(kwargs["node"])
'''

_ON_CREATED_SCRIPT = '''\
# Ylos Import Asset -- OnCreated callback
# Pre-fill entity and primpath from the active session context.
try:
    from ylos_houdini.session import YlosSession
    session = YlosSession.get()
    node = kwargs["node"]
    if session.current_entity:
        node.parm("entity").set(session.current_entity)
        node.parm("primpath").set(f"/ROOT/{session.current_entity}")
except Exception as e:
    print(f"[Ylos] OnCreated callback failed: {e}")
'''

_HELP_CARD = '''\
= Ylos Import Asset =

Import an asset's root USD into the current LOP stage via a Reference arc.

The node reads the *entity* parameter, looks up `asset_root.usd` in the
active Ylos project, and adds a reference at the specified *primpath*.

@parameters
    Entity Name:
        Asset name matching a published entity in the active Ylos project.
    Prim Path:
        USD prim path where the reference will be placed.
        Default: `/ROOT/{entity_name}`.

@note
    Requires an active Ylos project session.
    Publish at least one modeling layer from Blender before importing.
'''


# ---------------------------------------------------------------------------
# Build the HDA
# ---------------------------------------------------------------------------

def create_import_asset_hda():
    print(f"[Ylos] Creating Ylos Import Asset HDA -> {_HDA_PATH}")

    # We need a temporary LOP network to define the HDA from
    # Try /stage first; create a temp lopnet if /stage doesn't exist
    stage = hou.node("/stage")
    if stage is None:
        obj = hou.node("/obj")
        stage = obj.createNode("lopnet", "ylos_hda_build_stage")
        _cleanup_stage = True
    else:
        _cleanup_stage = False

    # Create a Python LOP node to base the HDA on
    tmp_node = stage.createNode("pythonscript", "ylos_import_asset_tmp")

    try:
        # Define the HDA
        hda_def = tmp_node.createDigitalAsset(
            name="ylos::import_asset",
            hda_file_name=_HDA_PATH,
            description="Ylos Import Asset",
            min_num_inputs=0,
            max_num_inputs=1,
            version="1.0",
        )
    except hou.OperationFailed as e:
        # If the HDA already exists, reinstall it
        print(f"  Note: {e} -- reinstalling existing HDA.")
        hou.hda.installFile(_HDA_PATH)
        hda_def = hou.hdaDefinition(
            hou.nodeTypeCategory().nodeTypes().get("lop"),
            "ylos::import_asset",
            _HDA_PATH,
        )
        if hda_def is None:
            print("[Ylos] ERROR: Could not get HDA definition after reinstall.")
            return
    finally:
        tmp_node.destroy()
        if _cleanup_stage:
            stage.destroy()

    # --- Parameters ---
    pt = hda_def.parmTemplateGroup()

    entity_pt = hou.StringParmTemplate(
        "entity", "Entity Name", 1,
        default_value=("",),
        help="Asset name matching a published entity in the active Ylos project.",
    )
    pt.append(entity_pt)

    primpath_pt = hou.StringParmTemplate(
        "primpath", "Prim Path", 1,
        default_value=("/ROOT",),
        help="USD prim path where the reference is placed.",
    )
    pt.append(primpath_pt)

    hda_def.setParmTemplateGroup(pt)

    # --- Cook script ---
    hda_def.addSection("PythonCook").setContents(_COOK_SCRIPT)

    # --- Callbacks ---
    hda_def.addSection("OnCreated").setContents(_ON_CREATED_SCRIPT)

    hda_def.setExtraFileOption("PythonCook/IsPython", True)
    hda_def.setExtraFileOption("OnCreated/IsPython", True)

    # --- Help ---
    hda_def.addSection("Tools.shelf").setContents(_HELP_CARD)
    hda_def.setDescription("Import an asset root USD into the LOP stage.")

    # --- Icon ---
    try:
        hda_def.setIcon("SOP_alembic")
    except Exception:
        pass

    # Reload
    hda_def.save(_HDA_PATH)
    hou.hda.installFile(_HDA_PATH, force_use_assets=True)

    print(f"[Ylos] HDA created successfully: {_HDA_PATH}")
    print("       Available in LOP networks as: ylos::import_asset::1.0")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

create_import_asset_hda()
