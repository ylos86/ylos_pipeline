#!/usr/bin/env python3
"""
tests/test_ylos_ui.py — tests stdlib (unittest) pour ylos_ui.py.

Couvre la garde d'origine (anti drive-by localhost) : toute requête portant un Origin
hors YlosHandler.allowed_origins est rejetée en 403 AVANT tout traitement — y compris
'Origin: null' (file:// mais aussi iframe sandboxée d'un site hostile). Les requêtes
sans Origin (curl, navigation directe) passent.

Usage : python3 tests/test_ylos_ui.py
     ou : python3 -m unittest tests.test_ylos_ui
"""
from __future__ import annotations

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
import ylos_ui  # noqa: E402


class TestOriginGate(unittest.TestCase):
    """Serveur réel sur port éphémère ; ~/.ylos/recent_projects redirigé vers un tmpdir
    pour ne jamais toucher l'état utilisateur réel."""

    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory(prefix="ylos_ui_test_")
        cls._saved = (ylos_ui.RECENT_FILE, ylos_ui.YlosHandler.allowed_origins,
                      ylos_ui.YlosHandler.log_message)
        ylos_ui.RECENT_FILE = str(Path(cls._tmpdir.name) / "recent_projects")
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
        (ylos_ui.RECENT_FILE, ylos_ui.YlosHandler.allowed_origins,
         ylos_ui.YlosHandler.log_message) = cls._saved
        cls._tmpdir.cleanup()

    def _request(self, path, method="GET", origin=None, body=None):
        """Retourne (status, headers) — les erreurs HTTP sont des réponses, pas des exceptions."""
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
                return resp.status, dict(resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers)

    def test_no_origin_allowed(self):
        status, _ = self._request("/api/recent-projects")
        self.assertEqual(status, 200)

    def test_trusted_origin_allowed_and_echoed(self):
        origin = f"http://127.0.0.1:{self.port}"
        status, headers = self._request("/api/recent-projects", origin=origin)
        self.assertEqual(status, 200)
        # Écho de l'origine exacte, jamais '*'.
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), origin)
        self.assertEqual(headers.get("Vary"), "Origin")

    def test_localhost_variant_allowed(self):
        origin = f"http://localhost:{self.port}"
        status, headers = self._request("/api/recent-projects", origin=origin)
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), origin)

    def test_untrusted_origin_rejected_no_cors(self):
        status, headers = self._request("/api/recent-projects", origin="https://evil.example")
        self.assertEqual(status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_null_origin_rejected(self):
        # 'null' = file:// mais aussi iframe sandboxée hostile : jamais de confiance.
        status, _ = self._request("/api/recent-projects", origin="null")
        self.assertEqual(status, 403)

    def test_preflight_untrusted_rejected(self):
        status, headers = self._request("/api/set-project", method="OPTIONS",
                                        origin="https://evil.example")
        self.assertEqual(status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_preflight_trusted_passes(self):
        origin = f"http://127.0.0.1:{self.port}"
        status, headers = self._request("/api/set-project", method="OPTIONS", origin=origin)
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), origin)

    def test_post_untrusted_origin_gated_before_handler(self):
        # Une 'simple request' cross-site exécuterait ses effets de bord malgré CORS :
        # la garde doit répondre 403 (et non 400 'dossier introuvable', qui prouverait
        # que le handler a tourné).
        status, _ = self._request("/api/set-project", origin="https://evil.example",
                                  body={"path": "/nonexistent_ylos_test_dir"})
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
