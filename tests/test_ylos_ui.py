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
  - _build_launch_argv : fonction pure de construction d'argv (INC-3).
  - POST /api/open-blender : resolution 100% serveur (create_project), jamais de chemin
    envoye par le client — regression du bug (segment 'assets/<entite>' manquant sur une
    concatenation naive project_root + rel) et verification qu'Importer cible la version
    EXACTE demandee, jamais 'latest'.

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
from unittest.mock import patch

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


class TestAssetScenefiles(ServerTestCase):
    """/api/asset/<name> expose 'scenefiles' (historique WIP + commentaire/user du sidecar
    '<wip>.blend.json' écrit par ylos.save_wip, INC-4) — lecture seule, tolérante à un
    sidecar absent/corrompu."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        info = cp.create("proj_scenefiles", root=str(cls._tmp / "sroot"),
                         cache=str(cls._tmp / "scache"))
        cls.project = Path(info["source"])
        cp.create_asset(cls.project, "PROP_Tente_Default", asset_type="PROP")

        wip_dir = cls.project / "assets" / "PROP_Tente_Default" / "modeling" / "wip"
        wip_dir.mkdir(parents=True, exist_ok=True)

        # v001 : sidecar conforme.
        (wip_dir / "PROP_Tente_Default_modeling_v001.blend").write_bytes(b"blend")
        (wip_dir / "PROP_Tente_Default_modeling_v001.blend.json").write_text(
            json.dumps({"comment": "blocking pass", "user": "seb",
                       "date": "2026-07-15T00:00:00+00:00", "blender_version": "5.1.1"}),
            encoding="utf-8")

        # v002 : PAS de sidecar (WIP legacy, avant INC-4) - ne doit jamais lever.
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
        self.assertNotIn("lookdev", data["scenefiles"])  # aucun WIP -> pas de cle


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


class TestWebPins(ServerTestCase):
    """Pinning web via l'API : /api/web-pins (état + disponibles), /api/pin-asset
    (validé contre les publishes GLB réels), /api/unpin-asset (idempotent), et le
    circuit complet pin -> set-web-target -> sync-web."""

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
        cls._publish("PROP_Tente_Default", "modeling", "usd")  # USD : jamais pinnable
        cls._set_active(cls.project)

    def _pins_state(self):
        status, _, body = self._request("/api/web-pins")
        self.assertEqual(status, 200)
        return json.loads(body)

    def test_available_lists_glb_only(self):
        state = self._pins_state()
        self.assertEqual(state["available"],
                         {"PROP_Tente_Default": {"lookdev": [1, 2]}})  # pas de modeling (USD)

    def test_pin_unpin_roundtrip(self):
        status, _, _ = self._request("/api/pin-asset", method="POST",
                                     body={"name": "PROP_Tente_Default",
                                           "step": "lookdev", "version": 2})
        self.assertEqual(status, 200)
        self.assertEqual(self._pins_state()["pins"],
                         {"PROP_Tente_Default": {"step": "lookdev", "version": 2}})
        # Persisté dans project.json (le contrat que sync_web_assets lit).
        manifest = cp.read_manifest(self.project)
        self.assertEqual(manifest["web"]["pinned_assets"]["PROP_Tente_Default"]["version"], 2)

        status, _, _ = self._request("/api/unpin-asset", method="POST",
                                     body={"name": "PROP_Tente_Default"})
        self.assertEqual(status, 200)
        self.assertEqual(self._pins_state()["pins"], {})
        # Idempotent : dé-pinner à nouveau reste un ok.
        status, _, _ = self._request("/api/unpin-asset", method="POST",
                                     body={"name": "PROP_Tente_Default"})
        self.assertEqual(status, 200)

    def test_pin_nonexistent_version_rejected(self):
        status, _, body = self._request("/api/pin-asset", method="POST",
                                        body={"name": "PROP_Tente_Default",
                                              "step": "lookdev", "version": 99})
        self.assertEqual(status, 400)
        self.assertIn("lookdev", json.loads(body)["error"])  # message liste les disponibles

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
        # Le circuit complet tel que le modal l'exécute : pin -> target -> sync.
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
    """_build_launch_argv est une fonction PURE (INC-3) : 'path' est déjà résolu par
    l'appelant, aucune reconstruction/concaténation de chemin ici."""

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
    """POST /api/open-blender — résolution 100% serveur (create_project), jamais de chemin
    envoyé par le client (cf. INC-3). subprocess.Popen mocké : on vérifie le chemin RÉSOLU
    (canonique, absolu, incluant le segment 'assets/<entité>' — c'était la cause du bug),
    jamais une vraie instance Blender lancée pendant les tests."""

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

        # WIP réel pour 'Ouvrir la scène' (kind='wip', ordre de résolution #1).
        wip_dir = cls.project / "assets" / "PROP_Tente_Default" / "modeling" / "wip"
        wip_dir.mkdir(parents=True, exist_ok=True)
        cls.wip_file = wip_dir / "PROP_Tente_Default_modeling_v001.blend"
        cls.wip_file.write_bytes(b"fake blend")

        # Deux versions publiées (lookdev) pour 'Importer' une version PRÉCISE (pas latest).
        cls.pub_v1 = cls._publish("lookdev", "usd")
        cls.pub_v2 = cls._publish("lookdev", "usd")

        cls._set_active(cls.project)

        cls._fake_blender = cls._tmp / "fake_blender_app"
        cls._fake_blender.write_bytes(b"")
        cls._fake_launcher = cls._tmp / "fake_launch_context.py"
        cls._fake_launcher.write_bytes(b"")

    def setUp(self):
        # Jamais de vraie instance Blender lancee pendant les tests : Popen mocke, binaire/
        # launcher pointes sur des fichiers factices (seul '.is_file()' compte ici). YLOS_DIR/
        # SERVER_LOG rediriges vers le tmpdir - jamais toucher ~/.ylos reel (meme discipline
        # que ServerTestCase pour RECENT_FILE/ACTIVE_FILE).
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
        # Régression du bug : le chemin résolu DOIT être le fichier WIP réel, segment
        # 'assets/<entité>' inclus (une concaténation naïve project_root + rel le sautait).
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


if __name__ == "__main__":
    unittest.main()
