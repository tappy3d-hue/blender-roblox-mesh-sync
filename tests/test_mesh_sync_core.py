from __future__ import annotations

import importlib.util
import copy
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / "blender_extension" / "mesh_sync_core.py"
SPEC = importlib.util.spec_from_file_location("rbx_mesh_sync_core", CORE_PATH)
core = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(core)


class MeshSyncCoreTests(unittest.TestCase):
    def test_schema_versions_include_new_and_legacy_reverse(self):
        self.assertEqual(core.MESH_SCHEMA_ID, "roblox-mesh-sync/4")
        self.assertEqual(core.REVERSE_SCHEMA_ID, "roblox-mesh-sync-reverse/4")
        self.assertEqual(core.PREVIOUS_REVERSE_SCHEMA_ID, "roblox-mesh-sync-reverse/3")
        legacy = {
            "schema": core.LEGACY_REVERSE_SCHEMA_ID,
            "objects": [{
                "id": "part", "kind": "PART", "name": "Part",
                "size": [1, 1, 1],
                "cframe": [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
            }],
            "meshes": [], "images": [], "appearances": [], "hierarchy": [],
        }
        core.validate_reverse_document(legacy)
        original = dict(legacy, schema=core.ORIGINAL_REVERSE_SCHEMA_ID)
        core.validate_reverse_document(original)

    def test_reverse_v3_accepts_blender_owned_property_only_mesh(self):
        document = {
            "schema": core.PREVIOUS_REVERSE_SCHEMA_ID,
            "model": {"id": "root", "name": "Root", "rootKind": "BLENDER_SCENE"},
            "objects": [{
                "id": "mesh", "kind": "MESH", "name": "Mesh",
                "geometryOwner": "BLENDER", "geometryAvailable": False,
                "size": [1, 2, 3],
                "cframe": [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
            }],
            "meshes": [], "images": [], "appearances": [], "hierarchy": [],
        }
        core.validate_reverse_document(document)

    def test_reverse_v4_validates_nested_csg_references(self):
        transform = [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]
        document = {
            "schema": core.REVERSE_SCHEMA_ID,
            "model": {"id": "root", "name": "Union", "rootKind": "STUDIO_SELECTION"},
            "objects": [
                {"id": "positive", "kind": "PART", "partType": "Block", "size": [1, 1, 1], "cframe": transform},
                {"id": "negative", "kind": "PART", "partType": "Ball", "size": [1, 1, 1], "cframe": transform},
            ],
            "meshes": [], "images": [], "appearances": [], "hierarchy": [],
            "csg": [
                {
                    "id": "inner", "name": "Inner", "op": "union", "size": [1, 1, 1], "cframe": transform,
                    "operands": [{"role": "positive", "kind": "instance", "ref": "positive"}],
                },
                {
                    "id": "outer", "name": "Outer", "op": "union", "size": [1, 1, 1], "cframe": transform,
                    "operands": [
                        {"role": "positive", "kind": "csg", "ref": "inner"},
                        {"role": "negative", "kind": "instance", "ref": "negative"},
                    ],
                },
            ],
            "csgRoots": [{"kind": "csg", "ref": "outer", "name": "Outer"}],
        }
        core.validate_reverse_document(document)
        self.assertEqual(core.csg_evaluation_order(document), ["inner", "outer"])
        self.assertEqual(core.csg_document_summary(document), {
            "roots": 1, "nodes": 2, "objects": 2,
            "positiveOperands": 2, "negativeOperands": 1,
        })

        missing = copy.deepcopy(document)
        missing["csg"][1]["operands"][1]["ref"] = "missing"
        with self.assertRaisesRegex(ValueError, "missing object"):
            core.validate_reverse_document(missing)

        cyclic = copy.deepcopy(document)
        cyclic["csg"][0]["operands"] = [{"role": "positive", "kind": "csg", "ref": "outer"}]
        with self.assertRaisesRegex(ValueError, "cycle"):
            core.validate_reverse_document(cyclic)

    def test_content_hash_is_key_order_independent(self):
        self.assertEqual(
            core.content_hash({"vertices": [1], "triangles": [2]}),
            core.content_hash({"triangles": [2], "vertices": [1]}),
        )

    def test_mesh_signature_ignores_instance_fields(self):
        payload = {
            "vertices": [[0, 0, 0]],
            "triangles": [[0, 0, 0]],
            "cornerNormals": [],
            "cornerUvs": [],
            "cornerColors": [],
            "name": "First",
            "cframe": list(range(12)),
        }
        first = core.content_hash(core.mesh_signature_payload(payload))
        payload["name"] = "Second"
        payload["cframe"] = [0] * 12
        second = core.content_hash(core.mesh_signature_payload(payload))
        self.assertEqual(first, second)

    def test_uv_and_vertex_color_change_mesh_signature(self):
        base = {
            "vertices": [[0, 0, 0]],
            "triangles": [[0, 0, 0]],
            "cornerNormals": [[[0, 1, 0]] * 3],
            "cornerUvs": [[[0, 0]] * 3],
            "cornerColors": [[[1, 1, 1, 1]] * 3],
        }
        first = core.content_hash(core.mesh_signature_payload(base))
        changed_uv = {**base, "cornerUvs": [[[1, 0]] * 3]}
        changed_color = {**base, "cornerColors": [[[1, 0, 0, 1]] * 3]}
        self.assertNotEqual(first, core.content_hash(core.mesh_signature_payload(changed_uv)))
        self.assertNotEqual(first, core.content_hash(core.mesh_signature_payload(changed_color)))

    def test_chunking_round_trip(self):
        source = bytes(range(251)) * 20
        chunks = core.chunk_bytes(source, 127)
        self.assertEqual(b"".join(chunks), source)
        self.assertGreater(len(chunks), 1)

    def test_shear_detection(self):
        identity = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
        scaled = ((2, 0, 0), (0, 3, 0), (0, 0, 4))
        sheared = ((1, 0.2, 0), (0, 1, 0), (0, 0, 1))
        # Real Blender 4.5.9 hierarchy output: tiny scale/rotation round-off
        # accumulated across four parents. This measured 1.04364e-5 in the
        # normalized column dot product and is visually indistinguishable from
        # the intended cylinder, so it must not be treated as authored shear.
        hierarchy_roundoff = (
            (-0.269318283, 0.0000394967283, -0.299586058),
            (0.12846458, -0.313966274, -0.0189702939),
            (-0.72873646, -0.0553616099, 0.107369907),
        )
        self.assertFalse(core.has_shear(identity))
        self.assertFalse(core.has_shear(scaled))
        self.assertFalse(core.has_shear(hierarchy_roundoff))
        self.assertTrue(core.has_shear(hierarchy_roundoff, tolerance=1e-5))
        self.assertTrue(core.has_shear(sheared))

    def test_material_variant_changes_appearance_signature(self):
        base = {
            "mode": "TEXTURE", "maps": {"baseColor": "a"},
            "material": "Plastic", "color": [1, 1, 1], "transparency": 0,
        }
        changed = {**base, "materialVariant": {"studsPerTile": 4}}
        self.assertNotEqual(
            core.content_hash(core.appearance_signature_payload(base)),
            core.content_hash(core.appearance_signature_payload(changed)),
        )

    def test_texture_source_changes_appearance_signature(self):
        base = {
            "mode": "TEXTURE", "maps": {"baseColor": "a"},
            "material": "Plastic", "color": [0.5, 0.75, 1], "transparency": 0,
        }
        surface = {**base, "textureSource": "SURFACE_APPEARANCE"}
        texture_id = {**base, "textureSource": "MESHPART_TEXTURE"}
        self.assertNotEqual(
            core.content_hash(core.appearance_signature_payload(surface)),
            core.content_hash(core.appearance_signature_payload(texture_id)),
        )

    def test_roblox_material_is_only_selected_by_explicit_toggle(self):
        self.assertEqual(core.resolve_appearance_mode(True, "AUTO", True, True), "MATERIAL")
        self.assertEqual(core.resolve_appearance_mode(False, "AUTO", True, True), "TEXTURE")
        self.assertEqual(core.resolve_appearance_mode(False, "AUTO", False, True), "VERTEX")
        self.assertEqual(core.resolve_appearance_mode(False, "AUTO", False, False), "NONE")

    def test_legacy_material_mode_does_not_bypass_disabled_toggle(self):
        self.assertEqual(core.resolve_appearance_mode(False, "MATERIAL", False, False), "NONE")

    def test_reverse_document_rejects_duplicate_ids(self):
        document = {
            "schema": core.PREVIOUS_REVERSE_SCHEMA_ID,
            "model": {"id": "root", "name": "Root", "rootKind": "STUDIO_SELECTION"},
            "objects": [{"id": "same"}, {"id": "same"}],
        }
        with self.assertRaises(ValueError):
            core.validate_reverse_document(document)

    def test_reverse_document_normalizes_empty_luau_map_array(self):
        document = {
            "schema": core.PREVIOUS_REVERSE_SCHEMA_ID,
            "model": {"id": "root", "name": "Root", "rootKind": "STUDIO_SELECTION"},
            "objects": [{
                "id": "part", "kind": "PART", "appearanceHash": "appearance",
                "size": [1, 1, 1], "cframe": [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
            }],
            "meshes": [], "images": [], "hierarchy": [],
            "appearances": [{"hash": "appearance", "mode": "MATERIAL", "maps": []}],
        }
        core.validate_reverse_document(document)
        self.assertEqual(document["appearances"][0]["maps"], {})

    def test_reverse_document_rejects_nonempty_array_as_image_map_table(self):
        document = {
            "schema": core.PREVIOUS_REVERSE_SCHEMA_ID,
            "model": {"id": "root", "name": "Root", "rootKind": "STUDIO_SELECTION"},
            "objects": [{
                "id": "part", "kind": "PART", "appearanceHash": "appearance",
                "size": [1, 1, 1], "cframe": [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
            }],
            "meshes": [], "images": [], "hierarchy": [],
            "appearances": [{"hash": "appearance", "mode": "MATERIAL", "maps": ["bad"]}],
        }
        with self.assertRaises(ValueError):
            core.validate_reverse_document(document)

    def test_reverse_document_validates_native_mesh_size(self):
        document = {
            "schema": core.PREVIOUS_REVERSE_SCHEMA_ID,
            "model": {"id": "root", "name": "Root", "rootKind": "STUDIO_SELECTION"},
            "objects": [{
                "id": "mesh", "kind": "MESH", "meshHash": "mesh-hash",
                "meshSize": [2, 3, 4], "size": [4, 6, 8],
                "cframe": [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
            }],
            "meshes": [{"hash": "mesh-hash"}], "images": [], "hierarchy": [],
            "appearances": [],
        }
        core.validate_reverse_document(document)
        document["objects"][0]["meshSize"] = [2, 0, 4]
        with self.assertRaisesRegex(ValueError, "native mesh size"):
            core.validate_reverse_document(document)

    def test_mixed_model_folder_hierarchy_is_valid(self):
        hierarchy = [
            {"id": "model", "kind": "MODEL", "name": "Model"},
            {"id": "folder", "kind": "FOLDER", "name": "Folder", "parentId": "model"},
            {"id": "child", "kind": "MODEL", "name": "Child", "parentId": "folder"},
        ]
        ordered = core.hierarchy_parent_order(hierarchy)
        self.assertEqual([node["id"] for node in ordered], ["model", "folder", "child"])

    def test_hierarchy_cycle_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "cycle"):
            core.hierarchy_parent_order([
                {"id": "a", "kind": "FOLDER", "parentId": "b"},
                {"id": "b", "kind": "MODEL", "parentId": "a"},
            ])

    def test_hierarchy_missing_parent_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing parent"):
            core.hierarchy_parent_order([
                {"id": "a", "kind": "FOLDER", "parentId": "missing"},
            ])

    def test_replace_scope_references_model_hierarchy(self):
        document = {
            "schema": core.MESH_SCHEMA_ID,
            "model": {"id": "root", "name": "Root", "rootKind": "BLENDER_SCENE"},
            "instances": [{"id": "part", "kind": "PART", "partType": "Block"}],
            "meshes": [], "images": [],
            "hierarchy": [{"id": "lamp", "name": "Lamp", "kind": "MODEL"}],
            "replaceScopes": [{
                "hierarchyId": "lamp", "mode": "REPLACE_DESCENDANTS",
                "presentSourceObjectIds": ["part"],
            }],
        }
        core.validate_document_limits(document)

    def test_replace_scope_rejects_folder_reference(self):
        document = {
            "schema": core.MESH_SCHEMA_ID,
            "model": {"id": "root", "name": "Root", "rootKind": "BLENDER_SCENE"},
            "instances": [{"id": "part", "kind": "PART", "partType": "Block"}],
            "meshes": [], "images": [],
            "hierarchy": [{"id": "folder", "name": "Folder", "kind": "FOLDER"}],
            "replaceScopes": [{
                "hierarchyId": "folder", "mode": "REPLACE_DESCENDANTS",
            }],
        }
        with self.assertRaisesRegex(ValueError, "Model hierarchy node"):
            core.validate_document_limits(document)

    def test_forward_document_accepts_part_only_send(self):
        core.validate_document_limits({
            "meshes": [],
            "images": [],
            "instances": [{"id": "part", "kind": "PART", "partType": "Block"}],
        })

    def test_forward_document_requires_mesh_reference_for_meshpart(self):
        with self.assertRaises(ValueError):
            core.validate_document_limits({
                "meshes": [],
                "images": [],
                "instances": [{"id": "mesh", "kind": "MESH", "meshHash": "missing"}],
            })

    def test_forward_replacement_ids_are_validated(self):
        document = {
            "schema": core.MESH_SCHEMA_ID,
            "model": {"id": "root", "name": "Root", "rootKind": "BLENDER_SCENE"},
            "meshes": [], "images": [], "hierarchy": [],
            "instances": [
                {
                    "id": "merged", "kind": "PART", "partType": "Block",
                    "replacesObjectIds": ["old-a", "old-b"],
                },
            ],
        }
        core.validate_document_limits(document)

        self_replacement = copy.deepcopy(document)
        self_replacement["instances"][0]["replacesObjectIds"] = ["merged"]
        with self.assertRaisesRegex(ValueError, "included in the same send"):
            core.validate_document_limits(self_replacement)

        duplicate_claim = copy.deepcopy(document)
        duplicate_claim["instances"].append({
            "id": "second", "kind": "PART", "partType": "Block",
            "replacesObjectIds": ["old-a"],
        })
        with self.assertRaisesRegex(ValueError, "claimed by multiple"):
            core.validate_document_limits(duplicate_claim)


if __name__ == "__main__":
    unittest.main()
