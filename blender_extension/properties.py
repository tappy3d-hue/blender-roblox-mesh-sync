from __future__ import annotations

import bpy
from bpy.props import (
    BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
    FloatVectorProperty, IntProperty, StringProperty,
)
from bpy.types import AddonPreferences, PropertyGroup

from .core import srgb_color_to_linear
from .material_library import MATERIAL_ITEMS


def _update_material_preview(settings, _context):
    owner = settings.id_data
    if isinstance(owner, bpy.types.Object):
        if settings.mesh_appearance_mode == "MATERIAL" and not settings.mesh_use_roblox_material:
            settings.mesh_use_roblox_material = True
            return
        owner.color = (*settings.color, 1.0 - settings.transparency)
        if owner.type == "MESH":
            from .material_preview import refresh_object_preview
            refresh_object_preview(owner)


_USE_ROBLOX_MATERIAL_KEY = "rbx_mesh_use_roblox_material"
_BLENDER_APPEARANCE_KEY = "rbx_mesh_blender_appearance_mode"
_BLENDER_APPEARANCE_VALUE = {"AUTO": 0, "TEXTURE": 1, "VERTEX": 2, "NONE": 3}


def _get_use_roblox_material(settings):
    """Keep the old Auto behavior for files saved before this toggle existed."""

    if _USE_ROBLOX_MATERIAL_KEY in settings:
        return bool(settings[_USE_ROBLOX_MATERIAL_KEY])
    mode = getattr(settings, "mesh_appearance_mode", "AUTO")
    if mode == "MATERIAL":
        return True
    if mode != "AUTO":
        return False
    owner = settings.id_data
    if not isinstance(owner, bpy.types.Object) or owner.type != "MESH":
        return True
    from .material_preview import has_authored_texture
    color_attributes = getattr(owner.data, "color_attributes", None)
    has_colors = bool(color_attributes and color_attributes.active)
    return not has_authored_texture(owner) and not has_colors


def _set_use_roblox_material(settings, value):
    settings[_USE_ROBLOX_MATERIAL_KEY] = bool(value)


def _update_use_roblox_material(settings, context):
    if not settings.mesh_use_roblox_material and settings.mesh_appearance_mode == "MATERIAL":
        settings.mesh_appearance_mode = "AUTO"
        return
    _update_material_preview(settings, context)


def _get_blender_appearance_mode(settings):
    if _BLENDER_APPEARANCE_KEY in settings:
        return int(settings[_BLENDER_APPEARANCE_KEY])
    return _BLENDER_APPEARANCE_VALUE.get(
        getattr(settings, "mesh_appearance_mode", "AUTO"), 0,
    )


def _set_blender_appearance_mode(settings, value):
    settings[_BLENDER_APPEARANCE_KEY] = int(value)


def _update_preview_scale(settings, _context):
    scene = settings.id_data
    if not isinstance(scene, bpy.types.Scene):
        return
    from .material_preview import refresh_object_preview, remove_unused_preview_materials
    for obj in scene.objects:
        if obj.type == "MESH" and hasattr(obj, "rbx_primitive_sync"):
            if obj.rbx_primitive_sync.mesh_material_preview:
                refresh_object_preview(obj)
    remove_unused_preview_materials()


def _update_tube_geometry(settings, _context):
    owner = settings.id_data
    if not isinstance(owner, bpy.types.Object) or owner.type != "MESH":
        return
    if not settings.is_roblox_part or settings.part_type != "Tube":
        return
    from .geometry import create_mesh, mesh_signature
    old_mesh = owner.data
    new_mesh = create_mesh(
        "Tube",
        tube_segments=settings.tube_segments,
        tube_inner_ratio=settings.tube_inner_ratio,
    )
    for material in old_mesh.materials:
        new_mesh.materials.append(material)
    owner.data = new_mesh
    owner["rbx_mesh_signature"] = mesh_signature(new_mesh)
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)
    from .material_preview import refresh_object_preview
    refresh_object_preview(owner)


PART_TYPE_ITEMS = (
    ("Block", "Block", "Roblox block Part"),
    ("Ball", "Ball", "Roblox ball Part"),
    ("Cylinder", "Cylinder", "Roblox cylinder Part"),
    ("Wedge", "Wedge", "Roblox wedge Part"),
    ("CornerWedge", "Corner Wedge", "Roblox corner wedge Part"),
    ("Tube", "Tube", "Polygonal tube rebuilt from standard WedgeParts"),
)

MESH_APPEARANCE_ITEMS = (
    ("AUTO", "Auto", "Use PBR images, then vertex colors, otherwise no texture"),
    ("TEXTURE", "Texture / PBR", "Use completed Base Color, Roughness, Metallic and Normal images"),
    ("VERTEX", "Vertex Color", "Use the active existing Color Attribute"),
    ("MATERIAL", "Roblox Material (Legacy)", "Enable Use Roblox Material instead"),
    ("NONE", "None", "Use the mesh with no texture or special material"),
)

BLENDER_APPEARANCE_ITEMS = (
    ("AUTO", "Auto", "Use PBR images, then vertex colors, otherwise no texture", 0),
    ("TEXTURE", "Texture / PBR", "Use completed Base Color, Roughness, Metallic and Normal images", 1),
    ("VERTEX", "Vertex Color", "Use the active existing Color Attribute", 2),
    ("NONE", "None", "Use the mesh with no texture or special material", 3),
)

COLLISION_FIDELITY_ITEMS = (
    ("Box", "Box", "Fast box collision"),
    ("Hull", "Hull", "Convex hull collision"),
    ("Default", "Default", "Roblox default collision"),
    ("PreciseConvexDecomposition", "Precise", "Most accurate and most expensive collision"),
)

CONVERSION_TYPE_ITEMS = (
    ("AUTO", "Auto Detect", "Detect the closest supported Roblox primitive"),
    *PART_TYPE_ITEMS,
)

REVERSE_RESOLUTION_ITEMS = (
    ("KEEP_BLENDER", "Keep Blender", "Do not overwrite the locally edited object"),
    ("APPLY_STUDIO", "Use Studio", "Replace the local object with the received Studio version"),
)

REVERSE_TOPOLOGY_ITEMS = (
    ("TRIANGLES", "Exact Triangles", "Keep the exact Roblox triangles, UVs, colors, and split normals"),
    ("QUADS", "Join to Quads", "Join only compatible adjacent triangles into editable quads"),
    ("NGONS", "Dissolve Coplanar", "Dissolve compatible coplanar triangles into N-gons"),
)

EMPTY_EXPORT_MODE_ITEMS = (
    ("MODEL", "Model", "Create a Studio Model so the Empty group can be selected and moved"),
    ("FOLDER", "Folder", "Create a Studio Folder for Explorer organization only"),
    ("IGNORE", "Do Not Send", "Do not create a Studio container for the Empty"),
)

EMPTY_EXPORT_OVERRIDE_ITEMS = (
    ("INHERIT", "Use Scene Default", "Use the scene Empty Export Mode"),
    *EMPTY_EXPORT_MODE_ITEMS,
)


class RBX_PG_ReverseConflict(PropertyGroup):
    object_id: StringProperty(name="Object ID")
    object_name: StringProperty(name="Object")
    resolution: EnumProperty(
        name="Resolution", items=REVERSE_RESOLUTION_ITEMS, default="KEEP_BLENDER",
    )


class RBX_AP_AddonPreferences(AddonPreferences):
    bl_idname = __package__

    mesh_sync_token: StringProperty(
        name="Mesh Sync Connection Token",
        default="",
        options={"HIDDEN"},
    )


class RBX_PG_ObjectSettings(PropertyGroup):
    is_roblox_part: BoolProperty(name="Roblox Part", default=False)
    sync_enabled: BoolProperty(name="Sync Enabled", default=True)
    guid: StringProperty(name="Object ID", default="")
    part_type: EnumProperty(name="Part Type", items=PART_TYPE_ITEMS, default="Block")
    tube_inner_ratio: FloatProperty(
        name="Inner Radius", min=0.05, max=0.95, default=0.5,
        description="Inner radius as a fraction of the outer radius",
        update=_update_tube_geometry,
    )
    tube_segments: IntProperty(
        name="Segments", min=3, max=64, default=16,
        description="Polygon segments; each segment becomes four WedgeParts",
        update=_update_tube_geometry,
    )
    material: EnumProperty(
        name="Material", items=MATERIAL_ITEMS, default="Plastic",
        update=_update_material_preview,
    )
    color: FloatVectorProperty(
        name="Color", subtype="COLOR", size=3, min=0.0, max=1.0,
        default=srgb_color_to_linear((163 / 255, 162 / 255, 165 / 255)),
        update=_update_material_preview,
    )
    transparency: FloatProperty(
        name="Transparency", min=0.0, max=1.0, default=0.0,
        update=_update_material_preview,
    )
    anchored: BoolProperty(name="Anchored", default=True)
    can_collide: BoolProperty(name="Can Collide", default=True)
    can_touch: BoolProperty(name="Can Touch", default=True)
    can_query: BoolProperty(name="Can Query", default=True)
    cast_shadow: BoolProperty(name="Cast Shadow", default=True)
    mesh_sync_enabled: BoolProperty(name="Mesh Sync Enabled", default=True)
    mesh_use_roblox_material: BoolProperty(
        name="Use Roblox Material",
        description="Send the selected built-in Roblox Material instead of reading the Blender material",
        get=_get_use_roblox_material,
        set=_set_use_roblox_material,
        update=_update_use_roblox_material,
    )
    mesh_appearance_mode: EnumProperty(
        name="Appearance", items=MESH_APPEARANCE_ITEMS, default="AUTO",
        update=_update_material_preview,
    )
    mesh_blender_appearance_mode: EnumProperty(
        name="Blender Appearance",
        items=BLENDER_APPEARANCE_ITEMS,
        get=_get_blender_appearance_mode,
        set=_set_blender_appearance_mode,
        update=_update_material_preview,
    )
    mesh_material_preview: BoolProperty(
        name="Live Material Preview", default=True,
        description="Preview Roblox Material and Color on this Blender object",
        update=_update_material_preview,
    )
    collision_fidelity: EnumProperty(
        name="Collision", items=COLLISION_FIDELITY_ITEMS, default="Hull",
    )
    empty_export_mode: EnumProperty(
        name="Studio Representation",
        items=EMPTY_EXPORT_OVERRIDE_ITEMS,
        default="INHERIT",
        description="How this Empty is represented in Roblox Studio",
    )


class RBX_PG_SceneSettings(PropertyGroup):
    model_name: StringProperty(name="Model Name", default="BlenderModel")
    studs_per_unit: FloatProperty(
        name="Studs per Blender Unit", min=0.000001, max=100000.0, default=1.0,
        update=_update_preview_scale,
    )
    conversion_type: EnumProperty(
        name="Conversion Type", items=CONVERSION_TYPE_ITEMS, default="AUTO",
    )
    conversion_tolerance: FloatProperty(
        name="Tolerance", description="Maximum normalized shape deviation",
        min=0.0001, max=0.25, default=0.04, precision=4,
    )
    keep_conversion_backup: BoolProperty(
        name="Keep Backup", description="Keep hidden copies of source meshes",
        default=True,
    )
    mesh_sync_port: IntProperty(
        name="Port", min=1024, max=65535, default=27182,
    )
    mesh_sync_token: StringProperty(
        name="Connection Token", default="",
        description="Local token used by the Studio plugin",
    )
    mesh_sync_export_position: BoolProperty(
        name="Position", default=True,
        description="Update position when sending to Studio; new objects always receive an initial position",
    )
    mesh_sync_export_rotation: BoolProperty(
        name="Rotation", default=True,
        description="Update rotation when sending to Studio; new objects always receive an initial rotation",
    )
    mesh_sync_export_scale: BoolProperty(
        name="Scale", default=True,
        description="Update Size when sending to Studio; new objects always receive an initial size",
    )
    mesh_empty_export_mode: EnumProperty(
        name="Empty Export Mode",
        items=EMPTY_EXPORT_MODE_ITEMS,
        default="MODEL",
        description="Default Studio representation for Blender Empty parents",
    )
    reverse_pending_revision: IntProperty(name="Incoming Revision", default=0)
    reverse_conflicts: CollectionProperty(type=RBX_PG_ReverseConflict)
    reverse_auto_apply: BoolProperty(
        name="Auto Apply from Studio",
        default=True,
        description="Apply Studio sends immediately; Blender Undo remains available",
    )
    reverse_preserve_hierarchy: BoolProperty(
        name="Preserve Blender Hierarchy",
        default=True,
        description="Keep existing Collection memberships and object parents when applying Studio updates",
    )
    reverse_preserve_geometry: BoolProperty(
        name="Preserve Blender Geometry",
        default=True,
        description="Keep Mesh data, modifiers, linked data, topology, and object origin for Blender-owned objects",
    )
    reverse_mesh_topology: EnumProperty(
        name="Mesh Topology",
        items=REVERSE_TOPOLOGY_ITEMS,
        default="TRIANGLES",
        description="Topology reconstruction used for new Studio MeshParts or destructive replacement",
    )
    reverse_merge_by_distance: BoolProperty(
        name="Merge by Distance",
        default=False,
        description="Merge nearby imported MeshPart vertices before topology reconstruction",
    )
    reverse_merge_distance: FloatProperty(
        name="Merge Distance",
        default=0.0001,
        min=0.0,
        max=1.0,
        precision=6,
        subtype="DISTANCE",
        description="Vertex merge distance in Blender units",
    )
    reverse_auto_apply_error: StringProperty(name="Auto Apply Error", default="")
    reverse_last_warning: StringProperty(name="Last Reverse Warning", default="")


CLASSES = (
    RBX_PG_ReverseConflict,
    RBX_AP_AddonPreferences,
    RBX_PG_ObjectSettings,
    RBX_PG_SceneSettings,
)
