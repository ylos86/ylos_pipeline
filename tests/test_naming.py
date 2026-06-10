# -*- coding: utf-8 -*-
# tests/test_naming.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from ylos_core.naming import (
    sanitize_entity_name,
    validate_entity_name,
    get_next_step,
    name_matches_asset,
    PREFIXES,
    STEP_ORDER,
)


class TestSanitizeEntityName:
    def test_clean_name_passthrough(self):
        assert sanitize_entity_name("HeroChar") == "HeroChar"

    def test_strips_spaces(self):
        assert sanitize_entity_name("Hero Char") == "HeroChar"

    def test_strips_hyphens(self):
        assert sanitize_entity_name("hero-char") == "herochar"

    def test_strips_leading_digits(self):
        assert sanitize_entity_name("123Hero") == "Hero"

    def test_strips_illegal_chars(self):
        assert sanitize_entity_name("Hero!@#Char") == "HeroChar"

    def test_empty_input(self):
        assert sanitize_entity_name("") == ""

    def test_only_illegal_chars(self):
        assert sanitize_entity_name("!@#$") == ""

    def test_strips_leading_underscores(self):
        assert sanitize_entity_name("_Hero") == "Hero"

    def test_preserves_internal_underscores(self):
        assert sanitize_entity_name("Hero_A") == "Hero_A"


class TestValidateEntityName:
    def test_valid_name(self):
        ok, msg = validate_entity_name("HeroCharacter")
        assert ok is True
        assert msg == ""

    def test_empty_name(self):
        ok, msg = validate_entity_name("")
        assert ok is False
        assert "empty" in msg.lower()

    def test_too_short(self):
        ok, msg = validate_entity_name("H")
        assert ok is False
        assert "2" in msg

    def test_lowercase_first(self):
        ok, msg = validate_entity_name("hero")
        assert ok is False
        assert "uppercase" in msg.lower()

    def test_two_char_minimum(self):
        ok, msg = validate_entity_name("He")
        assert ok is True


class TestGetNextStep:
    def test_modeling_to_rigging(self):
        assert get_next_step("modeling") == "rigging"

    def test_rigging_to_lookdev(self):
        assert get_next_step("rigging") == "lookdev"

    def test_lookdev_to_fx(self):
        assert get_next_step("lookdev") == "fx"

    def test_fx_is_last(self):
        assert get_next_step("fx") is None

    def test_unknown_step(self):
        assert get_next_step("nonexistent") is None

    def test_step_order_completeness(self):
        # Every step except the last has a defined next
        for i, step in enumerate(STEP_ORDER[:-1]):
            assert get_next_step(step) == STEP_ORDER[i + 1]


class TestNameMatchesAsset:
    def test_exact_match(self):
        assert name_matches_asset("GEO_Hero", "GEO_", "Hero") is True

    def test_with_variant_suffix(self):
        assert name_matches_asset("GEO_Hero_A", "GEO_", "Hero") is True

    def test_with_lod_suffix(self):
        assert name_matches_asset("GEO_Hero_LOD1", "GEO_", "Hero") is True

    def test_false_positive_guard(self):
        # HeroSword should NOT match Hero
        assert name_matches_asset("GEO_HeroSword", "GEO_", "Hero") is False

    def test_case_insensitive(self):
        assert name_matches_asset("geo_hero", "GEO_", "Hero") is True

    def test_wrong_prefix(self):
        assert name_matches_asset("RIG_Hero", "GEO_", "Hero") is False

    def test_dot_separator(self):
        # Blender duplicate notation
        assert name_matches_asset("GEO_Hero.001", "GEO_", "Hero") is True


class TestPrefixTable:
    def test_mesh_prefix(self):
        assert PREFIXES["MESH"] == "GEO_"

    def test_armature_prefix(self):
        assert PREFIXES["ARMATURE"] == "RIG_"

    def test_required_types_present(self):
        for t in ("MESH", "ARMATURE", "LIGHT", "CAMERA", "EMPTY", "CURVE"):
            assert t in PREFIXES
