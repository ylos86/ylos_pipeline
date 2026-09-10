# -*- coding: utf-8 -*-
# Scene Builder (plan-usable-v1 Phase 1.3) — realizes create_project.scene_starter_spec()
# in Blender: a fresh, department-contextualized authoring scene for the active entity+step,
# saved as a versioned WIP .blend, ready to work without going through Save Version first.
#
# The orchestrator decides WHAT the starter contains (pure, serializable spec); this operator
# only REALIZES it — it never re-derives paths, versions or department rules locally.
import bpy
import getpass
import os
import sys
from bpy.props import BoolProperty

from ..core.project import apply_scene_preset, setup_scene_collections
from ..core import vocab
from .op_import_product import import_artifact

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


def _set_enum_safe(scene, prop, value, items):
    """Assign an enum value only if it belongs to the property's items — a value outside
    the enum (legacy/unknown) is skipped rather than crashing. Mirror of the launcher's
    guard, operator-free (a starter context comes from the spec, already validated)."""
    if value in {v for v, _label, _desc in items}:
        try:
            setattr(scene, prop, value)
            return True
        except Exception:
            return False
    return False


class YLOS_OT_CreateScene(bpy.types.Operator):
    """Create a fresh, department-contextualized authoring scene for the active
    entity+step, saved as a new WIP version (Scene Builder)."""
    bl_idname = "ylos.create_scene"
    bl_label = "New Scene"
    bl_description = ("Build a fresh authoring scene for the active entity + step "
                      "(preset, frame range, assembly reference, camera) and save it as a "
                      "new WIP version — no need to Save Version first")
    bl_options = {"REGISTER"}

    def invoke(self, context, event):
        scene = context.scene
        if not scene.ylos_project_path or not scene.ylos_current_asset:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}
        # Building a new scene replaces the current file — confirm in GUI. EXEC_DEFAULT
        # (headless / launcher 'create' verb) skips this and builds straight away.
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        scene0 = context.scene
        project = scene0.ylos_project_path
        entity  = scene0.ylos_current_asset
        step    = scene0.ylos_current_step
        proj_name = scene0.ylos_project_name

        if not project or not entity:
            self.report({"ERROR"}, "No active project or asset.")
            return {"CANCELLED"}

        cp = _cp()
        spec = cp.scene_starter_spec(entity, step, dcc="blender", project_root=project)
        if not spec.get("ok"):
            self.report({"ERROR"}, f"Cannot create scene: {spec.get('reason', 'unknown')}")
            return {"CANCELLED"}

        # 1. Start from an empty file — a starter is a NEW authoring scene, not an edit
        #    of whatever is open.
        try:
            bpy.ops.wm.read_homefile(use_empty=True)
        except Exception as e:
            self.report({"ERROR"}, f"Could not reset to an empty scene: {e}")
            return {"CANCELLED"}
        scene = bpy.context.scene

        # 2. Scene preset (renderer / fps / resolution / color) from prod_type.
        apply_scene_preset(scene, spec["prod_type"])

        # 3. Department collection scaffold (COL_WORLD / ENV / CHAR / CAM / ...).
        setup_scene_collections(scene)

        # 4. Frame range (shots, schema 2.1) — the manifest wins over the preset fps.
        fr = spec.get("frame_range")
        if fr:
            try:
                scene.frame_start = int(fr["start"])
                scene.frame_end   = int(fr["end"])
                if fr.get("fps"):
                    scene.render.fps = int(round(float(fr["fps"])))
            except (TypeError, ValueError, KeyError):
                self.report({"WARNING"}, f"Invalid frame_range ignored: {fr!r}")

        # 5. Assembly references — import the built root(s) the spec points to.
        placed = 0
        for ref in spec.get("references", []):
            p = ref.get("path")
            if p and os.path.isfile(p):
                try:
                    import_artifact(p)
                    placed += 1
                except Exception as e:
                    self.report({"WARNING"}, f"Reference not placed ({os.path.basename(p)}): {e}")

        # 6. Camera when the department needs one and the scene has none.
        if spec.get("camera") and not any(o.type == "CAMERA" for o in scene.objects):
            cam_data = bpy.data.cameras.new("CAM_Main_A")
            cam_obj = bpy.data.objects.new("CAM_Main_A", cam_data)
            (bpy.data.collections.get("COL_CAM") or scene.collection).objects.link(cam_obj)
            cam_obj.location = (7.36, -6.93, 4.96)
            cam_obj.rotation_euler = (1.109, 0.0, 0.815)
            scene.camera = cam_obj

        # 7. Pipeline context — stamp the scene so the panel and publish work immediately.
        ctx = spec["context"]
        scene.ylos_project_path  = project
        scene.ylos_project_name  = proj_name or os.path.basename(project.rstrip("/"))
        scene.ylos_current_asset = ctx["entity"]
        _set_enum_safe(scene, "ylos_current_step", ctx["step"], vocab.STEP_ITEMS_ALL)
        _set_enum_safe(scene, "ylos_context_type", ctx["context_type"], vocab.CONTEXT_TYPE_ITEMS)
        _set_enum_safe(scene, "ylos_asset_type", ctx["asset_type"], vocab.ASSET_TYPE_ITEMS)
        _set_enum_safe(scene, "ylos_prod_type", ctx["prod_type"], vocab.PROD_TYPE_ITEMS)
        scene.name = f"SCENE_{entity}_{step}"

        # 8. Save the initial versioned .blend at the allocated WIP path (single logic:
        #    the path comes from the spec, not rebuilt here).
        save_path = spec["wip"].get("path")
        if not save_path:
            self.report({"ERROR"}, "Spec has no WIP path for Blender.")
            return {"CANCELLED"}
        try:
            os.makedirs(os.path.dirname(save_path), exist_ok=True)
            bpy.ops.wm.save_as_mainfile(filepath=save_path, copy=False)
        except Exception as e:
            self.report({"ERROR"}, f"Scene built but save failed: {e}")
            return {"CANCELLED"}

        # 9. Prism-style sidecar (comment / user / date) — best-effort, never loses the .blend.
        try:
            cp._atomic_write_json(save_path + ".json", {
                "comment": "Scene starter",
                "user": getpass.getuser(),
                "date": cp._now(),
                "blender_version": bpy.app.version_string,
            })
        except Exception:
            pass

        self.report(
            {"INFO"},
            f"New scene: {os.path.basename(save_path)} "
            f"(v{spec['wip']['version']:03d}, {placed} reference{'s' if placed != 1 else ''})",
        )
        return {"FINISHED"}
