"""Non-destructive previews built from Roblox Studio's exported materials.

The visible patterns come exclusively from the MTL and PNG files bundled in
``assets/roblox_materials``. Preview materials are attached through an
object-linked slot so objects sharing one Mesh datablock can still have
different appearances without changing the mesh signature.
"""

from __future__ import annotations

import hashlib
import json

import bpy
from bpy.types import Operator

from .i18n import trf

from .material_library import (
    EMISSIVE_MATERIALS,
    FIXED_ROUGHNESS,
    MATERIAL_NAMES,
    METALLIC_MATERIALS,
    TRANSMISSIVE_MATERIALS,
    TEXTURE_TILE_STUDS,
    material_files,
    parse_mtl,
)


PREVIEW_ACTIVE_KEY = "rbx_material_preview_active"
PREVIEW_STATE_KEY = "rbx_material_preview_original"
PREVIEW_MATERIAL_KEY = "rbx_material_preview"
MESH_PREVIEW_STATE_KEY = "rbx_material_preview_mesh_original"
PREVIEW_LIBRARY_VERSION = "studio-export-projected-v4"
PREVIEW_COMPLETE_KEY = "rbx_material_preview_complete"


def _socket(node, name, value):
    socket = node.inputs.get(name)
    if socket is not None:
        socket.default_value = value


def _appearance_key(material_name, color, transparency, studs_per_unit):
    payload = json.dumps(
        [
            PREVIEW_LIBRARY_VERSION,
            material_name,
            [round(float(v), 5) for v in color],
            round(float(transparency), 5),
            round(float(studs_per_unit), 5),
        ],
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_image(path, non_color=False):
    if path is None or not path.is_file():
        return None
    resolved = str(path.resolve())
    image = next(
        (candidate for candidate in bpy.data.images if bpy.path.abspath(candidate.filepath) == resolved),
        None,
    )
    if image is None:
        image = bpy.data.images.load(resolved, check_existing=True)
    image["rbx_material_library"] = PREVIEW_LIBRARY_VERSION
    if non_color:
        try:
            image.colorspace_settings.name = "Non-Color"
        except (AttributeError, TypeError):
            pass
    return image


def _projected_coordinates(nodes, links, studs_per_unit):
    geometry = nodes.new("ShaderNodeNewGeometry")
    geometry.name = "Roblox World Coordinates"
    geometry.label = "World Position"
    geometry.location = (-1260, 80)
    scale = nodes.new("ShaderNodeVectorMath")
    scale.name = "Roblox Stud Tiling"
    scale.label = f"{TEXTURE_TILE_STUDS:g} studs per tile"
    scale.operation = "SCALE"
    scale.location = (-1050, 80)
    scale_input = scale.inputs.get("Scale")
    if scale_input is not None:
        scale_input.default_value = max(0.000001, float(studs_per_unit)) / TEXTURE_TILE_STUDS
    links.new(geometry.outputs["Position"], scale.inputs[0])
    return scale.outputs["Vector"]


def _image_node(nodes, links, path, vector, location, label, *, non_color=False):
    image = _load_image(path, non_color=non_color)
    if image is None:
        return None
    node = nodes.new("ShaderNodeTexImage")
    node.image = image
    node.name = f"Roblox {label}"
    node.label = label
    node.location = location
    node.extension = "REPEAT"
    node.interpolation = "Linear"
    node.projection = "BOX"
    node.projection_blend = 0.15
    links.new(vector, node.inputs["Vector"])
    return node


def _build_material(material, material_name, color, transparency, studs_per_unit):
    if material_name not in MATERIAL_NAMES:
        raise ValueError(trf("Roblox Material is not in the library: {material}", material=material_name))
    material.use_nodes = True
    material.diffuse_color = (*color, 1.0 - transparency)
    material[PREVIEW_MATERIAL_KEY] = True
    material["rbx_material_preview_source"] = PREVIEW_LIBRARY_VERSION
    material["rbx_material_name"] = material_name
    material[PREVIEW_COMPLETE_KEY] = False
    tree = material.node_tree
    tree.nodes.clear()
    nodes, links = tree.nodes, tree.links
    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (500, 100)
    shader = nodes.new("ShaderNodeBsdfPrincipled")
    shader.location = (160, 100)
    files = material_files(material_name)
    mtl = parse_mtl(files["mtl"])
    has_texture = any(files[role] is not None for role in ("diffuse", "specular", "normal"))
    vector = _projected_coordinates(nodes, links, studs_per_unit) if has_texture else None

    _socket(shader, "Base Color", (*color, 1.0))
    shininess = max(0.0, float(mtl["shininess"]))
    roughness = FIXED_ROUGHNESS.get(
        material_name,
        max(0.08, min(1.0, (2.0 / (shininess + 2.0)) ** 0.5)),
    )
    _socket(shader, "Roughness", roughness)
    _socket(shader, "Metallic", 1.0 if material_name in METALLIC_MATERIALS else 0.0)
    _socket(shader, "Alpha", max(0.0, 1.0 - transparency))
    _socket(shader, "IOR", 1.45)

    diffuse = _image_node(
        nodes, links, files["diffuse"], vector, (-820, 360), "Diffuse",
    )
    if diffuse is not None:
        tint = nodes.new("ShaderNodeMixRGB")
        tint.name = "Roblox Color Tint"
        tint.label = "Color Tint"
        tint.location = (-420, 360)
        tint.blend_type = "MULTIPLY"
        tint.inputs[0].default_value = 1.0
        tint.inputs[2].default_value = (*color, 1.0)
        links.new(diffuse.outputs["Color"], tint.inputs[1])
        links.new(tint.outputs["Color"], shader.inputs["Base Color"])

    specular = _image_node(
        nodes, links, files["specular"], vector, (-820, 40), "Specular", non_color=True,
    )
    if specular is not None:
        grayscale = nodes.new("ShaderNodeRGBToBW")
        grayscale.location = (-520, 40)
        roughness_map = nodes.new("ShaderNodeMapRange")
        roughness_map.name = "Roblox Specular to Roughness"
        roughness_map.label = "Specular to Roughness"
        roughness_map.location = (-260, 40)
        _socket(roughness_map, "From Min", 0.0)
        _socket(roughness_map, "From Max", 1.0)
        _socket(roughness_map, "To Min", 0.95)
        _socket(roughness_map, "To Max", 0.12)
        roughness_map.clamp = True
        links.new(specular.outputs["Color"], grayscale.inputs["Color"])
        links.new(grayscale.outputs["Val"], roughness_map.inputs["Value"])
        links.new(roughness_map.outputs["Result"], shader.inputs["Roughness"])

    normal = _image_node(
        nodes, links, files["normal"], vector, (-820, -300), "Normal", non_color=True,
    )
    if normal is not None and shader.inputs.get("Normal") is not None:
        normal_map = nodes.new("ShaderNodeNormalMap")
        normal_map.location = (-340, -300)
        normal_map.space = "TANGENT"
        _socket(normal_map, "Strength", 1.0)
        links.new(normal.outputs["Color"], normal_map.inputs["Color"])
        links.new(normal_map.outputs["Normal"], shader.inputs["Normal"])

    if material_name in TRANSMISSIVE_MATERIALS:
        _socket(shader, "Transmission Weight", 0.88)
        _socket(shader, "Coat Weight", 0.25)
    if material_name in EMISSIVE_MATERIALS:
        _socket(shader, "Emission Color", (*color, 1.0))
        _socket(shader, "Emission Strength", EMISSIVE_MATERIALS[material_name])
        if material_name == "ForceField":
            _socket(shader, "Metallic", 0.2)
            _socket(shader, "Alpha", min(0.55, max(0.15, 1.0 - transparency)))

    links.new(shader.outputs["BSDF"], output.inputs["Surface"])
    material[PREVIEW_COMPLETE_KEY] = True
    try:
        material.surface_render_method = "DITHERED"
    except (AttributeError, TypeError):
        if hasattr(material, "blend_method"):
            material.blend_method = "BLEND"


def preview_material(material_name, color, transparency, studs_per_unit=1.0):
    key = _appearance_key(material_name, color, transparency, studs_per_unit)
    name = f"RPS Preview {material_name} {key[:10]}"
    material = bpy.data.materials.get(name)
    if material is None:
        material = bpy.data.materials.new(name)
    if (
        material.get("rbx_material_preview_source") != PREVIEW_LIBRARY_VERSION
        or not material.get(PREVIEW_COMPLETE_KEY)
        or not material.use_nodes
        or material.node_tree is None
    ):
        _build_material(
            material,
            material_name,
            tuple(color),
            float(transparency),
            float(studs_per_unit),
        )
    return material


def _material_has_authored_texture(material):
    if (
        not material
        or material.get(PREVIEW_MATERIAL_KEY)
        or not material.use_nodes
        or not material.node_tree
    ):
        return False
    return any(node.type == "TEX_IMAGE" and node.image for node in material.node_tree.nodes)


def _has_authored_texture(obj):
    # While a preview is active, inspect the captured source materials instead
    # of mistaking the generated preview's own Image Texture nodes for authored data.
    raw = obj.get(PREVIEW_STATE_KEY) if obj.get(PREVIEW_ACTIVE_KEY) else None
    if raw:
        try:
            state = json.loads(raw)
            return any(
                _material_has_authored_texture(bpy.data.materials.get(name))
                for name in state.get("names", []) if name
            )
        except (TypeError, ValueError):
            pass
    return _material_has_authored_texture(obj.active_material)


def has_authored_texture(obj):
    """Return whether the authored material uses an image, ignoring our preview."""

    return _has_authored_texture(obj)


def _capture_state(obj):
    if PREVIEW_STATE_KEY in obj:
        return
    mesh_state = None
    if obj.data and obj.data.get(MESH_PREVIEW_STATE_KEY):
        try:
            mesh_state = json.loads(obj.data[MESH_PREVIEW_STATE_KEY])
        except (TypeError, ValueError):
            mesh_state = None
    state = {
        "count": len(obj.material_slots),
        "names": [slot.material.name if slot.material else "" for slot in obj.material_slots],
        "links": [slot.link for slot in obj.material_slots],
    }
    if mesh_state and int(mesh_state.get("count", 0)) == 0:
        state = mesh_state
    elif obj.data and MESH_PREVIEW_STATE_KEY not in obj.data:
        obj.data[MESH_PREVIEW_STATE_KEY] = json.dumps(state, ensure_ascii=False)
    obj[PREVIEW_STATE_KEY] = json.dumps(state, ensure_ascii=False)


def restore_object_preview(obj):
    if not isinstance(obj, bpy.types.Object) or obj.type != "MESH":
        return
    raw = obj.get(PREVIEW_STATE_KEY)
    if not raw:
        obj.pop(PREVIEW_ACTIVE_KEY, None)
        return
    try:
        state = json.loads(raw)
    except (TypeError, ValueError):
        state = {"count": 0, "names": [], "links": []}
    for index in range(min(int(state.get("count", 0)), len(obj.material_slots))):
        slot = obj.material_slots[index]
        slot.link = state.get("links", [])[index] if index < len(state.get("links", [])) else "DATA"
        name = state.get("names", [])[index] if index < len(state.get("names", [])) else ""
        slot.material = bpy.data.materials.get(name) if name else None
    obj.pop(PREVIEW_ACTIVE_KEY, None)
    obj.pop(PREVIEW_STATE_KEY, None)

    if obj.data:
        other_active = any(
            candidate != obj and candidate.type == "MESH" and candidate.data == obj.data
            and bool(candidate.get(PREVIEW_ACTIVE_KEY))
            for candidate in bpy.data.objects
        )
        if int(state.get("count", 0)) == 0 and len(obj.data.materials) == 1:
            if other_active:
                obj.material_slots[0].link = "DATA"
                obj.data.materials[0] = None
            else:
                obj.data.materials.clear()
        if not other_active:
            obj.data.pop(MESH_PREVIEW_STATE_KEY, None)


def remove_unused_preview_materials():
    """Remove superseded generated previews without touching authored materials."""

    removed = 0
    for material in tuple(bpy.data.materials):
        if material.get(PREVIEW_MATERIAL_KEY) and material.users == 0:
            bpy.data.materials.remove(material)
            removed += 1
    return removed


def refresh_object_preview(obj):
    if not isinstance(obj, bpy.types.Object) or obj.type != "MESH" or not hasattr(obj, "rbx_primitive_sync"):
        return
    settings = obj.rbx_primitive_sync
    use_preview = bool(settings.mesh_material_preview) and (
        settings.is_roblox_part or settings.mesh_use_roblox_material
    )
    obj.color = (*settings.color, 1.0 - settings.transparency)
    if not use_preview:
        restore_object_preview(obj)
        return

    _capture_state(obj)
    scene = getattr(bpy.context, "scene", None)
    studs_per_unit = (
        scene.rbx_primitive_sync.studs_per_unit
        if scene is not None and hasattr(scene, "rbx_primitive_sync") else 1.0
    )
    material = preview_material(
        settings.material,
        settings.color,
        settings.transparency,
        studs_per_unit,
    )
    if not obj.material_slots:
        obj.data.materials.append(material)
    slot = obj.material_slots[0]
    slot.link = "OBJECT"
    slot.material = material
    obj[PREVIEW_ACTIVE_KEY] = True


def apply_object_material_override(obj, material):
    """Assign a reversible object-level material without changing shared Mesh ownership."""

    if not isinstance(obj, bpy.types.Object) or obj.type != "MESH":
        return
    _capture_state(obj)
    if not obj.material_slots:
        obj.data.materials.append(None)
    slot = obj.material_slots[0]
    slot.link = "OBJECT"
    slot.material = material
    obj[PREVIEW_ACTIVE_KEY] = True


def refresh_selected(context):
    count = 0
    for obj in context.selected_objects:
        if obj.type == "MESH":
            refresh_object_preview(obj)
            count += 1
    remove_unused_preview_materials()
    return count


class RBX_OT_RefreshMaterialPreview(Operator):
    bl_idname = "rbx_mesh_sync.refresh_material_preview"
    bl_label = "Refresh Material Preview"
    bl_description = "Rebuild the Roblox material preview for selected mesh objects"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return any(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        count = refresh_selected(context)
        self.report({"INFO"}, trf("Updated Roblox Material previews for {count} objects", count=count))
        return {"FINISHED"}


CLASSES = (RBX_OT_RefreshMaterialPreview,)
