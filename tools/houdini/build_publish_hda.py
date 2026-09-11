#!/usr/bin/env hython
"""build_publish_hda.py - builds ylos::publish::0.2 (Lop) by code, hython only.

Replayable: destroys/rebuilds the build node on each run, overwrites the target .hdanc.
The HDA definition lives in git via THIS script, not as a binary blob of GUI editing.

0.2 adds the step mode: the `publish_kind` parameter (menu 'lop' + the entity's steps,
read from the manifest) selects the publish subtree. 'lop' = historical contract
(complete snapshot, `asset_type` required); a step name = two-phase per-step publish
(kind=<step>, feeds the asset_root/shot_root composition). No 0.1 cohabitation: the
.hdanc is regenerated entirely by this script (see build()).

Usage:
    hython tools/houdini/build_publish_hda.py
"""

import os
import sys

import hou

TYPE_NAME = "ylos::publish::0.2"
TYPE_LABEL = "Ylos Publish"
BUILD_NODE_NAME = "ylos_publish_build"
OTL_REL_PATH = os.path.join("plugins", "houdini", "otls", "ylos_publish.hdanc")
THUMB_CAM_PRIMPATH = "/cameras/ylos_thumb_cam"
THUMB_RES = 512


def _repo_root():
    # os.path.realpath (never bare dirname(__file__)): same fix as the Blender symlink bug,
    # applied here to the build script itself (not just the HDA's embedded module).
    here = os.path.realpath(__file__)
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def _python_module_source():
    return '''\
"""ylos::publish callback. Imports create_project.py as the single source of truth for
locking/versioning/manifest - never reimplement this logic here (see the Ylos
pipeline contract)."""

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime

import hou


def _log(msg):
    # Temporary instrumentation to validate in a real GUI session that the thumb render
    # blocks before finalize (see orphan staging_dir bug, async usdrender_rop timing).
    # print(flush=True), not the logging module: one-off diagnostic, visible without config,
    # in the terminal that launched Houdini.
    print("[ylos.publish] {} {}".format(
        datetime.now().isoformat(timespec="milliseconds"), msg
    ), flush=True)


def _repo_root(node):
    # os.path.realpath on the HDA definition path (via the hou API, not __file__):
    # an embedded module has no reliable __file__, and even if it had one, the same
    # symlink fix as Blender applies.
    # otl_path = REPO/plugins/houdini/otls/ylos_publish.hdanc -> 4 dirname() for REPO
    # (1 for the file name + 3 for otls/houdini/plugins). Real bug observed in GUI:
    # with 3 dirname() we land on REPO/plugins (no create_project.py in it),
    # ModuleNotFoundError. Masked in hython because the shell cwd was already REPO (import
    # fallback via sys.path[0]='' that hid the bug) - never reliable, fixed here.
    otl_path = os.path.realpath(node.type().definition().libraryFilePath())
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(otl_path))))


def _cp(node):
    root = _repo_root(node)
    if root not in sys.path:
        sys.path.insert(0, root)
    import create_project
    return create_project


def _yh(node):
    """The Houdini bridge (plugins/houdini/python/ylos_houdini.py), for the pure
    dependency-collection functions (stage_layer_paths / dependencies_from_paths). Resolved
    from the INSTALLED definition like _cp(), never from PYTHONPATH: the HDA must work in a
    session where the ylos.json package was not loaded."""
    path = os.path.join(_repo_root(node), "plugins", "houdini", "python")
    if path not in sys.path:
        sys.path.insert(0, path)
    import ylos_houdini
    return ylos_houdini


def _collect_dependencies(node, project_root, asset_name):
    """[{"entity", "step", "version"}] - the published products the stage being published
    was composed from, read from the INPUT stage's layer stack (schema 2.2 'dependencies',
    consumed by create_project.build_dependency_index).

    Why the input and not node.stage(): the HDA's own branch adds the thumb camera and the
    Configure Layer edits; only what enters the node is the artist's composition.

    Why the manifest source matters here: the USD-layer scan of build_dependency_index only
    reads ASCII layers, and an Apprentice publish is '.usdnc' (encrypted) - unreadable. For
    Houdini publishes, this recorded list is the ONLY reliable dependency source
    (see CLAUDE.md, 'Dependances').

    Best-effort by design: a stage that refuses introspection returns None (key not
    written) and the publish proceeds - dependency metadata must never block a valid
    publish. Never raises."""
    try:
        inputs = node.inputs()
        stage = inputs[0].stage() if inputs and inputs[0] is not None else None
        if stage is None:
            return None
        yh = _yh(node)
        paths = yh.stage_layer_paths(stage)
        return yh.dependencies_from_paths(paths, project_root, exclude_entity=asset_name)
    except Exception as exc:
        _log("dependencies: not collected ({}: {})".format(type(exc).__name__, exc))
        return None


def kind_menu_items(node):
    """Generates the 'publish_kind' menu: 'lop' (complete snapshot, historical contract) followed
    by the entered entity's steps, read from its manifest.json via create_project (single source
    of truth - never a hard-coded step list in the HDA). Static fallback
    DEFAULT_SHOT_STEPS + DEFAULT_ASSET_STEPS (ordered union, duplicates removed) if the manifest
    is not readable (entity not entered yet, project not found, broken JSON...). Returns
    the flat list [value, label, ...] expected by item_generator_script (Replace mode)."""
    cp = _cp(node)  # _repo_root derived from the installed definition: always resolvable

    steps = None
    try:
        project_root = node.evalParm("project_root")
        asset_name = node.evalParm("asset_name")
        if project_root and asset_name:
            _entity_dir, manifest_path = cp._find_asset_entity(project_root, asset_name)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            steps = manifest.get("steps") or None
    except Exception:
        steps = None  # entity not entered yet / unreadable manifest -> static fallback

    if not steps:
        steps = []
        for s in list(cp.DEFAULT_SHOT_STEPS) + list(cp.DEFAULT_ASSET_STEPS):
            if s not in steps:
                steps.append(s)

    items = ["lop", "lop (complete snapshot)"]
    for s in steps:
        items.extend([s, s])
    return items


def publish(kwargs):
    node = kwargs["node"]
    status_parm = node.parm("status")
    cp = _cp(node)

    project_root = node.evalParm("project_root")
    asset_name = node.evalParm("asset_name")
    asset_type = node.evalParm("asset_type")
    kind = node.evalParm("publish_kind")
    comment = node.evalParm("version_comment")

    try:
        if kind == "lop":
            # Historical contract: complete snapshot outside the step taxonomy. The name is
            # (re)validated, 'asset_type' required and checked against the manifest by allocate.
            cp.validate_publish_asset_name(asset_name, asset_type)
            staging_dir, final_dir = cp.allocate_publish_version(
                project_root, asset_name, asset_type, comment=comment
            )
        else:
            # Step mode: two-phase publish in step_publishes[kind]. 'asset_type' is
            # ignored (as in allocate_publish_version for kind != 'lop'); the entity's
            # naming is already guaranteed at creation (validate_entity_name), no
            # revalidation here.
            staging_dir, final_dir = cp.allocate_publish_version(
                project_root, asset_name, comment=comment, kind=kind
            )
        version = cp.publish_version_from_dir(final_dir)

        # layer_stem = versioned_name computed by allocate_publish_version
        # ('{asset}_{kind}_v{NNN}'): identical for lop and for a step. Name requested to the ROP
        # (savepath), NOT guaranteed to be the name actually written to disk (Apprentice license
        # rewrites to '.usdnc') - finalize_publish_version resolves the stem against the known
        # USD extensions (PUBLISH_ARTIFACT_EXTENSIONS).
        layer_stem = "{}_{}_v{:03d}".format(asset_name, kind, version)
        layer_path = os.path.join(str(staging_dir), layer_stem + ".usd")
        thumb_path = os.path.join(str(staging_dir), cp.LOP_THUMB_NAME)

        # Configure Layer: marks the save path computed from staging_dir. The USD ROP
        # then exports THIS layer precisely (savestyle='separate', no flatten):
        # Houdini only serializes its own authored layer, never geo.usd.
        # A locked HDA instance forbids writing directly on the internal nodes'
        # parameters (hou.PermissionError): we go through the node-level promoted
        # parameters (_layer_savepath / _thumb_outputimage), linked to the internal
        # nodes by a channel-reference expression set at build (see build_publish_hda.py).
        node.parm("_layer_savepath").set(layer_path)
        node.parm("_thumb_outputimage").set(thumb_path)

        # savestyle='flattenimplicitlayers' also writes a disposable root layer (subLayers
        # -> our target file, already standalone): confine it outside staging_dir so
        # finalize_publish_version() only sees the real layer + the thumb there.
        scratch_dir = tempfile.mkdtemp(prefix="ylos_publish_scratch_")
        try:
            node.parm("_publish_scratch_output").set(
                os.path.join(scratch_dir, "root.usd")
            )

            publish_rop = node.node("publish_rop")
            _log("render start: publish_rop (layer)")
            publish_rop.render()
            _log("render end:   publish_rop (layer)")

            thumb_rop = node.node("thumb_rop")
            _log("render start: thumb_rop")
            thumb_rop.render()
            _log("render end:   thumb_rop")
        finally:
            shutil.rmtree(scratch_dir, ignore_errors=True)

        # Schema 2.2: record WHAT this publish was built from, read from the input stage's
        # layer stack (see _collect_dependencies). Collected AFTER the renders (the stage is
        # cooked and stable) and BEFORE finalize, which is the only writer of the manifest.
        dependencies = _collect_dependencies(node, project_root, asset_name)
        _log("dependencies: {}".format(dependencies))

        expected_artifacts = [layer_stem, cp.LOP_THUMB_NAME]
        _log("finalize call: expected_artifacts={}".format(expected_artifacts))
        result = cp.finalize_publish_version(
            project_root, asset_name, staging_dir, final_dir, version,
            expected_artifacts, comment=comment, dependencies=dependencies
        )

        status_parm.set("OK - {} v{:03d} - {}".format(kind, version, result["final_dir"]))
    except Exception as exc:
        status_parm.set("ERROR: {}".format(exc))
        raise
'''


def _build_parm_template_group(cp):
    g = hou.ParmTemplateGroup()

    project_root = hou.StringParmTemplate(
        "project_root", "Project Root", 1,
        default_expression=(
            "import os\n"
            "p = os.path.expanduser('~/.ylos/active_project')\n"
            "try:\n"
            "    with open(p) as f:\n"
            "        return f.read().strip()\n"
            "except OSError:\n"
            "    return ''\n",
        ),
        default_expression_language=(hou.scriptLanguage.Python,),
    )
    g.append(project_root)

    g.append(hou.StringParmTemplate("asset_name", "Asset Name", 1))

    # Dynamic menu 'lop' + the entered entity's steps (read from the manifest by kind_menu_items,
    # single source of truth). item_generator_script delegates to the embedded module via
    # hdaModule(): the step list is never hard-coded in the definition. Default
    # 'lop' -> full compat with the historical contract (a freshly created node publishes in
    # lop like 0.1). Replace mode: the value MUST be one of the generated items.
    # The menu script is evaluated in 'eval' mode (ONE expression, not a block): neither 'return X'
    # nor 'menu = X' is valid (verified empirically, SyntaxError on both). So we give
    # a bare expression that produces the flat list [token, label, ...]; 'kwargs' (with
    # 'node') is provided in the evaluation namespace.
    g.append(hou.StringParmTemplate(
        "publish_kind", "Publish Kind", 1,
        default_value=("lop",),
        menu_type=hou.menuType.Normal,
        item_generator_script="kwargs['node'].hdaModule().kind_menu_items(kwargs['node'])",
        item_generator_script_language=hou.scriptLanguage.Python,
    ))

    g.append(hou.StringParmTemplate(
        "asset_type", "Asset Type", 1,
        default_value=(cp.ASSET_TYPES[0],),
        menu_items=list(cp.ASSET_TYPES),
        menu_labels=list(cp.ASSET_TYPES),
    ))

    g.append(hou.StringParmTemplate("version_comment", "Version Comment", 1))

    # Promoted parameters (plumbing): the internal nodes of a locked HDA instance
    # are not directly editable (hou.PermissionError). These parms at the node
    # level are linked to the internal nodes by channel-reference (see build()); the callback
    # writes here, never on configure_publish_layer/savepath or thumb_rop/outputimage.
    for name, label in (
        ("_layer_savepath", "Layer Save Path (internal)"),
        ("_thumb_outputimage", "Thumb Output Image (internal)"),
        ("_publish_scratch_output", "Publish Scratch Output (internal)"),
    ):
        pt = hou.StringParmTemplate(name, label, 1)
        pt.hide(True)
        g.append(pt)

    g.append(hou.ButtonParmTemplate(
        "publish", "Publish",
        script_callback="hou.phm().publish(kwargs)",
        script_callback_language=hou.scriptLanguage.Python,
    ))

    status = hou.StringParmTemplate("status", "Status", 1)
    status.setDefaultValue(("",))
    status.setDisableWhen("{ 1 == 1 }")
    g.append(status)

    return g


def build():
    repo_root = _repo_root()
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    import create_project as cp

    otl_path = os.path.join(repo_root, OTL_REL_PATH)
    os.makedirs(os.path.dirname(otl_path), exist_ok=True)

    # No 0.1/0.2 cohabitation: hda_def.save() on an existing library ADDS the
    # definition (both versions would cohabit in the same .hdanc, ambiguous TAB menu). We
    # start from a fresh file - the script is the source of truth, the blob is disposable.
    if os.path.exists(otl_path):
        os.remove(otl_path)

    stage = hou.node("/stage")

    existing = stage.node(BUILD_NODE_NAME)
    if existing:
        existing.destroy()

    subnet = stage.createNode("subnet", BUILD_NODE_NAME)
    indirect_in = subnet.indirectInputs()[0]

    cfg = subnet.createNode("configurelayer", "configure_publish_layer")
    cfg.setInput(0, indirect_in)
    cfg.parm("startnewlayer").set(1)
    cfg.parm("setsavepath").set(1)

    output0 = subnet.node("output0")
    output0.setInput(0, cfg)

    # Camera dedicated to the thumb, on a separate branch (never merged into output0):
    # the published layer must NEVER contain this camera.
    cam = subnet.createNode("camera", "thumb_cam")
    cam.setInput(0, cfg)
    cam.parm("primpath").set(THUMB_CAM_PRIMPATH)
    cam.parm("tx").set(0)
    cam.parm("ty").set(1.5)
    cam.parm("tz").set(6)
    cam.parm("lookatenable").set(1)
    cam.parm("lookatpositionx").set(0)
    cam.parm("lookatpositiony").set(0)
    cam.parm("lookatpositionz").set(0)

    # "Detached" ROPs: read a LOP via loppath (relative path), are never wired
    # into the main network flow. Triggered by the Python callback, not by the cook.
    publish_rop = subnet.createNode("usd_rop", "publish_rop")
    publish_rop.parm("loppath").set(publish_rop.relativePathTo(cfg))
    # 'flattenimplicitlayers' (NOT 'flattenstage'/'flattenalllayers'): collapses the
    # anonymous/in-memory sub-layers of the upstream LOP network (the test sphere, the Configure
    # Layer edits...) into the single file targeted by savepath, but preserves intact
    # the already file-backed references (e.g. a geo.usd sublayered from a previous step -
    # never touched, never re-serialized). 'separate' fails as soon as an upstream node produces
    # an anonymous layer without an explicit savepath (verified empirically: hou.OperationFailed
    # "Layer saved to a location generated from a node path").
    publish_rop.parm("savestyle").set("flattenimplicitlayers")
    # Without this toggle at 0, the ROP errors as soon as an upstream node produces an anonymous
    # layer without an explicit savepath (verified empirically), instead of flattening it silently
    # as the savestyle name promises. At 0: a single file written, confirmed by a
    # `find` over the whole test tree (see build_publish_hda investigation).
    publish_rop.parm("errorsavingimplicitpaths").set(0)
    publish_rop.parm("trange").set(0)                # current frame only

    thumb_rop = subnet.createNode("usdrender_rop", "thumb_rop")
    thumb_rop.parm("loppath").set(thumb_rop.relativePathTo(cam))
    thumb_rop.parm("trange").set(0)
    thumb_rop.parm("override_camera").set(THUMB_CAM_PRIMPATH)
    thumb_rop.parm("override_res").set("specific")
    thumb_rop.parm("res_user1").set(THUMB_RES)
    thumb_rop.parm("res_user2").set(THUMB_RES)
    # 'soho_foreground' ("Wait for Render to Complete", inherited from the node's Mantra legacy):
    # found by actual enumeration of node.parms() via hython (Houdini 21.0.631), not assumed
    # from the docs. Without it, node.render() in a GUI session returns as soon as husk is
    # SUBMITTED (not finished): the thumb.png arrives several seconds after the publish, in a
    # staging_dir already renamed/orphan (bug reproducible in GUI only, never in headless
    # hython where render() blocks naturally). publish_rop (type usd_rop, not usdrender_rop)
    # does not have this effect: it's an in-process layer serialization, not a separate husk.
    thumb_rop.parm("soho_foreground").set(1)

    subnet.layoutChildren()

    hda_node = subnet.createDigitalAsset(
        name=TYPE_NAME,
        hda_file_name=otl_path,
        description=TYPE_LABEL,
        min_num_inputs=1,
        max_num_inputs=1,
        ignore_external_references=True,
    )

    hda_def = hda_node.type().definition()
    hda_def.setParmTemplateGroup(_build_parm_template_group(cp))
    hda_def.addSection("PythonModule", _python_module_source())

    # Channel-reference: "../<parm>" resolves, for an internal node, to the parameter of the
    # node that contains it (the HDA instance itself once promoted). Set AFTER
    # createDigitalAsset so that the promoted parm name already exists.
    hda_node.node("configure_publish_layer").parm("savepath").setExpression(
        'chs("../_layer_savepath")'
    )
    hda_node.node("thumb_rop").parm("outputimage").setExpression(
        'chs("../_thumb_outputimage")'
    )
    # savestyle='flattenimplicitlayers' also writes a "binding" root layer (subLayers
    # -> our target file) on lopoutput, in addition to the target file itself (verified
    # empirically via pxr.Usd: the target file is already standalone, without subLayers - this
    # root layer is a disposable artifact, never useful downstream). Without this wiring it falls back
    # on the default $HIP naming and pollutes the repo (verified: plugins/../geo/untitled...).
    # Redirect to a temporary scratch directory (never staging_dir: finalize_publish_
    # version() must see ONLY the real layer + the thumb in staging_dir).
    hda_node.node("publish_rop").parm("lopoutput").setExpression(
        'chs("../_publish_scratch_output")'
    )
    # template_node=hda_node is MANDATORY: hda_def.save() without template_node does NOT
    # push the node's live state into the definition (verified empirically - the two
    # setExpression() above were otherwise silently lost, rawValue() empty after
    # reload). See the hou.HDADefinition.save doc: "If None, this method does not update
    # the definition's contents."
    hda_def.save(otl_path, template_node=hda_node)

    hda_node.destroy()

    print("[ok] HDA built: {}".format(otl_path))
    return otl_path


if __name__ == "__main__":
    build()
