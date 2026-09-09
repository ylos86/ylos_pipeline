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
# Houdini actions (hou required)
# --------------------------------------------------------------------------------------

def save_wip(entity_name=None, step=None, project_root=None):
    """Saves the current hip as the next WIP version of <entity>/<step>. Without
    arguments: context inferred from the current hip path (parse_wip_context). Returns
    {'path', 'version'}."""
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
    return {"path": str(path), "version": version}


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


def tool_save_wip():
    """Shelf 'Save WIP': version++ in the current hip context, or entity/step
    choice if the hip is outside the pipeline."""
    import hou
    try:
        if parse_wip_context(hou.hipFile.path()) is not None:
            info = save_wip()
        else:
            project_root = active_project()
            if project_root is None:
                hou.ui.displayMessage(
                    "No active project - set the project via the web UI (ylos_ui.py).",
                    severity=hou.severityType.Warning)
                return
            entity = _pick_entity(project_root, "Save WIP")
            if entity is None:
                return
            steps = entity["steps"]
            idx = _pick_from_list("Save WIP", steps, "Step:")
            if idx is None:
                return
            info = save_wip(entity["name"], steps[idx], project_root)
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
