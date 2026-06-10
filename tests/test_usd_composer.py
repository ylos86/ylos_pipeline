# -*- coding: utf-8 -*-
# tests/test_usd_composer.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from ylos_core.project import create_project
from ylos_core.asset import create_asset, resolve_publish_path
from ylos_core.usd_composer import (
    write_usda_root,
    write_usda_with_variants,
    compose_asset_root,
    compose_set_root,
    read_root_sublayers,
    read_root_variants,
)


@pytest.fixture
def project(tmp_path):
    result = create_project(str(tmp_path), "USDTest", "FILM")
    return result["project_path"]


def _touch_publish(project, entity, step, version, variant=""):
    from ylos_core.asset import build_publish_filename, get_asset_root
    pub_dir = get_asset_root(project, entity) / step / "publish"
    pub_dir.mkdir(parents=True, exist_ok=True)
    fname = build_publish_filename(entity, step, version, variant=variant)
    p = pub_dir / fname
    p.touch()
    return str(p)


class TestWriteUsdaroot:
    def test_creates_valid_usda(self, tmp_path):
        root = tmp_path / "test_root.usd"
        pub1 = tmp_path / "step_a.usd"
        pub2 = tmp_path / "step_b.usd"
        pub1.touch()
        pub2.touch()
        write_usda_root(str(root), [str(pub1), str(pub2)])
        content = root.read_text()
        assert "#usda 1.0" in content
        assert 'defaultPrim = "ROOT"' in content
        assert "subLayers" in content

    def test_sublayer_order_strongest_last(self, tmp_path):
        root = tmp_path / "test_root.usd"
        layers = [tmp_path / f"layer_{i}.usd" for i in range(3)]
        for l in layers:
            l.touch()
        # strongest opinion = first in our list, should appear LAST in file
        write_usda_root(str(root), [str(l) for l in layers])
        content = root.read_text()
        idx0 = content.index("layer_0")
        idx2 = content.index("layer_2")
        assert idx2 < idx0  # layer_0 (strongest) appears last

    def test_empty_sublayers(self, tmp_path):
        root = tmp_path / "empty_root.usd"
        write_usda_root(str(root), [])
        content = root.read_text()
        assert "#usda 1.0" in content
        assert "subLayers" not in content


class TestWriteUsdarootWithVariants:
    def test_no_variants_fallback(self, tmp_path):
        root = tmp_path / "root.usd"
        pub = tmp_path / "modeling.usd"
        pub.touch()
        write_usda_with_variants(str(root), [str(pub)], {}, "HeroChar")
        content = root.read_text()
        assert 'def Xform "HeroChar"' in content
        assert "variantSet" not in content

    def test_writes_variant_set(self, tmp_path):
        root = tmp_path / "root.usd"
        v_default = tmp_path / "lookdev_default.usd"
        v_dirty   = tmp_path / "lookdev_dirty.usd"
        v_default.touch()
        v_dirty.touch()
        variant_blocks = {"lookdev": {"Default": str(v_default), "Dirty": str(v_dirty)}}
        write_usda_with_variants(str(root), [], variant_blocks, "HeroChar")
        content = root.read_text()
        assert 'variantSet "lookdevVariant"' in content
        assert '"Default"' in content
        assert '"Dirty"' in content

    def test_default_variant_selected(self, tmp_path):
        root = tmp_path / "root.usd"
        vd = tmp_path / "d.usd"
        vd.touch()
        variant_blocks = {"lookdev": {"Default": str(vd)}}
        write_usda_with_variants(str(root), [], variant_blocks, "Prop")
        content = root.read_text()
        assert 'string lookdevVariant = "Default"' in content


class TestReadRootFunctions:
    def test_read_sublayers_roundtrip(self, tmp_path):
        root = tmp_path / "root.usd"
        layers = [tmp_path / f"step_{i}.usd" for i in range(3)]
        for l in layers:
            l.touch()
        write_usda_root(str(root), [str(l) for l in layers])
        read_back = read_root_sublayers(str(root))
        # Should be relative paths, 3 entries
        assert len(read_back) == 3

    def test_read_sublayers_missing_file(self, tmp_path):
        assert read_root_sublayers(str(tmp_path / "nonexistent.usd")) == []

    def test_read_variants_roundtrip(self, tmp_path):
        root = tmp_path / "root.usd"
        vd = tmp_path / "d.usd"
        vd.touch()
        variant_blocks = {"lookdev": {"Default": str(vd)}}
        write_usda_with_variants(str(root), [], variant_blocks, "HeroChar")
        parsed = read_root_variants(str(root))
        assert "lookdevVariant" in parsed
        assert "Default" in parsed["lookdevVariant"]


class TestComposeAssetRoot:
    def test_no_publishes_fails(self, project):
        create_asset(project, "HeroChar")
        result = compose_asset_root(project, "HeroChar")
        assert result["success"] is False

    def test_single_publish_success(self, project):
        create_asset(project, "HeroChar")
        _touch_publish(project, "HeroChar", "modeling", 1)
        result = compose_asset_root(project, "HeroChar")
        assert result["success"] is True
        assert Path(result["root_path"]).exists()

    def test_root_contains_usda_header(self, project):
        create_asset(project, "HeroChar")
        _touch_publish(project, "HeroChar", "modeling", 1)
        result = compose_asset_root(project, "HeroChar")
        content = Path(result["root_path"]).read_text()
        assert "#usda 1.0" in content

    def test_multiple_steps(self, project):
        create_asset(project, "HeroChar")
        _touch_publish(project, "HeroChar", "modeling", 1)
        _touch_publish(project, "HeroChar", "rigging", 1)
        result = compose_asset_root(project, "HeroChar")
        assert result["success"] is True

    def test_variant_publish_produces_variantset(self, project):
        create_asset(project, "HeroChar")
        _touch_publish(project, "HeroChar", "lookdev", 1, "Default")
        _touch_publish(project, "HeroChar", "lookdev", 1, "Dirty")
        result = compose_asset_root(project, "HeroChar")
        assert result["success"] is True
        content = Path(result["root_path"]).read_text()
        assert "variantSet" in content

    def test_latest_version_used(self, project):
        create_asset(project, "HeroChar")
        for v in (1, 2, 3):
            _touch_publish(project, "HeroChar", "modeling", v)
        result = compose_asset_root(project, "HeroChar")
        content = Path(result["root_path"]).read_text()
        assert "v003" in content
        assert "v001" not in content
        assert "v002" not in content


class TestRelativePaths:
    def test_sublayer_paths_are_relative(self, project):
        """Absolute machine paths must never appear in a published root."""
        create_asset(project, "TestAsset")
        _touch_publish(project, "TestAsset", "modeling", 1)
        result = compose_asset_root(project, "TestAsset")
        content = Path(result["root_path"]).read_text()
        # POSIX-style absolute path would start with /
        # Windows absolute path would contain a drive letter like C:/
        # Relative path should just be something like ../../modeling/publish/...
        for line in content.splitlines():
            if "@" in line and "subLayers" not in line and "variantSet" not in line:
                # Extract path between @ markers
                parts = line.split("@")
                if len(parts) >= 3:
                    path_str = parts[1]
                    assert not path_str.startswith("/"), \
                        f"Absolute path found in root USDA: {path_str}"


# ---------------------------------------------------------------------------
# Phase 3: FX payload tests
# ---------------------------------------------------------------------------

class TestFxPayloadOutput:
    def _touch_publish(self, project, entity, step, version, variant=""):
        from ylos_core.asset import build_publish_filename, get_asset_root
        pub_dir = get_asset_root(project, entity) / step / "publish"
        pub_dir.mkdir(parents=True, exist_ok=True)
        fname = build_publish_filename(entity, step, version, variant=variant)
        p = pub_dir / fname
        p.touch()
        return str(p)

    def test_fx_only_produces_payload_scope(self, project):
        create_asset(project, "PropBox")
        self._touch_publish(project, "PropBox", "fx", 1)
        result = compose_asset_root(project, "PropBox")
        assert result["success"] is True
        content = Path(result["root_path"]).read_text()
        assert 'def Scope "fx"' in content
        assert "prepend payload" in content
        assert "subLayers" not in content  # FX never sublayered

    def test_fx_not_in_sublayers(self, project):
        create_asset(project, "PropBox")
        self._touch_publish(project, "PropBox", "modeling", 1)
        self._touch_publish(project, "PropBox", "fx", 1)
        result = compose_asset_root(project, "PropBox")
        content = Path(result["root_path"]).read_text()
        # FX path should NOT appear in subLayers block
        sublayers_section = ""
        in_sub = False
        for line in content.splitlines():
            if "subLayers" in line:
                in_sub = True
            if in_sub:
                sublayers_section += line
            if in_sub and "]" in line:
                break
        assert "_fx_" not in sublayers_section

    def test_modeling_in_sublayers_not_payload(self, project):
        create_asset(project, "PropBox")
        self._touch_publish(project, "PropBox", "modeling", 1)
        self._touch_publish(project, "PropBox", "fx", 1)
        result = compose_asset_root(project, "PropBox")
        content = Path(result["root_path"]).read_text()
        assert "subLayers" in content
        assert "_modeling_" in content.split("subLayers")[1].split("]")[0]

    def test_fx_payload_result_field(self, project):
        create_asset(project, "PropBox")
        self._touch_publish(project, "PropBox", "fx", 1)
        result = compose_asset_root(project, "PropBox")
        assert result["fx_payload_path"] is not None
        assert "_fx_" in result["fx_payload_path"]

    def test_no_fx_result_field_is_none(self, project):
        create_asset(project, "PropBox")
        self._touch_publish(project, "PropBox", "modeling", 1)
        result = compose_asset_root(project, "PropBox")
        assert result.get("fx_payload_path") is None

    def test_fx_payload_path_is_relative(self, project):
        create_asset(project, "PropBox")
        self._touch_publish(project, "PropBox", "fx", 1)
        result = compose_asset_root(project, "PropBox")
        content = Path(result["root_path"]).read_text()
        # Extract the path between @ markers in the payload line
        for line in content.splitlines():
            if "prepend payload" in line and "@" in line:
                path_str = line.split("@")[1]
                assert not path_str.startswith("/"), \
                    f"Absolute path in FX payload: {path_str}"
                break

    def test_fx_scope_is_child_of_entity_prim(self, project):
        create_asset(project, "PropBox")
        self._touch_publish(project, "PropBox", "fx", 1)
        result = compose_asset_root(project, "PropBox")
        content = Path(result["root_path"]).read_text()
        # def Scope "fx" must appear after def Xform "PropBox"
        entity_idx = content.index('def Xform "PropBox"')
        fx_idx     = content.index('def Scope "fx"')
        assert fx_idx > entity_idx

    def test_fx_with_variants_coexist(self, project):
        create_asset(project, "PropBox")
        self._touch_publish(project, "PropBox", "modeling", 1)
        self._touch_publish(project, "PropBox", "lookdev",  1, "Default")
        self._touch_publish(project, "PropBox", "lookdev",  1, "Dirty")
        self._touch_publish(project, "PropBox", "fx",       1)
        result = compose_asset_root(project, "PropBox")
        assert result["success"] is True
        content = Path(result["root_path"]).read_text()
        assert "variantSet" in content
        assert 'def Scope "fx"' in content

    def test_read_root_fx_payload(self, project):
        from ylos_core.usd_composer import read_root_fx_payload
        create_asset(project, "PropBox")
        self._touch_publish(project, "PropBox", "fx", 1)
        result = compose_asset_root(project, "PropBox")
        payload = read_root_fx_payload(result["root_path"])
        assert payload is not None
        assert "_fx_" in payload

    def test_read_root_fx_payload_none_when_absent(self, project):
        from ylos_core.usd_composer import read_root_fx_payload
        create_asset(project, "PropBox")
        self._touch_publish(project, "PropBox", "modeling", 1)
        result = compose_asset_root(project, "PropBox")
        assert read_root_fx_payload(result["root_path"]) is None

    def test_fx_latest_version_used(self, project):
        create_asset(project, "PropBox")
        for v in (1, 2, 3):
            self._touch_publish(project, "PropBox", "fx", v)
        result = compose_asset_root(project, "PropBox")
        content = Path(result["root_path"]).read_text()
        assert "v003" in content
        assert "v001" not in content

    def test_write_usda_with_fx_payload_only(self, tmp_path):
        root = tmp_path / "root.usd"
        fx = tmp_path / "fx.usd"
        fx.touch()
        write_usda_with_variants(str(root), [], {}, "Hero", fx_payload_path=str(fx))
        content = root.read_text()
        assert 'def Scope "fx"' in content
        assert "prepend payload" in content

    def test_write_usda_with_sublayers_and_fx(self, tmp_path):
        root  = tmp_path / "root.usd"
        mod   = tmp_path / "modeling.usd"
        fx    = tmp_path / "fx.usd"
        mod.touch(); fx.touch()
        write_usda_with_variants(str(root), [str(mod)], {}, "Hero",
                                 fx_payload_path=str(fx))
        content = root.read_text()
        assert "subLayers" in content
        assert "modeling" in content
        assert 'def Scope "fx"' in content

    def test_note_in_compose_result(self, project):
        create_asset(project, "PropBox")
        self._touch_publish(project, "PropBox", "fx", 1)
        result = compose_asset_root(project, "PropBox")
        assert "FX payload" in result["message"]


class TestFxPayloadUsdCore:
    """Round-trip validation with usd-core. Skipped if pxr not installed."""

    @staticmethod
    def _pxr_available():
        try:
            import pxr
            return True
        except ImportError:
            return False

    def test_fx_root_parses_with_usd_core(self, project, tmp_path):
        import pytest
        if not self._pxr_available():
            pytest.skip("usd-core not installed")

        create_asset(project, "Hero")
        from ylos_core.asset import build_publish_filename, get_asset_root
        pub_dir = get_asset_root(project, "Hero") / "fx" / "publish"
        pub_dir.mkdir(parents=True, exist_ok=True)
        fx_file = pub_dir / build_publish_filename("Hero", "fx", 1)
        fx_file.write_text(
            '#usda 1.0\n(\n    defaultPrim = "ROOT"\n    '
            'startTimeCode = 1001\n    endTimeCode = 1100\n)\n'
            'def Xform "ROOT" {\n}\n',
            encoding="utf-8",
        )

        result = compose_asset_root(project, "Hero")
        assert result["success"] is True

        from pxr import Usd
        # Open with LoadNone so payloads remain deferred -- this is the critical
        # check: the FX prim must be present in the composition graph but unloaded.
        stage = Usd.Stage.Open(result["root_path"], load=Usd.Stage.LoadNone)
        assert stage is not None
        assert stage.GetDefaultPrim().GetName() == "ROOT"
        fx_prim = stage.GetPrimAtPath("/ROOT/Hero/fx")
        assert fx_prim.IsValid(), "FX scope prim must exist in stage"
        assert not fx_prim.IsLoaded(), \
            "FX payload must be deferred at open time (LoadNone) -- payload arc broken?"

    def test_fx_payload_loads_on_demand(self, project):
        import pytest
        if not self._pxr_available():
            pytest.skip("usd-core not installed")

        create_asset(project, "Hero")
        from ylos_core.asset import build_publish_filename, get_asset_root
        pub_dir = get_asset_root(project, "Hero") / "fx" / "publish"
        pub_dir.mkdir(parents=True, exist_ok=True)
        fx_file = pub_dir / build_publish_filename("Hero", "fx", 1)
        fx_file.write_text(
            '#usda 1.0\n(\n    defaultPrim = "ROOT"\n)\n'
            'def Xform "ROOT" {\n'
            '    def Mesh "FX_Smoke" {}\n'
            '}\n',
            encoding="utf-8",
        )

        result = compose_asset_root(project, "Hero")
        from pxr import Usd
        # Open with payload loading enabled
        stage = Usd.Stage.Open(
            result["root_path"],
            load=Usd.Stage.LoadAll,
        )
        fx_prim = stage.GetPrimAtPath("/ROOT/Hero/fx")
        assert fx_prim.IsLoaded(), "FX payload should load when LoadAll is set"

    def test_modeling_sublayer_round_trip(self, project):
        import pytest
        if not self._pxr_available():
            pytest.skip("usd-core not installed")

        create_asset(project, "Hero")
        from ylos_core.asset import build_publish_filename, get_asset_root
        pub_dir = get_asset_root(project, "Hero") / "modeling" / "publish"
        pub_dir.mkdir(parents=True, exist_ok=True)
        mod_file = pub_dir / build_publish_filename("Hero", "modeling", 1)
        mod_file.write_text(
            '#usda 1.0\n(\n    defaultPrim = "ROOT"\n)\n'
            'def Xform "ROOT" {\n'
            '    def Xform "Hero" {\n'
            '        def Mesh "GEO_Body" {}\n'
            '    }\n'
            '}\n',
            encoding="utf-8",
        )

        result = compose_asset_root(project, "Hero")
        from pxr import Usd
        stage = Usd.Stage.Open(result["root_path"])
        geo = stage.GetPrimAtPath("/ROOT/Hero/GEO_Body")
        assert geo.IsValid(), "GEO_Body from modeling sublayer must be reachable"
