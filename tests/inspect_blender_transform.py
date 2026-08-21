"""Blender transform/shear diagnostic for the active object.

Usage:
1. Select the object that produced the export error.
2. Open Blender's Scripting workspace and this file in the Text Editor.
3. Press Run Script.
4. Paste the clipboard contents into the Codex chat.

The report contains object and transform data only. It does not include the
.blend file path or mesh vertex data.
"""

from __future__ import annotations

import math

import bpy
from mathutils import Matrix


REPORT_TEXT_NAME = "RBX_Transform_Diagnostic"
SHEAR_TOLERANCE = 1e-4


def _number(value: float) -> str:
    value = float(value)
    if abs(value) < 5e-13:
        value = 0.0
    return f"{value:.9g}"


def _vector(value) -> str:
    return "(" + ", ".join(_number(component) for component in value) + ")"


def _matrix_lines(label: str, matrix: Matrix) -> list[str]:
    lines = [f"  {label}:"]
    for row in matrix:
        lines.append("    [" + ", ".join(f"{float(value): .9g}" for value in row) + "]")
    return lines


def _shear_metrics(matrix: Matrix) -> dict[str, object]:
    basis = matrix.to_3x3()
    columns = [basis.col[index].copy() for index in range(3)]
    lengths = [column.length for column in columns]
    normalized = [
        column.normalized() if length > 1e-15 else column.copy()
        for column, length in zip(columns, lengths)
    ]
    dots = {
        "XY": float(normalized[0].dot(normalized[1])),
        "XZ": float(normalized[0].dot(normalized[2])),
        "YZ": float(normalized[1].dot(normalized[2])),
    }
    max_dot = max(abs(value) for value in dots.values())
    location, rotation, scale = matrix.decompose()
    reconstructed = (
        Matrix.Translation(location)
        @ rotation.to_matrix().to_4x4()
        @ Matrix.Diagonal((scale.x, scale.y, scale.z, 1.0))
    )
    residual = max(
        abs(float(matrix[row][column] - reconstructed[row][column]))
        for row in range(4)
        for column in range(4)
    )
    return {
        "lengths": lengths,
        "dots": dots,
        "max_dot": max_dot,
        "determinant": float(basis.determinant()),
        "decomposed_location": location,
        "decomposed_rotation": rotation,
        "decomposed_scale": scale,
        "residual": residual,
        "has_shear": max_dot > SHEAR_TOLERANCE,
    }


def _rotation_degrees(obj) -> tuple[float, float, float]:
    euler = obj.rotation_euler
    return tuple(math.degrees(float(value)) for value in euler)


def _object_lines(obj, depth: int) -> list[str]:
    local_metrics = _shear_metrics(obj.matrix_local)
    world_metrics = _shear_metrics(obj.matrix_world)
    settings = getattr(obj, "rbx_primitive_sync", None)
    lines = [
        f"[{depth}] {obj.name}",
        f"  type={obj.type} parent_type={obj.parent_type} parent={obj.parent.name if obj.parent else '<none>'}",
        f"  location={_vector(obj.location)}",
        f"  rotation_mode={obj.rotation_mode} rotation_euler_deg={_vector(_rotation_degrees(obj))}",
        f"  scale={_vector(obj.scale)} dimensions={_vector(obj.dimensions)}",
    ]
    if settings is not None:
        lines.append(
            "  addon="
            f"is_roblox_part:{bool(settings.is_roblox_part)} "
            f"part_type:{settings.part_type} "
            f"sync_enabled:{bool(settings.sync_enabled)} "
            f"mesh_sync_enabled:{bool(settings.mesh_sync_enabled)}"
        )
    for label, metrics in (("LOCAL", local_metrics), ("WORLD", world_metrics)):
        dots = metrics["dots"]
        lines.extend([
            f"  {label} basis_lengths={_vector(metrics['lengths'])}",
            f"  {label} normalized_dots=XY:{_number(dots['XY'])} XZ:{_number(dots['XZ'])} YZ:{_number(dots['YZ'])}",
            f"  {label} max_abs_dot={_number(metrics['max_dot'])} tolerance={SHEAR_TOLERANCE:g} has_shear={metrics['has_shear']}",
            f"  {label} determinant={_number(metrics['determinant'])} decomposition_residual={_number(metrics['residual'])}",
            f"  {label} decomposed_scale={_vector(metrics['decomposed_scale'])}",
        ])
    lines.extend(_matrix_lines("matrix_local", obj.matrix_local))
    lines.extend(_matrix_lines("matrix_world", obj.matrix_world))
    if obj.parent is not None:
        lines.extend(_matrix_lines("matrix_parent_inverse", obj.matrix_parent_inverse))
    return lines


def build_report(obj) -> str:
    chain = []
    current = obj
    while current is not None:
        chain.append(current)
        current = current.parent
    chain.reverse()

    lines = [
        "===== Roblox Blender Sync Transform Diagnostic =====",
        f"Blender version: {bpy.app.version_string}",
        f"Active object: {obj.name}",
        f"Hierarchy depth: {len(chain) - 1}",
        f"Shear tolerance used by addon: {SHEAR_TOLERANCE:g}",
        "Note: max_abs_dot above the tolerance is what triggers the current error.",
        "",
    ]
    for depth, item in enumerate(chain):
        lines.extend(_object_lines(item, depth))
        lines.append("")
    lines.append("===== END Transform Diagnostic =====")
    return "\n".join(lines)


def main() -> None:
    obj = bpy.context.view_layer.objects.active
    if obj is None:
        raise RuntimeError("診断するオブジェクトを1つ選択して、アクティブにしてください。")

    report = build_report(obj)
    print(report)
    bpy.context.window_manager.clipboard = report

    text = bpy.data.texts.get(REPORT_TEXT_NAME) or bpy.data.texts.new(REPORT_TEXT_NAME)
    text.clear()
    text.write(report)

    def draw_message(self, _context):
        self.layout.label(text=f"{obj.name} の診断結果をクリップボードへコピーしました。")
        self.layout.label(text=f"Text Editorの {REPORT_TEXT_NAME} にも保存しました。")

    bpy.context.window_manager.popup_menu(
        draw_message,
        title="Roblox Mesh Sync 診断完了",
        icon="INFO",
    )


if __name__ == "__main__":
    main()
