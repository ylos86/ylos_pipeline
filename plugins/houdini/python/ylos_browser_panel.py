"""ylos_browser_panel.py - Qt view of the Ylos Project Browser (Houdini Python Panel
cockpit, plan-usable-v1 Phase 4.1). Registered by
plugins/houdini/python_panels/ylos_browser.pypanel (found via HOUDINI_PATH, package
ylos.json), which calls createInterface().

STRICT split, the reason this file can exist on a machine without a Houdini license:
  - ylos_browser_model.py  -> WHAT to show (pure, orchestrator-backed, unit-tested);
  - ylos_houdini.py        -> WHAT to do in the scene (bridge, hou);
  - this file              -> only widgets, signals and error reporting. It derives NO
                              pipeline data of its own: every row it displays comes from
                              the model, every scene mutation goes through the bridge.

Qt is imported through `hutil.Qt` (SideFX shim; PySide6 on Houdini 21) rather than
PySide6 directly, so the panel keeps working if SideFX changes binding again. `hou` is
imported LAZILY inside the handlers - importing this module must not require a session.
"""

from __future__ import annotations

from pathlib import Path

from hutil.Qt import QtCore, QtGui, QtWidgets

import ylos_browser_model as model
import ylos_houdini as bridge

_THUMB_SIZE = 44
_ENTITY_ROLE = QtCore.Qt.UserRole + 1     # entity dict on a tree item
_PATH_ROLE = QtCore.Qt.UserRole + 2       # scenefile / product absolute path


def _hou():
    """Lazy hou (see module docstring)."""
    import hou
    return hou


class YlosBrowserPanel(QtWidgets.QWidget):
    """Mini Project Browser inside Houdini: active project -> entity -> step ->
    scenefiles / products / dependencies, plus the four gestures of a work session
    (New Scene, Save Version, Publish, Open)."""

    def __init__(self, parent=None):
        super(YlosBrowserPanel, self).__init__(parent)
        self._project = None          # str | None - active project root
        self._entity = None           # entity row dict | None
        self._step = None             # str | None
        self._build_ui()
        self.refresh()

    # -- construction -------------------------------------------------------------------

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        # Header: active project + the two global actions.
        header = QtWidgets.QHBoxLayout()
        self.project_label = QtWidgets.QLabel("No active project")
        font = self.project_label.font()
        font.setBold(True)
        self.project_label.setFont(font)
        self.project_path_label = QtWidgets.QLabel("")
        self.project_path_label.setEnabled(False)
        change_btn = QtWidgets.QPushButton("Change project...")
        change_btn.clicked.connect(self._on_change_project)
        refresh_btn = QtWidgets.QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        header.addWidget(self.project_label)
        header.addWidget(self.project_path_label, 1)
        header.addWidget(change_btn)
        header.addWidget(refresh_btn)
        root.addLayout(header)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        root.addWidget(splitter, 1)

        # Left: search + entities by family.
        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        self.search_field = QtWidgets.QLineEdit()
        self.search_field.setPlaceholderText("Filter entities (name or type)")
        # textChanged carries the new text while _reload_entities() takes none, and it
        # re-reads the field itself. Explicit lambda rather than relying on the binding's
        # argument-count adaptation, which differs across PySide2/PySide6/PyQt.
        self.search_field.textChanged.connect(lambda _text: self._reload_entities())
        self.entity_tree = QtWidgets.QTreeWidget()
        self.entity_tree.setHeaderHidden(True)
        self.entity_tree.setIconSize(QtCore.QSize(_THUMB_SIZE, _THUMB_SIZE))
        self.entity_tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.entity_tree.currentItemChanged.connect(self._on_entity_changed)
        left_layout.addWidget(self.search_field)
        left_layout.addWidget(self.entity_tree, 1)
        splitter.addWidget(left)

        # Right: steps + detail tabs + actions.
        right = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)

        self.entity_label = QtWidgets.QLabel("Select an entity")
        right_layout.addWidget(self.entity_label)

        self.step_tree = QtWidgets.QTreeWidget()
        self.step_tree.setHeaderLabels(["Step", "Status", "Scenefiles", "Products"])
        self.step_tree.setRootIsDecorated(False)
        self.step_tree.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.step_tree.currentItemChanged.connect(self._on_step_changed)
        self.step_tree.header().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents)
        right_layout.addWidget(self.step_tree, 1)

        status_row = QtWidgets.QHBoxLayout()
        status_row.addWidget(QtWidgets.QLabel("Step status:"))
        self.status_combo = QtWidgets.QComboBox()
        self.status_combo.addItems(list(model.STATUS_CHOICES))
        status_row.addWidget(self.status_combo)
        set_status_btn = QtWidgets.QPushButton("Set")
        set_status_btn.clicked.connect(self._on_set_status)
        status_row.addWidget(set_status_btn)
        status_row.addStretch(1)
        right_layout.addLayout(status_row)

        self.tabs = QtWidgets.QTabWidget()
        right_layout.addWidget(self.tabs, 2)

        # -- Scenefiles tab
        scenes = QtWidgets.QWidget()
        scenes_layout = QtWidgets.QVBoxLayout(scenes)
        scenes_layout.setContentsMargins(4, 4, 4, 4)
        self.scenefile_list = QtWidgets.QListWidget()
        self.scenefile_list.itemSelectionChanged.connect(self._sync_buttons)
        scenes_layout.addWidget(self.scenefile_list, 1)
        self.open_btn = QtWidgets.QPushButton("Open")
        self.open_btn.clicked.connect(self._on_open_scenefile)
        scenes_layout.addWidget(self.open_btn)
        self.tabs.addTab(scenes, "Scenefiles")

        # -- Products tab
        products = QtWidgets.QWidget()
        products_layout = QtWidgets.QVBoxLayout(products)
        products_layout.setContentsMargins(4, 4, 4, 4)
        self.product_list = QtWidgets.QListWidget()
        self.product_list.itemSelectionChanged.connect(self._sync_buttons)
        products_layout.addWidget(self.product_list, 1)
        product_buttons = QtWidgets.QHBoxLayout()
        self.sublayer_btn = QtWidgets.QPushButton("Sublayer")
        self.sublayer_btn.clicked.connect(self._on_sublayer)
        self.reference_btn = QtWidgets.QPushButton("Reference into /stage")
        self.reference_btn.clicked.connect(self._on_reference)
        product_buttons.addWidget(self.sublayer_btn)
        product_buttons.addWidget(self.reference_btn)
        product_buttons.addStretch(1)
        products_layout.addLayout(product_buttons)
        self.tabs.addTab(products, "Products")

        # -- Dependencies tab
        deps = QtWidgets.QWidget()
        deps_layout = QtWidgets.QVBoxLayout(deps)
        deps_layout.setContentsMargins(4, 4, 4, 4)
        self.dependency_tree = QtWidgets.QTreeWidget()
        self.dependency_tree.setHeaderLabels(["Dependency", "Source"])
        self.dependency_tree.header().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents)
        deps_layout.addWidget(self.dependency_tree, 1)
        self.tabs.addTab(deps, "Dependencies")

        # Actions on the selected entity+step.
        actions = QtWidgets.QHBoxLayout()
        self.new_scene_btn = QtWidgets.QPushButton("New Scene")
        self.new_scene_btn.clicked.connect(self._on_new_scene)
        self.save_btn = QtWidgets.QPushButton("Save Version")
        self.save_btn.clicked.connect(self._on_save_version)
        self.publish_btn = QtWidgets.QPushButton("Publish")
        self.publish_btn.clicked.connect(self._on_publish)
        for btn in (self.new_scene_btn, self.save_btn, self.publish_btn):
            actions.addWidget(btn)
        actions.addStretch(1)
        right_layout.addLayout(actions)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        self.message_label = QtWidgets.QLabel("")
        self.message_label.setWordWrap(True)
        root.addWidget(self.message_label)

    # -- reporting ----------------------------------------------------------------------

    def _say(self, text):
        self.message_label.setText(text)

    def _fail(self, exc, title="Ylos"):
        """An error is shown BOTH in the panel (persistent trace) and in a hou dialog (the
        artist may be looking at the viewport, not at the panel)."""
        self.message_label.setText("Error: {}".format(exc))
        hou = _hou()
        hou.ui.displayMessage(str(exc), severity=hou.severityType.Error, title=title)

    # -- data loading -------------------------------------------------------------------

    def refresh(self):
        """Full reload: active project -> entities -> steps -> tabs. The only entry point
        that re-reads the disk; every action ends by calling it."""
        info = model.active_project_info()
        self._project = info["path"] if info["exists"] else None
        if info["exists"]:
            self.project_label.setText(info["name"])
            self.project_path_label.setText(info["path"])
        elif info["path"]:
            self.project_label.setText("Invalid project")
            self.project_path_label.setText(
                "{} (no project manifest)".format(info["path"]))
        else:
            self.project_label.setText("No active project")
            self.project_path_label.setText(
                "Set one with 'Change project...' or from the web UI")
        self._reload_entities()

    def _reload_entities(self):
        wanted = self._entity["name"] if self._entity else None
        self.entity_tree.blockSignals(True)
        self.entity_tree.clear()
        restored = None
        if self._project:
            for group in model.entity_groups(self._project, self.search_field.text()):
                parent = QtWidgets.QTreeWidgetItem(self.entity_tree, [group["label"]])
                parent.setExpanded(True)
                parent.setFlags(QtCore.Qt.ItemIsEnabled)
                for row in group["rows"]:
                    item = QtWidgets.QTreeWidgetItem(parent, [row["label"]])
                    item.setData(0, _ENTITY_ROLE, row)
                    thumb = (row.get("thumbnail") or {}).get("path")
                    if thumb and Path(thumb).is_file():
                        item.setIcon(0, QtGui.QIcon(thumb))
                    if row.get("broken"):
                        item.setToolTip(0, str(row["broken"]))
                    if row["name"] == wanted:
                        restored = item
        self.entity_tree.blockSignals(False)
        if restored is not None:
            self.entity_tree.setCurrentItem(restored)
        else:
            self._entity = None
            self._reload_steps()

    def _reload_steps(self):
        wanted = self._step
        self.step_tree.blockSignals(True)
        self.step_tree.clear()
        restored = None
        if self._entity and self._project:
            self.entity_label.setText("{}  -  {} ({})".format(
                self._entity["name"], self._entity["family_label"],
                self._entity["entity_type"] or "?"))
            for row in model.step_rows(self._project, self._entity["name"]):
                label = row["status"] + ("" if row["explicit"] else "  (auto)")
                item = QtWidgets.QTreeWidgetItem(self.step_tree, [
                    row["step"], label, str(row["scenefiles"]), str(row["products"])])
                item.setForeground(1, QtGui.QBrush(QtGui.QColor(row["color"])))
                if row["step"] == wanted:
                    restored = item
        else:
            self.entity_label.setText("Select an entity")
        self.step_tree.blockSignals(False)
        if restored is not None:
            self.step_tree.setCurrentItem(restored)
        elif self.step_tree.topLevelItemCount():
            self.step_tree.setCurrentItem(self.step_tree.topLevelItem(0))
        else:
            self._step = None
            self._reload_tabs()

    def _reload_tabs(self):
        self.scenefile_list.clear()
        self.product_list.clear()
        self.dependency_tree.clear()
        if not (self._entity and self._step and self._project):
            self._sync_buttons()
            return
        entity = self._entity["name"]

        for row in model.scenefile_rows(self._project, entity, self._step):
            item = QtWidgets.QListWidgetItem(row["label"])
            item.setData(_PATH_ROLE, row["path"] if row["openable"] else None)
            if not row["openable"]:
                item.setToolTip("{} - not a Houdini scenefile".format(row["filename"]))
            self.scenefile_list.addItem(item)

        for row in model.product_rows(self._project, entity, self._step):
            item = QtWidgets.QListWidgetItem(row["label"])
            item.setData(_PATH_ROLE, row.get("abs_path"))
            self.product_list.addItem(item)

        deps = model.dependency_rows(self._project, entity)
        for key, title in (("used_in", "Used in"), ("uses", "Uses")):
            parent = QtWidgets.QTreeWidgetItem(
                self.dependency_tree, ["{} ({})".format(title, len(deps[key]))])
            parent.setExpanded(True)
            for row in deps[key]:
                label = row["label"]
                if row["update_available"]:
                    label += "   UPDATE AVAILABLE (latest v{:03d})".format(
                        row["latest_version"])
                child = QtWidgets.QTreeWidgetItem(parent, [label, row["source"]])
                if row["update_available"]:
                    child.setForeground(
                        0, QtGui.QBrush(QtGui.QColor(model.STATUS_COLORS["review"])))
        self._sync_buttons()

    # -- selection ----------------------------------------------------------------------

    def _on_entity_changed(self, current, _previous=None):
        row = current.data(0, _ENTITY_ROLE) if current is not None else None
        previous_name = self._entity["name"] if self._entity else None
        new_name = row["name"] if row else None
        self._entity = row
        if new_name != previous_name:
            # Only a DIFFERENT entity invalidates the step (another entity, other steps).
            # Re-selecting the same one must not: every action ends with refresh(), which
            # goes through this handler - resetting here would bounce the artist back to
            # the first step after each Save Version / New Scene.
            self._step = None
        self._reload_steps()

    def _on_step_changed(self, current, _previous=None):
        self._step = current.text(0) if current is not None else None
        self._reload_tabs()

    def _sync_buttons(self):
        has_target = bool(self._project and self._entity and self._step
                          and not (self._entity or {}).get("broken"))
        for btn in (self.new_scene_btn, self.save_btn, self.publish_btn,
                    self.sublayer_btn, self.reference_btn):
            btn.setEnabled(has_target)
        scenefile = self.scenefile_list.currentItem()
        self.open_btn.setEnabled(
            bool(scenefile is not None and scenefile.data(_PATH_ROLE)))

    # -- actions ------------------------------------------------------------------------

    def _on_change_project(self):
        hou = _hou()
        start = self._project or str(Path.home())
        try:
            chosen = hou.ui.selectFile(
                start_directory=start, title="Ylos - choose the active project",
                file_type=hou.fileType.Directory,
                chooser_mode=hou.fileChooserMode.Read)
        except hou.Error as exc:
            self._fail(exc)
            return
        if not chosen:
            return
        try:
            root = model.set_active_project(hou.text.expandString(chosen))
        except (ValueError, OSError) as exc:
            self._fail(exc)
            return
        self._entity = None
        self._step = None
        self._say("Active project: {}".format(root))
        self.refresh()

    def _on_set_status(self):
        if not (self._entity and self._step):
            return
        choice = self.status_combo.currentText()
        try:
            info = model.set_status(
                self._project, self._entity["name"], self._step, choice)
        except (ValueError, FileNotFoundError, OSError) as exc:
            self._fail(exc)
            return
        self._say("{} / {}: {}{}".format(
            self._entity["name"], self._step, info["status"],
            "" if info["explicit"] else " (derived)"))
        self._reload_steps()

    def _on_open_scenefile(self):
        item = self.scenefile_list.currentItem()
        path = item.data(_PATH_ROLE) if item is not None else None
        if not path:
            return
        hou = _hou()
        try:
            # suppress_save_prompt left at its default: the artist is asked to save the
            # current session before it is replaced - never a silent loss of work.
            hou.hipFile.load(str(path))
        except hou.LoadWarning as exc:
            self._say("Opened with warnings: {}".format(exc))
            return
        except hou.Error as exc:
            self._fail(exc)
            return
        self._say("Opened {}".format(path))

    def _on_sublayer(self):
        try:
            node = bridge.sublayer_step_publish(
                self._entity["name"], self._step, self._project)
        except (ValueError, FileNotFoundError, OSError) as exc:
            self._fail(exc)
            return
        self._say("Sublayer created: {}".format(node.path()))

    def _on_reference(self):
        try:
            node = bridge.reference_asset(self._entity["name"], self._project)
        except (ValueError, FileNotFoundError, OSError) as exc:
            self._fail(exc)
            return
        self._say("Reference created: {}".format(node.path()))

    def _on_new_scene(self):
        hou = _hou()
        preview = model.starter_preview(self._project, self._entity["name"], self._step)
        if not preview["ok"]:
            self._fail(preview["reason"], title="New Scene")
            return
        confirmed = hou.ui.displayConfirmation(
            "{} / {}\n{}\n\nThe current scene will be cleared. Continue?".format(
                self._entity["name"], self._step, preview["label"]),
            title="New Scene")
        if not confirmed:
            return
        comment = self._ask_comment("New Scene",
                                   "Comment for this first version (optional):")
        if comment is None:
            return
        try:
            info = bridge.create_scene(self._entity["name"], self._step,
                                       self._project, comment=comment)
        except (ValueError, FileNotFoundError, OSError) as exc:
            self._fail(exc, title="New Scene")
            return
        self._say("Scene v{:03d} created: {}{}".format(
            info["version"], info["path"],
            "  |  warnings: " + "; ".join(info["warnings"]) if info["warnings"] else ""))
        self.refresh()

    def _on_save_version(self):
        comment = self._ask_comment("Save Version")
        if comment is None:
            return
        hou = _hou()
        try:
            if bridge.parse_wip_context(hou.hipFile.path()) is not None:
                info = bridge.save_wip(comment=comment)
            else:
                info = bridge.save_wip(self._entity["name"], self._step,
                                       self._project, comment=comment)
        except (ValueError, FileNotFoundError, OSError) as exc:
            self._fail(exc, title="Save Version")
            return
        self._say("WIP v{:03d} saved: {}".format(info["version"], info["path"]))
        self.refresh()

    def _on_publish(self):
        try:
            node = bridge.create_publish_node(
                self._entity["name"], self._step, self._project)
        except (ValueError, RuntimeError, FileNotFoundError, OSError) as exc:
            self._fail(exc, title="Publish")
            return
        self._say("Publish node ready: {} - check the stage, then press its Publish "
                  "button.".format(node.path()))

    def _ask_comment(self, title, message="Version comment (optional):"):
        hou = _hou()
        ok, text = hou.ui.readInput(message, buttons=("Save", "Cancel"),
                                    close_choice=1, title=title, initial_contents="")
        return None if ok != 0 else (text or "").strip()


def createInterface():
    """Entry point of plugins/houdini/python_panels/ylos_browser.pypanel."""
    return YlosBrowserPanel()
