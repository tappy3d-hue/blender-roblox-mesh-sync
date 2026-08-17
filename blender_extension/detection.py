"""Geometry-only primitive detection used by Blender conversion operators.

The module intentionally has no bpy dependency so its math can be unit tested
outside Blender.
"""

from __future__ import annotations

from dataclasses import dataclass
import itertools
import math
from typing import Iterable, Sequence


Vector = tuple[float, float, float]
Matrix3 = tuple[Vector, Vector, Vector]


@dataclass(frozen=True)
class DetectionResult:
    part_type: str
    error: float
    # Maps canonical primitive coordinates to normalized source coordinates.
    canonical_to_source: Matrix3
    source_center: Vector
    source_half_size: Vector
    # Maps an oriented analysis frame to the source mesh's local coordinates.
    source_basis: Matrix3
    tube_inner_ratio: float = 0.0
    tube_segments: int = 0


def _subtract(left: Sequence[float], right: Sequence[float]) -> Vector:
    return tuple(float(left[index]) - float(right[index]) for index in range(3))


def _cross(left: Sequence[float], right: Sequence[float]) -> Vector:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(float(left[index]) * float(right[index]) for index in range(3))


def _length(vector: Sequence[float]) -> float:
    return math.sqrt(_dot(vector, vector))


def normalize_points(points: Iterable[Sequence[float]]):
    points = [tuple(float(value) for value in point) for point in points]
    if not points:
        raise ValueError("Mesh has no geometry samples")
    minimum = tuple(min(point[axis] for point in points) for axis in range(3))
    maximum = tuple(max(point[axis] for point in points) for axis in range(3))
    center = tuple((minimum[axis] + maximum[axis]) * 0.5 for axis in range(3))
    half_size = tuple((maximum[axis] - minimum[axis]) * 0.5 for axis in range(3))
    if any(value <= 1e-9 for value in half_size):
        raise ValueError("Mesh has a zero-size axis")
    normalized = [
        tuple((point[axis] - center[axis]) / half_size[axis] for axis in range(3))
        for point in points
    ]
    return normalized, center, half_size


def _determinant(matrix: Matrix3) -> float:
    return (
        matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1])
        - matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0])
        + matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0])
    )


def signed_permutations():
    """Return axis permutations that preserve handedness (proper rotations)."""

    results = []
    for axes in itertools.permutations(range(3)):
        for signs in itertools.product((-1.0, 1.0), repeat=3):
            rows = []
            for source_axis in range(3):
                row = [0.0, 0.0, 0.0]
                canonical_axis = axes[source_axis]
                row[canonical_axis] = signs[source_axis]
                rows.append(tuple(row))
            matrix = tuple(rows)
            if _determinant(matrix) > 0.5:
                results.append(matrix)
    return tuple(results)


SIGNED_PERMUTATIONS = signed_permutations()
IDENTITY_BASIS: Matrix3 = (
    (1.0, 0.0, 0.0),
    (0.0, 1.0, 0.0),
    (0.0, 0.0, 1.0),
)


def _source_to_canonical(point: Sequence[float], canonical_to_source: Matrix3) -> Vector:
    # Inverse of an orthogonal signed permutation is its transpose.
    return tuple(
        sum(canonical_to_source[source_axis][canonical_axis] * point[source_axis] for source_axis in range(3))
        for canonical_axis in range(3)
    )


def _polyhedron_planes(vertices, faces):
    centroid = tuple(sum(vertex[axis] for vertex in vertices) / len(vertices) for axis in range(3))
    planes = []
    for face in faces:
        first, second, third = (vertices[index] for index in face[:3])
        normal = _cross(_subtract(second, first), _subtract(third, first))
        magnitude = _length(normal)
        if magnitude <= 1e-9:
            continue
        normal = tuple(component / magnitude for component in normal)
        offset = -_dot(normal, first)
        if _dot(normal, centroid) + offset > 0:
            normal = tuple(-component for component in normal)
            offset = -offset
        planes.append((normal, offset))
    return tuple(planes)


BLOCK_VERTICES = (
    (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
    (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
)
BLOCK_FACES = (
    (0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
    (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7),
)
WEDGE_VERTICES = (
    (-1, 1, -1), (1, 1, -1),
    (-1, -1, -1), (1, -1, -1),
    (-1, -1, 1), (1, -1, 1),
)
WEDGE_FACES = (
    (0, 1, 3, 2), (2, 3, 5, 4),
    (0, 2, 4), (1, 5, 3), (0, 4, 5, 1),
)
CORNER_WEDGE_VERTICES = (
    (-1, 1, -1), (1, 1, -1),
    (1, -1, -1), (-1, -1, -1),
    (1, -1, 1),
)
CORNER_WEDGE_FACES = (
    (0, 1, 2, 3), (0, 4, 1), (1, 4, 2),
    (2, 4, 3), (3, 4, 0),
)

POLYHEDRON_PLANES = {
    "Block": _polyhedron_planes(BLOCK_VERTICES, BLOCK_FACES),
    "Wedge": _polyhedron_planes(WEDGE_VERTICES, WEDGE_FACES),
    "CornerWedge": _polyhedron_planes(CORNER_WEDGE_VERTICES, CORNER_WEDGE_FACES),
}


def _polyhedron_error(points, planes) -> float:
    maximum_error = 0.0
    for point in points:
        distances = [_dot(normal, point) + offset for normal, offset in planes]
        surface_error = min(abs(distance) for distance in distances)
        inside_error = max(0.0, max(distances))
        maximum_error = max(maximum_error, surface_error, inside_error)
    return maximum_error


def _ball_error(points) -> float:
    return max(abs(_length(point) - 1.0) for point in points)


def _cylinder_error(points) -> float:
    maximum_error = 0.0
    for x, y, z in points:
        radius = math.sqrt(y * y + z * z)
        surface_error = min(abs(abs(x) - 1.0), abs(radius - 1.0))
        inside_error = max(0.0, abs(x) - 1.0, radius - 1.0)
        maximum_error = max(maximum_error, surface_error, inside_error)
    return maximum_error


def _candidate_error(part_type: str, points) -> float:
    if part_type == "Ball":
        return _ball_error(points)
    if part_type == "Cylinder":
        return _cylinder_error(points)
    return _polyhedron_error(points, POLYHEDRON_PLANES[part_type])


def _basis_to_source(vector: Sequence[float], basis: Matrix3) -> Vector:
    return tuple(
        sum(basis[source_axis][frame_axis] * vector[frame_axis] for frame_axis in range(3))
        for source_axis in range(3)
    )


def detect_primitive(
    points,
    requested_type: str = "AUTO",
    source_bases: Iterable[Matrix3] | None = None,
) -> DetectionResult:
    points = [tuple(float(value) for value in point) for point in points]
    part_types = (
        ("Block", "Ball", "Cylinder", "Wedge", "CornerWedge")
        if requested_type == "AUTO"
        else (requested_type,)
    )
    best = None
    bases = tuple(source_bases or (IDENTITY_BASIS,))
    for basis in bases:
        frame_points = [_source_to_canonical(point, basis) for point in points]
        normalized, frame_center, half_size = normalize_points(frame_points)
        source_center = _basis_to_source(frame_center, basis)
        for part_type in part_types:
            for permutation in SIGNED_PERMUTATIONS:
                canonical_points = [_source_to_canonical(point, permutation) for point in normalized]
                error = _candidate_error(part_type, canonical_points)
                candidate = DetectionResult(
                    part_type,
                    error,
                    permutation,
                    source_center,
                    half_size,
                    basis,
                )
                if best is None or candidate.error < best.error:
                    best = candidate
    if best is None:
        raise ValueError(f"Unsupported requested type: {requested_type}")
    return best


def detect_tube_vertices(
    points,
    source_bases: Iterable[Matrix3] | None = None,
) -> DetectionResult:
    """Detect a straight two-ring polygonal tube from its mesh vertices.

    The first version intentionally targets clean static tube topology: matching
    inner and outer rings on the two end planes, with no longitudinal cuts or
    bevel vertices.
    """

    unique_points = sorted({
        tuple(round(float(value), 7) for value in point)
        for point in points
    })
    if len(unique_points) < 12:
        raise ValueError("Tube requires at least three inner and outer segments")

    best = None
    bases = tuple(source_bases or (IDENTITY_BASIS,))
    for basis in bases:
        frame_points = [_source_to_canonical(point, basis) for point in unique_points]
        try:
            normalized, frame_center, half_size = normalize_points(frame_points)
        except ValueError:
            continue
        source_center = _basis_to_source(frame_center, basis)
        for permutation in SIGNED_PERMUTATIONS:
            canonical = [_source_to_canonical(point, permutation) for point in normalized]
            x_error = max(abs(abs(point[0]) - 1.0) for point in canonical)
            if x_error > 0.2:
                continue
            radii = [math.hypot(point[1], point[2]) for point in canonical]
            inner_center, outer_center = min(radii), max(radii)
            if outer_center <= 1e-8 or outer_center - inner_center < 0.08:
                continue
            for _iteration in range(8):
                inner_values, outer_values = [], []
                midpoint = (inner_center + outer_center) * 0.5
                for radius in radii:
                    (inner_values if radius <= midpoint else outer_values).append(radius)
                if not inner_values or not outer_values:
                    break
                inner_center = sum(inner_values) / len(inner_values)
                outer_center = sum(outer_values) / len(outer_values)
            if len(inner_values) != len(outer_values) or len(inner_values) < 6:
                continue
            ratio = inner_center / outer_center
            if not 0.01 < ratio < 0.99:
                continue

            midpoint = (inner_center + outer_center) * 0.5
            inner_points = [point for point, radius in zip(canonical, radii) if radius <= midpoint]
            outer_points = [point for point, radius in zip(canonical, radii) if radius > midpoint]

            def direction_keys(values):
                keys = set()
                positive, negative = 0, 0
                for x, y, z in values:
                    radius = math.hypot(y, z)
                    if radius <= 1e-8:
                        continue
                    keys.add((round(y / radius, 4), round(z / radius, 4)))
                    if x >= 0:
                        positive += 1
                    else:
                        negative += 1
                return keys, positive, negative

            inner_keys, inner_positive, inner_negative = direction_keys(inner_points)
            outer_keys, outer_positive, outer_negative = direction_keys(outer_points)
            if inner_keys != outer_keys or len(inner_keys) < 3 or len(inner_keys) > 64:
                continue
            segments = len(inner_keys)
            if any(count != segments for count in (
                inner_positive, inner_negative, outer_positive, outer_negative,
            )):
                continue
            radial_error = max(
                min(abs(radius - inner_center), abs(radius - outer_center)) / outer_center
                for radius in radii
            )
            candidate = DetectionResult(
                "Tube",
                max(x_error, radial_error),
                permutation,
                source_center,
                half_size,
                basis,
                ratio,
                segments,
            )
            if best is None or candidate.error < best.error:
                best = candidate
    if best is None:
        raise ValueError("Mesh is not a clean straight tube")
    return best
