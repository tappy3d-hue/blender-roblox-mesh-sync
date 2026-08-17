from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "blender_extension" / "tube.py"
SPEC = importlib.util.spec_from_file_location("rbx_tube", MODULE_PATH)
tube = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(tube)


def determinant(matrix):
    return (
        matrix[0][0] * (matrix[1][1] * matrix[2][2] - matrix[1][2] * matrix[2][1])
        - matrix[0][1] * (matrix[1][0] * matrix[2][2] - matrix[1][2] * matrix[2][0])
        + matrix[0][2] * (matrix[1][0] * matrix[2][1] - matrix[1][1] * matrix[2][0])
    )


class TubeTests(unittest.TestCase):
    def test_each_segment_expands_to_four_wedges(self):
        for segments in (3, 8, 16, 64):
            with self.subTest(segments=segments):
                components = tube.tube_wedge_components((4, 8, 6), 0.45, segments)
                self.assertEqual(len(components), tube.estimated_tube_part_count(segments))

    def test_wedge_cross_section_area_matches_polygonal_tube(self):
        segments = 16
        ratio = 0.4
        diameter_y, diameter_z = 8.0, 6.0
        components = tube.tube_wedge_components((5, diameter_y, diameter_z), ratio, segments)
        wedge_area = sum(component["size"][1] * component["size"][2] * 0.5 for component in components)
        outer_area = (
            segments * 0.5 * (diameter_y * 0.5) * (diameter_z * 0.5)
            * math.sin(math.tau / segments)
        )
        expected = outer_area * (1.0 - ratio * ratio)
        self.assertAlmostEqual(wedge_area, expected, places=7)

    def test_component_frames_are_right_handed(self):
        for component in tube.tube_wedge_components((4, 8, 6), 0.5, 12):
            self.assertAlmostEqual(determinant(component["rotation"]), 1.0, places=7)
            self.assertTrue(all(value > 0 for value in component["size"]))

    def test_cframe_composition(self):
        parent = [10, 20, 30, 1, 0, 0, 0, 1, 0, 0, 0, 1]
        rotation = ((1, 0, 0), (0, 0, -1), (0, 1, 0))
        result = tube.compose_cframe(parent, (0, 2, 3), rotation)
        self.assertEqual(result[:3], [10.0, 22.0, 33.0])
        self.assertEqual(result[3:], [value for row in rotation for value in row])


if __name__ == "__main__":
    unittest.main()
