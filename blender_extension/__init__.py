from __future__ import annotations

bl_info = {
    "name": "Roblox Primitive Sync",
    "author": "Roblox Primitive Sync contributors",
    "version": (0, 11, 2),
    "blender": (4, 2, 0),
    "location": "3D View > Shift+A > Roblox Parts; Sidebar > Roblox",
    "description": "Author models that rebuild as standard Roblox Parts",
    "category": "Import-Export",
}

import bpy
from bpy.app.handlers import persistent
from bpy.props import PointerProperty

from . import i18n, material_preview, mesh_sync, operators, properties, reverse_sync, ui


CLASSES = (
    *properties.CLASSES,
    *operators.CLASSES,
    *mesh_sync.CLASSES,
    *material_preview.CLASSES,
    *reverse_sync.CLASSES,
    *ui.CLASSES,
)


def _remove_named_handlers(handler_list, names):
    for handler in tuple(handler_list):
        module_name = getattr(handler, "__module__", "")
        if (
            getattr(handler, "__name__", "") in names
            and ("roblox_primitive_sync" in module_name or module_name.startswith("blender_extension"))
        ):
            try:
                handler_list.remove(handler)
            except ValueError:
                pass


def _remove_add_menu_callbacks():
    draw = getattr(bpy.types.VIEW3D_MT_add, "draw", None)
    for callback in tuple(getattr(draw, "_draw_funcs", ())):
        module_name = getattr(callback, "__module__", "")
        if (
            getattr(callback, "__name__", "") == "draw_add_menu"
            and ("roblox_primitive_sync" in module_name or module_name.startswith("blender_extension"))
        ):
            try:
                bpy.types.VIEW3D_MT_add.remove(callback)
            except (ValueError, RuntimeError):
                pass


def _cleanup_stale_registration():
    """Recover from an interrupted install or an in-place extension upgrade."""

    try:
        mesh_sync.SERVER.stop()
    except Exception:
        pass
    deferred = globals().get("_deferred_refresh_saved_material_previews")
    if deferred is not None and bpy.app.timers.is_registered(deferred):
        bpy.app.timers.unregister(deferred)
    if bpy.app.timers.is_registered(reverse_sync.auto_apply_pending_timer):
        bpy.app.timers.unregister(reverse_sync.auto_apply_pending_timer)
    _remove_named_handlers(bpy.app.handlers.load_post, {"_refresh_saved_material_previews"})
    _remove_named_handlers(
        bpy.app.handlers.depsgraph_update_post,
        {"_deduplicate_object_ids", "sync_csg_operand_transforms"},
    )
    _remove_add_menu_callbacks()
    for owner in (bpy.types.Scene, bpy.types.Object):
        if hasattr(owner, "rbx_primitive_sync"):
            try:
                delattr(owner, "rbx_primitive_sync")
            except (AttributeError, RuntimeError):
                pass
    for cls in reversed(CLASSES):
        existing = getattr(bpy.types, cls.__name__, None)
        candidates = [cls]
        if existing is not None and existing is not cls:
            candidates.insert(0, existing)
        for candidate in candidates:
            try:
                bpy.utils.unregister_class(candidate)
            except (RuntimeError, ValueError):
                pass
    i18n.unregister()


@persistent
def _deduplicate_object_ids(scene, _depsgraph):
    if scene and hasattr(scene, "rbx_primitive_sync"):
        operators.ensure_unique_guids(scene)


@persistent
def _refresh_saved_material_previews(_unused=None):
    for obj in bpy.data.objects:
        if obj.type == "MESH" and obj.get(material_preview.PREVIEW_ACTIVE_KEY):
            material_preview.refresh_object_preview(obj)
    material_preview.remove_unused_preview_materials()


def _deferred_refresh_saved_material_previews():
    """Wait until Blender releases the restricted registration-time data proxy."""

    try:
        _refresh_saved_material_previews()
    except AttributeError:
        return 0.25
    return None


def register():
    _cleanup_stale_registration()
    registered = []
    try:
        i18n.register()
        for cls in CLASSES:
            bpy.utils.register_class(cls)
            registered.append(cls)
        bpy.types.Object.rbx_primitive_sync = PointerProperty(type=properties.RBX_PG_ObjectSettings)
        bpy.types.Scene.rbx_primitive_sync = PointerProperty(type=properties.RBX_PG_SceneSettings)
        bpy.types.VIEW3D_MT_add.append(ui.draw_add_menu)
        bpy.app.handlers.depsgraph_update_post.append(_deduplicate_object_ids)
        bpy.app.handlers.load_post.append(_refresh_saved_material_previews)
        bpy.app.timers.register(_deferred_refresh_saved_material_previews, first_interval=0.1)
        bpy.app.timers.register(
            reverse_sync.auto_apply_pending_timer, first_interval=0.25, persistent=True,
        )
    except Exception:
        _cleanup_stale_registration()
        for cls in reversed(registered):
            if getattr(bpy.types, cls.__name__, None) is not None:
                try:
                    bpy.utils.unregister_class(cls)
                except (RuntimeError, ValueError):
                    pass
        raise


def unregister():
    _cleanup_stale_registration()


if __name__ == "__main__":
    register()
