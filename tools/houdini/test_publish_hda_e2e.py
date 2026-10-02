#!/usr/bin/env hython
"""test_publish_hda_e2e.py - reproduces a ylos::publish::0.2 node EXACTLY as Houdini's TAB
menu would, then calls the publish callback as-is.

MANUAL / HYTHON ONLY - outside CI (requires Houdini + the installed HDA). CI
(`python3 -m unittest`, without Houdini) never loads this file (`import hou` top-level).

Deliberate difference from a "practical" test: we pre-fill NO parameter by hand,
except `asset_name` (the only one the user fills manually in the real workflow).
Everything else (project_root via default_expression, asset_type via default_value, the hidden
promoted parms) must come from the REAL default values of the installed definition. It's the
only way to reproduce the two bugs (663f5c8, d51682e) that existed ONLY on a freshly
created node - a test that sets the parms by hand masks them, as happened.

To run from a NEUTRAL directory (never the repo root): an import bug that only
shows up when the cwd is not the root (see 663f5c8, masked by sys.path[0]='' when the
shell was already in REPO) must be reproducible here.

Usage:
    cd /a/neutral/directory
    hython /absolute/path/to/tools/houdini/test_publish_hda_e2e.py
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import hou

# Absolute and resolved path (never bare dirname(__file__), same reason as the symlink fix
# documented in build_publish_hda.py): this script must work regardless of the cwd.
_HERE = os.path.realpath(__file__)
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))

TYPE_NAME = "ylos::publish::0.2"
ASSET_TYPE = "CHARACTER"          # must match ASSET_TYPES[0] (the parm's real default_value)
ASSET_NAME = "CHARACTER_Test_Default"

# Step mode (0.2): shot fixture + publish of a step that produces a USD layer.
SHOT_NAME = "ANIMATION_Test_Default"   # prefix = a SHOT_TYPES; distinct from the published step
SHOT_TYPE = "ANIMATION"
SHOT_STEP = "lighting"                 # a DEFAULT_SHOT_STEPS, does produce a layer (not comp/2D)


def fail(msg):
    print("[FAIL] {}".format(msg))
    sys.exit(1)


def test_finalize_rejects_missing_thumb():
    """finalize_publish_version() must refuse to commit if the thumbnail is missing from staging -
    completeness contract (see orphan staging_dir bug: thumb.png written ~4s after the os.replace
    in a Houdini GUI session, async husk/usdrender_rop). Pure create_project.py, no need for
    hou/HDA - just reproduces the state of an incomplete staging_dir by hand."""
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project as cp

    tmp_base = tempfile.mkdtemp(prefix="ylos_finalize_contract_")
    try:
        info = cp.create(
            "FinalizeContractTest",
            root=os.path.join(tmp_base, "projects"),
            cache=os.path.join(tmp_base, "cache"),
        )
        project_source = info["source"]
        asset_name = "CHARACTER_ContractTest_Default"
        cp.create_asset(project_source, asset_name, entity_type="asset", asset_type="CHARACTER")

        staging_dir, final_dir = cp.allocate_publish_version(
            project_source, asset_name, "CHARACTER", comment="finalize contract test"
        )
        version = cp.publish_version_from_dir(final_dir)

        # Writes ONLY the layer, never the thumb - simulates exactly the staging_dir captured
        # by the bug (thumb render not yet finished at finalize time).
        layer_stem = "{}_lop_v{:03d}".format(asset_name, version)
        (staging_dir / (layer_stem + ".usd")).write_text("#usda 1.0\n", encoding="utf-8")

        expected_artifacts = [layer_stem, cp.LOP_THUMB_NAME]

        raised = False
        try:
            cp.finalize_publish_version(
                project_source, asset_name, staging_dir, final_dir, version,
                expected_artifacts, comment="finalize contract test"
            )
        except ValueError:
            raised = True

        if not raised:
            fail("finalize_publish_version() did NOT raise while thumb.png was missing from staging")
        if not staging_dir.is_dir():
            fail("staging_dir vanished while finalize should have failed before any replace")
        if final_dir.exists():
            fail("final_dir exists while finalize should have failed before any replace")

        entity_manifest_path = (
            Path(project_source) / "assets" / asset_name / cp.ASSET_MANIFEST_NAME
        )
        entity_manifest = json.loads(entity_manifest_path.read_text(encoding="utf-8"))
        entry = next(
            e for e in entity_manifest[cp.LOP_PUBLISHES_KEY] if e["version"] == version
        )
        if entry["status"] != "pending":
            fail(
                "the version entry switched to {!r} while finalize failed - "
                "expected 'pending' (never committed)".format(entry["status"])
            )

        print("[ok] finalize_publish_version() correctly rejects a staging_dir without a thumb")
        print("[PASS] test_finalize_rejects_missing_thumb")
    finally:
        shutil.rmtree(tmp_base, ignore_errors=True)


def main():
    # Import of create_project for the test SETUP (scaffold a project + an asset) -
    # distinct from the import the HDA's own embedded Python module does (that one is
    # under test, not bypassed: we don't touch sys.path for it, we just verify that its
    # own _repo_root() copes with the real HOME already loaded by the Houdini package).
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project as cp

    tmp_base = tempfile.mkdtemp(prefix="ylos_publish_e2e_")
    fake_home = os.path.join(tmp_base, "fake_home")
    os.makedirs(os.path.join(fake_home, ".ylos"), exist_ok=True)

    node = None
    real_home = os.environ.get("HOME")
    try:
        # 1. Real project + asset on disk (test fixture, not a pre-filled parm: it's
        #    the equivalent of a project already scaffolded before the user opens Houdini).
        info = cp.create(
            "E2ETest",
            root=os.path.join(tmp_base, "projects"),
            cache=os.path.join(tmp_base, "cache"),
        )
        project_source = info["source"]
        cp.create_asset(project_source, ASSET_NAME, entity_type="asset", asset_type=ASSET_TYPE)

        # 2. ~/.ylos/active_project file - THIS is the file that the project_root parm's
        #    default_expression reads. We never set project_root by hand on the node.
        active_project_file = os.path.join(fake_home, ".ylos", "active_project")
        with open(active_project_file, "w", encoding="utf-8") as f:
            f.write(project_source)

        # 3. Node EXACTLY like the TAB menu: no parm kwarg, type resolved from the
        #    installed .hdanc via the Houdini package (HOUDINI_OTLSCAN_PATH), not from a
        #    local build. If the type is not found, the package is not loaded - we
        #    report it distinctly from a callback failure.
        stage = hou.node("/stage")
        try:
            node = stage.createNode(TYPE_NAME)
        except hou.OperationFailed as exc:
            fail(
                "type '{}' not found (Houdini package not loaded? "
                "see plugins/houdini/ylos.json): {}".format(TYPE_NAME, exc)
            )

        # 4. HOME switched to the fake home ONLY now: Houdini has already started and
        #    scanned HOUDINI_OTLSCAN_PATH with the real HOME (the package resolves $YLOS_REPO,
        #    see plugins/houdini/ylos.json, at that moment, before this script). The only code
        #    that must see the switched HOME is project_root's default_expression (read at
        #    parm eval) and the import of the HDA's embedded module (which does not depend
        #    on HOME - it derives its repo root from its own installed definition path).
        os.environ["HOME"] = fake_home

        # 5. Only parm set by hand: asset_name (the only one the user fills).
        node.parm("asset_name").set(ASSET_NAME)

        # 6. Sanity-check of the REAL definition defaults, BEFORE any click - it's
        #    precisely what the previous test did not verify.
        asset_type_default = node.evalParm("asset_type")
        if not asset_type_default:
            fail(
                "asset_type is empty on a freshly created node (regression of the "
                "d51682e fix - default_value missing on the menu ParmTemplate)."
            )
        if asset_type_default != ASSET_TYPE:
            fail(
                "default asset_type = {!r}, expected {!r} (did ASSET_TYPES[0] change "
                "without updating this test?)".format(asset_type_default, ASSET_TYPE)
            )

        project_root_default = node.evalParm("project_root")
        if project_root_default != project_source:
            fail(
                "default project_root = {!r}, expected {!r} (broken default_expression or "
                "~/.ylos/active_project not read).".format(project_root_default, project_source)
            )

        status_before = node.evalParm("status")
        if status_before:
            fail("status not empty before any publish: {!r}".format(status_before))

        # 7. Triggers the callback EXACTLY like a user click (pressButton runs
        #    the parm's script_callback, not a direct call to hou.phm().publish()).
        try:
            node.parm("publish").pressButton()
        except hou.OperationFailed as exc:
            # The callback re-raises after writing "ERROR: ..." into status - the status
            # message (checked just after) is the source of truth, this one is only a net.
            print("[warn] pressButton raised: {}".format(exc))

        status_after = node.evalParm("status")
        if not status_after.startswith("OK"):
            fail("publish callback errored - status = {!r}".format(status_after))

        # 8. Disk verification, not just the status text: the real deliverable.
        publish_dir = os.path.join(project_source, "assets", ASSET_NAME, "lop", "publish")
        if not os.path.isdir(publish_dir):
            fail("no publish directory on disk: {}".format(publish_dir))
        versions = sorted(os.listdir(publish_dir))
        if not versions:
            fail("empty publish directory: {}".format(publish_dir))
        version_dir = os.path.join(publish_dir, versions[-1])
        produced = sorted(os.listdir(version_dir))
        layers = [n for n in produced if n != cp.LOP_THUMB_NAME]
        thumbs = [n for n in produced if n == cp.LOP_THUMB_NAME]
        if not layers:
            fail("no USD layer written in {} (produced={!r})".format(version_dir, produced))
        if not thumbs:
            fail("no thumbnail written in {} (produced={!r})".format(version_dir, produced))

        print("[ok] status         : {}".format(status_after))
        print("[ok] published ver. : {}".format(version_dir))
        print("[ok] files          : {}".format(produced))
        print("[PASS]")

    finally:
        if real_home is not None:
            os.environ["HOME"] = real_home
        else:
            os.environ.pop("HOME", None)
        if node is not None:
            node.destroy()
        shutil.rmtree(tmp_base, ignore_errors=True)


def test_step_publish_shot():
    """Step mode (0.2): on a shot fixture, publishing `publish_kind=lighting` must place
    the layer in shots/<shot>/lighting/publish/, write step_publishes['lighting'] to the
    manifest (status complete) and trigger the recomposition of shot_root.usda
    (refresh_entity_root, kind != 'lop'). Same faithful TAB-menu reproduction as main():
    only asset_name (the shot) and publish_kind are set by hand (the two values
    the user chooses in the real step workflow); asset_type keeps its default and is
    ignored by the callback in step mode."""
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project as cp

    tmp_base = tempfile.mkdtemp(prefix="ylos_publish_step_e2e_")
    fake_home = os.path.join(tmp_base, "fake_home")
    os.makedirs(os.path.join(fake_home, ".ylos"), exist_ok=True)

    node = None
    real_home = os.environ.get("HOME")
    try:
        info = cp.create(
            "E2EStepTest",
            root=os.path.join(tmp_base, "projects"),
            cache=os.path.join(tmp_base, "cache"),
        )
        project_source = info["source"]
        cp.create_asset(project_source, SHOT_NAME, entity_type="shot", asset_type=SHOT_TYPE)

        active_project_file = os.path.join(fake_home, ".ylos", "active_project")
        with open(active_project_file, "w", encoding="utf-8") as f:
            f.write(project_source)

        stage = hou.node("/stage")
        try:
            node = stage.createNode(TYPE_NAME)
        except hou.OperationFailed as exc:
            fail(
                "type '{}' not found (Houdini package not loaded? "
                "see plugins/houdini/ylos.json): {}".format(TYPE_NAME, exc)
            )

        os.environ["HOME"] = fake_home

        # The only two parms set by hand in the step workflow: the shot and the step.
        node.parm("asset_name").set(SHOT_NAME)
        node.parm("publish_kind").set(SHOT_STEP)

        # The publish_kind menu must expose the step (read from the shot's manifest by
        # kind_menu_items) - otherwise the set value would match no item.
        kind_items = node.parm("publish_kind").menuItems()
        if SHOT_STEP not in kind_items:
            fail(
                "publish_kind does not propose {!r} (generated menu = {!r}; shot manifest "
                "not read by kind_menu_items?)".format(SHOT_STEP, kind_items)
            )
        if node.evalParm("publish_kind") != SHOT_STEP:
            fail("publish_kind = {!r}, expected {!r}".format(
                node.evalParm("publish_kind"), SHOT_STEP))

        try:
            node.parm("publish").pressButton()
        except hou.OperationFailed as exc:
            print("[warn] pressButton raised: {}".format(exc))

        status_after = node.evalParm("status")
        if not status_after.startswith("OK"):
            fail("publish callback (step) errored - status = {!r}".format(status_after))

        # 1. The layer lands under the step subtree, NOT under lop/.
        publish_dir = os.path.join(
            project_source, "shots", SHOT_NAME, SHOT_STEP, "publish"
        )
        if not os.path.isdir(publish_dir):
            fail("no step publish directory on disk: {}".format(publish_dir))
        versions = sorted(os.listdir(publish_dir))
        if not versions:
            fail("empty step publish directory: {}".format(publish_dir))
        version_dir = os.path.join(publish_dir, versions[-1])
        produced = sorted(os.listdir(version_dir))
        if not [n for n in produced if n != cp.LOP_THUMB_NAME]:
            fail("no USD layer written in {} (produced={!r})".format(version_dir, produced))
        if cp.LOP_THUMB_NAME not in produced:
            fail("no thumbnail in {} (produced={!r})".format(version_dir, produced))

        # 2. The manifest records the entry under step_publishes[step], status complete.
        manifest_path = Path(project_source) / "shots" / SHOT_NAME / cp.ASSET_MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        step_entries = manifest.get(cp.STEP_PUBLISHES_KEY, {}).get(SHOT_STEP)
        if not step_entries:
            fail(
                "step_publishes[{!r}] absent/empty in the manifest (kind mis-routed?): {}".format(
                    SHOT_STEP, manifest.get(cp.STEP_PUBLISHES_KEY))
            )
        if step_entries[-1].get("status") != "complete":
            fail("last step_publishes[{!r}] entry not 'complete': {!r}".format(
                SHOT_STEP, step_entries[-1]))
        # The LOP publish must NOT have been fed in step mode.
        if manifest.get(cp.LOP_PUBLISHES_KEY):
            fail("lop_publishes not empty after a step publish: {!r}".format(
                manifest.get(cp.LOP_PUBLISHES_KEY)))

        # 3. shot_root.usda recomposed by finalize (kind != 'lop' -> refresh_entity_root).
        shot_root = Path(project_source) / "shots" / SHOT_NAME / cp.SHOT_ROOT_NAME
        if not shot_root.is_file():
            fail("shot_root.usda not recomposed after the step publish: {}".format(shot_root))

        print("[ok] status         : {}".format(status_after))
        print("[ok] published ver. : {}".format(version_dir))
        print("[ok] step_publishes : {}".format(step_entries[-1]))
        print("[ok] shot_root.usda : {}".format(shot_root))
        print("[PASS] test_step_publish_shot")

    finally:
        if real_home is not None:
            os.environ["HOME"] = real_home
        else:
            os.environ.pop("HOME", None)
        if node is not None:
            node.destroy()
        shutil.rmtree(tmp_base, ignore_errors=True)


if __name__ == "__main__":
    # Completeness contract first: fast, no real Houdini render, immediate feedback.
    # Then the full e2e with the real HDA/render (lop mode, then step mode). Fail-fast
    # (see fail()): if a test fails, the following ones don't run.
    test_finalize_rejects_missing_thumb()
    main()
    test_step_publish_shot()
