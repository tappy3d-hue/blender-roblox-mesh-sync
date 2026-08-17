"""Preview geometry and mesh integrity helpers."""

from __future__ import annotations

import hashlib
import math

import bpy


def _roblox_to_blender(vertex):
    x, y, z = vertex
    return (x, -z, y)


def _block_geometry():
    vertices = [
        (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
        (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
    ]
    faces = [
        (0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
        (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7),
    ]
    return vertices, faces


def _ball_geometry(segments=24, rings=12):
    vertices = [(0.0, 0.0, -1.0)]
    for ring in range(1, rings):
        latitude = -math.pi / 2 + math.pi * ring / rings
        radius = math.cos(latitude)
        z = math.sin(latitude)
        for segment in range(segments):
            angle = 2 * math.pi * segment / segments
            vertices.append((radius * math.cos(angle), radius * math.sin(angle), z))
    top_index = len(vertices)
    vertices.append((0.0, 0.0, 1.0))

    faces = []
    first_ring = 1
    for segment in range(segments):
        faces.append((0, first_ring + (segment + 1) % segments, first_ring + segment))

    for ring in range(rings - 2):
        start = 1 + ring * segments
        next_start = start + segments
        for segment in range(segments):
            next_segment = (segment + 1) % segments
            faces.append((
                start + segment,
                start + next_segment,
                next_start + next_segment,
                next_start + segment,
            ))

    last_ring = 1 + (rings - 2) * segments
    for segment in range(segments):
        faces.append((last_ring + segment, last_ring + (segment + 1) % segments, top_index))
    return vertices, faces


def _cylinder_geometry(segments=32):
    # Roblox cylinders use their local X axis as their length axis, so the
    # Blender preview is authored along X as well.
    vertices = []
    for x in (-1.0, 1.0):
        for segment in range(segments):
            angle = 2 * math.pi * segment / segments
            vertices.append((x, math.cos(angle), math.sin(angle)))

    faces = []
    for segment in range(segments):
        next_segment = (segment + 1) % segments
        faces.append((segment, next_segment, segments + next_segment, segments + segment))
    faces.append(tuple(reversed(range(segments))))
    faces.append(tuple(range(segments, segments * 2)))
    return vertices, faces


def _tube_geometry(segments=16, inner_ratio=0.5):
    """Closed polygonal tube along local X, matching Roblox Cylinder's axis."""

    segments = max(3, min(64, int(segments)))
    inner_ratio = max(0.01, min(0.99, float(inner_ratio)))
    vertices = []
    for x in (-1.0, 1.0):
        for radius in (1.0, inner_ratio):
            for segment in range(segments):
                angle = math.tau * segment / segments
                vertices.append((x, math.cos(angle) * radius, math.sin(angle) * radius))

    outer_left = 0
    inner_left = segments
    outer_right = segments * 2
    inner_right = segments * 3
    faces = []
    for segment in range(segments):
        next_segment = (segment + 1) % segments
        faces.append((
            outer_left + segment,
            outer_left + next_segment,
            outer_right + next_segment,
            outer_right + segment,
        ))
        faces.append((
            inner_left + next_segment,
            inner_left + segment,
            inner_right + segment,
            inner_right + next_segment,
        ))
        faces.append((
            outer_left + next_segment,
            outer_left + segment,
            inner_left + segment,
            inner_left + next_segment,
        ))
        faces.append((
            outer_right + segment,
            outer_right + next_segment,
            inner_right + next_segment,
            inner_right + segment,
        ))
    return vertices, faces


def _wedge_geometry():
    roblox_vertices = [
        (-1, -1, -1), (1, -1, -1),
        (-1, -1, 1), (1, -1, 1),
        (-1, 1, 1), (1, 1, 1),
    ]
    faces = [
        (0, 1, 3, 2), (2, 3, 5, 4),
        (0, 2, 4), (1, 5, 3), (0, 4, 5, 1),
    ]
    return [_roblox_to_blender(vertex) for vertex in roblox_vertices], faces


def _corner_wedge_geometry():
    # Square base with an apex above one corner. This is the canonical visual
    # proxy for Roblox's two-slope CornerWedge primitive.
    roblox_vertices = [
        (-1, -1, -1), (1, -1, -1),
        (1, -1, 1), (-1, -1, 1),
        (1, 1, 1),
    ]
    faces = [
        (0, 1, 2, 3), (0, 4, 1), (1, 4, 2),
        (2, 4, 3), (3, 4, 0),
    ]
    return [_roblox_to_blender(vertex) for vertex in roblox_vertices], faces


GEOMETRY_BUILDERS = {
    "Block": _block_geometry,
    "Ball": _ball_geometry,
    "Cylinder": _cylinder_geometry,
    "Wedge": _wedge_geometry,
    "CornerWedge": _corner_wedge_geometry,
    "Tube": _tube_geometry,
}


def create_mesh(part_type: str, *, tube_segments=16, tube_inner_ratio=0.5):
    if part_type == "Tube":
        vertices, faces = _tube_geometry(tube_segments, tube_inner_ratio)
    else:
        vertices, faces = GEOMETRY_BUILDERS[part_type]()
    mesh = bpy.data.meshes.new(f"RBX_{part_type}_Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update(calc_edges=True)
    return mesh


def mesh_signature(mesh) -> str:
    digest = hashlib.sha256()
    for vertex in mesh.vertices:
        digest.update(",".join(f"{component:.6f}" for component in vertex.co).encode("ascii"))
        digest.update(b";")
    digest.update(b"|")
    for polygon in mesh.polygons:
        digest.update(",".join(str(index) for index in polygon.vertices).encode("ascii"))
        digest.update(b";")
    return digest.hexdigest()


def local_mesh_size(mesh):
    if not mesh.vertices:
        return (0.0, 0.0, 0.0)
    coordinates = [vertex.co for vertex in mesh.vertices]
    return tuple(
        max(coordinate[axis] for coordinate in coordinates)
        - min(coordinate[axis] for coordinate in coordinates)
        for axis in range(3)
    )


def mesh_surface_samples(mesh):
    """Sample vertices, edge midpoints, and face centers in local space."""

    samples = [tuple(vertex.co) for vertex in mesh.vertices]
    for edge in mesh.edges:
        first = mesh.vertices[edge.vertices[0]].co
        second = mesh.vertices[edge.vertices[1]].co
        samples.append(tuple((first + second) * 0.5))
    for polygon in mesh.polygons:
        coordinates = [mesh.vertices[index].co for index in polygon.vertices]
        if coordinates:
            center = sum(coordinates, coordinates[0].copy() * 0.0) / len(coordinates)
            samples.append(tuple(center))
    return samples


def mesh_orientation_bases(mesh, maximum_bases=32):
    """Build likely local orientation frames from face normals and edges."""

    identity = (
        (1.0, 0.0, 0.0),
        (0.0, 1.0, 0.0),
        (0.0, 0.0, 1.0),
    )
    directions = []

    def add_direction(vector):
        if vector.length <= 1e-8:
            return
        direction = vector.normalized()
        # Treat opposite directions as the same candidate.
        for component in direction:
            if abs(component) > 1e-6:
                if component < 0:
                    direction.negate()
                break
        if any(abs(direction.dot(existing)) > 0.9995 for existing in directions):
            return
        if len(directions) < 24:
            directions.append(direction)

    for polygon in mesh.polygons:
        add_direction(polygon.normal.copy())
    for edge in mesh.edges:
        first = mesh.vertices[edge.vertices[0]].co
        second = mesh.vertices[edge.vertices[1]].co
        add_direction(second - first)

    bases = [identity]
    for first_index, first in enumerate(directions):
        for second in directions[first_index + 1:]:
            dot = first.dot(second)
            if abs(dot) > 0.15:
                continue
            axis_x = first.normalized()
            axis_y = (second - axis_x * dot).normalized()
            axis_z = axis_x.cross(axis_y).normalized()
            basis = (
                (axis_x.x, axis_y.x, axis_z.x),
                (axis_x.y, axis_y.y, axis_z.y),
                (axis_x.z, axis_y.z, axis_z.z),
            )
            bases.append(basis)
            if len(bases) >= maximum_bases:
                return tuple(bases)
    return tuple(bases)
