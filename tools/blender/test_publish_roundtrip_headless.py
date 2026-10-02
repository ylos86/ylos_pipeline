# -*- coding: utf-8 -*-
"""Headless Blender test: the publish re-import check (core/publish_check.py + the gates in
operators/op_publish.py).  REAL exports and re-imports, GLB (web target) and USD (offline target).

Locks:
  - a clean asset publishes, in BOTH formats, and says the re-import check passed;
  - the re-import is a throwaway: object / mesh / material / image / collection counts and the
    scene's frame range are identical before and after;
  - pre-check refusals (empty mesh, missing texture file) happen BEFORE a version is allocated -
    no staging folder, no published version;
  - a publish that does not come back as it left (simulated: an exporter that drops an object) is
    refused with its staging PRESERVED;
  - Check Publish (dry run) gives the same verdict and writes nothing to the project.

Run:
  BLENDER=${YLOS_BLENDER:-/Applications/Blender.app/Contents/MacOS/Blender}
  "$BLENDER" --background --python tools/blender/test_publish_roundtrip_headless.py
Exit code != 0 on failure.
"""
import os
import shutil
import sys
import tempfile
import traceback

_THIS = os.path.realpath(__file__)
REPO_ROOT = os.path.normpath(os.path.join(_THIS, "..", "..", ".."))
PLUGINS = os.path.join(REPO_ROOT, "plugins")
for p in (REPO_ROOT, PLUGINS):
    if p not in sys.path:
        sys.path.insert(0, p)


def _fail(msg, exc=None):
    print("FAIL:", msg)
    if exc is not None:
        traceback.print_exc()
    sys.exit(1)


def _check(cond, msg):
    if not cond:
        _fail(msg)
    print("ok ", msg)


def _counts(bpy):
    sc = bpy.context.scene
    return {
        "objects": len(bpy.data.objects), "meshes": len(bpy.data.meshes),
        "materials": len(bpy.data.materials), "images": len(bpy.data.images),
        "collections": len(bpy.data.collections),
        "frames": (sc.frame_start, sc.frame_end, sc.render.fps),
    }


def _reset(bpy):
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for m in list(bpy.data.meshes):
        bpy.data.meshes.remove(m)
    for m in list(bpy.data.materials):
        bpy.data.materials.remove(m)
    for i in list(bpy.data.images):
        bpy.data.images.remove(i)


def _make_asset_mesh(bpy, entity, name="Body", with_texture=None, empty=False, offset=0.0):
    """A GEO_<entity>_<name> cube with a UV map and a MAT_ material (+ optional texture)."""
    mesh = bpy.data.meshes.new(f"GEO_{entity}_{name}")
    if not empty:
        import bmesh
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1.0)
        bm.to_mesh(mesh)
        bm.free()
        mesh.uv_layers.new(name="UV0")
    obj = bpy.data.objects.new(f"GEO_{entity}_{name}", mesh)
    obj.location = (offset, 0, 0)
    bpy.context.scene.collection.objects.link(obj)
    mat = bpy.data.materials.new(f"MAT_{entity}_{name}")
    mat.use_nodes = True
    if with_texture:
        img = bpy.data.images.load(with_texture) if os.path.isfile(with_texture) else \
            bpy.data.images.new("missing_tex", 4, 4)
        if not os.path.isfile(with_texture):
            img.source = "FILE"
            img.filepath = with_texture
        tex = mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex.image = img
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    mesh.materials.append(mat)
    return obj


def _staging(project_dir, entity, step):
    """Leftover staging FOLDERS (children of a '.staging' dir). The '.staging' container itself
    stays after a committed publish - only its contents mean 'an unfinished publish'."""
    out = []
    for root, dirs, _files in os.walk(os.path.join(project_dir, "assets", entity, step)):
        if os.path.basename(root) == ".staging":
            out += dirs
    return out


def _published_versions(cp, project_dir, entity, step):
    """COMMITTED publishes only: allocate_publish_version registers a 'pending' manifest entry
    (no artifact yet) that finalize completes - a refused publish may legitimately leave that
    pending entry next to its preserved staging, but never a complete one."""
    return [e for e in cp.list_publishes(project_dir, entity, step, "asset")
            if e.get("status") == "complete" and e.get("artifact")]


def _run_target(bpy, cp, op_publish, work, prod_type, label):
    root = os.path.join(work, f"src_{label}")
    cache = os.path.join(work, f"cache_{label}")
    os.makedirs(root)
    os.makedirs(cache)
    proj = cp.create(f"RT_{label}", root=root, cache=cache, prod_type=prod_type)
    project_dir = str(proj["source"])
    entity = "PROP_Crate_Default"
    cp.create_asset(project_dir, entity, entity_type="asset", asset_type="PROP")
    step = "modeling"
    ctx = bpy.context

    # --- a texture that exists, inside the project ---
    tex_path = os.path.join(project_dir, "assets", entity, "tex_ok.png")
    img = bpy.data.images.new("tex_src", 4, 4)
    img.filepath_raw = tex_path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)

    # ============ 1. clean publish ============
    _reset(bpy)
    _make_asset_mesh(bpy, entity, "Body", with_texture=tex_path)
    _make_asset_mesh(bpy, entity, "Lid", offset=2.0)
    before = _counts(bpy)
    res = op_publish.publish_entity_step(ctx, project_dir, entity, step)
    _check(res["ok"], f"[{label}] clean asset publishes ({res['message'][:90]})")
    _check("re-import check OK" in res["message"], f"[{label}] message reports the re-import check")
    after = _counts(bpy)
    _check(before["collections"] == after["collections"]
           and before["objects"] == after["objects"]
           and before["meshes"] == after["meshes"]
           and before["materials"] == after["materials"]
           and before["frames"] == after["frames"],
           f"[{label}] the re-import left no trace in the scene ({before} -> {after})")
    n_pub = len(_published_versions(cp, project_dir, entity, step))
    _check(n_pub == 1, f"[{label}] exactly one version published")

    # The re-import ALONE (no thumbnail render involved) must restore every datablock count.
    from blender.core import publish_check
    art = [os.path.join(r, f) for r, _d, fs in os.walk(os.path.join(project_dir, "assets", entity, step))
           for f in fs if f.endswith((".glb", ".usdc"))][0]
    objs = [o for o in bpy.data.objects if o.type == "MESH"]
    c0 = _counts(bpy)
    verdict = publish_check.verify_artifact(ctx, art, objs)
    _check(verdict["ok"], f"[{label}] verify_artifact on the published file is ok "
                          f"({[i['message'] for i in verdict['issues']]})")
    _check(_counts(bpy) == c0, f"[{label}] verify_artifact leaves EVERY datablock count unchanged "
                               f"(images included): {c0} -> {_counts(bpy)}")

    # ============ 2. dry run: same verdict, nothing written ============
    chk = op_publish.check_publish(ctx, project_dir, entity, step)
    _check(chk["ok"], f"[{label}] Check Publish (dry run) agrees: ok ({[i['message'] for i in chk['issues']]})")
    _check(len(_published_versions(cp, project_dir, entity, step)) == n_pub
           and not _staging(project_dir, entity, step),
           f"[{label}] dry run allocated no version and left no staging folder")

    # ============ 3. pre-check: empty mesh -> refused BEFORE allocation ============
    _reset(bpy)
    _make_asset_mesh(bpy, entity, "Body")
    _make_asset_mesh(bpy, entity, "Ghost", empty=True)
    res = op_publish.publish_entity_step(ctx, project_dir, entity, step)
    _check(not res["ok"] and "pre-publish check" in res["message"],
           f"[{label}] empty mesh is refused by the pre-publish check ({res['message'][:80]})")
    _check(len(_published_versions(cp, project_dir, entity, step)) == n_pub
           and not _staging(project_dir, entity, step),
           f"[{label}] ...and no version was allocated, no staging left")

    # ============ 4. pre-check: missing texture file ============
    _reset(bpy)
    _make_asset_mesh(bpy, entity, "Body", with_texture=os.path.join(work, "nowhere", "gone.png"))
    res = op_publish.publish_entity_step(ctx, project_dir, entity, step)
    _check(not res["ok"] and "Texture file is missing" in res["message"],
           f"[{label}] missing texture file is refused ({res['message'][:90]})")
    chk = op_publish.check_publish(ctx, project_dir, entity, step)
    _check(not chk["ok"], f"[{label}] dry run reports the same refusal")

    # ============ 5. round trip failure: an exporter that silently drops an object ============
    _reset(bpy)
    body = _make_asset_mesh(bpy, entity, "Body")
    _make_asset_mesh(bpy, entity, "Lid", offset=2.0)
    export_name = "_glb_export" if prod_type == "XR" else "_usd_export"
    real = getattr(op_publish, export_name)

    def lossy(filepath, context, objects, *rest):
        return real(filepath, context, [body], *rest)     # the Lid never makes it out

    setattr(op_publish, export_name, lossy)
    try:
        res = op_publish.publish_entity_step(ctx, project_dir, entity, step)
    finally:
        setattr(op_publish, export_name, real)
    _check(not res["ok"] and "does not re-import correctly" in res["message"],
           f"[{label}] a lossy export is refused at the re-import check ({res['message'][:100]})")
    _check(len(_published_versions(cp, project_dir, entity, step)) == n_pub,
           f"[{label}] ...and nothing was committed")
    _check(bool(_staging(project_dir, entity, step)) or "staging preserved" in res["message"],
           f"[{label}] ...with the staging preserved for audit")


def main():
    import bpy
    import create_project as cp
    import blender as addon
    from blender.operators import op_publish

    work = tempfile.mkdtemp(prefix="ylos_publish_roundtrip_")
    try:
        try:
            addon.register()
        except Exception as e:
            _fail("addon.register() raised", e)

        _run_target(bpy, cp, op_publish, work, "XR", "glb")
        _run_target(bpy, cp, op_publish, work, "FILM", "usd")

        try:
            addon.unregister()
        except Exception as e:
            _fail("addon.unregister() raised", e)
        print("\nPASS: publish re-import check (GLB + USD) headless OK")
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
