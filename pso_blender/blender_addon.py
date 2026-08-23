from typing import Any, cast, final
import bpy
from bpy.props import PointerProperty
from bpy.app.handlers import persistent, load_post
from .rel_export_menu import ExportRel
from .rel_import_menu import ImportRel
from .rel_properties_menu import (
    MeshRelSettings,
    MeshRelSettingsPanel,
    MeshNrelSettingsPanel,
    MeshCrelSettingsPanel,
    MeshRrelSettingsPanel)
from .bml_import_menu import ImportBml
from .bml_export_menu import ExportBml
from .xj_import_menu import ImportXj
from .xj_export_menu import ExportXj
from .xvm_export_menu import ExportXvm
from .xj_material_properties_menu import (
    XjMaterialSettings,
    XjMaterialSettingsPanel,
    XjSelectMaterialEverywhere,
    XjSendImageToImgGroup,
    XjSendAssetToImgGroup,
    XjSendAssetPackToImgGroup,
    XjSendFileToImgGroup,
    draw_send_to_imggroup_menu_item,
    draw_send_asset_to_imggroup_menu_item,
    draw_send_asset_pack_to_imggroup_menu_item,
    draw_send_file_to_imggroup_menu_item)
from .njcm_node_properties_menu import NjcmNodeSettings, NjcmNodeSettingsPanel
from .preferences_menu import PsoClearXvrCache, PsoBlenderAddonPreferences
from .recent_rel_menu import ImportRecentRel, PSO_MT_import_rel_recent, PSO_PT_recent_rel
from .bake_lighting_menu import BakeLightingToVertexColors, PSO_PT_bake_lighting


# @persistent causes an error when this file is executed with fake-bpy-module (unit tests)
# We can detect if the code is actually running inside blender by checking if bpy.app.binary_path is set
if bpy.app.binary_path:
    @persistent
    def convert_legacy_properties(_arg: Any):
        for obj in bpy.data.objects:
            obj = cast(Any, obj)
            if "nrel" in obj:
                obj.rel_settings.is_nrel = True
                del obj["nrel"]
            if "crel" in obj:
                obj.rel_settings.is_crel = True
                del obj["crel"]
            if "rrel" in obj:
                obj.rel_settings.is_rrel = True
                del obj["rrel"]


rel_import_export_description = "Phantasy Star Online map (.rel)"
bml_import_export_description = "BML (PSO)"
xj_import_export_description = "XJ (PSO)"
xvm_export_description = "XVM texture pack (PSO)"


@final
class PSO_MT_export(bpy.types.Menu):  # pyright: ignore[reportIncompatibleMethodOverride]
    bl_idname = "PSO_MT_export"
    bl_label = "pso-blender"

    def draw(self, context: bpy.types.Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        layout = self.layout
        if not layout:
            return
        layout.operator(ExportRel.bl_idname, text=rel_import_export_description)
        layout.operator(ExportBml.bl_idname, text=bml_import_export_description)
        layout.operator(ExportXj.bl_idname, text=xj_import_export_description)
        layout.operator(ExportXvm.bl_idname, text=xvm_export_description)


@final
class PSO_MT_import(bpy.types.Menu):  # pyright: ignore[reportIncompatibleMethodOverride]
    bl_idname = "PSO_MT_import"
    bl_label = "pso-blender"

    def draw(self, context: bpy.types.Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        layout = self.layout
        if not layout:
            return
        # REL gets its own submenu (Select File... + recent history) rather than a plain
        # operator entry - see recent_rel_menu.py.
        layout.menu(PSO_MT_import_rel_recent.bl_idname, text=rel_import_export_description)
        layout.operator(ImportBml.bl_idname, text=bml_import_export_description)
        layout.operator(ImportXj.bl_idname, text=xj_import_export_description)


def menu_func_export(self: bpy.types.Menu, _context: bpy.types.Context):
    if self.layout:
        self.layout.menu(PSO_MT_export.bl_idname)


def menu_func_import(self: bpy.types.Menu, _context: bpy.types.Context):
    if self.layout:
        self.layout.menu(PSO_MT_import.bl_idname)


classes = [
    ExportRel,
    ImportRel,
    MeshRelSettings,
    MeshRelSettingsPanel,
    MeshNrelSettingsPanel,
    MeshCrelSettingsPanel,
    MeshRrelSettingsPanel,
    ImportBml,
    ExportBml,
    ImportXj,
    ExportXj,
    ExportXvm,
    XjMaterialSettings,
    XjMaterialSettingsPanel,
    XjSelectMaterialEverywhere,
    XjSendImageToImgGroup,
    XjSendAssetToImgGroup,
    XjSendAssetPackToImgGroup,
    XjSendFileToImgGroup,
    NjcmNodeSettings,
    NjcmNodeSettingsPanel,
    PsoClearXvrCache,
    PsoBlenderAddonPreferences,
    ImportRecentRel,
    PSO_MT_import_rel_recent,
    PSO_PT_recent_rel,
    BakeLightingToVertexColors,
    PSO_PT_bake_lighting,
    PSO_MT_import,
    PSO_MT_export
]


# Register and add to the "file selector" menu
def register():
    # Register classes
    for clazz in classes:
        bpy.utils.register_class(clazz)
    # Add buttons to export and import menus
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.NODE_MT_context_menu.append(draw_send_to_imggroup_menu_item)
    bpy.types.ASSETBROWSER_MT_context_menu.append(draw_send_asset_to_imggroup_menu_item)
    bpy.types.ASSETBROWSER_MT_context_menu.append(draw_send_asset_pack_to_imggroup_menu_item)
    bpy.types.FILEBROWSER_MT_context_menu.append(draw_send_file_to_imggroup_menu_item)
    # Add settings to objects
    object_as_any = cast(Any, bpy.types.Object)
    object_as_any.rel_settings = PointerProperty(type=MeshRelSettings)
    object_as_any.njcm_settings = PointerProperty(type=NjcmNodeSettings)
    # Add settings to materials
    material_as_any = cast(Any, bpy.types.Material)
    material_as_any.xj_settings = PointerProperty(type=XjMaterialSettings)
    # Add hooks
    load_post.append(convert_legacy_properties)


def unregister():
    for clazz in classes:
        bpy.utils.unregister_class(clazz)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.types.NODE_MT_context_menu.remove(draw_send_to_imggroup_menu_item)
    bpy.types.ASSETBROWSER_MT_context_menu.remove(draw_send_asset_to_imggroup_menu_item)
    bpy.types.ASSETBROWSER_MT_context_menu.remove(draw_send_asset_pack_to_imggroup_menu_item)
    bpy.types.FILEBROWSER_MT_context_menu.remove(draw_send_file_to_imggroup_menu_item)
    del cast(Any, bpy.types.Object).rel_settings
    del cast(Any, bpy.types.Object).njcm_settings
    del cast(Any, bpy.types.Material).xj_settings
    load_post.remove(convert_legacy_properties)
