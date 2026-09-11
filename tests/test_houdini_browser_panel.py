#!/usr/bin/env python3
"""
tests/test_houdini_browser_panel.py - wiring smoke test of
plugins/houdini/python/ylos_browser_panel.py (the Qt view of the Houdini cockpit).

WHAT THIS PROVES, and what it does NOT. Houdini has no license on this machine and a Qt
panel cannot be driven headlessly, so the view is loaded against a MINIMAL Qt stub
(injected as 'hutil.Qt') and its READ path is executed end to end against a real temp
project: build the widgets -> refresh() -> select an entity -> select a step -> fill the
Scenefiles / Products / Dependencies tabs.

It therefore catches exactly the class of bug that actually happens in this layer - a key
that the model does not return, a method renamed on one side only, a reload chain that
breaks on an empty project - WITHOUT pretending to test Qt itself: no layout, no painting,
no real signal/slot dispatch is verified here. Everything hou-dependent (New Scene, Save
Version, Publish, Open, the folder picker) is NOT exercised: those go through the lazy
_hou() and must be validated by hand in a licensed session (see the manual checklist).

The read path is deliberately hou-free in the panel, which is what makes this possible.

Usage: python3 -m unittest tests.test_houdini_browser_panel
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
import create_project as cp  # noqa: E402


# ---------------------------------------------------------------------------------------
# Minimal Qt stub. Only what the panel actually uses has real behaviour (item storage and
# current-item dispatch); everything else is a permissive no-op, so the stub can never
# "pass" by accident for a widget API the panel does not use.
# ---------------------------------------------------------------------------------------

class _Signal:
    def __init__(self):
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


_SIGNALS = ("clicked", "textChanged", "currentItemChanged", "itemSelectionChanged")


class _StubMeta(type):
    """Class-level attributes resolve too, so the widget ENUMS the panel uses
    (QAbstractItemView.SingleSelection, QHeaderView.ResizeToContents...) are available
    without enumerating them here - they were audited against the real hutil.Qt
    whitelist separately."""

    def __getattr__(cls, name):
        return 0


class _Stub(metaclass=_StubMeta):
    """Permissive object: any unknown attribute is a callable returning another _Stub."""

    def __init__(self, *args, **kwargs):
        self.__dict__["_signals"] = {}

    def __getattr__(self, name):
        if name in _SIGNALS:
            return self.__dict__["_signals"].setdefault(name, _Signal())
        return lambda *a, **k: _Stub()


class _StubItem(_Stub):
    """QTreeWidgetItem / QListWidgetItem: stores its column texts and its per-role data -
    the panel reads both back (entity dict on role, step name on text(0))."""

    def __init__(self, *args, **kwargs):
        _Stub.__init__(self)
        self.__dict__["_texts"] = []
        self.__dict__["_data"] = {}
        self.__dict__["_children"] = []
        parent, texts = None, None
        for arg in args:
            if isinstance(arg, (list, tuple)):
                texts = list(arg)
            elif isinstance(arg, str):
                texts = [arg]
            elif arg is not None:
                parent = arg
        self.__dict__["_texts"] = texts or []
        if parent is not None and hasattr(parent, "_add_child"):
            parent._add_child(self)

    def _add_child(self, child):
        self.__dict__["_children"].append(child)

    def text(self, column=0):
        return self._texts[column] if column < len(self._texts) else ""

    def setData(self, *args):
        # QTreeWidgetItem.setData(column, role, value) / QListWidgetItem.setData(role, value)
        if len(args) == 3:
            self.__dict__["_data"][(args[0], args[1])] = args[2]
        else:
            self.__dict__["_data"][(0, args[0])] = args[1]

    def data(self, *args):
        if len(args) == 2:
            return self.__dict__["_data"].get((args[0], args[1]))
        return self.__dict__["_data"].get((0, args[0]))


class _StubItemView(_Stub):
    """QTreeWidget / QListWidget: item list + current item, with the currentItemChanged /
    itemSelectionChanged dispatch the panel relies on (and blockSignals honoured, which is
    what keeps the reload chain from recursing)."""

    def __init__(self, *args, **kwargs):
        _Stub.__init__(self)
        self.__dict__["_items"] = []
        self.__dict__["_current"] = None
        self.__dict__["_blocked"] = False

    def _add_child(self, child):
        self.__dict__["_items"].append(child)

    def clear(self):
        self.__dict__["_items"] = []
        self.__dict__["_current"] = None

    def blockSignals(self, blocked):
        self.__dict__["_blocked"] = bool(blocked)

    def addItem(self, item):
        self.__dict__["_items"].append(item)
        if self.__dict__["_current"] is None:
            self.__dict__["_current"] = item

    def topLevelItemCount(self):
        return len(self.__dict__["_items"])

    def topLevelItem(self, index):
        return self.__dict__["_items"][index]

    def currentItem(self):
        return self.__dict__["_current"]

    def setCurrentItem(self, item):
        self.__dict__["_current"] = item
        if not self.__dict__["_blocked"]:
            self.__dict__["_signals"].setdefault(
                "currentItemChanged", _Signal()).emit(item, None)
            self.__dict__["_signals"].setdefault(
                "itemSelectionChanged", _Signal()).emit()

    # -- test helpers (not Qt API) ------------------------------------------------------
    def _all(self):
        out = []
        for item in self.__dict__["_items"]:
            out.append(item)
            out.extend(item.__dict__.get("_children", []))
        return out


class _StubLineEdit(_Stub):
    def __init__(self, *args, **kwargs):
        _Stub.__init__(self)
        self.__dict__["_text"] = ""

    def text(self):
        return self.__dict__["_text"]

    def setText(self, value):
        self.__dict__["_text"] = value
        self.__dict__["_signals"].setdefault("textChanged", _Signal()).emit(value)


class _StubComboBox(_Stub):
    def __init__(self, *args, **kwargs):
        _Stub.__init__(self)
        self.__dict__["_items"] = []

    def addItems(self, items):
        self.__dict__["_items"].extend(items)

    def currentText(self):
        return self.__dict__["_items"][0] if self.__dict__["_items"] else ""


class _StubQt:
    """hou-free subset of the Qt namespace the panel touches (UserRole must be an int:
    the panel derives its item roles from it)."""
    UserRole = 32
    ItemIsEnabled = 32
    Horizontal = 1


_EXPLICIT = {
    "QTreeWidget": _StubItemView, "QListWidget": _StubItemView,
    "QTreeWidgetItem": _StubItem, "QListWidgetItem": _StubItem,
    "QLineEdit": _StubLineEdit, "QComboBox": _StubComboBox,
}


class _StubQtModule(types.ModuleType):
    def __getattr__(self, name):
        if name == "Qt":
            return _StubQt
        cls = _EXPLICIT.get(name) or type(name, (_Stub,), {})
        setattr(self, name, cls)
        return cls


def _install_qt_stub():
    """Inject 'hutil.Qt' before the panel imports it. Returns the loaded panel module."""
    hutil = sys.modules.get("hutil") or types.ModuleType("hutil")
    qt = _StubQtModule("hutil.Qt")
    for name in ("QtCore", "QtGui", "QtWidgets"):
        setattr(qt, name, _StubQtModule("hutil.Qt." + name))
    hutil.Qt = qt
    sys.modules["hutil"] = hutil
    sys.modules["hutil.Qt"] = qt
    # the panel imports its siblings by bare name (they live together on PYTHONPATH).
    python_dir = str(_REPO_ROOT / "plugins" / "houdini" / "python")
    if python_dir not in sys.path:
        sys.path.insert(0, python_dir)
    path = _REPO_ROOT / "plugins" / "houdini" / "python" / "ylos_browser_panel.py"
    spec = importlib.util.spec_from_file_location("ylos_browser_panel", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


panel_module = _install_qt_stub()


class PanelReadPathTestCase(unittest.TestCase):
    """Build the panel and walk its whole read path on a real project."""

    ASSET = "CHARACTER_Lina_Default"
    SHOT = "ANIMATION_Sq010_Default"

    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="ylos_panel_")).resolve()
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        info = cp.create("Proj", root=self._tmp / "src", cache=self._tmp / "cache")
        self.project = Path(info["source"])
        cp.create_asset(self.project, self.ASSET, entity_type="asset",
                        asset_type="CHARACTER")
        cp.create_asset(self.project, self.SHOT, entity_type="shot",
                        asset_type="ANIMATION")
        wip = self.project / "assets" / self.ASSET / "modeling" / "wip"
        wip.mkdir(parents=True, exist_ok=True)
        hip = wip / f"{self.ASSET}_modeling_v001.hipnc"
        hip.write_text("", encoding="utf-8")
        (wip / (hip.name + ".json")).write_text(
            json.dumps({"comment": "blocking", "user": "seb"}), encoding="utf-8")
        stem = f"{self.ASSET}_modeling_v001"
        manifest_path = self.project / "assets" / self.ASSET / cp.ASSET_MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest[cp.STEP_PUBLISHES_KEY] = {"modeling": [
            {"version": 1, "status": "complete",
             "artifact": f"modeling/publish/{stem}/{stem}.usda"}]}
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        # the panel reads the ACTIVE project; point it at ours for the duration.
        self.active = self._tmp / "active_project"
        self.active.write_text(str(self.project) + "\n", encoding="utf-8")
        real = panel_module.model.active_project_info
        panel_module.model.active_project_info = (
            lambda active_file=None, _r=real, _a=self.active: _r(_a))
        self.addCleanup(setattr, panel_module.model, "active_project_info", real)

    def _panel(self):
        return panel_module.YlosBrowserPanel()

    def test_panel_builds_and_loads_the_active_project(self):
        panel = self._panel()
        self.assertEqual(panel._project, str(self.project))

    def test_entities_are_grouped_and_selecting_one_loads_its_steps(self):
        panel = self._panel()
        labels = [i.text(0) for i in panel.entity_tree._all()]
        self.assertIn("Assets", labels)
        self.assertIn("Shots", labels)
        self.assertIn(self.ASSET, labels)
        # selecting an entity must cascade: entity -> steps -> tabs, with no exception.
        item = next(i for i in panel.entity_tree._all() if i.text(0) == self.ASSET)
        panel.entity_tree.setCurrentItem(item)
        self.assertEqual(panel._entity["name"], self.ASSET)
        self.assertEqual([i.text(0) for i in panel.step_tree._all()],
                         ["modeling", "rigging", "lookdev", "fx"])
        # the first step is auto-selected, so the tabs are never left stale/empty.
        self.assertEqual(panel._step, "modeling")

    def test_tabs_are_filled_for_the_selected_step(self):
        panel = self._panel()
        item = next(i for i in panel.entity_tree._all() if i.text(0) == self.ASSET)
        panel.entity_tree.setCurrentItem(item)
        scenefiles = [i.text(0) for i in panel.scenefile_list._all()]
        self.assertEqual(len(scenefiles), 1)
        self.assertIn("blocking", scenefiles[0])
        products = [i.text(0) for i in panel.product_list._all()]
        self.assertEqual(len(products), 1)
        self.assertIn("latest", products[0])
        headers = [i.text(0) for i in panel.dependency_tree._all()]
        self.assertTrue(any(h.startswith("Used in") for h in headers), headers)
        self.assertTrue(any(h.startswith("Uses") for h in headers), headers)

    def test_search_filters_the_tree(self):
        panel = self._panel()
        panel.search_field.setText("barrel_that_does_not_exist")
        self.assertEqual(panel.entity_tree._all(), [])
        self.assertIsNone(panel._entity)      # stale selection dropped, no dangling state
        panel.search_field.setText(self.SHOT)
        self.assertEqual([i.text(0) for i in panel.entity_tree._all()],
                         ["Shots", self.SHOT])

    def test_selection_survives_a_refresh(self):
        panel = self._panel()
        item = next(i for i in panel.entity_tree._all() if i.text(0) == self.ASSET)
        panel.entity_tree.setCurrentItem(item)
        step_item = next(i for i in panel.step_tree._all() if i.text(0) == "lookdev")
        panel.step_tree.setCurrentItem(step_item)
        self.assertEqual(panel._step, "lookdev")
        panel.refresh()      # an action ends by refreshing: the artist must not lose place
        self.assertEqual(panel._entity["name"], self.ASSET)
        self.assertEqual(panel._step, "lookdev")

    def test_empty_project_does_not_break_the_reload_chain(self):
        empty = Path(cp.create("Empty", root=self._tmp / "src2",
                               cache=self._tmp / "cache2")["source"])
        self.active.write_text(str(empty) + "\n", encoding="utf-8")
        panel = self._panel()
        self.assertEqual(panel.entity_tree._all(), [])
        self.assertIsNone(panel._entity)
        self.assertIsNone(panel._step)

    def test_no_active_project_does_not_break_the_panel(self):
        self.active.write_text("", encoding="utf-8")
        panel = self._panel()
        self.assertIsNone(panel._project)
        self.assertEqual(panel.entity_tree._all(), [])


if __name__ == "__main__":
    unittest.main()
