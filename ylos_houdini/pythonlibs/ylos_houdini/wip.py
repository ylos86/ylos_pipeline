# -*- coding: utf-8 -*-
# ylos_houdini/wip.py
# Versioned WIP save for Houdini hip files.
#
# Extension is determined by the active license (arch doc §2):
#   Apprentice  -> .hipnc
#   Indie       -> .hiplc
#   Commercial  -> .hip
#
# Version resolution reuses ylos_core.asset.list_wip_versions parameterised
# by all three hip extensions -- no duplication of version logic.

from ylos_core.asset import (
    HIP_EXTENSIONS,
    get_latest_wip_version,
    resolve_wip_save_path,
    list_wip_versions,
)
from .session import YlosSession


def _hip_ext_for_license(license_str: str) -> str:
    """Return the correct hip extension for the active license."""
    return {
        "commercial": "hip",
        "indie":      "hiplc",
    }.get(license_str, "hipnc")


def get_current_wip_ext() -> str:
    """Return the hip extension appropriate for the current session license."""
    return _hip_ext_for_license(YlosSession.get().license)


def suggest_next_wip_version(project_path: str, entity_name: str,
                              step: str, entity_type: str = "asset") -> int:
    """
    Return latest_version + 1 across all hip extensions.
    Scans .hip, .hiplc, .hipnc so version numbers stay monotone even if the
    license tier changes mid-project.
    """
    latest = get_latest_wip_version(
        project_path, entity_name, step, entity_type,
        exts=list(HIP_EXTENSIONS),
    )
    return latest + 1


def save_wip(version: int | None = None) -> dict:
    """
    Save the current Houdini session as a versioned WIP file.

    If version is None, auto-increments from the latest existing version.

    Returns:
        {"success": bool, "path": str, "message": str}
    """
    import hou

    session = YlosSession.get()

    if not session.project_path:
        return {"success": False, "path": "", "message": "No project loaded."}
    if not session.current_entity:
        return {"success": False, "path": "", "message": "No entity set in context."}

    step        = session.current_step
    entity      = session.current_entity
    entity_type = session.context_type.lower()
    ext         = get_current_wip_ext()

    if version is None:
        version = suggest_next_wip_version(
            session.project_path, entity, step, entity_type
        )

    save_path = resolve_wip_save_path(
        session.project_path, entity, step, version, entity_type, ext
    )

    try:
        hou.hipFile.save(save_path, save_to_recent_files=True)
    except hou.OperationFailed as e:
        return {"success": False, "path": save_path, "message": str(e)}

    return {
        "success": True,
        "path":    save_path,
        "message": f"Saved: {save_path}",
    }


def save_wip_dialog() -> None:
    """
    Show a Houdini version-input dialog then save.
    Callable from the shelf tool.
    """
    import hou

    session = YlosSession.get()
    if not session.project_path or not session.current_entity:
        hou.ui.displayMessage(
            "Load a project and set an active entity before saving a WIP.",
            severity=hou.severityType.Warning,
            title="Ylos - Save WIP",
        )
        return

    next_v = suggest_next_wip_version(
        session.project_path,
        session.current_entity,
        session.current_step,
        session.context_type.lower(),
    )
    ext = get_current_wip_ext()

    result = hou.ui.readInput(
        f"Save WIP version ({ext}):",
        initial_contents=str(next_v),
        title="Ylos - Save WIP",
        buttons=("Save", "Cancel"),
    )

    if result[0] != 0:          # Cancel
        return

    try:
        version = int(result[1])
    except ValueError:
        hou.ui.displayMessage(
            "Version must be a number.",
            severity=hou.severityType.Error,
            title="Ylos - Save WIP",
        )
        return

    outcome = save_wip(version)

    if outcome["success"]:
        hou.ui.displayMessage(
            outcome["message"],
            severity=hou.severityType.Message,
            title="Ylos - Save WIP",
        )
    else:
        hou.ui.displayMessage(
            f"Save failed: {outcome['message']}",
            severity=hou.severityType.Error,
            title="Ylos - Save WIP",
        )


def list_hip_wip_versions(project_path: str, entity_name: str,
                           step: str, entity_type: str = "asset") -> list:
    """
    Return all hip WIP versions (all extensions) sorted by version.
    Convenience wrapper for the panel.
    """
    return list_wip_versions(
        project_path, entity_name, step, entity_type,
        exts=list(HIP_EXTENSIONS),
    )
