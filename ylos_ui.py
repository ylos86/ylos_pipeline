#!/usr/bin/env python3
"""
ylos_ui.py — Serveur HTTP local stdlib-only pour l'UI Ylos Prod.

Gère le projet actif via ~/.ylos/active_project (chemin absolu, une ligne).

Politique d'origine (anti drive-by localhost) : toute requête portant un header Origin
non listé dans YlosHandler.allowed_origins (127.0.0.1/localhost sur le port actif) est
rejetée en 403 AVANT tout traitement — CORS seul ne suffit pas, une "simple request"
(GET, POST text/plain) déclenche ses effets de bord serveur même si le navigateur bloque
la lecture de la réponse. 'Origin: null' (file://, mais aussi iframe sandboxée d'un site
hostile) est rejeté : app.html se consomme via http://127.0.0.1:<port>/, plus en file://.
Les requêtes SANS Origin (curl, navigation directe) passent — ce ne sont pas des
requêtes cross-site émises par un navigateur.

Usage:
    python3 ylos_ui.py [--project /chemin] [--port 8765]

Endpoints:
    GET  /api/project          retourne project.json du projet actif
    GET  /api/assets           liste assets/* sets/* (manifest + dernière version + thumb)
    GET  /api/asset/<name>     détail + toutes les versions par step
    POST /api/open-blender     {path} ouvre Blender.app (non-bloquant)
    POST /api/set-project      {path} définit le projet actif
    POST /api/set-web-target   {target_dir} persiste project.json["web"]["target_dir"]
    POST /api/sync-web         sync_web_assets() vers web.target_dir (assets pinnés)
    GET  /thumb/<asset>/<rest> fichier statique depuis <step>/publish/ (LOP ou deux-phases)
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# create_project importé depuis le même dossier — logique unique, jamais dupliquée.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import create_project  # noqa: E402

# -------------------------------------------------------------------------------------
# Config
# -------------------------------------------------------------------------------------

YLOS_DIR = Path.home() / ".ylos"
ACTIVE_FILE = YLOS_DIR / "active_project"
RECENT_FILE = os.path.expanduser("~/.ylos/recent_projects")
DEFAULT_PORT = 8765
BLENDER_APP = Path("/Applications/Blender.app/Contents/MacOS/Blender")
THUMB_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# -------------------------------------------------------------------------------------
# Utilitaires
# -------------------------------------------------------------------------------------

def _allowed_origins(port: int) -> frozenset[str]:
    """Origines de confiance = le serveur lui-même. app.html::BASE pointe sur 127.0.0.1 ;
    'localhost' couvre le cas où la page est ouverte via http://localhost:<port>/ (origine
    différente de 127.0.0.1 pour le navigateur, même serveur en pratique)."""
    return frozenset({f"http://127.0.0.1:{port}", f"http://localhost:{port}"})


def _cors(handler: BaseHTTPRequestHandler) -> None:
    """Headers CORS uniquement pour une origine de confiance, écho de l'origine exacte
    (jamais '*'). Sans Origin ou origine inconnue : aucun header CORS — le refus effectif
    (403) est fait en amont par _origin_ok(), ceci n'est que la moitié 'lecture navigateur'."""
    origin = handler.headers.get("Origin")
    if origin and origin in handler.allowed_origins:
        handler.send_header("Access-Control-Allow-Origin", origin)
        handler.send_header("Vary", "Origin")
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        handler.send_header("Access-Control-Allow-Headers", "Content-Type")


def _json(handler: BaseHTTPRequestHandler, code: int, data: object) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    _cors(handler)
    handler.end_headers()
    handler.wfile.write(body)


def _read_active() -> Path | None:
    if not ACTIVE_FILE.is_file():
        return None
    text = ACTIVE_FILE.read_text(encoding="utf-8").strip()
    return Path(text) if text else None


def _write_active(path: str) -> None:
    YLOS_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_FILE.write_text(str(path) + "\n", encoding="utf-8")


def _load_recent():
    try:
        return json.loads(open(RECENT_FILE).read())
    except Exception:
        return []


def _push_recent(path):
    recent = [p for p in _load_recent() if p != path]
    recent.insert(0, os.path.abspath(path))
    recent = recent[:10]
    os.makedirs(os.path.dirname(RECENT_FILE), exist_ok=True)
    open(RECENT_FILE, "w").write(json.dumps(recent, indent=2))


def _read_asset_manifest(asset_dir: Path) -> dict | None:
    p = asset_dir / create_project.ASSET_MANIFEST_NAME
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _last_publish(paths: list[str]) -> str | None:
    """Retourne le publish avec le numéro de version le plus élevé."""
    return max(paths, key=create_project._ver) if paths else None


def _latest_step_publish_thumb(manifest: dict) -> str | None:
    """Cherche le thumb du publish 'complete' le plus recent dans manifest['step_publishes']
    (contrat deux-phases generalise, cf. create_project.finalize_publish_version — kind=step).
    Retourne le chemin relatif a l'entite (ex 'modeling/publish/Asset_modeling_v003/thumb.png'),
    ou None si aucun publish de ce type n'existe (projet/asset legacy)."""
    best = None  # (version, thumb_rel_path)
    for entries in manifest.get("step_publishes", {}).values():
        for e in entries:
            if e.get("status") != "complete" or not e.get("thumb"):
                continue
            if best is None or e["version"] > best[0]:
                best = (e["version"], e["thumb"])
    return best[1] if best else None


def _find_thumb(asset_dir: Path, asset_name: str, manifest: dict | None = None) -> str | None:
    """Retourne '<asset_name>/<chemin relatif depuis asset_dir>' → URL /thumb/<asset>/<rest>.
    Priorite au contrat deux-phases (manifest['step_publishes'], thumb toujours present et
    localise sans scan disque) ; repli sur un scan plat de <step>/publish/ pour les assets/
    projets legacy (publish_asset(), sans thumbnail garanti)."""
    if manifest is not None:
        rel = _latest_step_publish_thumb(manifest)
        if rel and (asset_dir / rel).is_file():
            return f"{asset_name}/{rel}"

    for step_dir in sorted(asset_dir.iterdir()):
        if not step_dir.is_dir():
            continue
        pub = step_dir / "publish"
        if not pub.is_dir():
            continue
        for f in sorted(pub.iterdir()):
            if f.is_file() and f.suffix.lower() in THUMB_EXTS:
                return f"{asset_name}/{step_dir.name}/publish/{f.name}"
    return None


def _last_versions(manifest: dict) -> dict:
    """Dernier chemin publie (relatif a l'entite, contenant 'vNNN' - contrat consomme tel
    quel par app.html::verNum/openBlender) par step. Contrat deux-phases en priorite
    (manifest['step_publishes'], entree 'complete' de version max -> son 'artifact') puis
    repli sur l'ancien format a plat (manifest['publishes'], liste de chemins -
    publish_asset() legacy)."""
    result: dict = {}
    for step, entries in manifest.get("step_publishes", {}).items():
        complete = [e for e in entries if e.get("status") == "complete" and e.get("artifact")]
        if complete:
            result[step] = max(complete, key=lambda e: e["version"])["artifact"]
    for step, paths in manifest.get("publishes", {}).items():
        if paths and step not in result:
            result[step] = _last_publish(paths)
    return result


def _list_assets(project_dir: Path) -> list[dict]:
    result: list[dict] = []
    for family in ("assets", "sets"):
        family_dir = project_dir / family
        if not family_dir.is_dir():
            continue
        for asset_dir in sorted(family_dir.iterdir()):
            if not asset_dir.is_dir():
                continue
            manifest = _read_asset_manifest(asset_dir)
            if manifest is None:
                continue
            thumb = _find_thumb(asset_dir, asset_dir.name, manifest)
            result.append({
                "name": asset_dir.name,
                "family": family,
                "entity_type": manifest.get("entity_type"),
                "type": manifest.get("type"),
                "steps": manifest.get("steps", []),
                "last_versions": _last_versions(manifest),
                "thumb": f"/thumb/{thumb}" if thumb else None,
            })
    return result


def _asset_detail(project_dir: Path, name: str) -> dict | None:
    for family in ("assets", "sets", "shots"):
        asset_dir = project_dir / family / name
        if not asset_dir.is_dir():
            continue
        manifest = _read_asset_manifest(asset_dir)
        if manifest is None:
            return None
        return {
            "name": name,
            "family": family,
            "path": str(asset_dir),
            "entity_type": manifest.get("entity_type"),
            "type": manifest.get("type"),
            "steps": manifest.get("steps", []),
            "publishes": manifest.get("publishes", {}),
            "step_publishes": manifest.get("step_publishes", {}),
            "created_utc": manifest.get("created_utc"),
            "modified_utc": manifest.get("modified_utc"),
        }
    return None


def _is_project(path: Path) -> bool:
    return (path / create_project.PIPELINE_DIR / create_project.MANIFEST_NAME).is_file()


def _is_user_volume(p: Path) -> bool:
    try:
        real = p.resolve()
        return not (str(real) == '/' or
                    str(real).startswith('/System/') or
                    str(real).startswith('/private/'))
    except Exception:
        return False


# -------------------------------------------------------------------------------------
# Handler HTTP
# -------------------------------------------------------------------------------------

class YlosHandler(BaseHTTPRequestHandler):

    # Recalculé dans main() depuis le port réel (--port). Défaut posé ici pour que le
    # handler reste utilisable importé tel quel (tests, port par défaut).
    allowed_origins = _allowed_origins(DEFAULT_PORT)

    def log_message(self, fmt, *args):
        print(f"[ylos] {self.command} {self.path} → {args[1] if len(args) > 1 else ''}")

    def _origin_ok(self) -> bool:
        """Garde anti drive-by : à appeler AVANT tout traitement (GET/POST/OPTIONS).
        Origin absent = pas une requête cross-site navigateur -> ok. Origin présent :
        seulement s'il est de confiance ('null' inclus dans le rejet, cf. docstring module)."""
        origin = self.headers.get("Origin")
        return origin is None or origin in self.allowed_origins

    def _reject_origin(self) -> None:
        _json(self, 403, {"error": "Origine non autorisée"})

    def do_OPTIONS(self):
        if not self._origin_ok():
            self._reject_origin()
            return
        self.send_response(204)
        _cors(self)
        self.end_headers()

    def do_GET(self):
        if not self._origin_ok():
            self._reject_origin()
            return
        p = self.path
        if p in ("/", "/app.html"):
            self._get_app_html()
        elif p == "/api/project":
            self._get_project()
        elif p == "/api/assets":
            self._get_assets()
        elif p.startswith("/api/asset/"):
            self._get_asset(p[len("/api/asset/"):])
        elif p.startswith("/thumb/"):
            self._get_thumb(p[len("/thumb/"):])
        elif p == "/favicon.ico":
            self.send_response(204); self.end_headers()
        elif p.startswith("/api/browse"):
            self._get_browse()
        elif p == "/api/recent-projects":
            self._get_recent_projects()
        else:
            _json(self, 404, {"error": "endpoint introuvable"})

    def do_POST(self):
        if not self._origin_ok():
            self._reject_origin()
            return
        p = self.path
        if p == "/api/open-blender":
            self._post_open_blender()
        elif p == "/api/set-project":
            self._post_set_project()
        elif p == "/api/create-project":
            self._post_create_project()
        elif p == "/api/create-asset":
            self._post_create_asset()
        elif p == "/api/set-web-target":
            self._post_set_web_target()
        elif p == "/api/sync-web":
            self._post_sync_web()
        else:
            _json(self, 404, {"error": "endpoint introuvable"})

    # --- utilitaires de requête

    def _body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _active(self) -> Path | None:
        return _read_active()

    # --- GET handlers

    def _get_app_html(self):
        f = Path(__file__).parent / "app.html"
        try:
            data = f.read_bytes()
        except OSError:
            _json(self, 404, {"error": "app.html introuvable"}); return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _get_project(self):
        project_dir = self._active()
        if project_dir is None:
            _json(self, 404, {"error": "Aucun projet actif — POST /api/set-project d'abord"})
            return
        try:
            manifest = create_project.read_manifest(project_dir)
            manifest["_project_path"] = str(project_dir)
            _json(self, 200, manifest)
        except FileNotFoundError:
            _json(self, 404, {"error": f"project.json introuvable dans {project_dir}/_pipeline/"})
        except (json.JSONDecodeError, ValueError) as e:
            _json(self, 500, {"error": str(e)})

    def _get_assets(self):
        project_dir = self._active()
        if project_dir is None:
            _json(self, 404, {"error": "Aucun projet actif"})
            return
        try:
            assets = _list_assets(project_dir)
            _json(self, 200, {"assets": assets, "count": len(assets)})
        except OSError as e:
            _json(self, 500, {"error": str(e)})

    def _get_asset(self, name: str):
        if not name:
            _json(self, 400, {"error": "Nom d'asset manquant dans l'URL"})
            return
        project_dir = self._active()
        if project_dir is None:
            _json(self, 404, {"error": "Aucun projet actif"})
            return
        detail = _asset_detail(project_dir, name)
        if detail is None:
            _json(self, 404, {"error": f"Asset introuvable : {name!r}"})
        else:
            _json(self, 200, detail)

    def _get_thumb(self, rel: str):
        project_dir = self._active()
        if project_dir is None:
            self.send_response(404); self.end_headers()
            return

        # rel = "<asset>/<chemin relatif depuis asset_dir>" - le second segment peut
        # maintenant contenir des '/' (publishes deux-phases en dossier par version, ex
        # 'modeling/publish/Asset_modeling_v003/thumb.png'), pas seulement un nom de
        # fichier plat (ancien contrat). La garde de securite est ".." + containment
        # (resolve().relative_to()) ci-dessous, pas l'absence de '/'.
        parts = rel.lstrip("/").split("/", 1)
        if len(parts) != 2:
            self.send_response(400); self.end_headers()
            return
        asset_name, sub_path = parts

        if ".." in Path(sub_path).parts or "\\" in sub_path:
            self.send_response(400); self.end_headers()
            return

        file_path: Path | None = None
        for family in ("assets", "sets"):
            asset_dir = project_dir / family / asset_name
            candidate = asset_dir / sub_path
            if candidate.is_file():
                try:
                    candidate.resolve().relative_to(project_dir.resolve())
                    file_path = candidate
                except ValueError:
                    pass
                break

        if file_path is None:
            self.send_response(404); self.end_headers()
            return

        mime, _ = mimetypes.guess_type(str(file_path))
        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        _cors(self)
        self.end_headers()
        self.wfile.write(data)

    def _get_browse(self):
        parsed = urlparse(self.path)
        raw    = parse_qs(parsed.query).get("path", [""])[0].strip()

        if not raw:
            home = Path.home().resolve()
            dirs = [{"name": f"{home.name}  (~)", "path": str(home),
                     "is_project": _is_project(home)}]
            vol  = Path("/Volumes")
            if vol.is_dir():
                try:
                    for v in sorted(vol.iterdir(), key=lambda x: x.name.lower()):
                        if v.is_dir() and not v.name.startswith('.') and _is_user_volume(v):
                            dirs.append({"name": v.name, "path": str(v),
                                         "is_project": _is_project(v)})
                except PermissionError:
                    pass
            _json(self, 200, {"path": "", "parent": None, "dirs": dirs})
            return

        target = Path(raw).expanduser().resolve()
        if not target.exists() or not target.is_dir():
            _json(self, 400, {"error": f"Dossier introuvable : {target}"})
            return
        try:
            entries = sorted(target.iterdir(), key=lambda p: p.name.lower())
        except PermissionError:
            _json(self, 403, {"error": f"Permission refusée : {target}"})
            return

        dirs = []
        for entry in entries:
            if not entry.is_dir() or entry.name.startswith('.'):
                continue
            dirs.append({"name": entry.name, "path": str(entry),
                         "is_project": _is_project(entry)})

        parent = str(target.parent) if target.parent != target else None
        _json(self, 200, {"path": str(target), "parent": parent, "dirs": dirs})

    def _get_recent_projects(self):
        recent = [p for p in _load_recent() if os.path.isdir(p)]
        _json(self, 200, recent)

    # --- POST handlers

    def _post_open_blender(self):
        body = self._body()
        if body is None:
            _json(self, 400, {"error": "JSON invalide dans le body"})
            return
        path = body.get("path")
        if not path:
            _json(self, 400, {"error": "Champ 'path' manquant"})
            return
        try:
            if BLENDER_APP.is_file():
                subprocess.Popen(
                    [str(BLENDER_APP), str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                # Fallback macOS : open -a Blender <fichier>
                subprocess.Popen(
                    ["open", "-a", "Blender", str(path)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            _json(self, 200, {"ok": True, "path": str(path)})
        except OSError as e:
            _json(self, 500, {"error": f"Impossible de lancer Blender : {e}"})

    def _post_set_project(self):
        body = self._body()
        if body is None:
            _json(self, 400, {"error": "JSON invalide dans le body"})
            return
        path = body.get("path")
        if not path:
            _json(self, 400, {"error": "Champ 'path' manquant"})
            return

        project_dir = Path(path).expanduser().resolve()
        if not project_dir.is_dir():
            _json(self, 400, {"error": f"Dossier introuvable : {project_dir}"})
            return
        try:
            create_project.read_manifest(project_dir)
        except FileNotFoundError:
            _json(self, 400, {"error": f"Pas de project.json dans {project_dir}/_pipeline/"})
            return
        except (json.JSONDecodeError, ValueError) as e:
            _json(self, 400, {"error": f"project.json invalide : {e}"})
            return

        _write_active(str(project_dir))
        _push_recent(str(project_dir))
        _json(self, 200, {"ok": True, "path": str(project_dir)})

    def _post_create_project(self):
        body = self._body()
        if body is None:
            _json(self, 400, {"error": "JSON invalide dans le body"})
            return
        name = body.get("name", "").strip()
        if not name:
            _json(self, 400, {"error": "Champ 'name' manquant"})
            return
        prod_type = body.get("prod_type", "FILM")
        root = body.get("root") or None
        try:
            info = create_project.create(name, root=root, prod_type=prod_type)
        except (ValueError, FileExistsError) as e:
            _json(self, 400, {"error": str(e)})
            return
        except OSError as e:
            _json(self, 500, {"error": str(e)})
            return
        _write_active(info["source"])
        _push_recent(info["source"])
        try:
            manifest = create_project.read_manifest(info["source"])
        except (OSError, ValueError):
            manifest = {}
        _json(self, 200, {"ok": True, "project_path": info["source"], "manifest": manifest})

    def _post_create_asset(self):
        body = self._body()
        if body is None:
            _json(self, 400, {"error": "JSON invalide dans le body"})
            return
        name = body.get("name", "").strip()
        if not name:
            _json(self, 400, {"error": "Champ 'name' manquant"})
            return
        project_dir = self._active()
        if project_dir is None:
            _json(self, 404, {"error": "Aucun projet actif"})
            return
        entity_type = body.get("entity_type", "asset")
        asset_type  = body.get("asset_type", "OTHER")
        steps       = body.get("steps") or None
        try:
            info = create_project.create_asset(
                project_dir, name,
                entity_type=entity_type,
                asset_type=asset_type,
                steps=steps,
            )
        except (ValueError, FileExistsError) as e:
            _json(self, 400, {"error": str(e)})
            return
        except OSError as e:
            _json(self, 500, {"error": str(e)})
            return
        try:
            asset_manifest = json.loads(Path(info["manifest"]).read_text(encoding="utf-8"))
            used_steps = asset_manifest.get("steps", [])
            wip_path = str(Path(info["path"]) / used_steps[0] / "wip") if used_steps else None
        except (OSError, json.JSONDecodeError):
            wip_path = None
        _json(self, 200, {"ok": True, "asset_path": info["path"], "wip_path": wip_path})

    def _post_set_web_target(self):
        body = self._body()
        if body is None:
            _json(self, 400, {"error": "JSON invalide dans le body"})
            return
        project_dir = self._active()
        if project_dir is None:
            _json(self, 404, {"error": "Aucun projet actif"})
            return
        target_dir = (body.get("target_dir") or "").strip() or None
        try:
            manifest = create_project.read_manifest(project_dir)
        except (OSError, ValueError) as e:
            _json(self, 500, {"error": str(e)})
            return
        web = manifest.setdefault("web", {"target_dir": None, "pinned_assets": {}})
        web["target_dir"] = target_dir
        manifest["modified_utc"] = create_project._now()
        try:
            create_project.write_manifest(project_dir / create_project.PIPELINE_DIR, manifest)
        except OSError as e:
            _json(self, 500, {"error": str(e)})
            return
        _json(self, 200, {"ok": True, "target_dir": target_dir})

    def _post_sync_web(self):
        project_dir = self._active()
        if project_dir is None:
            _json(self, 404, {"error": "Aucun projet actif"})
            return
        try:
            manifest = create_project.read_manifest(project_dir)
        except (OSError, ValueError) as e:
            _json(self, 500, {"error": str(e)})
            return
        target_dir = manifest.get("web", {}).get("target_dir")
        if not target_dir:
            _json(self, 400, {"error": "web.target_dir non configure - POST /api/set-web-target d'abord"})
            return
        try:
            result = create_project.sync_web_assets(project_dir, target_dir)
        except (OSError, ValueError) as e:
            _json(self, 500, {"error": str(e)})
            return
        _json(self, 200, {"ok": True, **result})


# -------------------------------------------------------------------------------------
# Entrée
# -------------------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serveur HTTP local stdlib-only — pipeline Ylos Prod.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--project", metavar="PATH",
                        help="Projet actif (écrit dans ~/.ylos/active_project)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="Port d'écoute")
    args = parser.parse_args()

    YLOS_DIR.mkdir(parents=True, exist_ok=True)

    if args.project:
        project_dir = Path(args.project).expanduser().resolve()
        _write_active(str(project_dir))
        print(f"[ylos] projet actif : {project_dir}")

    active = _read_active()
    if active:
        print(f"[ylos] projet courant : {active}")

    YlosHandler.allowed_origins = _allowed_origins(args.port)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), YlosHandler)
    print(f"[ylos] http://127.0.0.1:{args.port}  (Ctrl-C pour arrêter)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[ylos] arrêt")
        server.shutdown()


if __name__ == "__main__":
    main()
