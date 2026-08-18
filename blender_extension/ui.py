from __future__ import annotations

from bpy.types import Menu, Panel

from .mesh_sync_server import SERVER
from .i18n import trf
from .properties import PART_TYPE_ITEMS
from .tube import estimated_tube_part_count
from .validation import estimated_scene_part_count


def _selected_sync_objects(context):
    return [obj for obj in context.selected_objects if obj.type in {"MESH", "EMPTY"}]


def _draw_connection_status(layout):
    row = layout.row(align=True)
    if SERVER.running:
        row.label(text=trf("Studio connection ready · Port {port}", port=SERVER.port), icon="CHECKMARK")
        row.operator("rbx_mesh_sync.stop_server", text="Stop")
    else:
        row.label(text="Studio connection stopped", icon="PAUSE")
        row.operator("rbx_mesh_sync.start_server", text="Start")

    if SERVER.pairing_active:
        layout.label(
            text=trf("Studio pairing allowed ({seconds}s)", seconds=SERVER.pairing_seconds_remaining),
            icon="UNLOCKED",
        )
    elif SERVER.running:
        layout.operator("rbx_mesh_sync.allow_pairing", text="Allow Studio Connection", icon="LINKED")


def _draw_last_result(layout):
    result = SERVER.last_result
    if not isinstance(result, dict):
        return
    if result.get("ok"):
        layout.label(
            text=trf(
                "Last send: {added} added / {updated} updated",
                added=result.get("addedInstances", 0),
                updated=result.get("updatedInstances", 0),
            ),
            icon="CHECKMARK",
        )
    else:
        layout.label(
            text=trf("Studio failed during {stage}", stage=result.get("stage", "unknown stage")),
            icon="ERROR",
        )


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
    bl_label = "Roblox Sync"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Roblox"

    def draw(self, context):
        layout = self.layout
        if not hasattr(context.scene, "rbx_primitive_sync"):
            layout.label(text="Add-on registration incomplete. Reload the extension.", icon="ERROR")
            return
        settings = context.scene.rbx_primitive_sync

        _draw_connection_status(layout)

        selected = _selected_sync_objects(context)
        selected_meshes = [obj for obj in selected if obj.type == "MESH"]
        selected_parts = sum(
            1
            for obj in selected_meshes
            if obj.rbx_primitive_sync.is_roblox_part and obj.rbx_primitive_sync.sync_enabled
        )
        selected_mesh_parts = sum(
            1
            for obj in selected_meshes
            if not obj.rbx_primitive_sync.is_roblox_part
            and obj.rbx_primitive_sync.mesh_sync_enabled
        )
        selected_empties = sum(1 for obj in selected if obj.type == "EMPTY")
        selection_text = trf(
            "Selected: {parts} Parts / {mesh_parts} MeshParts",
            parts=selected_parts,
            mesh_parts=selected_mesh_parts,
        )
        if selected_empties:
            selection_text += trf(" / {groups} Groups", groups=selected_empties)
        layout.label(text=selection_text)
        send = layout.row()
        send.scale_y = 1.45
        send.enabled = bool(selected) and context.mode == "OBJECT"
        send.operator("rbx_mesh_sync.send_selected", text="Send Selected to Studio", icon="EXPORT")
        _draw_last_result(layout)

        pending = SERVER.pending_reverse
        if pending is None:
            layout.label(
                text="Waiting for Studio" if settings.reverse_auto_apply else "No incoming selection",
                icon="IMPORT",
            )
            return

        is_csg_preview = bool(pending.document.get("csg"))
        layout.label(
            text=(
                trf(
                    "Incoming revision {revision}: {count} CSG nodes",
                    revision=pending.revision,
                    count=len(pending.document.get("csg", [])),
                )
                if is_csg_preview
                else trf(
                    "Incoming revision {revision}: {count} objects",
                    revision=pending.revision,
                    count=len(pending.document.get("objects", [])),
                )
            ),
            icon="IMPORT",
        )
        if settings.reverse_auto_apply:
            layout.label(text="Applying automatically (Undo available)", icon="TIME")
        else:
            layout.operator("rbx_mesh_sync.review_incoming", icon="VIEWZOOM")
        if settings.reverse_pending_revision == pending.revision:
            for conflict in settings.reverse_conflicts:
                row = layout.row(align=True)
                row.label(text=conflict.object_name, icon="ERROR")
                row.prop(conflict, "resolution", text="")
            row = layout.row(align=True)
            row.operator("rbx_mesh_sync.apply_incoming", icon="CHECKMARK")
            row.operator("rbx_mesh_sync.discard_incoming", icon="X")


class RBX_PT_CreatePanel(Panel):
    bl_idname = "RBX_PT_primitive_sync_create"
    bl_label = "Create & Convert"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Roblox"
    bl_parent_id = RBX_PT_MainPanel.bl_idname
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.rbx_primitive_sync
        layout.label(
            text=trf("Estimated Studio Parts: {count}", count=estimated_scene_part_count(context.scene)),
            icon="INFO",
        )
        grid = layout.grid_flow(columns=2, even_columns=True, align=True)
        for identifier, label, _description in PART_TYPE_ITEMS:
            operator = grid.operator("rbx_primitive_sync.add_part", text=label)
            operator.part_type = identifier

        conversion = layout.box()
        conversion.label(text="Convert Existing Meshes")
        conversion.prop(settings, "conversion_type")
        conversion.prop(settings, "conversion_tolerance")
        conversion.prop(settings, "keep_conversion_backup")
        conversion.operator("rbx_primitive_sync.convert_selected", icon="MODIFIER")


class RBX_PT_ObjectPanel(Panel):
    bl_idname = "RBX_PT_primitive_sync_object"
    bl_label = "Selected Object"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Roblox"
    bl_parent_id = RBX_PT_MainPanel.bl_idname

    @classmethod
    def poll(cls, context):
        return bool(
            context.active_object
            and context.active_object.type in {"MESH", "EMPTY"}
            and hasattr(context.active_object, "rbx_primitive_sync")
        )

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        settings = obj.rbx_primitive_sync
        if obj.type == "EMPTY":
            layout.label(text="Studio Type: Model / Folder", icon="OUTLINER_OB_EMPTY")
            layout.prop(settings, "empty_export_mode")
            layout.label(text="Selecting this group sends its enabled descendants.", icon="INFO")
            return

        if settings.is_roblox_part:
            layout.label(text=trf("Studio Type: Part ({part_type})", part_type=settings.part_type), icon="MESH_CUBE")
            layout.prop(settings, "sync_enabled")
            layout.prop(settings, "material")
            layout.prop(settings, "color")
            layout.prop(settings, "transparency")
            if settings.part_type == "Tube":
                tube = layout.box()
                tube.label(text="Tube Approximation")
                tube.prop(settings, "tube_inner_ratio")
                tube.prop(settings, "tube_segments")
                count = estimated_tube_part_count(settings.tube_segments)
                tube.label(text=trf("Studio Parts: {count} WedgeParts", count=count), icon="INFO")
                if count > 128:
                    tube.label(text="High Part count", icon="ERROR")
        else:
            layout.label(text="Studio Type: MeshPart", icon="MESH_DATA")
            layout.prop(settings, "mesh_sync_enabled")
            layout.prop(settings, "mesh_use_roblox_material")
            if settings.mesh_use_roblox_material:
                layout.prop(settings, "material")
                layout.prop(settings, "color")
            else:
                layout.prop(settings, "mesh_blender_appearance_mode")
            layout.prop(settings, "mesh_material_preview")
            layout.prop(settings, "transparency")
            layout.prop(settings, "collision_fidelity")
        layout.prop(settings, "anchored")
        if len([selected for selected in context.selected_objects if selected.type == "MESH"]) > 1:
            layout.operator("rbx_mesh_sync.apply_settings", icon="PASTEDOWN")


class RBX_PT_AppearancePanel(Panel):
    bl_idname = "RBX_PT_mesh_sync_appearance"
    bl_label = "Appearance & Optimization"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Roblox"
    bl_parent_id = RBX_PT_MainPanel.bl_idname
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return bool(context.active_object and context.active_object.type == "MESH")

    def draw(self, context):
        layout = self.layout
        obj_settings = context.active_object.rbx_primitive_sync
        scene_settings = context.scene.rbx_primitive_sync
        layout.prop(scene_settings, "mesh_appearance_selection_scope")
        layout.prop(scene_settings, "mesh_appearance_exclude_parts")
        layout.operator("rbx_mesh_sync.select_same_appearance", icon="RESTRICT_SELECT_OFF")
        merge_row = layout.row()
        merge_row.enabled = bool(
            not obj_settings.is_roblox_part
            and len([obj for obj in context.selected_objects if obj.type == "MESH"]) > 1
        )
        merge_row.operator("rbx_mesh_sync.merge_to_active_appearance", icon="AUTOMERGE_ON")
        layout.label(text="Merge uses Ctrl+J modifier behavior.", icon="INFO")
        layout.operator("rbx_mesh_sync.refresh_material_preview", icon="MATERIAL")


class RBX_PT_PhysicsPanel(Panel):
    bl_idname = "RBX_PT_mesh_sync_physics"
    bl_label = "Physics & Rendering"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Roblox"
    bl_parent_id = RBX_PT_MainPanel.bl_idname
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        return bool(context.active_object and context.active_object.type == "MESH")

    def draw(self, context):
        layout = self.layout
        settings = context.active_object.rbx_primitive_sync
        layout.prop(settings, "can_collide")
        layout.prop(settings, "can_touch")
        layout.prop(settings, "can_query")
        layout.prop(settings, "cast_shadow")


class RBX_PT_TransferPanel(Panel):
    bl_idname = "RBX_PT_mesh_sync_transfer"
    bl_label = "Sync Settings"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Roblox"
    bl_parent_id = RBX_PT_MainPanel.bl_idname
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.rbx_primitive_sync
        layout.prop(settings, "model_name")
        layout.prop(settings, "studs_per_unit")

        connection = layout.box()
        connection.label(text="Connection")
        connection.prop(settings, "mesh_sync_port")

        outgoing = layout.box()
        outgoing.label(text="Blender to Studio")
        row = outgoing.row(align=True)
        row.prop(settings, "mesh_sync_export_position", toggle=True)
        row.prop(settings, "mesh_sync_export_rotation", toggle=True)
        row.prop(settings, "mesh_sync_export_scale", toggle=True)
        outgoing.prop(settings, "mesh_empty_export_mode")

        incoming = layout.box()
        incoming.label(text="Studio to Blender")
        incoming.prop(settings, "reverse_auto_apply")
        incoming.prop(settings, "reverse_preserve_hierarchy")
        incoming.prop(settings, "reverse_preserve_geometry")
        incoming.prop(settings, "reverse_mesh_topology")
        incoming.prop(settings, "reverse_merge_by_distance")
        distance = incoming.row()
        distance.enabled = settings.reverse_merge_by_distance
        distance.prop(settings, "reverse_merge_distance")
        if not settings.reverse_preserve_geometry:
            incoming.label(text="Mesh replacement removes modifiers and source topology", icon="ERROR")
        if settings.reverse_auto_apply_error:
            incoming.label(text=settings.reverse_auto_apply_error, icon="ERROR")
        if settings.reverse_last_warning:
            incoming.label(text=settings.reverse_last_warning, icon="INFO")


class RBX_PT_AdvancedPanel(Panel):
    bl_idname = "RBX_PT_mesh_sync_advanced"
    bl_label = "Advanced"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Roblox"
    bl_parent_id = RBX_PT_MainPanel.bl_idname
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        active_mesh = context.active_object if context.active_object and context.active_object.type == "MESH" else None
        link_row = layout.row()
        link_row.enabled = bool(
            context.mode == "OBJECT"
            and active_mesh
            and any(obj.type == "MESH" and obj != active_mesh for obj in context.selected_objects)
        )
        link_row.operator("rbx_mesh_sync.link_mesh_data", icon="LINKED")
        layout.operator("rbx_primitive_sync.validate_scene", icon="CHECKMARK")
        layout.operator("rbx_primitive_sync.repair_guids", icon="FILE_REFRESH")
        layout.operator("rbx_primitive_sync.export_json", text="Export Legacy JSON", icon="EXPORT")


CLASSES = (
    RBX_MT_AddMenu,
    RBX_PT_MainPanel,
    RBX_PT_CreatePanel,
    RBX_PT_ObjectPanel,
    RBX_PT_AppearancePanel,
    RBX_PT_PhysicsPanel,
    RBX_PT_TransferPanel,
    RBX_PT_AdvancedPanel,
)
