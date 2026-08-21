from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import re
import tomllib
import unittest


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "blender_extension"


class VersionConsistencyTests(unittest.TestCase):
    def test_all_distributed_components_use_manifest_version(self):
        manifest_version = tomllib.loads(
            (EXTENSION / "blender_manifest.toml").read_text(encoding="utf-8")
        )["version"]
        init_tree = ast.parse((EXTENSION / "__init__.py").read_text(encoding="utf-8"))
        bl_info = ast.literal_eval(init_tree.body[1].value)
        self.assertEqual(".".join(map(str, bl_info["version"])), manifest_version)

        core_path = EXTENSION / "mesh_sync_core.py"
        spec = importlib.util.spec_from_file_location("version_mesh_sync_core", core_path)
        core = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(core)
        self.assertEqual(core.MESH_SYNC_VERSION, manifest_version)

        serialization = (EXTENSION / "serialization.py").read_text(encoding="utf-8")
        self.assertIn(f'"version": "{manifest_version}"', serialization)
        studio = (ROOT / "roblox_plugin" / "src" / "MeshSync.luau").read_text(encoding="utf-8")
        match = re.search(r'local PLUGIN_VERSION = "([^"]+)"', studio)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), manifest_version)


if __name__ == "__main__":
    unittest.main()
