# -*- coding: utf-8 -*-
# tests/test_manifest.py
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from ylos_core.manifest import (
    write_publish_sidecar,
    read_publish_sidecar,
    get_published_prim_paths,
    find_removed_prims,
    sidecar_path,
    SIDECAR_SUFFIX,
)


@pytest.fixture
def publish_path(tmp_path):
    p = tmp_path / "CHAR_Hero_modeling_v001.usd"
    p.touch()
    return str(p)


@pytest.fixture
def sample_prims():
    return ["/ROOT/CHAR_Hero/GEO_Body", "/ROOT/CHAR_Hero/GEO_Head"]


class TestSidecarPath:
    def test_suffix_appended(self, publish_path):
        assert sidecar_path(publish_path) == publish_path + SIDECAR_SUFFIX


class TestWritePublishSidecar:
    def test_creates_sidecar_file(self, publish_path, sample_prims):
        write_publish_sidecar(
            publish_path, "CHAR_Hero", "modeling", 1,
            "blender", "4.2.3", sample_prims,
        )
        sc = Path(sidecar_path(publish_path))
        assert sc.exists()

    def test_sidecar_content(self, publish_path, sample_prims):
        write_publish_sidecar(
            publish_path, "CHAR_Hero", "modeling", 1,
            "blender", "4.2.3", sample_prims,
            variant=None, source_wip="CHAR_Hero_modeling_v005.blend",
        )
        data = json.loads(Path(sidecar_path(publish_path)).read_text())
        assert data["entity"] == "CHAR_Hero"
        assert data["step"] == "modeling"
        assert data["version"] == 1
        assert data["dcc"] == "blender"
        assert data["dcc_version"] == "4.2.3"
        assert data["prim_paths"] == sample_prims
        assert data["source_wip"] == "CHAR_Hero_modeling_v005.blend"
        assert data["schema_version"] == 1

    def test_immutability_guard(self, publish_path, sample_prims):
        write_publish_sidecar(
            publish_path, "CHAR_Hero", "modeling", 1,
            "blender", "4.2.3", sample_prims,
        )
        with pytest.raises(FileExistsError):
            write_publish_sidecar(
                publish_path, "CHAR_Hero", "modeling", 1,
                "blender", "4.2.3", sample_prims,
            )

    def test_with_frame_range(self, tmp_path, sample_prims):
        p = tmp_path / "CHAR_Hero_fx_v001.usd"
        p.touch()
        write_publish_sidecar(
            str(p), "CHAR_Hero", "fx", 1,
            "blender", "4.2.3", sample_prims,
            frame_range=[1001, 1100],
        )
        data = read_publish_sidecar(str(p))
        assert data["frame_range"] == [1001, 1100]

    def test_with_variant(self, tmp_path, sample_prims):
        p = tmp_path / "CHAR_Hero_lookdev_v001__Dirty.usd"
        p.touch()
        write_publish_sidecar(
            str(p), "CHAR_Hero", "lookdev", 1,
            "houdini", "21.0.631", sample_prims,
            variant="Dirty",
        )
        data = read_publish_sidecar(str(p))
        assert data["variant"] == "Dirty"

    def test_timestamp_format(self, publish_path, sample_prims):
        write_publish_sidecar(
            publish_path, "CHAR_Hero", "modeling", 1,
            "blender", "4.2.3", sample_prims,
        )
        data = read_publish_sidecar(publish_path)
        ts = data["timestamp"]
        # Should be ISO 8601 UTC: 2026-06-10T14:22:31Z
        import re
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", ts)


class TestReadPublishSidecar:
    def test_returns_none_for_missing(self, tmp_path):
        assert read_publish_sidecar(str(tmp_path / "nonexistent.usd")) is None

    def test_roundtrip(self, publish_path, sample_prims):
        write_publish_sidecar(
            publish_path, "CHAR_Hero", "modeling", 1,
            "blender", "4.2.3", sample_prims,
        )
        data = read_publish_sidecar(publish_path)
        assert data["prim_paths"] == sample_prims


class TestGetPublishedPrimPaths:
    def test_returns_empty_when_no_sidecar(self, tmp_path):
        assert get_published_prim_paths(str(tmp_path / "missing.usd")) == []

    def test_returns_prim_paths(self, publish_path, sample_prims):
        write_publish_sidecar(
            publish_path, "CHAR_Hero", "modeling", 1,
            "blender", "4.2.3", sample_prims,
        )
        result = get_published_prim_paths(publish_path)
        assert result == sample_prims


class TestFindRemovedPrims:
    def test_no_removals(self, publish_path, sample_prims):
        write_publish_sidecar(
            publish_path, "CHAR_Hero", "modeling", 1,
            "blender", "4.2.3", sample_prims,
        )
        removed = find_removed_prims(publish_path, sample_prims)
        assert removed == []

    def test_detects_removal(self, publish_path, sample_prims):
        write_publish_sidecar(
            publish_path, "CHAR_Hero", "modeling", 1,
            "blender", "4.2.3", sample_prims,
        )
        # New publish drops GEO_Head
        new_prims = ["/ROOT/CHAR_Hero/GEO_Body"]
        removed = find_removed_prims(publish_path, new_prims)
        assert "/ROOT/CHAR_Hero/GEO_Head" in removed
        assert "/ROOT/CHAR_Hero/GEO_Body" not in removed

    def test_additions_not_reported(self, publish_path, sample_prims):
        write_publish_sidecar(
            publish_path, "CHAR_Hero", "modeling", 1,
            "blender", "4.2.3", sample_prims,
        )
        new_prims = sample_prims + ["/ROOT/CHAR_Hero/GEO_Teeth"]
        removed = find_removed_prims(publish_path, new_prims)
        assert removed == []

    def test_missing_sidecar_returns_empty(self, tmp_path):
        removed = find_removed_prims(
            str(tmp_path / "nonexistent.usd"),
            ["/ROOT/Entity/GEO_Body"]
        )
        assert removed == []
