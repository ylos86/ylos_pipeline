# -*- coding: utf-8 -*-
# tests/test_project.py
import sys
import json
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from ylos_core.project import (
    create_project,
    load_project,
    find_project_root,
    is_step_valid_for_context,
    ASSET_STEPS,
    SHOT_STEPS,
    SET_STEPS,
    STEP_OWNERS,
    PIPELINE_DIR,
    PROJECT_CONFIG_FILE,
    SCHEMA_VERSION,
)
from ylos_core import SCHEMA_VERSION as CORE_SCHEMA_VERSION


class TestCreateProject:
    def test_creates_folder_structure(self, tmp_path):
        result = create_project(str(tmp_path), "TestFilm", "FILM")
        assert result["success"] is True
        project_path = Path(result["project_path"])
        assert project_path.exists()
        assert (project_path / "_pipeline" / "project.json").exists()
        assert (project_path / "assets").exists()
        assert (project_path / "shots").exists()
        assert (project_path / "sets").exists()

    def test_project_json_schema_version(self, tmp_path):
        result = create_project(str(tmp_path), "TestFilm", "FILM")
        config_path = Path(result["project_path"]) / "_pipeline" / "project.json"
        data = json.loads(config_path.read_text())
        assert data["schema_version"] == SCHEMA_VERSION

    def test_project_json_step_owners(self, tmp_path):
        result = create_project(str(tmp_path), "TestFilm", "FILM")
        config_path = Path(result["project_path"]) / "_pipeline" / "project.json"
        data = json.loads(config_path.read_text())
        assert "step_owners" in data
        assert data["step_owners"]["modeling"] == "blender"
        assert data["step_owners"]["lookdev"] == "houdini"
        assert data["step_owners"]["lighting"] == "any"

    def test_fails_on_unknown_prod_type(self, tmp_path):
        result = create_project(str(tmp_path), "Test", "UNKNOWN")
        assert result["success"] is False

    def test_fails_if_project_exists(self, tmp_path):
        create_project(str(tmp_path), "TestFilm", "FILM")
        result = create_project(str(tmp_path), "TestFilm", "FILM")
        assert result["success"] is False

    def test_project_name_in_folder(self, tmp_path):
        result = create_project(str(tmp_path), "MyProject", "AR")
        assert "YLOS_MyProject" in result["project_path"]


class TestLoadProject:
    def test_roundtrip(self, tmp_path):
        create_project(str(tmp_path), "LoadTest", "VR")
        project_path = str(tmp_path / "YLOS_LoadTest")
        data = load_project(project_path)
        assert data is not None
        assert data["project"]["name"] == "LoadTest"

    def test_returns_none_for_missing_path(self, tmp_path):
        assert load_project(str(tmp_path / "nonexistent")) is None

    def test_schema_version_forward_guard(self, tmp_path):
        create_project(str(tmp_path), "FutureTest", "FILM")
        project_path = tmp_path / "YLOS_FutureTest"
        config_path = project_path / "_pipeline" / "project.json"
        data = json.loads(config_path.read_text())
        data["schema_version"] = SCHEMA_VERSION + 99
        config_path.write_text(json.dumps(data))

        with pytest.raises(RuntimeError, match="schema_version"):
            load_project(str(project_path))

    def test_v1_project_loads_with_defaults(self, tmp_path):
        """Projects without schema_version key (v1) load without error."""
        create_project(str(tmp_path), "OldProject", "FILM")
        project_path = tmp_path / "YLOS_OldProject"
        config_path = project_path / "_pipeline" / "project.json"
        data = json.loads(config_path.read_text())
        del data["schema_version"]
        del data["step_owners"]
        config_path.write_text(json.dumps(data))

        result = load_project(str(project_path))
        assert result is not None
        assert "step_owners" in result  # back-compat injection
        assert result["step_owners"] == STEP_OWNERS

    def test_invalid_json_returns_none(self, tmp_path):
        (tmp_path / "_pipeline").mkdir()
        (tmp_path / "_pipeline" / "project.json").write_text("not json {{{")
        assert load_project(str(tmp_path)) is None


class TestFindProjectRoot:
    def test_finds_from_subfolder(self, tmp_path):
        result = create_project(str(tmp_path), "FindTest", "FILM")
        project_path = Path(result["project_path"])
        deep = project_path / "assets" / "SomeAsset" / "modeling" / "wip"
        deep.mkdir(parents=True)
        found = find_project_root(str(deep))
        assert found == str(project_path)

    def test_returns_none_when_no_project(self, tmp_path):
        assert find_project_root(str(tmp_path)) is None


class TestIsStepValidForContext:
    def test_modeling_valid_for_asset(self):
        assert is_step_valid_for_context("modeling", "asset") is True

    def test_layout_invalid_for_asset(self):
        assert is_step_valid_for_context("layout", "asset") is False

    def test_composite_valid_for_shot(self):
        assert is_step_valid_for_context("composite", "shot") is True

    def test_modeling_valid_for_set(self):
        assert is_step_valid_for_context("modeling", "set") is True

    def test_animation_invalid_for_set(self):
        assert is_step_valid_for_context("animation", "set") is False

    def test_case_insensitive(self):
        assert is_step_valid_for_context("modeling", "ASSET") is True


class TestStepConstants:
    def test_schema_version_constant(self):
        assert CORE_SCHEMA_VERSION == 2

    def test_asset_steps_order(self):
        assert ASSET_STEPS == ["modeling", "rigging", "lookdev", "fx"]

    def test_step_owners_coverage(self):
        for step in ASSET_STEPS:
            assert step in STEP_OWNERS


class TestGetStepOwner:
    def _config(self):
        return {
            "step_owners": {
                "modeling": "blender",
                "rigging":  "blender",
                "fx":       "blender",
                "lookdev":  "houdini",
                "layout":   "houdini",
                "lighting": "any",
            }
        }

    def test_blender_step(self):
        from ylos_core.project import get_step_owner
        assert get_step_owner(self._config(), "modeling") == "blender"

    def test_houdini_step(self):
        from ylos_core.project import get_step_owner
        assert get_step_owner(self._config(), "lookdev") == "houdini"

    def test_any_step(self):
        from ylos_core.project import get_step_owner
        assert get_step_owner(self._config(), "lighting") == "any"

    def test_unknown_step_returns_any(self):
        from ylos_core.project import get_step_owner
        assert get_step_owner(self._config(), "nonexistent") == "any"

    def test_empty_config_returns_module_default(self):
        from ylos_core.project import get_step_owner, STEP_OWNERS
        # Empty config falls back to module-level STEP_OWNERS, not "any"
        assert get_step_owner({}, "modeling") == STEP_OWNERS["modeling"]

    def test_truly_unknown_step_returns_any(self):
        from ylos_core.project import get_step_owner
        assert get_step_owner({}, "completelymadeupstep") == "any"

    def test_uses_default_step_owners_if_key_missing(self):
        from ylos_core.project import get_step_owner, STEP_OWNERS
        # Config without step_owners falls back to module-level STEP_OWNERS
        result = get_step_owner({}, "lookdev")
        assert result == STEP_OWNERS.get("lookdev", "any")
