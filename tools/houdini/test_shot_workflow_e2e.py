#!/usr/bin/env hython
"""test_shot_workflow_e2e.py - COMPLETE end-to-end shot scenario (Increment 7 of the plan
docs/plan-houdini-shots.md). Exercises the whole chain on a disposable fixture:

    create shot (+ frame_range 2.1)
      -> save WIP animation (ylos_houdini.save_wip, versioned .hip)
      -> publish animation (HDA ylos::publish::0.2, step mode)          -> shot_root recomposed
      -> publish lighting  (HDA ylos::publish::0.2, step mode)          -> shot_root recomposed
      -> load shot as a LOP sublayer (ylos_houdini.sublayer_shot)
      -> render Karma -> $PROJ_CACHE/.../render/<shot>/lighting/v001/   (cache tier)
      -> deliver_render -> delivery/render/<shot>/lighting/v001/        (explicit copy)

At EACH step we verify the REAL deliverable on disk / in the manifest, not just the
function's return. The key point of the scenario: after the two publishes, shot_root.usda
must list the *lighting* layer BEFORE *animation* (SHOT_DOWNSTREAM_ORDER: on a shot
lighting overrides animation - the opposite of an asset).

MANUAL / HYTHON ONLY - outside CI (requires Houdini, a license, and the HDA installed via
the package plugins/houdini/ylos.json). CI (`python3 -m unittest`, without Houdini) never
loads this file: `import hou` top-level. The PURE functions of ylos_houdini.py (render
versioning, path resolution) are already covered without hou by tests/test_ylos_houdini.py;
this script covers the plumbing that REQUIRES Houdini (HDA, LOPs, husk render).

Distinct from test_publish_hda_e2e.py: that one faithfully reproduces an HDA node from the TAB menu
(non-pre-filled defaults, default_expression bugs). This one does not revisit that - it sets
project_root explicitly on the node (as the artist types a path) and focuses on
the chaining of the shot tools. The two are complementary, neither duplicates the other.

Usage:
    cd /a/neutral/directory
    hython /absolute/path/to/tools/houdini/test_shot_workflow_e2e.py
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import hou

# Absolute and resolved path (never bare dirname(__file__) - same symlink reason as the rest of the
# repo): this script must work regardless of the cwd. 3 dirname: this file -> houdini
# -> tools -> REPO.
_HERE = os.path.realpath(__file__)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
BRIDGE_DIR = os.path.join(REPO_ROOT, "plugins", "houdini", "python")

TYPE_NAME = "ylos::publish::0.2"

# Shot fixture: the prefix MUST be a SHOT_TYPES (naming validation at creation).
# LAYOUT is a valid SHOT_TYPES distinct from the published steps (animation / lighting) - we
# avoid the confusion of the "ANIMATION" type used elsewhere, which is also a step name.
SHOT_NAME = "LAYOUT_Sq010_Default"
SHOT_TYPE = "LAYOUT"

# Two published steps, in the workflow's chronological order. lighting is STRONGER than
# animation in SHOT_DOWNSTREAM_ORDER -> must end up first in the subLayers.
STEP_ANIM = "animation"
STEP_LIGHT = "lighting"

# Tiny frame_range: the Karma render must cover only 2 frames (the default 1001-1100 =
# 100 husk frames, useless to validate the plumbing).
FRAME_START = 1001
FRAME_END = 1002


def fail(msg):
    print("[FAIL] {}".format(msg))
    sys.exit(1)


def _publish_step_via_hda(project_source, shot_name, step):
    """Creates a ylos::publish::0.2 HDA node in /stage, configures it in step mode (asset_name
    = the shot, publish_kind = the step, project_root set explicitly) and triggers the
    publish callback EXACTLY like a click (pressButton). Verifies that the publish_kind menu
    exposes the step (read from the manifest) and that the final status starts with 'OK'. Returns
    the node (to be destroyed by the caller)."""
    stage = hou.node("/stage")
    try:
        node = stage.createNode(TYPE_NAME)
    except hou.OperationFailed as exc:
        fail("type '{}' not found (Houdini package not loaded? see "
             "plugins/houdini/ylos.json): {}".format(TYPE_NAME, exc))

    # project_root set by hand (the artist types the path) - we don't test here the
    # ~/.ylos/active_project default_expression (covered by test_publish_hda_e2e.py).
    node.parm("project_root").set(project_source)
    node.parm("asset_name").set(shot_name)

    # The publish_kind menu must expose the step (kind_menu_items reads the shot's manifest).
    node.parm("publish_kind").set(step)
    kind_items = node.parm("publish_kind").menuItems()
    if step not in kind_items:
        fail("publish_kind does not propose {!r} (menu = {!r}; shot manifest not read by "
             "kind_menu_items?)".format(step, kind_items))
    if node.evalParm("publish_kind") != step:
        fail("publish_kind = {!r}, expected {!r}".format(node.evalParm("publish_kind"), step))

    try:
        node.parm("publish").pressButton()
    except hou.OperationFailed as exc:
        print("[warn] pressButton ({}) raised: {}".format(step, exc))

    status = node.evalParm("status")
    if not status.startswith("OK"):
        fail("publish callback ({}) errored - status = {!r}".format(step, status))
    return node


def _assert_step_published(cp, project_source, shot_name, step):
    """Verifies on disk + manifest that a step was indeed just published: USD layer + thumb
    in <shot>/<step>/publish/v###/, step_publishes[step] entry status 'complete', and
    lop_publishes still empty (step mode never feeds it)."""
    publish_dir = Path(project_source) / "shots" / shot_name / step / "publish"
    if not publish_dir.is_dir():
        fail("no publish directory for step {}: {}".format(step, publish_dir))
    versions = sorted(p.name for p in publish_dir.iterdir() if p.is_dir())
    if not versions:
        fail("empty publish directory for step {}: {}".format(step, publish_dir))
    version_dir = publish_dir / versions[-1]
    produced = sorted(p.name for p in version_dir.iterdir())
    if not [n for n in produced if n != cp.LOP_THUMB_NAME]:
        fail("no USD layer in {} (produced={!r})".format(version_dir, produced))
    if cp.LOP_THUMB_NAME not in produced:
        fail("no thumbnail in {} (produced={!r})".format(version_dir, produced))

    manifest_path = Path(project_source) / "shots" / shot_name / cp.ASSET_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    step_entries = manifest.get(cp.STEP_PUBLISHES_KEY, {}).get(step)
    if not step_entries:
        fail("step_publishes[{!r}] absent/empty (kind mis-routed?): {}".format(
            step, manifest.get(cp.STEP_PUBLISHES_KEY)))
    if step_entries[-1].get("status") != "complete":
        fail("last step_publishes[{!r}] entry not 'complete': {!r}".format(
            step, step_entries[-1]))
    if manifest.get(cp.LOP_PUBLISHES_KEY):
        fail("lop_publishes not empty after a step publish {}: {!r}".format(
            step, manifest.get(cp.LOP_PUBLISHES_KEY)))
    print("[ok] step {:<10} published: {}".format(step, produced))


def main():
    # Houdini bridge imported by path (plugins/houdini/python is not a package); it
    # adds REPO_ROOT to sys.path itself and imports create_project (single logic).
    if BRIDGE_DIR not in sys.path:
        sys.path.insert(0, BRIDGE_DIR)
    import ylos_houdini as yh
    import create_project as cp

    tmp_base = tempfile.mkdtemp(prefix="ylos_shot_workflow_e2e_")
    created = []  # nodes to destroy at the end of the scenario
    try:
        # --- 1. Project + shot + frame_range (schema 2.1) -----------------------------------
        info = cp.create(
            "ShotWorkflowE2E",
            root=os.path.join(tmp_base, "projects"),
            cache=os.path.join(tmp_base, "cache"),
        )
        project_source = info["source"]
        cp.create_asset(project_source, SHOT_NAME, entity_type="shot", asset_type=SHOT_TYPE)
        fr = cp.set_frame_range(project_source, SHOT_NAME, FRAME_START, FRAME_END)
        manifest_path = Path(project_source) / "shots" / SHOT_NAME / cp.ASSET_MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != cp.SCHEMA_VERSION:
            fail("shot schema_version = {!r}, expected {!r}".format(
                manifest.get("schema_version"), cp.SCHEMA_VERSION))
        if manifest.get("frame_range") != {"start": FRAME_START, "end": FRAME_END,
                                           "fps": fr["fps"]}:
            fail("frame_range badly written to the manifest: {!r}".format(manifest.get("frame_range")))
        print("[ok] shot created, frame_range = {}".format(fr))

        # --- 2. Save WIP animation (versioned .hip) ----------------------------------------
        # Blank scene: the versioned WIP is the deliverable tested here, not its content (the
        # HDA publish below does not re-read this hip - it serializes the current stage, like
        # test_step_publish_shot). save_wip validates the step against the manifest and sets the
        # right license extension.
        wip = yh.save_wip(SHOT_NAME, STEP_ANIM, project_root=project_source)
        if wip["version"] != 1:
            fail("first WIP version = {}, expected 1".format(wip["version"]))
        if not Path(wip["path"]).is_file():
            fail("WIP file not written to disk: {}".format(wip["path"]))
        expected_wip_dir = Path(project_source) / "shots" / SHOT_NAME / STEP_ANIM / "wip"
        if Path(wip["path"]).parent != expected_wip_dir:
            fail("WIP outside the expected subtree {}: {}".format(expected_wip_dir, wip["path"]))
        print("[ok] WIP animation v{:03d} : {}".format(wip["version"], wip["path"]))

        # --- 3. Publish animation (HDA step mode) + shot_root recomposed -------------------
        created.append(_publish_step_via_hda(project_source, SHOT_NAME, STEP_ANIM))
        _assert_step_published(cp, project_source, SHOT_NAME, STEP_ANIM)
        shot_root = Path(project_source) / "shots" / SHOT_NAME / cp.SHOT_ROOT_NAME
        if not shot_root.is_file():
            fail("shot_root.usda not recomposed after the animation publish: {}".format(shot_root))
        text = shot_root.read_text(encoding="utf-8")
        # frame_range 2.1 -> timecodes in the stage header.
        for tc in ("startTimeCode = {}".format(FRAME_START),
                   "endTimeCode = {}".format(FRAME_END)):
            if tc not in text:
                fail("timecode absent from shot_root after frame_range: '{}' missing".format(tc))
        if 'defaultPrim = "ROOT"' not in text:
            fail("shot_root without defaultPrim = \"ROOT\" (shot root prim expected):\n{}".format(text))
        print("[ok] shot_root recomposed (animation only) + timecodes")

        # --- 4. Publish lighting (HDA step mode) + shot_root recomposed (order!) -----------
        created.append(_publish_step_via_hda(project_source, SHOT_NAME, STEP_LIGHT))
        _assert_step_published(cp, project_source, SHOT_NAME, STEP_LIGHT)
        text = shot_root.read_text(encoding="utf-8")
        # subLayers relative to the entity: '@lighting/publish/...@' (preceded by '@', NOT a '/'
        # at the start - the file lives at the shot root). We anchor on '@<step>/publish'.
        idx_light = text.find("@{}/publish".format(STEP_LIGHT))
        idx_anim = text.find("@{}/publish".format(STEP_ANIM))
        if idx_light < 0 or idx_anim < 0:
            fail("one of the two steps absent from shot_root's subLayers:\n{}".format(text))
        # SHOT_DOWNSTREAM_ORDER: lighting (stronger) must appear BEFORE animation.
        if idx_light > idx_anim:
            fail("incorrect subLayers order: lighting must precede animation "
                 "(SHOT_DOWNSTREAM_ORDER) - shot_root:\n{}".format(text))
        print("[ok] shot_root recomposed: lighting BEFORE animation (correct shot order)")

        # --- 5. Load shot as a LOP sublayer ------------------------------------------------
        sub = yh.sublayer_shot(SHOT_NAME, project_root=project_source)
        created.append(sub)
        filepath = sub.evalParm("filepath1")
        # The path must be written in $PROJ_ROOT (relocatable), not resolved absolute.
        if "$" + cp.ENV_ROOT not in filepath:
            fail("sublayer_shot did not write the path in ${}: {!r}".format(
                cp.ENV_ROOT, filepath))
        print("[ok] Load Shot: LOP sublayer -> {}".format(filepath))

        # --- 6. Shot camera (/ROOT/cameras/ convention) then Karma render -> cache ---------
        # The layers published by the HDA (without input) carry no camera. We inject
        # one under /ROOT/cameras/ (docs/usd-convention.md convention) so the render is
        # viable and render_shot auto-selects it (first Camera prim under /ROOT/cameras/).
        cam = hou.node("/stage").createNode("camera", "cam_main")
        cam.setInput(0, sub)
        cam.parm("primpath").set("/ROOT/cameras/cam_main")
        cam.setDisplayFlag(True)
        created.append(cam)

        version = yh.next_render_version(project_source, SHOT_NAME, STEP_LIGHT)
        if version != 1:
            fail("first render version = {}, expected 1".format(version))
        rop = yh.render_shot(SHOT_NAME, STEP_LIGHT, project_root=project_source)
        created.append(rop)
        # The output convention (literal $PROJ_CACHE expression + $F4) is deterministic,
        # verifiable without launching husk.
        expected_out = yh.render_output_expression(project_source, SHOT_NAME, STEP_LIGHT, version)
        if rop.evalParm("outputimage") != expected_out:
            fail("outputimage = {!r}, expected {!r}".format(
                rop.evalParm("outputimage"), expected_out))
        if rop.evalParm("f1") != FRAME_START or rop.evalParm("f2") != FRAME_END:
            fail("render range = {}-{}, expected {}-{} (manifest frame_range)".format(
                rop.evalParm("f1"), rop.evalParm("f2"), FRAME_START, FRAME_END))
        print("[ok] render_shot configured: {} (frames {}-{})".format(
            expected_out, FRAME_START, FRAME_END))

        # Real render (soho_foreground=1 -> blocks until the end, see CLAUDE.md gotcha).
        rop.render()
        render_v = yh.render_dir(project_source, SHOT_NAME, STEP_LIGHT) / "v{:03d}".format(version)
        exrs = sorted(p.name for p in render_v.glob("*.exr")) if render_v.is_dir() else []
        if not exrs:
            fail("no EXR rendered in {} (did husk complete? Apprentice = watermark, "
                 "not a blocker).".format(render_v))
        print("[ok] Karma render -> cache: {} ({} frame(s))".format(render_v, len(exrs)))

        # --- 7. Deliver: explicit copy cache -> delivery/ ----------------------------------
        delivered = yh.deliver_render(project_source, SHOT_NAME, STEP_LIGHT, version)
        if not delivered.is_dir():
            fail("deliver_render did not create the delivery folder: {}".format(delivered))
        delivered_exrs = sorted(p.name for p in delivered.glob("*.exr"))
        if delivered_exrs != exrs:
            fail("incomplete delivery: cache={!r} vs delivery={!r}".format(exrs, delivered_exrs))
        expected_delivery = (Path(project_source) / "delivery" / "render" / SHOT_NAME
                             / STEP_LIGHT / "v{:03d}".format(version))
        if delivered != expected_delivery:
            fail("delivery off-convention: {} (expected {})".format(delivered, expected_delivery))
        print("[ok] deliver_render -> {} ({} frame(s))".format(delivered, len(delivered_exrs)))

        print("[PASS] complete shot scenario (create -> WIP -> 2 publishes -> load -> render "
              "-> deliver)")

    finally:
        for node in created:
            try:
                node.destroy()
            except hou.ObjectWasDeleted:
                pass
        shutil.rmtree(tmp_base, ignore_errors=True)


if __name__ == "__main__":
    main()
