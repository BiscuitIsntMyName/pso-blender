from typing import cast, final, override
import bpy
from bpy.props import EnumProperty  # pyright: ignore[reportUnknownVariableType]
from bpy.types import Context
from . import njcm


def make_enum_prop_items(the_enum: object):
    return [(str(the_enum.__dict__[name]), name, "", the_enum.__dict__[name])
        for (_i, name) in enumerate(the_enum.__dict__) if not name.startswith("_")]


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
class NjcmNodeSettings(bpy.types.PropertyGroup):
    eval_flags: EnumProperty(
        name="Eval Flags",
        default={str(njcm.NinjaEvalFlag.UNIT_ANG), str(njcm.NinjaEvalFlag.UNIT_SCL), str(njcm.NinjaEvalFlag.BREAK)},
        items=make_enum_prop_items(njcm.NinjaEvalFlag),
        options={"ENUM_FLAG", "ANIMATABLE"})


class ObjectWithNjcmSettings(bpy.types.Object):
    njcm_settings: NjcmNodeSettings


@final
class NjcmNodeSettingsPanel(bpy.types.Panel):
    bl_label = "NJCM settings"
    bl_idname = "MATERIAL_PT_NjcmNodeSettingsPanel"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    @classmethod
    @override
    def poll(cls, context: Context):
        return context.object is not None
    
    @override
    def draw(self, context: Context):
        if self.layout is not None and context.object is not None:
            settings = cast(ObjectWithNjcmSettings, context.object).njcm_settings
            self.layout.prop_menu_enum(settings, "eval_flags")
