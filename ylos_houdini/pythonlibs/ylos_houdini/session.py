# -*- coding: utf-8 -*-
# ylos_houdini/session.py
# YlosSession -- singleton that holds the active project/entity/step context.
#
# Persistence model (arch doc S-7):
#   - Primary store  : hou.node('/').userData("ylos_context")  -- survives hip save/load
#   - Fallback store : $HOUDINI_USER_PREF_DIR/ylos_prefs.json -- last used project
#
# Conflict resolution rule (arch doc review S-2):
#   userData of the loaded hip always wins; ylos_prefs.json is only consulted
#   when opening a blank / new hip that carries no context.
#
# License detection:
#   hou.licenseCategory() is queried once on first access and cached.
#   Apprentice/Education: publish actions are disabled.
#   Indie/Commercial: full access.

from __future__ import annotations
import json
import os
from pathlib import Path


# ---------------------------------------------------------------------------
# License helpers
# ---------------------------------------------------------------------------

def _detect_license() -> str:
    """Return 'commercial', 'indie', 'apprentice', 'education', or 'unknown'."""
    try:
        import hou
        cat = hou.licenseCategory()
        mapping = {
            hou.licenseCategoryType.Commercial:  "commercial",
            hou.licenseCategoryType.Indie:       "indie",
            hou.licenseCategoryType.Apprentice:  "apprentice",
            hou.licenseCategoryType.Education:   "education",
        }
        return mapping.get(cat, "unknown")
    except Exception:
        return "unknown"


def can_publish() -> bool:
    """Return True if the current license allows writing USD publishes."""
    lic = YlosSession.get().license
    return lic in ("commercial", "indie")


# ---------------------------------------------------------------------------
# Prefs path
# ---------------------------------------------------------------------------

def _prefs_path() -> Path:
    try:
        import hou
        return Path(hou.homeHoudiniDirectory()) / "ylos_prefs.json"
    except Exception:
        return Path.home() / "ylos_prefs.json"


# ---------------------------------------------------------------------------
# Session singleton
# ---------------------------------------------------------------------------

_HIP_USER_DATA_KEY = "ylos_context"

_DEFAULTS = {
    "project_path":    "",
    "project_name":    "",
    "current_entity":  "",
    "current_step":    "modeling",
    "context_type":    "asset",
    "prod_type":       "FILM",
}


class YlosSession:
    """
    Module-level singleton holding the active Ylos context.
    Access via YlosSession.get() -- never instantiate directly.
    """

    _instance: YlosSession | None = None
    _license: str | None = None

    def __init__(self):
        self.project_path   = _DEFAULTS["project_path"]
        self.project_name   = _DEFAULTS["project_name"]
        self.current_entity = _DEFAULTS["current_entity"]
        self.current_step   = _DEFAULTS["current_step"]
        self.context_type   = _DEFAULTS["context_type"]
        self.prod_type      = _DEFAULTS["prod_type"]
        # Refresh callbacks registered by the panel
        self._on_change_callbacks: list = []

    # ------------------------------------------------------------------
    # Singleton access
    # ------------------------------------------------------------------

    @classmethod
    def get(cls) -> YlosSession:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    # License (cached, detected once)
    # ------------------------------------------------------------------

    @property
    def license(self) -> str:
        if YlosSession._license is None:
            YlosSession._license = _detect_license()
        return YlosSession._license

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def _to_dict(self) -> dict:
        return {
            "project_path":   self.project_path,
            "project_name":   self.project_name,
            "current_entity": self.current_entity,
            "current_step":   self.current_step,
            "context_type":   self.context_type,
            "prod_type":      self.prod_type,
        }

    def _from_dict(self, data: dict) -> None:
        self.project_path   = data.get("project_path",   _DEFAULTS["project_path"])
        self.project_name   = data.get("project_name",   _DEFAULTS["project_name"])
        self.current_entity = data.get("current_entity", _DEFAULTS["current_entity"])
        self.current_step   = data.get("current_step",   _DEFAULTS["current_step"])
        self.context_type   = data.get("context_type",   _DEFAULTS["context_type"])
        self.prod_type      = data.get("prod_type",      _DEFAULTS["prod_type"])

    # ------------------------------------------------------------------
    # Hip persistence
    # ------------------------------------------------------------------

    def save_to_hip(self) -> None:
        """Write current context to hou.node('/').userData(). Call after any change."""
        try:
            import hou
            hou.node("/").setUserData(_HIP_USER_DATA_KEY, json.dumps(self._to_dict()))
        except Exception as e:
            print(f"[Ylos] save_to_hip failed: {e}")

    def load_from_hip(self) -> bool:
        """
        Restore context from hou.node('/').userData().
        Returns True if context data was found, False if the hip had none.
        """
        try:
            import hou
            raw = hou.node("/").userData(_HIP_USER_DATA_KEY)
            if raw:
                self._from_dict(json.loads(raw))
                return True
        except Exception as e:
            print(f"[Ylos] load_from_hip failed: {e}")
        return False

    # ------------------------------------------------------------------
    # Prefs persistence (last-used project for blank hips)
    # ------------------------------------------------------------------

    def save_to_prefs(self) -> None:
        """Persist the current project path as the last-used project."""
        try:
            path = _prefs_path()
            data = {}
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            data["last_project_path"] = self.project_path
            data["last_project_name"] = self.project_name
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[Ylos] save_to_prefs failed: {e}")

    def load_from_prefs(self) -> bool:
        """
        Load last project from prefs.json. Used only for blank hips (no userData).
        Returns True if a valid project path was found.
        """
        try:
            path = _prefs_path()
            if not path.exists():
                return False
            data = json.loads(path.read_text(encoding="utf-8"))
            proj = data.get("last_project_path", "")
            if proj and Path(proj).exists():
                from ylos_core.project import load_project
                config = load_project(proj)
                if config:
                    self.project_path = proj
                    self.project_name = data.get(
                        "last_project_name",
                        config.get("project", {}).get("name", "")
                    )
                    return True
        except Exception as e:
            print(f"[Ylos] load_from_prefs failed: {e}")
        return False

    # ------------------------------------------------------------------
    # Context mutation (always persist + notify)
    # ------------------------------------------------------------------

    def set_project(self, project_path: str, project_name: str,
                    prod_type: str = "FILM") -> None:
        self.project_path = project_path
        self.project_name = project_name
        self.prod_type    = prod_type
        self.save_to_hip()
        self.save_to_prefs()
        self._notify()

    def set_entity(self, entity_name: str, context_type: str = "asset") -> None:
        self.current_entity = entity_name
        self.context_type   = context_type
        self.save_to_hip()
        self._notify()

    def set_step(self, step: str) -> None:
        self.current_step = step
        self.save_to_hip()
        self._notify()

    # ------------------------------------------------------------------
    # Change notification (panel refresh)
    # ------------------------------------------------------------------

    def register_on_change(self, callback) -> None:
        """Register a zero-argument callable called after any context mutation."""
        if callback not in self._on_change_callbacks:
            self._on_change_callbacks.append(callback)

    def unregister_on_change(self, callback) -> None:
        self._on_change_callbacks = [c for c in self._on_change_callbacks if c != callback]

    def _notify(self) -> None:
        for cb in list(self._on_change_callbacks):
            try:
                cb()
            except Exception as e:
                print(f"[Ylos] on_change callback failed: {e}")


# ---------------------------------------------------------------------------
# Hip file event callbacks (registered by startup/ylos_init.py)
# ---------------------------------------------------------------------------

def _on_hip_event(event_type) -> None:
    """
    Houdini hip file event handler.
    Restores session context after a hip is loaded.
    """
    try:
        import hou
        if event_type == hou.hipFileEventType.AfterLoad:
            session = YlosSession.get()
            # Hip userData takes priority (arch doc S-7 conflict resolution).
            restored = session.load_from_hip()
            if not restored:
                # Blank/new hip -- fall back to last-used project from prefs.
                session.load_from_prefs()
            session._notify()
    except Exception as e:
        print(f"[Ylos] hip event callback failed: {e}")


def register_callbacks() -> None:
    """Register Houdini event callbacks. Called once from startup/ylos_init.py."""
    try:
        import hou
        hou.hipFile.addEventCallback(_on_hip_event)
        print("[Ylos] Pipeline callbacks registered.")
    except Exception as e:
        print(f"[Ylos] register_callbacks failed: {e}")
