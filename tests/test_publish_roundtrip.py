#!/usr/bin/env python3
"""
tests/test_publish_roundtrip.py - stdlib tests for create_project.validate_publish_roundtrip,
the DCC-agnostic rule behind "will this publish re-import correctly?".

The DCC builds two fingerprints (what it exported / what it got back from the artifact in a
throwaway container); the orchestrator decides. Locks: an identical round trip passes; triangles
(not vertices/polygons) are the geometry invariant; a size, UV or mesh-count drift is an ERROR
(refuse the publish); renamed objects/materials are only WARNINGS; a malformed fingerprint is an
error, never an exception.

Usage: python3 tests/test_publish_roundtrip.py
    or: python3 -m unittest tests.test_publish_roundtrip
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
import create_project as cp  # noqa: E402


def _fp(**over):
    base = {
        "mesh_count": 2,
        "tri_count": 480,
        "extents": [0.5, 1.0, 1.8],
        "uv_mesh_count": 2,
        "materials": ["MAT_Skin", "MAT_Cloth"],
        "object_names": ["GEO_Body", "GEO_Hair"],
    }
    base.update(over)
    return base


def _codes(result, kind):
    return [e["code"] for e in result[kind]]


class RoundTripTest(unittest.TestCase):
    def test_identical_round_trip_passes(self):
        r = cp.validate_publish_roundtrip(_fp(), _fp())
        self.assertTrue(r["ok"])
        self.assertEqual(r["errors"], [])
        self.assertEqual(r["warnings"], [])

    def test_nothing_comes_back_is_an_error(self):
        r = cp.validate_publish_roundtrip(_fp(), _fp(mesh_count=0, tri_count=0,
                                                     extents=[0, 0, 0], uv_mesh_count=0,
                                                     materials=[], object_names=[]))
        self.assertFalse(r["ok"])
        self.assertIn("empty_import", _codes(r, "errors"))

    def test_mesh_count_mismatch(self):
        r = cp.validate_publish_roundtrip(_fp(), _fp(mesh_count=1))
        self.assertIn("mesh_count", _codes(r, "errors"))

    def test_triangles_are_the_invariant_not_vertices(self):
        # Same triangles, different everything else: legitimate (seam splits, quads -> tris).
        r = cp.validate_publish_roundtrip(_fp(), _fp(object_names=["GEO_Body", "GEO_Hair"]))
        self.assertTrue(r["ok"])
        r = cp.validate_publish_roundtrip(_fp(), _fp(tri_count=479))
        self.assertIn("tri_count", _codes(r, "errors"))

    def test_extents_within_tolerance_pass_beyond_fail(self):
        ok = cp.validate_publish_roundtrip(_fp(), _fp(extents=[0.505, 1.01, 1.82]))
        self.assertTrue(ok["ok"], ok)
        bad = cp.validate_publish_roundtrip(_fp(), _fp(extents=[0.5, 1.0, 180.0]))  # cm vs m
        self.assertIn("extents", _codes(bad, "errors"))

    def test_zero_size_geometry_is_refused(self):
        r = cp.validate_publish_roundtrip(_fp(extents=[0, 0, 0]), _fp(extents=[0, 0, 0]))
        self.assertIn("zero_size", _codes(r, "errors"))

    def test_lost_uvs_are_an_error(self):
        r = cp.validate_publish_roundtrip(_fp(), _fp(uv_mesh_count=0))
        self.assertIn("uv_lost", _codes(r, "errors"))

    def test_renamed_objects_and_materials_are_warnings_only(self):
        r = cp.validate_publish_roundtrip(
            _fp(), _fp(materials=["MAT_Skin"], object_names=["GEO_Body"]))
        self.assertTrue(r["ok"])
        self.assertEqual(sorted(_codes(r, "warnings")), ["materials", "names"])

    def test_exporter_name_sanitizing_is_not_drift(self):
        # USD turns 'GEO_Body.001' into 'GEO_Body_001'; glTF keeps it: neither is a rename.
        r = cp.validate_publish_roundtrip(
            _fp(object_names=["GEO_Body.001", "GEO Hair"]),
            _fp(object_names=["GEO_Body_001", "GEO_Hair"]))
        self.assertEqual(r["warnings"], [])

    def test_malformed_fingerprint_is_an_error_not_an_exception(self):
        self.assertFalse(cp.validate_publish_roundtrip(None, _fp())["ok"])
        broken = copy.deepcopy(_fp())
        del broken["tri_count"]
        r = cp.validate_publish_roundtrip(_fp(), broken)
        self.assertFalse(r["ok"])
        self.assertEqual(_codes(r, "errors"), ["fingerprint"])


if __name__ == "__main__":
    unittest.main()
