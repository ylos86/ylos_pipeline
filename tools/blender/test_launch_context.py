# -*- coding: utf-8 -*-
"""e2e test of the versioned launcher tools/blender/launch_context.py.

This script runs in plain python3 (stdlib + create_project): it builds a fixture on
disk then ACTUALLY invokes Blender in a subprocess on the launcher, once per opening
mode, and checks the exit code + success marker + scene population
(objects=N logged by the launcher BEFORE exiting).

Run:
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  YLOS_BLENDER="$BLENDER" python3 tools/blender/test_launch_context.py

Exit code != 0 on failure (assert / exception), 0 if everything passes.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback

_THIS = os.path.realpath(__file__)
REPO_ROOT = os.path.normpath(os.path.join(_THIS, "..", "..", ".."))  # tools/blender/.. -> repo
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

LAUNCHER = os.path.join(REPO_ROOT, "tools", "blender", "launch_context.py")

# Minimal USD cube written by hand (a single Mesh prim is enough to populate bpy.data.objects).
CUBE_USDA = """#usda 1.0
(
    defaultPrim = "Cube"
    upAxis = "Y"
    metersPerUnit = 1
)

def Mesh "Cube"
{
    int[] faceVertexCounts = [4, 4, 4, 4, 4, 4]
    int[] faceVertexIndices = [0, 1, 3, 2, 2, 3, 5, 4, 4, 5, 7, 6, 6, 7, 1, 0, 1, 7, 5, 3, 6, 0, 2, 4]
    point3f[] points = [(-1, -1, -1), (-1, -1, 1), (-1, 1, -1), (-1, 1, 1),
                        (1, 1, -1), (1, 1, 1), (1, -1, -1), (1, -1, 1)]
}
"""


def _fail(msg, exc=None):
    print("FAIL:", msg)
    if exc is not None:
        traceback.print_exc()
    sys.exit(1)


def _blender_bin():
    return (os.environ.get("YLOS_BLENDER")
            or shutil.which("blender")
            or "/Applications/Blender.app/Contents/MacOS/Blender")


def _run_launcher(blender, log_path, extra_args, env_extra=None):
    """Invokes Blender --background --python launcher -- <args>. Returns (returncode, log).
    'env_extra' seeds the CHILD environment (used to prove the per-session $PROJ_ROOT
    override of Phase 0.2)."""
    cmd = [blender, "--background", "--factory-startup",
           "--python", LAUNCHER, "--"] + extra_args
    env = {**os.environ, "YLOS_LAUNCH_LOG": log_path, **(env_extra or {})}
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180, env=env)
    log = ""
    if os.path.isfile(log_path):
        with open(log_path, encoding="utf-8") as fh:
            log = fh.read()
    if proc.returncode != 0:
        print("--- STDOUT ---\n", proc.stdout)
        print("--- STDERR ---\n", proc.stderr)
        print("--- LOG ---\n", log)
    return proc.returncode, log


def _assert_success(rc, log, label):
    if rc != 0:
        _fail(f"{label}: exit code {rc} != 0")
    if "LAUNCH SUCCESS" not in log:
        _fail(f"{label}: 'LAUNCH SUCCESS' marker missing from the log:\n{log}")
    m = re.search(r"objects=(\d+)", log)
    if not m:
        _fail(f"{label}: no 'objects=N' in the log:\n{log}")
    n = int(m.group(1))
    if n <= 0:
        _fail(f"{label}: empty scene (objects={n}) - the import populated nothing")
    print(f"ok  {label}: exit 0, LAUNCH SUCCESS, objects={n}")


_PUBLISH_GLB_FIXTURE_SCRIPT = """
import bpy, json, os, sys
sys.path.insert(0, {repo_root!r})
sys.path.insert(0, os.path.join({repo_root!r}, "plugins"))
import create_project as cp
import blender as addon

addon.register()
scene = bpy.context.scene
scene.ylos_project_path  = {project_dir!r}
scene.ylos_project_name  = "GlbFixture"
scene.ylos_current_asset = {entity!r}
scene.ylos_current_step  = {step!r}
scene.ylos_context_type  = "ASSET"

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete()
bpy.ops.mesh.primitive_cube_add(size=2.0)

res = bpy.ops.ylos.publish('EXEC_DEFAULT', step={step!r}, allow_full_scene=True, load_after=False)
assert res == {{"FINISHED"}}, res

latest = cp.latest_publish_artifact({project_dir!r}, {entity!r}, {step!r})
assert latest and latest.get("abs_path"), latest
with open({out_json!r}, "w") as fh:
    json.dump({{"glb_path": latest["abs_path"]}}, fh)
"""


def _publish_glb_fixture(blender, project_dir, entity, step, work):
    """Generates a REAL .glb (pipeline_target='web') via the real publish pipeline (bpy.ops.
    ylos.publish, same path as the Publish button), not a hand-written file - the GLB is a
    binary format, unlike the hard-coded USDA cube used for the other invocations. Returns
    the absolute path of the published artifact."""
    script_path = os.path.join(work, "publish_glb_fixture.py")
    out_json = os.path.join(work, "glb_fixture.json")
    with open(script_path, "w", encoding="utf-8") as fh:
        fh.write(_PUBLISH_GLB_FIXTURE_SCRIPT.format(
            repo_root=REPO_ROOT, project_dir=project_dir, entity=entity, step=step,
            out_json=out_json,
        ))
    cmd = [blender, "--background", "--factory-startup", "--python", script_path]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0 or not os.path.isfile(out_json):
        print("--- STDOUT ---\n", proc.stdout)
        print("--- STDERR ---\n", proc.stderr)
        _fail(f"GLB fixture: real publish failed (exit {proc.returncode})")
    with open(out_json, encoding="utf-8") as fh:
        return json.load(fh)["glb_path"]


def main():
    import create_project as cp

    blender = _blender_bin()
    if not os.path.isfile(blender):
        _fail(f"Blender binary not found: {blender} (set $YLOS_BLENDER)")

    work = tempfile.mkdtemp(prefix="ylos_launch_test_")
    try:
        root = os.path.join(work, "src")
        cache = os.path.join(work, "cache")
        os.makedirs(root)
        os.makedirs(cache)

        # 1. Fixture: project + entity via the orchestrator (single logic).
        proj = cp.create("LaunchTest", root=root, cache=cache, prod_type="FILM")
        project_dir = str(proj["source"])
        entity = "PROP_Cube_Default"
        cp.create_asset(project_dir, entity, entity_type="asset", asset_type="PROP")

        with open(os.path.join(project_dir, "assets", entity, "manifest.json"),
                  encoding="utf-8") as fh:
            ent_manifest = json.load(fh)
        step = ent_manifest["steps"][0]

        # 2a. Nested publish containing a .usda cube (two-phase contract: one folder per version).
        versioned = f"{entity}_{step}_v001"
        pub_dir = os.path.join(project_dir, "assets", entity, step, "publish", versioned)
        os.makedirs(pub_dir)
        cube_pub = os.path.join(pub_dir, f"{versioned}.usda")
        with open(cube_pub, "w", encoding="utf-8") as fh:
            fh.write(CUBE_USDA)

        # 2b. asset_root.usda (stub written by create_asset) replaced by the cube: the
        #     resolved default scene (scene_default) then actually populates the scene.
        with open(os.path.join(project_dir, "assets", entity, cp.ASSET_ROOT_NAME),
                  "w", encoding="utf-8") as fh:
            fh.write(CUBE_USDA)

        # 3. Invocation A - explicit path (--path on the nested publish).
        logA = os.path.join(work, "launchA.log")
        rc, log = _run_launcher(blender, logA, [
            "--project", project_dir, "--entity", entity, "--step", step,
            "--path", cube_pub, "--kind", "publish"])
        _assert_success(rc, log, "invocation A (--path nested publish)")

        # 4. Invocation B - without --path: resolution via resolve_open_target (scene_default).
        logB = os.path.join(work, "launchB.log")
        rc, log = _run_launcher(blender, logB, [
            "--project", project_dir, "--entity", entity, "--step", step])
        _assert_success(rc, log, "invocation B (resolve scene_default)")
        if "resolve:" not in log or "scene_default" not in log:
            _fail(f"invocation B: the scene_default resolution does not appear in the log:\n{log}")
        print("ok  invocation B: resolve_open_target -> scene_default traced in the log")

        # 5. Context set: the addon registers (--factory-startup does not enable it) and the
        #    kept enums are applied. Also locks the registration check: a broken guard
        #    (re-register in GUI / never register in CI) would miss these markers.
        if "addon: register() OK" not in log:
            _fail(f"invocation B: addon not registered cleanly (guard?):\n{log}")
        for marker in (f"ylos_current_asset = {entity!r}",
                       "ylos_context_type = 'ASSET'",
                       "ylos_asset_type = 'PROP'"):
            if marker not in log:
                _fail(f"invocation B: context not set - marker missing {marker!r}:\n{log}")
        print("ok  invocation B: addon registered + pipeline context set "
              "(asset/context_type/asset_type)")

        # 6. Invocation C - web project (GLB): REAL publish then import_scene.gltf via the
        #    launcher (regression of the INC-3 bug - the launcher opened a .glb with
        #    open_mainfile, for lack of routing by extension; acceptance: 'Import' a version
        #    imports the GLB into a contextualized session).
        web_root = os.path.join(work, "web_src")
        web_cache = os.path.join(work, "web_cache")
        os.makedirs(web_root)
        os.makedirs(web_cache)
        web_proj = cp.create("LaunchTestWeb", root=web_root, cache=web_cache, prod_type="XR")
        web_project_dir = str(web_proj["source"])
        web_entity = "PROP_Cube_Default"
        cp.create_asset(web_project_dir, web_entity, entity_type="asset", asset_type="PROP")
        with open(os.path.join(web_project_dir, "assets", web_entity, "manifest.json"),
                  encoding="utf-8") as fh:
            web_step = json.load(fh)["steps"][0]

        glb_path = _publish_glb_fixture(blender, web_project_dir, web_entity, web_step, work)
        if not glb_path.lower().endswith(".glb"):
            _fail(f"GLB fixture: unexpected extension: {glb_path}")

        logC = os.path.join(work, "launchC.log")
        rc, log = _run_launcher(blender, logC, [
            "--project", web_project_dir, "--entity", web_entity, "--step", web_step,
            "--path", glb_path, "--kind", "publish"])
        _assert_success(rc, log, "invocation C (--path real GLB publish)")
        if "gltf_import" not in log:
            _fail(f"invocation C: 'gltf_import' mode missing from the log (extension routing "
                  f"broken?):\n{log}")
        print("ok  invocation C: GLB routing -> import_scene.gltf traced in the log")

        # 7. Invocation D - '--kind create' (New Scene, plan-usable-v1 Phase 1.4): NO
        #    --path; the launcher sets the context then hands over to ylos.create_scene,
        #    which reads scene_starter_spec() and allocates the WIP itself. Checked on a
        #    step that PULLS THE ASSEMBLY IN (asset/lookdev, see
        #    create_project._STARTER_ASSEMBLY_STEPS): the cube must land in the scene, so
        #    a starter that opens empty is a failure, not a pass.
        #    The child also gets a deliberately WRONG $PROJ_ROOT: the launcher must
        #    override it per session (tension #1 of CLAUDE.md, Phase 0.2 acceptance).
        create_step = "lookdev"
        if create_step not in ent_manifest["steps"]:
            _fail(f"fixture: step {create_step!r} not declared ({ent_manifest['steps']})")
        spec = cp.scene_starter_spec(entity, create_step, "blender", project_root=project_dir)
        if not spec.get("ok"):
            _fail(f"invocation D: scene_starter_spec refused the fixture: {spec.get('reason')}")
        expected_wip = spec["wip"]["path"]
        if os.path.exists(expected_wip):
            _fail(f"invocation D: the WIP already exists before the launch: {expected_wip}")

        logD = os.path.join(work, "launchD.log")
        rc, log = _run_launcher(
            blender, logD,
            ["--project", project_dir, "--entity", entity, "--step", create_step,
             "--kind", "create"],
            env_extra={"PROJ_ROOT": "/nonexistent/other/project/root"},
        )
        _assert_success(rc, log, "invocation D (--kind create)")
        if "LAUNCH SUCCESS: [create]" not in log:
            _fail(f"invocation D: mode 'create' missing from the success line:\n{log}")
        if "create: ylos.create_scene OK" not in log:
            _fail(f"invocation D: the operator was not the one that built the scene:\n{log}")
        if not os.path.isfile(expected_wip):
            _fail(f"invocation D: no WIP written at the path allocated by the spec: {expected_wip}")
        print(f"ok  invocation D: create -> {os.path.basename(expected_wip)} written by ylos.create_scene")

        # Per-session env: PROJ_ROOT is the PARENT of the project, and the inherited
        # (wrong) value was replaced - two DCCs on two projects never collide.
        expected_root = os.path.dirname(os.path.realpath(project_dir))
        if f"env: PROJ_ROOT overridden for this session" not in log or expected_root not in log:
            _fail(f"invocation D: $PROJ_ROOT not set per session to {expected_root!r}:\n{log}")
        if "/nonexistent/other/project/root" not in log:
            _fail(f"invocation D: the overridden shell value is not traced:\n{log}")
        print(f"ok  invocation D: per-session PROJ_ROOT = {expected_root}")

        # A second 'create' allocates the NEXT version, never an overwrite.
        logE = os.path.join(work, "launchE.log")
        rc, log = _run_launcher(blender, logE, [
            "--project", project_dir, "--entity", entity, "--step", create_step,
            "--kind", "create"])
        _assert_success(rc, log, "invocation E (create v002)")
        spec2 = cp.scene_starter_spec(entity, create_step, "blender", project_root=project_dir)
        if spec2["wip"]["version"] != spec["wip"]["version"] + 2:
            _fail(f"invocation E: versions not chained "
                  f"(after 2 creates the next should be {spec['wip']['version'] + 2}, "
                  f"got {spec2['wip']['version']})")
        print("ok  invocation E: a 2nd create allocates the next version (no overwrite)")

        print("\nPASS: contextualized launcher e2e OK")
        sys.exit(0)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _fail("unexpected exception", e)
