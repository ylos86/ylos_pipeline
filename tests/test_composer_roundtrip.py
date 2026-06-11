# -*- coding: utf-8 -*-
# tests/test_composer_roundtrip.py
# USD round-trip validation with usd-core (arch doc S-3.2).
# Skipped automatically when pxr is not installed.
#
# Critical assertions (C6 requirement):
#   - FX payload: prim /ROOT/{Entity}/fx exists under LoadNone.
#   - HasAuthoredPayloads() is True on that prim.
#   - Content is NOT composed until Load() is called.
#   - Modeling sublayer resolves geometry prims correctly.
#   - variantSet round-trip: variants list, switch, reference resolves.

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
pxr = pytest.importorskip("pxr", reason="usd-core not installed -- skip round-trip tests")

from pxr import Usd, Sdf

from ylos_core.project import create_project
from ylos_core.asset import create_asset, get_asset_root, build_publish_filename
from ylos_core.usd_composer import (
    compose_asset_root,
    write_usda_with_variants,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def project(tmp_path):
    result = create_project(str(tmp_path), "RTTest", "FILM")
    return result["project_path"]


def _make_publish(project, entity, step, version, variant="", usda_body=""):
    """Write a minimal valid USDA publish file and return its path."""
    pub_dir = get_asset_root(project, entity) / step / "publish"
    pub_dir.mkdir(parents=True, exist_ok=True)
    fname = build_publish_filename(entity, step, version, variant=variant)
    p = pub_dir / fname
    body = usda_body or (
        '#usda 1.0\n(\n    defaultPrim = "ROOT"\n)\n'
        f'def Xform "ROOT" {{\n'
        f'    def Xform "{entity}" {{\n'
        f'    }}\n'
        f'}}\n'
    )
    p.write_text(body, encoding="utf-8")
    return str(p)


# ---------------------------------------------------------------------------
# Simple root
# ---------------------------------------------------------------------------

class TestSimpleRootRoundTrip:
    def test_opens_cleanly(self, project):
        create_asset(project, "Hero")
        _make_publish(project, "Hero", "modeling", 1)
        result = compose_asset_root(project, "Hero")
        assert result["success"]

        stage = Usd.Stage.Open(result["root_path"])
        assert stage is not None

    def test_default_prim_is_root(self, project):
        create_asset(project, "Hero")
        _make_publish(project, "Hero", "modeling", 1)
        result = compose_asset_root(project, "Hero")

        stage = Usd.Stage.Open(result["root_path"])
        assert stage.GetDefaultPrim().GetName() == "ROOT"

    def test_sublayer_geometry_resolves(self, project):
        create_asset(project, "Hero")
        _make_publish(project, "Hero", "modeling", 1, usda_body=(
            '#usda 1.0\n(\n    defaultPrim = "ROOT"\n)\n'
            'def Xform "ROOT" {\n'
            '    def Xform "Hero" {\n'
            '        def Mesh "GEO_Body" {}\n'
            '        def Mesh "GEO_Head" {}\n'
            '    }\n'
            '}\n'
        ))
        result = compose_asset_root(project, "Hero")
        stage = Usd.Stage.Open(result["root_path"])
        assert stage.GetPrimAtPath("/ROOT/Hero/GEO_Body").IsValid()
        assert stage.GetPrimAtPath("/ROOT/Hero/GEO_Head").IsValid()


# ---------------------------------------------------------------------------
# variantSet round-trip
# ---------------------------------------------------------------------------

class TestVariantRoundTrip:
    def test_variant_set_present(self, project):
        create_asset(project, "Hero")
        _make_publish(project, "Hero", "lookdev", 1, "Default")
        _make_publish(project, "Hero", "lookdev", 1, "Dirty")
        result = compose_asset_root(project, "Hero")

        stage = Usd.Stage.Open(result["root_path"])
        entity = stage.GetPrimAtPath("/ROOT/Hero")
        assert entity.IsValid()
        vsets = entity.GetVariantSets()
        assert "lookdevVariant" in vsets.GetNames()

    def test_default_variant_selected(self, project):
        create_asset(project, "Hero")
        _make_publish(project, "Hero", "lookdev", 1, "Default")
        _make_publish(project, "Hero", "lookdev", 1, "Dirty")
        result = compose_asset_root(project, "Hero")

        stage = Usd.Stage.Open(result["root_path"])
        entity = stage.GetPrimAtPath("/ROOT/Hero")
        vset = entity.GetVariantSets().GetVariantSet("lookdevVariant")
        assert vset.GetVariantSelection() == "Default"

    def test_variant_switch_selection(self, project):
        """Switching variant selection updates stage metadata correctly."""
        create_asset(project, "Hero")
        _make_publish(project, "Hero", "lookdev", 1, "Default")
        _make_publish(project, "Hero", "lookdev", 1, "Dirty")
        result = compose_asset_root(project, "Hero")
        stage = Usd.Stage.Open(result["root_path"])
        entity = stage.GetPrimAtPath("/ROOT/Hero")
        vset = entity.GetVariantSets().GetVariantSet("lookdevVariant")

        vset.SetVariantSelection("Dirty")
        assert vset.GetVariantSelection() == "Dirty"

        vset.SetVariantSelection("Default")
        assert vset.GetVariantSelection() == "Default"


# ---------------------------------------------------------------------------
# FX payload round-trip -- C6 core requirement (arch doc S-3.2)
# ---------------------------------------------------------------------------

class TestFxPayloadRoundTrip:
    def _make_fx(self, project, entity, version=1):
        return _make_publish(project, entity, "fx", version, usda_body=(
            '#usda 1.0\n(\n    defaultPrim = "ROOT"\n'
            '    startTimeCode = 1001\n    endTimeCode = 1100\n)\n'
            f'def Xform "ROOT" {{\n'
            f'    def Xform "{entity}" {{\n'
            f'        def Mesh "FX_Cache" {{}}\n'
            f'    }}\n'
            f'}}\n'
        ))

    def test_fx_prim_exists_under_loadnone(self, project):
        """
        /ROOT/{Entity}/fx must be present in the composition graph even when
        the payload is not loaded (arch doc S-3.2 deferral requirement).
        """
        create_asset(project, "Hero")
        self._make_fx(project, "Hero")
        result = compose_asset_root(project, "Hero")

        stage = Usd.Stage.Open(result["root_path"], load=Usd.Stage.LoadNone)
        fx_prim = stage.GetPrimAtPath("/ROOT/Hero/fx")
        assert fx_prim.IsValid(), "FX scope prim must exist under LoadNone"

    def test_has_authored_payloads(self, project):
        """
        The FX prim must carry an authored payload arc (not just a reference).
        This is the canonical check that the USDA syntax is correct.
        """
        create_asset(project, "Hero")
        self._make_fx(project, "Hero")
        result = compose_asset_root(project, "Hero")

        stage = Usd.Stage.Open(result["root_path"], load=Usd.Stage.LoadNone)
        fx_prim = stage.GetPrimAtPath("/ROOT/Hero/fx")
        assert fx_prim.HasAuthoredPayloads(), \
            "FX prim must have an authored payload arc (not a reference)"

    def test_not_loaded_at_open_with_loadnone(self, project):
        """
        The FX cache must NOT be composed at open time.
        A heavy VDB or mesh cache must wait for an explicit Load() call.
        """
        create_asset(project, "Hero")
        self._make_fx(project, "Hero")
        result = compose_asset_root(project, "Hero")

        stage = Usd.Stage.Open(result["root_path"], load=Usd.Stage.LoadNone)
        fx_prim = stage.GetPrimAtPath("/ROOT/Hero/fx")
        assert not fx_prim.IsLoaded(), \
            "FX payload must be deferred at open time -- LoadNone was set"

    def test_loads_on_demand(self, project):
        """
        After stage.Load(), the FX content must be accessible.
        """
        create_asset(project, "Hero")
        self._make_fx(project, "Hero")
        result = compose_asset_root(project, "Hero")

        stage = Usd.Stage.Open(result["root_path"], load=Usd.Stage.LoadNone)
        stage.Load("/ROOT/Hero/fx")
        fx_prim = stage.GetPrimAtPath("/ROOT/Hero/fx")
        assert fx_prim.IsLoaded(), "FX payload must load after stage.Load()"

    def test_fx_content_not_visible_before_load(self, project):
        """
        FX mesh prims inside the payload must NOT be traversable until loaded.
        This validates that the payload boundary is correctly placed.
        """
        create_asset(project, "Hero")
        self._make_fx(project, "Hero")
        result = compose_asset_root(project, "Hero")

        stage = Usd.Stage.Open(result["root_path"], load=Usd.Stage.LoadNone)
        fx_cache = stage.GetPrimAtPath("/ROOT/Hero/fx/ROOT/Hero/FX_Cache")
        assert not fx_cache.IsValid(), \
            "FX_Cache must not be traversable before the payload is loaded"

    def test_fx_content_visible_after_load(self, project):
        create_asset(project, "Hero")
        self._make_fx(project, "Hero")
        result = compose_asset_root(project, "Hero")

        stage = Usd.Stage.Open(result["root_path"], load=Usd.Stage.LoadAll)
        fx_prim = stage.GetPrimAtPath("/ROOT/Hero/fx")
        assert fx_prim.IsLoaded()

    def test_fx_and_modeling_coexist(self, project):
        """
        Both the modeling sublayer and the FX payload must be correctly
        composed in the same stage.
        """
        create_asset(project, "Hero")
        _make_publish(project, "Hero", "modeling", 1, usda_body=(
            '#usda 1.0\n(\n    defaultPrim = "ROOT"\n)\n'
            'def Xform "ROOT" {\n    def Xform "Hero" {\n'
            '        def Mesh "GEO_Body" {}\n    }\n}\n'
        ))
        self._make_fx(project, "Hero")
        result = compose_asset_root(project, "Hero")

        stage = Usd.Stage.Open(result["root_path"], load=Usd.Stage.LoadNone)
        # Modeling geometry resolves via sublayer (no load needed)
        assert stage.GetPrimAtPath("/ROOT/Hero/GEO_Body").IsValid()
        # FX prim exists but is not loaded
        fx_prim = stage.GetPrimAtPath("/ROOT/Hero/fx")
        assert fx_prim.IsValid()
        assert not fx_prim.IsLoaded()
