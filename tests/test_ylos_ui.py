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
    EXACT requested version, never 'latest'; plus the 'create' (New Scene, Phase 1.4) and
    'scenefile' (open a specific WIP) verbs.
  - _launch_env: PURE per-session env of a launched DCC (Phase 0.2) — PROJ_ROOT always
    overridden with the PARENT of the project, PROJ_CACHE kept when the shell has one.
  - POST /api/set-step-status + POST /api/reveal (schema 2.2 cockpit), and the cockpit
    payloads of /api/assets and /api/asset/<name> (step_status, outdated, publishes,
    dependencies, multi-DCC scenefiles).

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
                entity="PROP_Foo_Default", step="modeling", version=3,
            )

            self.assertEqual(argv, [
                str(blender), "--python", str(launcher), "--",
                "--project", str(project),
                "--entity", "PROP_Foo_Default",
                "--step", "modeling",
                "--path", path,
                "--version", "3",
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

    def test_argv_create_verb_has_no_path(self):
        """'create' (New Scene, Phase 1.4) is the only kind WITHOUT --path: the operator
        ylos.create_scene allocates the WIP version itself from the orchestrator spec —
        the server pre-allocating one would burn a version number per click."""
        with tempfile.TemporaryDirectory() as tmp:
            argv = ylos_ui._build_launch_argv(
                Path(tmp) / "b", Path(tmp) / "l.py", Path(tmp) / "Proj", None, "create",
                entity="PROP_Foo_Default", step="modeling",
            )
            self.assertNotIn("--path", argv)
            self.assertEqual(argv[-2:], ["--kind", "create"])
            self.assertIn("--entity", argv)
            self.assertIn("--step", argv)


class TestLaunchEnv(unittest.TestCase):
    """_launch_env — per-session DCC environment (plan-usable-v1 Phase 0.2, tension #1 of
    CLAUDE.md). PURE function: it returns a COPY, never touches os.environ."""

    def test_proj_root_is_the_parent_of_the_project(self):
        # Contract: $PROJ_ROOT/<project> == project_dir (create_project._expand_proj_root /
        # ylos_houdini.env_relative), so PROJ_ROOT is the PARENT folder.
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "roots" / "MyProject"
            project.mkdir(parents=True)
            env = ylos_ui._launch_env(project, {})
            self.assertEqual(env["PROJ_ROOT"], str(project.resolve().parent))
            self.assertEqual(Path(env["PROJ_ROOT"]) / project.name, project.resolve())

    def test_shell_proj_root_is_always_overridden(self):
        # Two projects launched from the same UI must not share the shell's global value:
        # an inherited PROJ_ROOT pointing elsewhere is replaced, never kept.
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "B" / "ProjectB"
            project.mkdir(parents=True)
            env = ylos_ui._launch_env(project, {"PROJ_ROOT": "/somewhere/else"})
            self.assertEqual(env["PROJ_ROOT"], str(project.resolve().parent))

    def test_shell_proj_cache_kept_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "P"
            project.mkdir()
            env = ylos_ui._launch_env(project, {"PROJ_CACHE": "/Volumes/NVMe/cache"})
            self.assertEqual(env["PROJ_CACHE"], "/Volumes/NVMe/cache")

    def test_proj_cache_falls_back_to_orchestrator(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "P"
            project.mkdir()
            with patch.object(cp, "resolve_cache", return_value=Path("/tmp/ylos_cache_x")):
                env = ylos_ui._launch_env(project, {})
            self.assertEqual(env["PROJ_CACHE"], "/tmp/ylos_cache_x")

    def test_pure_copy_preserves_other_vars_and_leaves_base_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "P"
            project.mkdir()
            base = {"PATH": "/usr/bin", "HOME": "/Users/x"}
            env = ylos_ui._launch_env(project, base)
            self.assertEqual(env["PATH"], "/usr/bin")
            self.assertEqual(env["HOME"], "/Users/x")
            self.assertNotIn("PROJ_ROOT", base)  # the caller's env is never mutated


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
        # the exact version travels to the launcher (ylos.import_product, tagged import)
        argv = data["argv"]
        self.assertEqual(argv[argv.index("--version") + 1], "1")

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


class TestCockpitVerbs(ServerTestCase):
    """The verbs the entity view drives (plan-usable-v1 Phases 1.4 / 2.2): 'New Scene'
    (create) and 'open a specific WIP version' (scenefile). subprocess.Popen mocked — the
    ARGV and the child ENV are the contract, never a real Blender instance."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        info = cp.create("proj_cockpit", root=str(cls._tmp / "croot"),
                         cache=str(cls._tmp / "ccache"))
        cls.project = Path(info["source"])
        cp.create_asset(cls.project, "PROP_Tente_Default", asset_type="PROP")
        cls.steps = cp.resolve_entity(cls.project, "PROP_Tente_Default")["manifest"]["steps"]
        cls.step = cls.steps[0]

        wip = cls.project / "assets" / "PROP_Tente_Default" / cls.step / "wip"
        wip.mkdir(parents=True, exist_ok=True)
        for v in (1, 2):
            (wip / f"PROP_Tente_Default_{cls.step}_v{v:03d}.blend").write_bytes(b"blend")
        # A Houdini WIP sits in the same folder (multi-DCC listing) — Blender must refuse
        # it explicitly rather than hand a .hipnc to open_mainfile.
        (wip / f"PROP_Tente_Default_{cls.step}_v003.hipnc").write_bytes(b"hip")

        cls._set_active(cls.project)
        cls._fake_blender = cls._tmp / "fake_blender_cockpit"
        cls._fake_blender.write_bytes(b"")
        cls._fake_launcher = cls._tmp / "fake_launcher_cockpit.py"
        cls._fake_launcher.write_bytes(b"")

    def setUp(self):
        self._popen = patch.object(ylos_ui.subprocess, "Popen").start()
        self._patches = [
            patch.object(ylos_ui, "BLENDER_APP", self._fake_blender),
            patch.object(ylos_ui, "LAUNCHER", self._fake_launcher),
            patch.object(ylos_ui, "YLOS_DIR", self._tmp / "ylos_home_c"),
            patch.object(ylos_ui, "SERVER_LOG", self._tmp / "ylos_home_c" / "launch.log"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        patch.stopall()

    # --- 'New Scene' (create)

    def test_create_launches_with_kind_create_and_no_path(self):
        status, _, body = self._request(
            "/api/open-blender", method="POST",
            body={"entity": "PROP_Tente_Default", "step": self.step, "create": True})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["kind"], "create")
        self.assertIsNone(data["path"])
        self.assertIn("--kind", data["argv"])
        self.assertEqual(data["argv"][data["argv"].index("--kind") + 1], "create")
        self.assertNotIn("--path", data["argv"])
        self.assertEqual(data["step"], self.step)

    def test_create_child_gets_per_session_proj_root(self):
        """Phase 0.2 acceptance: the CHILD env carries this project's PROJ_ROOT, so two
        Blenders launched from the same UI on two projects never collide."""
        status, _, _ = self._request(
            "/api/open-blender", method="POST",
            body={"entity": "PROP_Tente_Default", "step": self.step, "create": True})
        self.assertEqual(status, 200)
        env = self._popen.call_args.kwargs["env"]
        self.assertEqual(env["PROJ_ROOT"], str(self.project.resolve().parent))
        self.assertTrue(env["PROJ_CACHE"])

    def test_create_without_step_400(self):
        status, _, _ = self._request(
            "/api/open-blender", method="POST",
            body={"entity": "PROP_Tente_Default", "create": True})
        self.assertEqual(status, 400)

    def test_create_undeclared_step_400_with_orchestrator_reason(self):
        status, _, body = self._request(
            "/api/open-blender", method="POST",
            body={"entity": "PROP_Tente_Default", "step": "not_a_step", "create": True})
        self.assertEqual(status, 400)
        # The message is the orchestrator's own (scene_starter_spec reason), not a
        # server-local rewording — single point of truth for the rule.
        self.assertIn("not declared", json.loads(body)["error"])

    def test_create_unknown_entity_404(self):
        status, _, _ = self._request(
            "/api/open-blender", method="POST",
            body={"entity": "PROP_Fantome_Default", "step": self.step, "create": True})
        self.assertEqual(status, 404)

    # --- open a specific WIP version (version gallery / scenefiles list)

    def test_scenefile_opens_the_exact_requested_version(self):
        status, _, body = self._request(
            "/api/open-blender", method="POST",
            body={"entity": "PROP_Tente_Default", "step": self.step, "scenefile": 1})
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["kind"], "wip")
        self.assertTrue(data["path"].endswith(f"_{self.step}_v001.blend"))

    def test_scenefile_unknown_version_404(self):
        status, _, _ = self._request(
            "/api/open-blender", method="POST",
            body={"entity": "PROP_Tente_Default", "step": self.step, "scenefile": 99})
        self.assertEqual(status, 404)

    def test_scenefile_houdini_row_refused_400(self):
        status, _, body = self._request(
            "/api/open-blender", method="POST",
            body={"entity": "PROP_Tente_Default", "step": self.step, "scenefile": 3})
        self.assertEqual(status, 400)
        self.assertIn("houdini", json.loads(body)["error"].lower())

    def test_scenefile_without_step_400(self):
        status, _, _ = self._request(
            "/api/open-blender", method="POST",
            body={"entity": "PROP_Tente_Default", "scenefile": 1})
        self.assertEqual(status, 400)

    def test_plain_open_still_gets_per_session_env(self):
        """Existing contract untouched: 'Open the scene' keeps resolving server-side AND
        now also inherits the per-session env."""
        status, _, body = self._request(
            "/api/open-blender", method="POST",
            body={"entity": "PROP_Tente_Default", "step": self.step})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["kind"], "wip")
        self.assertEqual(self._popen.call_args.kwargs["env"]["PROJ_ROOT"],
                         str(self.project.resolve().parent))


class TestStepStatusApi(ServerTestCase):
    """POST /api/set-step-status — thin adapter to create_project.set_step_status (schema
    2.2). Only 'review'/'approved' are persisted; everything else is derived from disk, so
    the endpoint must refuse them rather than invent a second source of truth."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        info = cp.create("proj_status", root=str(cls._tmp / "stroot"),
                         cache=str(cls._tmp / "stcache"))
        cls.project = Path(info["source"])
        cp.create_asset(cls.project, "PROP_Tente_Default", asset_type="PROP")
        cls.step = cp.resolve_entity(cls.project, "PROP_Tente_Default")["manifest"]["steps"][0]
        cls._set_active(cls.project)

    def tearDown(self):
        # Leave the manifest clean for the next test (explicit status cleared).
        try:
            cp.set_step_status(self.project, "PROP_Tente_Default", self.step, cp.STEP_STATUS_AUTO)
        except (ValueError, FileNotFoundError):
            pass

    def _set(self, **body):
        return self._request("/api/set-step-status", method="POST", body=body)

    def test_config_exposes_the_status_vocabulary(self):
        # The selector in app.html must never hard-code the vocabulary (principle 5).
        status, _, body = self._request("/api/config")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["step_statuses"], cp.STEP_STATUSES)
        self.assertEqual(data["step_status_explicit"], list(cp.STEP_STATUS_EXPLICIT))
        self.assertEqual(data["step_status_auto"], cp.STEP_STATUS_AUTO)

    def test_set_review_persists_and_is_exposed(self):
        status, _, body = self._set(entity="PROP_Tente_Default", step=self.step,
                                    status="review")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["step_status"]["status"], "review")
        # Persisted in the manifest (source of truth, principle 3).
        manifest = cp.resolve_entity(self.project, "PROP_Tente_Default")["manifest"]
        self.assertEqual(manifest["step_status"][self.step], "review")
        # Surfaced by both cockpit payloads.
        _, _, detail = self._request("/api/asset/PROP_Tente_Default")
        self.assertEqual(json.loads(detail)["step_status"][self.step],
                         {"status": "review", "explicit": True, "derived": "empty"})
        _, _, grid = self._request("/api/assets")
        card = next(a for a in json.loads(grid)["assets"]
                    if a["name"] == "PROP_Tente_Default")
        self.assertEqual(card["step_status"][self.step], "review")

    def test_auto_clears_back_to_the_derived_value(self):
        self._set(entity="PROP_Tente_Default", step=self.step, status="approved")
        status, _, body = self._set(entity="PROP_Tente_Default", step=self.step,
                                    status=cp.STEP_STATUS_AUTO)
        self.assertEqual(status, 200)
        result = json.loads(body)["step_status"]
        self.assertFalse(result["explicit"])
        self.assertEqual(result["status"], result["derived"])
        manifest = cp.resolve_entity(self.project, "PROP_Tente_Default")["manifest"]
        self.assertNotIn("step_status", manifest)  # key dropped, never left empty

    def test_derived_status_cannot_be_set_400(self):
        status, _, body = self._set(entity="PROP_Tente_Default", step=self.step,
                                    status="published")
        self.assertEqual(status, 400)
        self.assertIn("published", json.loads(body)["error"])

    def test_undeclared_step_400(self):
        status, _, body = self._set(entity="PROP_Tente_Default", step="not_a_step",
                                    status="review")
        self.assertEqual(status, 400)
        self.assertIn("not declared", json.loads(body)["error"])

    def test_unknown_entity_404(self):
        status, _, _ = self._set(entity="PROP_Fantome_Default", step=self.step,
                                 status="review")
        self.assertEqual(status, 404)

    def test_missing_fields_400(self):
        self.assertEqual(self._set(step=self.step, status="review")[0], 400)
        self.assertEqual(self._set(entity="PROP_Tente_Default", status="review")[0], 400)


class TestRevealTarget(ServerTestCase):
    """_reveal_target: the folder to reveal is resolved SERVER-SIDE from names only
    (same rule as open-blender, INC-3) + POST /api/reveal (subprocess mocked)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        info = cp.create("proj_reveal", root=str(cls._tmp / "rvroot"),
                         cache=str(cls._tmp / "rvcache"))
        cls.project = Path(info["source"])
        cp.create_asset(cls.project, "PROP_Tente_Default", asset_type="PROP")
        cls.step = cp.resolve_entity(cls.project, "PROP_Tente_Default")["manifest"]["steps"][0]
        # Orphan (no manifest.json) — the 'broken' card must stay revealable so the user
        # can go fix it in the Finder.
        (cls.project / "sets" / "lecube" / "modeling").mkdir(parents=True, exist_ok=True)
        cls._set_active(cls.project)

    def test_entity_folder(self):
        target = ylos_ui._reveal_target(self.project, "PROP_Tente_Default", None)
        self.assertEqual(target, self.project / "assets" / "PROP_Tente_Default")

    def test_declared_step_folder(self):
        target = ylos_ui._reveal_target(self.project, "PROP_Tente_Default", self.step)
        self.assertEqual(target, self.project / "assets" / "PROP_Tente_Default" / self.step)

    def test_undeclared_step_refused(self):
        self.assertIsNone(
            ylos_ui._reveal_target(self.project, "PROP_Tente_Default", "not_a_step"))

    def test_orphan_entity_is_revealable(self):
        target = ylos_ui._reveal_target(self.project, "lecube", None)
        self.assertEqual(target, self.project / "sets" / "lecube")
        self.assertEqual(ylos_ui._reveal_target(self.project, "lecube", "modeling"),
                         self.project / "sets" / "lecube" / "modeling")

    def test_traversal_refused(self):
        for entity, step in (("..", None), ("../..", None), ("a/b", None),
                             ("PROP_Tente_Default", ".."), ("PROP_Tente_Default", "a/b")):
            self.assertIsNone(ylos_ui._reveal_target(self.project, entity, step),
                              f"traversal accepted for {entity!r}/{step!r}")

    def test_unknown_entity_404(self):
        status, _, _ = self._request("/api/reveal", method="POST",
                                     body={"entity": "PROP_Fantome_Default"})
        self.assertEqual(status, 404)

    def test_missing_entity_400(self):
        status, _, _ = self._request("/api/reveal", method="POST", body={})
        self.assertEqual(status, 400)

    def test_reveal_opens_the_resolved_folder(self):
        if sys.platform != "darwin":
            self.skipTest("reveal shells out to macOS 'open'")
        with patch.object(ylos_ui.subprocess, "Popen") as popen:
            status, _, body = self._request(
                "/api/reveal", method="POST",
                body={"entity": "PROP_Tente_Default", "step": self.step})
        self.assertEqual(status, 200)
        expected = str(self.project / "assets" / "PROP_Tente_Default" / self.step)
        self.assertEqual(json.loads(body)["path"], expected)
        self.assertEqual(popen.call_args.args[0], ["open", expected])


class TestCockpitPayload(ServerTestCase):
    """/api/assets and /api/asset/<name> as the entity view consumes them (Phase 2.x):
    per-step status, enriched publishes, multi-DCC scenefiles, dependency view, and the
    orphan still surfaced as a 'broken' card (never silently skipped)."""

    @classmethod
    def _publish(cls, entity, step, ext, comment=None, dependencies=None):
        staging, final = cp.allocate_publish_version(cls.project, entity, comment="", kind=step)
        version = cp.publish_version_from_dir(final)
        stem = f"{entity}_{step}_v{version:03d}"
        (staging / f"{stem}.{ext}").write_bytes(b"artifact")
        (staging / "thumb.png").write_bytes(b"png")
        cp.finalize_publish_version(cls.project, entity, staging, final, version,
                                    expected_artifacts=[stem, "thumb.png"],
                                    comment=comment, dependencies=dependencies)
        return version

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        info = cp.create("proj_payload", root=str(cls._tmp / "pyroot"),
                         cache=str(cls._tmp / "pycache"))
        cls.project = Path(info["source"])
        cp.create_asset(cls.project, "PROP_Tente_Default", asset_type="PROP")
        cp.create_asset(cls.project, "ANIMATION_Sh010_Default",
                        entity_type="shot", asset_type="ANIMATION")
        cls.asset_step = cp.resolve_entity(
            cls.project, "PROP_Tente_Default")["manifest"]["steps"][0]
        cls.shot_step = cp.resolve_entity(
            cls.project, "ANIMATION_Sh010_Default")["manifest"]["steps"][0]

        cls.v1 = cls._publish("PROP_Tente_Default", cls.asset_step, "usd", comment="blocking")
        cls.v2 = cls._publish("PROP_Tente_Default", cls.asset_step, "usd", comment="clean")
        # The shot pins v1 of the asset -> 'update available' (v2 exists).
        cls._publish("ANIMATION_Sh010_Default", cls.shot_step, "usd",
                     dependencies=[{"entity": "PROP_Tente_Default",
                                    "step": cls.asset_step, "version": cls.v1}])

        # Multi-DCC WIPs on the asset (Houdini .hipnc listed next to .blend).
        wip = cls.project / "assets" / "PROP_Tente_Default" / cls.asset_step / "wip"
        wip.mkdir(parents=True, exist_ok=True)
        (wip / f"PROP_Tente_Default_{cls.asset_step}_v001.blend").write_bytes(b"b")
        (wip / f"PROP_Tente_Default_{cls.asset_step}_v002.hipnc").write_bytes(b"h")

        # Orphan folder (real case of Ylos__Test: sets/lecube).
        (cls.project / "sets" / "lecube" / "modeling" / "wip").mkdir(parents=True, exist_ok=True)
        cls._set_active(cls.project)

    def _detail(self, name):
        status, _, body = self._request(f"/api/asset/{name}")
        self.assertEqual(status, 200)
        return json.loads(body)

    def _cards(self):
        status, _, body = self._request("/api/assets")
        self.assertEqual(status, 200)
        return {a["name"]: a for a in json.loads(body)["assets"]}

    def test_grid_card_carries_status_and_outdated(self):
        cards = self._cards()
        self.assertIn(self.asset_step, cards["PROP_Tente_Default"]["step_status"])
        self.assertEqual(cards["PROP_Tente_Default"]["step_status"][self.asset_step],
                         "published")
        self.assertEqual(cards["PROP_Tente_Default"]["outdated"], 0)
        # The shot pins an outdated version of the asset -> badge on the shot card.
        self.assertGreaterEqual(cards["ANIMATION_Sh010_Default"]["outdated"], 1)

    def test_orphan_still_a_broken_card(self):
        card = self._cards()["lecube"]
        self.assertTrue(card["broken"])
        self.assertEqual(card["step_status"], {})
        self.assertEqual(card["outdated"], 0)
        self.assertIn("modeling", card["steps"])   # disk sub-folders, no manifest

    def test_detail_publishes_are_enriched_rows(self):
        rows = self._detail("PROP_Tente_Default")["publishes"][self.asset_step]
        self.assertEqual([r["version"] for r in rows], [self.v1, self.v2])
        last = rows[-1]
        self.assertEqual(last["status"], "complete")
        self.assertEqual(last["ext"], "usd")
        self.assertEqual(last["comment"], "clean")
        self.assertTrue(last["exists"])
        self.assertTrue(last["published_utc"])
        self.assertTrue(last["thumb"].startswith("/thumb/PROP_Tente_Default/"))
        # Never an absolute path: the client imports by {entity, step, version}.
        self.assertNotIn("abs_path", last)

    def test_detail_scenefiles_carry_every_dcc(self):
        sf = self._detail("PROP_Tente_Default")["scenefiles"][self.asset_step]
        self.assertEqual([(r["version"], r["dcc"]) for r in sf],
                         [(1, "blender"), (2, "houdini")])
        self.assertTrue(all("path" not in r for r in sf))  # no absolute path client-side

    def test_detail_dependencies_uses_used_in_outdated(self):
        shot = self._detail("ANIMATION_Sh010_Default")["dependencies"]
        self.assertEqual([e["dependency"]["entity"] for e in shot["uses"]],
                         ["PROP_Tente_Default"])
        self.assertEqual(len(shot["outdated"]), 1)
        asset = self._detail("PROP_Tente_Default")["dependencies"]
        self.assertEqual([e["consumer"]["entity"] for e in asset["used_in"]],
                         ["ANIMATION_Sh010_Default"])
        self.assertEqual(asset["outdated"], [])

    def test_detail_step_status_is_the_full_orchestrator_dict(self):
        detail = self._detail("PROP_Tente_Default")
        entry = detail["step_status"][self.asset_step]
        self.assertEqual(set(entry), {"status", "explicit", "derived"})
        self.assertFalse(entry["explicit"])

    def test_detail_unknown_entity_404(self):
        status, _, _ = self._request("/api/asset/PROP_Fantome_Default")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
