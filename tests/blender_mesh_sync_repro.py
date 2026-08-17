"""Blender integration repro for shared Mesh data hashing.

Run with Blender's bundled Python:
    blender --background --factory-startup --python tests/blender_mesh_sync_repro.py
"""

from __future__ import annotations

import math
from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import blender_extension  # noqa: E402
from blender_extension.mesh_sync import build_selection_document  # noqa: E402


def cube_mesh(name: str):
    vertices = (
        (-1, -1, -1), (-1, -1, 1), (-1, 1, -1), (-1, 1, 1),
        (1, -1, -1), (1, -1, 1), (1, 1, -1), (1, 1, 1),
    )
    faces = (
        (0, 4, 6, 2), (1, 3, 7, 5), (0, 1, 5, 4),
        (2, 6, 7, 3), (0, 2, 3, 1), (4, 5, 7, 6),
    )
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, (), faces)
    mesh.update()
    return mesh


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    blender_extension.register()
    shared = cube_mesh("SharedCube")
    objects = []
    for index in range(5):
        obj = bpy.data.objects.new(f"Shared_{index}", shared)
        bpy.context.scene.collection.objects.link(obj)
        obj.location = (index * 3.0, index * -1.25, index * 0.5)
        obj.rotation_euler = (index * 0.1, index * 0.2, index * 0.3)
        obj.scale = (1.0 + index * 0.2, 0.75 + index * 0.1, 1.5 + index * 0.15)
        obj.select_set(True)
        objects.append(obj)
    bpy.context.view_layer.objects.active = objects[-1]
    bpy.context.view_layer.update()

    observed = set()
    for _ in range(100):
        document, _mesh_blobs, _image_blobs = build_selection_document(bpy.context)
        hashes = tuple(instance["meshHash"] for instance in document["instances"])
        assert len(document["meshes"]) == 1, hashes
        assert len(set(hashes)) == 1, hashes
        observed.add(hashes[0])

    assert len(observed) == 1, observed

    uv_layer = shared.uv_layers.new(name="UVMap")
    for loop in shared.loops:
        vertex = shared.vertices[loop.vertex_index].co
        uv_layer.data[loop.index].uv = ((vertex.x + 1.0) * 0.5, (vertex.y + 1.0) * 0.5)
    for obj in objects:
        bevel = obj.modifiers.new(name="Bevel", type="BEVEL")
        bevel.width = 0.2
        bevel.segments = 3
    bpy.context.view_layer.update()

    for _ in range(100):
        document, _mesh_blobs, _image_blobs = build_selection_document(bpy.context)
        hashes = tuple(instance["meshHash"] for instance in document["instances"])
        assert len(document["meshes"]) == 1, hashes
        assert len(set(hashes)) == 1, hashes

    objects[-1].modifiers["Bevel"].width = 0.35
    bpy.context.view_layer.update()
    document, _mesh_blobs, _image_blobs = build_selection_document(bpy.context)
    assert len(document["meshes"]) == 2, document["meshes"]
    print("SHARED_MESH_REPRO_OK base=100 bevel=100 instances=5 uniqueMeshes=1 differingBevel=2")


if __name__ == "__main__":
    main()
