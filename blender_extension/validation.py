from __future__ import annotations

from dataclasses import dataclass
import uuid

from .core import MAX_PART_SIZE, MIN_PART_SIZE, SUPPORTED_PART_TYPES, convert_size
from .geometry import local_mesh_size, mesh_signature
from .tube import estimated_tube_part_count, tube_wedge_components


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    message: str
    object_name: str = ""


def _valid_guid(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError, TypeError):
        return False


def estimated_scene_part_count(scene) -> int:
    return sum(
        estimated_tube_part_count(obj.rbx_primitive_sync.tube_segments)
        if obj.rbx_primitive_sync.part_type == "Tube" else 1
        for obj in scene.objects
        if obj.rbx_primitive_sync.is_roblox_part and obj.rbx_primitive_sync.sync_enabled
    )


def validate_scene(scene):
    issues = []
    seen_guids = {}
    settings = scene.rbx_primitive_sync

    for obj in scene.objects:
        part = obj.rbx_primitive_sync
        if not part.is_roblox_part or not part.sync_enabled:
            continue

        if obj.type != "MESH":
            issues.append(ValidationIssue("ERROR", "対応オブジェクトはMeshである必要があります", obj.name))
            continue
        if part.part_type not in SUPPORTED_PART_TYPES:
            issues.append(ValidationIssue("ERROR", f"未対応のPart種別です: {part.part_type}", obj.name))
        if not _valid_guid(part.guid):
            issues.append(ValidationIssue("ERROR", "GUIDが未設定または不正です", obj.name))
        elif part.guid in seen_guids:
            issues.append(ValidationIssue(
                "ERROR", f"GUIDが{seen_guids[part.guid]}と重複しています", obj.name,
            ))
        else:
            seen_guids[part.guid] = obj.name

        if any(component <= 0 for component in obj.scale):
            issues.append(ValidationIssue("ERROR", "負またはゼロのスケールは使用できません", obj.name))
        if any(modifier.show_viewport for modifier in obj.modifiers):
            issues.append(ValidationIssue("ERROR", "有効なModifierは使用できません", obj.name))

        expected_signature = obj.get("rbx_mesh_signature", "")
        if not expected_signature or expected_signature != mesh_signature(obj.data):
            issues.append(ValidationIssue(
                "ERROR", "プリミティブの頂点が変更されています。Object Modeで変形してください", obj.name,
            ))

        _, _, world_scale = obj.matrix_world.decompose()
        local_size = local_mesh_size(obj.data)
        blender_size = tuple(local_size[index] * abs(world_scale[index]) for index in range(3))
        roblox_size = convert_size(blender_size, settings.studs_per_unit)
        if any(size < MIN_PART_SIZE or size > MAX_PART_SIZE for size in roblox_size):
            issues.append(ValidationIssue(
                "ERROR",
                f"Roblox Size範囲外です: {tuple(round(value, 4) for value in roblox_size)}",
                obj.name,
            ))
        if part.part_type == "Tube":
            if not 0.0 < part.tube_inner_ratio < 1.0:
                issues.append(ValidationIssue(
                    "ERROR", "TubeのInner Radiusは0より大きく1未満にしてください", obj.name,
                ))
            if part.tube_segments < 3 or part.tube_segments > 64:
                issues.append(ValidationIssue(
                    "ERROR", "TubeのSegmentsは3～64にしてください", obj.name,
                ))
            try:
                components = tube_wedge_components(
                    roblox_size, part.tube_inner_ratio, part.tube_segments,
                )
            except ValueError as error:
                issues.append(ValidationIssue("ERROR", str(error), obj.name))
                components = []
            invalid_component = next((
                component for component in components
                if any(value < MIN_PART_SIZE or value > MAX_PART_SIZE for value in component["size"])
            ), None)
            if invalid_component:
                issues.append(ValidationIssue(
                    "ERROR",
                    "Tube分解後のWedgePartがRoblox Size範囲外です。サイズ・内径・分割数を調整してください",
                    obj.name,
                ))
            if estimated_tube_part_count(part.tube_segments) > 128:
                issues.append(ValidationIssue(
                    "WARNING",
                    f"{obj.name}: Tubeだけで{estimated_tube_part_count(part.tube_segments)} Partsを使用します",
                    obj.name,
                ))

    part_count = estimated_scene_part_count(scene)
    if part_count > 10000:
        issues.append(ValidationIssue(
            "ERROR", f"展開後のPart数が10,000を超えます: {part_count}"
        ))
    if part_count > 500:
        issues.append(ValidationIssue(
            "WARNING", f"同期対象が{part_count}個あります。Studioで性能を計測してください"
        ))
    return issues
