from enum import Enum
from typing import cast, final
import os
import bpy
import bmesh
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty  # pyright: ignore[reportUnknownVariableType]
from bpy.types import Context
from . import util


class BlendMode(Enum):
    # Not the actual d3d enum values, but indices into an array containing the enum values
    D3DBLEND_ZERO = 0
    D3DBLEND_ONE = 1
    D3DBLEND_SRCCOLOR = 2
    D3DBLEND_INVSRCCOLOR = 3
    D3DBLEND_SRCALPHA = 4
    D3DBLEND_INVSRCALPHA = 5
    D3DBLEND_DESTALPHA = 6
    D3DBLEND_INVDESTALPHA = 7
    D3DBLEND_DESTCOLOR = 8
    D3DBLEND_INVDESTCOLOR = 9


BlendMode_items = [
    ("D3DBLEND_ZERO", "D3DBLEND_ZERO", "", 0),
    ("D3DBLEND_ONE", "D3DBLEND_ONE", "", 1),
    ("D3DBLEND_SRCCOLOR", "D3DBLEND_SRCCOLOR", "", 2),
    ("D3DBLEND_INVSRCCOLOR", "D3DBLEND_INVSRCCOLOR", "", 3),
    ("D3DBLEND_SRCALPHA", "D3DBLEND_SRCALPHA", "", 4),
    ("D3DBLEND_INVSRCALPHA", "D3DBLEND_INVSRCALPHA", "", 5),
    ("D3DBLEND_DESTALPHA", "D3DBLEND_DESTALPHA", "", 6),
    ("D3DBLEND_INVDESTALPHA", "D3DBLEND_INVDESTALPHA", "", 7),
    ("D3DBLEND_DESTCOLOR", "D3DBLEND_DESTCOLOR", "", 8),
    ("D3DBLEND_INVDESTCOLOR", "D3DBLEND_INVDESTCOLOR", "", 9),
]


class TextureAddressingMode(Enum):
    D3DTADDRESS_WRAP = 3
    D3DTADDRESS_MIRROR = 4
    D3DTADDRESS_CLAMP = 5
    D3DTADDRESS_BORDER = 6
    D3DTADDRESS_MIRRORONCE = 7


TextureAddressingMode_items = [
    ("D3DTADDRESS_WRAP", "D3DTADDRESS_WRAP", "", 0),
    ("D3DTADDRESS_MIRROR", "D3DTADDRESS_MIRROR", "", 1),
    ("D3DTADDRESS_CLAMP", "D3DTADDRESS_CLAMP", "", 2),
    ("D3DTADDRESS_BORDER", "D3DTADDRESS_BORDER", "", 3),
    ("D3DTADDRESS_MIRRORONCE", "D3DTADDRESS_MIRRORONCE", "", 4),
]


class MaterialColorSource(Enum):
    D3DMCS_MATERIAL = 0
    D3DMCS_COLOR1 = 1
    D3DMCS_COLOR2 = 3


MaterialColorSource_items = [
    ("D3DMCS_MATERIAL", "D3DMCS_MATERIAL", "", 0),
    ("D3DMCS_COLOR1", "D3DMCS_COLOR1", "", 1),
    ("D3DMCS_COLOR2", "D3DMCS_COLOR2", "", 2),
]


class NormalType(Enum):
    Vertex = 1
    Face = 2


NormalType_items = [
    ("Vertex", "Vertex", "", 0),
    ("Face", "Face", "", 1)
]


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
class XjMaterialSettings(bpy.types.PropertyGroup):
    generate_mipmaps: BoolProperty(
        name="Generate Mipmaps",
        default=False,
        description="Generate mipmaps for this texture. Can make exporting very slow.")
    src_blend: EnumProperty(
        name="Source",
        default=str(BlendMode.D3DBLEND_SRCALPHA.name),
        items=BlendMode_items)
    dst_blend: EnumProperty(
        name="Destination",
        default=str(BlendMode.D3DBLEND_INVSRCALPHA.name),
        items=BlendMode_items)
    tex_addr_u: EnumProperty(
        name="U",
        default=str(TextureAddressingMode.D3DTADDRESS_MIRROR.name),
        items=TextureAddressingMode_items)
    tex_addr_v: EnumProperty(
        name="V",
        default=str(TextureAddressingMode.D3DTADDRESS_MIRROR.name),
        items=TextureAddressingMode_items)
    material1: IntProperty(name="Unknown 1", default=0)
    material2: IntProperty(name="Unknown 2", default=0)
    lighting: BoolProperty(name="Affected by lighting", default=True)
    camera_space_normals: BoolProperty(name="Camera space normals", default=False)
    normal_type: EnumProperty(
        name="Normal type",
        default=str(NormalType.Vertex.name),
        items=NormalType_items)
    diffuse_color_source: EnumProperty(
        name="Diffuse color source",
        default=str(MaterialColorSource.D3DMCS_COLOR1.name),
        items=MaterialColorSource_items)
    # The Xvr.id this material's texture had in the .xvm it was imported from. -1 means unknown
    # (material wasn't created by this addon's importer) - real PSO ids are never 0 or negative,
    # so -1 is a safe "not set" sentinel. A standalone XVM export needs this to write textures
    # back under the same id the existing, untouched .rel/.xj files expect them under.
    pso_id: IntProperty(name="PSO Texture ID", default=-1)
    # Full path to the .xvm this material's texture was imported from. Lets a standalone XVM
    # export find the original file on its own (to carry through textures the user hasn't
    # touched) without asking the user to re-locate it - Blender can't open a file browser
    # inside another file browser anyway, so there's no good way to ask for it interactively at
    # export time.
    source_xvm_path: StringProperty(name="Source XVM Path", subtype="FILE_PATH", default="")


class MaterialWithXjSettings(bpy.types.Material):
    xj_settings: XjMaterialSettings


def _get_material_image(mat: bpy.types.Material) -> bpy.types.Image | None:
    """The texture image plugged into a material's image texture node, if any.

    Different material variants of the same texture (different blend mode / addressing) each
    get their own material datablock, but they all share the same node group wrapping the actual
    Image Texture node (see get_or_create_texture_node_group in xj.py) - that image is the thing
    that actually identifies "this texture" for the user.
    """
    return util.find_diffuse_image(mat)


@final
class XjSelectMaterialEverywhere(bpy.types.Operator):
    "Select every object (optionally down to the exact faces) in the view layer that uses this texture (regardless of which material variant - blend mode / addressing - it's assigned through)"

    bl_idname = "material.xj_select_faces_everywhere"
    bl_label = "Select Objects Using This Texture"
    bl_options = {"REGISTER", "UNDO"}

    precise_face_selection: BoolProperty(
        name="Precise Face Selection",
        description="Enter Edit Mode and select only the exact faces using this texture, instead of stopping at whole-object selection",
        default=False)

    @classmethod
    def poll(cls, context: Context):
        return context.material is not None and context.view_layer is not None

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        target_mat = context.material
        view_layer = context.view_layer
        if target_mat is None or view_layer is None:
            self.report({"ERROR"}, "No active material or view layer")
            return {"CANCELLED"}

        target_image = _get_material_image(target_mat)
        if target_image is None:
            self.report({"ERROR"}, "Active material has no texture to match against")
            return {"CANCELLED"}

        if context.object is not None and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")

        matching_objects: list[bpy.types.Object] = []
        for obj in view_layer.objects:
            if obj.type != "MESH":
                continue
            mesh = cast(bpy.types.Mesh, obj.data)
            if any(slot_mat is not None and _get_material_image(slot_mat) is target_image for slot_mat in mesh.materials):
                matching_objects.append(obj)

        for obj in view_layer.objects:
            obj.select_set(False)

        if not matching_objects:
            self.report({"WARNING"}, "No objects in the view layer use this texture")
            return {"CANCELLED"}

        for obj in matching_objects:
            obj.select_set(True)
        view_layer.objects.active = matching_objects[0]

        if not self.precise_face_selection:
            self.report({"INFO"}, "Selected {} object(s)".format(len(matching_objects)))
            return {"FINISHED"}

        bpy.ops.object.mode_set(mode="EDIT")
        for obj in matching_objects:
            mesh = cast(bpy.types.Mesh, obj.data)
            matching_slot_indices = {
                i for i, slot_mat in enumerate(mesh.materials)
                if slot_mat is not None and _get_material_image(slot_mat) is target_image
            }
            bm = bmesh.from_edit_mesh(mesh)
            for face in bm.faces:
                face.select_set(face.material_index in matching_slot_indices)
            bm.select_flush(True)
            bmesh.update_edit_mesh(mesh)

        self.report({"INFO"}, "Selected faces on {} object(s)".format(len(matching_objects)))
        return {"FINISHED"}


def _resolve_target_imggroup_tex_node(context: Context) -> tuple[bpy.types.ShaderNodeTexImage, bpy.types.Material] | str:
    """The shared ImgGroup's Image Texture node for the PSO texture currently active on the
    selected object, or an error message string if none could be resolved. Shared by every
    "Send to ImgGroup" operator regardless of where the source image comes from (a Shader Editor
    node, an Asset Browser asset...) - only the source differs between them.
    """
    obj = context.active_object
    target_mat = obj.active_material if obj is not None else None
    if target_mat is None or target_mat.node_tree is None:
        return "Select the object (and material slot) that should receive this texture first"

    settings = cast(MaterialWithXjSettings, target_mat).xj_settings
    if settings.pso_id < 0:
        return (
            "'{}' isn't a PSO texture created by this addon's import - select the object/face "
            "whose material is the texture you want to replace.").format(target_mat.name)

    img_group_node = next(
        (n for n in target_mat.node_tree.nodes
         if n.type == "GROUP" and cast(bpy.types.ShaderNodeGroup, n).node_tree is not None
         and cast(bpy.types.ShaderNodeGroup, n).node_tree.name.startswith("ImgGroup_")),
        None)
    if img_group_node is None:
        return "'{}' has no shared texture group to send this image into".format(target_mat.name)

    group_node_tree = cast(bpy.types.ShaderNodeGroup, img_group_node).node_tree
    tex_image_node = next((n for n in group_node_tree.nodes if n.type == "TEX_IMAGE"), None)
    if tex_image_node is None:
        return "'{}' texture group has no Image Texture node".format(target_mat.name)

    return (cast(bpy.types.ShaderNodeTexImage, tex_image_node), target_mat)


@final
class XjSendImageToImgGroup(bpy.types.Operator):
    "Send this Image Texture node's image into the shared ImgGroup of the PSO texture currently active on the selected object - lets a texture from any external source (e.g. an asset browser addon) be adopted as a texture-pack replacement without rebuilding a material's node graph by hand"

    bl_idname = "node.xj_send_to_imggroup"
    bl_label = "Send to ImgGroup (PSO)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context):
        node = context.active_node
        if node is None or node.type != "TEX_IMAGE":
            return False
        return cast(bpy.types.ShaderNodeTexImage, node).image is not None

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        source_node = cast(bpy.types.ShaderNodeTexImage, context.active_node)
        source_image = source_node.image
        if source_image is None:
            self.report({"ERROR"}, "This node has no image")
            return {"CANCELLED"}

        resolved = _resolve_target_imggroup_tex_node(context)
        if isinstance(resolved, str):
            self.report({"ERROR"}, resolved)
            return {"CANCELLED"}
        tex_image_node, target_mat = resolved

        tex_image_node.image = source_image
        settings = cast(MaterialWithXjSettings, target_mat).xj_settings
        self.report({"INFO"}, "Sent '{}' to PSO texture id {} ('{}')".format(source_image.name, settings.pso_id, target_mat.name))
        return {"FINISHED"}


def draw_send_to_imggroup_menu_item(self: bpy.types.Menu, context: Context):
    node = context.active_node
    if node is not None and node.type == "TEX_IMAGE" and self.layout is not None:
        self.layout.separator()
        _ = self.layout.operator(XjSendImageToImgGroup.bl_idname, icon="EXPORT")


def _resolve_asset_image(asset: bpy.types.AssetRepresentation) -> bpy.types.Image | None:
    """The usable Image behind an Asset Browser asset - directly if it's an Image asset, or its
    Base Color texture if it's a Material asset (see util.find_material_base_color_image).
    Appends the asset's datablock from its source library first if it isn't already local to
    this file (the normal case for an external library like Poly Haven's).
    """
    datablock = asset.local_id
    if datablock is None:
        if not asset.full_library_path:
            return None
        with bpy.data.libraries.load(asset.full_library_path, link=False) as (data_from, data_to):
            if asset.id_type == "IMAGE" and asset.name in data_from.images:
                data_to.images = [asset.name]
            elif asset.id_type == "MATERIAL" and asset.name in data_from.materials:
                data_to.materials = [asset.name]
        if asset.id_type == "IMAGE":
            datablock = data_to.images[0] if data_to.images else None
        elif asset.id_type == "MATERIAL":
            datablock = data_to.materials[0] if data_to.materials else None

    if isinstance(datablock, bpy.types.Image):
        return datablock
    if isinstance(datablock, bpy.types.Material):
        return util.find_material_base_color_image(datablock)
    return None


@final
class XjSendAssetToImgGroup(bpy.types.Operator):
    "Send this Asset Browser asset's image into the shared ImgGroup of the PSO texture currently active on the selected object - works directly from the Asset Browser, no need to drop the asset into the scene first"

    bl_idname = "asset.xj_send_to_imggroup"
    bl_label = "Send to ImgGroup (PSO)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context):
        asset = context.asset
        return asset is not None and asset.id_type in {"IMAGE", "MATERIAL"}

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        asset = context.asset
        if asset is None:
            self.report({"ERROR"}, "No asset selected")
            return {"CANCELLED"}

        source_image = _resolve_asset_image(asset)
        if source_image is None:
            self.report({"ERROR"}, "Could not find a usable image in '{}'".format(asset.name))
            return {"CANCELLED"}

        resolved = _resolve_target_imggroup_tex_node(context)
        if isinstance(resolved, str):
            self.report({"ERROR"}, resolved)
            return {"CANCELLED"}
        tex_image_node, target_mat = resolved

        tex_image_node.image = source_image
        settings = cast(MaterialWithXjSettings, target_mat).xj_settings
        self.report({"INFO"}, "Sent '{}' to PSO texture id {} ('{}')".format(source_image.name, settings.pso_id, target_mat.name))
        return {"FINISHED"}


def draw_send_asset_to_imggroup_menu_item(self: bpy.types.Menu, context: Context):
    asset = context.asset
    if asset is not None and asset.id_type in {"IMAGE", "MATERIAL"} and self.layout is not None:
        self.layout.separator()
        _ = self.layout.operator(XjSendAssetToImgGroup.bl_idname, icon="EXPORT")


@final
class XjSendFileToImgGroup(bpy.types.Operator):
    "Send this file browser selection into the shared ImgGroup of the PSO texture currently active on the selected object - works directly on a texture file on disk, no need to load it as an asset first"

    bl_idname = "file.xj_send_to_imggroup"
    bl_label = "Send to ImgGroup (PSO)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context):
        return bool(context.selected_files)

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        selected_files = context.selected_files
        space = context.space_data
        if not selected_files or space is None or space.type != "FILE_BROWSER":
            self.report({"ERROR"}, "No file selected")
            return {"CANCELLED"}

        directory = cast(bpy.types.SpaceFileBrowser, space).params.directory
        if isinstance(directory, bytes):
            directory = directory.decode("utf-8")
        filepath = os.path.join(directory, selected_files[0].name)
        if not os.path.isfile(filepath):
            self.report({"ERROR"}, "'{}' is not a file".format(filepath))
            return {"CANCELLED"}

        try:
            source_image = bpy.data.images.load(filepath, check_existing=True)
        except RuntimeError as e:
            self.report({"ERROR"}, "Could not load '{}' as an image: {}".format(filepath, e))
            return {"CANCELLED"}

        resolved = _resolve_target_imggroup_tex_node(context)
        if isinstance(resolved, str):
            self.report({"ERROR"}, resolved)
            return {"CANCELLED"}
        tex_image_node, target_mat = resolved

        tex_image_node.image = source_image
        settings = cast(MaterialWithXjSettings, target_mat).xj_settings
        self.report({"INFO"}, "Sent '{}' to PSO texture id {} ('{}')".format(source_image.name, settings.pso_id, target_mat.name))
        return {"FINISHED"}


def draw_send_file_to_imggroup_menu_item(self: bpy.types.Menu, context: Context):
    if context.selected_files and self.layout is not None:
        self.layout.separator()
        _ = self.layout.operator(XjSendFileToImgGroup.bl_idname, icon="EXPORT")


@final
class XjMaterialSettingsPanel(bpy.types.Panel):
    bl_label = "XJ Settings"
    bl_idname = "MATERIAL_PT_XjMaterialSettingsPanel"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"

    @classmethod
    def poll(cls, context: Context):
        return context.material is not None
    
    def draw(self, context: Context):
        if self.layout is not None and context.material is not None:
            self.layout.use_property_split = True
            self.layout.use_property_decorate = False
            settings = cast(MaterialWithXjSettings, context.material).xj_settings
            select_col = self.layout.column(align=True)
            select_objects_op = cast(XjSelectMaterialEverywhere, select_col.operator(
                XjSelectMaterialEverywhere.bl_idname, text="Select Objects Using This Texture", icon="RESTRICT_SELECT_OFF"))
            select_objects_op.precise_face_selection = False
            select_faces_op = cast(XjSelectMaterialEverywhere, select_col.operator(
                XjSelectMaterialEverywhere.bl_idname, text="Select Faces Using This Texture (Edit Mode)", icon="EDITMODE_HLT"))
            select_faces_op.precise_face_selection = True
            self.layout.prop(settings, "generate_mipmaps")
            self.layout.prop(settings, "lighting")
            # Alpha blending
            blend_box = self.layout.box()
            blend_box.label(text="Alpha blending mode")
            blend_input_col = blend_box.column(align=True)
            blend_input_col.prop(settings, "src_blend")
            blend_input_col.prop(settings, "dst_blend")
            # Texture addressing
            tex_addr_box = self.layout.box()
            tex_addr_box.label(text="Texture addressing mode")
            tex_addr_input_col = tex_addr_box.column(align=True)
            tex_addr_input_col.prop(settings, "tex_addr_u")
            tex_addr_input_col.prop(settings, "tex_addr_v")
            # Other
            self.layout.prop(settings, "camera_space_normals")
            self.layout.prop(settings, "normal_type")
            self.layout.prop(settings, "diffuse_color_source")
            self.layout.prop(settings, "material1")
            self.layout.prop(settings, "material2")
            self.layout.prop(settings, "pso_id")
            self.layout.prop(settings, "source_xvm_path")
