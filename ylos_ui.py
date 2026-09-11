#!/usr/bin/env python3
"""
ylos_ui.py — Local stdlib-only HTTP server for the Ylos Prod UI.

Manages the active project via ~/.ylos/active_project (absolute path, one line).

Origin policy (anti drive-by localhost): any request carrying an Origin header
not listed in YlosHandler.allowed_origins (127.0.0.1/localhost on the active port) is
rejected with 403 BEFORE any processing — CORS alone is not enough, a "simple request"
(GET, POST text/plain) triggers its server side effects even if the browser blocks
reading the response. 'Origin: null' (file://, but also a sandboxed iframe of a
hostile site) is rejected: app.html is served via http://127.0.0.1:<port>/, no longer via file://.
Requests WITHOUT an Origin (curl, direct navigation) pass — they are not
cross-site requests emitted by a browser.

Usage:
    python3 ylos_ui.py [--project /path] [--port 8765]

Endpoints:
    GET  /api/project          returns project.json of the active project
    GET  /api/config           types/steps per family (single source: create_project.py,
                               steps overridden by the active project's pipeline) + the
                               step-status vocabulary (schema 2.2)
    GET  /api/assets           lists assets/* sets/* shots/* (manifest + latest version + thumb
                               + status per step + 'outdated' dependency count)
    GET  /api/asset/<name>     detail: status per step, scenefiles (every DCC, sidecar merged),
                               publishes per step (enriched), dependencies (uses / used_in /
                               outdated), thumbnail
    POST /api/open-blender     {entity, step?} opens the scene (WIP-first) OR
                               {entity, step, scenefile} opens a specific WIP version OR
                               {entity, step, version} imports a specific publish OR
                               {entity, step, create: true} builds a NEW department starter
                               scene (Scene Creator) — all resolved server-side
                               (create_project), never a path sent by the client
                               (non-blocking). The child Blender gets a per-session
                               $PROJ_ROOT/$PROJ_CACHE (never a global export).
    POST /api/set-step-status  {entity, step, status} persists an explicit step status
                               (review|approved) or clears it ('auto') — schema 2.2
    POST /api/reveal           {entity, step?} reveals the entity/step folder in the Finder
                               (path resolved server-side, never sent by the client)
    POST /api/set-project      {path} sets the active project
    POST /api/set-web-target   {target_dir} persists project.json["web"]["target_dir"]
    GET  /api/web-pins         current pins + available GLB publishes per asset
    POST /api/pin-asset        {name, step, version} pins a published GLB (validated)
    POST /api/unpin-asset      {name} removes the pin (idempotent)
    POST /api/sync-web         sync_web_assets() to web.target_dir (pinned assets)
    POST /api/set-frame-range  {entity, start, end, fps?} frame range of a shot (schema 2.1)
    GET  /thumb/<asset>/<rest> static file from <step>/publish/ (LOP or two-phase)
"""
from __future__ import annotations

import argparse
import json
import mimetypes
from email.utils import formatdate
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# create_project imported from the same folder — single logic, never duplicated.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import create_project  # noqa: E402

# -------------------------------------------------------------------------------------
# Config
# -------------------------------------------------------------------------------------

YLOS_DIR = Path.home() / ".ylos"
ACTIVE_FILE = YLOS_DIR / "active_project"
RECENT_FILE = YLOS_DIR / "recent_projects"
DEFAULT_PORT = 8765
# Blender binary: overridable via $YLOS_BLENDER (other OSes / non-standard installs).
BLENDER_APP = Path(os.environ.get("YLOS_BLENDER")
                   or "/Applications/Blender.app/Contents/MacOS/Blender")
THUMB_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
# Versioned launcher: EVERY DCC launch goes through it (see CLAUDE.md, end of --python-expr).
LAUNCHER = _HERE / "tools" / "blender" / "launch_context.py"
# Output of the launched Blender process (append) - no more silent DEVNULL (lesson CC#1b/#1d).
SERVER_LOG = YLOS_DIR / "launch-server.log"

# -------------------------------------------------------------------------------------
# Utilities
# -------------------------------------------------------------------------------------

def _allowed_origins(port: int) -> frozenset[str]:
    """Trusted origins = the server itself. app.html::BASE points to 127.0.0.1;
    'localhost' covers the case where the page is opened via http://localhost:<port>/ (a
    different origin from 127.0.0.1 for the browser, the same server in practice)."""
    return frozenset({f"http://127.0.0.1:{port}", f"http://localhost:{port}"})


def _cors(handler: BaseHTTPRequestHandler) -> None:
    """CORS headers only for a trusted origin, echoing the exact origin
    (never '*'). Without an Origin or with an unknown origin: no CORS header — the actual
    refusal (403) is done upstream by _origin_ok(), this is only the 'browser read' half."""
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
    # Single reader of the ~/.ylos/active_project contract (shared with the Houdini module).
    # ACTIVE_FILE passed as a parameter: tests redirect this constant to a tmpdir.
    return create_project.read_active_project(ACTIVE_FILE)


def _write_active(path: str) -> None:
    YLOS_DIR.mkdir(parents=True, exist_ok=True)
    ACTIVE_FILE.write_text(str(path) + "\n", encoding="utf-8")


def _load_recent() -> list[str]:
    try:
        data = json.loads(Path(RECENT_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return [p for p in data if isinstance(p, str)] if isinstance(data, list) else []


def _push_recent(path: str) -> None:
    path = str(Path(path).expanduser().resolve())
    recent = [p for p in _load_recent() if p != path]
    recent.insert(0, path)
    YLOS_DIR.mkdir(parents=True, exist_ok=True)
    create_project._atomic_write_json(RECENT_FILE, recent[:10])


def _read_asset_manifest(asset_dir: Path) -> dict | None:
    p = asset_dir / create_project.ASSET_MANIFEST_NAME
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _find_thumb(project_dir: Path, asset_name: str) -> tuple[str | None, str]:
    """THIN adapter to create_project.resolve_entity_thumbnail (single point, principle 5
    — the custom/publish/legacy/wip cascade lives in the orchestrator, shared with the Blender
    panel; duplicating it here would guarantee drift). Returns
    ('<asset_name>/<rel>', source) to build the /thumb/<asset>/<rest> URL, or (None, 'none')."""
    info = create_project.resolve_entity_thumbnail(project_dir, asset_name)
    rel = info.get("rel")
    return (f"{asset_name}/{rel}" if rel else None), info.get("source", "none")


def _last_versions(project_root: Path, entity_name: str, manifest: dict) -> dict:
    """Latest 'complete' publish per step - CANONICAL data only (version number,
    extension, thumbnail URL), NEVER a file path or a relative path:
    the client no longer reconstructs 'project_root + rel' to open/import (the exact
    cause of the fixed bug, see INC-3 - a two-phase publish's rel is relative to
    the ENTITY, not the project; naive concatenation skipped the 'assets/<entity>' segment).
    Resolved via the ORCHESTRATOR (create_project.latest_publish_artifact): nested two-phase +
    legacy flat files merged, max-version 'complete' entry. Opening/import
    goes through POST /api/open-blender {entity, step[, version]}, resolved server-side at
    click time (never when building this list)."""
    steps = set(manifest.get("step_publishes", {})) | set(manifest.get("publishes", {}))
    result: dict = {}
    for step in steps:
        latest = create_project.latest_publish_artifact(project_root, entity_name, step)
        if not latest or not latest.get("artifact"):
            continue
        thumb_rel = latest.get("thumbnail") or latest.get("thumb")
        result[step] = {
            "version": latest.get("version"),
            "ext": os.path.splitext(latest["artifact"])[1].lstrip("."),
            "thumb": f"/thumb/{entity_name}/{thumb_rel}" if thumb_rel else None,
        }
    return result


def _artifact_ext(artifact: str | None) -> str | None:
    """Extension label of a publish artifact ('usd', 'glb', 'bgeo.sc'...) — the longest
    known PUBLISH_ARTIFACT_EXTENSIONS suffix wins, so a compound extension is not cut."""
    if not artifact:
        return None
    low = artifact.lower()
    known = sorted(create_project.PUBLISH_ARTIFACT_EXTENSIONS, key=len, reverse=True)
    for ext in known:
        if low.endswith(ext):
            return ext.lstrip(".")
    return os.path.splitext(artifact)[1].lstrip(".").lower() or None


def _publish_rows(project_dir: Path, entity_name: str, step: str) -> list[dict]:
    """Enriched publish rows of ONE step for the cockpit (Products tab / version gallery),
    read through create_project.list_publishes (two-phase + legacy merged, principle 5).
    CANONICAL data only — version, status, ext, thumb URL, comment, dates, dependencies,
    exists — never an absolute path (the client imports through
    POST /api/open-blender {entity, step, version}, resolved at click time)."""
    rows = []
    for e in create_project.list_publishes(project_dir, entity_name, step):
        artifact = e.get("artifact") or ""
        thumb_rel = e.get("thumbnail") or e.get("thumb")
        rows.append({
            "version": e.get("version"),
            "status": e.get("status"),
            "ext": _artifact_ext(artifact),
            "filename": os.path.basename(artifact) if artifact else None,
            "thumb": f"/thumb/{entity_name}/{thumb_rel}" if thumb_rel else None,
            "comment": e.get("comment") or "",
            "published_utc": e.get("published_utc"),
            "reserved_utc": e.get("reserved_utc"),
            "dependencies": list(e.get("dependencies") or []),
            "exists": bool(e.get("exists")),
            "legacy": bool(e.get("legacy")),
        })
    return rows


def _scenefile_rows(project_dir: Path, entity_name: str) -> dict:
    """{step: [scenefile...]} from create_project.list_scenefiles (every DCC of
    SCENEFILE_EXTENSIONS — Houdini .hip* WIPs appear next to .blend ones, each row carries
    'dcc'). The absolute 'path' is stripped: the client opens a WIP through
    POST /api/open-blender {entity, step, scenefile: <version>}, resolved server-side."""
    out = {}
    for step, rows in create_project.list_scenefiles(project_dir, entity_name).items():
        out[step] = [{k: v for k, v in r.items() if k != "path"} for r in rows]
    return out


def _list_assets(project_dir: Path) -> list[dict]:
    """Grid cards — thin adapter over create_project.list_entities (single listing, orphans
    flagged). ORPHAN entity (folder under assets/sets/shots WITHOUT a readable
    manifest.json — never went through create_asset(): invalid naming, undeclared steps,
    never publishable): surfaced as a 'broken' card, never silently skipped — a ghost
    folder the tool hides is worse than a flagged one (the Blender panel scans the disk
    and lists it). Per-step status (schema 2.2) and the 'outdated' dependency count are
    added for every created entity; the dependency index is built ONCE per request
    (build_dependency_index scans the whole project), never once per entity."""
    index = create_project.build_dependency_index(project_dir)
    result: list[dict] = []
    for ent in create_project.list_entities(project_dir):
        name = ent["name"]
        family = create_project.ENTITY_DIR[ent["family"]]   # 'assets'|'sets'|'shots' (web contract)
        thumb, source = _find_thumb(project_dir, name)
        if ent["broken"]:
            result.append({
                "name": name,
                "family": family,
                "entity_type": None,
                "type": None,
                "steps": ent["steps"],            # disk sub-folders (no manifest)
                "last_versions": {},
                "step_status": {},
                "outdated": 0,
                "thumb": f"/thumb/{thumb}" if thumb else None,
                "thumb_source": source,
                "broken": ent["broken"],
            })
            continue
        manifest = ent["manifest"]
        statuses = create_project.get_step_status(project_dir, name) or {}
        deps = create_project.entity_dependencies(project_dir, name, index)
        result.append({
            "name": name,
            "family": family,
            "entity_type": manifest.get("entity_type"),
            "type": manifest.get("type"),
            "steps": ent["steps"],
            "last_versions": _last_versions(project_dir, name, manifest),
            "step_status": {s: v["status"] for s, v in statuses.items()},
            "outdated": len(deps["outdated"]),
            "frame_range": manifest.get("frame_range"),  # shots only; None for asset/set
            "thumb": f"/thumb/{thumb}" if thumb else None,
            "thumb_source": source,
            "broken": None,
        })
    return result


def _asset_detail(project_dir: Path, name: str) -> dict | None:
    """Entity cockpit payload (Phase 2.2): everything the drill-down needs in ONE call, all
    of it read through the orchestrator — status per step (get_step_status), scenefiles of
    every DCC (list_scenefiles), enriched publishes per step (list_publishes), dependency
    view (entity_dependencies: uses / used_in / outdated) and the thumbnail cascade.
    None for an unknown or orphan entity (no manifest -> nothing to drill into)."""
    resolved = create_project.resolve_entity(project_dir, name)
    if resolved is None:
        return None
    manifest = resolved["manifest"]
    steps = list(manifest.get("steps") or [])
    publish_steps = list(steps)
    for s in list(manifest.get("step_publishes") or {}) + list(manifest.get("publishes") or {}):
        if s not in publish_steps:
            publish_steps.append(s)
    publishes = {}
    for s in publish_steps:
        rows = _publish_rows(project_dir, name, s)
        if rows:
            publishes[s] = rows
    thumb, source = _find_thumb(project_dir, name)
    return {
        "name": name,
        "family": create_project.ENTITY_DIR[resolved["family"]],
        "path": resolved["dir"],
        "entity_type": manifest.get("entity_type"),
        "type": manifest.get("type"),
        "steps": steps,
        "step_status": create_project.get_step_status(project_dir, name) or {},
        "publishes": publishes,
        "step_publishes": manifest.get("step_publishes", {}),
        "last_versions": _last_versions(project_dir, name, manifest),
        "frame_range": manifest.get("frame_range"),  # shots only (schema 2.1); None otherwise
        "scenefiles": _scenefile_rows(project_dir, name),
        "dependencies": create_project.entity_dependencies(project_dir, name),
        "thumb": f"/thumb/{thumb}" if thumb else None,
        "thumb_source": source,
        "created_utc": manifest.get("created_utc"),
        "modified_utc": manifest.get("modified_utc"),
    }


def _glb_publishes(manifest: dict) -> dict:
    """{step: [sorted versions]} of 'complete' two-phase publishes whose artifact is
    a .glb — the only ones pinnable for the web (see sync_web_assets, which resolves the GLB via
    (step, version)). A USD publish never appears here."""
    out: dict = {}
    for step, entries in manifest.get("step_publishes", {}).items():
        versions = sorted(
            e["version"] for e in entries
            if e.get("status") == "complete" and (e.get("artifact") or "").endswith(".glb")
        )
        if versions:
            out[step] = versions
    return out


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
# DCC open/import — server-side resolution + argv construction (INC-3).
#
# The client NEVER sends a path anymore: two verbs, resolved here against
# the orchestrator, never by reconstructing 'project_root + rel' (that is exactly the
# cause of the fixed bug — a publish path is relative to the ENTITY, not the project; the
# naive concatenation skipped the 'assets/<entity>' segment and could break the
# root depending on the client-side path source).
#   - 'Open the scene'   : {entity, step?}          -> resolve_open_target (WIP-first).
#   - 'Import'           : {entity, step, version}  -> EXACT version (never 'latest'),
#                           via list_publishes (abs_path already canonical).
# -------------------------------------------------------------------------------------

def _resolve_publish_entry(project_dir: Path, entity: str, step: str, version: int) -> dict | None:
    """EXACT 'complete' entry (the requested version number, not the latest — Import can
    target any past version) for (entity, step, version). None if not found.
    'abs_path' comes as-is from create_project.list_publishes (already resolved by
    the orchestrator): no concatenation here."""
    if not step:
        return None
    for entry in create_project.list_publishes(project_dir, entity, step):
        if entry.get("version") == version and entry.get("status") == "complete":
            return entry
    return None


def _build_launch_argv(blender_app: Path, launcher: Path, project: Path, path: str | None,
                       kind: str, entity: str | None = None,
                       step: str | None = None, version: int | None = None) -> list[str]:
    """Argv of the Blender subprocess — PURE function: 'path' is already a canonical
    ABSOLUTE path provided by the caller (resolve_open_target / list_publishes), no path
    resolution or concatenation here. 'path' None (kind 'create': the launcher builds a NEW
    starter scene, the operator allocates the WIP itself) -> no '--path' argument."""
    argv = [str(blender_app), "--python", str(launcher), "--", "--project", str(project)]
    if entity:
        argv += ["--entity", str(entity)]
    if step:
        argv += ["--step", str(step)]
    if path:
        argv += ["--path", str(path)]
    if version is not None:
        # Import verb: the launcher routes through ylos.import_product with this exact
        # version (tagged collection) - never 'latest' when the user picked a version.
        argv += ["--version", str(int(version))]
    argv += ["--kind", kind]
    return argv


def _launch_env(project_dir: Path, base_env) -> dict:
    """Per-session environment of a launched DCC (plan-usable-v1 Phase 0.2) — PURE function,
    never touches os.environ. Returns a COPY of 'base_env' with:
      - PROJ_ROOT  = the PARENT of the project folder — the contract is
                     '$PROJ_ROOT/<project> == project_dir' (see
                     create_project._expand_proj_root / ylos_houdini.env_relative). ALWAYS
                     overridden: two Blender sessions launched on two projects from the same
                     UI must never share the shell's global value (tension #1 of CLAUDE.md);
      - PROJ_CACHE = the shell's value when set (kept verbatim), otherwise the orchestrator
                     fallback create_project.resolve_cache().
    The child inherits it through subprocess.Popen(env=...) only — never an export."""
    env = dict(base_env)
    env[create_project.ENV_ROOT] = str(Path(project_dir).resolve().parent)
    if not env.get(create_project.ENV_CACHE):
        env[create_project.ENV_CACHE] = str(create_project.resolve_cache())
    return env


def _reveal_target(project_dir: Path, entity: str, step: str | None) -> Path | None:
    """Folder to reveal for {entity, step?} — resolved SERVER-SIDE only (the client never
    sends a path). A created entity resolves through the orchestrator (resolve_entity);
    an ORPHAN folder (no manifest.json — the 'broken' card) is still revealable so the
    user can fix it in the Finder. 'step' must be a declared step (created entity) or an
    existing sub-folder (orphan). None when nothing matches."""
    if not entity or "/" in entity or "\\" in entity or entity in (".", ".."):
        return None
    resolved = create_project.resolve_entity(project_dir, entity)
    declared = None
    if resolved is not None:
        entity_dir = Path(resolved["dir"])
        declared = list(resolved["manifest"].get("steps") or [])
    else:
        entity_dir = next((project_dir / fam / entity for fam in create_project.ENTITY_DIR.values()
                           if (project_dir / fam / entity).is_dir()), None)
        if entity_dir is None:
            return None
    if not step:
        return entity_dir
    if "/" in step or "\\" in step or step in (".", "..") or (declared is not None and step not in declared):
        return None
    target = entity_dir / step
    return target if target.is_dir() else None


# -------------------------------------------------------------------------------------
# Handler HTTP
# -------------------------------------------------------------------------------------

class YlosHandler(BaseHTTPRequestHandler):

    # Recomputed in main() from the real port (--port). Default set here so the
    # handler stays usable imported as-is (tests, default port).
    allowed_origins = _allowed_origins(DEFAULT_PORT)

    def log_message(self, fmt, *args):
        print(f"[ylos] {self.command} {self.path} → {args[1] if len(args) > 1 else ''}")

    def _origin_ok(self) -> bool:
        """Anti drive-by guard: to call BEFORE any processing (GET/POST/OPTIONS).
        Origin absent = not a cross-site browser request -> ok. Origin present:
        only if it is trusted ('null' included in the rejection, see module docstring)."""
        origin = self.headers.get("Origin")
        return origin is None or origin in self.allowed_origins

    def _reject_origin(self) -> None:
        _json(self, 403, {"error": "Origin not allowed"})

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
        elif p == "/api/config":
            self._get_config()
        elif p == "/api/assets":
            self._get_assets()
        elif p.startswith("/api/asset/"):
            self._get_asset(p[len("/api/asset/"):])
        elif p.startswith("/thumb/"):
            # urlparse().path BEFORE slicing: a thumbnail is mutable at a constant URL,
            # so the client appends a cache-bust token ('?t=...'). Passing raw self.path
            # would look for a file named 'thumb.png?t=1786...' -> 404 on ALL
            # thumbnails as soon as the cache-bust exists client-side.
            self._get_thumb(urlparse(p).path[len("/thumb/"):])
        elif p == "/favicon.ico":
            self.send_response(204); self.end_headers()
        elif p.startswith("/api/browse"):
            self._get_browse()
        elif p == "/api/recent-projects":
            self._get_recent_projects()
        elif p == "/api/web-pins":
            self._get_web_pins()
        else:
            _json(self, 404, {"error": "endpoint not found"})

    def do_POST(self):
        if not self._origin_ok():
            self._reject_origin()
            return
        p = self.path
        if p == "/api/open-blender":
            self._post_open_blender()
        elif p == "/api/set-step-status":
            self._post_set_step_status()
        elif p == "/api/reveal":
            self._post_reveal()
        elif p == "/api/set-project":
            self._post_set_project()
        elif p == "/api/create-project":
            self._post_create_project()
        elif p == "/api/create-asset":
            self._post_create_asset()
        elif p == "/api/set-web-target":
            self._post_set_web_target()
        elif p == "/api/pin-asset":
            self._post_pin_asset()
        elif p == "/api/unpin-asset":
            self._post_unpin_asset()
        elif p == "/api/sync-web":
            self._post_sync_web()
        elif p == "/api/set-frame-range":
            self._post_set_frame_range()
        else:
            _json(self, 404, {"error": "endpoint not found"})

    # --- request utilities

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
            _json(self, 404, {"error": "app.html not found"}); return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _get_config(self):
        """Types and steps per family, consumed by app.html (replaces its old
        hard-coded FAMILY_CONFIG — single logic, see CLAUDE.md principle 5). Types
        come from the module (the only source validation knows); steps from the
        active project's pipeline if readable, otherwise from the module defaults — same
        resolution as create_project._project_steps, so the 'new asset' modal
        proposes exactly what create_asset() will do. Also exposes the step-status
        vocabulary (schema 2.2) so the cockpit's status selector is never hard-coded
        client-side: 'step_statuses' = every value that can be DISPLAYED,
        'step_status_explicit' = the only ones that can be SET (the rest are derived),
        'step_status_auto' = the sentinel that clears an explicit value."""
        families = {
            "asset": {"types": list(create_project.ASSET_TYPES),
                      "steps": list(create_project.DEFAULT_ASSET_STEPS)},
            "set":   {"types": list(create_project.SET_TYPES),
                      "steps": list(create_project.DEFAULT_SET_STEPS)},
            "shot":  {"types": list(create_project.SHOT_TYPES),
                      "steps": list(create_project.DEFAULT_SHOT_STEPS)},
        }
        project_dir = self._active()
        if project_dir is not None:
            try:
                pipeline = create_project.read_manifest(project_dir).get("pipeline", {})
            except (OSError, ValueError, json.JSONDecodeError):
                pipeline = {}
            for family, key in (("asset", "asset_steps"), ("set", "set_steps"),
                                ("shot", "shot_steps")):
                steps = pipeline.get(key)
                if steps:
                    families[family]["steps"] = list(steps)
        _json(self, 200, {
            "families": families,
            "step_statuses": list(create_project.STEP_STATUSES),
            "step_status_explicit": list(create_project.STEP_STATUS_EXPLICIT),
            "step_status_auto": create_project.STEP_STATUS_AUTO,
            "schema_version": create_project.SCHEMA_VERSION,
        })

    def _get_project(self):
        project_dir = self._active()
        if project_dir is None:
            _json(self, 404, {"error": "No active project — POST /api/set-project first"})
            return
        try:
            manifest = create_project.read_manifest(project_dir)
            manifest["_project_path"] = str(project_dir)
            # Resolved (never the raw field, absent/stale on an old project) - the
            # asset card derives the target badge from it (web -> .glb / offline -> .usd).
            manifest["pipeline_target"] = create_project.get_pipeline_target(project_dir)
            _json(self, 200, manifest)
        except FileNotFoundError:
            _json(self, 404, {"error": f"project.json not found in {project_dir}/_pipeline/"})
        except (json.JSONDecodeError, ValueError) as e:
            _json(self, 500, {"error": str(e)})

    def _get_assets(self):
        project_dir = self._active()
        if project_dir is None:
            _json(self, 404, {"error": "No active project"})
            return
        try:
            assets = _list_assets(project_dir)
            _json(self, 200, {"assets": assets, "count": len(assets)})
        except OSError as e:
            _json(self, 500, {"error": str(e)})

    def _get_asset(self, name: str):
        if not name:
            _json(self, 400, {"error": "Missing asset name in the URL"})
            return
        project_dir = self._active()
        if project_dir is None:
            _json(self, 404, {"error": "No active project"})
            return
        detail = _asset_detail(project_dir, name)
        if detail is None:
            _json(self, 404, {"error": f"Asset not found: {name!r}"})
        else:
            _json(self, 200, detail)

    def _get_thumb(self, rel: str):
        project_dir = self._active()
        if project_dir is None:
            self.send_response(404); self.end_headers()
            return

        # rel = "<asset>/<path relative to asset_dir>" - the second segment can
        # now contain '/' (two-phase publishes as a per-version folder, e.g.
        # 'modeling/publish/Asset_modeling_v003/thumb.png'), not only a flat file
        # name (old contract). The security guard is ".." + containment
        # (resolve().relative_to()) below, not the absence of '/'.
        rel = rel.lstrip("/")
        # '..' forbidden on the WHOLE path (asset_name included: '/thumb/../_pipeline/...'
        # would stay in the project thanks to containment, but would serve files outside
        # the thumb contract — manifests, etc.).
        if ".." in Path(rel).parts or "\\" in rel:
            self.send_response(400); self.end_headers()
            return
        parts = rel.split("/", 1)
        if len(parts) != 2:
            self.send_response(400); self.end_headers()
            return
        asset_name, sub_path = parts

        file_path: Path | None = None
        for family in ("assets", "sets", "shots"):
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
        # A thumbnail is MUTABLE at a constant URL: republishing a step rewrites thumb.png at
        # the same path. Without an explicit header the browser applies its cache heuristic
        # and may re-serve the old image after a publish — a freshness bug that reads
        # as "the thumbnail does not update". 'no-cache' = mandatory revalidation
        # (not 'no-store': with the ETag, a 304 still avoids resending the bytes).
        try:
            st = file_path.stat()
            self.send_header("ETag", f'"{int(st.st_mtime)}-{st.st_size}"')
            self.send_header("Last-Modified", formatdate(st.st_mtime, usegmt=True))
        except OSError:
            pass
        self.send_header("Cache-Control", "no-cache")
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
            _json(self, 400, {"error": f"Folder not found: {target}"})
            return
        try:
            entries = sorted(target.iterdir(), key=lambda p: p.name.lower())
        except PermissionError:
            _json(self, 403, {"error": f"Permission denied: {target}"})
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
        recent = [p for p in _load_recent() if Path(p).is_dir()]
        _json(self, 200, recent)

    def _get_web_pins(self):
        """Web pinning state: current pins (project.json['web']) + available GLB
        publishes per entity ({name: {step: [versions]}}) — what the Sync Web modal
        proposes in its selects. An entity with no GLB publish does not appear."""
        project_dir = self._active()
        if project_dir is None:
            _json(self, 404, {"error": "No active project"})
            return
        try:
            manifest = create_project.read_manifest(project_dir)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            _json(self, 500, {"error": str(e)})
            return
        web = manifest.get("web", {})
        available: dict = {}
        for family in ("assets", "sets", "shots"):
            family_dir = project_dir / family
            if not family_dir.is_dir():
                continue
            for asset_dir in sorted(family_dir.iterdir()):
                if not asset_dir.is_dir():
                    continue
                asset_manifest = _read_asset_manifest(asset_dir)
                if asset_manifest is None:
                    continue
                glb = _glb_publishes(asset_manifest)
                if glb:
                    available[asset_dir.name] = glb
        _json(self, 200, {
            "target_dir": web.get("target_dir"),
            "pins": web.get("pinned_assets", {}),
            "available": available,
        })

    # --- POST handlers

    def _post_open_blender(self):
        """POST /api/open-blender - FOUR verbs depending on the body, NEVER a path sent by
        the client (see INC-3):
          - {entity, step?}            -> 'Open the scene' (WIP-first, resolve_open_target).
          - {entity, step, scenefile}  -> open a SPECIFIC WIP version (list_scenefiles).
          - {entity, step, version}    -> 'Import' a SPECIFIC publish (list_publishes).
          - {entity, step, create}     -> 'New Scene': the department STARTER
                                          (scene_starter_spec validates, the launcher runs
                                          ylos.create_scene which allocates the WIP itself
                                          -> no '--path', kind 'create').
        The versioned launcher (tools/blender/launch_context.py) carries ALL DCC opening:
        pipeline context set + ops deferred to the first timer tick (context ready) +
        logging. The SERVER resolves a canonical ABSOLUTE path before building
        the argv (_build_launch_argv, pure function) - no manual reconstruction. The child
        gets a PER-SESSION $PROJ_ROOT/$PROJ_CACHE (_launch_env), never a global export."""
        body = self._body()
        if body is None:
            _json(self, 400, {"error": "Invalid JSON in the body"})
            return

        entity = body.get("entity")
        step = body.get("step")
        version = body.get("version")
        scenefile = body.get("scenefile")
        create = bool(body.get("create"))
        project = body.get("project")

        if not entity:
            _json(self, 400, {"error": "Missing 'entity' field"})
            return

        if project:
            project_dir = Path(project).expanduser().resolve()
        else:
            active = self._active()
            project_dir = active.resolve() if active is not None else None
        if not project_dir:
            _json(self, 400, {"error": "No project: provide 'project' or set the "
                                       "active project"})
            return

        # Blender binary required (overridable via $YLOS_BLENDER). Not found -> explicit
        # HTTP error, never a silent no-op (see observability lesson CC#1b).
        if not BLENDER_APP.is_file():
            _json(self, 500, {
                "error": f"Blender binary not found: {BLENDER_APP}. "
                         f"Set $YLOS_BLENDER to the Blender executable.",
            })
            return
        if not LAUNCHER.is_file():
            _json(self, 500, {"error": f"Launcher not found: {LAUNCHER}"})
            return

        if create:
            # 'New Scene' (Scene Creator, plan-usable-v1 Phase 1.4). The SERVER only
            # VALIDATES here (scene_starter_spec, the same pure spec the operator will
            # re-read in Blender) - it never allocates the WIP version itself: two
            # allocations for one click would burn a version number. Hence no '--path'.
            if not step:
                _json(self, 400, {"error": "Field 'step' required with 'create' (New Scene)"})
                return
            spec = create_project.scene_starter_spec(
                entity, step, "blender", project_root=project_dir,
            )
            if not spec.get("ok"):
                # Unknown entity -> 404 (nothing to create for); declared-step / project
                # problem -> 400 (client input), with the orchestrator's own reason.
                unknown = create_project.resolve_entity(project_dir, entity) is None
                _json(self, 404 if unknown else 400,
                      {"error": spec.get("reason") or f"Cannot create a scene for {entity!r}"})
                return
            path, kind, resolved_step = None, "create", step
        elif version is not None:
            try:
                version = int(version)
            except (TypeError, ValueError):
                _json(self, 400, {"error": f"invalid 'version': {version!r}"})
                return
            if not step:
                _json(self, 400, {"error": "Field 'step' required with 'version' (Import)"})
                return
            entry = _resolve_publish_entry(project_dir, entity, step, version)
            if entry is None or not entry.get("abs_path"):
                _json(self, 404, {
                    "error": f"Publish not found: {entity}/{step} v{version:03d}",
                })
                return
            path, kind, resolved_step = entry["abs_path"], "publish", step
        elif scenefile is not None:
            # Open a SPECIFIC WIP version of the version gallery. Resolved through
            # create_project.list_scenefiles (single scan point, every DCC) - the client
            # sends a version NUMBER, never a path.
            try:
                sf_version = int(scenefile)
            except (TypeError, ValueError):
                _json(self, 400, {"error": f"invalid 'scenefile': {scenefile!r}"})
                return
            if not step:
                _json(self, 400, {"error": "Field 'step' required with 'scenefile'"})
                return
            rows = create_project.list_scenefiles(project_dir, entity, step).get(step, [])
            match = next((r for r in rows if r.get("version") == sf_version), None)
            if match is None:
                _json(self, 404, {
                    "error": f"Scenefile not found: {entity}/{step} v{sf_version:03d}",
                })
                return
            if match.get("dcc") != "blender":
                # A .hip* WIP is listed by the cockpit (multi-DCC) but Blender cannot
                # open it - explicit refusal rather than a launcher that fails silently.
                _json(self, 400, {
                    "error": f"{match['filename']} is a {match.get('dcc')} scenefile — "
                             f"Blender cannot open it.",
                })
                return
            path, kind, resolved_step = match["path"], "wip", step
        else:
            target = create_project.resolve_open_target(
                entity, "blender", step, project_root=project_dir,
            )
            if not target.get("exists"):
                _json(self, 404, {
                    "error": target.get("reason") or f"Nothing to open for {entity!r}",
                })
                return
            path, kind = target["path"], target["kind"]
            resolved_step = target.get("step") or step

        args = _build_launch_argv(BLENDER_APP, LAUNCHER, project_dir, path, kind,
                                  entity=entity, step=resolved_step,
                                  version=version if kind == "publish" else None)

        try:
            YLOS_DIR.mkdir(parents=True, exist_ok=True)
            with open(SERVER_LOG, "a", encoding="utf-8") as log_fh:  # inherited by the child, no more DEVNULL
                subprocess.Popen(args, stdout=log_fh, stderr=log_fh,
                                 env=_launch_env(project_dir, os.environ))
            _json(self, 200, {"ok": True, "path": path, "kind": kind,
                              "project": str(project_dir), "entity": entity,
                              "step": resolved_step, "argv": args})
        except OSError as e:
            _json(self, 500, {"error": f"Cannot launch Blender: {e}"})

    def _post_set_step_status(self):
        """POST /api/set-step-status {entity, step, status} — persist an EXPLICIT step
        status (schema 2.2) or clear it ('auto'). Thin adapter → create_project.
        set_step_status (single point: validation + atomic write under the manifest flock).
        A business refusal (unknown step, status outside STEP_STATUS_EXPLICIT) is a
        ValueError → 400 with the orchestrator's message; an unknown entity is a
        FileNotFoundError → 404."""
        body = self._body()
        if body is None:
            _json(self, 400, {"error": "Invalid JSON in the body"})
            return
        project_dir = self._active()
        if project_dir is None:
            _json(self, 404, {"error": "No active project"})
            return
        entity = (body.get("entity") or "").strip()
        step = (body.get("step") or "").strip()
        if not entity:
            _json(self, 400, {"error": "Missing 'entity' field"})
            return
        if not step:
            _json(self, 400, {"error": "Missing 'step' field"})
            return
        status = body.get("status")
        if status is not None:
            status = str(status).strip()
        try:
            result = create_project.set_step_status(project_dir, entity, step, status)
        except ValueError as e:
            _json(self, 400, {"error": str(e)})
            return
        except FileNotFoundError as e:
            _json(self, 404, {"error": str(e)})
            return
        except OSError as e:
            _json(self, 500, {"error": str(e)})
            return
        _json(self, 200, {"ok": True, "entity": entity, "step": step,
                          "step_status": result})

    def _post_reveal(self):
        """POST /api/reveal {entity, step?} — reveal the entity (or one of its steps) in
        the Finder. The path is resolved SERVER-SIDE (_reveal_target: orchestrator for a
        created entity, disk for an orphan 'broken' card) — the client only ever sends
        names, never a path (same rule as open-blender, INC-3). macOS 'open' only:
        elsewhere the endpoint answers 501 rather than shelling out blindly."""
        body = self._body()
        if body is None:
            _json(self, 400, {"error": "Invalid JSON in the body"})
            return
        project_dir = self._active()
        if project_dir is None:
            _json(self, 404, {"error": "No active project"})
            return
        entity = (body.get("entity") or "").strip()
        if not entity:
            _json(self, 400, {"error": "Missing 'entity' field"})
            return
        step = (body.get("step") or "").strip() or None
        target = _reveal_target(project_dir, entity, step)
        if target is None:
            _json(self, 404, {"error": f"Nothing to reveal for {entity!r}"
                                       + (f" / {step!r}" if step else "")})
            return
        if sys.platform != "darwin":
            _json(self, 501, {"error": "Reveal in Finder is macOS-only",
                              "path": str(target)})
            return
        try:
            subprocess.Popen(["open", str(target)])
        except OSError as e:
            _json(self, 500, {"error": f"Cannot reveal {target}: {e}"})
            return
        _json(self, 200, {"ok": True, "path": str(target)})

    def _post_set_project(self):
        body = self._body()
        if body is None:
            _json(self, 400, {"error": "Invalid JSON in the body"})
            return
        path = body.get("path")
        if not path:
            _json(self, 400, {"error": "Missing 'path' field"})
            return

        project_dir = Path(path).expanduser().resolve()
        if not project_dir.is_dir():
            _json(self, 400, {"error": f"Folder not found: {project_dir}"})
            return
        try:
            create_project.read_manifest(project_dir)
        except FileNotFoundError:
            _json(self, 400, {"error": f"No project.json in {project_dir}/_pipeline/"})
            return
        except (json.JSONDecodeError, ValueError) as e:
            _json(self, 400, {"error": f"project.json invalid: {e}"})
            return

        _write_active(str(project_dir))
        _push_recent(str(project_dir))
        _json(self, 200, {"ok": True, "path": str(project_dir)})

    def _post_create_project(self):
        body = self._body()
        if body is None:
            _json(self, 400, {"error": "Invalid JSON in the body"})
            return
        name = body.get("name", "").strip()
        if not name:
            _json(self, 400, {"error": "Missing 'name' field"})
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
            _json(self, 400, {"error": "Invalid JSON in the body"})
            return
        name = body.get("name", "").strip()
        if not name:
            _json(self, 400, {"error": "Missing 'name' field"})
            return
        project_dir = self._active()
        if project_dir is None:
            _json(self, 404, {"error": "No active project"})
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
            first_step = used_steps[0] if used_steps else None
        except (OSError, json.JSONDecodeError):
            first_step = None
        # No path built here (see INC-3): the client reopens via
        # POST /api/open-blender {entity: name, step: first_step}, resolved canonically
        # (resolve_open_target degrades cleanly on a fresh entity without a WIP -> opens
        # scene_default, the asset_root/shot_root stub already written by create_asset()).
        _json(self, 200, {"ok": True, "asset_path": info["path"], "first_step": first_step})

    def _post_set_web_target(self):
        body = self._body()
        if body is None:
            _json(self, 400, {"error": "Invalid JSON in the body"})
            return
        project_dir = self._active()
        if project_dir is None:
            _json(self, 404, {"error": "No active project"})
            return
        try:
            result = create_project.set_web_target(project_dir, body.get("target_dir") or "")
        except (OSError, ValueError) as e:
            _json(self, 500, {"error": str(e)})
            return
        _json(self, 200, {"ok": True, "target_dir": result["target_dir"]})

    def _post_pin_asset(self):
        """Pin a published GLB: {name, step, version}. Thin adapter — validation
        (real 'complete' GLB publish) and atomic write live in the orchestrator
        (create_project.pin_web_asset, principle 5). A refused pin → 400 with the list of what
        exists (never a business exception surfaced from the module)."""
        body = self._body()
        if body is None:
            _json(self, 400, {"error": "Invalid JSON in the body"})
            return
        project_dir = self._active()
        if project_dir is None:
            _json(self, 404, {"error": "No active project"})
            return
        try:
            result = create_project.pin_web_asset(
                project_dir, body.get("name"), body.get("step"), body.get("version"))
        except (OSError, ValueError) as e:
            _json(self, 500, {"error": str(e)})
            return
        if not result.get("ok"):
            _json(self, 400, {"error": result.get("error", "pin refused")})
            return
        _json(self, 200, {"ok": True, "name": result["asset"],
                          "step": result["step"], "version": result["version"]})

    def _post_unpin_asset(self):
        """Remove an asset's pin. Idempotent. Thin adapter to
        create_project.unpin_web_asset (un-pinning an unpinned asset stays an ok)."""
        body = self._body()
        if body is None:
            _json(self, 400, {"error": "Invalid JSON in the body"})
            return
        project_dir = self._active()
        if project_dir is None:
            _json(self, 404, {"error": "No active project"})
            return
        name = (body.get("name") or "").strip()
        if not name:
            _json(self, 400, {"error": "Missing 'name' field"})
            return
        try:
            result = create_project.unpin_web_asset(project_dir, name)
        except (OSError, ValueError) as e:
            _json(self, 500, {"error": str(e)})
            return
        _json(self, 200, {"ok": True, "name": name, "was_pinned": result["was_pinned"]})

    def _post_sync_web(self):
        project_dir = self._active()
        if project_dir is None:
            _json(self, 404, {"error": "No active project"})
            return
        try:
            manifest = create_project.read_manifest(project_dir)
        except (OSError, ValueError) as e:
            _json(self, 500, {"error": str(e)})
            return
        target_dir = manifest.get("web", {}).get("target_dir")
        if not target_dir:
            _json(self, 400, {"error": "web.target_dir not configured - POST /api/set-web-target first"})
            return
        try:
            result = create_project.sync_web_assets(project_dir, target_dir)
        except (OSError, ValueError) as e:
            _json(self, 500, {"error": str(e)})
            return
        _json(self, 200, {"ok": True, **result})

    def _post_set_frame_range(self):
        """POST /api/set-frame-range {entity, start, end, fps?} — sets a SHOT's frame
        range. Thin adapter → create_project.set_frame_range (validation start<end + entity
        = shot + atomic write under lock + shot_root.usda recomposition, principle 5).
        set_frame_range RAISES for a business case (invalid range, not a shot, entity absent)
        → 400 (client input), never 500."""
        body = self._body()
        if body is None:
            _json(self, 400, {"error": "Invalid JSON in the body"})
            return
        project_dir = self._active()
        if project_dir is None:
            _json(self, 404, {"error": "No active project"})
            return
        name = (body.get("entity") or "").strip()
        if not name:
            _json(self, 400, {"error": "Missing 'entity' field"})
            return
        if body.get("start") is None or body.get("end") is None:
            _json(self, 400, {"error": "Fields 'start' and 'end' required"})
            return
        try:
            start, end = int(body["start"]), int(body["end"])
            fps = body.get("fps")
            fps = float(fps) if fps not in (None, "") else None
        except (TypeError, ValueError):
            _json(self, 400, {"error": "start/end must be integers, fps a number"})
            return
        try:
            frame_range = create_project.set_frame_range(project_dir, name, start, end, fps)
        except (ValueError, FileNotFoundError) as e:
            _json(self, 400, {"error": str(e)})
            return
        except OSError as e:
            _json(self, 500, {"error": str(e)})
            return
        _json(self, 200, {"ok": True, "entity": name, "frame_range": frame_range})


# -------------------------------------------------------------------------------------
# Entry point
# -------------------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Local stdlib-only HTTP server — Ylos Prod pipeline.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--project", metavar="PATH",
                        help="Active project (written to ~/.ylos/active_project)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help="Listening port")
    args = parser.parse_args()

    YLOS_DIR.mkdir(parents=True, exist_ok=True)

    if args.project:
        project_dir = Path(args.project).expanduser().resolve()
        _write_active(str(project_dir))
        print(f"[ylos] active project: {project_dir}")

    active = _read_active()
    if active:
        print(f"[ylos] current project: {active}")

    YlosHandler.allowed_origins = _allowed_origins(args.port)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), YlosHandler)
    print(f"[ylos] http://127.0.0.1:{args.port}  (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[ylos] stopping")
        server.shutdown()


if __name__ == "__main__":
    main()
