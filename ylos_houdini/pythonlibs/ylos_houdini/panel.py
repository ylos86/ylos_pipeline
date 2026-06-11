# -*- coding: utf-8 -*-
# ylos_houdini/panel.py
# Ylos Pipeline Python Panel for Houdini.
#
# Three tabs mirroring the Blender addon (arch doc S-8):
#   Pipeline  -- project load, current context, license badge
#   Assets    -- entity list, step status
#   Scene     -- save WIP, open root in Solaris, publish list
#
# PySide6 preferred; falls back to PySide2 for older Houdini builds.

try:
    from PySide6 import QtWidgets, QtCore, QtGui
    from PySide6.QtCore import Qt
    _QT6 = True
except ImportError:
    from PySide2 import QtWidgets, QtCore, QtGui
    from PySide2.QtCore import Qt
    _QT6 = False

from pathlib import Path
from ylos_core.project import load_project, ASSET_STEPS
from ylos_core.asset import (
    list_project_entities,
    invalidate_entity_cache,
    list_publish_versions,
    get_asset_step_status,
)
from .session import YlosSession, can_publish
from .wip import save_wip_dialog, list_hip_wip_versions


# ---------------------------------------------------------------------------
# Colours / minimal token set (no external stylesheet dependency)
# ---------------------------------------------------------------------------

_C = {
    "bg":           "#1e1e1e",
    "bg_raised":    "#2a2a2a",
    "bg_header":    "#252525",
    "border":       "#3a3a3a",
    "text":         "#d4d4d4",
    "text_dim":     "#888888",
    "accent":       "#4d9de0",
    "ok":           "#4caf50",
    "warn":         "#ff9800",
    "error":        "#f44336",
    "apprentice":   "#c77dff",
    "indie":        "#4d9de0",
    "commercial":   "#4caf50",
}

_STEP_LABEL = {
    "modeling": "MOD", "rigging": "RIG", "lookdev": "LKD",
    "fx": "FX", "layout": "LAY", "animation": "ANI",
    "lighting": "LGT", "render": "RND", "composite": "COM",
}


def _badge(text: str, color: str) -> "QtWidgets.QLabel":
    lbl = QtWidgets.QLabel(text)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setFixedHeight(20)
    lbl.setStyleSheet(
        f"background:{color};color:#fff;border-radius:3px;"
        f"padding:0 6px;font-size:11px;font-weight:bold;"
    )
    return lbl


def _label(text: str, dim: bool = False) -> "QtWidgets.QLabel":
    lbl = QtWidgets.QLabel(text)
    lbl.setStyleSheet(f"color:{_C['text_dim'] if dim else _C['text']};font-size:12px;")
    return lbl


def _button(text: str, primary: bool = False) -> "QtWidgets.QPushButton":
    btn = QtWidgets.QPushButton(text)
    if primary:
        btn.setStyleSheet(
            f"background:{_C['accent']};color:#fff;border:none;border-radius:3px;"
            f"padding:5px 12px;font-size:12px;font-weight:bold;"
        )
    else:
        btn.setStyleSheet(
            f"background:{_C['bg_raised']};color:{_C['text']};"
            f"border:1px solid {_C['border']};border-radius:3px;"
            f"padding:5px 12px;font-size:12px;"
        )
    return btn


# ---------------------------------------------------------------------------
# Tab: Pipeline
# ---------------------------------------------------------------------------

class _PipelineTab(QtWidgets.QWidget):
    def __init__(self, panel: "YlosPipelinePanel"):
        super().__init__()
        self._panel = panel
        self._build()

    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # --- Project section ---
        layout.addWidget(_label("PROJECT", dim=True))

        row = QtWidgets.QHBoxLayout()
        self._proj_name = _label("(none)")
        self._proj_name.setWordWrap(True)
        row.addWidget(self._proj_name, 1)
        layout.addLayout(row)

        self._proj_path = _label("", dim=True)
        self._proj_path.setWordWrap(True)
        layout.addWidget(self._proj_path)

        btn_row = QtWidgets.QHBoxLayout()
        load_btn = _button("Load Project...", primary=True)
        load_btn.clicked.connect(self._panel._load_project)
        btn_row.addWidget(load_btn)
        refresh_btn = _button("Refresh")
        refresh_btn.clicked.connect(self._panel.refresh)
        btn_row.addWidget(refresh_btn)
        layout.addLayout(btn_row)

        layout.addWidget(self._hr())

        # --- Context section ---
        layout.addWidget(_label("CONTEXT", dim=True))

        entity_row = QtWidgets.QHBoxLayout()
        entity_row.addWidget(_label("Entity:"))
        self._entity_combo = QtWidgets.QComboBox()
        self._entity_combo.setStyleSheet(
            f"background:{_C['bg_raised']};color:{_C['text']};"
            f"border:1px solid {_C['border']};border-radius:3px;padding:3px 6px;"
        )
        self._entity_combo.currentTextChanged.connect(self._on_entity_changed)
        entity_row.addWidget(self._entity_combo, 1)
        layout.addLayout(entity_row)

        step_row = QtWidgets.QHBoxLayout()
        step_row.addWidget(_label("Step:"))
        self._step_combo = QtWidgets.QComboBox()
        self._step_combo.setStyleSheet(
            f"background:{_C['bg_raised']};color:{_C['text']};"
            f"border:1px solid {_C['border']};border-radius:3px;padding:3px 6px;"
        )
        for step in ASSET_STEPS:
            self._step_combo.addItem(step.capitalize(), step)
        self._step_combo.currentIndexChanged.connect(self._on_step_changed)
        step_row.addWidget(self._step_combo, 1)
        layout.addLayout(step_row)

        layout.addWidget(self._hr())

        # --- License badge ---
        layout.addWidget(_label("LICENSE", dim=True))
        self._license_badge = _badge("...", _C["text_dim"])
        layout.addWidget(self._license_badge)

        layout.addStretch(1)

        # --- Apprentice notice ---
        self._apprentice_notice = QtWidgets.QLabel(
            "Apprentice licence detected. Publish actions are disabled.\n"
            "Upgrade to Indie to enable USD publish from Houdini."
        )
        self._apprentice_notice.setWordWrap(True)
        self._apprentice_notice.setStyleSheet(
            f"color:{_C['apprentice']};font-size:11px;padding:6px;"
            f"border:1px solid {_C['apprentice']};border-radius:3px;"
        )
        self._apprentice_notice.setVisible(False)
        layout.addWidget(self._apprentice_notice)

    def _hr(self):
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet(f"color:{_C['border']};")
        return line

    def _on_entity_changed(self, text: str):
        if text:
            YlosSession.get().set_entity(text)

    def _on_step_changed(self, idx: int):
        step = self._step_combo.itemData(idx)
        if step:
            YlosSession.get().set_step(step)

    def update_from_session(self):
        session = YlosSession.get()

        self._proj_name.setText(session.project_name or "(none)")
        p = session.project_path
        self._proj_path.setText(
            ("..." + p[-40:]) if len(p) > 43 else p
        )

        # Populate entity combo
        self._entity_combo.blockSignals(True)
        self._entity_combo.clear()
        if session.project_path:
            entities = list_project_entities(session.project_path, "asset")
            for e in entities:
                self._entity_combo.addItem(e["name"])
            idx = self._entity_combo.findText(session.current_entity)
            if idx >= 0:
                self._entity_combo.setCurrentIndex(idx)
        self._entity_combo.blockSignals(False)

        # Step combo
        self._step_combo.blockSignals(True)
        for i in range(self._step_combo.count()):
            if self._step_combo.itemData(i) == session.current_step:
                self._step_combo.setCurrentIndex(i)
                break
        self._step_combo.blockSignals(False)

        # License badge
        lic = session.license
        color_map = {
            "commercial": _C["commercial"],
            "indie":      _C["indie"],
            "apprentice": _C["apprentice"],
            "education":  _C["warn"],
        }
        self._license_badge.setText(lic.upper())
        self._license_badge.setStyleSheet(
            f"background:{color_map.get(lic, _C['text_dim'])};"
            f"color:#fff;border-radius:3px;padding:0 6px;"
            f"font-size:11px;font-weight:bold;"
        )
        self._apprentice_notice.setVisible(lic == "apprentice")


# ---------------------------------------------------------------------------
# Tab: Assets
# ---------------------------------------------------------------------------

class _AssetsTab(QtWidgets.QWidget):
    def __init__(self, panel: "YlosPipelinePanel"):
        super().__init__()
        self._panel = panel
        self._build()

    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        top_row = QtWidgets.QHBoxLayout()
        top_row.addWidget(_label("ASSETS", dim=True))
        top_row.addStretch()
        refresh_btn = _button("~")
        refresh_btn.setFixedWidth(28)
        refresh_btn.clicked.connect(self._refresh_list)
        top_row.addWidget(refresh_btn)
        layout.addLayout(top_row)

        self._list = QtWidgets.QListWidget()
        self._list.setStyleSheet(
            f"background:{_C['bg_raised']};color:{_C['text']};"
            f"border:1px solid {_C['border']};border-radius:3px;"
            f"font-size:12px;"
        )
        self._list.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self._list, 1)

        # Step status grid for selected entity
        layout.addWidget(_label("STEP STATUS", dim=True))
        self._status_grid = QtWidgets.QHBoxLayout()
        self._status_labels: dict[str, QtWidgets.QLabel] = {}
        for step in ASSET_STEPS:
            lbl = QtWidgets.QLabel(_STEP_LABEL[step])
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setFixedSize(36, 22)
            lbl.setStyleSheet(
                f"background:{_C['bg_raised']};color:{_C['text_dim']};"
                f"border:1px solid {_C['border']};border-radius:3px;"
                f"font-size:10px;"
            )
            self._status_grid.addWidget(lbl)
            self._status_labels[step] = lbl
        self._status_grid.addStretch()
        layout.addLayout(self._status_grid)

    def _refresh_list(self):
        session = YlosSession.get()
        if not session.project_path:
            return
        invalidate_entity_cache(session.project_path)
        self.update_from_session()

    def _on_item_clicked(self, item: "QtWidgets.QListWidgetItem"):
        YlosSession.get().set_entity(item.data(Qt.UserRole) or item.text())
        self._update_step_status()

    def _update_step_status(self):
        session = YlosSession.get()
        if not session.project_path or not session.current_entity:
            for lbl in self._status_labels.values():
                lbl.setStyleSheet(
                    f"background:{_C['bg_raised']};color:{_C['text_dim']};"
                    f"border:1px solid {_C['border']};border-radius:3px;font-size:10px;"
                )
            return

        status = get_asset_step_status(
            session.project_path, session.current_entity
        )
        for step, lbl in self._status_labels.items():
            published = status.get(step, False)
            bg = _C["ok"] if published else _C["bg_raised"]
            fg = "#fff" if published else _C["text_dim"]
            border = _C["ok"] if published else _C["border"]
            lbl.setStyleSheet(
                f"background:{bg};color:{fg};"
                f"border:1px solid {border};border-radius:3px;font-size:10px;"
            )

    def update_from_session(self):
        session = YlosSession.get()
        self._list.clear()
        if not session.project_path:
            return

        entities = list_project_entities(session.project_path, "asset")
        icon_map = {
            "CHARACTER":   "ARMATURE_DATA",
            "ENVIRONMENT": "WORLD",
            "PROP":        "MESH_CUBE",
        }
        for e in entities:
            item = QtWidgets.QListWidgetItem(e["name"])
            item.setData(Qt.UserRole, e["name"])
            item.setToolTip(e["type_label"])
            self._list.addItem(item)
            if e["name"] == session.current_entity:
                self._list.setCurrentItem(item)

        self._update_step_status()


# ---------------------------------------------------------------------------
# Tab: Scene
# ---------------------------------------------------------------------------

class _SceneTab(QtWidgets.QWidget):
    def __init__(self, panel: "YlosPipelinePanel"):
        super().__init__()
        self._panel = panel
        self._build()

    def _build(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # --- Actions ---
        layout.addWidget(_label("ACTIONS", dim=True))

        save_btn = _button("Save WIP  (.hip*)", primary=True)
        save_btn.clicked.connect(save_wip_dialog)
        layout.addWidget(save_btn)

        open_btn = _button("Open Root in Solaris")
        open_btn.clicked.connect(self._open_in_solaris)
        layout.addWidget(open_btn)

        layout.addSpacing(4)

        # Publish action disabled notice
        self._pub_notice = QtWidgets.QLabel(
            "Publish (S-4) requires Indie license."
        )
        self._pub_notice.setStyleSheet(
            f"color:{_C['text_dim']};font-size:11px;"
        )
        layout.addWidget(self._pub_notice)

        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet(f"color:{_C['border']};")
        layout.addWidget(line)

        # --- WIP history ---
        layout.addWidget(_label("WIP HISTORY (.hip*)", dim=True))
        self._wip_list = QtWidgets.QListWidget()
        self._wip_list.setStyleSheet(
            f"background:{_C['bg_raised']};color:{_C['text']};"
            f"border:1px solid {_C['border']};border-radius:3px;font-size:11px;"
        )
        self._wip_list.setFixedHeight(90)
        layout.addWidget(self._wip_list)

        # --- Publish history ---
        layout.addWidget(_label("PUBLISHES (USD)", dim=True))
        self._pub_list = QtWidgets.QListWidget()
        self._pub_list.setStyleSheet(
            f"background:{_C['bg_raised']};color:{_C['text']};"
            f"border:1px solid {_C['border']};border-radius:3px;font-size:11px;"
        )
        layout.addWidget(self._pub_list, 1)

    def _open_in_solaris(self):
        from .lop_utils import import_current_entity
        import_current_entity()

    def update_from_session(self):
        session = YlosSession.get()

        # Publish notice visibility
        self._pub_notice.setVisible(not can_publish())

        self._wip_list.clear()
        self._pub_list.clear()

        if not session.project_path or not session.current_entity:
            return

        # WIP history (hip files)
        wips = list_hip_wip_versions(
            session.project_path,
            session.current_entity,
            session.current_step,
            session.context_type.lower(),
        )
        for w in reversed(wips):
            self._wip_list.addItem(
                f"v{w['version']:03d}  {w['filename']}  ({w['date']})"
            )

        # Publish history (USD)
        pubs = list_publish_versions(
            session.project_path,
            session.current_entity,
            session.current_step,
            session.context_type.lower(),
        )
        for p in reversed(pubs):
            variant_str = f"  [{p['variant']}]" if p["variant"] != "Default" else ""
            self._pub_list.addItem(
                f"v{p['version']:03d}{variant_str}  {p['filename']}"
            )


# ---------------------------------------------------------------------------
# Main panel widget
# ---------------------------------------------------------------------------

class YlosPipelinePanel(QtWidgets.QWidget):
    """
    Top-level widget returned by createInterface() in the .pypanel file.
    Registered as a Python Panel in Houdini (python_panels/ylos_pipeline.pypanel).
    """

    def __init__(self):
        super().__init__()
        self._build()
        # Register for session change notifications
        YlosSession.get().register_on_change(self.refresh)
        # Initial populate
        self.refresh()

    def closeEvent(self, event):
        YlosSession.get().unregister_on_change(self.refresh)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        self.setStyleSheet(f"background:{_C['bg']};color:{_C['text']};")

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Header ---
        header = QtWidgets.QLabel("  Ylos Pipeline")
        header.setFixedHeight(30)
        header.setStyleSheet(
            f"background:{_C['bg_header']};color:{_C['accent']};"
            f"font-size:13px;font-weight:bold;"
            f"border-bottom:1px solid {_C['border']};"
        )
        root.addWidget(header)

        # --- Tabs ---
        self._tabs = QtWidgets.QTabWidget()
        self._tabs.setStyleSheet(
            f"QTabWidget::pane{{border:none;background:{_C['bg']};}}"
            f"QTabBar::tab{{background:{_C['bg_raised']};color:{_C['text_dim']};"
            f"padding:6px 14px;border:none;border-bottom:2px solid transparent;}}"
            f"QTabBar::tab:selected{{color:{_C['text']};"
            f"border-bottom:2px solid {_C['accent']};}}"
        )
        self._pipeline_tab = _PipelineTab(self)
        self._assets_tab   = _AssetsTab(self)
        self._scene_tab    = _SceneTab(self)
        self._tabs.addTab(self._pipeline_tab, "Pipeline")
        self._tabs.addTab(self._assets_tab,   "Assets")
        self._tabs.addTab(self._scene_tab,    "Scene")
        root.addWidget(self._tabs)

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------

    def refresh(self):
        """Repopulate all tabs from the current session state."""
        self._pipeline_tab.update_from_session()
        self._assets_tab.update_from_session()
        self._scene_tab.update_from_session()

    def _load_project(self):
        """Open a directory picker and load the selected project."""
        try:
            import hou
            path = hou.ui.selectFile(
                title="Select Ylos Project Folder",
                pattern="*",
                chooser_mode=hou.fileChooserMode.Directory,
            )
        except Exception:
            # Fallback to Qt dialog if hou.ui not available
            path = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Select Ylos Project Folder"
            )

        if not path:
            return

        path = path.strip()
        config = load_project(path)
        if config is None:
            try:
                import hou
                hou.ui.displayMessage(
                    f"No valid project.json found at:\n{path}",
                    severity=hou.severityType.Warning,
                    title="Ylos - Load Project",
                )
            except Exception:
                QtWidgets.QMessageBox.warning(
                    self, "Ylos - Load Project",
                    f"No valid project.json found at:\n{path}"
                )
            return

        proj = config.get("project", {})
        YlosSession.get().set_project(
            path,
            proj.get("name", ""),
            proj.get("prod_type", "FILM"),
        )


# ---------------------------------------------------------------------------
# Standalone / floating window (shelf tool entry point)
# ---------------------------------------------------------------------------

def show_floating() -> None:
    """Show the panel as a floating window. Called from the shelf tool."""
    try:
        import hou
        parent = hou.qt.mainWindow()
    except Exception:
        parent = None

    win = QtWidgets.QMainWindow(parent)
    win.setWindowTitle("Ylos Pipeline")
    win.setMinimumSize(300, 500)
    panel = YlosPipelinePanel()
    win.setCentralWidget(panel)
    win.show()
    win._ylos_panel_ref = panel   # prevent GC
