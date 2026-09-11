"""ylos_houdini.py - Ylos pipeline workflow in Houdini: versioned .hip WIPs, entity
creation, loading publishes/asset_root as LOPs.

Importable WITHOUT hou: the hou imports live in the functions that need them, the
pure functions (context parsing, versioning, path resolution) are testable in
plain python3 (see tests/test_ylos_houdini.py) and CI depends on no Houdini license.
All the creation/versioning/manifest logic comes from create_project.py (single
logic, see CLAUDE.md) - this module is only the Houdini bridge, as plugins/blender/ is
for Blender.

Loaded by the plugins/houdini/ylos.json package (PYTHONPATH); the shelf tools in
plugins/houdini/toolbar/ylos_pipeline.shelf call the tool_*() functions.

Context: the Houdini context IS the current hip path
(<project>/<family>/<entity>/<step>/wip/<file>) - no session state to maintain nor
to desynchronize. Blender stores its own in the scene (properties), same idea: the
context lives in the document, never in a global process state.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

# create_project imported from the repo root, derived from the REAL path of this file
# (os.path.realpath, never bare __file__ - same symlink fix as Blender and the HDA's
# embedded module, see build_publish_hda.py). 4 dirname: ylos_houdini.py -> python ->
# houdini -> plugins -> REPO.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.realpath(__file__)))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
import create_project as cp  # noqa: E402

# .hip commercial / .hiplc Indie / .hipnc Apprentice+Education: hou.hipFile.save refuses
# an extension of another license - same class of gotcha as .usdnc/.hdanc (see
# CLAUDE.md, Apprentice extensions, never assumed in advance).
HIP_EXTENSIONS = (".hip", ".hiplc", ".hipnc")
_HIP_EXT_BY_LICENSE = {"Commercial": ".hip", "Indie": ".hiplc"}  # everything else: .hipnc

# Name-agnostic version detection (only the _vNNN.ext suffix matters) - same
# philosophy as Blender core/asset.py::VERSION_PATTERN: a renamed/migrated file keeps
# its place in the version continuity.
WIP_VERSION_RE = re.compile(r"_v(\d{3})\.hip(?:lc|nc)?$")

# Shot renders: regenerable cache tier, under $PROJ_CACHE/<project>/render/ (mirror of
# CACHE_TREE). A render is disposable and its tracking belongs to production management
# (principle 4, CLAUDE.md), not the technical pipeline: versioning by disk scan (v<NNN>), no
# manifest. Only deliver_render() explicitly copies a valid take to delivery/.
RENDER_SUBDIR = "render"
RENDER_VERSION_RE = re.compile(r"^v(\d{3})$")


# --------------------------------------------------------------------------------------
# Pure functions (testable without hou)
# --------------------------------------------------------------------------------------

def hip_extension(license_category=None):
    """Save extension according to the license. 'license_category' (str, e.g. 'Commercial')
    injectable for tests; otherwise read from hou.licenseCategory() at call time."""
    if license_category is None:
        import hou
        license_category = hou.licenseCategory().name()
    return _HIP_EXT_BY_LICENSE.get(license_category, ".hipnc")


def active_project():
    """Active project (Path) or None - same source as the web UI and the HDA ylos::publish
    (~/.ylos/active_project, see create_project.read_active_project)."""
    return cp.read_active_project()


def parse_wip_context(hip_path):
    """(project_root, entity_name, step) if hip_path has the canonical form
    <project>/<family>/<entity>/<step>/wip/<file> of a real project (project.json
    present), otherwise None. Purely lexical + project manifest check: a path
    that has the right form outside a real project is not a context."""
    parts = Path(hip_path).parts
    if len(parts) < 6 or parts[-2] != "wip":
        return None
    family, entity_name, step = parts[-5], parts[-4], parts[-3]
    if family not in cp.ENTITY_DIR.values():
        return None
    project_root = Path(*parts[:-5])
    if not (project_root / cp.PIPELINE_DIR / cp.MANIFEST_NAME).is_file():
        return None
    return project_root, entity_name, step


def list_wip_versions(project_root, entity_name, step):
    """[{'version', 'filename', 'path'}, ...] sorted by version, all .hip* extensions
    together (a single WIP lineage may mix licenses)."""
    entity_dir, _ = cp._find_asset_entity(project_root, entity_name)
    wip_dir = entity_dir / step / "wip"
    if not wip_dir.is_dir():
        return []
    out = []
    for f in sorted(wip_dir.iterdir()):
        m = WIP_VERSION_RE.search(f.name)
        if f.is_file() and m:
            out.append({"version": int(m.group(1)), "filename": f.name, "path": str(f)})
    return sorted(out, key=lambda e: e["version"])


def next_wip_path(project_root, entity_name, step, license_category=None):
    """(path, version) of the NEXT WIP version: disk max + 1, never inferred from the
    current hip name (two sessions on the same step don't collide on the
    numbering). Validates 'step' against the entity's manifest (same message as
    publish_asset) - catches the typo before it creates a ghost tree."""
    entity_dir, manifest_path = cp._find_asset_entity(project_root, entity_name)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    declared = manifest.get("steps", [])
    if declared and step not in declared:
        raise ValueError(
            f"Step '{step}' invalid for '{entity_name}' (declared steps: {declared})."
        )
    versions = list_wip_versions(project_root, entity_name, step)
    version = (versions[-1]["version"] if versions else 0) + 1
    filename = f"{entity_name}_{step}_v{version:03d}{hip_extension(license_category)}"
    return entity_dir / step / "wip" / filename, version


def list_entities(project_root):
    """[{'name', 'family', 'type', 'steps'}, ...] of the project's entities (readable
    manifest), for the shelf tools' dialogs."""
    project_root = Path(project_root)
    out = []
    for family in cp.ENTITY_DIR.values():
        family_dir = project_root / family
        if not family_dir.is_dir():
            continue
        for d in sorted(family_dir.iterdir()):
            manifest_path = d / cp.ASSET_MANIFEST_NAME
            if not (d.is_dir() and manifest_path.is_file()):
                continue
            try:
                m = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            out.append({"name": d.name, "family": family,
                        "type": m.get("type"), "steps": m.get("steps", [])})
    return out


def asset_root_path(project_root, entity_name):
    """Path of an entity's asset_root.usda - the subLayers composition to reference in a
    set/shot. FileNotFoundError if absent (a shot has none, see create_asset)."""
    entity_dir, _ = cp._find_asset_entity(project_root, entity_name)
    path = entity_dir / cp.ASSET_ROOT_NAME
    if not path.is_file():
        raise FileNotFoundError(
            f"No {cp.ASSET_ROOT_NAME} for '{entity_name}' ({path}) - shots have "
            f"no asset_root, reference a LOP publish instead."
        )
    return path


def latest_lop_publish(project_root, entity_name):
    """Path of the USD layer of the entity's latest 'complete' LOP publish, or None
    if none. Reads manifest.lop_publishes (contract written by finalize_publish_version)."""
    entity_dir, manifest_path = cp._find_asset_entity(project_root, entity_name)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = [e for e in manifest.get(cp.LOP_PUBLISHES_KEY, [])
               if e.get("status") == "complete" and e.get("layer")]
    if not entries:
        return None
    best = max(entries, key=lambda e: e["version"])
    return entity_dir / best["layer"]


def latest_step_publish(project_root, entity_name, step):
    """Path of the artifact of the entity's latest 'complete' publish of step <step>,
    or None if none. Mirror of latest_lop_publish on manifest.step_publishes
    (contract written by finalize_publish_version with kind=<step>, key 'artifact') - feeds
    the shot_root composition and the manual sublayer of a specific step."""
    entity_dir, manifest_path = cp._find_asset_entity(project_root, entity_name)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = [e for e in manifest.get(cp.STEP_PUBLISHES_KEY, {}).get(step, [])
               if e.get("status") == "complete" and e.get("artifact")]
    if not entries:
        return None
    best = max(entries, key=lambda e: e["version"])
    return entity_dir / best["artifact"]


def shot_root_path(project_root, shot_name):
    """Path of a shot's shot_root.usda. Explicit FileNotFoundError if absent:
    until a step is published, refresh_entity_root() has not composed it yet."""
    entity_dir, _ = cp._find_asset_entity(project_root, shot_name)
    path = entity_dir / cp.SHOT_ROOT_NAME
    if not path.is_file():
        raise FileNotFoundError(
            f"No {cp.SHOT_ROOT_NAME} for '{shot_name}' ({path}) - publish at least "
            f"one step of the shot to compose the shot_root."
        )
    return path


def cache_dir_expression(project_root, entity_name, step, env_name=cp.ENV_CACHE):
    """LITERAL expression (unresolved variable) of a step's scratch cache folder:
    '$PROJ_CACHE/<project>/houdini/<entity>/<step>/'. Set as-is on a filecache SOP's 'basedir'
    - cache-side mirror of env_relative(): the path stays relocatable (the
    internal NVMe can change, the project moves) rather than hard-resolved. The
    v1/v2 versioning of disposable caches stays the filecache's native one (no manifest). The
    actual path resolution lives in create_project.entity_cache_dir (single logic). Pure (without hou)."""
    return f"${env_name}/{Path(project_root).name}/houdini/{entity_name}/{step}/"


def env_relative(path, env_name=cp.ENV_ROOT):
    """'$PROJ_ROOT/<relative>' if 'path' lives under $PROJ_ROOT - scenes reference via
    env, never a hard-coded absolute path (principle 1, CLAUDE.md): the project stays
    relocatable between internal and external disk. Absolute path otherwise (project outside
    the root: we don't invent a relocatability that does not exist)."""
    root = os.environ.get(env_name)
    if root:
        try:
            rel = Path(path).resolve().relative_to(Path(root).expanduser().resolve())
            return f"${env_name}/{rel.as_posix()}"
        except (ValueError, OSError):
            pass
    return str(Path(path))


def render_dir(project_root, shot_name, step):
    """Render folder (resolved on disk) of a shot step:
    $PROJ_CACHE/<project>/render/<shot>/<step>/ (regenerable tier, see CLAUDE.md). $PROJ_CACHE
    resolution via create_project.resolve_cache (single logic, same source as
    entity_cache_dir) - 'project_root' is the SOURCE root, only its basename names
    the cache subtree. Creates nothing (read/scan); versions are created by the render."""
    return (cp.resolve_cache() / Path(project_root).name / RENDER_SUBDIR
            / shot_name / step)


def list_render_versions(project_root, shot_name, step):
    """[int, ...] sorted render versions (v<NNN> folders) present on disk for
    <shot>/<step>. Empty if no render (folder absent). Pure scan, no manifest."""
    rdir = render_dir(project_root, shot_name, step)
    if not rdir.is_dir():
        return []
    out = [int(m.group(1)) for d in rdir.iterdir()
           if d.is_dir() and (m := RENDER_VERSION_RE.match(d.name))]
    return sorted(out)


def next_render_version(project_root, shot_name, step):
    """Next render version (max of the v<NNN> on disk + 1) for <shot>/<step>, or 1
    if no render. No manifest: a render is regenerable (cache tier) and its tracking
    belongs to production management, outside the technical pipeline (principle 4, CLAUDE.md). Pure."""
    versions = list_render_versions(project_root, shot_name, step)
    return (max(versions) + 1) if versions else 1


def render_output_expression(project_root, shot_name, step, version, env_name=cp.ENV_CACHE):
    """LITERAL expression (unresolved variable) of a render's EXR output file:
    '$PROJ_CACHE/<project>/render/<shot>/<step>/v<NNN>/<shot>_<step>_v<NNN>.$F4.exr'. Set
    as-is on the usdrender_rop's 'outputimage' - mirror of cache_dir_expression: the
    path stays relocatable (disposable tier, $PROJ_CACHE can change) rather than hard-
    resolved. '$F4' = Houdini frame number zero-padded to 4 digits (one image per frame). Pure."""
    stem = f"{shot_name}_{step}_v{version:03d}"
    return (f"${env_name}/{Path(project_root).name}/{RENDER_SUBDIR}/{shot_name}/{step}"
            f"/v{version:03d}/{stem}.$F4.exr")


def deliver_render(project_root, shot_name, step, version):
    """Explicit copy of a valid render take (v<NNN> from the cache tier) to
    delivery/render/<shot>/<step>/v<NNN>/ (permanent). The <step> is in the path: two
    steps delivered at the same version must NOT merge (copytree dirs_exist_ok=True would
    overwrite them silently). ONLY path that writes to delivery/: renders only arrive there
    by human validation, never automatically (see plan). Refuses if the
    source is absent or empty (nothing to deliver). No manifest (production management + regenerable
    source). Returns the Path of the delivered folder. Testable (shutil, without hou)."""
    project_root = Path(project_root)
    src = render_dir(project_root, shot_name, step) / f"v{version:03d}"
    if not src.is_dir() or not any(src.iterdir()):
        raise FileNotFoundError(
            f"Nothing to deliver: {src} absent or empty (render the step before delivering)."
        )
    dst = project_root / "delivery" / RENDER_SUBDIR / shot_name / step / f"v{version:03d}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, dirs_exist_ok=True)
    return dst


# --------------------------------------------------------------------------------------
# Pure functions - Prism parity (scene starter plan, WIP sidecar, publish dependencies)
# --------------------------------------------------------------------------------------

# Sidecar next to every Houdini WIP ('<hip>.json'), same Prism-style contract as the
# Blender operators ('<wip>.blend.json': comment / user / date / <dcc>_version) - read
# back by create_project.list_scenefiles (key 'houdini_version' -> 'dcc_version').
SIDECAR_SUFFIX = ".json"

# Starter prims (docs/usd-convention.md): the shot camera lives under /ROOT/cameras/
# (found by _first_shot_camera for the render); the starter dome light is a scratch
# hint, kept under the assembly root of the family (/ROOT for a shot, top level for an
# asset/set whose default prim is the entity itself).
STARTER_CAMERA_PRIMPATH = "/ROOT/cameras/cam_main"
STARTER_LIGHT_PRIMPATH = {"shot": "/ROOT/lights/dome_starter"}
STARTER_LIGHT_PRIMPATH_DEFAULT = "/lights/dome_starter"

# Pipeline context stamped on the Houdini session by create_scene (hou.putenv) - the
# document (hip path, parse_wip_context) stays the source of truth; these variables are a
# convenience for expressions ($YLOS_ENTITY in a filecache label, etc.) and for $JOB.
CONTEXT_ENV_PROJECT = "YLOS_PROJECT"
CONTEXT_ENV_ENTITY = "YLOS_ENTITY"
CONTEXT_ENV_STEP = "YLOS_STEP"

# '$JOB' / '${JOB}' with a real word boundary: a bare '\$\{?JOB\}?' would also match the
# '$JOB' PREFIX of an unrelated '$JOBS/...' and rewrite it into a half-expanded path.
_JOB_VAR_RE = re.compile(r"\$\{JOB\}|\$JOB(?![A-Za-z0-9_])")

# Python Panel interface name (plugins/houdini/python_panels/ylos_browser.pypanel). The
# shelf tool opens it BY NAME (hou.pypanel.interfaceByName), never by file path.
PANEL_INTERFACE_NAME = "ylos_browser"

# Publish HDA (tools/houdini/build_publish_hda.py). The UNVERSIONED name is tried as a
# fallback: Houdini resolves it to the installed version, so a future 0.3 keeps working
# without editing this bridge (extensions gotcha, CLAUDE.md: never assume a fixed name).
PUBLISH_HDA_TYPES = ("ylos::publish::0.2", "ylos::publish")


def wip_sidecar(comment, user, date, houdini_version):
    """Sidecar payload of a Houdini WIP: {comment, user, date, houdini_version}. Pure
    (values injected - the hou-side caller reads hou.userName()/applicationVersionString).
    The keys mirror the Blender sidecar so list_scenefiles shows both DCCs the same way."""
    return {
        "comment": comment or "",
        "user": user or "",
        "date": date or "",
        "houdini_version": houdini_version or "",
    }


def starter_plan(spec, license_category=None):
    """Serializable plan realizing a create_project.scene_starter_spec(..., dcc='houdini')
    in Houdini - the orchestrator decides WHAT (spec), this function decides HOW (which LOP
    nodes, which hip path); create_scene() only executes it. Pure: no hou (the license only
    picks the hip extension, injectable for tests).

    Returns {"ok": False, "reason"} when the spec is not ok, otherwise:
      ok, entity, step, family, version
      target:      absolute hip path '<wip.dir>/<wip.stem><ext>' (ext by license)
      sidecar:     '<target>.json' (wip_sidecar payload written next to the hip)
      fps:         frames per second to set (shot frame_range, else None = leave Houdini's)
      frame_range: [start, end] or None (shots with a manifest frame_range only)
      env:         {YLOS_PROJECT, YLOS_ENTITY, YLOS_STEP, JOB} to hou.putenv
      nodes:       ordered LOP chain to create in /stage, each {"type", "name", "parms"}:
                   assembly references -> 'sublayer' (shot: the shot IS the stage) or
                   'reference' (asset/set: grafted under /<entity>), then a 'camera'
                   (spec.camera) and a 'domelight' (spec.lighting). Paths in $PROJ_ROOT
                   (env_relative). Empty chain = empty scene (modeling/rigging starters).
      display:     name of the node to give the display flag (last of the chain) or None
      context:     the spec's context, passed through for the caller."""
    if not spec or not spec.get("ok"):
        return {"ok": False, "reason": (spec or {}).get("reason", "invalid starter spec")}
    if spec.get("dcc") not in (None, "houdini"):
        return {"ok": False, "reason": f"starter spec is for dcc {spec.get('dcc')!r}, not houdini"}

    wip = spec["wip"]
    ext = hip_extension(license_category)
    allowed = tuple(wip.get("extensions") or ())
    if allowed and ext not in allowed:
        return {"ok": False,
                "reason": f"hip extension {ext!r} not among the spec's {list(allowed)}"}
    target = Path(wip["dir"]) / (wip["stem"] + ext)
    family = spec.get("family", "asset")
    entity = spec["entity"]

    nodes = []
    for ref in spec.get("references") or []:
        if ref.get("kind") != "usd":
            continue
        path = env_relative(ref["path"])
        if family == "shot":
            # sublayer, never reference: the shot_root's root prim is /ROOT, it IS the stage.
            nodes.append({"type": "sublayer", "name": entity,
                          "parms": {"num_files": 1, "filepath1": path}})
        else:
            nodes.append({"type": "reference", "name": entity,
                          "parms": {"primpath": f"/{entity}", "filepath1": path}})
    if spec.get("camera"):
        nodes.append({"type": "camera", "name": "cam_main",
                      "parms": {"primpath": STARTER_CAMERA_PRIMPATH}})
    if spec.get("lighting"):
        nodes.append({"type": "domelight", "name": "dome_starter",
                      "parms": {"primpath": STARTER_LIGHT_PRIMPATH.get(
                          family, STARTER_LIGHT_PRIMPATH_DEFAULT)}})

    fr = spec.get("frame_range") if family == "shot" else None
    frame_range, fps = None, None
    if fr:
        try:
            frame_range = [int(fr["start"]), int(fr["end"])]
            fps = float(fr.get("fps") or cp.DEFAULT_SCENE["fps"])
        except (KeyError, TypeError, ValueError):
            frame_range, fps = None, None

    context = dict(spec.get("context") or {})
    project_root = context.get("project_root", "")
    return {
        "ok": True,
        "entity": entity,
        "step": spec["step"],
        "family": family,
        "version": wip["version"],
        "target": str(target),
        "sidecar": str(target) + SIDECAR_SUFFIX,
        "fps": fps,
        "frame_range": frame_range,
        "env": {
            CONTEXT_ENV_PROJECT: project_root,
            CONTEXT_ENV_ENTITY: entity,
            CONTEXT_ENV_STEP: spec["step"],
            "JOB": project_root,
        },
        "nodes": nodes,
        "display": nodes[-1]["name"] if nodes else None,
        "context": context,
    }


def stage_layer_paths(stage):
    """File paths a composed USD stage depends on: every used layer (sublayers, references,
    payloads loaded - pxr.Usd.Stage.GetUsedLayers) plus the asset paths each file-backed
    layer declares (GetCompositionAssetDependencies / legacy GetExternalReferences, resolved
    against the layer with ComputeAbsolutePath). Anonymous layers are skipped. Duck-typed on
    the pxr API (testable with stubs, no hou/pxr import here). Never raises: a layer that
    refuses introspection is skipped."""
    out, seen = [], set()

    def _add(path):
        path = str(path or "").strip()
        if not path or path.startswith("anon:") or path in seen:
            return
        seen.add(path)
        out.append(path)

    try:
        layers = list(stage.GetUsedLayers())
    except Exception:
        return out
    for layer in layers:
        try:
            if getattr(layer, "anonymous", False):
                continue
            _add(getattr(layer, "realPath", "") or getattr(layer, "identifier", ""))
            refs = None
            for attr in ("GetCompositionAssetDependencies", "GetExternalReferences"):
                fn = getattr(layer, attr, None)
                if fn is not None:
                    refs = fn()
                    break
            for ref in refs or ():
                ref = str(ref)
                resolver = getattr(layer, "ComputeAbsolutePath", None)
                _add(resolver(ref) if resolver is not None else ref)
        except Exception:
            continue
    return out


def dependencies_from_paths(paths, project_root, exclude_entity=None, expand_roots=True):
    """Map file paths a published stage used to publish dependencies
    [{"entity", "step", "version"}] for finalize_publish_version(dependencies=...).

    Built on create_project._classify_project_path - the SINGLE path -> entity mapper of
    the orchestrator (the same one build_dependency_index uses on the USD ASCII scan) - so
    Houdini never re-derives the project layout. Per path:
      - '$PROJ_ROOT'/'${PROJ_ROOT}' expanded like the bridges write it (env_relative:
        $PROJ_ROOT/<project> == project_root, via cp._expand_proj_root), '$JOB'/'${JOB}'
        expanded to project_root (create_scene sets $JOB), '~' expanded;
      - relative or 'anon:' paths, paths outside assets/ sets/ shots/ -> ignored;
      - coordinates of 'exclude_entity' (the publishing entity: its own stack is not a
        dependency, same rule as build_dependency_index) -> ignored;
      - an assembly root (asset_root.usda / shot_root.usda, step None) cannot be recorded
        as-is (finalize requires a step) -> with expand_roots it is expanded into the
        step publishes it composes right now (latest complete USD per step, the exact
        versions the stage was built from); without, it is dropped.
    Deduplicated, insertion order, versions int or None. Pure (no hou)."""
    project_root = Path(project_root)
    out, seen = [], set()

    def _push(coords):
        if coords is None or coords.get("entity") == exclude_entity or not coords.get("step"):
            return
        key = (coords["entity"], coords["step"], coords.get("version"))
        if key in seen:
            return
        seen.add(key)
        out.append({"entity": coords["entity"], "step": coords["step"],
                    "version": coords.get("version")})

    def _expand_root(entity):
        resolved = cp.resolve_entity(project_root, entity)
        if resolved is None:
            return
        entity_dir = Path(resolved["dir"])
        for rel in cp._latest_by_step(resolved["manifest"]).values():
            _push(cp._classify_project_path(entity_dir / rel, project_root))

    for raw in paths or ():
        raw = str(raw or "").strip()
        if not raw or raw.startswith("anon:"):
            continue
        raw = cp._expand_proj_root(raw, project_root)
        raw = _JOB_VAR_RE.sub(lambda _m: str(project_root), raw)
        raw = os.path.expanduser(raw)
        if raw.startswith("$") or not os.path.isabs(raw):
            continue   # unexpanded variable ($PROJ_CACHE...) or relative: never a source dep
        coords = cp._classify_project_path(raw, project_root)
        if coords is None:
            continue
        if coords.get("step") is None:
            if expand_roots and coords["entity"] != exclude_entity:
                _expand_root(coords["entity"])
            continue
        _push(coords)
    return out


# --------------------------------------------------------------------------------------
# Houdini actions (hou required)
# --------------------------------------------------------------------------------------

def write_wip_sidecar(hip_path, comment="", user=None, date=None, houdini_version=None):
    """Writes '<hip>.json' next to a saved WIP - the Prism-style sidecar that
    create_project.list_scenefiles merges into the scenefile rows (comment / user / date /
    houdini_version, see wip_sidecar). Atomic write (cp._atomic_write_json, same contract as
    the Blender op_save_wip). 'user'/'houdini_version' None -> read from hou at call time
    (lazy import: fully injectable, hence testable without Houdini). Returns the Path."""
    if user is None or houdini_version is None:
        import hou
        if user is None:
            user = hou.userName()
        if houdini_version is None:
            houdini_version = hou.applicationVersionString()
    if date is None:
        date = cp._now()
    path = Path(str(hip_path) + SIDECAR_SUFFIX)
    cp._atomic_write_json(path, wip_sidecar(comment, user, date, houdini_version))
    return path


def save_wip(entity_name=None, step=None, project_root=None, comment=""):
    """Saves the current hip as the next WIP version of <entity>/<step>, plus its sidecar
    '<hip>.json' (comment / user / date / houdini_version). Without arguments: context
    inferred from the current hip path (parse_wip_context). Returns
    {'path', 'version', 'sidecar'}.

    The sidecar is BEST-EFFORT (warning on stderr, never an exception): a metadata write
    failure must never lose the .hip that is already on disk - same rule as the Blender
    op_save_wip."""
    import hou
    if entity_name is None or step is None:
        ctx = parse_wip_context(hou.hipFile.path())
        if ctx is None:
            raise ValueError(
                "Current hip outside the pipeline (expected .../<entity>/<step>/wip/) - "
                "specify entity_name and step."
            )
        project_root, entity_name, step = ctx
    if project_root is None:
        project_root = active_project()
    if project_root is None:
        raise ValueError(
            "No active project (~/.ylos/active_project) and project_root not provided."
        )
    path, version = next_wip_path(project_root, entity_name, step)
    path.parent.mkdir(parents=True, exist_ok=True)
    hou.hipFile.save(str(path))
    sidecar = None
    try:
        sidecar = write_wip_sidecar(path, comment)
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"[warn] WIP sidecar not written ({path}{SIDECAR_SUFFIX}): {exc}\n")
    return {"path": str(path), "version": version,
            "sidecar": str(sidecar) if sidecar is not None else None}


def create_scene(entity_name, step, project_root=None, comment=""):
    """Creates a NEW authoring scene for <entity>/<step> in Houdini (plan-usable-v1 Phase
    1.3) - the Houdini mirror of the Blender op_create_scene. The orchestrator decides WHAT
    (cp.scene_starter_spec), starter_plan decides HOW (which LOP chain, which hip path), and
    only THIS function touches hou.

    Sequence (order matters): hipFile.clear -> context variables (a fresh hip resets $JOB)
    -> fps / frame range -> LOP chain in /stage -> save the versioned hip -> sidecar, so
    the scene shows up in create_project.list_scenefiles like any Save Version.

    NEVER overwrites: the version comes from the spec (latest WIP on disk + 1, every .hip*
    extension counted). Raises ValueError if the spec/plan refuses (unknown entity, step not
    declared, license extension outside the spec's).

    Returns {'ok', 'entity', 'step', 'version', 'path', 'sidecar', 'nodes' (created paths),
    'warnings'}. 'warnings' is never silent: it is also printed on stderr - a starter that
    could not create its camera must be visible, not disappear."""
    import hou
    if project_root is None:
        project_root = active_project()
    if project_root is None:
        raise ValueError(
            "No active project (~/.ylos/active_project) and project_root not provided."
        )
    spec = cp.scene_starter_spec(entity_name, step, "houdini", project_root=project_root)
    # hou.licenseCategory() returns an enum value, hip_extension() expects its name.
    plan = starter_plan(spec, hou.licenseCategory().name())
    if not plan.get("ok"):
        raise ValueError(plan.get("reason", "starter plan refused"))

    hou.hipFile.clear(suppress_save_prompt=True)

    for name, value in (plan.get("env") or {}).items():
        if value:
            hou.putenv(name, str(value))

    if plan.get("fps"):
        hou.setFps(float(plan["fps"]))
    frame_range = plan.get("frame_range")
    if frame_range:
        start, end = float(frame_range[0]), float(frame_range[1])
        hou.playbar.setFrameRange(start, end)
        hou.playbar.setPlaybackRange(start, end)

    stage = hou.node("/stage")
    created, warnings = [], []
    for entry in plan.get("nodes") or []:
        try:
            node = stage.createNode(entry["type"], entry["name"])
        except hou.OperationFailed as exc:
            warnings.append(
                f"LOP {entry['type']!r} not created ({exc}) - starter incomplete."
            )
            continue
        if created:
            node.setInput(0, created[-1])
        for parm_name, value in (entry.get("parms") or {}).items():
            parm = node.parm(parm_name)
            if parm is None:
                warnings.append(
                    f"{node.path()}: no {parm_name!r} parameter (check the Houdini version)."
                )
                continue
            parm.set(value)
        created.append(node)
    if created:
        stage.layoutChildren()
        created[-1].setDisplayFlag(True)

    target = Path(plan["target"])
    target.parent.mkdir(parents=True, exist_ok=True)
    hou.hipFile.save(str(target))
    sidecar = None
    try:
        sidecar = write_wip_sidecar(target, comment)
    except (OSError, ValueError) as exc:
        warnings.append(f"WIP sidecar not written ({target}{SIDECAR_SUFFIX}): {exc}")
    for w in warnings:
        sys.stderr.write(f"[warn] create_scene {entity_name}/{step}: {w}\n")

    return {
        "ok": True,
        "entity": plan["entity"],
        "step": plan["step"],
        "version": plan["version"],
        "path": str(target),
        "sidecar": str(sidecar) if sidecar is not None else None,
        "nodes": [n.path() for n in created],
        "warnings": warnings,
    }


def reference_asset(entity_name, project_root=None):
    """Creates a 'reference' LOP in /stage pointing at the entity's asset_root (composed
    under /<entity>, defaultPrim aligned - see docs/usd-convention.md). The path is written
    in $PROJ_ROOT when possible (env_relative). Returns the created node."""
    import hou
    if project_root is None:
        project_root = active_project()
    if project_root is None:
        raise ValueError("No active project (~/.ylos/active_project).")
    path = asset_root_path(project_root, entity_name)
    stage = hou.node("/stage")
    node = stage.createNode("reference", entity_name)
    node.parm("primpath").set(f"/{entity_name}")
    node.parm("filepath1").set(env_relative(path))
    node.moveToGoodPosition()
    return node


def _create_sublayer(node_name, path):
    """Creates a 'sublayer' LOP in /stage on 'path' (written in $PROJ_ROOT via env_relative).
    Common helper for sublayer_shot / sublayer_step_publish - a sublayer stacks the layer on
    the stage (unlike 'reference' which grafts it under a prim). Returns the node."""
    import hou
    stage = hou.node("/stage")
    node = stage.createNode("sublayer", node_name)
    # 'sublayer' carries a list of files (multiparm 'num_files') - a single one here.
    num = node.parm("num_files")
    if num is not None:
        num.set(1)
    node.parm("filepath1").set(env_relative(path))
    node.moveToGoodPosition()
    return node


def sublayer_shot(shot_name, project_root=None):
    """Creates a 'sublayer' LOP in /stage on the shot's shot_root.usda. sublayer and NOT
    reference: the shot IS the stage (root prim /ROOT, see docs/usd-convention.md), we don't
    graft it under a prim. Returns the node."""
    if project_root is None:
        project_root = active_project()
    if project_root is None:
        raise ValueError("No active project (~/.ylos/active_project).")
    path = shot_root_path(project_root, shot_name)
    return _create_sublayer(shot_name, path)


def sublayer_step_publish(entity_name, step, project_root=None):
    """Creates a 'sublayer' LOP in /stage on the entity's latest 'complete' publish of the
    step - to manually compose a specific step (e.g. lighting that only wants
    the anim) without going through the whole shot_root. Returns the node."""
    if project_root is None:
        project_root = active_project()
    if project_root is None:
        raise ValueError("No active project (~/.ylos/active_project).")
    path = latest_step_publish(project_root, entity_name, step)
    if path is None:
        raise FileNotFoundError(
            f"No 'complete' publish for step '{step}' of '{entity_name}'."
        )
    return _create_sublayer(f"{entity_name}_{step}", path)


def create_publish_node(entity_name, step, project_root=None):
    """Drops a ylos::publish HDA in /stage, pre-filled for <entity>/<step> and wired to the
    current display node of /stage - the panel's 'Publish' button.

    Does NOT publish: the two-phase contract stays a HUMAN gesture (the artist checks the
    composed stage, then presses the HDA's own Publish button). The parameters are set in
    this order on purpose: 'project_root' and 'asset_name' FIRST, because the
    'publish_kind' menu is generated from the entity's manifest (kind_menu_items) and
    would not contain <step> yet if the entity were unknown. Returns the node."""
    import hou
    if project_root is None:
        project_root = active_project()
    if project_root is None:
        raise ValueError("No active project (~/.ylos/active_project).")
    stage = hou.node("/stage")
    node = None
    for type_name in PUBLISH_HDA_TYPES:
        try:
            node = stage.createNode(type_name, f"publish_{entity_name}_{step}")
            break
        except hou.OperationFailed:
            continue
    if node is None:
        raise RuntimeError(
            f"Publish HDA not installed (tried {', '.join(PUBLISH_HDA_TYPES)}) - check that "
            f"HOUDINI_OTLSCAN_PATH contains $YLOS_REPO/plugins/houdini/otls (package "
            f"ylos.json) and that the .hdanc has been regenerated "
            f"(hython tools/houdini/build_publish_hda.py)."
        )
    display = stage.displayNode()
    if display is not None and display is not node:
        node.setInput(0, display)
    for parm_name, value in (("project_root", str(project_root)),
                             ("asset_name", entity_name),
                             ("publish_kind", step)):
        parm = node.parm(parm_name)
        if parm is not None:
            parm.set(value)
        else:
            sys.stderr.write(
                f"[warn] publish HDA without a {parm_name!r} parameter - not pre-filled "
                f"(regenerate the HDA?).\n")
    node.moveToGoodPosition()
    return node


def _first_shot_camera(node):
    """Prim path of the first camera under /ROOT/cameras/ in the input stage of 'node',
    or None. Shot convention (docs/usd-convention.md): published cameras live under
    /ROOT/cameras/ (distinct from the thumbnail HDA's /cameras/ylos_thumb_cam). Requires hou."""
    try:
        inputs = node.inputs()
        stage = inputs[0].stage() if inputs and inputs[0] is not None else node.stage()
    except (AttributeError, IndexError):
        return None
    if stage is None:
        return None
    cams = stage.GetPrimAtPath("/ROOT/cameras")
    if not cams or not cams.IsValid():
        return None
    for child in cams.GetChildren():
        if child.GetTypeName() == "Camera":
            return child.GetPath().pathString
    return None


def render_shot(shot_name, step, camera=None, project_root=None):
    """Creates and configures (does NOT launch) a usdrender_rop in /stage to render <shot>/<step>
    to the cache tier ($PROJ_CACHE/.../render/<shot>/<step>/v<NNN>/, version =
    next_render_version). Input = current display node of /stage (the composed stage). trange
    on the manifest's frame_range (schema 2.1), fallback to the hip range with a warning if
    absent. 'camera' = prim path (None -> first under /ROOT/cameras/). 'outputimage' as a
    literal $PROJ_CACHE expression + $F4 (relocatable). soho_foreground=1: otherwise
    node.render() in GUI returns at husk submission, not at the end of the render (gotcha
    CLAUDE.md). Returns the node."""
    import hou
    if project_root is None:
        project_root = active_project()
    if project_root is None:
        raise ValueError("No active project (~/.ylos/active_project).")
    project_root = Path(project_root)
    _, manifest_path = cp._find_asset_entity(project_root, shot_name)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = next_render_version(project_root, shot_name, step)

    stage = hou.node("/stage")
    node = stage.createNode("usdrender_rop", f"render_{shot_name}_{step}")
    display = stage.displayNode()
    if display is not None and display is not node:
        node.setInput(0, display)

    fr = manifest.get("frame_range")
    if fr:
        f1, f2 = int(fr["start"]), int(fr["end"])
    else:
        rng = hou.playbar.frameRange()
        f1, f2 = int(rng[0]), int(rng[1])
        sys.stderr.write(
            f"[warn] {shot_name} without frame_range in the manifest - fallback to the hip "
            f"range ({f1}-{f2}). Set create_project.set_frame_range() to fix the range.\n"
        )
    for name, val in (("trange", 1), ("f1", f1), ("f2", f2), ("f3", 1),
                      ("soho_foreground", 1)):
        parm = node.parm(name)
        if parm is not None:
            parm.set(val)

    if camera is None:
        camera = _first_shot_camera(node)
    if camera:
        # On usdrender_rop the camera parm is called 'override_camera' (verified by
        # hython enumeration - node.parm("camera") is None; same parm as the HDA
        # build on this node type). Explicit warning if not found (never silent:
        # a requested but unset camera must be visible, not disappear).
        parm = node.parm("override_camera")
        if parm is not None:
            parm.set(camera)
        else:
            sys.stderr.write(
                f"[warn] usdrender_rop without an 'override_camera' parameter - camera "
                f"{camera!r} not set (check the Houdini version).\n"
            )

    out = node.parm("outputimage")
    if out is not None:
        out.set(render_output_expression(project_root, shot_name, step, version))
    node.moveToGoodPosition()
    return node


# --------------------------------------------------------------------------------------
# Shelf tools (hou.ui dialogs, called by ylos_pipeline.shelf)
# --------------------------------------------------------------------------------------

def _pick_from_list(title, items, message):
    """Index chosen from a list (exclusive hou.ui.selectFromList), or None if cancelled."""
    import hou
    if not items:
        return None
    sel = hou.ui.selectFromList(items, title=title, message=message,
                                exclusive=True, clear_on_cancel=True)
    return sel[0] if sel else None


def _pick_entity(project_root, title, entities=None):
    """Entity chosen from the project's (dict from list_entities), or None if cancelled."""
    import hou
    entities = entities if entities is not None else list_entities(project_root)
    if not entities:
        hou.ui.displayMessage("No entity in this project - create an asset first.",
                              severity=hou.severityType.Warning)
        return None
    labels = [f"{e['name']}  ({e['family']}, {e['type']})" for e in entities]
    idx = _pick_from_list(title, labels, "Entity:")
    return entities[idx] if idx is not None else None


def _read_comment(title, message="Version comment (optional):"):
    """Version comment typed by the user, or None if cancelled (empty string = accepted,
    no comment). Shared by the Save Version / New Scene tools and the panel."""
    import hou
    ok, text = hou.ui.readInput(message, buttons=("Save", "Cancel"), close_choice=1,
                                title=title, initial_contents="")
    return None if ok != 0 else (text or "").strip()


def tool_save_wip():
    """Shelf 'Save Version': version++ in the current hip context, or entity/step
    choice if the hip is outside the pipeline. Asks for a version comment (Prism-style),
    stored in the sidecar '<hip>.json' read back by the web UI and the panel."""
    import hou
    try:
        ctx = parse_wip_context(hou.hipFile.path())
        if ctx is not None:
            comment = _read_comment("Save Version")
            if comment is None:
                return
            info = save_wip(comment=comment)
        else:
            project_root = active_project()
            if project_root is None:
                hou.ui.displayMessage(
                    "No active project - set the project via the web UI (ylos_ui.py).",
                    severity=hou.severityType.Warning)
                return
            entity = _pick_entity(project_root, "Save Version")
            if entity is None:
                return
            steps = entity["steps"]
            idx = _pick_from_list("Save Version", steps, "Step:")
            if idx is None:
                return
            comment = _read_comment("Save Version")
            if comment is None:
                return
            info = save_wip(entity["name"], steps[idx], project_root, comment=comment)
        hou.ui.displayMessage(
            f"WIP v{info['version']:03d} saved:\n{info['path']}")
    except (ValueError, FileNotFoundError, OSError) as exc:
        hou.ui.displayMessage(str(exc), severity=hou.severityType.Error)


def tool_new_asset():
    """Shelf 'New Asset': creates an entity in the active project via create_asset()
    (TYPE_Name_Variant validation at creation - same error message, with suggestion,
    as the web UI and Blender, since same function)."""
    import hou
    project_root = active_project()
    if project_root is None:
        hou.ui.displayMessage(
            "No active project - set the project via the web UI (ylos_ui.py).",
            severity=hou.severityType.Warning)
        return
    families = list(cp.ENTITY_DIR)  # asset / set / shot
    idx = _pick_from_list("New Asset", families, "Family:")
    if idx is None:
        return
    family = families[idx]
    types = cp._TYPES_BY_ENTITY[family]
    idx = _pick_from_list("New Asset", types, "Type:")
    if idx is None:
        return
    sub_type = types[idx]
    ok, name = hou.ui.readInput(
        f"Entity name (convention {sub_type}_Name_Variant):",
        buttons=("Create", "Cancel"), close_choice=1,
        initial_contents=f"{sub_type}_Name_Default")
    if ok != 0 or not name.strip():
        return
    try:
        info = cp.create_asset(project_root, name.strip(),
                               entity_type=family, asset_type=sub_type)
        hou.ui.displayMessage(f"{family} '{info['name']}' created:\n{info['path']}")
    except (ValueError, FileExistsError, OSError) as exc:
        hou.ui.displayMessage(str(exc), severity=hou.severityType.Error)


def tool_load_asset():
    """Shelf 'Load Asset': references an entity's asset_root in /stage."""
    import hou
    project_root = active_project()
    if project_root is None:
        hou.ui.displayMessage(
            "No active project - set the project via the web UI (ylos_ui.py).",
            severity=hou.severityType.Warning)
        return
    # Only entities with an asset_root are proposed (shots have none).
    entities = [e for e in list_entities(project_root)
                if (Path(project_root) / e["family"] / e["name"] / cp.ASSET_ROOT_NAME).is_file()]
    entity = _pick_entity(project_root, "Load Asset", entities)
    if entity is None:
        return
    try:
        node = reference_asset(entity["name"], project_root)
        hou.ui.displayMessage(f"Reference created: {node.path()}")
    except (ValueError, FileNotFoundError, OSError) as exc:
        hou.ui.displayMessage(str(exc), severity=hou.severityType.Error)


def tool_load_shot():
    """Shelf 'Load Shot': sublayers a shot's shot_root.usda in /stage (the shot IS the
    stage, root prim /ROOT) - composes all the shot's published steps."""
    import hou
    project_root = active_project()
    if project_root is None:
        hou.ui.displayMessage(
            "No active project - set the project via the web UI (ylos_ui.py).",
            severity=hou.severityType.Warning)
        return
    shots = [e for e in list_entities(project_root)
             if e["family"] == cp.ENTITY_DIR["shot"]]
    entity = _pick_entity(project_root, "Load Shot", shots)
    if entity is None:
        return
    try:
        node = sublayer_shot(entity["name"], project_root)
        hou.ui.displayMessage(f"Sublayer created: {node.path()}")
    except (ValueError, FileNotFoundError, OSError) as exc:
        hou.ui.displayMessage(str(exc), severity=hou.severityType.Error)


def tool_setup_filecache():
    """Shelf 'Setup File Cache': sets 'basedir' of the selected filecache SOP(s) to
    the expression $PROJ_CACHE/<project>/houdini/<entity>/<step>/ (relocatable, see
    cache_dir_expression). Context inferred from the current hip (parse_wip_context) - a cache is
    always written in the context of the open entity/step. The v1/v2 versioning stays the
    filecache's native one (disposable data, no manifest)."""
    import hou
    ctx = parse_wip_context(hou.hipFile.path())
    if ctx is None:
        hou.ui.displayMessage(
            "Current hip outside the pipeline (.../<entity>/<step>/wip/) - cannot infer "
            "the cache's entity/step. Open a WIP first.",
            severity=hou.severityType.Warning)
        return
    project_root, entity_name, step = ctx
    selected = hou.selectedNodes()
    if not selected:
        hou.ui.displayMessage(
            "Select the 'filecache' SOP node to configure.",
            severity=hou.severityType.Warning)
        return
    expr = cache_dir_expression(project_root, entity_name, step)
    done = []
    for node in selected:
        parm = node.parm("basedir")
        if parm is not None:
            parm.set(expr)
            done.append(node)
    if not done:
        hou.ui.displayMessage(
            "No selected node has a 'basedir' parameter (filecache SOP expected).",
            severity=hou.severityType.Warning)
        return
    hou.ui.displayMessage(f"basedir set on {len(done)} node(s):\n{expr}")


def tool_load_step_publish():
    """Shelf 'Load Step Publish': sublayers the latest publish of a step (entity + step of
    choice) - to compose manually when you don't want the whole shot_root."""
    import hou
    project_root = active_project()
    if project_root is None:
        hou.ui.displayMessage(
            "No active project - set the project via the web UI (ylos_ui.py).",
            severity=hou.severityType.Warning)
        return
    entity = _pick_entity(project_root, "Load Step Publish")
    if entity is None:
        return
    steps = entity["steps"]
    idx = _pick_from_list("Load Step Publish", steps, "Step:")
    if idx is None:
        return
    try:
        node = sublayer_step_publish(entity["name"], steps[idx], project_root)
        hou.ui.displayMessage(f"Sublayer created: {node.path()}")
    except (ValueError, FileNotFoundError, OSError) as exc:
        hou.ui.displayMessage(str(exc), severity=hou.severityType.Error)


def tool_render_shot():
    """Shelf 'Render Shot': configures a usdrender_rop to render a shot step to the
    cache tier (shot + step choice), on the manifest's frame_range, auto camera
    (/ROOT/cameras/), relocatable output. Offers to launch the render (soho_foreground: it
    blocks until the end)."""
    import hou
    project_root = active_project()
    if project_root is None:
        hou.ui.displayMessage(
            "No active project - set the project via the web UI (ylos_ui.py).",
            severity=hou.severityType.Warning)
        return
    shots = [e for e in list_entities(project_root)
             if e["family"] == cp.ENTITY_DIR["shot"]]
    entity = _pick_entity(project_root, "Render Shot", shots)
    if entity is None:
        return
    steps = entity["steps"]
    idx = _pick_from_list("Render Shot", steps, "Step:")
    if idx is None:
        return
    try:
        node = render_shot(entity["name"], steps[idx], project_root=project_root)
    except (ValueError, FileNotFoundError, OSError) as exc:
        hou.ui.displayMessage(str(exc), severity=hou.severityType.Error)
        return
    launch = hou.ui.displayConfirmation(
        f"usdrender_rop configured: {node.path()}\nLaunch the render now?")
    if launch:
        try:
            node.render()
            hou.ui.displayMessage("Render finished (see $PROJ_CACHE/.../render/).")
        except hou.OperationFailed as exc:
            hou.ui.displayMessage(str(exc), severity=hou.severityType.Error)


def tool_deliver_render():
    """Shelf 'Deliver Render': copies a valid render take from the cache to delivery/ (shot
    + step + version choice). ONLY path that writes to delivery/ (human validation, see
    deliver_render)."""
    import hou
    project_root = active_project()
    if project_root is None:
        hou.ui.displayMessage(
            "No active project - set the project via the web UI (ylos_ui.py).",
            severity=hou.severityType.Warning)
        return
    shots = [e for e in list_entities(project_root)
             if e["family"] == cp.ENTITY_DIR["shot"]]
    entity = _pick_entity(project_root, "Deliver Render", shots)
    if entity is None:
        return
    steps = entity["steps"]
    idx = _pick_from_list("Deliver Render", steps, "Step:")
    if idx is None:
        return
    step = steps[idx]
    versions = list_render_versions(project_root, entity["name"], step)
    if not versions:
        hou.ui.displayMessage(
            f"No render for {entity['name']}/{step} - render the step before delivering.",
            severity=hou.severityType.Warning)
        return
    labels = [f"v{v:03d}" for v in versions]
    vidx = _pick_from_list("Deliver Render", labels, "Version to deliver:")
    if vidx is None:
        return
    try:
        dst = deliver_render(project_root, entity["name"], step, versions[vidx])
        hou.ui.displayMessage(f"Render delivered:\n{dst}")
    except (ValueError, FileNotFoundError, OSError) as exc:
        hou.ui.displayMessage(str(exc), severity=hou.severityType.Error)


def tool_new_scene():
    """Shelf 'New Scene': creates a NEW authoring scene (starter by department) for an
    entity+step of the active project - the Houdini entry point of Phase 1.3. Confirms
    BEFORE acting: create_scene() clears the current hip, an unsaved session must never be
    lost by a mis-click."""
    import hou
    project_root = active_project()
    if project_root is None:
        hou.ui.displayMessage(
            "No active project - set the project via the web UI (ylos_ui.py).",
            severity=hou.severityType.Warning)
        return
    entity = _pick_entity(project_root, "New Scene")
    if entity is None:
        return
    steps = entity["steps"]
    idx = _pick_from_list("New Scene", steps, "Step:")
    if idx is None:
        return
    step = steps[idx]
    spec = cp.scene_starter_spec(entity["name"], step, "houdini", project_root=project_root)
    if not spec.get("ok"):
        hou.ui.displayMessage(spec.get("reason", "Starter refused."),
                              severity=hou.severityType.Error)
        return
    summary = (f"{entity['name']} / {step} -> v{spec['wip']['version']:03d}\n"
               f"{len(spec.get('references') or [])} assembly reference(s), "
               f"camera: {'yes' if spec.get('camera') else 'no'}, "
               f"light: {'yes' if spec.get('lighting') else 'no'}\n\n"
               f"The current scene will be cleared. Continue?")
    if not hou.ui.displayConfirmation(summary, title="New Scene"):
        return
    comment = _read_comment("New Scene", "Comment for this first version (optional):")
    if comment is None:
        return
    try:
        info = create_scene(entity["name"], step, project_root, comment=comment)
    except (ValueError, FileNotFoundError, OSError) as exc:
        hou.ui.displayMessage(str(exc), severity=hou.severityType.Error)
        return
    message = f"Scene v{info['version']:03d} created:\n{info['path']}"
    if info["warnings"]:
        message += "\n\nWarnings:\n- " + "\n- ".join(info["warnings"])
    hou.ui.displayMessage(
        message,
        severity=hou.severityType.Warning if info["warnings"] else hou.severityType.Message)


def tool_set_status():
    """Shelf 'Set Status': sets the EXPLICIT production status of a step
    (review / approved), or clears it back to the derived value (auto). Delegates to
    create_project.set_step_status - the only writer of the schema 2.2 field."""
    import hou
    project_root = active_project()
    if project_root is None:
        hou.ui.displayMessage(
            "No active project - set the project via the web UI (ylos_ui.py).",
            severity=hou.severityType.Warning)
        return
    entity = _pick_entity(project_root, "Set Status")
    if entity is None:
        return
    statuses = cp.get_step_status(project_root, entity["name"]) or {}
    steps = list(statuses) or list(entity["steps"])
    labels = [f"{s}  ({statuses.get(s, {}).get('status', 'empty')})" for s in steps]
    idx = _pick_from_list("Set Status", labels, "Step:")
    if idx is None:
        return
    step = steps[idx]
    choices = [cp.STEP_STATUS_AUTO] + list(cp.STEP_STATUS_EXPLICIT)
    cidx = _pick_from_list("Set Status", choices, f"Status for '{step}':")
    if cidx is None:
        return
    try:
        info = cp.set_step_status(project_root, entity["name"], step, choices[cidx])
    except (ValueError, FileNotFoundError, OSError) as exc:
        hou.ui.displayMessage(str(exc), severity=hou.severityType.Error)
        return
    hou.ui.displayMessage(
        f"{entity['name']} / {step}: {info['status']}"
        f"{'' if info['explicit'] else ' (derived)'}")


def tool_project_browser():
    """Shelf 'Project Browser': opens the Ylos cockpit (Python Panel 'ylos_browser') in a
    FLOATING pane so it never steals a pane of the working desktop. The interface is
    resolved BY NAME (hou.pypanel.interfaceByName) - it is registered by
    plugins/houdini/python_panels/ylos_browser.pypanel, found through HOUDINI_PATH
    (package ylos.json). Explicit message if it is missing rather than a traceback:
    the usual cause is a package not loaded / Houdini not restarted."""
    import hou
    interface = hou.pypanel.interfaceByName(PANEL_INTERFACE_NAME)
    if interface is None:
        hou.ui.displayMessage(
            f"Python Panel '{PANEL_INTERFACE_NAME}' not found - check that HOUDINI_PATH "
            f"contains $YLOS_REPO/plugins/houdini (package ylos.json) and restart Houdini.",
            severity=hou.severityType.Error)
        return None
    panel = hou.ui.curDesktop().createFloatingPaneTab(
        hou.paneTabType.PythonPanel, size=(1100, 700))
    panel.setActiveInterface(interface)
    return panel
