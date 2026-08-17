"""Apply Studio selections received by the local Mesh Sync server."""

from __future__ import annotations

import json

import bmesh
import bpy
from bpy.types import Operator
from mathutils import Matrix, Vector

from .core import reverse_position, reverse_rotation, reverse_size, rounded, srgb_color_to_linear
from .geometry import create_mesh, mesh_signature
from .material_preview import (
    apply_object_material_override, refresh_object_preview, restore_object_preview,
)
from .mesh_sync_core import (
    content_hash, hierarchy_parent_order, mesh_signature_payload, sha256_bytes,
    validate_reverse_document,
)
from .mesh_sync_server import SERVER


OBJECT_GUID_KEY = "rbx_mesh_object_guid"
HIERARCHY_GUID_KEY = "rbx_mesh_hierarchy_guid"
HIERARCHY_PARENT_KEY = "rbx_mesh_hierarchy_parent_guid"
COLLECTION_GUID_KEY = "rbx_mesh_collection_guid"
LAST_LOCAL_STATE_KEY = "rbx_mesh_last_local_state"
IMAGE_HASH_KEY = "rbx_mesh_image_hash"
IMAGE_SOURCE_URI_KEY = "rbx_mesh_source_uri"
IMAGE_SOURCE_HASH_KEY = "rbx_mesh_source_forward_hash"
MATERIAL_VARIANT_KEY = "rbx_mesh_material_variant"
APPEARANCE_METADATA_KEY = "rbx_mesh_appearance_metadata"
ROOT_KIND_KEY = "rbx_mesh_root_kind"
DOCUMENT_MODEL_GUID_KEY = "rbx_mesh_document_model_guid"
DOCUMENT_MODEL_NAME_KEY = "rbx_mesh_document_model_name"
DOCUMENT_ROOT_KIND_KEY = "rbx_mesh_document_root_kind"
GEOMETRY_WARNING_KEY = "rbx_mesh_geometry_warning"
APPEARANCE_AVAILABLE_KEY = "rbx_mesh_appearance_available"
APPEARANCE_WARNING_KEY = "rbx_mesh_appearance_warning"
_AUTO_APPLY_FAILED_REVISION = 0


def _axis_vector(value, studs_per_unit=1.0):
    return reverse_position(value, studs_per_unit)


def _existing_object(object_id):
    return next((obj for obj in bpy.data.objects if obj.get(OBJECT_GUID_KEY) == object_id), None)


def _local_state(obj):
    settings = obj.rbx_primitive_sync
    mesh = obj.data if obj.type == "MESH" else None
    geometry = None
    if mesh:
        geometry = {
            "vertices": [rounded(vertex.co) for vertex in mesh.vertices],
            "faces": [list(polygon.vertices) for polygon in mesh.polygons],
        }
    return content_hash({
        "matrix": [rounded(row) for row in obj.matrix_world],
        "geometry": geometry,
        "appearance": {
            "useRobloxMaterial": bool(settings.mesh_use_roblox_material),
            "mode": (
                "MATERIAL" if settings.mesh_use_roblox_material
                else settings.mesh_blender_appearance_mode
            ),
            "material": settings.material,
            "color": rounded(settings.color),
            "transparency": round(float(settings.transparency), 7),
        },
        "physics": {
            "anchored": bool(settings.anchored),
            "canCollide": bool(settings.can_collide),
            "canTouch": bool(settings.can_touch),
            "canQuery": bool(settings.can_query),
            "castShadow": bool(settings.cast_shadow),
        },
    })


def _is_conflict(obj):
    previous = obj.get(LAST_LOCAL_STATE_KEY, "")
    return bool(previous and previous != _local_state(obj))


def _image_for(record, raw):
    digest = record["hash"]
    existing = next((image for image in bpy.data.images if image.get(IMAGE_HASH_KEY) == digest), None)
    if existing:
        return existing
    width, height = int(record["width"]), int(record["height"])
    if len(raw) != width * height * 4:
        raise ValueError(f"{record.get('name', digest)} has invalid RGBA data")
    image = bpy.data.images.new(
        f"RPS {record.get('role', 'Image')} {digest[:10]}", width=width, height=height, alpha=True,
    )
    values = [0.0] * len(raw)
    # Roblox EditableImage uses a top-left origin; Blender image pixels start at
    # the bottom-left.  Reversing rows makes a round-trip lossless.
    color_map = record.get("role") in {"baseColor", "emissive"}

    def component(value):
        value = value / 255.0
        if not color_map:
            return value
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    for output_y in range(height):
        source_y = height - 1 - output_y
        source = source_y * width * 4
        target = output_y * width * 4
        for pixel in range(width):
            source_pixel = source + pixel * 4
            target_pixel = target + pixel * 4
            values[target_pixel] = component(raw[source_pixel])
            values[target_pixel + 1] = component(raw[source_pixel + 1])
            values[target_pixel + 2] = component(raw[source_pixel + 2])
            values[target_pixel + 3] = raw[source_pixel + 3] / 255.0
    image.pixels.foreach_set(values)
    image.update()
    image[IMAGE_HASH_KEY] = digest
    if record.get("sourceUri"):
        image[IMAGE_SOURCE_URI_KEY] = record["sourceUri"]
        image[IMAGE_SOURCE_HASH_KEY] = sha256_bytes(
            width.to_bytes(4, "little") + height.to_bytes(4, "little") + raw
        )
    try:
        image.pack()
    except RuntimeError:
        pass
    return image


def _set_socket(node, name, value):
    socket = node.inputs.get(name)
    if socket is not None:
        socket.default_value = value


def _pbr_material(appearance, image_by_hash):
    digest = appearance.get("hash") or content_hash(appearance)
    name = f"RPS PBR sRGB {digest[:12]}"
    existing = bpy.data.materials.get(name)
    if existing:
        return existing
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    shader = tree.nodes.new("ShaderNodeBsdfPrincipled")
    color = srgb_color_to_linear(appearance.get("color", [1.0, 1.0, 1.0]))
    _set_socket(shader, "Base Color", (*color, 1.0))
    _set_socket(shader, "Alpha", 1.0 - float(appearance.get("transparency", 0.0)))
    tree.links.new(shader.outputs["BSDF"], output.inputs["Surface"])

    maps = appearance.get("maps", {})
    for role in ("baseColor", "roughness", "metallic", "normal", "emissive"):
        image = image_by_hash.get(maps.get(role))
        if image is None:
            continue
        texture = tree.nodes.new("ShaderNodeTexImage")
        texture.image = image
        if role != "baseColor":
            try:
                image.colorspace_settings.name = "Non-Color"
            except TypeError:
                pass
        if role == "baseColor":
            mix = tree.nodes.new("ShaderNodeMixRGB")
            mix.blend_type = "MULTIPLY"
            mix.inputs[0].default_value = 1.0
            mix.inputs[2].default_value = (*color, 1.0)
            tree.links.new(texture.outputs["Color"], mix.inputs[1])
            tree.links.new(mix.outputs["Color"], shader.inputs["Base Color"])
            if texture.outputs.get("Alpha") and shader.inputs.get("Alpha"):
                tree.links.new(texture.outputs["Alpha"], shader.inputs["Alpha"])
        elif role == "roughness":
            tree.links.new(texture.outputs["Color"], shader.inputs["Roughness"])
        elif role == "metallic":
            tree.links.new(texture.outputs["Color"], shader.inputs["Metallic"])
        elif role == "normal":
            normal = tree.nodes.new("ShaderNodeNormalMap")
            normal.space = "TANGENT"
            tree.links.new(texture.outputs["Color"], normal.inputs["Color"])
            tree.links.new(normal.outputs["Normal"], shader.inputs["Normal"])
        elif role == "emissive":
            tree.links.new(texture.outputs["Color"], shader.inputs["Emission Color"])
            _set_socket(shader, "Emission Strength", float(appearance.get("emissiveStrength", 1.0)))
    try:
        material.surface_render_method = "DITHERED"
    except (AttributeError, TypeError):
        if hasattr(material, "blend_method"):
            material.blend_method = "BLEND"
    return material


def _merged_payload_geometry(payload, studs_per_unit, merge_distance):
    vertices = [_axis_vector(value, studs_per_unit) for value in payload["vertices"]]
    triangles = [tuple(int(index) for index in value) for value in payload["triangles"]]
    normals = list(payload.get("cornerNormals", []))
    uvs = list(payload.get("cornerUvs", []))
    colors = list(payload.get("cornerColors", []))
    if merge_distance <= 0.0:
        return vertices, triangles, normals, uvs, colors

    distance = float(merge_distance)
    buckets, remap, merged = {}, {}, []
    for index, vertex in enumerate(vertices):
        cell = tuple(int(round(float(component) / distance)) for component in vertex)
        target = None
        for x in range(cell[0] - 1, cell[0] + 2):
            for y in range(cell[1] - 1, cell[1] + 2):
                for z in range(cell[2] - 1, cell[2] + 2):
                    for candidate in buckets.get((x, y, z), ()):
                        other = merged[candidate]
                        if sum((float(vertex[axis]) - float(other[axis])) ** 2 for axis in range(3)) <= distance ** 2:
                            target = candidate
                            break
                    if target is not None:
                        break
                if target is not None:
                    break
            if target is not None:
                break
        if target is None:
            target = len(merged)
            merged.append(vertex)
            buckets.setdefault(cell, []).append(target)
        remap[index] = target

    kept_triangles, kept_normals, kept_uvs, kept_colors = [], [], [], []
    seen = set()
    for face_index, triangle in enumerate(triangles):
        remapped = tuple(remap[index] for index in triangle)
        key = tuple(sorted(remapped))
        if len(set(remapped)) < 3 or key in seen:
            continue
        seen.add(key)
        kept_triangles.append(remapped)
        if face_index < len(normals):
            kept_normals.append(normals[face_index])
        if face_index < len(uvs):
            kept_uvs.append(uvs[face_index])
        if face_index < len(colors):
            kept_colors.append(colors[face_index])
    return merged, kept_triangles, kept_normals, kept_uvs, kept_colors


def _mark_corner_boundaries(mesh, triangles, normals, uvs, colors):
    signatures = {}
    for face_index, triangle in enumerate(triangles):
        for corner in range(3):
            first = triangle[corner]
            second = triangle[(corner + 1) % 3]
            values = []
            for attribute in (normals, uvs, colors):
                if face_index < len(attribute):
                    face = attribute[face_index]
                    values.append((tuple(face[corner]), tuple(face[(corner + 1) % 3])))
                else:
                    values.append(None)
            key = tuple(sorted((first, second)))
            oriented = tuple(values) if first <= second else tuple(
                None if value is None else (value[1], value[0]) for value in values
            )
            signatures.setdefault(key, []).append(oriented)
    edge_by_vertices = {tuple(sorted(edge.vertices)): edge for edge in mesh.edges}
    for key, values in signatures.items():
        if len(values) > 1 and any(value != values[0] for value in values[1:]):
            edge = edge_by_vertices.get(key)
            if edge is not None:
                edge.use_seam = True
                edge.use_edge_sharp = True


def _reconstruct_topology(mesh, mode):
    if mode == "TRIANGLES":
        return
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.faces.ensure_lookup_table()
        if mode == "QUADS":
            bmesh.ops.join_triangles(
                bm,
                faces=list(bm.faces),
                cmp_seam=True,
                cmp_sharp=True,
                # Corner attribute discontinuities were promoted to seam/sharp
                # edges before entering BMesh. Comparing per-loop UV/color data
                # here rejects even compatible triangle pairs in Blender 4.5.
                cmp_uvs=False,
                cmp_vcols=False,
                cmp_materials=True,
                angle_face_threshold=0.0017453292519943296,
                angle_shape_threshold=0.6981317007977318,
            )
        elif mode == "NGONS":
            bmesh.ops.dissolve_limit(
                bm,
                angle_limit=0.0017453292519943296,
                use_dissolve_boundaries=False,
                verts=list(bm.verts),
                edges=list(bm.edges),
                # Split-normal discontinuities are already represented as sharp
                # edges. NORMAL delimiting treats every imported flat triangle
                # as separate and prevents otherwise coplanar dissolution.
                delimit={"MATERIAL", "SEAM", "SHARP", "UV"},
            )
        else:
            raise ValueError(f"Unsupported reverse mesh topology mode: {mode}")
        bm.to_mesh(mesh)
        mesh.update(calc_edges=True)
    finally:
        bm.free()


def _mesh_from_payload(
    name, payload, studs_per_unit, source_uri="", *, topology_mode="TRIANGLES", merge_distance=0.0,
):
    vertices, triangles, normals, uvs, colors = _merged_payload_geometry(
        payload, studs_per_unit, merge_distance,
    )
    mesh = bpy.data.meshes.new(name)
    if source_uri:
        mesh["rbx_mesh_source_uri"] = source_uri
        source_vertices = payload["vertices"]
        minimum = [min(float(vertex[axis]) for vertex in source_vertices) for axis in range(3)]
        maximum = [max(float(vertex[axis]) for vertex in source_vertices) for axis in range(3)]
        center = [(minimum[axis] + maximum[axis]) * 0.5 for axis in range(3)]
        normalized = {
            "vertices": [
                rounded((
                    (float(vertex[0]) - center[0]) / studs_per_unit,
                    (float(vertex[1]) - center[1]) / studs_per_unit,
                    (float(vertex[2]) - center[2]) / studs_per_unit,
                ))
                for vertex in source_vertices
            ],
            "triangles": [[int(value) for value in face] for face in payload["triangles"]],
            "cornerNormals": [
                [rounded(value) for value in face]
                for face in payload.get("cornerNormals", [])
            ],
            "cornerUvs": [
                [[round(float(value[0]), 7), round(float(value[1]), 7)] for value in face]
                for face in payload.get("cornerUvs", [])
            ],
            "cornerColors": [
                [rounded(value) for value in face]
                for face in payload.get("cornerColors", [])
            ],
        }
        mesh["rbx_mesh_source_forward_hash"] = content_hash(mesh_signature_payload(normalized))
    mesh.from_pydata(vertices, [], triangles)
    mesh.update(calc_edges=True)

    if uvs:
        layer = mesh.uv_layers.new(name="UVMap")
        for polygon_index, polygon in enumerate(mesh.polygons):
            if polygon_index >= len(uvs):
                break
            for corner, loop_index in enumerate(polygon.loop_indices):
                uv = uvs[polygon_index][corner]
                layer.data[loop_index].uv = (float(uv[0]), 1.0 - float(uv[1]))

    if colors:
        attribute = mesh.color_attributes.new(name="Color", type="FLOAT_COLOR", domain="CORNER")
        for polygon_index, polygon in enumerate(mesh.polygons):
            if polygon_index >= len(colors):
                break
            for corner, loop_index in enumerate(polygon.loop_indices):
                value = colors[polygon_index][corner]
                data = attribute.data[loop_index]
                rgba = tuple(float(component) for component in value[:4])
                if hasattr(data, "color_srgb"):
                    data.color_srgb = rgba
                else:
                    data.color = (*srgb_color_to_linear(rgba), rgba[3] if len(rgba) > 3 else 1.0)

    _mark_corner_boundaries(mesh, triangles, normals, uvs, colors)
    _reconstruct_topology(mesh, topology_mode)
    has_complete_normals = (
        topology_mode == "TRIANGLES"
        and len(normals) == len(mesh.polygons)
        and all(
            isinstance(face, (list, tuple)) and len(face) == len(polygon.loop_indices)
            for face, polygon in zip(normals, mesh.polygons)
        )
    )
    if has_complete_normals and hasattr(mesh, "normals_split_custom_set"):
        converted = []
        for polygon_index, polygon in enumerate(mesh.polygons):
            polygon.use_smooth = True
            for corner, _loop_index in enumerate(polygon.loop_indices):
                converted.append(_axis_vector(normals[polygon_index][corner], 1.0))
        try:
            # Custom corner normals are ignored by Blender on flat-shaded
            # polygons. Imported Roblox meshes are triangulated, so leaving the
            # default flat flag produced dense black/faceted stripes even though
            # Studio supplied valid split normals. Smooth polygons still retain
            # hard edges because each corner keeps its own received normal.
            mesh.normals_split_custom_set(converted)
            mesh.update()
        except (IndexError, RuntimeError, TypeError, ValueError):
            for polygon in mesh.polygons:
                polygon.use_smooth = False
    elif topology_mode != "TRIANGLES":
        for polygon in mesh.polygons:
            polygon.use_smooth = True
    return mesh


def _matrix_from_cframe(cframe, studs_per_unit):
    location = reverse_position(cframe[:3], studs_per_unit)
    roblox_rotation = tuple(tuple(float(cframe[3 + row * 3 + column]) for column in range(3)) for row in range(3))
    blender_rotation = reverse_rotation(roblox_rotation)
    matrix = Matrix.Identity(4)
    for row in range(3):
        for column in range(3):
            matrix[row][column] = blender_rotation[row][column]
    matrix.translation = location
    return matrix


def _mesh_local_dimensions(mesh):
    """Return mesh-space bounds without object rotation affecting the result."""

    if not mesh.vertices:
        return (1.0, 1.0, 1.0)
    minimum = [float("inf")] * 3
    maximum = [float("-inf")] * 3
    for vertex in mesh.vertices:
        for axis in range(3):
            value = float(vertex.co[axis])
            minimum[axis] = min(minimum[axis], value)
            maximum[axis] = max(maximum[axis], value)
    return tuple(max(1e-9, maximum[axis] - minimum[axis]) for axis in range(3))


def _native_local_dimensions(record, mesh, studs_per_unit):
    """Use Roblox's native MeshPart bounds when Studio supplied them."""

    mesh_size = record.get("meshSize") if record.get("kind") == "MESH" else None
    if isinstance(mesh_size, (list, tuple)) and len(mesh_size) == 3:
        converted = reverse_size(mesh_size, studs_per_unit)
        if all(float(value) > 1e-9 for value in converted):
            return tuple(float(value) for value in converted)
    return _mesh_local_dimensions(mesh)


def _apply_settings(obj, record, appearance, image_by_hash):
    settings = obj.rbx_primitive_sync
    settings.mesh_sync_enabled = True
    settings.guid = record["id"]
    if record.get("kind") == "PART":
        settings.part_type = record.get("partType", "Block")
        settings.sync_enabled = True
        settings.is_roblox_part = True
        obj["rbx_mesh_signature"] = mesh_signature(obj.data)
    else:
        settings.is_roblox_part = False
    settings.anchored = record.get("anchored", True)
    settings.can_collide = record.get("canCollide", True)
    settings.can_touch = record.get("canTouch", True)
    settings.can_query = record.get("canQuery", True)
    settings.cast_shadow = record.get("castShadow", True)
    settings.collision_fidelity = record.get("collisionFidelity", "Hull")
    settings.transparency = float(appearance.get("transparency", record.get("transparency", 0.0)))
    color = appearance.get("color", record.get("color", [1.0, 1.0, 1.0]))
    settings.color = srgb_color_to_linear(color[:3])
    settings.material = appearance.get("material", record.get("material", "Plastic"))
    mode = appearance.get("mode", "MATERIAL")
    has_maps = bool(appearance.get("maps"))
    settings.mesh_use_roblox_material = not has_maps and mode == "MATERIAL"
    settings.mesh_blender_appearance_mode = (
        "TEXTURE" if has_maps else ("AUTO" if mode == "MATERIAL" else mode)
    )
    if appearance.get("materialVariant"):
        obj[MATERIAL_VARIANT_KEY] = json.dumps(appearance["materialVariant"], sort_keys=True)
    else:
        obj.pop(MATERIAL_VARIANT_KEY, None)
    metadata = {
        key: appearance[key]
        for key in ("alphaMode", "emissiveStrength", "emissiveTint")
        if key in appearance
    }
    if metadata:
        obj[APPEARANCE_METADATA_KEY] = json.dumps(metadata, sort_keys=True)
    else:
        obj.pop(APPEARANCE_METADATA_KEY, None)
    appearance_available = record.get("appearanceAvailable", True) is not False
    obj[APPEARANCE_AVAILABLE_KEY] = appearance_available
    if appearance_available:
        obj.pop(APPEARANCE_WARNING_KEY, None)
    else:
        obj[APPEARANCE_WARNING_KEY] = str(
            record.get("appearanceError", "Studio appearance images were not readable")
        )
        # Keep the current Blender material assignment intact. The accessible
        # Material/Color/Transparency values above still update the sync settings,
        # but an unreadable Studio texture must not destroy a local material.
        return
    restore_object_preview(obj)
    if appearance.get("maps"):
        material = _pbr_material(appearance, image_by_hash)
        apply_object_material_override(obj, material)
    else:
        refresh_object_preview(obj)


def _create_object(
    record, mesh_payloads, mesh_sources, mesh_data_cache,
    appearances, image_by_hash, studs_per_unit, *, topology_mode="TRIANGLES", merge_distance=0.0,
):
    kind = record.get("kind")
    if kind == "PART":
        part_type = record.get("partType", "Block")
        if part_type not in {"Block", "Ball", "Cylinder", "Wedge", "CornerWedge"}:
            raise ValueError(f"Unsupported Studio Part shape: {part_type}")
        cache_key = f"part:{part_type}"
        mesh = mesh_data_cache.get(cache_key)
        if mesh is None:
            mesh = create_mesh(part_type)
            mesh_data_cache[cache_key] = mesh
    elif kind == "MESH":
        mesh_hash = record.get("meshHash")
        payload = mesh_payloads.get(mesh_hash)
        if payload is None:
            raise ValueError(f"{record.get('name', 'MeshPart')}: missing mesh payload")
        mesh = mesh_data_cache.get(mesh_hash)
        if mesh is None:
            mesh = _mesh_from_payload(
                record.get("name", "StudioMesh"), payload, studs_per_unit,
                mesh_sources.get(mesh_hash, ""),
                topology_mode=topology_mode,
                merge_distance=merge_distance,
            )
            mesh_data_cache[mesh_hash] = mesh
    else:
        raise ValueError(f"Unsupported Studio object kind: {kind}")

    obj = bpy.data.objects.new(record.get("name", "StudioObject"), mesh)
    obj[OBJECT_GUID_KEY] = record["id"]
    obj.matrix_world = _matrix_from_cframe(record["cframe"], studs_per_unit)
    desired = reverse_size(record["size"], studs_per_unit)
    # Object.dimensions is a world-axis-aligned bounding box.  Once a Part is
    # rotated it no longer represents the local X/Y/Z size, which used to make
    # diagonal bars and latches grow unpredictably.  Scale against the native
    # mesh-space bounds instead (EditableMesh:GetSize for MeshParts).
    local = _native_local_dimensions(record, mesh, studs_per_unit)
    obj.scale = tuple(float(obj.scale[index]) * desired[index] / local[index] for index in range(3))
    appearance = appearances.get(record.get("appearanceHash"), {
        "mode": "MATERIAL",
        "material": record.get("material", "Plastic"),
        "color": record.get("color", [1.0, 1.0, 1.0]),
        "transparency": record.get("transparency", 0.0),
    })
    _apply_settings(obj, record, appearance, image_by_hash)
    return obj


def _preserve_disabled_transform_channels(obj, old, transform_mask):
    """Keep unchecked channels on updates; new objects always get an initial transform."""

    if old is None:
        return
    use_position = transform_mask.get("position", True) is not False
    use_rotation = transform_mask.get("rotation", True) is not False
    use_scale = transform_mask.get("scale", True) is not False
    new_location, new_rotation, new_scale = obj.matrix_world.decompose()
    old_location, old_rotation, old_scale = old.matrix_world.decompose()
    obj.matrix_world = Matrix.LocRotScale(
        new_location if use_position else old_location,
        new_rotation if use_rotation else old_rotation,
        new_scale if use_scale else old_scale,
    )


def _evaluated_local_bounds(obj, depsgraph):
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        if not mesh.vertices:
            raise ValueError(f"{obj.name}: evaluated mesh has no vertices")
        minimum = [min(float(vertex.co[axis]) for vertex in mesh.vertices) for axis in range(3)]
        maximum = [max(float(vertex.co[axis]) for vertex in mesh.vertices) for axis in range(3)]
        center = Vector((minimum[axis] + maximum[axis]) * 0.5 for axis in range(3))
        size = tuple(maximum[axis] - minimum[axis] for axis in range(3))
        if any(value <= 1e-9 for value in size):
            raise ValueError(f"{obj.name}: evaluated mesh has a zero-size axis")
        return center, size
    finally:
        evaluated.to_mesh_clear()


def _update_preserved_object(
    obj, record, appearance, image_by_hash, transform_mask, studs_per_unit, local_center, local_size,
):
    incoming_matrix = _matrix_from_cframe(record["cframe"], studs_per_unit)
    _incoming_location, incoming_rotation, _incoming_scale = incoming_matrix.decompose()
    _current_location, current_rotation, current_scale = obj.matrix_world.decompose()
    use_position = transform_mask.get("position", True) is not False
    use_rotation = transform_mask.get("rotation", True) is not False
    use_scale = transform_mask.get("scale", True) is not False

    rotation = incoming_rotation if use_rotation else current_rotation
    if use_scale:
        desired_size = reverse_size(record["size"], studs_per_unit)
        scale = Vector(desired_size[index] / local_size[index] for index in range(3))
    else:
        scale = Vector(current_scale)
    current_center = obj.matrix_world @ local_center
    desired_center = incoming_matrix.translation if use_position else current_center
    scaled_center = Vector(scale[index] * local_center[index] for index in range(3))
    origin = desired_center - (rotation.to_matrix() @ scaled_center)
    obj.matrix_world = Matrix.LocRotScale(origin, rotation, scale)
    obj[OBJECT_GUID_KEY] = record["id"]
    _apply_settings(obj, record, appearance, image_by_hash)
    if record.get("geometryChanged"):
        obj[GEOMETRY_WARNING_KEY] = "Studio MeshId changed; Blender geometry was preserved"
    else:
        obj.pop(GEOMETRY_WARNING_KEY, None)


def _ensure_collection(node):
    collection = next(
        (value for value in bpy.data.collections if value.get(COLLECTION_GUID_KEY) == node["id"]), None,
    )
    if collection is None:
        collection = bpy.data.collections.new(node["name"])
        collection[COLLECTION_GUID_KEY] = node["id"]
    collection.name = node["name"]
    collection[HIERARCHY_PARENT_KEY] = node.get("parentId", "")
    return collection


def _nearest_ancestor(nodes, node, kind):
    parent_id = node.get("parentId")
    while parent_id:
        parent = nodes[parent_id]
        if parent.get("kind") == kind:
            return parent_id
        parent_id = parent.get("parentId")
    return None


def _set_parent_keep_world(obj, parent):
    # Newly created reverse-sync objects are linked and parented in the same
    # dependency-graph update.  In that window ``matrix_world`` can still hold
    # the pre-scale matrix even though ``obj.scale`` / ``matrix_basis`` already
    # contain the received Part.Size.  Reading it here used to reset every Part
    # below a Studio Model to a 2x2x2 cube.  Recompose from the current local
    # transform instead of trusting the possibly stale evaluated matrix.
    if obj.parent is None:
        world = obj.matrix_basis.copy()
    else:
        world = obj.parent.matrix_world @ obj.matrix_parent_inverse @ obj.matrix_basis
    obj.parent = parent
    obj.matrix_world = world


def _required_hierarchy_ids(document, staged, preserve_existing):
    if not preserve_existing:
        return {node["id"] for node in document.get("hierarchy", [])}
    required = set()
    for staged_item in staged:
        record, old = staged_item[0], staged_item[2]
        if old is not None:
            continue
        required.update(filter(None, (
            record.get("parentId"), record.get("primaryCollectionId"),
        )))
        required.update(filter(None, record.get("collectionIds", [])))
    by_id = {node["id"]: node for node in document.get("hierarchy", [])}
    pending = list(required)
    while pending:
        node = by_id.get(pending.pop())
        if not node:
            continue
        for ancestor_id in (
            node.get("parentId"), node.get("primaryCollectionId"),
            *node.get("collectionIds", []),
        ):
            if ancestor_id and ancestor_id not in required:
                required.add(ancestor_id)
                pending.append(ancestor_id)
    return required


def _migrate_legacy_studio_roots(context):
    """Remove generated Studio Selection wrappers without losing their contents."""

    root = context.scene.collection
    legacy_roots = [
        collection for collection in bpy.data.collections
        if collection != root
        and isinstance(collection.get("rbx_model_guid", ""), str)
        and bool(collection.get("rbx_model_guid", ""))
        and collection.get(ROOT_KIND_KEY, "STUDIO_SELECTION") == "STUDIO_SELECTION"
    ]
    for legacy in legacy_roots:
        model_id = legacy.get("rbx_model_guid", "")
        for obj in tuple(legacy.all_objects):
            obj[DOCUMENT_MODEL_GUID_KEY] = model_id
            obj[DOCUMENT_MODEL_NAME_KEY] = legacy.name
            obj[DOCUMENT_ROOT_KIND_KEY] = "STUDIO_SELECTION"
        for child in tuple(legacy.children):
            if child.name not in root.children:
                root.children.link(child)
            legacy.children.unlink(child)
        for obj in tuple(legacy.objects):
            if len(obj.users_collection) == 1 and root not in obj.users_collection:
                root.objects.link(obj)
            legacy.objects.unlink(obj)
        for possible_parent in (root, *tuple(bpy.data.collections)):
            if legacy.name in possible_parent.children:
                possible_parent.children.unlink(legacy)
        if not legacy.objects and not legacy.children:
            bpy.data.collections.remove(legacy)
    return root


def _ensure_hierarchy(
    context, document, *, preserve_existing=False, required_ids=None, needs_root=True,
):
    model = document.get("model", {})
    model_id = model.get("id", "studio-selection")
    root_kind = model.get("rootKind", "STUDIO_SELECTION")
    scene_model_id = context.scene.get("rbx_model_guid", "")
    if root_kind == "STUDIO_SELECTION":
        root = _migrate_legacy_studio_roots(context)
    elif root_kind == "BLENDER_SCENE" and scene_model_id == model_id:
        root = context.scene.collection
    else:
        root = next(
            (value for value in bpy.data.collections if value.get("rbx_model_guid") == model_id),
            None,
        )
        if root is None:
            if preserve_existing and not required_ids and not needs_root:
                root = context.scene.collection
            else:
                root = bpy.data.collections.new(model.get("name", "Studio Selection"))
                root["rbx_model_guid"] = model_id
                root[ROOT_KIND_KEY] = root_kind
        if root != context.scene.collection and root.name not in context.scene.collection.children:
            context.scene.collection.children.link(root)

    ordered = hierarchy_parent_order(document.get("hierarchy", []))
    nodes = {node["id"]: node for node in ordered}
    required_ids = set(required_ids or ())
    collections = {}
    new_collections = set()
    for node in ordered:
        if node.get("kind") != "FOLDER":
            continue
        collection = next(
            (value for value in bpy.data.collections if value.get(COLLECTION_GUID_KEY) == node["id"]),
            None,
        )
        if collection is None:
            if preserve_existing and node["id"] not in required_ids:
                continue
            collection = _ensure_collection(node)
            new_collections.add(collection)
        elif not preserve_existing:
            collection.name = node["name"]
            collection[HIERARCHY_PARENT_KEY] = node.get("parentId", "")
        collections[node["id"]] = collection

    # Existing Blender Collection edges are authoritative in preserve mode.
    synced_collections = set(collections.values())
    if not preserve_existing:
        for possible_parent in (context.scene.collection, *tuple(bpy.data.collections)):
            for child in tuple(possible_parent.children):
                if child in synced_collections:
                    possible_parent.children.unlink(child)
    for node in ordered:
        if node.get("kind") != "FOLDER":
            continue
        collection = collections.get(node["id"])
        if collection is None or (preserve_existing and collection not in new_collections):
            continue
        folder_parent_id = _nearest_ancestor(nodes, node, "FOLDER")
        parent = collections.get(folder_parent_id, root)
        if collection.name not in parent.children:
            parent.children.link(collection)

    empties = {}
    new_empties = set()
    for node in ordered:
        if node.get("kind") != "MODEL":
            continue
        empty = next((obj for obj in bpy.data.objects if obj.get(HIERARCHY_GUID_KEY) == node["id"]), None)
        if empty is None:
            if preserve_existing and node["id"] not in required_ids:
                continue
            empty = bpy.data.objects.new(node["name"], None)
            empty.empty_display_type = "PLAIN_AXES"
            empty[HIERARCHY_GUID_KEY] = node["id"]
            new_empties.add(empty)
        if not preserve_existing or empty in new_empties:
            empty.name = node["name"]
            empty[HIERARCHY_PARENT_KEY] = node.get("parentId", "")
            target = collections.get(node.get("primaryCollectionId"), root)
            for collection in tuple(empty.users_collection):
                if collection != target and (collection == root or collection in synced_collections):
                    collection.objects.unlink(empty)
            if target not in empty.users_collection:
                target.objects.link(empty)
        if node.get("cframe"):
            empty.matrix_world = _matrix_from_cframe(node["cframe"], context.scene.rbx_primitive_sync.studs_per_unit)
        empties[node["id"]] = empty
    for node_id, empty in empties.items():
        if preserve_existing and empty not in new_empties:
            continue
        model_parent_id = _nearest_ancestor(nodes, nodes[node_id], "MODEL")
        _set_parent_keep_world(empty, empties.get(model_parent_id))
    return root, collections, empties


def review_pending(context):
    pending = SERVER.pending_reverse
    if pending is None:
        raise ValueError("Studioからの保留中データはありません")
    settings = context.scene.rbx_primitive_sync
    settings.reverse_pending_revision = pending.revision
    settings.reverse_conflicts.clear()
    for record in pending.document.get("objects", []):
        existing = _existing_object(record["id"])
        if existing and _is_conflict(existing):
            item = settings.reverse_conflicts.add()
            item.object_id = record["id"]
            item.object_name = existing.name
            item.resolution = "KEEP_BLENDER"
    return len(pending.document.get("objects", [])), len(settings.reverse_conflicts)


def apply_pending(context):
    pending = SERVER.pending_reverse
    settings = context.scene.rbx_primitive_sync
    if pending is None or settings.reverse_pending_revision != pending.revision:
        raise ValueError("先にReview Incomingを実行してください")
    validate_reverse_document(pending.document)
    resolutions = {item.object_id: item.resolution for item in settings.reverse_conflicts}
    mesh_payloads = {
        record["hash"]: json.loads(pending.blobs[("mesh", record["hash"])].decode("utf-8"))
        for record in pending.document.get("meshes", [])
    }
    mesh_sources = {
        record["hash"]: record.get("sourceUri", "")
        for record in pending.document.get("meshes", [])
    }
    image_by_hash = {
        record["hash"]: _image_for(record, pending.blobs[("image", record["hash"])])
        for record in pending.document.get("images", [])
    }
    appearances = {item["hash"]: item for item in pending.document.get("appearances", [])}
    transform_mask = pending.document.get("transformMask", {})
    staged = []
    mesh_data_cache = {}
    warnings = []
    root_kind = pending.document.get("model", {}).get("rootKind", "STUDIO_SELECTION")
    preserve_geometry = bool(settings.reverse_preserve_geometry)
    depsgraph = context.evaluated_depsgraph_get()
    merge_distance = (
        float(settings.reverse_merge_distance)
        if settings.reverse_merge_by_distance else 0.0
    )
    try:
        for record in pending.document.get("objects", []):
            if resolutions.get(record["id"]) == "KEEP_BLENDER":
                continue
            old = _existing_object(record["id"])
            geometry_owner = record.get("geometryOwner")
            if geometry_owner is None:
                geometry_owner = "BLENDER" if root_kind == "BLENDER_SCENE" else "STUDIO"
            keep_source = bool(
                preserve_geometry
                and geometry_owner == "BLENDER"
                and root_kind == "BLENDER_SCENE"
                and old is not None
                and old.type == "MESH"
            )
            if keep_source:
                local_center, local_size = _evaluated_local_bounds(old, depsgraph)
                if record.get("geometryChanged"):
                    warnings.append(f"{record.get('name', old.name)}: Studio MeshId change ignored")
                staged.append((record, old, old, (local_center, local_size)))
                continue
            if record.get("kind") == "MESH" and not record.get("geometryAvailable", True):
                warnings.append(
                    f"{record.get('name', 'MeshPart')}: geometry unavailable; object was not replaced"
                )
                continue
            replacement = _create_object(
                record, mesh_payloads, mesh_sources, mesh_data_cache,
                appearances, image_by_hash, settings.studs_per_unit,
                topology_mode=settings.reverse_mesh_topology,
                merge_distance=merge_distance,
            )
            _preserve_disabled_transform_channels(replacement, old, transform_mask)
            staged.append((record, replacement, old, None))
    except Exception:
        for _record, obj, old, preserved_bounds in staged:
            if preserved_bounds is None and obj != old:
                bpy.data.objects.remove(obj, do_unlink=True)
        for mesh in set(mesh_data_cache.values()):
            if mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        raise

    preserve_hierarchy = bool(settings.reverse_preserve_hierarchy)
    required_ids = _required_hierarchy_ids(pending.document, staged, preserve_hierarchy)
    needs_root = not preserve_hierarchy or any(item[2] is None for item in staged)
    root, collections, empties = _ensure_hierarchy(
        context,
        pending.document,
        preserve_existing=preserve_hierarchy,
        required_ids=required_ids,
        needs_root=needs_root,
    )
    document_model = pending.document.get("model", {})
    for record, obj, old, preserved_bounds in staged:
        if preserved_bounds is not None:
            appearance = appearances.get(record.get("appearanceHash"), {
                "mode": "MATERIAL",
                "material": record.get("material", "Plastic"),
                "color": record.get("color", [1.0, 1.0, 1.0]),
                "transparency": record.get("transparency", 0.0),
            })
            _update_preserved_object(
                obj,
                record,
                appearance,
                image_by_hash,
                transform_mask,
                settings.studs_per_unit,
                *preserved_bounds,
            )
        if preserve_hierarchy and old is not None:
            if obj != old:
                targets = tuple(old.users_collection) or (context.scene.collection,)
                for target in targets:
                    if target not in obj.users_collection:
                        target.objects.link(obj)
                _set_parent_keep_world(obj, old.parent)
        else:
            target = collections.get(record.get("primaryCollectionId"), root)
            requested_targets = {target}
            for collection_id in record.get("collectionIds", []):
                collection = collections.get(collection_id)
                if collection:
                    requested_targets.add(collection)
            for collection in requested_targets:
                if collection not in obj.users_collection:
                    collection.objects.link(obj)
            for collection in tuple(obj.users_collection):
                if collection not in requested_targets:
                    collection.objects.unlink(obj)
            _set_parent_keep_world(obj, empties.get(record.get("parentId")))
        if old and old != obj:
            bpy.data.objects.remove(old, do_unlink=True)
        obj.name = record.get("name", obj.name)
        obj[DOCUMENT_MODEL_GUID_KEY] = document_model.get("id", "")
        obj[DOCUMENT_MODEL_NAME_KEY] = document_model.get("name", "Studio Selection")
        obj[DOCUMENT_ROOT_KIND_KEY] = document_model.get("rootKind", "STUDIO_SELECTION")
        obj[LAST_LOCAL_STATE_KEY] = _local_state(obj)
    SERVER.complete_reverse(pending.revision)
    settings.reverse_pending_revision = 0
    settings.reverse_conflicts.clear()
    settings.reverse_last_warning = "; ".join(warnings[:3])
    return len(staged)


def auto_apply_pending_timer():
    """Apply completed Studio transfers on Blender's main thread through an Undo operator."""

    global _AUTO_APPLY_FAILED_REVISION
    pending = SERVER.pending_reverse
    context = bpy.context
    scene = getattr(context, "scene", None)
    if (
        pending is None
        or scene is None
        or not hasattr(scene, "rbx_primitive_sync")
        or not scene.rbx_primitive_sync.reverse_auto_apply
    ):
        return 0.25
    if pending.revision == _AUTO_APPLY_FAILED_REVISION:
        return 0.25
    if getattr(context, "mode", "OBJECT") != "OBJECT":
        return 0.25

    settings = scene.rbx_primitive_sync
    settings.reverse_auto_apply_error = ""
    try:
        review_pending(context)
        for conflict in settings.reverse_conflicts:
            conflict.resolution = "APPLY_STUDIO"
        result = bpy.ops.rbx_mesh_sync.apply_incoming("EXEC_DEFAULT")
        if "FINISHED" not in result:
            raise RuntimeError(settings.reverse_auto_apply_error or "Auto Apply was cancelled")
        _AUTO_APPLY_FAILED_REVISION = 0
    except Exception as error:
        _AUTO_APPLY_FAILED_REVISION = pending.revision
        settings.reverse_auto_apply_error = str(error)
        print(f"Roblox Mesh Sync Auto Apply failed: {error}")
    return 0.25


class RBX_OT_ReverseReview(Operator):
    bl_idname = "rbx_mesh_sync.review_incoming"
    bl_label = "Review Incoming"
    bl_description = "Validate the pending Studio selection and list local conflicts"

    def execute(self, context):
        try:
            count, conflicts = review_pending(context)
        except (ValueError, KeyError, TypeError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        self.report({"INFO"}, f"{count} objects ready; {conflicts} local conflicts")
        return {"FINISHED"}


class RBX_OT_ReverseApply(Operator):
    bl_idname = "rbx_mesh_sync.apply_incoming"
    bl_label = "Apply Studio Selection"
    bl_description = "Apply the reviewed Studio selection to Blender"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        try:
            count = apply_pending(context)
        except (ValueError, KeyError, TypeError, RuntimeError) as error:
            context.scene.rbx_primitive_sync.reverse_auto_apply_error = str(error)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        context.scene.rbx_primitive_sync.reverse_auto_apply_error = ""
        self.report({"INFO"}, f"Studioから{count}個のオブジェクトを適用しました")
        return {"FINISHED"}


class RBX_OT_ReverseDiscard(Operator):
    bl_idname = "rbx_mesh_sync.discard_incoming"
    bl_label = "Discard Incoming"
    bl_description = "Discard the pending Studio selection"

    def execute(self, context):
        SERVER.discard_reverse()
        settings = context.scene.rbx_primitive_sync
        settings.reverse_pending_revision = 0
        settings.reverse_conflicts.clear()
        settings.reverse_auto_apply_error = ""
        self.report({"INFO"}, "Studioからの保留中データを破棄しました")
        return {"FINISHED"}


CLASSES = (RBX_OT_ReverseReview, RBX_OT_ReverseApply, RBX_OT_ReverseDiscard)
