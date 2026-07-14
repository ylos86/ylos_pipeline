# -*- coding: utf-8 -*-
"""Test e2e du launcher versionne tools/blender/launch_context.py.

Ce script tourne en python3 ordinaire (stdlib + create_project) : il monte une fixture
sur disque puis invoque REELLEMENT Blender en subprocess sur le launcher, une fois par
mode d'ouverture, et verifie exit code + marqueur de succes + peuplement de la scene
(objects=N loggue par le launcher AVANT de sortir).

Lancer :
  BLENDER=$(which blender || echo "/Applications/Blender.app/Contents/MacOS/Blender")
  YLOS_BLENDER="$BLENDER" python3 tools/blender/test_launch_context.py

Exit code != 0 en cas d'echec (assert / exception), 0 si tout passe.
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

# Cube USD minimal ecrit a la main (un prim Mesh suffit a peupler bpy.data.objects).
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


def _run_launcher(blender, log_path, extra_args):
    """Invoque Blender --background --python launcher -- <args>. Retourne (returncode, log)."""
    cmd = [blender, "--background", "--factory-startup",
           "--python", LAUNCHER, "--"] + extra_args
    env = {**os.environ, "YLOS_LAUNCH_LOG": log_path}
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
        _fail(f"{label}: marqueur 'LAUNCH SUCCESS' absent du log:\n{log}")
    m = re.search(r"objects=(\d+)", log)
    if not m:
        _fail(f"{label}: aucun 'objects=N' dans le log:\n{log}")
    n = int(m.group(1))
    if n <= 0:
        _fail(f"{label}: scene vide (objects={n}) - l'import n'a rien peuple")
    print(f"ok  {label}: exit 0, LAUNCH SUCCESS, objects={n}")


def main():
    import create_project as cp

    blender = _blender_bin()
    if not os.path.isfile(blender):
        _fail(f"binaire Blender introuvable : {blender} (definir $YLOS_BLENDER)")

    work = tempfile.mkdtemp(prefix="ylos_launch_test_")
    try:
        root = os.path.join(work, "src")
        cache = os.path.join(work, "cache")
        os.makedirs(root)
        os.makedirs(cache)

        # 1. Fixture : projet + entite via l'orchestrateur (logique unique).
        proj = cp.create("LaunchTest", root=root, cache=cache, prod_type="FILM")
        project_dir = str(proj["source"])
        entity = "PROP_Cube_Default"
        cp.create_asset(project_dir, entity, entity_type="asset", asset_type="PROP")

        with open(os.path.join(project_dir, "assets", entity, "manifest.json"),
                  encoding="utf-8") as fh:
            ent_manifest = json.load(fh)
        step = ent_manifest["steps"][0]

        # 2a. Publish niche contenant un .usda cube (contrat deux-phases : dossier par version).
        versioned = f"{entity}_{step}_v001"
        pub_dir = os.path.join(project_dir, "assets", entity, step, "publish", versioned)
        os.makedirs(pub_dir)
        cube_pub = os.path.join(pub_dir, f"{versioned}.usda")
        with open(cube_pub, "w", encoding="utf-8") as fh:
            fh.write(CUBE_USDA)

        # 2b. asset_root.usda (stub ecrit par create_asset) remplace par le cube : la scene
        #     par defaut resolue (scene_default) peuple alors reellement la scene.
        with open(os.path.join(project_dir, "assets", entity, cp.ASSET_ROOT_NAME),
                  "w", encoding="utf-8") as fh:
            fh.write(CUBE_USDA)

        # 3. Invocation A - chemin explicite (--path sur le publish niche).
        logA = os.path.join(work, "launchA.log")
        rc, log = _run_launcher(blender, logA, [
            "--project", project_dir, "--entity", entity, "--step", step,
            "--path", cube_pub, "--kind", "publish"])
        _assert_success(rc, log, "invocation A (--path publish niche)")

        # 4. Invocation B - sans --path : resolution via resolve_open_target (scene_default).
        logB = os.path.join(work, "launchB.log")
        rc, log = _run_launcher(blender, logB, [
            "--project", project_dir, "--entity", entity, "--step", step])
        _assert_success(rc, log, "invocation B (resolve scene_default)")
        if "resolve:" not in log or "scene_default" not in log:
            _fail(f"invocation B: la resolution scene_default n'apparait pas dans le log:\n{log}")
        print("ok  invocation B: resolve_open_target -> scene_default trace dans le log")

        # 5. Contexte pose : l'addon s'enregistre (--factory-startup ne l'active pas) et les
        #    enums gardes sont appliques. Verrouille aussi le check de registration : un guard
        #    casse (re-register en GUI / jamais de register en CI) ferait rater ces marqueurs.
        if "addon: register() OK" not in log:
            _fail(f"invocation B: addon non enregistre proprement (guard ?):\n{log}")
        for marker in (f"ylos_current_asset = {entity!r}",
                       "ylos_context_type = 'ASSET'",
                       "ylos_asset_type = 'PROP'"):
            if marker not in log:
                _fail(f"invocation B: contexte non pose - marqueur absent {marker!r}:\n{log}")
        print("ok  invocation B: addon enregistre + contexte pipeline pose "
              "(asset/context_type/asset_type)")

        print("\nPASS: launcher contextualise e2e OK")
        sys.exit(0)
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        _fail("exception inattendue", e)
