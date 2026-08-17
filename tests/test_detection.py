from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
DETECTION_PATH = ROOT / "blender_extension" / "detection.py"
SPEC = importlib.util.spec_from_file_location("rbx_primitive_detection", DETECTION_PATH)
detection = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = detection
SPEC.loader.exec_module(detection)


def polyhedron_samples(vertices, faces):
    samples = list(vertices)
    edges = set()
    for face in faces:
        samples.append(tuple(sum(vertices[index][axis] for index in face) / len(face) for axis in range(3)))
        for offset, first in enumerate(face):
            second = face[(offset + 1) % len(face)]
            edges.add(tuple(sorted((first, second))))
    for first, second in edges:
        samples.append(tuple((vertices[first][axis] + vertices[second][axis]) * 0.5 for axis in range(3)))
    return samples


def ball_samples():
    samples = []
    for latitude_degrees in (-90, -60, -30, 0, 30, 60, 90):
        latitude = math.radians(latitude_degrees)
        for longitude_degrees in range(0, 360, 30):
            longitude = math.radians(longitude_degrees)
            samples.append((
                math.cos(latitude) * math.cos(longitude),
                math.cos(latitude) * math.sin(longitude),
                math.sin(latitude),
            ))
    return samples


def cylinder_samples():
    samples = []
    for x in (-1.0, 0.0, 1.0):
        for degrees in range(0, 360, 15):
            angle = math.radians(degrees)
            samples.append((x, math.cos(angle), math.sin(angle)))
    samples.extend(((-1, 0, 0), (1, 0, 0)))
    return samples


def tube_vertices(segments=16, inner_ratio=0.45):
    vertices = []
    for x in (-1.0, 1.0):
        for radius in (1.0, inner_ratio):
            for segment in range(segments):
                angle = math.tau * segment / segments
                vertices.append((x, math.cos(angle) * radius, math.sin(angle) * radius))
    return vertices


class DetectionTests(unittest.TestCase):
    def test_detects_polyhedron_shapes(self):
        cases = (
            ("Block", detection.BLOCK_VERTICES, detection.BLOCK_FACES),
            ("Wedge", detection.WEDGE_VERTICES, detection.WEDGE_FACES),
            ("CornerWedge", detection.CORNER_WEDGE_VERTICES, detection.CORNER_WEDGE_FACES),
        )
        for expected, vertices, faces in cases:
            with self.subTest(expected=expected):
                result = detection.detect_primitive(polyhedron_samples(vertices, faces))
                self.assertEqual(result.part_type, expected)
                self.assertLess(result.error, 1e-8)

    def test_detects_ball(self):
        result = detection.detect_primitive(ball_samples())
        self.assertEqual(result.part_type, "Ball")
        self.assertLess(result.error, 1e-8)

    def test_detects_cylinder_and_its_axis(self):
        result = detection.detect_primitive(cylinder_samples())
        self.assertEqual(result.part_type, "Cylinder")
        self.assertLess(result.error, 1e-8)

        # Move canonical X to source Z to represent Blender's usual Z-axis cylinder.
        reoriented = [(y, z, x) for x, y, z in cylinder_samples()]
        result = detection.detect_primitive(reoriented)
        self.assertEqual(result.part_type, "Cylinder")
        self.assertLess(result.error, 1e-8)

    def test_detects_clean_tube_and_parameters(self):
        result = detection.detect_tube_vertices(tube_vertices(20, 0.4))
        self.assertEqual(result.part_type, "Tube")
        self.assertEqual(result.tube_segments, 20)
        self.assertAlmostEqual(result.tube_inner_ratio, 0.4, places=6)
        self.assertLess(result.error, 1e-6)

    def test_tube_detector_rejects_solid_cylinder(self):
        with self.assertRaises(ValueError):
            detection.detect_tube_vertices(cylinder_samples())

    def test_manual_type_limits_detection(self):
        samples = polyhedron_samples(detection.BLOCK_VERTICES, detection.BLOCK_FACES)
        result = detection.detect_primitive(samples, "Block")
        self.assertEqual(result.part_type, "Block")

    def test_detects_applied_arbitrary_rotation_with_source_basis(self):
        angle = math.radians(31)
        cosine = math.cos(angle)
        sine = math.sin(angle)
        source_basis = (
            (cosine, -sine, 0.0),
            (sine, cosine, 0.0),
            (0.0, 0.0, 1.0),
        )
        samples = polyhedron_samples(detection.BLOCK_VERTICES, detection.BLOCK_FACES)
        rotated = [
            tuple(
                sum(source_basis[row][column] * point[column] for column in range(3))
                for row in range(3)
            )
            for point in samples
        ]
        result = detection.detect_primitive(rotated, source_bases=(source_basis,))
        self.assertEqual(result.part_type, "Block")
        self.assertLess(result.error, 1e-8)


if __name__ == "__main__":
    unittest.main()
