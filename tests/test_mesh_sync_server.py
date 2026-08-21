from __future__ import annotations

import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import types
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_DIR = ROOT / "blender_extension"
package = types.ModuleType("mesh_sync_test_package")
package.__path__ = [str(PACKAGE_DIR)]
sys.modules[package.__name__] = package


def load(name):
    path = PACKAGE_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{package.__name__}.{name}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


load("mesh_sync_core")
server_module = load("mesh_sync_server")


class MeshSyncServerTests(unittest.TestCase):
    def setUp(self):
        self.server = server_module.MeshSyncServer()
        self.server.start(0, "test-token")
        self.base = f"http://127.0.0.1:{self.server.port}"

    def tearDown(self):
        self.server.stop()

    def fetch(self, path, token=None, method="GET", body=None):
        headers = {}
        if token:
            headers["X-Roblox-Sync-Token"] = token
        request = Request(self.base + path, data=body, headers=headers, method=method)
        with urlopen(request, timeout=3) as response:
            return response.status, response.read()

    def test_health_does_not_expose_token(self):
        status, body = self.fetch("/v1/health")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["version"], 3)
        self.assertEqual(data["addonVersion"], "0.11.2")
        self.assertIn("roblox-mesh-sync/4", data["schemas"])
        self.assertIn("roblox-mesh-sync/3", data["schemas"])
        self.assertIn("roblox-mesh-sync/2", data["schemas"])
        self.assertNotIn("token", data)

    def test_protected_endpoints_require_token(self):
        with self.assertRaises(HTTPError) as error:
            self.fetch("/v1/revision")
        self.assertEqual(error.exception.code, 401)

    def test_publish_manifest_and_chunked_blobs(self):
        mesh = b"m" * (256 * 1024 + 17)
        image = bytes((10, 20, 30, 255))
        revision = self.server.publish(
            {"schema": "roblox-mesh-sync/1", "model": {"id": "id", "name": "Test"}},
            {"meshhash": mesh},
            {"imagehash": image},
        )
        self.assertEqual(revision, 1)
        _, revision_body = self.fetch("/v1/revision", "test-token")
        self.assertEqual(json.loads(revision_body)["revision"], 1)
        _, manifest_body = self.fetch("/v1/manifest", "test-token")
        self.assertEqual(json.loads(manifest_body)["revision"], 1)
        _, mesh_first = self.fetch("/v1/blob/mesh/meshhash/0", "test-token")
        _, mesh_second = self.fetch("/v1/blob/mesh/meshhash/1", "test-token")
        _, image_body = self.fetch("/v1/blob/image/imagehash/0", "test-token")
        self.assertEqual(mesh_first + mesh_second, mesh)
        self.assertEqual(image_body, image)

    def test_studio_result_is_recorded(self):
        payload = json.dumps({"ok": True, "revision": 7}).encode()
        status, _ = self.fetch("/v1/result", "test-token", "POST", payload)
        self.assertEqual(status, 200)
        self.assertEqual(self.server.last_result["revision"], 7)

    def test_reverse_transfer_is_verified_and_queued(self):
        raw = b'{"vertices":[[0,0,0]],"triangles":[[0,0,0]]}'
        digest = hashlib.sha256(raw).hexdigest()
        document = {
            "schema": "roblox-mesh-sync-reverse/1",
            "model": {"id": "model", "name": "Studio"},
            "objects": [{
                "id": "object", "kind": "MESH", "meshHash": digest,
                "size": [1, 1, 1], "cframe": [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
            }],
            "meshes": [{"hash": digest, "byteSize": len(raw), "chunkCount": 1}],
            "images": [], "appearances": [], "hierarchy": [],
        }
        begin = json.dumps({"transferId": "transfer", "document": document}).encode()
        status, _ = self.fetch("/v2/reverse/begin", "test-token", "POST", begin)
        self.assertEqual(status, 200)

        request = Request(
            self.base + f"/v2/reverse/blob/mesh/{digest}/0",
            data=raw,
            headers={
                "X-Roblox-Sync-Token": "test-token",
                "X-Roblox-Sync-Transfer": "transfer",
                "Content-Type": "application/octet-stream",
            },
            method="POST",
        )
        with urlopen(request, timeout=3) as response:
            self.assertEqual(response.status, 200)
        commit = json.dumps({"transferId": "transfer"}).encode()
        status, body = self.fetch("/v2/reverse/commit", "test-token", "POST", commit)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["revision"], 1)
        status, body = self.fetch("/v2/reverse/commit", "test-token", "POST", commit)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["revision"], 1)
        self.assertEqual(self.server.pending_reverse.document["model"]["name"], "Studio")

    def test_reverse_v4_csg_preview_is_validated_and_queued_without_blobs(self):
        transform = [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1]
        document = {
            "schema": "roblox-mesh-sync-reverse/4",
            "model": {"id": "model", "name": "Union", "rootKind": "STUDIO_SELECTION"},
            "objects": [{
                "id": "part", "kind": "PART", "partType": "Block",
                "size": [1, 1, 1], "cframe": transform,
            }],
            "meshes": [], "images": [], "appearances": [], "hierarchy": [],
            "csg": [{
                "id": "union", "name": "Union", "op": "union",
                "size": [1, 1, 1], "cframe": transform,
                "operands": [{"role": "positive", "kind": "instance", "ref": "part"}],
            }],
            "csgRoots": [{"kind": "csg", "ref": "union", "name": "Union"}],
        }
        begin = json.dumps({"transferId": "csg", "document": document}).encode()
        status, _ = self.fetch("/v2/reverse/begin", "test-token", "POST", begin)
        self.assertEqual(status, 200)
        commit = json.dumps({"transferId": "csg"}).encode()
        status, body = self.fetch("/v2/reverse/commit", "test-token", "POST", commit)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["revision"], 1)
        self.assertEqual(self.server.pending_reverse.document["csg"][0]["id"], "union")

    def test_reverse_material_without_images_accepts_luau_empty_maps(self):
        document = {
            "schema": "roblox-mesh-sync-reverse/1",
            "model": {"id": "model", "name": "Studio"},
            "objects": [{
                "id": "wood-part", "kind": "PART", "partType": "Block",
                "appearanceHash": "wood", "size": [1, 1, 1],
                "cframe": [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
            }],
            "meshes": [], "images": [], "hierarchy": [],
            "appearances": [{
                "hash": "wood", "mode": "MATERIAL", "material": "Wood",
                "color": [0.827451, 0.5843137, 0.3764706], "maps": [],
            }],
        }
        begin = json.dumps({"transferId": "wood", "document": document}).encode()
        status, _ = self.fetch("/v2/reverse/begin", "test-token", "POST", begin)
        self.assertEqual(status, 200)
        commit = json.dumps({"transferId": "wood"}).encode()
        status, body = self.fetch("/v2/reverse/commit", "test-token", "POST", commit)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["revision"], 1)
        self.assertEqual(
            self.server.pending_reverse.document["appearances"][0]["maps"], {},
        )

    def test_reverse_transfer_rejects_wrong_hash(self):
        digest = hashlib.sha256(b"expected").hexdigest()
        document = {
            "schema": "roblox-mesh-sync-reverse/1",
            "objects": [{
                "id": "object", "kind": "MESH", "meshHash": digest,
                "size": [1, 1, 1], "cframe": [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
            }],
            "meshes": [{"hash": digest, "byteSize": 5, "chunkCount": 1}],
            "images": [],
        }
        begin = json.dumps({"transferId": "bad", "document": document}).encode()
        self.fetch("/v2/reverse/begin", "test-token", "POST", begin)
        request = Request(
            self.base + f"/v2/reverse/blob/mesh/{digest}/0",
            data=b"wrong",
            headers={"X-Roblox-Sync-Token": "test-token", "X-Roblox-Sync-Transfer": "bad"},
            method="POST",
        )
        with urlopen(request, timeout=3):
            pass
        commit = json.dumps({"transferId": "bad"}).encode()
        with self.assertRaises(HTTPError) as error:
            self.fetch("/v2/reverse/commit", "test-token", "POST", commit)
        self.assertEqual(error.exception.code, 400)

    def test_reverse_unexpected_validation_error_returns_json_instead_of_closing(self):
        digest = hashlib.sha256(b"mesh").hexdigest()
        document = {
            "schema": "roblox-mesh-sync-reverse/1",
            "objects": [{
                "id": "object", "kind": "MESH", "meshHash": digest,
                "size": [1, 1, 1], "cframe": [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
            }],
            "meshes": [{"hash": digest, "byteSize": None, "chunkCount": 1}],
            "images": [], "appearances": [], "hierarchy": [],
        }
        begin = json.dumps({"transferId": "invalid", "document": document}).encode()
        with self.assertRaises(HTTPError) as error:
            self.fetch("/v2/reverse/begin", "test-token", "POST", begin)
        self.assertEqual(error.exception.code, 500)
        body = json.loads(error.exception.read())
        self.assertIn("Blender server error", body["error"])


if __name__ == "__main__":
    unittest.main()
