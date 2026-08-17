from __future__ import annotations

import bpy
from bpy.types import Menu, Panel

from .mesh_sync_server import SERVER
from .properties import PART_TYPE_ITEMS
from .tube import estimated_tube_part_count
from .validation import estimated_scene_part_count


class RBX_MT_AddMenu(Menu):
    bl_idname = "RBX_MT_primitive_sync_add"
    bl_label = "Roblox Parts"

    def draw(self, context):
        layout = self.layout
        for identifier, label, _description in PART_TYPE_ITEMS:
            operator = layout.operator("rbx_primitive_sync.add_part", text=label)
            operator.part_type = identifier


def draw_add_menu(self, context):
    if not hasattr(context.scene, "rbx_primitive_sync"):
        return
    self.layout.separator()
    self.layout.menu(RBX_MT_AddMenu.bl_idname, text="Roblox Parts")


class RBX_PT_MainPanel(Panel):
    bl_idname = "RBX_PT_primitive_sync_main"
    bl_label = "Primitive Sync"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Roblox"

    def draw(self, context):
        layout = self.layout
        if not hasattr(context.scene, "rbx_primitive_sync"):
            layout.label(text="Add-on registration incomplete. Reload the extension.", icon="ERROR")
            return
        scene_settings = context.scene.rbx_primitive_sync

        layout.prop(scene_settings, "model_name")
        layout.prop(scene_settings, "studs_per_unit")
        layout.label(
            text=f"Estimated Studio Parts: {estimated_scene_part_count(context.scene)}",
            icon="INFO",
        )

        box = layout.box()
        box.label(text="Add Part")
        grid = box.grid_flow(columns=2, even_columns=True, align=True)
        for identifier, label, _description in PART_TYPE_ITEMS:
            operator = grid.operator("rbx_primitive_sync.add_part", text=label)
            operator.part_type = identifier

        conversion = layout.box()
        conversion.label(text="Convert Existing Meshes")
        conversion.prop(scene_settings, "conversion_type")
        conversion.prop(scene_settings, "conversion_tolerance")
        conversion.prop(scene_settings, "keep_conversion_backup")
        conversion.operator("rbx_primitive_sync.convert_selected", icon="MODIFIER")

        layout.separator()
        layout.operator("rbx_primitive_sync.validate_scene", icon="CHECKMARK")
        layout.operator("rbx_primitive_sync.repair_guids", icon="FILE_REFRESH")
        layout.operator("rbx_primitive_sync.export_json", icon="EXPORT")


class RBX_PT_ObjectPanel(Panel):
    bl_idname = "RBX_PT_primitive_sync_object"
    bl_label = "Selected Part"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Roblox"
    bl_parent_id = RBX_PT_MainPanel.bl_idname

    @classmethod
    def poll(cls, context):
        return bool(
            context.active_object
            and hasattr(context.active_object, "rbx_primitive_sync")
            and context.active_object.rbx_primitive_sync.is_roblox_part
        )

    def draw(self, context):
        layout = self.layout
        settings = context.active_object.rbx_primitive_sync
        layout.label(text=f"Type: {settings.part_type}")
        layout.prop(settings, "sync_enabled")
        object_id_row = layout.row()
        object_id_row.enabled = False
        object_id_row.prop(settings, "guid")
        layout.prop(settings, "material")
        layout.prop(settings, "color")
        layout.prop(settings, "transparency")
        layout.prop(settings, "anchored")

        if settings.part_type == "Tube":
            tube = layout.box()
            tube.label(text="Tube Approximation")
            tube.prop(settings, "tube_inner_ratio")
            tube.prop(settings, "tube_segments")
            count = estimated_tube_part_count(settings.tube_segments)
            tube.label(text=f"Studio Parts: {count} WedgeParts", icon="INFO")
            if count > 128:
                tube.label(text="High Part count", icon="ERROR")

        collision = layout.box()
        collision.label(text="Collision")
        collision.prop(settings, "can_collide")
        collision.prop(settings, "can_touch")
        collision.prop(settings, "can_query")
        collision.prop(settings, "cast_shadow")


class RBX_PT_MeshSyncPanel(Panel):
    bl_idname = "RBX_PT_mesh_sync_main"
    bl_label = "Mesh Sync"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Roblox"

    def draw(self, context):
        layout = self.layout
        if not hasattr(context.scene, "rbx_primitive_sync"):
            layout.label(text="Add-on registration incomplete. Reload the extension.", icon="ERROR")
            return
        settings = context.scene.rbx_primitive_sync

        connection = layout.box()
        connection.label(text="Studio Connection")
        connection.prop(settings, "mesh_sync_port")
        row = connection.row(align=True)
        row.operator("rbx_mesh_sync.start_server", text="Start")
        row.operator("rbx_mesh_sync.stop_server", text="Stop")
        connection.operator("rbx_mesh_sync.allow_pairing", icon="LINKED")
        if SERVER.running:
            connection.label(text=f"Running on 127.0.0.1:{SERVER.port}", icon="CHECKMARK")
            if SERVER.pairing_active:
                connection.label(
                    text=f"Studio pairing allowed ({SERVER.pairing_seconds_remaining}s)",
                    icon="UNLOCKED",
                )
        else:
            connection.label(text="Server stopped", icon="PAUSE")
        transform = connection.box()
        transform.label(text="Blender to Studio Transform")
        row = transform.row(align=True)
        row.prop(settings, "mesh_sync_export_position", toggle=True)
        row.prop(settings, "mesh_sync_export_rotation", toggle=True)
        row.prop(settings, "mesh_sync_export_scale", toggle=True)
        hierarchy = connection.box()
        hierarchy.label(text="Blender to Studio Hierarchy")
        hierarchy.prop(settings, "mesh_empty_export_mode")
        result = SERVER.last_result
        if isinstance(result, dict):
            if result.get("ok"):
                connection.label(
                    text=f"Studio imported revision {result.get('revision', '?')}",
                    icon="CHECKMARK",
                )
                connection.label(
                    text=(
                        f"Objects: {result.get('addedInstances', 0)} added / "
                        f"{result.get('updatedInstances', 0)} updated"
                    ),
                )
                connection.label(
                    text=(
                        f"Assets: {result.get('uploadedMeshes', 0) + result.get('uploadedImages', 0)} new / "
                        f"{result.get('reusedMeshes', 0) + result.get('reusedImages', 0)} reused"
                    ),
                )
            else:
                stage = result.get("stage", "unknown stage")
                connection.label(text=f"Studio failed during {stage}", icon="ERROR")

        incoming = layout.box()
        incoming.label(text="Studio to Blender")
        incoming.prop(settings, "reverse_auto_apply")
        incoming.prop(settings, "reverse_preserve_hierarchy")
        incoming.prop(settings, "reverse_preserve_geometry")
        topology = incoming.box()
        topology.label(text="Reconstructed Studio Meshes")
        topology.prop(settings, "reverse_mesh_topology")
        topology.prop(settings, "reverse_merge_by_distance")
        distance = topology.row()
        distance.enabled = settings.reverse_merge_by_distance
        distance.prop(settings, "reverse_merge_distance")
        if not settings.reverse_preserve_geometry:
            incoming.label(text="Mesh replacement removes modifiers and source topology", icon="ERROR")
        if settings.reverse_auto_apply_error:
            incoming.label(text=settings.reverse_auto_apply_error, icon="ERROR")
        if settings.reverse_last_warning:
            incoming.label(text=settings.reverse_last_warning, icon="INFO")
        pending = SERVER.pending_reverse
        if pending is None:
            incoming.label(
                text="Waiting; Studio sends apply immediately" if settings.reverse_auto_apply else "No incoming selection",
                icon="IMPORT",
            )
        else:
            incoming.label(
                text=f"Revision {pending.revision}: {len(pending.document.get('objects', []))} objects",
                icon="IMPORT",
            )
            if settings.reverse_auto_apply:
                incoming.label(text="Applying automatically (Undo available)", icon="TIME")
            else:
                incoming.operator("rbx_mesh_sync.review_incoming", icon="VIEWZOOM")
            if settings.reverse_pending_revision == pending.revision:
                for conflict in settings.reverse_conflicts:
                    row = incoming.row(align=True)
                    row.label(text=conflict.object_name, icon="ERROR")
                    row.prop(conflict, "resolution", text="")
                row = incoming.row(align=True)
                row.operator("rbx_mesh_sync.apply_incoming", icon="CHECKMARK")
                row.operator("rbx_mesh_sync.discard_incoming", icon="X")

        selected_meshes = [obj for obj in context.selected_objects if obj.type == "MESH"]
        summary = layout.box()
        selected_parts = sum(
            1 for obj in selected_meshes
            if obj.rbx_primitive_sync.is_roblox_part and obj.rbx_primitive_sync.sync_enabled
        )
        selected_mesh_parts = sum(
            1 for obj in selected_meshes
            if not obj.rbx_primitive_sync.is_roblox_part and obj.rbx_primitive_sync.mesh_sync_enabled
        )
        summary.label(text=f"Selected: {selected_parts} Parts / {selected_mesh_parts} MeshParts")
        active_mesh = context.active_object if context.active_object and context.active_object.type == "MESH" else None
        link_row = summary.row()
        link_row.enabled = bool(
            context.mode == "OBJECT"
            and active_mesh
            and any(obj.type == "MESH" and obj != active_mesh for obj in context.selected_objects)
        )
        link_row.operator("rbx_mesh_sync.link_mesh_data", icon="LINKED")
        if active_mesh and len(selected_meshes) > 1:
            summary.label(text=f"Link target: active object '{active_mesh.name}'", icon="INFO")
            summary.label(text="Also shares UVs, vertex colors, and materials.")
        row = summary.row()
        row.scale_y = 1.35
        row.enabled = bool(selected_meshes)
        row.operator("rbx_mesh_sync.send_selected", icon="EXPORT")
        summary.label(text="Studio must be connected before sending.", icon="INFO")


class RBX_PT_MeshSyncObjectPanel(Panel):
    bl_idname = "RBX_PT_mesh_sync_object"
    bl_label = "Selected Mesh Settings"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Roblox"
    bl_parent_id = RBX_PT_MeshSyncPanel.bl_idname

    @classmethod
    def poll(cls, context):
        return bool(
            context.active_object
            and context.active_object.type == "MESH"
            and hasattr(context.active_object, "rbx_primitive_sync")
        )

    def draw(self, context):
        layout = self.layout
        settings = context.active_object.rbx_primitive_sync
        if settings.is_roblox_part:
            layout.label(text=f"Studio Type: Part ({settings.part_type})", icon="MESH_CUBE")
            layout.prop(settings, "sync_enabled")
            layout.prop(settings, "material")
            layout.prop(settings, "color")
            layout.prop(settings, "transparency")
        else:
            layout.label(text="Studio Type: MeshPart", icon="MESH_DATA")
            layout.prop(settings, "mesh_sync_enabled")
            layout.prop(settings, "mesh_use_roblox_material")
            if settings.mesh_use_roblox_material:
                roblox_box = layout.box()
                roblox_box.prop(settings, "material")
                roblox_box.prop(settings, "color")
            else:
                layout.prop(settings, "mesh_blender_appearance_mode")
            layout.prop(settings, "mesh_material_preview")
            layout.prop(settings, "transparency")
        layout.operator("rbx_mesh_sync.refresh_material_preview", icon="MATERIAL")
        if not settings.is_roblox_part:
            layout.prop(settings, "collision_fidelity")
        layout.prop(settings, "anchored")

        collision = layout.box()
        collision.label(text="Collision and Rendering")
        collision.prop(settings, "can_collide")
        collision.prop(settings, "can_touch")
        collision.prop(settings, "can_query")
        collision.prop(settings, "cast_shadow")
        if len([obj for obj in context.selected_objects if obj.type == "MESH"]) > 1:
            layout.operator("rbx_mesh_sync.apply_settings", icon="PASTEDOWN")


class RBX_PT_MeshSyncEmptyPanel(Panel):
    bl_idname = "RBX_PT_mesh_sync_empty"
    bl_label = "Selected Empty Settings"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Roblox"
    bl_parent_id = RBX_PT_MeshSyncPanel.bl_idname

    @classmethod
    def poll(cls, context):
        return bool(
            context.active_object
            and context.active_object.type == "EMPTY"
            and hasattr(context.active_object, "rbx_primitive_sync")
        )

    def draw(self, context):
        self.layout.prop(context.active_object.rbx_primitive_sync, "empty_export_mode")


CLASSES = (
    RBX_MT_AddMenu,
    RBX_PT_MainPanel,
    RBX_PT_ObjectPanel,
    RBX_PT_MeshSyncPanel,
    RBX_PT_MeshSyncObjectPanel,
    RBX_PT_MeshSyncEmptyPanel,
)
