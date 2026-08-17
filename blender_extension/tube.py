"""Pure geometry helpers for expanding a polygonal tube into WedgeParts."""

from __future__ import annotations

import math
from typing import Sequence


EPSILON = 1e-9


def _subtract(left, right):
    return tuple(float(left[index]) - float(right[index]) for index in range(2))


def _add(left, right):
    return tuple(float(left[index]) + float(right[index]) for index in range(2))


def _scale(vector, amount):
    return tuple(float(component) * float(amount) for component in vector)


def _dot(left, right):
    return sum(float(left[index]) * float(right[index]) for index in range(2))


def _length(vector):
    return math.sqrt(_dot(vector, vector))


def _right_triangle_wedge(right, first, second, length):
    """Map one right triangle in Roblox local YZ space to one WedgePart."""

    first_leg = _subtract(first, right)
    second_leg = _subtract(second, right)
    first_length = _length(first_leg)
    second_length = _length(second_leg)
    if first_length <= EPSILON or second_length <= EPSILON:
        return None
    first_direction = _scale(first_leg, 1.0 / first_length)
    second_direction = _scale(second_leg, 1.0 / second_length)

    # Wedge local +Y is one leg and local -Z is the other. Pick the assignment
    # which keeps the full 3D basis right-handed around the tube's local +X.
    cross_x_second = (-second_direction[1], second_direction[0])
    if _dot(cross_x_second, _scale(first_direction, -1.0)) >= 0.0:
        y_direction = second_direction
        z_direction = _scale(first_direction, -1.0)
        size_y, size_z = second_length, first_length
    else:
        y_direction = first_direction
        z_direction = _scale(second_direction, -1.0)
        size_y, size_z = first_length, second_length

    center = _scale(_add(first, second), 0.5)
    rotation = (
        (1.0, 0.0, 0.0),
        (0.0, y_direction[0], z_direction[0]),
        (0.0, y_direction[1], z_direction[1]),
    )
    return {
        "center": (0.0, center[0], center[1]),
        "rotation": rotation,
        "size": (float(length), size_y, size_z),
    }


def _triangle_wedges(first, second, third, length):
    """Split an arbitrary triangle along the altitude to its longest side."""

    points = (first, second, third)
    sides = (
        (_length(_subtract(second, first)), first, second, third),
        (_length(_subtract(third, second)), second, third, first),
        (_length(_subtract(first, third)), third, first, second),
    )
    _side_length, base_start, base_end, apex = max(sides, key=lambda item: item[0])
    base = _subtract(base_end, base_start)
    denominator = _dot(base, base)
    if denominator <= EPSILON:
        return []
    projection = max(0.0, min(1.0, _dot(_subtract(apex, base_start), base) / denominator))
    foot = _add(base_start, _scale(base, projection))
    wedges = (
        _right_triangle_wedge(foot, base_start, apex, length),
        _right_triangle_wedge(foot, apex, base_end, length),
    )
    return [wedge for wedge in wedges if wedge is not None]


def tube_wedge_components(size: Sequence[float], inner_ratio: float, segments: int):
    """Return exact WedgePart boxes for a polygonal elliptical tube.

    ``size`` is the Roblox-local bounding size ``(X length, Y diameter,
    Z diameter)``. The returned centers and rotations are relative to the
    logical tube CFrame.
    """

    length, diameter_y, diameter_z = (float(value) for value in size)
    inner_ratio = float(inner_ratio)
    segments = int(segments)
    if length <= 0 or diameter_y <= 0 or diameter_z <= 0:
        raise ValueError("Tube size must be positive")
    if not 0.0 < inner_ratio < 1.0:
        raise ValueError("Tube inner ratio must be between 0 and 1")
    if segments < 3 or segments > 64:
        raise ValueError("Tube segments must be between 3 and 64")

    radius_y = diameter_y * 0.5
    radius_z = diameter_z * 0.5

    def point(angle, ratio):
        # Blender local (x, cos(a), sin(a)) becomes Roblox local
        # (x, sin(a), -cos(a)) after the add-on's axis conversion.
        return (
            math.sin(angle) * radius_y * ratio,
            -math.cos(angle) * radius_z * ratio,
        )

    components = []
    for segment in range(segments):
        first_angle = math.tau * segment / segments
        second_angle = math.tau * (segment + 1) / segments
        outer_first = point(first_angle, 1.0)
        outer_second = point(second_angle, 1.0)
        inner_first = point(first_angle, inner_ratio)
        inner_second = point(second_angle, inner_ratio)
        triangles = (
            (outer_first, outer_second, inner_second),
            (outer_first, inner_second, inner_first),
        )
        for triangle_index, triangle in enumerate(triangles):
            for wedge_index, wedge in enumerate(_triangle_wedges(*triangle, length)):
                wedge["segment"] = segment
                wedge["triangle"] = triangle_index
                wedge["wedge"] = wedge_index
                components.append(wedge)
    return components


def estimated_tube_part_count(segments: int) -> int:
    return int(segments) * 4


def compose_cframe(parent: Sequence[float], center, rotation):
    """Compose a 12-number Roblox CFrame with a relative component frame."""

    if len(parent) != 12:
        raise ValueError("Parent CFrame must have 12 numbers")
    parent_position = tuple(float(value) for value in parent[:3])
    parent_rotation = tuple(
        tuple(float(parent[3 + row * 3 + column]) for column in range(3))
        for row in range(3)
    )
    world_position = tuple(
        parent_position[row]
        + sum(parent_rotation[row][axis] * float(center[axis]) for axis in range(3))
        for row in range(3)
    )
    world_rotation = tuple(
        tuple(
            sum(parent_rotation[row][axis] * float(rotation[axis][column]) for axis in range(3))
            for column in range(3)
        )
        for row in range(3)
    )
    return [
        *world_position,
        *(world_rotation[row][column] for row in range(3) for column in range(3)),
    ]
