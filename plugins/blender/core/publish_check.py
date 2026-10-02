# -*- coding: utf-8 -*-
"""Will this publish re-import correctly? (Blender side of the check.)

Two stages, both before anything is committed to the project:

  1. pre_publish_check()  - things that predictably break a re-import, read from the scene
                            (no mesh, empty mesh, missing texture file, texture outside the
                            project, negative scale, an object named like the USD root prim).
  2. verify_artifact()    - the artifact is RE-IMPORTED into a throwaway collection, fingerprinted
                            like the source objects, and judged by
                            create_project.validate_publish_roundtrip (single rule, DCC-agnostic,
                            also usable from Houdini). The throwaway data is removed again: the
                            user's scene, scene settings and datablocks end up exactly as before.

Issues use the scene_checker shape ({severity, obj_name, message, fix_id}) so the existing
ERROR / WARNING drawing code can show them unchanged.
"""

import os
import sys

import bpy
from mathutils import Vector

from . import usd_convention

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))

# Datablock collections an importer may create. Only those that exist in this Blender are used.
_ID_COLLECTIONS = ("objects", "meshes", "materials", "images", "armatures", "cameras", "lights",
                   "curves", "collections", "node_groups", "actions", "textures", "worlds",
                   "grease_pencils")


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


def _issue(severity, subject, message):
    return {"severity": severity, "obj_name": subject, "message": message, "fix_id": ""}


# ---------------------------------------------------------------------------------------
# Stage 1 - scene checks
# ---------------------------------------------------------------------------------------

def _inside(path, root):
    try:
        return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) \
            == os.path.realpath(root)
    except ValueError:      # different drives / mixed absolute+relative
        return False


def pre_publish_check(context, objects, entity, project_path, target):
    """Issues found on the scene BEFORE exporting. `objects` = what would be exported."""
    issues = []
    meshes = [o for o in objects if o.type == "MESH"]

    if not meshes:
        issues.append(_issue("ERROR", entity,
                             "Nothing to publish: no mesh object in the publish set"))

    for o in meshes:
        if len(o.data.polygons) == 0:
            issues.append(_issue("ERROR", o.name, "Mesh has no faces"))
        if any(s < 0 for s in o.scale):
            issues.append(_issue("WARNING", o.name,
                                 "Negative scale: normals may come back flipped"))
        if o.hide_render or o.hide_viewport:
            issues.append(_issue("WARNING", o.name,
                                 "Hidden object: exporters may skip it"))

    # Offline (USD) stage is rooted at /<Entity>: an object named exactly like the entity would
    # collide with that root prim.
    if target != "web":
        for o in objects:
            if o.name == entity:
                issues.append(_issue("ERROR", o.name,
                                     "Object has the entity's name: collides with the USD root prim"))

    seen = set()
    for o in meshes:
        for slot in o.material_slots:
            mat = slot.material
            if not mat or not mat.use_nodes or not mat.node_tree:
                continue
            for node in mat.node_tree.nodes:
                img = getattr(node, "image", None) if node.type == "TEX_IMAGE" else None
                if img is None or img.name in seen:
                    continue
                seen.add(img.name)
                if img.packed_file or img.source in {"GENERATED", "VIEWER"}:
                    continue
                path = bpy.path.abspath(img.filepath)
                if not path or not os.path.isfile(path):
                    issues.append(_issue("ERROR", img.name, f"Texture file is missing: {path or '(no path)'}"))
                elif target != "web" and not _inside(path, project_path):
                    issues.append(_issue("WARNING", img.name,
                                         "Texture lives outside the project: it will not follow "
                                         "the project if it is moved"))
    return issues


# ---------------------------------------------------------------------------------------
# Stage 2 - fingerprint + throwaway re-import
# ---------------------------------------------------------------------------------------

def fingerprint(context, objects):
    """DCC-agnostic fingerprint (schema: create_project.validate_publish_roundtrip) of the MESH
    objects among `objects`, evaluated (modifiers applied) like an exporter sees them."""
    depsgraph = context.evaluated_depsgraph_get()
    tris = uv_meshes = count = 0
    materials, names = set(), []
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3

    for obj in objects:
        if obj.type != "MESH":
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            if hasattr(mesh, "calc_loop_triangles"):
                mesh.calc_loop_triangles()
            tris += len(mesh.loop_triangles)
            if len(mesh.uv_layers):
                uv_meshes += 1
            count += 1
            names.append(obj.name)
            for slot in obj.material_slots:
                if slot.material:
                    materials.add(slot.material.name)
            world = obj.matrix_world
            for corner in evaluated.bound_box:
                p = world @ Vector(corner)
                for axis in range(3):
                    lo[axis] = min(lo[axis], p[axis])
                    hi[axis] = max(hi[axis], p[axis])
        finally:
            evaluated.to_mesh_clear()

    extents = sorted(h - l for l, h in zip(lo, hi)) if count else [0.0, 0.0, 0.0]
    return {"mesh_count": count, "tri_count": tris, "extents": extents,
            "uv_mesh_count": uv_meshes, "materials": sorted(materials),
            "object_names": names}


def _id_pointers():
    return {name: {i.as_pointer() for i in getattr(bpy.data, name)}
            for name in _ID_COLLECTIONS if hasattr(bpy.data, name)}


def _find_layer_collection(layer_collection, collection):
    if layer_collection.collection == collection:
        return layer_collection
    for child in layer_collection.children:
        found = _find_layer_collection(child, collection)
        if found:
            return found
    return None


def reimport_fingerprint(context, artifact_path):
    """Import `artifact_path` into a throwaway collection, fingerprint what came in, then remove
    EVERYTHING the importer created. Returns (fingerprint | None, error_message)."""
    scene = context.scene
    view_layer = context.view_layer
    before = _id_pointers()
    saved_settings = (scene.frame_start, scene.frame_end, scene.render.fps,
                      scene.unit_settings.scale_length)
    prev_active = view_layer.active_layer_collection

    holder = bpy.data.collections.new("YLOS_ROUNDTRIP")
    scene.collection.children.link(holder)
    before.setdefault("collections", set()).add(holder.as_pointer())
    fp, error = None, ""
    try:
        layer = _find_layer_collection(view_layer.layer_collection, holder)
        if layer is not None:
            view_layer.active_layer_collection = layer
        ext = os.path.splitext(artifact_path)[1].lower()
        if ext == ".glb":
            bpy.ops.import_scene.gltf(filepath=artifact_path)
        else:
            bpy.ops.wm.usd_import(filepath=artifact_path, **usd_convention.import_kwargs())
        view_layer.update()
        imported = [o for o in bpy.data.objects if o.as_pointer() not in before["objects"]]
        fp = fingerprint(context, imported)
    except Exception as exc:      # noqa: BLE001 - the importer's own failure IS the answer
        error = str(exc)
    finally:
        created = []
        for name, known in before.items():
            created.extend(i for i in getattr(bpy.data, name) if i.as_pointer() not in known)
        try:
            bpy.data.batch_remove(ids=created)
        except Exception as exc:  # noqa: BLE001
            print(f"[Ylos publish check] cleanup incomplete: {exc}")
        try:
            bpy.data.collections.remove(holder)
        except Exception:         # noqa: BLE001
            pass
        try:
            view_layer.active_layer_collection = prev_active
        except Exception:         # noqa: BLE001
            pass
        (scene.frame_start, scene.frame_end, scene.render.fps,
         scene.unit_settings.scale_length) = saved_settings
    return fp, error


def verify_artifact(context, artifact_path, objects):
    """Re-import the artifact and judge it against the source `objects`. Returns
    {"ok", "issues"} - ERROR issues mean: do not publish this."""
    issues = []
    expected = fingerprint(context, objects)
    actual, error = reimport_fingerprint(context, artifact_path)
    if actual is None:
        issues.append(_issue("ERROR", "re-import", f"The exported file does not re-import: {error}"))
        return {"ok": False, "issues": issues, "expected": expected, "actual": None}

    verdict = _cp().validate_publish_roundtrip(expected, actual)
    for e in verdict["errors"]:
        issues.append(_issue("ERROR", "re-import", e["message"]))
    for w in verdict["warnings"]:
        issues.append(_issue("WARNING", "re-import", w["message"]))
    return {"ok": verdict["ok"], "issues": issues, "expected": expected, "actual": actual}


def summarize(issues, limit=3):
    """One line for a report: '2 error(s): msg; msg' (errors first)."""
    errors = [i for i in issues if i["severity"] == "ERROR"]
    if not errors:
        return ""
    head = "; ".join(f"{i['obj_name']}: {i['message']}" for i in errors[:limit])
    more = f" (+{len(errors) - limit} more)" if len(errors) > limit else ""
    return f"{len(errors)} error(s): {head}{more}"
