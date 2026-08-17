"""Run with Blender --background to verify texture-free plastic previews."""

from __future__ import annotations

from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blender_extension.material_preview import preview_material


for material_name, expected_roughness in (("Plastic", 0.8), ("SmoothPlastic", 0.25)):
    material = preview_material(material_name, (0.5, 0.25, 0.1), 0.0, 1.0)
    nodes = material.node_tree.nodes
    assert not any(node.type == "TEX_IMAGE" for node in nodes), material_name
    assert not any(node.type == "NORMAL_MAP" for node in nodes), material_name
    assert not any(node.type in {"NEW_GEOMETRY", "VECT_MATH"} for node in nodes), material_name
    shader = next(node for node in nodes if node.type == "BSDF_PRINCIPLED")
    actual = shader.inputs["Roughness"].default_value
    assert abs(actual - expected_roughness) < 1e-6, (material_name, actual)

print("PLASTIC_PREVIEW_OK")
