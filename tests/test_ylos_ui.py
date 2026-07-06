#!/usr/bin/env python3
"""
tests/test_ylos_ui.py — tests stdlib (unittest) pour ylos_ui.py.

Couvre :
  - la garde d'origine (anti drive-by localhost) : tout Origin hors allowed_origins
    rejete en 403 AVANT tout traitement — y compris 'Origin: null' (file:// mais aussi
    iframe sandboxee hostile). Sans Origin (curl, navigation directe) : passe.
  - /api/config : source unique types/steps (create_project.py), steps surcharges par
    le pipeline du projet actif.
  - /thumb/ : garde anti path-traversal ('..' interdit sur tout le chemin, asset_name
    compris), chemin legitime deux-phases servi.

Usage : python3 tests/test_ylos_ui.py
     ou : python3 -m unittest tests.test_ylos_ui
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

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
import create_project as cp  # noqa: E402
import ylos_ui  # noqa: E402


class ServerTestCase(unittest.TestCase):
    """Serveur réel sur port éphémère ; état ~/.ylos (actif + récents) redirigé vers un
    tmpdir pour ne jamais toucher l'état utilisateur réel."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory(prefix="ylos_ui_test_")
        cls._tmp = Path(cls._tmpdir.name)
        cls._saved = (ylos_ui.RECENT_FILE, ylos_ui.ACTIVE_FILE,
                      ylos_ui.YlosHandler.allowed_origins, ylos_ui.YlosHandler.log_message)
        ylos_ui.RECENT_FILE = cls._tmp / "recent_projects"
        ylos_ui.ACTIVE_FILE = cls._tmp / "active_project"
        ylos_ui.YlosHandler.log_message = lambda *a, **kw: None  # silence pendant les tests

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
        """Retourne (status, headers, body_bytes) — les erreurs HTTP sont des réponses,
        pas des exceptions."""
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
        """GET avec le chemin envoyé TEL QUEL (http.client) — urllib normalise les '..'
        avant envoi, ce qui rendrait les tests de traversal inoffensifs côté client."""
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
        # Écho de l'origine exacte, jamais '*'.
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
        # 'null' = file:// mais aussi iframe sandboxée hostile : jamais de confiance.
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
        # Une 'simple request' cross-site exécuterait ses effets de bord malgré CORS :
        # la garde doit répondre 403 (et non 400 'dossier introuvable', qui prouverait
        # que le handler a tourné).
        status, _, _ = self._request("/api/set-project", origin="https://evil.example",
                                     body={"path": "/nonexistent_ylos_test_dir"})
        self.assertEqual(status, 403)


class TestApiConfig(ServerTestCase):
    """/api/config : types depuis create_project.py (source unique, consommée par
    app.html::loadConfig à la place de son ancien FAMILY_CONFIG codé en dur), steps
    surchargés par le pipeline du projet actif."""

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
        # Les types ne sont jamais surchargés : c'est le contrat de validation.
        self.assertEqual(families["asset"]["types"], cp.ASSET_TYPES)
        # Clés set/shot non déclarées dans ce manifeste modifié : défauts du module.
        self.assertEqual(families["set"]["steps"], cp.DEFAULT_SET_STEPS)


class TestThumbSecurity(ServerTestCase):
    """Garde anti path-traversal de /thumb/ + service d'un thumb deux-phases légitime."""

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
        # Régression : '..' en asset_name restait DANS le projet (containment ok) mais
        # servait des fichiers hors contrat thumb (project.json...).
        status, _, _ = self._raw_request("/thumb/../_pipeline/project.json")
        self.assertEqual(status, 400)

    def test_deep_traversal_rejected(self):
        status, _, _ = self._raw_request(
            "/thumb/PROP_Tente_Default/../../../../../../etc/hosts")
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
