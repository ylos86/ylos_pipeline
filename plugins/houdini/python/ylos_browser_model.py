"""ylos_browser_model.py - PURE view model of the Ylos Project Browser (the Houdini
Python Panel cockpit, plan-usable-v1 Phase 4.1).

NO hou, NO Qt, NO Houdini license: every row this module returns comes from
create_project.py (single source of truth, principle 5 of CLAUDE.md) and is a plain
serializable dict. The Qt layer (ylos_browser_panel.py) is dumb - it maps rows to widgets
and never derives pipeline data itself. This split is what makes the cockpit testable in
plain python3 on a machine where hython has no license (tests/test_houdini_browser_model.py).

The module also owns the panel's DISPLAY vocabulary (family labels, status colours,
status choices) - the Houdini equivalent of plugins/blender/core/vocab.py: the VALUES come
from create_project (STEP_STATUSES, ENTITY_DIR...), only the labels/colours live here, in
one place, never inline in a widget.

Loaded by the plugins/houdini/ylos.json package (PYTHONPATH), like ylos_houdini.py.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Same repo-root resolution as ylos_houdini.py: REAL path of this file (os.path.realpath,
# never bare __file__ - symlink fix), 4 dirname to reach the repo root.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.realpath(__file__)))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
import create_project as cp  # noqa: E402

# Families in cockpit order (the artist reads assets first, shots last), with their label.
# The VALUES are create_project's (cp.ENTITY_DIR keys); only the labels live here.
FAMILY_ORDER = ("asset", "set", "shot")
FAMILY_LABELS = {"asset": "Assets", "set": "Sets", "shot": "Shots"}

# Colour per step status. Same five values as cp.STEP_STATUSES (never a local list): a
# status added to the orchestrator without a colour here falls back to STATUS_COLOR_DEFAULT
# rather than raising - the cockpit degrades, it does not break.
STATUS_COLORS = {
    "empty": "#5c5c66",
    "wip": "#c8a02c",
    "published": "#3f8fd0",
    "review": "#d4762c",
    "approved": "#43a06b",
}
STATUS_COLOR_DEFAULT = "#5c5c66"

# What the 'set status' combo offers: the sentinel that CLEARS the explicit value first
# (the default state), then the only two persisted values (cp.STEP_STATUS_EXPLICIT).
STATUS_CHOICES = (cp.STEP_STATUS_AUTO,) + tuple(cp.STEP_STATUS_EXPLICIT)

# Scenefile extensions Houdini can actually open - a .blend is LISTED (a cockpit shows the
# whole task, every DCC) but never offered for opening.
_OPENABLE_EXTENSIONS = tuple(cp.SCENEFILE_EXTENSIONS.get("houdini", ()))


def status_color(status):
    """Hex colour of a step status (unknown status -> neutral, never an exception)."""
    return STATUS_COLORS.get(status, STATUS_COLOR_DEFAULT)


def is_project(path):
    """True if 'path' is an Ylos project root (its _pipeline/project.json is readable).
    Same test as ylos_houdini.parse_wip_context - the manifest makes the project, never
    the folder name (principle 3, CLAUDE.md)."""
    if not path:
        return False
    return (Path(path).expanduser() / cp.PIPELINE_DIR / cp.MANIFEST_NAME).is_file()


def active_project_info(active_file=None):
    """Header of the cockpit: {"path", "name", "prod_type", "exists", "manifest"} of the
    machine's active project (~/.ylos/active_project, cp.read_active_project - the same
    single source as the web UI, Blender and the ylos::publish HDA). No active project or
    a stale path -> {"path": None|str, "exists": False}: the panel says so instead of
    pretending. 'active_file' redirects the file (tests)."""
    root = cp.read_active_project(active_file)
    if root is None:
        return {"path": None, "name": "", "prod_type": "", "exists": False, "manifest": {}}
    root = Path(root)
    manifest = {}
    if is_project(root):
        try:
            manifest = cp.read_manifest(root)
        except (OSError, ValueError):
            manifest = {}
    return {
        "path": str(root),
        "name": manifest.get("name") or root.name,
        "prod_type": manifest.get("prod_type", ""),
        "exists": is_project(root),
        "manifest": manifest,
    }


def set_active_project(path, active_file=None):
    """Points the machine at another project: writes ~/.ylos/active_project EXACTLY as
    ylos_ui._write_active does (one line = the path, trailing newline, parent folder
    created). Deliberately the same contract, not an import: the web server must not be
    running for Houdini to switch project, and the file IS the contract.

    Refuses a folder that is not a project (no _pipeline/project.json): pointing the whole
    machine at a non-project would break every consumer at once. Returns the resolved Path."""
    root = Path(path).expanduser()
    if not is_project(root):
        raise ValueError(
            f"{root} is not an Ylos project (no {cp.PIPELINE_DIR}/{cp.MANIFEST_NAME})."
        )
    target = Path(active_file) if active_file else (Path.home() / ".ylos" / "active_project")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(root) + "\n", encoding="utf-8")
    return root


def entity_rows(project_root, search="", family=None):
    """[{name, family, family_label, entity_type, dir, steps, broken, thumbnail, label}] -
    the project's entities (cp.list_entities, orphans included and FLAGGED via 'broken'),
    sorted by FAMILY_ORDER then name.

    'search' filters case-insensitively on the name AND the sub-type (an artist types
    'lina' or 'character'). 'thumbnail' is cp.resolve_entity_thumbnail's dict
    ({"rel", "path", "source"}) - resolving it here costs nothing and spares the panel
    the cascade. Never raises: an unreadable project -> []."""
    project_root = Path(project_root)
    needle = (search or "").strip().lower()
    rows = []
    for ent in cp.list_entities(project_root, family=family):
        if needle and needle not in ent["name"].lower() \
                and needle not in str(ent.get("entity_type") or "").lower():
            continue
        thumb = cp.resolve_entity_thumbnail(project_root, ent["name"])
        rows.append({
            "name": ent["name"],
            "family": ent["family"],
            "family_label": FAMILY_LABELS.get(ent["family"], ent["family"]),
            "entity_type": ent.get("entity_type") or "",
            "dir": ent["dir"],
            "steps": list(ent.get("steps") or []),
            "broken": ent.get("broken"),
            "thumbnail": thumb,
            "label": ent["name"] if not ent.get("broken") else f"{ent['name']}  (broken)",
        })
    order = {f: i for i, f in enumerate(FAMILY_ORDER)}
    rows.sort(key=lambda r: (order.get(r["family"], len(order)), r["name"].lower()))
    return rows


def entity_groups(project_root, search=""):
    """[{"family", "label", "rows"}] - entity_rows grouped by family in FAMILY_ORDER, with
    the EMPTY groups dropped (a search that matches nothing must not leave three empty
    headers behind). The panel renders one tree top-level per group."""
    rows = entity_rows(project_root, search=search)
    groups = []
    for fam in FAMILY_ORDER:
        fam_rows = [r for r in rows if r["family"] == fam]
        if fam_rows:
            groups.append({"family": fam, "label": FAMILY_LABELS.get(fam, fam),
                           "rows": fam_rows})
    return groups


def step_rows(project_root, entity_name):
    """[{step, status, explicit, derived, color, scenefiles, products, latest_version}] -
    every step DECLARED for the entity, in the manifest's order (cp.get_step_status - the
    explicit schema-2.2 value wins, the rest is derived from disk).

    'scenefiles' / 'products' are counts (all DCCs together for the scenefiles, complete
    publishes only for the products): the cockpit must show at a glance where the work is,
    without a click. Unknown entity -> []."""
    statuses = cp.get_step_status(project_root, entity_name) or {}
    scenefiles = cp.list_scenefiles(project_root, entity_name)
    rows = []
    for step, info in statuses.items():
        complete = [p for p in cp.list_publishes(project_root, entity_name, step)
                    if p.get("status") == "complete"]
        latest = max((p.get("version", 0) for p in complete), default=None)
        rows.append({
            "step": step,
            "status": info["status"],
            "explicit": info["explicit"],
            "derived": info["derived"],
            "color": status_color(info["status"]),
            "scenefiles": len(scenefiles.get(step) or []),
            "products": len(complete),
            "latest_version": latest,
        })
    return rows


def scenefile_rows(project_root, entity_name, step):
    """[{version, filename, path, dcc, comment, user, date, dcc_version, openable, label}] -
    the step's versioned WIPs, EVERY DCC together (cp.list_scenefiles), newest first (an
    artist wants the last version at the top, unlike the orchestrator's ascending order).

    'openable' is True only for a Houdini scenefile: the panel shows the .blend of the
    Blender artist (the task is shared) but only offers 'Open' for a .hip*."""
    rows = list(cp.list_scenefiles(project_root, entity_name, step).get(step) or [])
    out = []
    for row in rows:
        row = dict(row)
        row["openable"] = Path(row["filename"]).suffix.lower() in _OPENABLE_EXTENSIONS
        comment = row.get("comment") or ""
        row["label"] = "v{:03d}  {}{}".format(
            row["version"], row.get("dcc") or "?", f"  -  {comment}" if comment else "")
        out.append(row)
    out.sort(key=lambda r: r["version"], reverse=True)
    return out


def product_rows(project_root, entity_name, step):
    """[{version, status, artifact, abs_path, exists, legacy, is_latest, comment,
    dependencies, label}] - the step's PUBLISHES (cp.list_publishes: two-phase contract +
    legacy flat files), newest first. 'is_latest' marks the max complete version - the one
    a sublayer/reference should normally pull. A missing file on disk is kept and flagged
    ('exists' False) rather than hidden: a broken publish is information."""
    rows = list(cp.list_publishes(project_root, entity_name, step))
    latest = max((r.get("version", 0) for r in rows if r.get("status") == "complete"),
                 default=None)
    out = []
    for row in rows:
        row = dict(row)
        row["is_latest"] = (row.get("status") == "complete"
                            and row.get("version") == latest)
        row["dependencies"] = list(row.get(cp.DEPENDENCIES_KEY) or [])
        flags = []
        if row["is_latest"]:
            flags.append("latest")
        if row.get("legacy"):
            flags.append("legacy")
        if not row.get("exists", True):
            flags.append("MISSING")
        comment = row.get("comment") or ""
        row["label"] = "v{:03d}  {}{}{}".format(
            row.get("version", 0), row.get("status", "?"),
            f"  [{', '.join(flags)}]" if flags else "",
            f"  -  {comment}" if comment else "")
        out.append(row)
    out.sort(key=lambda r: r.get("version", 0), reverse=True)
    return out


def _edge_label(entity, step, version):
    """'ENTITY / step v003' (or 'ENTITY / root' for an unpinned assembly reference)."""
    if not step:
        return f"{entity} / root"
    return f"{entity} / {step}" + (f" v{version:03d}" if version is not None else " latest")


def dependency_rows(project_root, entity_name, index=None):
    """{"uses": [...], "used_in": [...], "outdated": n} - the entity's dependency view
    (cp.entity_dependencies, Phase 3). Each row:
    {label, entity, step, version, source, update_available, latest_version}.

    'uses'    = the products this entity was built from ("Uses").
    'used_in' = the entities that consume this one ("Used in").
    'update_available' marks a row pinned to a version older than the latest complete
    publish of the dependency - the panel shows it as 'UPDATE AVAILABLE'. Pass a prebuilt
    'index' (cp.build_dependency_index) to avoid rescanning the project per entity."""
    deps = cp.entity_dependencies(project_root, entity_name, index=index)

    def _uses_row(edge):
        d = edge["dependency"]
        return {
            "label": _edge_label(d["entity"], d.get("step"), d.get("version")),
            "entity": d["entity"], "step": d.get("step"), "version": d.get("version"),
            "latest_version": d.get("latest_version"), "source": edge["source"],
            "update_available": bool(d.get("outdated")),
        }

    def _used_in_row(edge):
        c, d = edge["consumer"], edge["dependency"]
        return {
            "label": _edge_label(c["entity"], c.get("step"), c.get("version")),
            "entity": c["entity"], "step": c.get("step"), "version": c.get("version"),
            "latest_version": d.get("latest_version"), "source": edge["source"],
            # a consumer is "outdated" when IT pins an older version of US.
            "update_available": bool(d.get("outdated")),
        }

    return {
        "uses": [_uses_row(e) for e in deps["uses"]],
        "used_in": [_used_in_row(e) for e in deps["used_in"]],
        "outdated": len(deps["outdated"]),
    }


def set_status(project_root, entity_name, step, status):
    """Persists a step's EXPLICIT status, or clears it (cp.STEP_STATUS_AUTO). Pure
    delegation to cp.set_step_status - the ONLY writer of the schema-2.2 field - kept here
    so the Qt layer never imports create_project directly (it talks to the model, period).
    Returns the resulting {"status", "explicit", "derived"} dict; raises ValueError on an
    undeclared step / invalid status."""
    return cp.set_step_status(project_root, entity_name, step, status)


def starter_preview(project_root, entity_name, step):
    """What 'New Scene' WOULD create, without creating anything: {"ok", "reason", "version",
    "stem", "references", "camera", "lighting", "frame_range", "label"} derived from
    cp.scene_starter_spec(..., dcc='houdini'). The panel confirms with this BEFORE calling
    ylos_houdini.create_scene(), which clears the current hip - a destructive gesture is
    never blind."""
    spec = cp.scene_starter_spec(entity_name, step, "houdini", project_root=project_root)
    if not spec.get("ok"):
        return {"ok": False, "reason": spec.get("reason", "starter refused"),
                "label": spec.get("reason", "starter refused")}
    wip = spec["wip"]
    fr = spec.get("frame_range")
    bits = [f"v{wip['version']:03d}"]
    refs = spec.get("references") or []
    bits.append(f"{len(refs)} reference(s)" if refs else "empty stage")
    if spec.get("camera"):
        bits.append("camera")
    if spec.get("lighting"):
        bits.append("dome light")
    if fr:
        bits.append(f"frames {fr.get('start')}-{fr.get('end')}")
    return {
        "ok": True,
        "reason": None,
        "version": wip["version"],
        "stem": wip["stem"],
        "references": [r["path"] for r in refs],
        "camera": bool(spec.get("camera")),
        "lighting": bool(spec.get("lighting")),
        "frame_range": fr,
        "label": "  |  ".join(bits),
    }
