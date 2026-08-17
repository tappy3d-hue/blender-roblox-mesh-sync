"""Local-only HTTP transport for bidirectional Blender/Studio Mesh Sync."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from .mesh_sync_core import (
    CHUNK_SIZE,
    MAX_BLOB_SIZE,
    MESH_SCHEMA_ID,
    PREVIOUS_MESH_SCHEMA_ID,
    LEGACY_MESH_SCHEMA_ID,
    ORIGINAL_MESH_SCHEMA_ID,
    MESH_SYNC_VERSION,
    chunk_bytes,
    sha256_bytes,
    stable_json_bytes,
    validate_reverse_document,
)


@dataclass
class Snapshot:
    revision: int = 0
    manifest: bytes = b""
    blobs: dict[tuple[str, str], list[bytes]] = field(default_factory=dict)


@dataclass
class ReverseTransfer:
    transfer_id: str
    document: dict
    expected: dict[tuple[str, str], tuple[int, int]]
    chunks: dict[tuple[str, str], dict[int, bytes]] = field(default_factory=dict)


@dataclass
class ReverseSnapshot:
    revision: int
    document: dict
    blobs: dict[tuple[str, str], bytes]


class MeshSyncServer:
    def __init__(self):
        self._lock = threading.RLock()
        self._server = None
        self._thread = None
        self._token = ""
        self._snapshot = Snapshot()
        self._last_result = None
        self._reverse_transfer = None
        self._pending_reverse = None
        self._reverse_revision = 0
        self._last_reverse_commit = None
        self._pairing_deadline = 0.0

    @property
    def running(self):
        return self._server is not None

    @property
    def port(self):
        return self._server.server_port if self._server else None

    @property
    def last_result(self):
        with self._lock:
            return self._last_result

    @property
    def pending_reverse(self):
        with self._lock:
            return self._pending_reverse

    @property
    def pairing_active(self):
        with self._lock:
            return time.monotonic() < self._pairing_deadline

    @property
    def pairing_seconds_remaining(self):
        with self._lock:
            return max(0, int(self._pairing_deadline - time.monotonic() + 0.999))

    def enable_pairing(self, seconds=60):
        with self._lock:
            self._pairing_deadline = time.monotonic() + max(1, int(seconds))

    def disable_pairing(self):
        with self._lock:
            self._pairing_deadline = 0.0

    def discard_reverse(self):
        with self._lock:
            self._pending_reverse = None

    def complete_reverse(self, revision):
        with self._lock:
            if self._pending_reverse and self._pending_reverse.revision == revision:
                self._pending_reverse = None

    def start(self, port: int, token: str):
        if self._server and self.port == port and self._token == token:
            return
        self.stop()
        self._token = token
        controller = self

        class Handler(BaseHTTPRequestHandler):
            server_version = f"RobloxMeshSync/{MESH_SYNC_VERSION}"
            protocol_version = "HTTP/1.1"

            def log_message(self, _format, *_args):
                return

            def _authorized(self):
                return self.headers.get("X-Roblox-Sync-Token", "") == controller._token

            def _send(self, status, body=b"", content_type="application/json; charset=utf-8"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _json(self, status, value):
                self._send(status, stable_json_bytes(value))

            def _read_json(self, maximum=16 * 1024 * 1024):
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > maximum:
                    raise ValueError("invalid request length")
                return json.loads(self.rfile.read(length).decode("utf-8"))

            def do_GET(self):
                path = urlparse(self.path).path
                if path == "/v1/health":
                    self._json(200, {
                        "ok": True,
                        "service": "roblox-mesh-sync",
                        "version": 3,
                        "addonVersion": MESH_SYNC_VERSION,
                        "schemas": [
                            MESH_SCHEMA_ID, PREVIOUS_MESH_SCHEMA_ID,
                            LEGACY_MESH_SCHEMA_ID, ORIGINAL_MESH_SCHEMA_ID,
                        ],
                    })
                    return
                if not self._authorized():
                    self._json(401, {"error": "invalid connection token"})
                    return
                with controller._lock:
                    snapshot = controller._snapshot
                    if path == "/v1/revision":
                        self._json(200, {"revision": snapshot.revision})
                        return
                    if path == "/v2/reverse/pending":
                        pending = controller._pending_reverse
                        self._json(200, {
                            "pending": pending is not None,
                            "revision": pending.revision if pending else 0,
                            "objects": len(pending.document.get("objects", [])) if pending else 0,
                        })
                        return
                    if path == "/v1/manifest":
                        if not snapshot.manifest:
                            self._json(404, {"error": "no model has been sent from Blender"})
                        else:
                            self._send(200, snapshot.manifest)
                        return
                    parts = path.strip("/").split("/")
                    if len(parts) == 5 and parts[:2] == ["v1", "blob"]:
                        kind, digest, raw_index = unquote(parts[2]), unquote(parts[3]), parts[4]
                        try:
                            index = int(raw_index)
                            chunks = snapshot.blobs[(kind, digest)]
                            body = chunks[index]
                        except (ValueError, KeyError, IndexError):
                            self._json(404, {"error": "blob chunk not found"})
                            return
                        self._send(200, body, "application/octet-stream")
                        return
                self._json(404, {"error": "not found"})

            def do_POST(self):
                path = urlparse(self.path).path
                if path == "/v1/pair":
                    with controller._lock:
                        if time.monotonic() >= controller._pairing_deadline:
                            self._json(403, {"error": "pairing approval required in Blender"})
                            return
                        value = {
                            "ok": True,
                            "service": "roblox-mesh-sync",
                            "port": controller.port,
                            "token": controller._token,
                        }
                        # A pairing approval is single-use. If the response is
                        # interrupted, the user can press Allow again.
                        controller._pairing_deadline = 0.0
                    self._json(200, value)
                    return
                if not self._authorized():
                    self._json(401, {"error": "invalid connection token"})
                    return
                if path.startswith("/v2/reverse/"):
                    try:
                        if path == "/v2/reverse/begin":
                            value = self._read_json()
                            transfer_id = value.get("transferId")
                            document = value.get("document")
                            if not isinstance(transfer_id, str) or not transfer_id:
                                raise ValueError("missing transferId")
                            if not isinstance(document, dict):
                                raise ValueError("missing reverse document")
                            validate_reverse_document(document)
                            expected = {}
                            for kind, records in (("mesh", document.get("meshes", [])), ("image", document.get("images", []))):
                                for record in records:
                                    digest = record.get("hash")
                                    size = int(record.get("byteSize", -1))
                                    count = int(record.get("chunkCount", 0))
                                    if not isinstance(digest, str) or len(digest) != 64:
                                        raise ValueError(f"invalid {kind} hash")
                                    if size < 0 or size > MAX_BLOB_SIZE or count < 1 or count > 10000:
                                        raise ValueError(f"invalid {kind} blob limits")
                                    expected[(kind, digest)] = (size, count)
                            with controller._lock:
                                controller._reverse_transfer = ReverseTransfer(transfer_id, document, expected)
                            self._json(200, {"ok": True})
                            return

                        parts = path.strip("/").split("/")
                        if len(parts) == 6 and parts[:3] == ["v2", "reverse", "blob"]:
                            kind, digest, raw_index = unquote(parts[3]), unquote(parts[4]), parts[5]
                            index = int(raw_index)
                            length = int(self.headers.get("Content-Length", "0"))
                            if length < 0 or length > CHUNK_SIZE:
                                raise ValueError("invalid chunk length")
                            body = self.rfile.read(length)
                            transfer_id = self.headers.get("X-Roblox-Sync-Transfer", "")
                            with controller._lock:
                                transfer = controller._reverse_transfer
                                if not transfer or transfer.transfer_id != transfer_id:
                                    raise ValueError("reverse transfer is not active")
                                expected = transfer.expected.get((kind, digest))
                                if not expected or index < 0 or index >= expected[1]:
                                    raise ValueError("unexpected reverse blob chunk")
                                transfer.chunks.setdefault((kind, digest), {})[index] = body
                            self._json(200, {"ok": True})
                            return

                        if path == "/v2/reverse/commit":
                            value = self._read_json(1024 * 1024)
                            transfer_id = value.get("transferId")
                            with controller._lock:
                                transfer = controller._reverse_transfer
                                if not transfer and controller._last_reverse_commit:
                                    completed_id, completed_revision = controller._last_reverse_commit
                                    if completed_id == transfer_id:
                                        self._json(200, {"ok": True, "revision": completed_revision})
                                        return
                                if not transfer or transfer.transfer_id != transfer_id:
                                    raise ValueError("reverse transfer is not active")
                                blobs = {}
                                for key, (size, count) in transfer.expected.items():
                                    received = transfer.chunks.get(key, {})
                                    if len(received) != count:
                                        raise ValueError(f"missing chunks for {key[0]} {key[1][:12]}")
                                    raw = b"".join(received[index] for index in range(count))
                                    if len(raw) != size or sha256_bytes(raw) != key[1]:
                                        raise ValueError(f"invalid content for {key[0]} {key[1][:12]}")
                                    blobs[key] = raw
                                controller._reverse_revision += 1
                                controller._pending_reverse = ReverseSnapshot(
                                    controller._reverse_revision, transfer.document, blobs,
                                )
                                controller._reverse_transfer = None
                                revision = controller._reverse_revision
                                controller._last_reverse_commit = (transfer_id, revision)
                            self._json(200, {"ok": True, "revision": revision})
                            return
                    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, KeyError) as error:
                        self._json(400, {"error": str(error)})
                        return
                    except Exception as error:
                        # Never leave Studio with an opaque ConnectionClosed when validation
                        # or transport code encounters an unexpected Python exception.
                        try:
                            self._json(500, {"error": f"Blender server error: {type(error).__name__}: {error}"})
                        except (BrokenPipeError, ConnectionResetError, OSError):
                            pass
                        return
                    self._json(404, {"error": "not found"})
                    return

                if path != "/v1/result":
                    self._json(404, {"error": "not found"})
                    return
                try:
                    result = self._read_json(1024 * 1024)
                except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                    self._json(400, {"error": str(error)})
                    return
                with controller._lock:
                    controller._last_result = result
                self._json(200, {"ok": True})

        self._server = ThreadingHTTPServer(("127.0.0.1", int(port)), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, name="RobloxMeshSync", daemon=True)
        self._thread.start()

    def stop(self):
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        self.disable_pairing()
        if server:
            server.shutdown()
            server.server_close()
        if thread and thread.is_alive():
            thread.join(timeout=2.0)

    def publish(self, document: dict, mesh_blobs: dict[str, bytes], image_blobs: dict[str, bytes]):
        blobs = {}
        for kind, values in (("mesh", mesh_blobs), ("image", image_blobs)):
            for digest, value in values.items():
                if len(value) > MAX_BLOB_SIZE:
                    raise ValueError(f"{kind} {digest[:12]} exceeds the 32 MB transfer limit")
                blobs[(kind, digest)] = chunk_bytes(value, CHUNK_SIZE)

        with self._lock:
            revision = self._snapshot.revision + 1
            document = dict(document)
            document["revision"] = revision
            self._snapshot = Snapshot(revision, stable_json_bytes(document), blobs)
            self._last_result = None
        return revision


SERVER = MeshSyncServer()
