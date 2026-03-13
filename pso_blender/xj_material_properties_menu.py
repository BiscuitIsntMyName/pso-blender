from dataclasses import dataclass
from typing import cast, final
import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty  # pyright: ignore[reportUnknownVariableType]
from bpy.types import Context


@dataclass
@final
class BlendMode:
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


@dataclass
@final
class TextureAddressingMode:
    D3DTADDRESS_WRAP = 3
    D3DTADDRESS_MIRROR = 4
    D3DTADDRESS_CLAMP = 5
    D3DTADDRESS_BORDER = 6
    D3DTADDRESS_MIRRORONCE = 7


@dataclass
@final
class MaterialColorSource:
    D3DMCS_MATERIAL = 0
    D3DMCS_COLOR1 = 1
    D3DMCS_COLOR2 = 3


@dataclass
@final
class NormalType:
    Vertex = 1
    Face = 2


def make_enum_prop_items(the_enum: object):
    return [(str(the_enum.__dict__[name]), name, "", i)
        for (i, name) in enumerate(the_enum.__dict__) if not name.startswith("_")]


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
class XjMaterialSettings(bpy.types.PropertyGroup):
    generate_mipmaps: BoolProperty(
        name="Generate Mipmaps",
        default=False,
        description="Generate mipmaps for this texture. Can make exporting very slow.")
    src_blend: EnumProperty(
        name="Source",
        default=str(BlendMode.D3DBLEND_SRCALPHA),
        items=make_enum_prop_items(BlendMode))
    dst_blend: EnumProperty(
        name="Destination",
        default=str(BlendMode.D3DBLEND_INVSRCALPHA),
        items=make_enum_prop_items(BlendMode))
    tex_addr_u: EnumProperty(
        name="U",
        default=str(TextureAddressingMode.D3DTADDRESS_MIRROR),
        items=make_enum_prop_items(TextureAddressingMode))
    tex_addr_v: EnumProperty(
        name="V",
        default=str(TextureAddressingMode.D3DTADDRESS_MIRROR),
        items=make_enum_prop_items(TextureAddressingMode))
    material1: IntProperty(name="Unknown 1", default=0)
    material2: IntProperty(name="Unknown 2", default=0)
    lighting: BoolProperty(name="Affected by lighting", default=True)
    camera_space_normals: BoolProperty(name="Camera space normals", default=False)
    normal_type: EnumProperty(
        name="Normal type",
        default=str(NormalType.Vertex),
        items=make_enum_prop_items(NormalType))
    diffuse_color_source: EnumProperty(
        name="Diffuse color source",
        default=str(MaterialColorSource.D3DMCS_COLOR1),
        items=make_enum_prop_items(MaterialColorSource))


class MaterialWithXjSettings(bpy.types.Material):
    xj_settings: XjMaterialSettings


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
