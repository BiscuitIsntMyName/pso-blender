from enum import Enum
from typing import cast, final
import bpy
import bmesh
from bpy.props import BoolProperty, EnumProperty, IntProperty  # pyright: ignore[reportUnknownVariableType]
from bpy.types import Context


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


class MaterialWithXjSettings(bpy.types.Material):
    xj_settings: XjMaterialSettings


def _get_material_image(mat: bpy.types.Material) -> bpy.types.Image | None:
    """The texture image plugged into a material's image texture node, if any.

    Different material variants of the same texture (different blend mode / addressing) each
    get their own material datablock, but they all point at the same image datablock - that
    image is the thing that actually identifies "this texture" for the user.
    """
    if mat.node_tree is None:
        return None
    for node in mat.node_tree.nodes:
        if node.type == "TEX_IMAGE":
            return cast(bpy.types.ShaderNodeTexImage, node).image
    return None


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
