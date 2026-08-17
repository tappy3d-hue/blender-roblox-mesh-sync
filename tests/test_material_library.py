from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIBRARY_PATH = ROOT / "blender_extension" / "material_library.py"
SPEC = importlib.util.spec_from_file_location("rbx_material_library", LIBRARY_PATH)
library = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(library)


class MaterialLibraryTests(unittest.TestCase):
    def test_library_is_limited_to_supplied_materials(self):
        self.assertEqual(len(library.MATERIAL_NAMES), 43)
        self.assertNotIn("Air", library.MATERIAL_NAMES)
        self.assertNotIn("Water", library.MATERIAL_NAMES)

    def test_every_material_has_bundled_mtl(self):
        for material_name in library.MATERIAL_NAMES:
            with self.subTest(material=material_name):
                files = library.material_files(material_name)
                self.assertTrue(files["mtl"].is_file())

    def test_texture_map_coverage_matches_export(self):
        untextured = {"ForceField", "Neon"}
        no_diffuse = {"DiamondPlate", "Foil", "Plastic", "SmoothPlastic"}
        no_specular = {"Plastic", "SmoothPlastic"}
        no_normal = {"Plastic", "SmoothPlastic"}
        for material_name in library.MATERIAL_NAMES:
            with self.subTest(material=material_name):
                files = library.material_files(material_name)
                if material_name in untextured:
                    self.assertIsNone(files["diffuse"])
                    self.assertIsNone(files["normal"])
                    self.assertIsNone(files["specular"])
                    continue
                self.assertEqual(files["normal"] is None, material_name in no_normal)
                self.assertEqual(files["diffuse"] is None, material_name in no_diffuse)
                self.assertEqual(files["specular"] is None, material_name in no_specular)

    def test_exported_mtl_values_are_readable(self):
        metal = library.parse_mtl(library.material_files("Metal")["mtl"])
        self.assertEqual(metal["shininess"], 255.0)
        self.assertEqual(len(metal["specular"]), 3)

    def test_plastic_roughness_is_fixed(self):
        self.assertEqual(library.FIXED_ROUGHNESS["Plastic"], 0.8)
        self.assertEqual(library.FIXED_ROUGHNESS["SmoothPlastic"], 0.25)


if __name__ == "__main__":
    unittest.main()
