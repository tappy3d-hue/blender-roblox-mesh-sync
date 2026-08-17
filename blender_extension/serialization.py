from __future__ import annotations

from datetime import datetime, timezone
import uuid

from .core import SCHEMA_ID, convert_size, linear_color_to_srgb, make_cframe, rounded
from .geometry import local_mesh_size
from .tube import compose_cframe, tube_wedge_components


def serialize_object(obj, studs_per_unit: float):
    settings = obj.rbx_primitive_sync
    location, rotation, world_scale = obj.matrix_world.decompose()
    local_size = local_mesh_size(obj.data)
    blender_size = tuple(local_size[index] * abs(world_scale[index]) for index in range(3))

    rotation_rows = tuple(tuple(rotation.to_matrix()[row][column] for column in range(3)) for row in range(3))
    size = convert_size(blender_size, studs_per_unit)
    cframe = make_cframe(location, rotation_rows, studs_per_unit)

    return {
        "id": settings.guid,
        "name": obj.name,
        "type": settings.part_type,
        "size": rounded(size),
        "cframe": rounded(cframe),
        "color": rounded(linear_color_to_srgb(settings.color)),
        "material": settings.material,
        "transparency": round(float(settings.transparency), 7),
        "anchored": bool(settings.anchored),
        "canCollide": bool(settings.can_collide),
        "canTouch": bool(settings.can_touch),
        "canQuery": bool(settings.can_query),
        "castShadow": bool(settings.cast_shadow),
    }


def serialize_object_parts(obj, studs_per_unit: float):
    """Serialize one Blender logical primitive to one or more standard Parts."""

    logical = serialize_object(obj, studs_per_unit)
    settings = obj.rbx_primitive_sync
    if settings.part_type != "Tube":
        return [logical]

    components = tube_wedge_components(
        logical["size"], settings.tube_inner_ratio, settings.tube_segments,
    )
    namespace = uuid.UUID(settings.guid)
    group = {
        "id": settings.guid,
        "name": obj.name,
        "type": "Tube",
        "segments": int(settings.tube_segments),
        "innerRatio": round(float(settings.tube_inner_ratio), 7),
        "partCount": len(components),
    }
    records = []
    for index, component in enumerate(components):
        record = dict(logical)
        record.update({
            "id": str(uuid.uuid5(namespace, f"tube-wedge:{index}")),
            "name": f"{obj.name}_Wedge_{index + 1:03d}",
            "type": "Wedge",
            "size": rounded(component["size"]),
            "cframe": rounded(compose_cframe(
                logical["cframe"], component["center"], component["rotation"],
            )),
            "sourceObjectId": settings.guid,
            "group": group,
            "tubeSegment": int(component["segment"]),
        })
        records.append(record)
    return records


def serialize_scene(scene):
    settings = scene.rbx_primitive_sync
    objects = [
        obj for obj in scene.objects
        if obj.rbx_primitive_sync.is_roblox_part and obj.rbx_primitive_sync.sync_enabled
    ]
    objects.sort(key=lambda obj: (obj.name.casefold(), obj.rbx_primitive_sync.guid))
    return {
        "schema": SCHEMA_ID,
        "generator": {"name": "Roblox Primitive Sync", "version": "0.10.3"},
        "model": {
            "id": scene.get("rbx_model_guid", ""),
            "name": settings.model_name or scene.name,
        },
        "units": {"name": "stud", "studsPerBlenderUnit": settings.studs_per_unit},
        "exportedAt": datetime.now(timezone.utc).isoformat(),
        "parts": [
            part
            for obj in objects
            for part in serialize_object_parts(obj, settings.studs_per_unit)
        ],
    }
