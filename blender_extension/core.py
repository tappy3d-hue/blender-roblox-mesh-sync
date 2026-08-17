"""Pure coordinate and schema helpers that do not depend on Blender."""

from __future__ import annotations

from typing import Iterable, Sequence

SCHEMA_ID = "roblox-primitives/1"
STANDARD_PART_TYPES = ("Block", "Ball", "Cylinder", "Wedge", "CornerWedge")
SUPPORTED_PART_TYPES = (*STANDARD_PART_TYPES, "Tube")
MIN_PART_SIZE = 0.001
MAX_PART_SIZE = 2048.0

# Maps Blender vector components to Roblox components:
# (x, y, z) -> (x, z, -y)
AXIS_CONVERSION = (
    (1.0, 0.0, 0.0),
    (0.0, 0.0, 1.0),
    (0.0, -1.0, 0.0),
)


def _matmul(left: Sequence[Sequence[float]], right: Sequence[Sequence[float]]):
    return tuple(
        tuple(sum(left[row][k] * right[k][column] for k in range(3)) for column in range(3))
        for row in range(3)
    )


def _transpose(matrix: Sequence[Sequence[float]]):
    return tuple(tuple(matrix[column][row] for column in range(3)) for row in range(3))


def convert_position(location: Sequence[float], studs_per_unit: float):
    x, y, z = (float(value) for value in location)
    scale = float(studs_per_unit)
    return (x * scale, z * scale, -y * scale)


def convert_rotation(rotation: Sequence[Sequence[float]]):
    """Convert a Blender 3x3 rotation matrix to Roblox coordinates."""

    converted = _matmul(_matmul(AXIS_CONVERSION, rotation), _transpose(AXIS_CONVERSION))
    return converted


def convert_size(size_xyz: Sequence[float], studs_per_unit: float):
    x, y, z = (abs(float(value)) for value in size_xyz)
    scale = float(studs_per_unit)
    return (x * scale, z * scale, y * scale)


def reverse_position(location: Sequence[float], studs_per_unit: float):
    """Convert a Roblox position to Blender coordinates."""

    x, y, z = (float(value) for value in location)
    scale = float(studs_per_unit)
    return (x / scale, -z / scale, y / scale)


def reverse_rotation(rotation: Sequence[Sequence[float]]):
    """Convert a Roblox 3x3 rotation matrix to Blender coordinates."""

    return _matmul(_matmul(_transpose(AXIS_CONVERSION), rotation), AXIS_CONVERSION)


def reverse_size(size_xyz: Sequence[float], studs_per_unit: float):
    x, y, z = (abs(float(value)) for value in size_xyz)
    scale = float(studs_per_unit)
    return (x / scale, z / scale, y / scale)


def make_cframe(location: Sequence[float], rotation: Sequence[Sequence[float]], studs_per_unit: float):
    position = convert_position(location, studs_per_unit)
    converted_rotation = convert_rotation(rotation)
    return [
        *position,
        *(converted_rotation[row][column] for row in range(3) for column in range(3)),
    ]


def rounded(values: Iterable[float], digits: int = 7):
    return [round(float(value), digits) for value in values]


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def linear_channel_to_srgb(value: float) -> float:
    value = _clamp_unit(value)
    return 12.92 * value if value <= 0.0031308 else 1.055 * (value ** (1.0 / 2.4)) - 0.055


def srgb_channel_to_linear(value: float) -> float:
    value = _clamp_unit(value)
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def linear_color_to_srgb(color: Sequence[float]):
    """Convert Blender scene-linear RGB to the normalized sRGB used by Roblox Color3."""

    return tuple(linear_channel_to_srgb(color[index]) for index in range(3))


def srgb_color_to_linear(color: Sequence[float]):
    """Convert normalized Roblox Color3 values to Blender scene-linear RGB."""

    return tuple(srgb_channel_to_linear(color[index]) for index in range(3))
