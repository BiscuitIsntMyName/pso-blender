
from collections.abc import Callable
from typing import Any, cast, final
import bpy
from bpy.types import Panel, Context
from bpy.props import BoolProperty, IntProperty  # pyright: ignore[reportUnknownVariableType]
from . import c_rel


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
class MeshRelSettings(bpy.types.PropertyGroup):
    is_nrel: BoolProperty(name="N.REL")
    is_crel: BoolProperty(name="C.REL")
    is_rrel: BoolProperty(name="R.REL")
    receives_shadows: BoolProperty(name="Receives shadows", default=True)
    receives_fog: BoolProperty(name="Affected by fog", default=True)
    is_chunk: BoolProperty(name="Chunk marker", description="Object is used as a chunk marker. All meshes are automatically assigned to the nearest chunk marker.", default=False)
    # Can't figure out how to get IntProperty to support a 32bit unsigned value so I'll just split it into two 16bit values
    collision_flags_value1: IntProperty(default=0, subtype="UNSIGNED")
    collision_flags_value2: IntProperty(default=0, subtype="UNSIGNED")
    is_translucent: BoolProperty(name="Translucent", default=False)
    always_rendered: BoolProperty(name="Always rendered", description="Object will not be affected by view distance and will always be rendered.", default=False)
    is_stencil_viewer: BoolProperty(name="Stencil viewer", description="Stenciled objects will be visible when rendered behind this object.", default=False)
    is_stenciled: BoolProperty(name="Stenciled", description="Object will only be visible when rendered behind a stencil viewer", default=False)
    exclude_from_relief_displacement: BoolProperty(name="Exclude from relief displacement", description="Skip this object when running Apply Relief Displacement (relief_displace_menu.py)", default=False)


class ObjectWithRelSettings(bpy.types.Object):
    rel_settings: MeshRelSettings


def make_bitfield_props(setting_name: str, name_map: dict[int, str]) -> list[str]:
    prop_keys: list[str] = []

    def make_flag_getter(flag: int) -> Callable[[MeshRelSettings], bool]:
        return lambda settings: (getattr(settings, setting_name) & flag) != 0

    def make_flag_setter(flag: int) -> Callable[[MeshRelSettings, Any], None]:
        return lambda settings, value: (
                setattr(settings, setting_name, getattr(settings, setting_name) | flag)
                if value else
                setattr(settings, setting_name, getattr(settings, setting_name) & ~flag))

    for flag, name in name_map.items():
        key = setting_name + "_" + hex(flag)
        label = "{} ({})".format(name, hex(flag))
        prop = BoolProperty(
            name=label,
            default=False,
            get=make_flag_getter(flag),
            set=make_flag_setter(flag))
        MeshRelSettings.__annotations__[key] = prop
        prop_keys.append(key)
    
    return prop_keys


COLLISION_FLAG_PROP_KEYS = make_bitfield_props("collision_flags_value1", c_rel.COLLISION_FLAG_TYPES)


@final
class MeshNrelSettingsPanel(Panel):
    bl_label = "N.REL"
    bl_idname = "OBJECT_PT_MeshNrelSettingsPanel"
    bl_parent_id = "OBJECT_PT_MeshRelSettingsPanel"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context: Context):
        return context.object is not None and context.object.type == "MESH"

    def draw_header(self, context: Context):
        if context.object is not None and self.layout is not None:
            self.layout.prop(cast(ObjectWithRelSettings, context.object).rel_settings, "is_nrel", text="")
    
    def draw(self, context: Context):
        if self.layout is not None and context.object is not None:
            self.layout.use_property_split = True
            self.layout.use_property_decorate = False
            settings = cast(ObjectWithRelSettings, context.object).rel_settings
            self.layout.active = cast(bool, settings.is_nrel)
            col = self.layout.column(align=True)
            col.prop(settings, "receives_shadows")
            col.prop(settings, "receives_fog")
            col.prop(settings, "is_translucent")
            col.prop(settings, "is_stencil_viewer")
            col.prop(settings, "is_stenciled")
            col.prop(settings, "exclude_from_relief_displacement")


@final
class MeshCrelSettingsPanel(Panel):
    bl_label = "C.REL"
    bl_idname = "OBJECT_PT_MeshCrelSettingsPanel"
    bl_parent_id = "OBJECT_PT_MeshRelSettingsPanel"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context: Context):
        return context.object is not None and context.object.type == "MESH"

    def draw_header(self, context: Context):
        if self.layout is not None:
            self.layout.prop(cast(ObjectWithRelSettings, context.object).rel_settings, "is_crel", text="")
    
    def draw(self, context: Context):
        if self.layout is not None and context.object is not None:
            self.layout.use_property_split = True
            self.layout.use_property_decorate = False
            settings = cast(ObjectWithRelSettings, context.object).rel_settings
            self.layout.active = cast(bool, settings.is_crel)
            combined_collision_flag = cast(int, settings.collision_flags_value1) | (cast(int, settings.collision_flags_value2) << 16)
            self.layout.row(align=True).label(text="Collision flags: " + hex(combined_collision_flag))
            col = self.layout.column(heading="Collision type:", align=True)
            for prop_key in COLLISION_FLAG_PROP_KEYS:
                col.prop(settings, prop_key)


@final
class MeshRrelSettingsPanel(Panel):
    bl_label = "R.REL"
    bl_idname = "OBJECT_PT_MeshRrelSettingsPanel"
    bl_parent_id = "OBJECT_PT_MeshRelSettingsPanel"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context: Context):
        return context.object is not None and context.object.type == "MESH"

    def draw_header(self, context: Context):
        if self.layout is not None:
            self.layout.prop(cast(ObjectWithRelSettings, context.object).rel_settings, "is_rrel", text="")
    
    def draw(self, context: Context):
        if self.layout is not None and context.object is not None:
            settings = cast(ObjectWithRelSettings, context.object).rel_settings
            self.layout.active = cast(bool, settings.is_rrel)
            self.layout.label(text="Nothing here yet")


@final
class MeshRelSettingsPanel(Panel):
    bl_label = "REL Settings"
    bl_idname = "OBJECT_PT_MeshRelSettingsPanel"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    @classmethod
    def poll(cls, context: Context):
        return context.object is not None and context.object.type == "MESH"
    
    def draw(self, context: Context):
        if self.layout is not None and context.object is not None:
            self.layout.use_property_split = True
            self.layout.use_property_decorate = False
            settings = cast(ObjectWithRelSettings, context.object).rel_settings
            self.layout.prop(settings, "is_chunk")
            self.layout.prop(settings, "always_rendered")
