"""Read-only report of shared Blender Mesh data and evaluated Mesh Sync hashes."""

from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blender_extension.mesh_sync import (  # noqa: E402
    _collect_mesh,
    _shareable_evaluation_key,
    build_selection_document,
)
from blender_extension.mesh_sync_core import content_hash  # noqa: E402


def main():
    depsgraph = bpy.context.evaluated_depsgraph_get()
    groups = defaultdict(list)
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH":
            groups[obj.data.as_pointer()].append(obj)

    report = []
    for objects in groups.values():
        if len(objects) < 2:
            continue
        entries = []
        for obj in sorted(objects, key=lambda item: item.name.casefold()):
            try:
                payload, digest, _center, local_size, has_uv, has_colors = _collect_mesh(obj, depsgraph)
                error = None
            except Exception as caught:  # report every object without mutating the source file
                payload, digest, local_size, has_uv, has_colors = {}, None, None, None, None
                error = str(caught)
            entries.append({
                "object": obj.name,
                "hash": digest,
                "vertices": len(payload.get("vertices", ())),
                "triangles": len(payload.get("triangles", ())),
                "componentHashes": {
                    key: content_hash(payload.get(key, []))
                    for key in ("vertices", "triangles", "cornerNormals", "cornerUvs", "cornerColors")
                },
                "shareableEvaluationKey": _shareable_evaluation_key(obj),
                "localSize": local_size,
                "hasUv": has_uv,
                "hasColors": has_colors,
                "modifiers": [
                    {
                        "name": modifier.name,
                        "type": modifier.type,
                        "viewport": bool(modifier.show_viewport),
                        "simpleSettings": {
                            prop.identifier: getattr(modifier, prop.identifier)
                            for prop in modifier.bl_rna.properties
                            if prop.identifier not in {"rna_type", "name"}
                            and not prop.is_readonly
                            and prop.type in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}
                        },
                    }
                    for modifier in obj.modifiers
                ],
                "scale": tuple(float(value) for value in obj.scale),
                "error": error,
            })
        report.append({
            "meshData": objects[0].data.name,
            "objectCount": len(objects),
            "uniqueHashes": sorted({entry["hash"] for entry in entries if entry["hash"]}),
            "objects": entries,
        })

    print("MESH_SYNC_GROUP_REPORT=" + json.dumps(report, ensure_ascii=False, sort_keys=True))

    document_reports = []
    for objects in groups.values():
        if len(objects) < 2:
            continue
        for scene_obj in bpy.context.scene.objects:
            scene_obj.select_set(False)
        for obj in objects:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = objects[0]
        try:
            document, _mesh_blobs, _image_blobs = build_selection_document(bpy.context)
            document_reports.append({
                "meshData": objects[0].data.name,
                "instances": len(document["instances"]),
                "uniqueMeshes": len(document["meshes"]),
                "hashes": sorted({instance["meshHash"] for instance in document["instances"]}),
            })
        except Exception as caught:
            document_reports.append({"meshData": objects[0].data.name, "error": str(caught)})
    print("MESH_SYNC_DOCUMENT_REPORT=" + json.dumps(document_reports, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
