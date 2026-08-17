"""Blender-side collection and send operators for Roblox Mesh Sync."""

from __future__ import annotations

import math
import json
from pathlib import Path
import secrets
import uuid

import bpy
from bpy.types import Operator
from mathutils import Vector

from .core import convert_position, convert_rotation, convert_size, linear_color_to_srgb, rounded
from .mesh_sync_core import (
    CHUNK_SIZE,
    MAX_IMAGE_SIZE,
    MAX_TRIANGLES,
    MESH_SCHEMA_ID,
    MESH_SYNC_VERSION,
    appearance_signature_payload,
    content_hash,
    has_shear,
    mesh_signature_payload,
    resolve_appearance_mode,
    sha256_bytes,
    stable_json_bytes,
    validate_document_limits,
)
from .mesh_sync_server import SERVER
from .geometry import mesh_signature
from .material_preview import PREVIEW_MATERIAL_KEY
from .serialization import serialize_object_parts


OBJECT_GUID_KEY = "rbx_mesh_object_guid"
MESH_GUID_KEY = "rbx_mesh_group_guid"
COLLECTION_GUID_KEY = "rbx_mesh_collection_guid"
HIERARCHY_GUID_KEY = "rbx_mesh_hierarchy_guid"
HIERARCHY_PARENT_KEY = "rbx_mesh_hierarchy_parent_guid"
ROOT_KIND_KEY = "rbx_mesh_root_kind"
DOCUMENT_MODEL_GUID_KEY = "rbx_mesh_document_model_guid"
DOCUMENT_MODEL_NAME_KEY = "rbx_mesh_document_model_name"
DOCUMENT_ROOT_KIND_KEY = "rbx_mesh_document_root_kind"
MATERIAL_VARIANT_KEY = "rbx_mesh_material_variant"
APPEARANCE_METADATA_KEY = "rbx_mesh_appearance_metadata"

# Blender can produce different generated UV values when the same Mesh data is
# evaluated independently through equivalent Bevel modifiers. Reusing one
# evaluation is safe for local-only modifier stacks whose complete inputs are
# represented by the shared Mesh and scalar modifier settings. Keep this list
# deliberately narrow; object-dependent modifiers must still be evaluated per
# object so genuine result differences receive separate asset IDs.
SHAREABLE_EVALUATED_MODIFIER_TYPES = {"BEVEL"}
_NON_EVALUATION_MODIFIER_PROPERTIES = {
    "rna_type", "name", "custom_profile", "is_active", "show_expanded", "show_in_editmode",
    "show_on_cage", "show_render", "show_viewport", "use_pin_to_last",
}


def _valid_uuid(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return ""


def _ensure_guid(owner, key):
    value = _valid_uuid(owner.get(key, ""))
    if not value:
        value = str(uuid.uuid4())
        owner[key] = value
    return value


def _ensure_object_guid(obj):
    """Use one stable GUID whether the object is sent as a Part or MeshPart."""

    settings = obj.rbx_primitive_sync
    object_guid = _valid_uuid(obj.get(OBJECT_GUID_KEY, ""))
    primitive_guid = _valid_uuid(settings.guid)
    value = object_guid or primitive_guid or str(uuid.uuid4())
    obj[OBJECT_GUID_KEY] = value
    settings.guid = value
    return value


def _validate_primitive(obj):
    settings = obj.rbx_primitive_sync
    if any(float(component) <= 0 for component in obj.scale):
        raise ValueError(f"{obj.name}: negative or zero scale is not supported")
    if obj.matrix_world.to_3x3().determinant() <= 0:
        raise ValueError(f"{obj.name}: mirrored transforms are not supported")
    if has_shear(obj.matrix_world):
        raise ValueError(f"{obj.name}: shear is not supported")
    if any(modifier.show_viewport for modifier in obj.modifiers):
        raise ValueError(f"{obj.name}: Roblox Parts cannot have active modifiers")
    if obj.get("rbx_mesh_signature", "") != mesh_signature(obj.data):
        raise ValueError(
            f"{obj.name}: プリミティブの頂点が変更されています。Object Modeで変形してください"
        )
    if settings.part_type not in {"Block", "Ball", "Cylinder", "Wedge", "CornerWedge", "Tube"}:
        raise ValueError(f"{obj.name}: unsupported Roblox Part type {settings.part_type}")


def _axis_vector(vector):
    return (float(vector[0]), float(vector[2]), -float(vector[1]))


def _color_value(data):
    srgb = getattr(data, "color_srgb", None)
    if srgb is not None:
        return [round(float(srgb[index]), 7) for index in range(min(4, len(srgb)))]
    value = getattr(data, "color", (1.0, 1.0, 1.0, 1.0))
    converted = (*linear_color_to_srgb(value), float(value[3]) if len(value) > 3 else 1.0)
    return [round(float(component), 7) for component in converted]


def _active_color_attribute(mesh):
    attributes = getattr(mesh, "color_attributes", None)
    if not attributes:
        return None
    return attributes.active_color or attributes.active


def _mesh_color(attribute, loop_index, vertex_index):
    if attribute is None:
        return None
    if attribute.domain == "CORNER":
        return _color_value(attribute.data[loop_index])
    if attribute.domain == "POINT":
        return _color_value(attribute.data[vertex_index])
    return None


def _shareable_evaluation_key(obj):
    """Return a per-send cache key only for provably equivalent local stacks."""

    modifiers = [modifier for modifier in obj.modifiers if modifier.show_viewport]
    if not modifiers:
        return (obj.data.as_pointer(), "BASE_MESH")
    if any(modifier.type not in SHAREABLE_EVALUATED_MODIFIER_TYPES for modifier in modifiers):
        return None
    # Custom Bevel profiles contain CurveProfile data that is not represented by
    # the scalar RNA settings below, so evaluate those objects independently.
    if any(getattr(modifier, "profile_type", "SUPERELLIPSE") == "CUSTOM" for modifier in modifiers):
        return None

    stack = []
    for modifier in modifiers:
        settings = {}
        for prop in modifier.bl_rna.properties:
            if prop.identifier in _NON_EVALUATION_MODIFIER_PROPERTIES or prop.is_readonly:
                continue
            if prop.type not in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}:
                return None
            value = getattr(modifier, prop.identifier)
            if getattr(prop, "array_length", 0) > 0:
                settings[prop.identifier] = tuple(float(item) for item in value)
            else:
                settings[prop.identifier] = float(value) if prop.type == "FLOAT" else value
        stack.append({"type": modifier.type, "settings": settings})

    # Vertex group names map modifier inputs to the weights stored on the shared
    # Mesh. Include the mapping so equal names with different indices do not get
    # collapsed accidentally.
    vertex_groups = [(group.index, group.name) for group in obj.vertex_groups]
    return (
        obj.data.as_pointer(),
        content_hash({"modifiers": stack, "vertexGroups": vertex_groups}),
    )


def _collect_mesh(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        mesh.calc_loop_triangles()
        if not mesh.vertices or not mesh.loop_triangles:
            raise ValueError("mesh has no triangles")
        if len(mesh.loop_triangles) > MAX_TRIANGLES:
            raise ValueError(f"{len(mesh.loop_triangles)} triangles exceeds the {MAX_TRIANGLES} limit")

        minimum = [min(vertex.co[axis] for vertex in mesh.vertices) for axis in range(3)]
        maximum = [max(vertex.co[axis] for vertex in mesh.vertices) for axis in range(3)]
        center = tuple((minimum[axis] + maximum[axis]) * 0.5 for axis in range(3))
        local_size = tuple(maximum[axis] - minimum[axis] for axis in range(3))
        if any(value <= 1e-8 for value in local_size):
            raise ValueError("mesh must have non-zero size on all three axes")

        vertices = [
            rounded(_axis_vector(tuple(vertex.co[axis] - center[axis] for axis in range(3))))
            for vertex in mesh.vertices
        ]
        uv_layer = mesh.uv_layers.active
        color_attribute = _active_color_attribute(mesh)
        triangles = []
        corner_normals = []
        corner_uvs = []
        corner_colors = []

        for triangle in mesh.loop_triangles:
            vertex_indices = [int(value) for value in triangle.vertices]
            loop_indices = [int(value) for value in triangle.loops]
            triangles.append(vertex_indices)
            face_normals, face_uvs, face_colors = [], [], []
            for vertex_index, loop_index in zip(vertex_indices, loop_indices):
                loop = mesh.loops[loop_index]
                face_normals.append(rounded(_axis_vector(loop.normal)))
                if uv_layer is not None:
                    uv = uv_layer.data[loop_index].uv
                    face_uvs.append([round(float(uv.x), 7), round(1.0 - float(uv.y), 7)])
                color = _mesh_color(color_attribute, loop_index, vertex_index)
                if color is not None:
                    face_colors.append(color)
            corner_normals.append(face_normals)
            if face_uvs:
                corner_uvs.append(face_uvs)
            if face_colors:
                corner_colors.append(face_colors)

        payload = {
            "vertices": vertices,
            "triangles": triangles,
            "cornerNormals": corner_normals,
            "cornerUvs": corner_uvs,
            "cornerColors": corner_colors,
        }
        digest = content_hash(mesh_signature_payload(payload))
        return payload, digest, center, local_size, uv_layer is not None, color_attribute is not None
    finally:
        evaluated.to_mesh_clear()


def _principled(material):
    if not material or not material.use_nodes or not material.node_tree:
        return None
    for node in material.node_tree.nodes:
        if node.type == "BSDF_PRINCIPLED":
            return node
    return None


def _find_image_from_socket(socket, visited=None, selected_channel=None):
    if socket is None or not socket.is_linked:
        return None
    visited = visited or set()
    for link in socket.links:
        node = link.from_node
        if node.as_pointer() in visited:
            continue
        visited.add(node.as_pointer())
        if node.type == "TEX_IMAGE" and node.image is not None:
            return node.image, selected_channel
        channel = selected_channel
        if node.type in {"SEPARATE_COLOR", "SEPRGB"}:
            channel_names = {"RED": 0, "R": 0, "GREEN": 1, "G": 1, "BLUE": 2, "B": 2}
            channel = channel_names.get(link.from_socket.name.upper(), selected_channel)
        for input_socket in node.inputs:
            image = _find_image_from_socket(input_socket, visited, channel)
            if image is not None:
                return image
    return None


def _used_materials(obj):
    indices = {polygon.material_index for polygon in obj.data.polygons}
    values = []
    for index in sorted(indices):
        if index < len(obj.material_slots):
            material = obj.material_slots[index].material
            # Live Roblox Material previews contain Image Texture nodes for
            # viewport display only. They must never turn Auto export into
            # Texture/PBR mode or replace the selected Roblox Material enum.
            if material and not material.get(PREVIEW_MATERIAL_KEY) and material not in values:
                values.append(material)
    return values


def _linear_to_srgb(value):
    value = max(0.0, min(1.0, float(value)))
    return 12.92 * value if value <= 0.0031308 else 1.055 * (value ** (1.0 / 2.4)) - 0.055


def _image_bytes(image, color_map, selected_channel=None):
    width, height = int(image.size[0]), int(image.size[1])
    if width <= 0 or height <= 0:
        raise ValueError(f"image {image.name} has no pixels")
    if width > MAX_IMAGE_SIZE or height > MAX_IMAGE_SIZE:
        raise ValueError(f"image {image.name} is {width}x{height}; maximum is 1024x1024")
    try:
        pixels = tuple(image.pixels[:])
    except RuntimeError as error:
        raise ValueError(f"image {image.name} could not be read: {error}") from error
    expected = width * height * 4
    if len(pixels) != expected:
        raise ValueError(f"image {image.name} returned an unexpected pixel count")

    output = bytearray(expected)
    for output_y in range(height):
        source_y = height - 1 - output_y
        for x in range(width):
            source = (source_y * width + x) * 4
            target = (output_y * width + x) * 4
            for channel in range(3):
                source_channel = selected_channel if selected_channel is not None else channel
                value = pixels[source + source_channel]
                if color_map:
                    value = _linear_to_srgb(value)
                output[target + channel] = max(0, min(255, int(round(value * 255.0))))
            output[target + 3] = max(0, min(255, int(round(pixels[source + 3] * 255.0))))
    return bytes(output), width, height


def _material_maps(material):
    principled = _principled(material)
    if principled is None:
        return {}, [1.0, 1.0, 1.0]
    socket_names = {
        "baseColor": "Base Color",
        "roughness": "Roughness",
        "metallic": "Metallic",
        "normal": "Normal",
    }
    images = {}
    for role, name in socket_names.items():
        image_info = _find_image_from_socket(principled.inputs.get(name))
        if image_info is not None:
            images[role] = image_info
    base = principled.inputs.get("Base Color")
    color = list(base.default_value[:3]) if base is not None else [1.0, 1.0, 1.0]
    return images, rounded(color)


def _appearance_for(obj, has_uv, has_colors, image_records, image_blobs):
    settings = obj.rbx_primitive_sync
    use_roblox_material = bool(settings.is_roblox_part or settings.mesh_use_roblox_material)
    requested_mode = settings.mesh_blender_appearance_mode
    materials = [] if use_roblox_material else _used_materials(obj)
    if len(materials) > 1 and requested_mode in {"AUTO", "TEXTURE"}:
        raise ValueError("Texture/PBR Mesh Sync requires one baked/atlas material per object")
    material = materials[0] if materials else None
    images, material_color = (
        ({}, [1.0, 1.0, 1.0])
        if use_roblox_material else _material_maps(material)
    )
    mode = resolve_appearance_mode(
        use_roblox_material, requested_mode, bool(images), has_colors,
    )
    if mode == "TEXTURE" and not has_uv:
        raise ValueError("Texture/PBR appearance requires an active UV map")
    if mode == "TEXTURE" and not images:
        raise ValueError("Texture/PBR appearance has no connected image maps")
    if mode == "VERTEX" and not has_colors:
        raise ValueError("Vertex Color appearance requires an active Color Attribute")

    appearance = {
        "mode": mode,
        "maps": {},
        "material": settings.material,
        "color": rounded(linear_color_to_srgb(
            settings.color if mode == "MATERIAL" else material_color
        )),
        "transparency": round(float(settings.transparency), 7),
    }
    raw_variant = obj.get(MATERIAL_VARIANT_KEY, "")
    if raw_variant:
        try:
            appearance["materialVariant"] = json.loads(raw_variant)
        except (TypeError, json.JSONDecodeError):
            obj.pop(MATERIAL_VARIANT_KEY, None)
    raw_metadata = obj.get(APPEARANCE_METADATA_KEY, "")
    if raw_metadata:
        try:
            appearance.update(json.loads(raw_metadata))
        except (TypeError, json.JSONDecodeError):
            obj.pop(APPEARANCE_METADATA_KEY, None)
    if mode == "TEXTURE":
        for role, image_info in images.items():
            image, selected_channel = image_info
            raw, width, height = _image_bytes(image, role == "baseColor", selected_channel)
            digest = sha256_bytes(width.to_bytes(4, "little") + height.to_bytes(4, "little") + raw)
            appearance["maps"][role] = digest
            if digest not in image_records:
                image_records[digest] = {
                    "hash": digest,
                    "name": image.name,
                    "role": role,
                    "width": width,
                    "height": height,
                    "byteSize": len(raw),
                    "chunkCount": max(1, math.ceil(len(raw) / CHUNK_SIZE)),
                }
                source_uri = image.get("rbx_mesh_source_uri", "")
                if source_uri and image.get("rbx_mesh_source_forward_hash") == digest:
                    image_records[digest]["sourceUri"] = source_uri
                image_blobs[digest] = raw
    appearance_hash = content_hash(appearance_signature_payload(appearance))
    appearance["hash"] = appearance_hash
    appearance["previewHash"] = appearance_hash
    return appearance


def _blend_model_name(context, fallback):
    filepath = getattr(bpy.data, "filepath", "")
    return Path(filepath).stem if filepath else (fallback or context.scene.name)


def _collection_parent_map(scene):
    parents = {}

    def visit(collection):
        for child in collection.children:
            parents.setdefault(child, collection)
            visit(child)

    visit(scene.collection)
    return parents


def _document_root_for_collection(scene, collection, parents):
    current = collection
    while current and current != scene.collection:
        if _valid_uuid(current.get("rbx_model_guid", "")):
            return current
        current = parents.get(current)
    return None


def _selection_document_root(scene, selected, parents):
    scopes = {}
    has_unscoped_object = False
    for obj in selected:
        direct_model_id = obj.get(DOCUMENT_MODEL_GUID_KEY, "")
        if _valid_uuid(direct_model_id):
            scopes[direct_model_id] = {
                "id": direct_model_id,
                "name": obj.get(DOCUMENT_MODEL_NAME_KEY, "Studio Selection"),
                "rootKind": obj.get(DOCUMENT_ROOT_KIND_KEY, "STUDIO_SELECTION"),
                "collection": None,
            }
            continue
        object_roots = {
            root
            for collection in obj.users_collection
            if (root := _document_root_for_collection(scene, collection, parents)) is not None
        }
        if len(object_roots) > 1:
            raise ValueError(f"{obj.name}: 複数の同期ルートCollectionに所属しています")
        if object_roots:
            root = next(iter(object_roots))
            model_id = root.get("rbx_model_guid", "")
            scopes[model_id] = {
                "id": model_id,
                "name": root.name,
                "rootKind": root.get(ROOT_KIND_KEY, "STUDIO_SELECTION"),
                "collection": root,
            }
        else:
            has_unscoped_object = True
    if len(scopes) > 1:
        raise ValueError("異なる同期ルートのオブジェクトは分けて送信してください")
    if scopes and has_unscoped_object:
        raise ValueError("同期済みオブジェクトと未所属オブジェクトは分けて送信してください")
    return next(iter(scopes.values()), None)


def _collect_hierarchy(scene, selected, *, parents=None, document_root=None):
    """Build collection/empty hierarchy records without depending on transforms."""

    parents = parents or _collection_parent_map(scene)
    included_collections = set()
    for obj in selected:
        for collection in obj.users_collection:
            current = collection
            while current and current != scene.collection:
                # A root Collection created by Studio -> Blender represents the
                # document itself. Sending it as a normal hierarchy node caused
                # Studio Selection/Studio Selection.001 nesting on every round trip.
                if current == document_root:
                    break
                included_collections.add(current)
                current = parents.get(current)

    empty_objects = set()
    for obj in selected:
        current = obj.parent
        while current is not None:
            if current.type == "EMPTY":
                empty_objects.add(current)
            current = current.parent

    collection_ids = {
        collection: _ensure_guid(collection, COLLECTION_GUID_KEY)
        for collection in included_collections
    }
    empty_ids = {
        obj: _ensure_guid(obj, HIERARCHY_GUID_KEY)
        for obj in empty_objects
    }
    records = []
    collection_id_values = set(collection_ids.values())
    empty_id_values = set(empty_ids.values())
    for collection in sorted(included_collections, key=lambda item: item.name.casefold()):
        parent = parents.get(collection)
        actual_parent_id = collection_ids.get(parent)
        stored_parent_id = collection.get(HIERARCHY_PARENT_KEY, "")
        records.append({
            "id": collection_ids[collection],
            "name": collection.name,
            "kind": "FOLDER",
            # A Folder below a Studio Model cannot be represented directly by
            # Blender's Collection tree. Preserve that mixed parent explicitly.
            "parentId": stored_parent_id if stored_parent_id in empty_id_values else actual_parent_id,
        })
    for obj in sorted(empty_objects, key=lambda item: item.name.casefold()):
        parent_empty = obj.parent if obj.parent in empty_ids else None
        user_collections = sorted(
            (collection_ids[value] for value in obj.users_collection if value in collection_ids),
        )
        actual_parent_id = empty_ids.get(parent_empty)
        stored_parent_id = obj.get(HIERARCHY_PARENT_KEY, "")
        override = obj.rbx_primitive_sync.empty_export_mode
        studio_mode = (
            scene.rbx_primitive_sync.mesh_empty_export_mode
            if override == "INHERIT" else override
        )
        records.append({
            "id": empty_ids[obj],
            "name": obj.name,
            "kind": "MODEL",
            "studioMode": studio_mode,
            # A Model below a Studio Folder is represented through Collection
            # membership in Blender, so retain the original mixed parent ID.
            "parentId": stored_parent_id if stored_parent_id in collection_id_values else actual_parent_id,
            "primaryCollectionId": user_collections[0] if user_collections else None,
            "collectionIds": user_collections,
        })
    return records, collection_ids, empty_ids


def _instance_transform(obj, local_center, local_size, studs_per_unit):
    matrix = obj.matrix_world
    rows = tuple(tuple(matrix[row][column] for column in range(3)) for row in range(3))
    if matrix.to_3x3().determinant() <= 0:
        raise ValueError("negative or mirrored transforms are not supported")
    if has_shear(rows):
        raise ValueError("sheared transforms are not supported")
    _location, rotation, scale = matrix.decompose()
    if any(value <= 0 for value in scale):
        raise ValueError("zero or negative scale is not supported")
    world_center = matrix @ Vector(local_center)
    rotation_rows = tuple(tuple(rotation.to_matrix()[row][column] for column in range(3)) for row in range(3))
    size = convert_size(
        tuple(local_size[index] * float(scale[index]) for index in range(3)),
        studs_per_unit,
    )
    if any(value < 0.001 or value > 2048 for value in size):
        raise ValueError(f"resulting size {tuple(round(v, 4) for v in size)} is outside 0.001..2048 studs")
    position = convert_position(world_center, studs_per_unit)
    converted_rotation = convert_rotation(rotation_rows)
    cframe = [
        *position,
        *(converted_rotation[row][column] for row in range(3) for column in range(3)),
    ]
    return rounded(size), rounded(cframe)


def build_selection_document(context):
    selected_meshes = [obj for obj in context.selected_objects if obj.type == "MESH"]
    primitive_objects = [
        obj for obj in selected_meshes
        if obj.rbx_primitive_sync.is_roblox_part and obj.rbx_primitive_sync.sync_enabled
    ]
    mesh_objects = [
        obj for obj in selected_meshes
        if not obj.rbx_primitive_sync.is_roblox_part and obj.rbx_primitive_sync.mesh_sync_enabled
    ]
    selected = primitive_objects + mesh_objects
    if not selected:
        raise ValueError("Select at least one enabled Roblox Part or MeshPart object")
    seen_object_ids = set()
    for obj in sorted(selected, key=lambda item: item.as_pointer()):
        object_id = _ensure_object_guid(obj)
        if object_id in seen_object_ids:
            object_id = str(uuid.uuid4())
            obj[OBJECT_GUID_KEY] = object_id
            obj.rbx_primitive_sync.guid = object_id
        seen_object_ids.add(object_id)

    depsgraph = context.evaluated_depsgraph_get()
    settings = context.scene.rbx_primitive_sync
    image_records, image_blobs = {}, {}
    appearances = {}
    candidates = []
    evaluated_mesh_cache = {}

    for obj in sorted(mesh_objects, key=lambda item: (item.name.casefold(), item.as_pointer())):
        if obj.data.shape_keys is not None:
            raise ValueError(f"{obj.name}: Shape Keys are not supported")
        if any(modifier.type == "ARMATURE" and modifier.show_viewport for modifier in obj.modifiers):
            raise ValueError(f"{obj.name}: Armature modifiers are not supported")
        try:
            evaluation_key = _shareable_evaluation_key(obj)
            mesh_result = evaluated_mesh_cache.get(evaluation_key) if evaluation_key is not None else None
            if mesh_result is None:
                mesh_result = _collect_mesh(obj, depsgraph)
                if evaluation_key is not None:
                    evaluated_mesh_cache[evaluation_key] = mesh_result
            payload, digest, center, local_size, has_uv, has_colors = mesh_result
            appearance = _appearance_for(obj, has_uv, has_colors, image_records, image_blobs)
            appearances.setdefault(appearance["hash"], appearance)
            size, cframe = _instance_transform(obj, center, local_size, settings.studs_per_unit)
        except ValueError as error:
            raise ValueError(f"{obj.name}: {error}") from error
        candidates.append({
            "obj": obj,
            "payload": payload,
            "meshHash": digest,
            "appearanceHash": appearance["hash"],
            "size": size,
            "cframe": cframe,
        })

    buckets = {}
    for candidate in candidates:
        buckets.setdefault(candidate["meshHash"], []).append(candidate)
    mesh_records, mesh_blobs = [], {}
    mesh_group_ids = {}
    data_hashes = {}
    for candidate in candidates:
        data_hashes.setdefault(candidate["obj"].data.as_pointer(), set()).add(candidate["meshHash"])
    for digest, bucket in sorted(buckets.items()):
        existing = sorted(filter(None, (_valid_uuid(item["obj"].data.get(MESH_GUID_KEY, "")) for item in bucket)))
        has_shared_data_variants = any(len(data_hashes[item["obj"].data.as_pointer()]) > 1 for item in bucket)
        if has_shared_data_variants:
            group_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"roblox-mesh-sync:{digest}"))
        else:
            group_id = existing[0] if existing else str(uuid.uuid4())
            for item in bucket:
                item["obj"].data[MESH_GUID_KEY] = group_id
        payload = bucket[0]["payload"]
        raw = stable_json_bytes(payload)
        mesh_group_ids[digest] = group_id
        mesh_records.append({
            "hash": digest,
            "groupId": group_id,
            "name": bucket[0]["obj"].data.name,
            "vertexCount": len(payload["vertices"]),
            "triangleCount": len(payload["triangles"]),
            "byteSize": len(raw),
            "chunkCount": max(1, math.ceil(len(raw) / CHUNK_SIZE)),
        })
        source_data = bucket[0]["obj"].data
        source_uri = source_data.get("rbx_mesh_source_uri", "")
        if source_uri and source_data.get("rbx_mesh_source_forward_hash") == digest:
            mesh_records[-1]["sourceUri"] = source_uri
        mesh_blobs[digest] = raw

    collection_parents = _collection_parent_map(context.scene)
    document_scope = _selection_document_root(context.scene, selected, collection_parents)
    document_root = document_scope.get("collection") if document_scope else None
    hierarchy, collection_ids, empty_ids = _collect_hierarchy(
        context.scene,
        selected,
        parents=collection_parents,
        document_root=document_root,
    )
    instances = []
    for item in candidates:
        obj = item["obj"]
        object_id = _ensure_object_guid(obj)
        object_settings = obj.rbx_primitive_sync
        memberships = sorted(
            collection_ids[value] for value in obj.users_collection if value in collection_ids
        )
        instance = {
            "id": object_id,
            "sourceObjectId": object_id,
            "name": obj.name,
            "kind": "MESH",
            "meshHash": item["meshHash"],
            "meshGroupId": mesh_group_ids[item["meshHash"]],
            "appearanceHash": item["appearanceHash"],
            "size": item["size"],
            "cframe": item["cframe"],
            "collisionFidelity": object_settings.collision_fidelity,
            "anchored": bool(object_settings.anchored),
            "canCollide": bool(object_settings.can_collide),
            "canTouch": bool(object_settings.can_touch),
            "canQuery": bool(object_settings.can_query),
            "castShadow": bool(object_settings.cast_shadow),
            "parentId": empty_ids.get(obj.parent),
            "primaryCollectionId": memberships[0] if memberships else None,
            "collectionIds": memberships,
        }
        instance["stateHash"] = content_hash({
            key: value for key, value in instance.items() if key != "stateHash"
        })
        instances.append(instance)

    for obj in sorted(primitive_objects, key=lambda item: (item.name.casefold(), item.as_pointer())):
        _validate_primitive(obj)
        source_object_id = _ensure_object_guid(obj)
        memberships = sorted(
            collection_ids[value] for value in obj.users_collection if value in collection_ids
        )
        for record in serialize_object_parts(obj, settings.studs_per_unit):
            instance = {
                "id": record["id"],
                "sourceObjectId": source_object_id,
                "name": record["name"],
                "kind": "PART",
                "partType": record["type"],
                "logicalPartType": obj.rbx_primitive_sync.part_type,
                "size": record["size"],
                "cframe": record["cframe"],
                "material": record["material"],
                "color": record["color"],
                "transparency": record["transparency"],
                "anchored": record["anchored"],
                "canCollide": record["canCollide"],
                "canTouch": record["canTouch"],
                "canQuery": record["canQuery"],
                "castShadow": record["castShadow"],
                "parentId": empty_ids.get(obj.parent),
                "primaryCollectionId": memberships[0] if memberships else None,
                "collectionIds": memberships,
            }
            instance["stateHash"] = content_hash({
                key: value for key, value in instance.items() if key != "stateHash"
            })
            instances.append(instance)

    if document_scope is not None:
        model_id = document_scope["id"]
        model_name = document_scope["name"]
        root_kind = document_scope["rootKind"]
    else:
        model_id = context.scene.get("rbx_model_guid", "")
        if not _valid_uuid(model_id):
            model_id = str(uuid.uuid4())
            context.scene["rbx_model_guid"] = model_id
        model_name = _blend_model_name(context, settings.model_name)
        root_kind = "BLENDER_SCENE"
    document = {
        "schema": MESH_SCHEMA_ID,
        "generator": {"name": "Roblox Primitive Sync", "version": MESH_SYNC_VERSION},
        "model": {"id": model_id, "name": model_name, "rootKind": root_kind},
        "transformMask": {
            "position": bool(settings.mesh_sync_export_position),
            "rotation": bool(settings.mesh_sync_export_rotation),
            "scale": bool(settings.mesh_sync_export_scale),
        },
        "hierarchy": hierarchy,
        "meshes": mesh_records,
        "images": list(sorted(image_records.values(), key=lambda item: item["hash"])),
        "appearances": list(sorted(appearances.values(), key=lambda item: item["hash"])),
        "instances": instances,
    }
    validate_document_limits(document)
    return document, mesh_blobs, image_blobs


def ensure_server(scene):
    settings = scene.rbx_primitive_sync
    addon = bpy.context.preferences.addons.get(__package__)
    addon_preferences = getattr(addon, "preferences", None) if addon else None
    if addon_preferences is not None:
        if not addon_preferences.mesh_sync_token:
            addon_preferences.mesh_sync_token = settings.mesh_sync_token or secrets.token_hex(16)
        settings.mesh_sync_token = addon_preferences.mesh_sync_token
    elif not settings.mesh_sync_token:
        settings.mesh_sync_token = secrets.token_hex(16)
    SERVER.start(settings.mesh_sync_port, settings.mesh_sync_token)
    return settings


class RBX_OT_MeshSyncStart(Operator):
    bl_idname = "rbx_mesh_sync.start_server"
    bl_label = "Start Mesh Sync Server"
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            settings = ensure_server(context.scene)
        except OSError as error:
            self.report({"ERROR"}, f"ローカルサーバーを開始できません: {error}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"Mesh Sync server: 127.0.0.1:{settings.mesh_sync_port}")
        return {"FINISHED"}


class RBX_OT_MeshSyncStop(Operator):
    bl_idname = "rbx_mesh_sync.stop_server"
    bl_label = "Stop Mesh Sync Server"
    bl_options = {"REGISTER"}

    def execute(self, _context):
        SERVER.stop()
        self.report({"INFO"}, "Mesh Sync server stopped")
        return {"FINISHED"}


class RBX_OT_MeshSyncCopyConnection(Operator):
    bl_idname = "rbx_mesh_sync.copy_connection"
    bl_label = "Copy Connection Code"

    def execute(self, context):
        settings = ensure_server(context.scene)
        context.window_manager.clipboard = f"{settings.mesh_sync_port}|{settings.mesh_sync_token}"
        self.report({"INFO"}, "Studio接続コードをコピーしました")
        return {"FINISHED"}


class RBX_OT_MeshSyncAllowPairing(Operator):
    bl_idname = "rbx_mesh_sync.allow_pairing"
    bl_label = "Allow Studio Connection"
    bl_description = "Allow one local Roblox Studio plugin to pair automatically for 60 seconds"
    bl_options = {"REGISTER"}

    def execute(self, context):
        try:
            settings = ensure_server(context.scene)
            SERVER.enable_pairing(60)
        except OSError as error:
            self.report({"ERROR"}, f"ローカルサーバーを開始できません: {error}")
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Studio接続を60秒間許可しました: 127.0.0.1:{settings.mesh_sync_port}",
        )
        return {"FINISHED"}


class RBX_OT_MeshSyncSendSelected(Operator):
    bl_idname = "rbx_mesh_sync.send_selected"
    bl_label = "Send Selected to Studio"
    bl_description = "Send selected Roblox Parts and MeshParts to the connected Studio plugin"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        try:
            ensure_server(context.scene)
            document, mesh_blobs, image_blobs = build_selection_document(context)
            revision = SERVER.publish(document, mesh_blobs, image_blobs)
        except (ValueError, OSError, RuntimeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"Revision {revision}: {len(document['instances'])} Parts and MeshParts / "
            f"{len(document['meshes'])} meshes / {len(document['images'])} images",
        )
        return {"FINISHED"}


class RBX_OT_MeshSyncApplySettings(Operator):
    bl_idname = "rbx_mesh_sync.apply_settings"
    bl_label = "Apply Active Settings to Selected"
    bl_description = "Copy Mesh Sync appearance and physics settings from the active object"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return bool(context.active_object and context.active_object.type == "MESH" and len(context.selected_objects) > 1)

    def execute(self, context):
        source = context.active_object.rbx_primitive_sync
        fields = (
            "mesh_sync_enabled", "mesh_use_roblox_material", "mesh_blender_appearance_mode",
            "mesh_material_preview", "material", "color",
            "transparency", "collision_fidelity", "anchored", "can_collide",
            "can_touch", "can_query", "cast_shadow",
        )
        count = 0
        for obj in context.selected_objects:
            if obj.type != "MESH" or obj == context.active_object:
                continue
            target = obj.rbx_primitive_sync
            for field in fields:
                setattr(target, field, getattr(source, field))
            count += 1
        self.report({"INFO"}, f"{count}個へMesh Sync設定を適用しました")
        return {"FINISHED"}


class RBX_OT_MeshSyncLinkMeshData(Operator):
    bl_idname = "rbx_mesh_sync.link_mesh_data"
    bl_label = "Link Mesh Data to Active"
    bl_description = (
        "Link the selected Mesh objects to the active object's Mesh data "
        "(same as Ctrl+L > Link Object Data; geometry, UVs, vertex colors, and materials become shared)"
    )
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        active = context.active_object
        return bool(
            context.mode == "OBJECT"
            and active
            and active.type == "MESH"
            and any(obj.type == "MESH" and obj != active for obj in context.selected_objects)
        )

    def execute(self, context):
        source = context.active_object
        targets = [
            obj for obj in context.selected_objects
            if obj.type == "MESH" and obj != source
        ]
        for obj in targets:
            obj.data = source.data
        self.report(
            {"INFO"},
            f"{len(targets)}個をアクティブMesh「{source.name}」のデータへリンクしました",
        )
        return {"FINISHED"}


CLASSES = (
    RBX_OT_MeshSyncStart,
    RBX_OT_MeshSyncStop,
    RBX_OT_MeshSyncCopyConnection,
    RBX_OT_MeshSyncAllowPairing,
    RBX_OT_MeshSyncSendSelected,
    RBX_OT_MeshSyncApplySettings,
    RBX_OT_MeshSyncLinkMeshData,
)
