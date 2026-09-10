#!/usr/bin/env python3
"""
tests/test_ylos_ui.py — stdlib tests (unittest) for ylos_ui.py.

Covers:
  - the origin gate (anti localhost drive-by): any Origin outside allowed_origins
    rejected with 403 BEFORE any processing — including 'Origin: null' (file:// but also
    a hostile sandboxed iframe). Without an Origin (curl, direct navigation): passes.
  - /api/config: single source for types/steps (create_project.py), steps overridden by
    the active project's pipeline.
  - /thumb/: anti path-traversal gate ('..' forbidden across the whole path, asset_name
    included), legitimate two-phase path served.
  - _build_launch_argv: pure argv-building function (INC-3).
  - POST /api/open-blender: 100% server-side resolution (create_project), never a path
    sent by the client — regression of the bug (missing 'assets/<entity>' segment on a
    naive project_root + rel concatenation) and verification that Import targets the
    EXACT requested version, never 'latest'.

Usage: python3 tests/test_ylos_ui.py
    or: python3 -m unittest tests.test_ylos_ui
"""
from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
import create_project as cp  # noqa: E402
import ylos_ui  # noqa: E402


class ServerTestCase(unittest.TestCase):
    """Real server on an ephemeral port; ~/.ylos state (active + recent) redirected to a
    tmpdir so it never touches the real user state."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory(prefix="ylos_ui_test_")
        cls._tmp = Path(cls._tmpdir.name)
        cls._saved = (ylos_ui.RECENT_FILE, ylos_ui.ACTIVE_FILE,
                      ylos_ui.YlosHandler.allowed_origins, ylos_ui.YlosHandler.log_message)
        ylos_ui.RECENT_FILE = cls._tmp / "recent_projects"
        ylos_ui.ACTIVE_FILE = cls._tmp / "active_project"
        ylos_ui.YlosHandler.log_message = lambda *a, **kw: None  # silence during the tests

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), ylos_ui.YlosHandler)
        cls.port = cls.server.server_address[1]
        ylos_ui.YlosHandler.allowed_origins = ylos_ui._allowed_origins(cls.port)
        cls._thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        (ylos_ui.RECENT_FILE, ylos_ui.ACTIVE_FILE,
         ylos_ui.YlosHandler.allowed_origins, ylos_ui.YlosHandler.log_message) = cls._saved
        cls._tmpdir.cleanup()

    @classmethod
    def _set_active(cls, project_dir):
        ylos_ui.ACTIVE_FILE.write_text(f"{project_dir}\n", encoding="utf-8")

    @classmethod
    def _clear_active(cls):
        if ylos_ui.ACTIVE_FILE.exists():
            ylos_ui.ACTIVE_FILE.unlink()

    def _request(self, path, method="GET", origin=None, body=None):
        """Returns (status, headers, body_bytes) — HTTP errors are responses,
        not exceptions."""
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            method=method,
        )
        if origin is not None:
            req.add_header("Origin", origin)
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def _raw_request(self, path):
        """GET with the path sent AS-IS (http.client) — urllib normalizes the '..'
        before sending, which would make the traversal tests harmless on the client side."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()


class TestOriginGate(ServerTestCase):

    def test_no_origin_allowed(self):
        status, _, _ = self._request("/api/recent-projects")
        self.assertEqual(status, 200)

    def test_trusted_origin_allowed_and_echoed(self):
        origin = f"http://127.0.0.1:{self.port}"
        status, headers, _ = self._request("/api/recent-projects", origin=origin)
        self.assertEqual(status, 200)
        # Echo the exact origin, never '*'.
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), origin)
        self.assertEqual(headers.get("Vary"), "Origin")

    def test_localhost_variant_allowed(self):
        origin = f"http://localhost:{self.port}"
        status, headers, _ = self._request("/api/recent-projects", origin=origin)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), origin)

    def test_untrusted_origin_rejected_no_cors(self):
        status, headers, _ = self._request("/api/recent-projects", origin="https://evil.example")
        self.assertEqual(status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_null_origin_rejected(self):
        # 'null' = file:// but also a hostile sandboxed iframe: never trusted.
        status, _, _ = self._request("/api/recent-projects", origin="null")
        self.assertEqual(status, 403)

    def test_preflight_untrusted_rejected(self):
        status, headers, _ = self._request("/api/set-project", method="OPTIONS",
                                           origin="https://evil.example")
        self.assertEqual(status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_preflight_trusted_passes(self):
        origin = f"http://127.0.0.1:{self.port}"
        status, headers, _ = self._request("/api/set-project", method="OPTIONS", origin=origin)
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), origin)

    def test_post_untrusted_origin_gated_before_handler(self):
        # A cross-site 'simple request' would run its side effects despite CORS:
        # the gate must respond 403 (and not 400 'folder not found', which would prove
        # the handler ran).
        status, _, _ = self._request("/api/set-project", origin="https://evil.example",
                                     body={"path": "/nonexistent_ylos_test_dir"})
        self.assertEqual(status, 403)


class TestApiConfig(ServerTestCase):
    """/api/config: types from create_project.py (single source, consumed by
    app.html::loadConfig instead of its old hard-coded FAMILY_CONFIG), steps
    overridden by the active project's pipeline."""

    def tearDown(self):
        self._clear_active()

    def test_defaults_without_active_project(self):
        self._clear_active()
        status, _, body = self._request("/api/config")
        self.assertEqual(status, 200)
        families = json.loads(body)["families"]
        self.assertEqual(families["asset"]["types"], cp.ASSET_TYPES)
        self.assertEqual(families["set"]["types"], cp.SET_TYPES)
        self.assertEqual(families["shot"]["types"], cp.SHOT_TYPES)
        self.assertEqual(families["asset"]["steps"], cp.DEFAULT_ASSET_STEPS)

    def test_active_project_pipeline_overrides_steps(self):
        info = cp.create("proj_config", root=str(self._tmp / "root"),
                         cache=str(self._tmp / "cache"))
        manifest = cp.read_manifest(info["source"])
        manifest["pipeline"]["asset_steps"] = ["modeling", "uvs", "lookdev"]
        cp.write_manifest(Path(info["source"]) / cp.PIPELINE_DIR, manifest)
        self._set_active(info["source"])

        status, _, body = self._request("/api/config")
        self.assertEqual(status, 200)
        families = json.loads(body)["families"]
        self.assertEqual(families["asset"]["steps"], ["modeling", "uvs", "lookdev"])
        # Types are never overridden: that's the validation contract.
        self.assertEqual(families["asset"]["types"], cp.ASSET_TYPES)
        # set/shot keys not declared in this modified manifest: module defaults.
        self.assertEqual(families["set"]["steps"], cp.DEFAULT_SET_STEPS)


class TestAssetScenefiles(ServerTestCase):
    """/api/asset/<name> exposes 'scenefiles' (WIP history + comment/user from the
    '<wip>.blend.json' sidecar written by ylos.save_wip, INC-4) — read-only, tolerant of an
    absent/corrupt sidecar."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        info = cp.create("proj_scenefiles", root=str(cls._tmp / "sroot"),
                         cache=str(cls._tmp / "scache"))
        cls.project = Path(info["source"])
        cp.create_asset(cls.project, "PROP_Tente_Default", asset_type="PROP")

        wip_dir = cls.project / "assets" / "PROP_Tente_Default" / "modeling" / "wip"
        wip_dir.mkdir(parents=True, exist_ok=True)

        # v001: conforming sidecar.
        (wip_dir / "PROP_Tente_Default_modeling_v001.blend").write_bytes(b"blend")
        (wip_dir / "PROP_Tente_Default_modeling_v001.blend.json").write_text(
            json.dumps({"comment": "blocking pass", "user": "seb",
                       "date": "2026-07-15T00:00:00+00:00", "blender_version": "5.1.1"}),
            encoding="utf-8")

        # v002: NO sidecar (legacy WIP, before INC-4) - must never raise.
        (wip_dir / "PROP_Tente_Default_modeling_v002.blend").write_bytes(b"blend")

        cls._set_active(cls.project)

    def test_scenefiles_merges_sidecar_and_tolerates_missing(self):
        status, _, body = self._request("/api/asset/PROP_Tente_Default")
        self.assertEqual(status, 200)
        data = json.loads(body)
        sf = data["scenefiles"]["modeling"]
        self.assertEqual([v["version"] for v in sf], [1, 2])

        v1 = sf[0]
        self.assertEqual(v1["comment"], "blocking pass")
        self.assertEqual(v1["user"], "seb")
        self.assertEqual(v1["blender_version"], "5.1.1")

        v2 = sf[1]
        self.assertEqual(v2["comment"], "")
        self.assertEqual(v2["user"], "")

    def test_scenefiles_absent_for_unknown_step(self):
        status, _, body = self._request("/api/asset/PROP_Tente_Default")
        data = json.loads(body)
        self.assertNotIn("lookdev", data["scenefiles"])  # no WIP -> no key


class TestThumbSecurity(ServerTestCase):
    """/thumb/ anti path-traversal gate + serving a legitimate two-phase thumb."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        info = cp.create("proj_thumb", root=str(cls._tmp / "troot"),
                         cache=str(cls._tmp / "tcache"))
        cls.project = info["source"]
        cp.create_asset(cls.project, "PROP_Tente_Default", asset_type="PROP")
        staging, final = cp.allocate_publish_version(
            cls.project, "PROP_Tente_Default", comment="", kind="modeling")
        version = cp.publish_version_from_dir(final)
        stem = f"PROP_Tente_Default_modeling_v{version:03d}"
        (staging / f"{stem}.usd").write_bytes(b"artifact")
        (staging / "thumb.png").write_bytes(b"\x89PNG fake")
        cp.finalize_publish_version(cls.project, "PROP_Tente_Default", staging, final,
                                    version, expected_artifacts=[stem, "thumb.png"])
        cls.thumb_rel = f"modeling/publish/{stem}/thumb.png"
        cls._set_active(cls.project)

    def test_legit_two_phase_thumb_served(self):
        status, headers, body = self._request(f"/thumb/PROP_Tente_Default/{self.thumb_rel}")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/png")
        self.assertEqual(body, b"\x89PNG fake")

    def test_dotdot_in_subpath_rejected(self):
        status, _, _ = self._raw_request("/thumb/PROP_Tente_Default/../_pipeline/project.json")
        self.assertEqual(status, 400)

    def test_dotdot_as_asset_name_rejected(self):
        # Regression: '..' as asset_name stayed INSIDE the project (containment ok) but
        # served files outside the thumb contract (project.json...).
        status, _, _ = self._raw_request("/thumb/../_pipeline/project.json")
        self.assertEqual(status, 400)

    def test_deep_traversal_rejected(self):
        status, _, _ = self._raw_request(
            "/thumb/PROP_Tente_Default/../../../../../../etc/hosts")
        self.assertEqual(status, 400)


class TestWebPins(ServerTestCase):
    """Web pinning via the API: /api/web-pins (state + available), /api/pin-asset
    (validated against the real GLB publishes), /api/unpin-asset (idempotent), and the
    full circuit pin -> set-web-target -> sync-web."""

    @classmethod
    def _publish(cls, asset_name, step, ext):
        staging, final = cp.allocate_publish_version(
            cls.project, asset_name, comment="", kind=step)
        version = cp.publish_version_from_dir(final)
        stem = f"{asset_name}_{step}_v{version:03d}"
        (staging / f"{stem}.{ext}").write_bytes(b"artifact")
        (staging / "thumb.png").write_bytes(b"png")
        cp.finalize_publish_version(cls.project, asset_name, staging, final, version,
                                    expected_artifacts=[stem, "thumb.png"])
        return version

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        info = cp.create("proj_pins", root=str(cls._tmp / "proot"),
                         cache=str(cls._tmp / "pcache"))
        cls.project = info["source"]
        cp.create_asset(cls.project, "PROP_Tente_Default", asset_type="PROP")
        cls._publish("PROP_Tente_Default", "lookdev", "glb")   # v1
        cls._publish("PROP_Tente_Default", "lookdev", "glb")   # v2
        cls._publish("PROP_Tente_Default", "modeling", "usd")  # USD: never pinnable
        cls._set_active(cls.project)

    def _pins_state(self):
        status, _, body = self._request("/api/web-pins")
        self.assertEqual(status, 200)
        return json.loads(body)

    def test_available_lists_glb_only(self):
        state = self._pins_state()
        self.assertEqual(state["available"],
                         {"PROP_Tente_Default": {"lookdev": [1, 2]}})  # no modeling (USD)

    def test_pin_unpin_roundtrip(self):
        status, _, _ = self._request("/api/pin-asset", method="POST",
                                     body={"name": "PROP_Tente_Default",
                                           "step": "lookdev", "version": 2})
        self.assertEqual(status, 200)
        self.assertEqual(self._pins_state()["pins"],
                         {"PROP_Tente_Default": {"step": "lookdev", "version": 2}})
        # Persisted in project.json (the contract sync_web_assets reads).
        manifest = cp.read_manifest(self.project)
        self.assertEqual(manifest["web"]["pinned_assets"]["PROP_Tente_Default"]["version"], 2)

        status, _, _ = self._request("/api/unpin-asset", method="POST",
                                     body={"name": "PROP_Tente_Default"})
        self.assertEqual(status, 200)
        self.assertEqual(self._pins_state()["pins"], {})
        # Idempotent: un-pinning again stays an ok.
        status, _, _ = self._request("/api/unpin-asset", method="POST",
                                     body={"name": "PROP_Tente_Default"})
        self.assertEqual(status, 200)

    def test_pin_nonexistent_version_rejected(self):
        status, _, body = self._request("/api/pin-asset", method="POST",
                                        body={"name": "PROP_Tente_Default",
                                              "step": "lookdev", "version": 99})
        self.assertEqual(status, 400)
        self.assertIn("lookdev", json.loads(body)["error"])  # message lists the available ones

    def test_pin_usd_step_rejected(self):
        status, _, _ = self._request("/api/pin-asset", method="POST",
                                     body={"name": "PROP_Tente_Default",
                                           "step": "modeling", "version": 1})
        self.assertEqual(status, 400)

    def test_pin_unknown_asset_rejected(self):
        status, _, _ = self._request("/api/pin-asset", method="POST",
                                     body={"name": "PROP_Fantome_Default",
                                           "step": "lookdev", "version": 1})
        self.assertEqual(status, 400)

    def test_full_pin_sync_cycle(self):
        # The full circuit as the modal runs it: pin -> target -> sync.
        for path, payload in (
            ("/api/pin-asset", {"name": "PROP_Tente_Default", "step": "lookdev", "version": 1}),
            ("/api/set-web-target", {"target_dir": str(self._tmp / "webproj")}),
        ):
            status, _, _ = self._request(path, method="POST", body=payload)
            self.assertEqual(status, 200)
        status, _, body = self._request("/api/sync-web", method="POST")
        self.assertEqual(status, 200)
        result = json.loads(body)
        self.assertEqual(result["warnings"], [])
        glb = self._tmp / "webproj" / "public" / "assets" / "PROP_Tente_Default_v001.glb"
        self.assertTrue(glb.is_file())


class TestBuildLaunchArgv(unittest.TestCase):
    """_build_launch_argv is a PURE function (INC-3): 'path' is already resolved by
    the caller, no path reconstruction/concatenation here."""

    def test_argv_includes_project_path_kind_entity_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            project  = Path(tmp) / "MyProject"
            blender  = Path(tmp) / "Blender"
            launcher = Path(tmp) / "launch_context.py"
            path = str(project / "assets" / "PROP_Foo_Default" / "modeling" / "publish" /
                      "PROP_Foo_Default_modeling_v003" / "PROP_Foo_Default_modeling_v003.usd")

            argv = ylos_ui._build_launch_argv(
                blender, launcher, project, path, "publish",
                entity="PROP_Foo_Default", step="modeling",
            )

            self.assertEqual(argv, [
                str(blender), "--python", str(launcher), "--",
                "--project", str(project),
                "--entity", "PROP_Foo_Default",
                "--step", "modeling",
                "--path", path,
                "--kind", "publish",
            ])

    def test_argv_omits_optional_entity_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "Proj"
            argv = ylos_ui._build_launch_argv(
                Path(tmp) / "b", Path(tmp) / "l.py", project,
                "/some/resolved/path.usda", "scene_default",
            )
            self.assertNotIn("--entity", argv)
            self.assertNotIn("--step", argv)
            self.assertIn("--path", argv)
            self.assertIn("--kind", argv)


class TestOpenBlenderResolution(ServerTestCase):
    """POST /api/open-blender — 100% server-side resolution (create_project), never a path
    sent by the client (see INC-3). subprocess.Popen mocked: we check the RESOLVED path
    (canonical, absolute, including the 'assets/<entity>' segment — that was the cause of the
    bug), never a real Blender instance launched during the tests."""

    @classmethod
    def _publish(cls, step, ext):
        staging, final = cp.allocate_publish_version(
            cls.project, "PROP_Tente_Default", comment="", kind=step)
        version = cp.publish_version_from_dir(final)
        stem = f"PROP_Tente_Default_{step}_v{version:03d}"
        (staging / f"{stem}.{ext}").write_bytes(f"artifact v{version}".encode())
        (staging / "thumb.png").write_bytes(b"png")
        cp.finalize_publish_version(cls.project, "PROP_Tente_Default", staging, final,
                                    version, expected_artifacts=[stem, "thumb.png"])
        return final / f"{stem}.{ext}"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        info = cp.create("proj_open", root=str(cls._tmp / "oroot"),
                         cache=str(cls._tmp / "ocache"))
        cls.project = Path(info["source"])
        cp.create_asset(cls.project, "PROP_Tente_Default", asset_type="PROP")

        # Real WIP for 'Open scene' (kind='wip', resolution order #1).
        wip_dir = cls.project / "assets" / "PROP_Tente_Default" / "modeling" / "wip"
        wip_dir.mkdir(parents=True, exist_ok=True)
        cls.wip_file = wip_dir / "PROP_Tente_Default_modeling_v001.blend"
        cls.wip_file.write_bytes(b"fake blend")

        # Two published versions (lookdev) for 'Import' of a SPECIFIC version (not latest).
        cls.pub_v1 = cls._publish("lookdev", "usd")
        cls.pub_v2 = cls._publish("lookdev", "usd")

        cls._set_active(cls.project)

        cls._fake_blender = cls._tmp / "fake_blender_app"
        cls._fake_blender.write_bytes(b"")
        cls._fake_launcher = cls._tmp / "fake_launch_context.py"
        cls._fake_launcher.write_bytes(b"")

    def setUp(self):
        # Never a real Blender instance launched during the tests: Popen mocked, binary/
        # launcher pointed at dummy files (only '.is_file()' matters here). YLOS_DIR/
        # SERVER_LOG redirected to the tmpdir - never touch the real ~/.ylos (same discipline
        # as ServerTestCase for RECENT_FILE/ACTIVE_FILE).
        self._patches = [
            patch.object(ylos_ui, "BLENDER_APP", self._fake_blender),
            patch.object(ylos_ui, "LAUNCHER", self._fake_launcher),
            patch.object(ylos_ui, "YLOS_DIR", self._tmp / "ylos_home"),
            patch.object(ylos_ui, "SERVER_LOG", self._tmp / "ylos_home" / "launch-server.log"),
            patch.object(ylos_ui.subprocess, "Popen"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()

    def test_open_scene_resolves_wip_canonical_absolute_path(self):
        status, _, body = self._request(
            "/api/open-blender", method="POST",
            body={"entity": "PROP_Tente_Default", "step": "modeling"})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["kind"], "wip")
        # Regression of the bug: the resolved path MUST be the real WIP file, 'assets/<entity>'
        # segment included (a naive project_root + rel concatenation skipped it).
        self.assertEqual(Path(data["path"]), self.wip_file)
        norm = data["path"].replace("\\", "/")
        self.assertIn("assets/PROP_Tente_Default/modeling/wip", norm)

    def test_import_publish_targets_exact_version_not_latest(self):
        status, _, body = self._request(
            "/api/open-blender", method="POST",
            body={"entity": "PROP_Tente_Default", "step": "lookdev", "version": 1})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["kind"], "publish")
        self.assertEqual(Path(data["path"]), self.pub_v1)
        self.assertNotEqual(Path(data["path"]), self.pub_v2)

    def test_import_publish_nonexistent_version_404(self):
        status, _, _ = self._request(
            "/api/open-blender", method="POST",
            body={"entity": "PROP_Tente_Default", "step": "lookdev", "version": 99})
        self.assertEqual(status, 404)

    def test_import_without_step_400(self):
        status, _, _ = self._request(
            "/api/open-blender", method="POST",
            body={"entity": "PROP_Tente_Default", "version": 1})
        self.assertEqual(status, 400)

    def test_open_scene_missing_entity_400(self):
        status, _, _ = self._request("/api/open-blender", method="POST", body={})
        self.assertEqual(status, 400)

    def test_open_scene_unknown_entity_404(self):
        status, _, _ = self._request(
            "/api/open-blender", method="POST", body={"entity": "PROP_Fantome_Default"})
        self.assertEqual(status, 404)


class TestSetFrameRange(ServerTestCase):
    """POST /api/set-frame-range: thin adapter to create_project.set_frame_range
    (start<end validation + entity=shot + atomic write + shot_root recompo, principle 5).
    set_frame_range RAISES for a business case (invalid range, entity != shot, absent) → the
    server maps it to 400 (client input), never 500. frame_range exposed on /api/asset so
    the web UI can prefill its modal."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        info = cp.create("proj_fr", root=str(cls._tmp / "frroot"),
                         cache=str(cls._tmp / "frcache"))
        cls.project = info["source"]
        cp.create_asset(cls.project, "ANIMATION_Sh010_Default",
                        entity_type="shot", asset_type="ANIMATION")
        cp.create_asset(cls.project, "PROP_Tente_Default", asset_type="PROP")  # not a shot
        cls._set_active(cls.project)

    def test_set_valid_persists_and_exposes(self):
        status, _, body = self._request(
            "/api/set-frame-range", method="POST",
            body={"entity": "ANIMATION_Sh010_Default", "start": 1010, "end": 1200, "fps": 25})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["frame_range"],
                         {"start": 1010, "end": 1200, "fps": 25})
        # Persisted in the manifest (source of truth, principle 3).
        manifest = cp.resolve_entity(self.project, "ANIMATION_Sh010_Default")["manifest"]
        self.assertEqual(manifest["frame_range"], {"start": 1010, "end": 1200, "fps": 25})
        # Exposed on /api/asset to prefill the modal on the web UI side.
        _, _, detail = self._request("/api/asset/ANIMATION_Sh010_Default")
        self.assertEqual(json.loads(detail)["frame_range"],
                         {"start": 1010, "end": 1200, "fps": 25})

    def test_invalid_range_400(self):
        status, _, body = self._request(
            "/api/set-frame-range", method="POST",
            body={"entity": "ANIMATION_Sh010_Default", "start": 1100, "end": 1001})
        self.assertEqual(status, 400)
        self.assertIn("start", json.loads(body)["error"].lower())

    def test_non_shot_400(self):
        status, _, _ = self._request(
            "/api/set-frame-range", method="POST",
            body={"entity": "PROP_Tente_Default", "start": 1001, "end": 1100})
        self.assertEqual(status, 400)

    def test_unknown_entity_400(self):
        status, _, _ = self._request(
            "/api/set-frame-range", method="POST",
            body={"entity": "SHOT_Fantome_Default", "start": 1001, "end": 1100})
        self.assertEqual(status, 400)

    def test_missing_field_400(self):
        status, _, _ = self._request(
            "/api/set-frame-range", method="POST",
            body={"entity": "ANIMATION_Sh010_Default", "start": 1001})  # 'end' missing
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
