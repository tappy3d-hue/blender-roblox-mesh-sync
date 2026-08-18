"""Run with Blender --background to verify mixed hierarchy and world transforms."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import bpy
from mathutils import Euler, Matrix, Vector


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import blender_extension
from blender_extension import mesh_sync, reverse_sync
from blender_extension.mesh_sync_server import ReverseSnapshot


blender_extension.register()
document = {
    "model": {"id": "smoke-root", "name": "Smoke Root"},
    "hierarchy": [
        {
            "id": "outer-model", "kind": "MODEL", "name": "Outer Model",
            "cframe": [100, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
        },
        {
            "id": "folder", "kind": "FOLDER", "name": "Folder",
            "parentId": "outer-model",
        },
        {
            "id": "inner-model", "kind": "MODEL", "name": "Inner Model",
            "parentId": "folder", "primaryCollectionId": "folder",
            "cframe": [105, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
        },
    ],
}

_root, collections, empties = reverse_sync._ensure_hierarchy(bpy.context, document)
outer = empties["outer-model"]
inner = empties["inner-model"]
assert inner.parent == outer
assert tuple(round(value, 6) for value in outer.matrix_world.translation) == (100.0, 0.0, 0.0)
assert tuple(round(value, 6) for value in inner.matrix_world.translation) == (105.0, 0.0, 0.0)
assert collections["folder"].get(reverse_sync.HIERARCHY_PARENT_KEY) == "outer-model"

child = bpy.data.objects.new("World Transform Child", None)
bpy.context.scene.collection.objects.link(child)
child.matrix_world = Matrix.Translation((110.0, 20.0, 30.0))
reverse_sync._set_parent_keep_world(child, inner)
assert tuple(round(value, 6) for value in child.matrix_world.translation) == (110.0, 20.0, 30.0)

# Reverse-sync sets Size immediately before parenting.  matrix_world can still
# be stale at this point, while matrix_basis already contains the new scale.
scaled_mesh = reverse_sync.create_mesh("Block")
scaled_child = bpy.data.objects.new("Scaled Hierarchy Child", scaled_mesh)
scaled_child.matrix_world = Matrix.Translation((112.0, 21.0, 31.0))
scaled_child.scale = (0.16, 1.85, 1.625)
collections["folder"].objects.link(scaled_child)
reverse_sync._set_parent_keep_world(scaled_child, inner)
assert tuple(round(value, 6) for value in scaled_child.scale) == (0.16, 1.85, 1.625)
assert tuple(round(value, 6) for value in scaled_child.dimensions) == (0.32, 3.7, 3.25)

print("MIXED_HIERARCHY_OK")
print("WORLD_TRANSFORM_OK")
print("PARENTED_SCALE_OK")

AUTO_ROOT_ID = "10000000-0000-4000-8000-000000000001"
auto_document = {
    "schema": "roblox-mesh-sync-reverse/1",
    "model": {"id": AUTO_ROOT_ID, "name": "Auto Root"},
    "transformMask": {"position": True, "rotation": True, "scale": True},
    "hierarchy": [], "meshes": [], "images": [],
    "appearances": [{
        "hash": "wood", "mode": "MATERIAL", "maps": {},
        "material": "Wood", "color": [0.827451, 0.5843137, 0.3764706],
        "transparency": 0,
    }],
    "objects": [{
        "id": "auto-part", "name": "Auto Part", "kind": "PART", "partType": "Block",
        "appearanceHash": "wood", "size": [2, 3, 4],
        "cframe": [120, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
    }],
}
reverse_sync.SERVER._pending_reverse = ReverseSnapshot(1, auto_document, {})
reverse_sync.auto_apply_pending_timer()
assert reverse_sync.SERVER.pending_reverse is None
auto_part = next(obj for obj in bpy.data.objects if obj.get(reverse_sync.OBJECT_GUID_KEY) == "auto-part")
assert tuple(round(value, 6) for value in auto_part.matrix_world.translation) == (120.0, 0.0, 0.0)
print("AUTO_APPLY_OK")

# Sending an object received from Studio back to Studio must reuse the same
# document ID and must not serialize the root Collection as a nested Folder.
bpy.ops.object.select_all(action="DESELECT")
auto_part.select_set(True)
bpy.context.view_layer.objects.active = auto_part
round_trip, _mesh_blobs, _image_blobs = mesh_sync.build_selection_document(bpy.context)
assert round_trip["model"] == {
    "id": AUTO_ROOT_ID, "name": "Auto Root", "rootKind": "STUDIO_SELECTION",
}
assert all(node["name"] != "Auto Root" for node in round_trip["hierarchy"])
print("ROUND_TRIP_ROOT_REUSED_OK")

# A rotated non-cubic Part must still be scaled from its local mesh axes.  The
# former Object.dimensions calculation used the rotated world AABB here.
angle = 0.7071067811865476
rotated_record = {
    "id": "rotated-part", "name": "Rotated Part", "kind": "PART", "partType": "Block",
    "appearanceHash": "wood", "size": [0.25, 6.0, 1.5],
    "cframe": [0, 0, 0, angle, 0, angle, 0, 1, 0, -angle, 0, angle],
}
rotated = reverse_sync._create_object(
    rotated_record, {}, {}, {}, {"wood": auto_document["appearances"][0]}, {}, 1.0,
)
native = reverse_sync._mesh_local_dimensions(rotated.data)
displayed_local = tuple(abs(float(rotated.scale[i])) * native[i] for i in range(3))
expected_local = reverse_sync.reverse_size(rotated_record["size"], 1.0)
assert tuple(round(value, 6) for value in displayed_local) == tuple(
    round(value, 6) for value in expected_local
)
print("ROTATED_LOCAL_SIZE_OK")

# MeshPart.Size is relative to EditableMesh:GetSize, which can differ from a
# freshly calculated vertex AABB.  Prefer the native size sent by Studio.
mesh_payload = {
    "vertices": [[0, 0, 0], [2, 0, 0], [0, 4, 0], [0, 0, 6]],
    "triangles": [[0, 1, 2], [0, 3, 1], [0, 2, 3], [1, 3, 2]],
    "cornerNormals": [], "cornerUvs": [], "cornerColors": [],
}
mesh_record = {
    "id": "native-size-mesh", "name": "Native Size Mesh", "kind": "MESH",
    "meshHash": "native-size", "meshSize": [4, 8, 12], "size": [8, 8, 6],
    "appearanceHash": "wood",
    "cframe": [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
}
mesh_obj = reverse_sync._create_object(
    mesh_record, {"native-size": mesh_payload}, {}, {},
    {"wood": auto_document["appearances"][0]}, {}, 1.0,
)
assert tuple(round(abs(float(value)), 6) for value in mesh_obj.scale) == (2.0, 0.5, 1.0)
print("MESHPART_NATIVE_SIZE_OK")

# Roblox EditableMesh supplies split normals per triangle corner. Blender only
# displays those normals when the imported polygons use smooth shading; flat
# defaults expose every narrow triangulation facet as dark stripes.
normal_payload = {
    "vertices": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]],
    "triangles": [[0, 1, 2], [0, 2, 3]],
    "cornerNormals": [
        [[0, 0, 1], [0, 0, 1], [0, 0, 1]],
        [[0, 0, 1], [0, 0, 1], [0, 0, 1]],
    ],
    "cornerUvs": [], "cornerColors": [],
}
normal_mesh = reverse_sync._mesh_from_payload("Split Normal Mesh", normal_payload, 1.0)
assert all(polygon.use_smooth for polygon in normal_mesh.polygons)
assert len(normal_mesh.corner_normals) == len(normal_mesh.loops)
print("SPLIT_NORMAL_SMOOTHING_OK")

# Collections are Studio Folders, Empty presentation is independently
# configurable, and the Blender scene root is never serialized as "Scene".
forward_collection = bpy.data.collections.new("Forward Collection")
bpy.context.scene.collection.children.link(forward_collection)
forward_empty = bpy.data.objects.new("Forward Empty", None)
forward_collection.objects.link(forward_empty)
forward_empty.rbx_primitive_sync.empty_export_mode = "FOLDER"
forward_mesh = reverse_sync.create_mesh("Block")
forward_part = bpy.data.objects.new("Forward Part", forward_mesh)
forward_collection.objects.link(forward_part)
forward_part.parent = forward_empty
forward_part.rbx_primitive_sync.is_roblox_part = False
forward_part.rbx_primitive_sync.mesh_sync_enabled = True
bpy.ops.object.select_all(action="DESELECT")
forward_part.select_set(True)
bpy.context.view_layer.objects.active = forward_part
forward_document, _mesh_blobs, _image_blobs = mesh_sync.build_selection_document(bpy.context)
assert forward_document["model"]["rootKind"] == "BLENDER_SCENE"
folder_node = next(node for node in forward_document["hierarchy"] if node["name"] == "Forward Collection")
empty_node = next(node for node in forward_document["hierarchy"] if node["name"] == "Forward Empty")
assert folder_node["kind"] == "FOLDER"
assert empty_node["kind"] == "MODEL" and empty_node["studioMode"] == "FOLDER"
assert all(node["name"] != "Scene" for node in forward_document["hierarchy"])
print("FORWARD_HIERARCHY_PRESENTATION_OK")

# Objects linked directly to the scene master Collection are emitted at the
# document root.  The master Collection itself must never become a Scene node.
root_mesh = reverse_sync.create_mesh("Block")
root_part = bpy.data.objects.new("Root Part", root_mesh)
bpy.context.scene.collection.objects.link(root_part)
root_part.rbx_primitive_sync.mesh_sync_enabled = True
bpy.ops.object.select_all(action="DESELECT")
root_part.select_set(True)
bpy.context.view_layer.objects.active = root_part
root_document, _root_mesh_blobs, _root_image_blobs = mesh_sync.build_selection_document(bpy.context)
root_record = root_document["instances"][0]
assert root_document["hierarchy"] == []
assert not root_record.get("primaryCollectionId")
assert not root_record.get("collectionIds")
assert not root_record.get("parentId")
print("SCENE_ROOT_OBJECT_DIRECT_OK")

# Preserve mode replaces object content but retains every Collection membership
# and the exact Blender parent, and BLENDER_SCENE does not create a new root Collection.
secondary_collection = bpy.data.collections.new("Secondary Membership")
bpy.context.scene.collection.children.link(secondary_collection)
secondary_collection.objects.link(forward_part)
before_memberships = set(forward_part.users_collection)
before_parent = forward_part.parent
forward_id = forward_part[reverse_sync.OBJECT_GUID_KEY]
scene_model_id = bpy.context.scene["rbx_model_guid"]
preserve_document = {
    "schema": "roblox-mesh-sync-reverse/2",
    "model": {"id": scene_model_id, "name": "Should Not Become Collection", "rootKind": "BLENDER_SCENE"},
    "transformMask": {"position": True, "rotation": True, "scale": True},
    "hierarchy": [], "meshes": [], "images": [],
    "appearances": auto_document["appearances"],
    "objects": [{
        "id": forward_id, "name": "Forward Part Updated", "kind": "PART", "partType": "Block",
        "appearanceHash": "wood", "size": [3, 4, 5],
        "cframe": [130, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
    }],
}
bpy.context.scene.rbx_primitive_sync.reverse_preserve_hierarchy = True
reverse_sync.SERVER._pending_reverse = ReverseSnapshot(2, preserve_document, {})
reverse_sync.auto_apply_pending_timer()
preserved = next(obj for obj in bpy.data.objects if obj.get(reverse_sync.OBJECT_GUID_KEY) == forward_id)
assert set(preserved.users_collection) == before_memberships
assert preserved.parent == before_parent
assert bpy.data.collections.get("Should Not Become Collection") is None
assert preserved.name == "Forward Part Updated"
print("PRESERVE_BLENDER_HIERARCHY_OK")

# Disabling Preserve Blender Hierarchy deliberately applies the incoming Studio
# Folder/Model hierarchy instead of keeping the existing Blender memberships.
studio_folder_id = "studio-folder-for-reparent"
studio_empty_id = "studio-empty-for-reparent"
reparent_document = {
    "schema": "roblox-mesh-sync-reverse/2",
    "model": {"id": scene_model_id, "name": "Scene Root", "rootKind": "BLENDER_SCENE"},
    "transformMask": {"position": True, "rotation": True, "scale": True},
    "hierarchy": [
        {"id": studio_folder_id, "name": "Studio Folder", "kind": "FOLDER", "parentId": None},
        {
            "id": studio_empty_id, "name": "Studio Empty", "kind": "MODEL",
            "parentId": studio_folder_id, "primaryCollectionId": studio_folder_id,
            "collectionIds": [studio_folder_id],
            "cframe": [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
        },
    ],
    "meshes": [], "images": [], "appearances": auto_document["appearances"],
    "objects": [{
        "id": forward_id, "name": "Studio Reparented Part", "kind": "PART", "partType": "Block",
        "appearanceHash": "wood", "size": [3, 4, 5],
        "cframe": [130, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
        "parentId": studio_empty_id,
        "primaryCollectionId": studio_folder_id,
        "collectionIds": [studio_folder_id],
    }],
}
bpy.context.scene.rbx_primitive_sync.reverse_preserve_hierarchy = False
reverse_sync.SERVER._pending_reverse = ReverseSnapshot(3, reparent_document, {})
reverse_sync.auto_apply_pending_timer()
reparented = next(obj for obj in bpy.data.objects if obj.get(reverse_sync.OBJECT_GUID_KEY) == forward_id)
studio_folder = next(
    collection for collection in bpy.data.collections
    if collection.get(reverse_sync.COLLECTION_GUID_KEY) == studio_folder_id
)
studio_empty = next(
    obj for obj in bpy.data.objects
    if obj.get(reverse_sync.HIERARCHY_GUID_KEY) == studio_empty_id
)
assert set(reparented.users_collection) == {studio_folder}
assert reparented.parent == studio_empty
print("APPLY_STUDIO_HIERARCHY_OK")

# Blender-owned reverse updates must keep editable source topology, modifiers,
# linked Mesh data, and the off-center object origin while matching Studio's
# geometry-center transform and appearance.
source_mesh = bpy.data.meshes.new("Editable Source Mesh")
bottom = [(2.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.6, 1.0, 0.0), (3.0, 2.0, 0.0), (1.4, 1.0, 0.0)]
top = [(x, y, 3.0) for x, y, _z in bottom]
source_mesh.from_pydata(bottom + top, [], [
    (0, 4, 3, 2, 1), (5, 6, 7, 8, 9),
    (0, 1, 6, 5), (1, 2, 7, 6), (2, 3, 8, 7), (3, 4, 9, 8), (4, 0, 5, 9),
])
source_mesh.update()
source = bpy.data.objects.new("Editable Source", source_mesh)
linked = bpy.data.objects.new("Editable Source Linked", source_mesh)
bpy.context.scene.collection.objects.link(source)
bpy.context.scene.collection.objects.link(linked)
source_id = "90000000-0000-4000-8000-000000000001"
source[reverse_sync.OBJECT_GUID_KEY] = source_id
bevel = source.modifiers.new("Source Bevel", "BEVEL")
bevel.width = 0.1
source_pointer = source.as_pointer()
mesh_pointer = source.data.as_pointer()
source_vertices = [tuple(vertex.co) for vertex in source.data.vertices]
source_faces = [tuple(polygon.vertices) for polygon in source.data.polygons]
source_document = {
    "schema": "roblox-mesh-sync-reverse/3",
    "model": {"id": scene_model_id, "name": "Scene Root", "rootKind": "BLENDER_SCENE"},
    "transformMask": {"position": True, "rotation": True, "scale": True},
    "hierarchy": [], "meshes": [], "images": [],
    "appearances": [{
        "hash": "studio-red", "mode": "MATERIAL", "maps": {},
        "material": "Brick", "color": [0.8, 0.1, 0.05], "transparency": 0.2,
    }],
    "objects": [{
        "id": source_id, "name": "Editable Source Updated", "kind": "MESH",
        "geometryOwner": "BLENDER", "geometryAvailable": False, "geometryChanged": True,
        "appearanceHash": "studio-red", "size": [8.0, 10.0, 6.0],
        "cframe": [20, 30, 40, 1, 0, 0, 0, 1, 0, 0, 0, 1],
        "anchored": False, "canCollide": False, "canTouch": True,
        "canQuery": False, "castShadow": False, "collisionFidelity": "Hull",
    }],
}
bpy.context.scene.rbx_primitive_sync.reverse_preserve_geometry = True
bpy.context.scene.rbx_primitive_sync.reverse_preserve_hierarchy = True
reverse_sync.SERVER._pending_reverse = ReverseSnapshot(4, source_document, {})
reverse_sync.auto_apply_pending_timer()
preserved_source = next(obj for obj in bpy.data.objects if obj.get(reverse_sync.OBJECT_GUID_KEY) == source_id)
assert preserved_source.as_pointer() == source_pointer
assert preserved_source.data.as_pointer() == mesh_pointer == linked.data.as_pointer()
assert [tuple(vertex.co) for vertex in preserved_source.data.vertices] == source_vertices
assert [tuple(polygon.vertices) for polygon in preserved_source.data.polygons] == source_faces
assert preserved_source.modifiers.get("Source Bevel") is not None
assert preserved_source.rbx_primitive_sync.material == "Brick"
assert tuple(round(value, 5) for value in preserved_source.rbx_primitive_sync.color) != (0.8, 0.1, 0.05)
local_center, _local_size = reverse_sync._evaluated_local_bounds(
    preserved_source, bpy.context.evaluated_depsgraph_get(),
)
world_center = preserved_source.matrix_world @ local_center
assert tuple(round(value, 5) for value in world_center) == (20.0, -40.0, 30.0)
assert reverse_sync.GEOMETRY_WARNING_KEY in preserved_source
print("PRESERVE_SOURCE_GEOMETRY_OK")

# Reconstructed Studio meshes expose exact triangles, compatible quad joining,
# coplanar N-gons, and optional merge-by-distance without changing wire data.
reconstruct_payload = {
    "vertices": [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [1.00005, 1, 0]],
    "triangles": [[0, 1, 2], [0, 4, 3]],
    "cornerNormals": [],
    "cornerUvs": [[[0, 0], [1, 0], [1, 1]], [[0, 0], [1, 1], [0, 1]]],
    "cornerColors": [],
}
exact_mesh = reverse_sync._mesh_from_payload("Exact", reconstruct_payload, 1.0)
assert len(exact_mesh.vertices) == 5 and len(exact_mesh.polygons) == 2
quad_mesh = reverse_sync._mesh_from_payload(
    "Quad", reconstruct_payload, 1.0, topology_mode="QUADS", merge_distance=0.0001,
)
assert len(quad_mesh.vertices) == 4
assert len(quad_mesh.polygons) == 1 and len(quad_mesh.polygons[0].vertices) == 4
ngon_payload = {
    "vertices": [[0, 0, 0], [2, 0, 0], [2, 2, 0], [0, 2, 0], [1, 1, 0]],
    "triangles": [[0, 1, 4], [1, 2, 4], [2, 3, 4], [3, 0, 4]],
    "cornerNormals": [], "cornerUvs": [], "cornerColors": [],
}
ngon_mesh = reverse_sync._mesh_from_payload("Ngon", ngon_payload, 1.0, topology_mode="NGONS")
assert len(ngon_mesh.polygons) == 1
assert len(ngon_mesh.polygons[0].vertices) == 4
print("REVERSE_TOPOLOGY_MODES_OK")

# DataModelMesh metadata on separated CSG operands changes the actual Boolean
# geometry even though the restored parent remains a plain Part.
data_mesh_cache = {}
data_mesh_object = reverse_sync._create_object(
    {
        "id": "block-mesh-part", "name": "BlockMesh Part", "kind": "PART",
        "partType": "Block", "size": [2, 4, 6],
        "cframe": [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
        "dataModelMeshClass": "BlockMesh",
        "meshScale": [2, 0.5, 1.5], "meshOffset": [1, 2, 3],
    },
    {}, {}, data_mesh_cache, {}, {}, 1.0,
)
bpy.context.scene.collection.objects.link(data_mesh_object)
bpy.context.view_layer.update()
data_mesh_dimensions = tuple(round(value, 5) for value in data_mesh_object.dimensions)
data_mesh_location = tuple(round(value, 5) for value in data_mesh_object.location)
data_mesh_scale = tuple(round(value, 5) for value in data_mesh_object.scale)
assert data_mesh_dimensions == (4.0, 9.0, 2.0), (data_mesh_dimensions, data_mesh_scale)
assert data_mesh_location == (1.0, -3.0, 2.0), data_mesh_location
bpy.data.objects.remove(data_mesh_object, do_unlink=True)
for data_mesh in data_mesh_cache.values():
    if data_mesh.users == 0:
        bpy.data.meshes.remove(data_mesh)
print("CSG_DATAMODELMESH_TRANSFORM_OK")

special_sphere_cache = {}
special_sphere = reverse_sync._create_object(
    {
        "id": "special-sphere", "name": "SpecialMesh Sphere", "kind": "PART",
        "partType": "Ball", "size": [10, 4, 6],
        "cframe": [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
        "dataModelMeshClass": "SpecialMesh", "specialMeshType": "Sphere",
        "meshScale": [0.5, 2, 1], "meshOffset": [0, 0, 0],
    },
    {}, {}, special_sphere_cache, {}, {}, 1.0,
)
bpy.context.scene.collection.objects.link(special_sphere)
bpy.context.view_layer.update()
assert tuple(round(value, 5) for value in special_sphere.dimensions) == (5.0, 6.0, 8.0)
bpy.data.objects.remove(special_sphere, do_unlink=True)
for special_mesh in special_sphere_cache.values():
    if special_mesh.users == 0:
        bpy.data.meshes.remove(special_mesh)
print("CSG_SPECIALMESH_SPHERE_OK")

# reverse/4 CSG documents are evaluated and baked into one standalone Mesh.
csg_transform = [100, 20, 30, 0, -1, 0, 1, 0, 0, 0, 0, 1]
positive_transform = [100, 20, 30, 1, 0, 0, 0, 1, 0, 0, 0, 1]
inner_negative_transform = [98.93408, 20, 30, *csg_transform[3:]]
outer_negative_transform = [100.76856, 20, 30, *csg_transform[3:]]
csg_document = {
    "schema": "roblox-mesh-sync-reverse/4",
    "csgPhase": 3,
    "model": {"id": "csg-model", "name": "Nested Union", "rootKind": "STUDIO_SELECTION"},
    "transformMask": {"position": True, "rotation": True, "scale": True},
    "hierarchy": [
        {"id": "csg-folder", "name": "CSG Folder", "kind": "FOLDER"},
        {
            "id": "csg-model-node", "name": "CSG Model", "kind": "MODEL",
            "primaryCollectionId": "csg-folder",
        },
    ], "meshes": [], "images": [], "appearances": [],
    "objects": [
        {
            "id": "csg-positive", "name": "Positive", "kind": "PART", "partType": "Block",
            "size": [4, 1, 2], "cframe": positive_transform,
        },
        {
            "id": "csg-negative-inner", "name": "Inner Negative", "kind": "PART", "partType": "Block",
            "size": [4, 1, 2], "cframe": inner_negative_transform,
        },
        {
            "id": "csg-negative-outer", "name": "Outer Negative", "kind": "PART", "partType": "Block",
            "size": [4, 1, 2], "cframe": outer_negative_transform,
        },
        {
            "id": "mixed-ordinary", "name": "Mixed Ordinary Part", "kind": "PART", "partType": "Ball",
            "size": [2, 3, 4], "cframe": [110, 20, 30, 1, 0, 0, 0, 1, 0, 0, 0, 1],
        },
    ],
    "csg": [
        {
            "id": "csg-inner", "name": "Inner Union", "op": "union",
            "size": [1, 4, 2], "cframe": csg_transform,
            "appearance": {"material": "Plastic", "color": [0.7, 0.7, 0.7], "transparency": 0},
            "operands": [
                {"role": "positive", "kind": "instance", "ref": "csg-positive"},
                {"role": "negative", "kind": "instance", "ref": "csg-negative-inner"},
            ],
        },
        {
            "id": "csg-negative-group", "name": "Nested Negative Union", "op": "union",
            "size": [1, 4, 2], "cframe": csg_transform,
            "appearance": {"material": "Plastic", "color": [0.6, 0.6, 0.6], "transparency": 0},
            "operands": [
                {"role": "positive", "kind": "instance", "ref": "csg-negative-outer"},
            ],
        },
        {
            "id": "csg-outer", "name": "Outer Union", "op": "union",
            "size": [1, 4, 2], "cframe": csg_transform,
            "appearance": {"material": "Plastic", "color": [0.5, 0.6, 0.7], "transparency": 0},
            "operands": [
                {"role": "positive", "kind": "csg", "ref": "csg-inner"},
                {"role": "negative", "kind": "csg", "ref": "csg-negative-group"},
            ],
        },
    ],
    "csgRoots": [{
        "kind": "csg", "ref": "csg-outer", "name": "Outer Union",
        "sourceGuid": "40000000-0000-4000-8000-000000000001",
        "sourcePath": "Workspace.Test.OuterUnion",
        "parentId": "csg-model-node",
        "primaryCollectionId": "csg-folder",
        "collectionIds": ["csg-folder"],
    }],
}
reverse_sync.SERVER._pending_reverse = ReverseSnapshot(5, csg_document, {})
reverse_sync.auto_apply_pending_timer()
assert reverse_sync.SERVER.pending_reverse is None
csg_result = next(
    obj for obj in bpy.data.objects
    if obj.get(reverse_sync.CSG_SOURCE_KEY) == "40000000-0000-4000-8000-000000000001"
    and obj.get(reverse_sync.CSG_ROLE_KEY) == "BAKED_RESULT"
    and not obj.hide_get()
)
assert csg_result.rbx_primitive_sync.is_roblox_part is False
assert csg_result.rbx_primitive_sync.mesh_sync_enabled is True
assert len(csg_result.modifiers) == 0
operand_collection = next((
    collection for collection in bpy.data.collections
    if collection.get(reverse_sync.CSG_COLLECTION_ROLE_KEY) == "OPERANDS"
    and collection.get(reverse_sync.CSG_SOURCE_KEY) == "40000000-0000-4000-8000-000000000001"
), None)
assert operand_collection is None
assert not any(
    collection.get(reverse_sync.CSG_COLLECTION_ROLE_KEY) == "RESULTS_ROOT"
    for collection in bpy.data.collections
)
assert csg_result.get(reverse_sync.CSG_ROLE_KEY) == "BAKED_RESULT"
assert len(csg_result.modifiers) == 0
assert [
    obj for obj in bpy.data.objects
    if obj.get(reverse_sync.CSG_SOURCE_KEY) == "40000000-0000-4000-8000-000000000001"
] == [csg_result]
assert csg_result.get(reverse_sync.OBJECT_GUID_KEY) == "40000000-0000-4000-8000-000000000001"
csg_parent = csg_result.parent
assert csg_parent is not None
assert csg_parent.get(reverse_sync.HIERARCHY_GUID_KEY) == "csg-model-node"
assert any(
    collection.get(reverse_sync.COLLECTION_GUID_KEY) == "csg-folder"
    for collection in csg_result.users_collection
)
assert bpy.context.scene.collection not in csg_result.users_collection
print("CSG_SOURCE_HIERARCHY_OK")
mixed_ordinary = next(
    obj for obj in bpy.data.objects
    if obj.get(reverse_sync.OBJECT_GUID_KEY) == "mixed-ordinary"
)
assert mixed_ordinary.rbx_primitive_sync.is_roblox_part is True
assert mixed_ordinary.rbx_primitive_sync.part_type == "Ball"
assert mixed_ordinary.select_get()
print("CSG_MIXED_ORDINARY_APPLY_OK")

def rounded_bounds(obj):
    bpy.context.view_layer.update()
    minimum, maximum = reverse_sync._evaluated_local_bounds(
        obj, bpy.context.evaluated_depsgraph_get(),
    )
    return tuple(round(value, 5) for point in (minimum, maximum) for value in point)


def evaluated_component_count(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        neighbors = [set() for _vertex in mesh.vertices]
        for edge in mesh.edges:
            first, second = edge.vertices
            neighbors[first].add(second)
            neighbors[second].add(first)
        unseen = set(range(len(mesh.vertices)))
        components = 0
        while unseen:
            components += 1
            pending = [unseen.pop()]
            while pending:
                current = pending.pop()
                adjacent = neighbors[current] & unseen
                unseen.difference_update(adjacent)
                pending.extend(adjacent)
        return components
    finally:
        evaluated.to_mesh_clear()


original_bounds = rounded_bounds(csg_result)
assert evaluated_component_count(csg_result) == 3
csg_result.location += Vector((7.5, -3.25, 2.0))
csg_result.rotation_euler = Euler((0.35, -0.2, 0.6), "XYZ")
csg_result.scale = (1.75, 0.6, 1.25)
# A baked result behaves like an ordinary standalone mesh.
bpy.context.view_layer.update()
assert rounded_bounds(csg_result) == original_bounds
assert evaluated_component_count(csg_result) == 3
bpy.ops.object.select_all(action="DESELECT")
csg_result.select_set(True)
bpy.context.view_layer.objects.active = csg_result
csg_round_trip, _csg_mesh_blobs, _csg_image_blobs = mesh_sync.build_selection_document(bpy.context)
assert csg_round_trip["instances"][0]["kind"] == "MESH"
assert csg_round_trip["instances"][0]["id"] == "40000000-0000-4000-8000-000000000001"
print("CSG_BOOLEAN_RECONSTRUCTION_OK")

# Selecting an imported Empty is a complete-sync boundary. New child meshes
# inherit its document scope, every enabled descendant is collected, and even
# an empty child Empty remains in the hierarchy snapshot.
assert csg_parent.get(reverse_sync.DOCUMENT_MODEL_GUID_KEY) == "csg-model"
complete_model_id = "40000000-0000-4000-8000-000000000010"
complete_hierarchy_id = "40000000-0000-4000-8000-000000000011"
for scoped_object in (csg_parent, csg_result):
    scoped_object[mesh_sync.DOCUMENT_MODEL_GUID_KEY] = complete_model_id
    scoped_object[mesh_sync.DOCUMENT_MODEL_NAME_KEY] = "Nested Union"
    scoped_object[mesh_sync.DOCUMENT_ROOT_KIND_KEY] = "STUDIO_SELECTION"
csg_parent[mesh_sync.HIERARCHY_GUID_KEY] = complete_hierarchy_id
replacement_mesh = bpy.data.meshes.new("Low Poly Chain Mesh")
replacement_mesh.from_pydata(
    [(-0.5, -0.5, 0), (0.5, -0.5, 0), (0, 0.5, 0), (0, 0, 0.5)],
    [],
    [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)],
)
replacement_mesh.update()
replacement_chain = bpy.data.objects.new("Low Poly Chain", replacement_mesh)
bpy.context.scene.collection.objects.link(replacement_chain)
replacement_chain.parent = csg_parent
replacement_chain.rbx_primitive_sync.is_roblox_part = False
replacement_chain.rbx_primitive_sync.mesh_sync_enabled = True
disabled_existing = replacement_chain.copy()
disabled_existing.data = replacement_chain.data.copy()
disabled_existing.name = "Disabled Existing Chain"
disabled_existing[mesh_sync.OBJECT_GUID_KEY] = "40000000-0000-4000-8000-000000000012"
disabled_existing.rbx_primitive_sync.mesh_sync_enabled = False
bpy.context.scene.collection.objects.link(disabled_existing)
disabled_existing.parent = csg_parent
empty_child = bpy.data.objects.new("Empty Child Group", None)
bpy.context.scene.collection.objects.link(empty_child)
empty_child.parent = csg_parent
bpy.ops.object.select_all(action="DESELECT")
csg_parent.select_set(True)
empty_child.select_set(True)
bpy.context.view_layer.objects.active = csg_parent
assert mesh_sync.RBX_OT_MeshSyncSendSelected.poll(bpy.context)
complete_document, _complete_meshes, _complete_images = mesh_sync.build_selection_document(bpy.context)
complete_ids = {record["id"] for record in complete_document["instances"]}
assert replacement_chain.get(mesh_sync.OBJECT_GUID_KEY) in complete_ids
assert csg_result.get(mesh_sync.OBJECT_GUID_KEY) in complete_ids
assert disabled_existing.get(mesh_sync.OBJECT_GUID_KEY) not in complete_ids
assert complete_document["model"]["id"] == complete_model_id
assert complete_document["replaceScopes"] == [{
    "hierarchyId": complete_hierarchy_id,
    "mode": "REPLACE_DESCENDANTS",
    "presentSourceObjectIds": sorted((
        csg_result.get(mesh_sync.OBJECT_GUID_KEY),
        disabled_existing.get(mesh_sync.OBJECT_GUID_KEY),
        replacement_chain.get(mesh_sync.OBJECT_GUID_KEY),
    )),
}]
assert any(
    node["id"] == empty_child.get(mesh_sync.HIERARCHY_GUID_KEY)
    for node in complete_document["hierarchy"]
)
print("EMPTY_COMPLETE_SYNC_SCOPE_OK")

# Old files did not store document metadata on imported Empties. An
# unambiguous synchronized descendant migrates the Empty, allowing a new child
# mesh to inherit the same scope without being treated as unrelated.
legacy_empty = bpy.data.objects.new("Legacy Imported Empty", None)
bpy.context.scene.collection.objects.link(legacy_empty)
legacy_known = replacement_chain.copy()
legacy_known.data = replacement_chain.data.copy()
bpy.context.scene.collection.objects.link(legacy_known)
legacy_known.parent = legacy_empty
legacy_known[mesh_sync.DOCUMENT_MODEL_GUID_KEY] = "40000000-0000-4000-8000-000000000099"
legacy_known[mesh_sync.DOCUMENT_MODEL_NAME_KEY] = "Legacy Lamp"
legacy_known[mesh_sync.DOCUMENT_ROOT_KIND_KEY] = "STUDIO_SELECTION"
legacy_new = replacement_chain.copy()
legacy_new.data = replacement_chain.data.copy()
bpy.context.scene.collection.objects.link(legacy_new)
legacy_new.parent = legacy_empty
for key in (
    mesh_sync.DOCUMENT_MODEL_GUID_KEY,
    mesh_sync.DOCUMENT_MODEL_NAME_KEY,
    mesh_sync.DOCUMENT_ROOT_KIND_KEY,
    mesh_sync.OBJECT_GUID_KEY,
):
    if key in legacy_new:
        del legacy_new[key]
bpy.ops.object.select_all(action="DESELECT")
legacy_empty.select_set(True)
bpy.context.view_layer.objects.active = legacy_empty
legacy_document, _legacy_meshes, _legacy_images = mesh_sync.build_selection_document(bpy.context)
assert legacy_document["model"]["id"] == "40000000-0000-4000-8000-000000000099"
assert legacy_empty.get(mesh_sync.DOCUMENT_MODEL_GUID_KEY) == "40000000-0000-4000-8000-000000000099"
assert len(legacy_document["instances"]) == 2
print("LEGACY_EMPTY_SCOPE_MIGRATION_OK")

# Appearance selection supports direct-parent and current-selection scopes,
# optionally includes Parts, and merging keeps the active MeshPart identity
# while recording every removed source GUID for Studio cleanup.
def make_merge_mesh(name, location):
    mesh = bpy.data.meshes.new(f"{name} Mesh")
    mesh.from_pydata(
        [(-0.5, -0.5, 0), (0.5, -0.5, 0), (0, 0.5, 0), (0, 0, 0.5)],
        [],
        [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)],
    )
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = location
    return obj


merge_parent = bpy.data.objects.new("Merge Parent", None)
bpy.context.scene.collection.objects.link(merge_parent)
other_parent = bpy.data.objects.new("Other Parent", None)
bpy.context.scene.collection.objects.link(other_parent)
merge_active = make_merge_mesh("Merge Active", (0, 0, 0))
merge_match = make_merge_mesh("Merge Match", (2, 0, 0))
merge_different = make_merge_mesh("Merge Different", (4, 0, 0))
merge_part = make_merge_mesh("Matching Roblox Part", (6, 0, 0))
merge_outside = make_merge_mesh("Matching Outside", (8, 0, 0))
for obj in (merge_active, merge_match, merge_different, merge_part, merge_outside):
    obj.rbx_primitive_sync.mesh_use_roblox_material = True
    obj.rbx_primitive_sync.material = "Plastic"
    obj.rbx_primitive_sync.color = (0.2, 0.4, 0.6)
    obj.rbx_primitive_sync.transparency = 0.1
for obj in (merge_active, merge_match, merge_different, merge_part):
    obj.parent = merge_parent
merge_outside.parent = other_parent
merge_different.rbx_primitive_sync.material = "Metal"
merge_part.rbx_primitive_sync.is_roblox_part = True
merge_part.rbx_primitive_sync.sync_enabled = True
merge_active[mesh_sync.OBJECT_GUID_KEY] = "50000000-0000-4000-8000-000000000001"
merge_match[mesh_sync.OBJECT_GUID_KEY] = "50000000-0000-4000-8000-000000000002"
merge_different[mesh_sync.OBJECT_GUID_KEY] = "50000000-0000-4000-8000-000000000003"

bpy.ops.object.select_all(action="DESELECT")
merge_active.select_set(True)
bpy.context.view_layer.objects.active = merge_active
bpy.context.scene.rbx_primitive_sync.mesh_appearance_selection_scope = "SAME_PARENT"
bpy.context.scene.rbx_primitive_sync.mesh_appearance_exclude_parts = True
assert bpy.ops.rbx_mesh_sync.select_same_appearance() == {"FINISHED"}
assert set(bpy.context.selected_objects) == {merge_active, merge_match}

bpy.context.scene.rbx_primitive_sync.mesh_appearance_exclude_parts = False
assert bpy.ops.rbx_mesh_sync.select_same_appearance() == {"FINISHED"}
assert set(bpy.context.selected_objects) == {merge_active, merge_match, merge_part}

# Current Selection can include an object outside the parent while filtering a
# different appearance out of the initial selection.
bpy.ops.object.select_all(action="DESELECT")
for obj in (merge_active, merge_different, merge_outside):
    obj.select_set(True)
bpy.context.view_layer.objects.active = merge_active
bpy.context.scene.rbx_primitive_sync.mesh_appearance_selection_scope = "CURRENT_SELECTION"
bpy.context.scene.rbx_primitive_sync.mesh_appearance_exclude_parts = True
assert bpy.ops.rbx_mesh_sync.select_same_appearance() == {"FINISHED"}
assert set(bpy.context.selected_objects) == {merge_active, merge_outside}
print("SELECT_SAME_APPEARANCE_OK")

# A manually added Part blocks the destructive operation before Join.
merge_match.select_set(True)
merge_part.select_set(True)
before_merge_objects = set(bpy.data.objects)
try:
    bpy.ops.rbx_mesh_sync.merge_to_active_appearance()
    raise AssertionError("Part selection should block MeshPart merge")
except RuntimeError as error:
    assert "Roblox Part" in str(error)
assert set(bpy.data.objects) == before_merge_objects
merge_part.select_set(False)

# A manually added different MeshPart is accepted and receives the active
# appearance. The active GUID survives and both removed GUIDs are exported.
merge_different.select_set(True)
merge_outside.select_set(False)
active_parent = merge_active.parent
active_location = merge_active.location.copy()
merge_match_name = merge_match.name
merge_different_name = merge_different.name
assert bpy.ops.rbx_mesh_sync.merge_to_active_appearance() == {"FINISHED"}
assert merge_active.name in bpy.data.objects
assert merge_match_name not in bpy.data.objects
assert merge_different_name not in bpy.data.objects
assert merge_active.parent == active_parent
assert merge_active.location == active_location
assert merge_active.get(mesh_sync.OBJECT_GUID_KEY) == "50000000-0000-4000-8000-000000000001"
assert merge_active.rbx_primitive_sync.material == "Plastic"
stored_replacements = json.loads(merge_active.get(mesh_sync.REPLACES_OBJECT_IDS_KEY))
assert stored_replacements == [
    "50000000-0000-4000-8000-000000000002",
    "50000000-0000-4000-8000-000000000003",
]
bpy.ops.object.select_all(action="DESELECT")
merge_active.select_set(True)
bpy.context.view_layer.objects.active = merge_active
merged_document, _merged_meshes, _merged_images = mesh_sync.build_selection_document(bpy.context)
assert merged_document["instances"][0]["replacesObjectIds"] == stored_replacements
print("MERGE_TO_ACTIVE_APPEARANCE_OK")
