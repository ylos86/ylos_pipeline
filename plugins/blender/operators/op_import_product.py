# -*- coding: utf-8 -*-
# Import states (State-Manager-lite, INC-5). Absorbs the old op_load_publish.py (USD-only,
# flat import by path, no tracking of what was imported): an import now creates
# a TAGGED collection (custom props ylos_import_entity/step/version/path) placed under the
# same parent as entity creation (core.project.resolve_parent_collection), routed by
# extension (GLB -> import_scene.gltf, USD -> wm.usd_import). The collection is the stable
# identity of the import ('<entity>_<step>'): ylos.update_import (op_update_imports.py)
# replaces its CONTENT without changing its name, so that external references (cameras,
# animation) stay valid from one version to another.
import bpy
import os
import sys
from bpy.props import StringProperty, IntProperty
from ..core.asset import resolve_publish_entry
from ..core.project import (
    resolve_parent_collection, get_or_create_collection, link_collection,
    set_active_collection,
)
from ..core import usd_convention

REPO_ROOT = os.path.normpath(os.path.join(os.path.realpath(__file__), "..", "..", "..", ".."))


def _cp():
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    import create_project
    return create_project


# Mirror of launch_context.py (identical extension routing): USD -> merge via
# wm.usd_import, GLB/GLTF -> merge via import_scene.gltf. Unknown extensions -> explicit
# error, never a silently ignored import.
USD_IMPORT_EXTS = (".usd", ".usda", ".usdc", ".usdz", ".usdnc")
GLB_IMPORT_EXTS = (".glb", ".gltf")


def import_state_collection_name(entity: str, step: str) -> str:
    """Stable identity of an import: '<entity>_<step>'. Only one 'state' per (entity,
    step) in a scene - re-importing the same pair must go through Update, not create a
    duplicate (see YLOS_OT_ImportProduct.execute)."""
    return f"{entity}_{step}"


def import_artifact(abs_path: str) -> None:
    """Imports 'abs_path' into the ACTIVE collection (never a path resolved here - already
    provided by the caller via the orchestrator). Raises ValueError on an unknown extension:
    the caller turns it into an error report, never a silent crash nor a mainfile
    opened by mistake on an unsupported artifact.

    USD goes through core.usd_convention.import_kwargs(): our publishes are Y-up /
    metersPerUnit stages (docs/usd-convention.md), and those kwargs are what brings them back
    at their original Blender orientation and scale. Export and import must always be read
    together, never tuned on one side only."""
    ext = os.path.splitext(abs_path)[1].lower()
    if ext in GLB_IMPORT_EXTS:
        bpy.ops.import_scene.gltf(filepath=abs_path)
    elif ext in USD_IMPORT_EXTS:
        bpy.ops.wm.usd_import(filepath=abs_path, **usd_convention.import_kwargs())
    else:
        raise ValueError(f"unsupported artifact extension for import: {ext!r}")


def tag_import_collection(collection, entity: str, step: str, version: int, abs_path: str) -> None:
    collection["ylos_import_entity"] = entity
    collection["ylos_import_step"] = step
    collection["ylos_import_version"] = version
    collection["ylos_import_path"] = abs_path


class YLOS_OT_ImportProduct(bpy.types.Operator):
    """Import a published product (USD or GLB) into a tagged, versioned collection."""
    bl_idname = "ylos.import_product"
    bl_label = "Import Product"
    bl_description = "Import a published product into a tagged import collection"
    bl_options = {"REGISTER", "UNDO"}

    entity: StringProperty(name="Entity")
    step: StringProperty(name="Step")
    # 0 = latest published version (no 'latest' string sentinel - see the
    # 'version|latest' spec, an IntProperty=0 is safer on the UI/RNA side than a magic string).
    version: IntProperty(name="Version", default=0, min=0)

    def execute(self, context):
        scene = context.scene
        project_path = scene.ylos_project_path
        if not project_path or not self.entity or not self.step:
            self.report({"ERROR"}, "Missing project/entity/step.")
            return {"CANCELLED"}

        # The imported entity's family comes from the ORCHESTRATOR, never from the scene's
        # context enum. Cockpit bug this fixes: importing an asset while the scene context
        # said SHOT read shots/<asset>/manifest.json (absent) -> asset_type silently fell
        # back to PROP and the collection landed under the wrong parent. Called from the
        # launcher - where the context enum is whatever the previous file left behind -
        # that was the normal case, not the edge case.
        resolved = _cp().resolve_entity(project_path, self.entity)
        if resolved is None:
            self.report({"ERROR"}, f"Entity '{self.entity}' not found under project.")
            return {"CANCELLED"}
        ctx_type = resolved["family"]

        col_name = import_state_collection_name(self.entity, self.step)
        if bpy.data.collections.get(col_name):
            self.report(
                {"ERROR"},
                f"'{col_name}' is already imported in this scene - use Update Import instead.",
            )
            return {"CANCELLED"}

        entry = resolve_publish_entry(
            project_path, self.entity, self.step, self.version or None, ctx_type,
        )
        if not entry or not entry.get("abs_path"):
            target = f"v{self.version:03d}" if self.version else "latest"
            self.report(
                {"ERROR"}, f"No publish found for {self.entity}/{self.step} ({target}).",
            )
            return {"CANCELLED"}

        abs_path = entry["abs_path"]

        # Manifest already read by resolve_entity - no second disk read, and no family guess.
        manifest = resolved.get("manifest") or {}
        asset_type = manifest.get("type", "PROP")
        entity_ctx = (manifest.get("entity_type") or ctx_type).upper()

        parent, _label = resolve_parent_collection(asset_type, entity_ctx, scene)
        collection = get_or_create_collection(col_name)
        link_collection(collection, parent)

        previous_active = set_active_collection(context, collection)
        try:
            import_artifact(abs_path)
        except Exception as e:
            # Empty/taggable collection created for nothing - remove it rather than leave a
            # ghost "import" with no content in the outliner.
            bpy.data.collections.remove(collection)
            self.report({"ERROR"}, f"Import failed: {e}")
            return {"CANCELLED"}
        finally:
            context.view_layer.active_layer_collection = previous_active

        tag_import_collection(collection, self.entity, self.step, entry["version"], abs_path)

        n_objects = len(collection.objects)
        self.report(
            {"INFO"},
            f"Imported {self.entity}/{self.step} v{entry['version']:03d} "
            f"({n_objects} object(s)) -> '{col_name}'",
        )
        return {"FINISHED"}
