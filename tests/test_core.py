from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "blender_extension" / "core.py"
SPEC = importlib.util.spec_from_file_location("rbx_primitive_core", CORE_PATH)
core = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(core)


class CoordinateTests(unittest.TestCase):
    def test_position_axis_mapping(self):
        self.assertEqual(core.convert_position((1, 2, 3), 2), (2, 6, -4))

    def test_size_axis_mapping(self):
        self.assertEqual(core.convert_size((2, 4, 6), 2), (4, 12, 8))

    def test_identity_rotation_stays_identity(self):
        identity = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
        self.assertEqual(core.convert_rotation(identity), identity)

    def test_blender_z_rotation_becomes_roblox_y_rotation(self):
        blender_z_90 = ((0, -1, 0), (1, 0, 0), (0, 0, 1))
        roblox_y_90 = ((0, 0, 1), (0, 1, 0), (-1, 0, 0))
        self.assertEqual(core.convert_rotation(blender_z_90), roblox_y_90)

    def test_cframe_has_twelve_numbers(self):
        identity = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
        self.assertEqual(len(core.make_cframe((0, 0, 0), identity, 1)), 12)

    def test_reverse_coordinate_mapping_round_trip(self):
        position = (2.5, -3.0, 7.25)
        converted = core.convert_position(position, 2.0)
        self.assertEqual(core.reverse_position(converted, 2.0), position)
        size = (2.0, 3.0, 4.0)
        self.assertEqual(core.reverse_size(core.convert_size(size, 2.0), 2.0), size)

    def test_reverse_rotation_round_trip(self):
        rotation = ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        self.assertEqual(core.reverse_rotation(core.convert_rotation(rotation)), rotation)

    def test_blender_linear_color_exports_as_roblox_srgb(self):
        expected_srgb = (0xD3 / 255, 0x95 / 255, 0x60 / 255)
        blender_linear = core.srgb_color_to_linear(expected_srgb)
        exported = core.linear_color_to_srgb(blender_linear)
        self.assertEqual(tuple(round(value * 255) for value in exported), (0xD3, 0x95, 0x60))

    def test_roblox_color_round_trip(self):
        source = (0.12, 0.48, 0.91)
        restored = core.linear_color_to_srgb(core.srgb_color_to_linear(source))
        for actual, expected in zip(restored, source):
            self.assertAlmostEqual(actual, expected, places=7)


class FixtureTests(unittest.TestCase):
    def test_example_uses_supported_schema(self):
        fixture = ROOT / "examples" / "ExampleModel.rbxprimitives.json"
        document = json.loads(fixture.read_text(encoding="utf-8"))
        self.assertEqual(document["schema"], core.SCHEMA_ID)
        self.assertGreater(len(document["parts"]), 0)
        for part in document["parts"]:
            self.assertIn(part["type"], core.SUPPORTED_PART_TYPES)
            self.assertEqual(len(part["size"]), 3)
            self.assertEqual(len(part["cframe"]), 12)
            self.assertEqual(len(part["color"]), 3)


if __name__ == "__main__":
    unittest.main()
