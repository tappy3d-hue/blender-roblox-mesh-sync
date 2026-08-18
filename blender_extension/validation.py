from __future__ import annotations

from dataclasses import dataclass
import uuid

from .core import MAX_PART_SIZE, MIN_PART_SIZE, SUPPORTED_PART_TYPES, convert_size
from .geometry import local_mesh_size, mesh_signature
from .i18n import tr, trf
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
            issues.append(ValidationIssue("ERROR", tr("Supported objects must be Meshes"), obj.name))
            continue
        if part.part_type not in SUPPORTED_PART_TYPES:
            issues.append(ValidationIssue("ERROR", trf(
                "Unsupported Part type: {part_type}", part_type=part.part_type,
            ), obj.name))
        if not _valid_guid(part.guid):
            issues.append(ValidationIssue("ERROR", tr("The GUID is missing or invalid"), obj.name))
        elif part.guid in seen_guids:
            issues.append(ValidationIssue(
                "ERROR", trf("The GUID duplicates {name}", name=seen_guids[part.guid]), obj.name,
            ))
        else:
            seen_guids[part.guid] = obj.name

        if any(component <= 0 for component in obj.scale):
            issues.append(ValidationIssue("ERROR", tr("Negative or zero scale is not supported"), obj.name))
        if any(modifier.show_viewport for modifier in obj.modifiers):
            issues.append(ValidationIssue("ERROR", tr("Active modifiers are not supported"), obj.name))

        expected_signature = obj.get("rbx_mesh_signature", "")
        if not expected_signature or expected_signature != mesh_signature(obj.data):
            issues.append(ValidationIssue(
                "ERROR", tr("Primitive vertices were edited. Transform the object in Object Mode."), obj.name,
            ))

        _, _, world_scale = obj.matrix_world.decompose()
        local_size = local_mesh_size(obj.data)
        blender_size = tuple(local_size[index] * abs(world_scale[index]) for index in range(3))
        roblox_size = convert_size(blender_size, settings.studs_per_unit)
        if any(size < MIN_PART_SIZE or size > MAX_PART_SIZE for size in roblox_size):
            issues.append(ValidationIssue(
                "ERROR",
                trf(
                    "Roblox Size is out of range: {size}",
                    size=tuple(round(value, 4) for value in roblox_size),
                ),
                obj.name,
            ))
        if part.part_type == "Tube":
            if not 0.0 < part.tube_inner_ratio < 1.0:
                issues.append(ValidationIssue(
                    "ERROR", tr("Tube Inner Radius must be greater than 0 and less than 1"), obj.name,
                ))
            if part.tube_segments < 3 or part.tube_segments > 64:
                issues.append(ValidationIssue(
                    "ERROR", tr("Tube Segments must be between 3 and 64"), obj.name,
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
                    tr("A WedgePart produced by Tube decomposition is outside the Roblox Size range. Adjust the size, inner radius, or segments."),
                    obj.name,
                ))
            if estimated_tube_part_count(part.tube_segments) > 128:
                issues.append(ValidationIssue(
                    "WARNING",
                    trf(
                        "{name}: Tube alone uses {count} Parts",
                        name=obj.name, count=estimated_tube_part_count(part.tube_segments),
                    ),
                    obj.name,
                ))

    part_count = estimated_scene_part_count(scene)
    if part_count > 10000:
        issues.append(ValidationIssue(
            "ERROR", trf("Expanded Part count exceeds 10,000: {count}", count=part_count)
        ))
    if part_count > 500:
        issues.append(ValidationIssue(
            "WARNING", trf(
                "There are {count} synchronized Parts. Measure performance in Studio.", count=part_count,
            )
        ))
    return issues
