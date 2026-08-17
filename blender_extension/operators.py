from __future__ import annotations

import json
import os
import uuid

import bpy
from bpy.props import EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ExportHelper
from mathutils import Matrix

from .core import SUPPORTED_PART_TYPES
from .detection import detect_primitive, detect_tube_vertices
from .geometry import create_mesh, mesh_orientation_bases, mesh_signature, mesh_surface_samples
from .properties import PART_TYPE_ITEMS
from .serialization import serialize_scene
from .validation import estimated_scene_part_count, validate_scene


def ensure_scene_guid(scene):
    value = scene.get("rbx_model_guid", "")
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        value = str(uuid.uuid4())
        scene["rbx_model_guid"] = value
    return value


def ensure_unique_guids(scene):
    """Give newly duplicated tagged objects their own stable identifier."""

    seen = set()
    changed = 0
    for obj in sorted(scene.objects, key=lambda item: item.as_pointer()):
        settings = obj.rbx_primitive_sync
        if not settings.is_roblox_part:
            continue
        value = settings.guid
        try:
            canonical = str(uuid.UUID(value))
        except (ValueError, AttributeError, TypeError):
            canonical = ""
        if not canonical or canonical in seen:
            canonical = str(uuid.uuid4())
            settings.guid = canonical
            changed += 1
        seen.add(canonical)
    ensure_scene_guid(scene)
    return changed


def _get_backup_collection(scene):
    name = "Roblox Conversion Backup"
    collection = bpy.data.collections.get(name)
    if collection is None:
        collection = bpy.data.collections.new(name)
        scene.collection.children.link(collection)
    elif collection.name not in {child.name for child in scene.collection.children}:
        scene.collection.children.link(collection)
    collection.hide_viewport = True
    collection.hide_render = True
    return collection


def _backup_object(obj, scene):
    backup = obj.copy()
    if obj.data:
        backup.data = obj.data.copy()
    backup.name = f"Original_{obj.name}"
    _get_backup_collection(scene).objects.link(backup)
    settings = backup.rbx_primitive_sync
    settings.is_roblox_part = False
    settings.sync_enabled = False
    settings.guid = ""
    backup.hide_render = True
    return backup


def _analyze_object(obj, depsgraph, requested_type, tolerance):
    evaluated = obj.evaluated_get(depsgraph)
    evaluated_mesh = evaluated.to_mesh()
    try:
        bases = mesh_orientation_bases(evaluated_mesh)
        tube_result = None
        if requested_type in {"AUTO", "Tube"}:
            try:
                tube_result = detect_tube_vertices(
                    (tuple(vertex.co) for vertex in evaluated_mesh.vertices),
                    bases,
                )
            except ValueError:
                if requested_type == "Tube":
                    raise
        if requested_type == "Tube":
            return tube_result
        samples = mesh_surface_samples(evaluated_mesh)
        result = detect_primitive(samples, requested_type)
        if result.error <= tolerance:
            primitive_result = result
        else:
            primitive_result = detect_primitive(samples, requested_type, bases)
        if tube_result is not None and tube_result.error < primitive_result.error:
            return tube_result
        return primitive_result
    finally:
        evaluated.to_mesh_clear()


def _source_transform(detection):
    center = detection.source_center
    half_size = detection.source_half_size
    permutation = detection.canonical_to_source
    basis = detection.source_basis
    scaled_permutation = tuple(
        tuple(half_size[row] * permutation[row][column] for column in range(3))
        for row in range(3)
    )
    linear = tuple(
        tuple(
            sum(basis[row][axis] * scaled_permutation[axis][column] for axis in range(3))
            for column in range(3)
        )
        for row in range(3)
    )
    transform = Matrix((
        (
            linear[0][0], linear[0][1], linear[0][2],
            center[0],
        ),
        (
            linear[1][0], linear[1][1], linear[1][2],
            center[1],
        ),
        (
            linear[2][0], linear[2][1], linear[2][2],
            center[2],
        ),
        (0.0, 0.0, 0.0, 1.0),
    ))
    return transform


def _convert_object(obj, detection, scene, keep_backup):
    if obj.active_material:
        diffuse = tuple(obj.active_material.diffuse_color)
        source_color = diffuse[:3]
        source_transparency = max(0.0, min(1.0, 1.0 - diffuse[3]))
    else:
        source_color = tuple(obj.color[:3])
        source_transparency = max(0.0, min(1.0, 1.0 - obj.color[3]))

    if keep_backup:
        _backup_object(obj, scene)

    original_world = obj.matrix_world.copy()
    new_mesh = create_mesh(
        detection.part_type,
        tube_segments=detection.tube_segments or 16,
        tube_inner_ratio=detection.tube_inner_ratio or 0.5,
    )
    obj.data = new_mesh
    obj.modifiers.clear()
    obj.matrix_world = original_world @ _source_transform(detection)

    settings = obj.rbx_primitive_sync
    settings.is_roblox_part = False
    settings.sync_enabled = True
    settings.guid = str(uuid.uuid4())
    settings.part_type = detection.part_type
    if detection.part_type == "Tube":
        settings.tube_segments = detection.tube_segments
        settings.tube_inner_ratio = detection.tube_inner_ratio
    settings.is_roblox_part = True
    settings.color = source_color
    settings.transparency = source_transparency
    obj["rbx_mesh_signature"] = mesh_signature(new_mesh)


class RBX_OT_ConvertSelected(Operator):
    bl_idname = "rbx_primitive_sync.convert_selected"
    bl_label = "Convert Selected Meshes"
    bl_description = "Detect or assign Roblox primitive types and convert selected meshes"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        scene_settings = context.scene.rbx_primitive_sync
        selected = [
            obj for obj in context.selected_objects
            if obj.type == "MESH"
        ]
        if not selected:
            self.report({"ERROR"}, "Meshオブジェクトを選択してください")
            return {"CANCELLED"}

        depsgraph = context.evaluated_depsgraph_get()
        converted = []
        failures = []
        already_valid = 0
        for obj in selected:
            try:
                settings = obj.rbx_primitive_sync
                signature_is_current = (
                    settings.is_roblox_part
                    and obj.get("rbx_mesh_signature", "") == mesh_signature(obj.data)
                    and not any(modifier.show_viewport for modifier in obj.modifiers)
                )
                if signature_is_current:
                    already_valid += 1
                    continue
                if obj.matrix_world.to_3x3().determinant() <= 0:
                    failures.append(f"{obj.name}: negative or mirrored transforms are not supported")
                    continue
                detection = _analyze_object(
                    obj,
                    depsgraph,
                    scene_settings.conversion_type,
                    scene_settings.conversion_tolerance,
                )
                if detection.error > scene_settings.conversion_tolerance:
                    failures.append(
                        f"{obj.name}: {detection.part_type} deviation "
                        f"{detection.error:.4f} > {scene_settings.conversion_tolerance:.4f}"
                    )
                    continue
                _convert_object(
                    obj, detection, context.scene,
                    scene_settings.keep_conversion_backup,
                )
                converted.append((obj, detection))
            except Exception as error:
                failures.append(f"{obj.name}: {error}")

        ensure_scene_guid(context.scene)
        for message in failures[:8]:
            self.report({"WARNING"}, message)
        if not converted:
            if already_valid and not failures:
                self.report({"INFO"}, f"選択した{already_valid}個はすでに有効なプリミティブです")
                return {"FINISHED"}
            self.report({"ERROR"}, "変換できるメッシュがありませんでした")
            return {"CANCELLED"}

        summary = ", ".join(
            f"{obj.name}={result.part_type}" for obj, result in converted[:5]
        )
        self.report(
            {"INFO"},
            f"{len(converted)}個を変換しました ({summary}) / {len(failures)} skipped",
        )
        return {"FINISHED"}


class RBX_OT_AddPart(Operator):
    bl_idname = "rbx_primitive_sync.add_part"
    bl_label = "Add Roblox Part"
    bl_description = "Add a primitive that can be rebuilt as a standard Roblox Part"
    bl_options = {"REGISTER", "UNDO"}

    part_type: EnumProperty(name="Part Type", items=PART_TYPE_ITEMS, default="Block")
    tube_inner_ratio: FloatProperty(
        name="Inner Radius", min=0.05, max=0.95, default=0.5,
    )
    tube_segments: IntProperty(name="Segments", min=3, max=64, default=16)

    def execute(self, context):
        mesh = create_mesh(
            self.part_type,
            tube_segments=self.tube_segments,
            tube_inner_ratio=self.tube_inner_ratio,
        )
        obj = bpy.data.objects.new(self.part_type, mesh)
        target_collection = context.collection or context.scene.collection
        target_collection.objects.link(obj)
        obj.location = context.scene.cursor.location
        obj.display_type = "SOLID"
        obj.color = (0.639, 0.635, 0.647, 1.0)

        for selected in context.selected_objects:
            selected.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj

        settings = obj.rbx_primitive_sync
        settings.is_roblox_part = False
        settings.sync_enabled = True
        settings.guid = str(uuid.uuid4())
        settings.part_type = self.part_type
        if self.part_type == "Tube":
            settings.tube_segments = self.tube_segments
            settings.tube_inner_ratio = self.tube_inner_ratio
        settings.is_roblox_part = True
        obj["rbx_mesh_signature"] = mesh_signature(mesh)
        ensure_scene_guid(context.scene)
        return {"FINISHED"}


class RBX_OT_ValidateScene(Operator):
    bl_idname = "rbx_primitive_sync.validate_scene"
    bl_label = "Validate Scene"
    bl_description = "Validate every enabled Roblox primitive in this scene"
    bl_options = {"REGISTER"}

    def execute(self, context):
        ensure_unique_guids(context.scene)
        issues = validate_scene(context.scene)
        errors = [issue for issue in issues if issue.severity == "ERROR"]
        warnings = [issue for issue in issues if issue.severity == "WARNING"]

        if errors:
            error_names = {issue.object_name for issue in errors if issue.object_name}
            for obj in context.selected_objects:
                obj.select_set(False)
            for name in error_names:
                obj = context.scene.objects.get(name)
                if obj:
                    obj.select_set(True)
            for issue in errors[:8]:
                self.report({"ERROR"}, f"{issue.object_name}: {issue.message}")
            self.report({"ERROR"}, f"検証失敗: {len(errors)} errors / {len(warnings)} warnings")
            return {"CANCELLED"}

        for issue in warnings[:8]:
            self.report({"WARNING"}, issue.message)
        count = estimated_scene_part_count(context.scene)
        self.report({"INFO"}, f"検証完了: {count} parts / {len(warnings)} warnings")
        return {"FINISHED"}


class RBX_OT_RepairGuids(Operator):
    bl_idname = "rbx_primitive_sync.repair_guids"
    bl_label = "Repair Object IDs"
    bl_description = "Assign missing IDs and replace duplicate IDs"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        changed = ensure_unique_guids(context.scene)
        self.report({"INFO"}, f"{changed}個のObject IDを修復しました")
        return {"FINISHED"}


class RBX_OT_ExportJson(Operator, ExportHelper):
    bl_idname = "rbx_primitive_sync.export_json"
    bl_label = "Export Roblox Primitive JSON"
    bl_description = "Export enabled Roblox primitives for the Studio plugin"

    filename_ext = ".rbxprimitives.json"
    filter_glob: StringProperty(default="*.rbxprimitives.json;*.json", options={"HIDDEN"})

    def invoke(self, context, event):
        if not self.filepath:
            model_name = context.scene.rbx_primitive_sync.model_name or "BlenderModel"
            safe_name = "".join(character if character.isalnum() or character in "-_" else "_" for character in model_name)
            self.filepath = safe_name + self.filename_ext
        return super().invoke(context, event)

    def execute(self, context):
        ensure_unique_guids(context.scene)
        errors = [issue for issue in validate_scene(context.scene) if issue.severity == "ERROR"]
        if errors:
            for issue in errors[:8]:
                self.report({"ERROR"}, f"{issue.object_name}: {issue.message}")
            return {"CANCELLED"}

        payload = serialize_scene(context.scene)
        if not payload["parts"]:
            self.report({"ERROR"}, "同期対象のRoblox Partがありません")
            return {"CANCELLED"}

        filepath = bpy.path.ensure_ext(self.filepath, self.filename_ext)
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, "w", encoding="utf-8", newline="\n") as output:
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.write("\n")
        self.report({"INFO"}, f"{len(payload['parts'])} partsを書き出しました")
        return {"FINISHED"}


CLASSES = (
    RBX_OT_AddPart,
    RBX_OT_ConvertSelected,
    RBX_OT_ValidateScene,
    RBX_OT_RepairGuids,
    RBX_OT_ExportJson,
)
