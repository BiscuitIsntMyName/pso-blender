import bpy
from bpy.props import EnumProperty
from . import njcm


def make_enum_prop_items(the_enum):
    return [(str(the_enum.__dict__[name]), name, "", the_enum.__dict__[name])
        for (i, name) in enumerate(the_enum.__dict__) if not name.startswith("_")]


class NjcmNodeSettings(bpy.types.PropertyGroup):
    eval_flags: EnumProperty(
        name="Eval Flags",
        default={str(njcm.NinjaEvalFlag.UNIT_ANG), str(njcm.NinjaEvalFlag.UNIT_SCL), str(njcm.NinjaEvalFlag.BREAK)},
        items=make_enum_prop_items(njcm.NinjaEvalFlag),
        options={"ENUM_FLAG", "ANIMATABLE"})


class NjcmNodeSettingsPanel(bpy.types.Panel):
    bl_label = "NJCM settings"
    bl_idname = "MATERIAL_PT_NjcmNodeSettingsPanel"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "object"

    @classmethod
    def poll(self, context):
        return context.object is not None
    
    def draw(self, context):
        settings = context.object.njcm_settings
        self.layout.prop_menu_enum(settings, "eval_flags")
