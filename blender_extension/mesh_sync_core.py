"""Pure helpers shared by Blender Mesh Sync and its unit tests."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Iterable, Sequence


MESH_SCHEMA_ID = "roblox-mesh-sync/4"
PREVIOUS_MESH_SCHEMA_ID = "roblox-mesh-sync/3"
LEGACY_MESH_SCHEMA_ID = "roblox-mesh-sync/2"
ORIGINAL_MESH_SCHEMA_ID = "roblox-mesh-sync/1"
REVERSE_SCHEMA_ID = "roblox-mesh-sync-reverse/3"
LEGACY_REVERSE_SCHEMA_ID = "roblox-mesh-sync-reverse/2"
ORIGINAL_REVERSE_SCHEMA_ID = "roblox-mesh-sync-reverse/1"
MESH_SYNC_VERSION = "0.10.6"
DEFAULT_PORT = 27182
CHUNK_SIZE = 256 * 1024
MAX_BLOB_SIZE = 32 * 1024 * 1024
MAX_IMAGE_SIZE = 1024
MAX_TRIANGLES = 20000


def stable_json_bytes(value) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def content_hash(value) -> str:
    return sha256_bytes(stable_json_bytes(value))


def rounded_tuple(values: Iterable[float], digits: int = 7):
    return tuple(round(float(value), digits) for value in values)


def chunk_bytes(value: bytes, chunk_size: int = CHUNK_SIZE):
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    return [value[offset:offset + chunk_size] for offset in range(0, len(value), chunk_size)] or [b""]


def validate_finite(values: Iterable[float], label: str):
    for value in values:
        if not math.isfinite(float(value)):
            raise ValueError(f"{label} contains a non-finite number")


def mesh_signature_payload(mesh_payload: dict):
    """Return only fields which affect uploaded mesh content.

    Object name, transform, material and collision settings deliberately do not
    participate, so instances of the same local mesh share one Roblox asset.
    """

    return {
        "vertices": mesh_payload["vertices"],
        "triangles": mesh_payload["triangles"],
        "cornerNormals": mesh_payload.get("cornerNormals", []),
        "cornerUvs": mesh_payload.get("cornerUvs", []),
        "cornerColors": mesh_payload.get("cornerColors", []),
    }


def appearance_signature_payload(appearance: dict):
    return {
        "mode": appearance["mode"],
        "maps": appearance.get("maps", {}),
        "material": appearance.get("material", ""),
        "color": appearance.get("color", [1.0, 1.0, 1.0]),
        "transparency": appearance.get("transparency", 0.0),
        "materialVariant": appearance.get("materialVariant"),
        "alphaMode": appearance.get("alphaMode"),
        "emissiveStrength": appearance.get("emissiveStrength", 0.0),
        "emissiveTint": appearance.get("emissiveTint"),
    }


def resolve_appearance_mode(
    use_roblox_material: bool,
    requested_mode: str,
    has_images: bool,
    has_colors: bool,
) -> str:
    """Resolve Blender appearance without an implicit Roblox Material fallback."""

    if use_roblox_material:
        return "MATERIAL"
    if requested_mode == "TEXTURE":
        return "TEXTURE"
    if requested_mode == "VERTEX":
        return "VERTEX"
    if requested_mode == "NONE":
        return "NONE"
    # AUTO and the legacy MATERIAL enum remain on the Blender appearance side.
    return "TEXTURE" if has_images else ("VERTEX" if has_colors else "NONE")


def has_shear(matrix_rows: Sequence[Sequence[float]], tolerance: float = 1e-5) -> bool:
    """Detect shear from the three columns of a 3x3 transform matrix."""

    columns = [tuple(float(matrix_rows[row][column]) for row in range(3)) for column in range(3)]
    lengths = [math.sqrt(sum(component * component for component in column)) for column in columns]
    if any(length <= tolerance for length in lengths):
        return True
    normalized = [tuple(component / length for component in column) for column, length in zip(columns, lengths)]
    return any(
        abs(sum(normalized[first][axis] * normalized[second][axis] for axis in range(3))) > tolerance
        for first, second in ((0, 1), (0, 2), (1, 2))
    )


def validate_document_limits(document: dict):
    meshes = document.get("meshes", [])
    instances = document.get("instances", [])
    images = document.get("images", [])
    hierarchy = document.get("hierarchy", [])
    if not instances:
        raise ValueError("No Part or MeshPart instances were produced")
    if len(meshes) > 1000:
        raise ValueError("Mesh Sync supports at most 1,000 unique meshes per send")
    if len(instances) > 10000:
        raise ValueError("Mesh Sync supports at most 10,000 instances per send")
    if len(images) > 1000:
        raise ValueError("Mesh Sync supports at most 1,000 unique images per send")
    if len(hierarchy) > 10000:
        raise ValueError("Mesh Sync supports at most 10,000 hierarchy nodes per send")
    if document.get("schema") == MESH_SCHEMA_ID:
        if document.get("model", {}).get("rootKind") not in {"BLENDER_SCENE", "STUDIO_SELECTION"}:
            raise ValueError("Mesh Sync model has an invalid root kind")
        for node in hierarchy:
            if node.get("kind") == "MODEL" and node.get("studioMode", "MODEL") not in {
                "MODEL", "FOLDER", "IGNORE",
            }:
                raise ValueError("Mesh Sync Empty has an invalid Studio mode")
    hierarchy_parent_order(hierarchy)
    mesh_hashes = {item.get("hash") for item in meshes}
    instance_ids = [item.get("id") for item in instances]
    if len(instance_ids) != len(set(instance_ids)):
        raise ValueError("Mesh Sync produced duplicate instance IDs")
    for instance in instances:
        kind = instance.get("kind", "MESH")
        if kind == "MESH" and instance.get("meshHash") not in mesh_hashes:
            raise ValueError(f"{instance.get('name', 'MeshPart')} references a missing mesh")
        if kind == "PART" and instance.get("partType") not in {
            "Block", "Ball", "Cylinder", "Wedge", "CornerWedge",
        }:
            raise ValueError(f"{instance.get('name', 'Part')} has an unsupported Part type")


def validate_reverse_document(document: dict):
    if document.get("schema") not in {
        REVERSE_SCHEMA_ID, LEGACY_REVERSE_SCHEMA_ID, ORIGINAL_REVERSE_SCHEMA_ID,
    }:
        raise ValueError("Unsupported Studio to Blender schema")
    objects = document.get("objects", [])
    meshes = document.get("meshes", [])
    images = document.get("images", [])
    hierarchy = document.get("hierarchy", [])
    if document.get("schema") == REVERSE_SCHEMA_ID:
        if document.get("model", {}).get("rootKind") not in {"BLENDER_SCENE", "STUDIO_SELECTION"}:
            raise ValueError("Studio document has an invalid root kind")
    if not objects:
        raise ValueError("Studio did not send any supported objects")
    if len(objects) > 10000 or len(meshes) > 1000 or len(images) > 1000 or len(hierarchy) > 10000:
        raise ValueError("Studio to Blender document exceeds the supported item limits")
    object_ids = [item.get("id") for item in objects]
    if any(not isinstance(value, str) or not value for value in object_ids):
        raise ValueError("Every received object must have an ID")
    if len(object_ids) != len(set(object_ids)):
        raise ValueError("Studio sent duplicate object IDs")
    mesh_hashes = {item.get("hash") for item in meshes}
    image_hashes = {item.get("hash") for item in images}
    appearance_hashes = {item.get("hash") for item in document.get("appearances", [])}
    if None in mesh_hashes or None in image_hashes or None in appearance_hashes:
        raise ValueError("Studio sent a record without a content hash")
    for item in objects:
        if item.get("kind") == "MESH" and item.get("meshHash") not in mesh_hashes:
            property_only = (
                document.get("schema") == REVERSE_SCHEMA_ID
                and item.get("geometryOwner") == "BLENDER"
                and item.get("geometryAvailable") is False
            )
            if not property_only:
                raise ValueError(f"{item.get('name', 'MeshPart')} references a missing mesh")
        if document.get("schema") == REVERSE_SCHEMA_ID:
            if item.get("geometryOwner", "STUDIO") not in {"BLENDER", "STUDIO"}:
                raise ValueError(f"{item.get('name', 'object')} has an invalid geometry owner")
        appearance_hash = item.get("appearanceHash")
        if appearance_hash and appearance_hash not in appearance_hashes:
            raise ValueError(f"{item.get('name', 'object')} references a missing appearance")
        size, cframe = item.get("size", []), item.get("cframe", [])
        if len(size) != 3 or len(cframe) != 12:
            raise ValueError(f"{item.get('name', 'object')} has an invalid transform")
        validate_finite((*size, *cframe), f"{item.get('name', 'object')} transform")
        mesh_size = item.get("meshSize")
        if mesh_size is not None:
            if item.get("kind") != "MESH" or not isinstance(mesh_size, (list, tuple)) or len(mesh_size) != 3:
                raise ValueError(f"{item.get('name', 'MeshPart')} has an invalid native mesh size")
            validate_finite(mesh_size, f"{item.get('name', 'MeshPart')} native mesh size")
            if any(float(value) <= 0.0 for value in mesh_size):
                raise ValueError(f"{item.get('name', 'MeshPart')} has an invalid native mesh size")
    for appearance in document.get("appearances", []):
        maps = appearance.get("maps", {})
        # HttpService:JSONEncode serializes an empty Luau table as [], because
        # it cannot infer whether the empty table was intended as an array or
        # dictionary. Treat only that empty-array form as an empty map.
        if maps == []:
            maps = {}
            appearance["maps"] = maps
        if not isinstance(maps, dict):
            raise ValueError("An appearance has an invalid image map table")
        for digest in maps.values():
            if digest is not None and digest not in image_hashes:
                raise ValueError("An appearance references a missing image")
    hierarchy_parent_order(hierarchy)


def hierarchy_parent_order(hierarchy: Sequence[dict]):
    """Return hierarchy nodes parent-first while accepting mixed Folder/Model parents."""

    by_id = {}
    for node in hierarchy:
        node_id = node.get("id")
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("Studio hierarchy contains a node without an ID")
        if node_id in by_id:
            raise ValueError("Studio hierarchy contains duplicate IDs")
        if node.get("kind") not in {"FOLDER", "MODEL"}:
            raise ValueError("Studio hierarchy contains an unsupported node kind")
        by_id[node_id] = node

    ordered = []
    state = {}

    def visit(node_id):
        status = state.get(node_id, 0)
        if status == 1:
            raise ValueError("Studio hierarchy contains a cycle")
        if status == 2:
            return
        state[node_id] = 1
        parent_id = by_id[node_id].get("parentId")
        if parent_id:
            if parent_id not in by_id:
                raise ValueError("Studio hierarchy references a missing parent")
            visit(parent_id)
        state[node_id] = 2
        ordered.append(by_id[node_id])

    for node_id in by_id:
        visit(node_id)
    return ordered
