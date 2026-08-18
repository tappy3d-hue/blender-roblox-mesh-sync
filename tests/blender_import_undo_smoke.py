from __future__ import annotations

import copy
from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import blender_extension
from blender_extension import reverse_sync
from blender_extension.mesh_sync_server import ReverseSnapshot


MODE = sys.argv[sys.argv.index("--") + 1]
blender_extension.register()
bpy.context.scene.rbx_primitive_sync.reverse_auto_apply = True


def apply(revision, document):
    reverse_sync.SERVER._pending_reverse = ReverseSnapshot(revision, document, {})
    reverse_sync.auto_apply_pending_timer()
    assert reverse_sync.SERVER.pending_reverse is None


appearance = [{
    "hash": "plastic", "mode": "MATERIAL", "maps": {},
    "material": "Plastic", "color": [0.6, 0.7, 0.8], "transparency": 0,
}]

if MODE == "normal":
    object_id = "50000000-0000-4000-8000-000000000001"
    document = {
        "schema": "roblox-mesh-sync-reverse/3",
        "model": {"id": "50000000-0000-4000-8000-000000000002", "name": "Undo Test", "rootKind": "STUDIO_SELECTION"},
        "transformMask": {"position": True, "rotation": True, "scale": True},
        "hierarchy": [], "meshes": [], "images": [], "appearances": appearance,
        "objects": [{
            "id": object_id, "name": "Original Mesh", "kind": "PART", "partType": "Block",
            "appearanceHash": "plastic", "size": [2, 3, 4],
            "cframe": [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
        }],
    }
    apply(1, document)
    original = next(obj for obj in bpy.data.objects if obj.get(reverse_sync.OBJECT_GUID_KEY) == object_id)
    original_vertex_count = len(original.data.vertices)
    update = copy.deepcopy(document)
    update["objects"][0]["name"] = "Replacement Mesh"
    update["objects"][0]["partType"] = "Ball"
    update["objects"][0]["size"] = [8, 9, 10]
    apply(2, update)
    replacement = next(obj for obj in bpy.data.objects if obj.get(reverse_sync.OBJECT_GUID_KEY) == object_id)
    assert replacement.name == "Replacement Mesh"
    assert len(replacement.data.vertices) != original_vertex_count
    assert "FINISHED" in bpy.ops.ed.undo()
    restored = next(obj for obj in bpy.data.objects if obj.get(reverse_sync.OBJECT_GUID_KEY) == object_id)
    assert restored.name == "Original Mesh"
    assert len(restored.data.vertices) == original_vertex_count
    print("AUTO_IMPORT_UNDO_RESTORES_MESH_OK")

elif MODE == "csg":
    source_id = "60000000-0000-4000-8000-000000000001"
    transform = [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]
    document = {
        "schema": "roblox-mesh-sync-reverse/4",
        "model": {"id": "60000000-0000-4000-8000-000000000002", "name": "CSG Undo Test", "rootKind": "STUDIO_SELECTION"},
        "transformMask": {"position": True, "rotation": True, "scale": True},
        "hierarchy": [], "meshes": [], "images": [], "appearances": [],
        "objects": [
            {"id": "positive", "name": "Positive", "kind": "PART", "partType": "Block", "size": [4, 4, 4], "cframe": transform},
            {"id": "negative", "name": "Negative", "kind": "PART", "partType": "Block", "size": [1, 1, 1], "cframe": transform},
        ],
        "csg": [{
            "id": "root-node", "name": "Original CSG", "op": "union",
            "size": [4, 4, 4], "cframe": transform,
            "appearance": appearance[0],
            "operands": [
                {"role": "positive", "kind": "instance", "ref": "positive"},
                {"role": "negative", "kind": "instance", "ref": "negative"},
            ],
        }],
        "csgRoots": [{"kind": "csg", "ref": "root-node", "name": "Original CSG", "sourceGuid": source_id}],
    }
    apply(1, document)
    original = next(
        obj for obj in bpy.data.objects
        if obj.get(reverse_sync.CSG_SOURCE_KEY) == source_id
        and obj.get(reverse_sync.CSG_ROLE_KEY) == "BAKED_RESULT" and not obj.hide_get()
    )
    original_modifier_count = len(original.modifiers)
    assert original_modifier_count == 0
    assert sum(obj.get(reverse_sync.CSG_SOURCE_KEY) == source_id for obj in bpy.data.objects) == 1
    update = copy.deepcopy(document)
    update["csgRoots"][0]["name"] = "Replacement CSG"
    apply(2, update)
    replacement = next(
        obj for obj in bpy.data.objects
        if obj.get(reverse_sync.CSG_SOURCE_KEY) == source_id
        and obj.get(reverse_sync.CSG_ROLE_KEY) == "BAKED_RESULT" and not obj.hide_get()
    )
    assert replacement.name == "Replacement CSG"
    assert "FINISHED" in bpy.ops.ed.undo()
    restored = next(
        obj for obj in bpy.data.objects
        if obj.get(reverse_sync.CSG_SOURCE_KEY) == source_id
        and obj.get(reverse_sync.CSG_ROLE_KEY) == "BAKED_RESULT" and not obj.hide_get()
    )
    assert restored.name == "Original CSG"
    assert len(restored.modifiers) == original_modifier_count
    assert sum(obj.get(reverse_sync.CSG_SOURCE_KEY) == source_id for obj in bpy.data.objects) == 1
    print("CSG_IMPORT_UNDO_RESTORES_BAKED_MESH_OK")

else:
    raise AssertionError(f"Unknown mode: {MODE}")
