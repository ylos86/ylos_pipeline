#!/usr/bin/env python3
"""
ylos_ui.py — Serveur HTTP local stdlib-only pour l'UI Ylos Prod.

Gère le projet actif via ~/.ylos/active_project (chemin absolu, une ligne).
CORS activé pour les pages file:// (app.html local).

Usage:
    python3 ylos_ui.py [--project /chemin] [--port 8765]

Endpoints:
    GET  /api/project          retourne project.json du projet actif
    GET  /api/assets           liste assets/* sets/* (manifest + dernière version + thumb)
    GET  /api/asset/<name>     détail + toutes les versions par step
    POST /api/open-blender     {path} ouvre Blender.app (non-bloquant)
    POST /api/set-project      {path} définit le projet actif
    GET  /thumb/<asset>/<file> fichier statique depuis publish/
"""
from __future__ import annotations

import argparse
import json
import mimetypes
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
DEFAULT_PORT = 8765
BLENDER_APP = Path("/Applications/Blender.app/Contents/MacOS/Blender")
THUMB_EXTS = {".png", ".jpg", ".jpeg", ".webp"}

# -------------------------------------------------------------------------------------
# Utilitaires
# -------------------------------------------------------------------------------------

def _cors(handler: BaseHTTPRequestHandler) -> None:
    handler.send_header("Access-Control-Allow-Origin", "*")
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


def _find_thumb(asset_dir: Path, asset_name: str) -> str | None:
    """Cherche la première image dans n'importe quel <step>/publish/.
    Retourne '<asset_name>/<filename>' → URL /thumb/<asset>/<file>."""
    for step_dir in sorted(asset_dir.iterdir()):
        if not step_dir.is_dir():
            continue
        pub = step_dir / "publish"
        if not pub.is_dir():
            continue
        for f in sorted(pub.iterdir()):
            if f.suffix.lower() in THUMB_EXTS:
                return f"{asset_name}/{f.name}"
    return None


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
            publishes = manifest.get("publishes", {})
            thumb = _find_thumb(asset_dir, asset_dir.name)
            result.append({
                "name": asset_dir.name,
                "family": family,
                "entity_type": manifest.get("entity_type"),
                "type": manifest.get("type"),
                "steps": manifest.get("steps", []),
                "last_versions": {
                    step: _last_publish(paths)
                    for step, paths in publishes.items()
                    if paths
                },
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

    def log_message(self, fmt, *args):
        print(f"[ylos] {self.command} {self.path} → {args[1] if len(args) > 1 else ''}")

    def do_OPTIONS(self):
        self.send_response(204)
        _cors(self)
        self.end_headers()

    def do_GET(self):
        p = self.path
        if p == "/api/project":
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
        else:
            _json(self, 404, {"error": "endpoint introuvable"})

    def do_POST(self):
        p = self.path
        if p == "/api/open-blender":
            self._post_open_blender()
        elif p == "/api/set-project":
            self._post_set_project()
        elif p == "/api/create-project":
            self._post_create_project()
        elif p == "/api/create-asset":
            self._post_create_asset()
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

        # rel = "<asset>/<filename>" — exactement deux segments
        parts = rel.lstrip("/").split("/", 1)
        if len(parts) != 2:
            self.send_response(400); self.end_headers()
            return
        asset_name, filename = parts

        # Interdire toute traversée dans le filename
        if ".." in filename or "/" in filename or "\\" in filename:
            self.send_response(400); self.end_headers()
            return

        # Cherche <filename> dans n'importe quel <step>/publish/ de l'asset
        file_path: Path | None = None
        for family in ("assets", "sets"):
            asset_dir = project_dir / family / asset_name
            if not asset_dir.is_dir():
                continue
            for step_dir in asset_dir.iterdir():
                if not step_dir.is_dir():
                    continue
                candidate = step_dir / "publish" / filename
                if candidate.is_file():
                    try:
                        candidate.resolve().relative_to(project_dir.resolve())
                        file_path = candidate
                    except ValueError:
                        pass
                    break
            if file_path:
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

    server = ThreadingHTTPServer(("127.0.0.1", args.port), YlosHandler)
    print(f"[ylos] http://127.0.0.1:{args.port}  (Ctrl-C pour arrêter)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[ylos] arrêt")
        server.shutdown()


if __name__ == "__main__":
    main()
