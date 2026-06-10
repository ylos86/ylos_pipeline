# -*- coding: utf-8 -*-
# ylos_houdini/lop_utils.py
# Utilities for importing Ylos assets into a Houdini LOP stage.
#
# Used by:
#   - The Python Panel "Open in Solaris" button
#   - The Ylos Import Asset HDA cook script

from pathlib import Path

from ylos_core.asset import get_asset_root, get_set_root
from .session import YlosSession


# ---------------------------------------------------------------------------
# Asset root resolution
# ---------------------------------------------------------------------------

def _find_root_usd(project_path: str, entity_name: str,
                   entity_type: str = "asset") -> Path | None:
    """Return the path to the entity root USD, or None if not published yet."""
    if entity_type == "asset":
        root = get_asset_root(project_path, entity_name) / "asset_root.usd"
    elif entity_type == "set":
        root = get_set_root(project_path, entity_name) / "set_root.usd"
    else:
        return None
    return root if root.exists() else None


# ---------------------------------------------------------------------------
# Stage import
# ---------------------------------------------------------------------------

def import_asset_to_stage(
    entity_name: str,
    project_path: str | None = None,
    entity_type: str = "asset",
    lopnet_path: str = "/stage",
    prim_path: str | None = None,
) -> "hou.Node":
    """
    Import an entity's root USD into the LOP stage via a Reference node.

    Args:
        entity_name:  Entity name (e.g. "CHAR_Hero").
        project_path: Project root. Defaults to session.project_path.
        entity_type:  "asset" or "set".
        lopnet_path:  Path to the LOP network. Created if absent.
        prim_path:    USD prim path for the reference.
                      Defaults to /ROOT/{entity_name}.

    Returns:
        The created hou.Node (type "reference").

    Raises:
        FileNotFoundError if no root USD exists for the entity.
        RuntimeError if hou is unavailable.
    """
    import hou

    if project_path is None:
        project_path = YlosSession.get().project_path
    if not project_path:
        raise RuntimeError("No project loaded in Ylos session.")

    root_usd = _find_root_usd(project_path, entity_name, entity_type)
    if root_usd is None:
        raise FileNotFoundError(
            f"No root USD found for {entity_type} '{entity_name}'. "
            f"Publish at least one modeling layer from Blender first."
        )

    if prim_path is None:
        prim_path = f"/ROOT/{entity_name}"

    # Get or create the LOP network
    lopnet = hou.node(lopnet_path)
    if lopnet is None:
        parent_path, _, net_name = lopnet_path.rpartition("/")
        parent = hou.node(parent_path or "/") or hou.node("/")
        lopnet = parent.createNode("lopnet", net_name or "stage")

    # Name the node after the entity so it is human-readable in the graph
    node_name = "ylos_" + entity_name.lower().replace("-", "_")
    existing = lopnet.node(node_name)
    if existing:
        # Update existing node rather than duplicate
        existing.parm("filepath1").set(str(root_usd))
        existing.parm("primpath").set(prim_path)
        existing.moveToGoodPosition()
        return existing

    ref_node = lopnet.createNode("reference", node_name)
    ref_node.parm("filepath1").set(str(root_usd))
    ref_node.parm("primpath").set(prim_path)
    ref_node.moveToGoodPosition()

    return ref_node


def import_current_entity() -> "hou.Node | None":
    """
    Import the session's current entity into /stage.
    Convenience wrapper for the panel button.
    Returns the node, or None on error (prints to stdout).
    """
    session = YlosSession.get()
    if not session.project_path or not session.current_entity:
        print("[Ylos] No project/entity in session. Load a project first.")
        return None
    try:
        node = import_asset_to_stage(
            session.current_entity,
            session.project_path,
            session.context_type.lower(),
        )
        print(f"[Ylos] Imported {session.current_entity} -> {node.path()}")
        return node
    except Exception as e:
        print(f"[Ylos] import_current_entity failed: {e}")
        return None


# ---------------------------------------------------------------------------
# HDA cook helper (called from within the HDA Python script)
# ---------------------------------------------------------------------------

def hda_import_asset_cook(node: "hou.Node") -> None:
    """
    Cook callback for the Ylos Import Asset HDA.
    Resolves the entity path from the 'entity' parameter and creates
    a Reference arc on the stage.

    The HDA is a Python LOP; this function is bound to its cook script.
    """
    import hou

    entity_name = node.parm("entity").eval().strip()
    if not entity_name:
        node.errors()
        return

    session = YlosSession.get()
    if not session.project_path:
        node.addError("No Ylos project loaded. Use the Ylos Pipeline panel.")
        return

    root_usd = _find_root_usd(session.project_path, entity_name)
    if root_usd is None:
        node.addError(
            f"No asset_root.usd for '{entity_name}'. "
            f"Publish a modeling layer from Blender first."
        )
        return

    stage = node.editableStage()
    if stage is None:
        return

    from pxr import Usd, UsdGeom, Sdf
    prim_path = node.parm("primpath").eval() or f"/ROOT/{entity_name}"

    prim = stage.DefinePrim(prim_path)
    prim.GetReferences().AddReference(str(root_usd))
