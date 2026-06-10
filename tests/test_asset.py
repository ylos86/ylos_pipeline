# -*- coding: utf-8 -*-
# tests/test_asset.py
import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from ylos_core.project import create_project, ASSET_STEPS
from ylos_core.asset import (
    create_asset,
    create_shot,
    create_set,
    list_wip_versions,
    list_publish_versions,
    get_latest_wip_version,
    get_latest_publish_version,
    resolve_wip_save_path,
    resolve_publish_path,
    build_wip_filename,
    build_publish_filename,
    get_asset_root,
    invalidate_entity_cache,
    list_project_entities,
    get_asset_step_status,
    ASSET_TYPE_PREFIXES,
    ASSET_TYPE_PARENT_COL,
)


@pytest.fixture
def project(tmp_path):
    result = create_project(str(tmp_path), "TestProj", "FILM")
    return result["project_path"]


class TestCreateAsset:
    def test_creates_step_dirs(self, project):
        result = create_asset(project, "HeroChar", asset_type="CHARACTER")
        assert result["success"] is True
        asset_root = Path(project) / "assets" / "HeroChar"
        for step in ASSET_STEPS:
            assert (asset_root / step / "wip").exists()
            assert (asset_root / step / "publish").exists()

    def test_manifest_json_written(self, project):
        create_asset(project, "PropBox", asset_type="PROP")
        mf = json.loads((Path(project) / "assets" / "PropBox" / "manifest.json").read_text())
        assert mf["name"] == "PropBox"
        assert mf["type"] == "PROP"

    def test_fails_if_exists(self, project):
        create_asset(project, "HeroChar")
        result = create_asset(project, "HeroChar")
        assert result["success"] is False

    def test_character_type(self, project):
        create_asset(project, "HeroChar", asset_type="CHARACTER")
        mf = json.loads((Path(project) / "assets" / "HeroChar" / "manifest.json").read_text())
        assert mf["type"] == "CHARACTER"


class TestFilenameBuilders:
    def test_wip_filename(self):
        assert build_wip_filename("HeroChar", "modeling", 1) == "HeroChar_modeling_v001.blend"

    def test_wip_filename_padded(self):
        assert build_wip_filename("HeroChar", "modeling", 12) == "HeroChar_modeling_v012.blend"

    def test_publish_filename_default(self):
        assert build_publish_filename("HeroChar", "modeling", 3) == "HeroChar_modeling_v003.usd"

    def test_publish_filename_variant(self):
        assert build_publish_filename("HeroChar", "lookdev", 1, variant="Dirty") == \
               "HeroChar_lookdev_v001__Dirty.usd"

    def test_publish_filename_default_variant_ignored(self):
        # variant="Default" or "" should not appear in filename
        assert build_publish_filename("HeroChar", "modeling", 1, variant="Default") == \
               "HeroChar_modeling_v001.usd"
        assert build_publish_filename("HeroChar", "modeling", 1, variant="") == \
               "HeroChar_modeling_v001.usd"


class TestVersionDetection:
    def _make_wip(self, project, asset, step, version):
        p = Path(project) / "assets" / asset / step / "wip"
        p.mkdir(parents=True, exist_ok=True)
        (p / build_wip_filename(asset, step, version)).touch()

    def _make_publish(self, project, asset, step, version, variant=""):
        p = Path(project) / "assets" / asset / step / "publish"
        p.mkdir(parents=True, exist_ok=True)
        (p / build_publish_filename(asset, step, version, variant=variant)).touch()

    def test_list_wip_versions_empty(self, project):
        create_asset(project, "HeroChar")
        assert list_wip_versions(project, "HeroChar", "modeling") == []

    def test_list_wip_versions_sorted(self, project):
        create_asset(project, "HeroChar")
        for v in (3, 1, 2):
            self._make_wip(project, "HeroChar", "modeling", v)
        versions = list_wip_versions(project, "HeroChar", "modeling")
        assert [v["version"] for v in versions] == [1, 2, 3]

    def test_get_latest_wip_zero_when_empty(self, project):
        create_asset(project, "HeroChar")
        assert get_latest_wip_version(project, "HeroChar", "modeling") == 0

    def test_get_latest_wip(self, project):
        create_asset(project, "HeroChar")
        for v in (1, 2, 5):
            self._make_wip(project, "HeroChar", "modeling", v)
        assert get_latest_wip_version(project, "HeroChar", "modeling") == 5

    def test_list_publish_versions_with_variants(self, project):
        create_asset(project, "HeroChar")
        self._make_publish(project, "HeroChar", "lookdev", 1, "Default")
        self._make_publish(project, "HeroChar", "lookdev", 1, "Dirty")
        versions = list_publish_versions(project, "HeroChar", "lookdev")
        assert len(versions) == 2
        variants = {v["variant"] for v in versions}
        assert "Default" in variants
        assert "Dirty" in variants

    def test_get_latest_publish_version(self, project):
        create_asset(project, "HeroChar")
        for v in (1, 2, 3):
            self._make_publish(project, "HeroChar", "modeling", v)
        assert get_latest_publish_version(project, "HeroChar", "modeling") == 3


class TestResolvePaths:
    def test_resolve_wip_path(self, project):
        create_asset(project, "PropBox")
        p = resolve_wip_save_path(project, "PropBox", "modeling", 1)
        assert p.endswith("PropBox_modeling_v001.blend")
        assert "wip" in p

    def test_resolve_publish_path(self, project):
        create_asset(project, "PropBox")
        p = resolve_publish_path(project, "PropBox", "modeling", 1)
        assert p.endswith("PropBox_modeling_v001.usd")
        assert "publish" in p

    def test_resolve_publish_with_variant(self, project):
        create_asset(project, "PropBox")
        p = resolve_publish_path(project, "PropBox", "lookdev", 2, variant="Clean")
        assert "PropBox_lookdev_v002__Clean.usd" in p


class TestListProjectEntities:
    def test_lists_assets(self, project):
        create_asset(project, "HeroChar", asset_type="CHARACTER")
        create_asset(project, "PropBox", asset_type="PROP")
        invalidate_entity_cache(project)
        entities = list_project_entities(project, "asset")
        names = [e["name"] for e in entities]
        assert "HeroChar" in names
        assert "PropBox" in names

    def test_type_icons(self, project):
        create_asset(project, "HeroChar", asset_type="CHARACTER")
        invalidate_entity_cache(project)
        entities = list_project_entities(project, "asset")
        hero = next(e for e in entities if e["name"] == "HeroChar")
        assert hero["type_icon"] == "ARMATURE_DATA"


class TestGetAssetStepStatus:
    def test_all_false_when_no_publishes(self, project):
        create_asset(project, "PropBox")
        status = get_asset_step_status(project, "PropBox")
        assert all(v is False for v in status.values())

    def test_true_when_publish_exists(self, project):
        create_asset(project, "PropBox")
        pub_dir = Path(project) / "assets" / "PropBox" / "modeling" / "publish"
        pub_dir.mkdir(parents=True, exist_ok=True)
        (pub_dir / "PropBox_modeling_v001.usd").touch()
        status = get_asset_step_status(project, "PropBox")
        assert status["modeling"] is True
        assert status["rigging"] is False


class TestAssetTypeTables:
    def test_prefixes_coverage(self):
        for k in ("PROP", "CHARACTER", "ENVIRONMENT"):
            assert k in ASSET_TYPE_PREFIXES

    def test_parent_col_coverage(self):
        assert ASSET_TYPE_PARENT_COL["PROP"] == "COL_ENV_Props"
        assert ASSET_TYPE_PARENT_COL["CHARACTER"] == "COL_CHAR"
