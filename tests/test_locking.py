# -*- coding: utf-8 -*-
# tests/test_locking.py
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from ylos_core.locking import atomic_write_text, atomic_write_json


class TestAtomicWriteText:
    def test_writes_content(self, tmp_path):
        p = tmp_path / "test.txt"
        atomic_write_text(str(p), "hello world")
        assert p.read_text() == "hello world"

    def test_no_tmp_file_left(self, tmp_path):
        p = tmp_path / "test.txt"
        atomic_write_text(str(p), "content")
        assert not (tmp_path / "test.txt.tmp").exists()

    def test_overwrites_existing(self, tmp_path):
        p = tmp_path / "mutable.usda"
        atomic_write_text(str(p), "first version")
        atomic_write_text(str(p), "second version")
        assert p.read_text() == "second version"

    def test_usda_content(self, tmp_path):
        p = tmp_path / "root.usda"
        usda = '#usda 1.0\n(\n    defaultPrim = "ROOT"\n)\n'
        atomic_write_text(str(p), usda)
        assert p.read_text() == usda

    def test_encoding(self, tmp_path):
        p = tmp_path / "utf8.txt"
        atomic_write_text(str(p), "test \u00e9\u00e0\u00fc", encoding="utf-8")
        assert p.read_text(encoding="utf-8") == "test \u00e9\u00e0\u00fc"


class TestAtomicWriteJson:
    def test_writes_valid_json(self, tmp_path):
        p = tmp_path / "config.json"
        data = {"key": "value", "number": 42}
        atomic_write_json(str(p), data)
        loaded = json.loads(p.read_text())
        assert loaded == data

    def test_no_tmp_file_left(self, tmp_path):
        p = tmp_path / "config.json"
        atomic_write_json(str(p), {})
        assert not (tmp_path / "config.json.tmp").exists()

    def test_overwrites_existing(self, tmp_path):
        p = tmp_path / "config.json"
        atomic_write_json(str(p), {"v": 1})
        atomic_write_json(str(p), {"v": 2})
        assert json.loads(p.read_text())["v"] == 2

    def test_nested_dict(self, tmp_path):
        p = tmp_path / "nested.json"
        data = {"project": {"name": "Test", "steps": [1, 2, 3]}}
        atomic_write_json(str(p), data)
        loaded = json.loads(p.read_text())
        assert loaded["project"]["steps"] == [1, 2, 3]
