from typing import cast, final
import bpy
from bpy.props import EnumProperty  # pyright: ignore[reportUnknownVariableType]
from bpy.types import Context
from . import njcm


NinjaEvalFlag_items = [
    ("UNIT_POS", "UNIT_POS", "", njcm.NinjaEvalFlag.UNIT_POS.value),
    ("UNIT_ANG", "UNIT_ANG", "", njcm.NinjaEvalFlag.UNIT_ANG.value),
    ("UNIT_SCL", "UNIT_SCL", "", njcm.NinjaEvalFlag.UNIT_SCL.value),
    ("HIDE", "HIDE", "", njcm.NinjaEvalFlag.HIDE.value),
    ("BREAK", "BREAK", "", njcm.NinjaEvalFlag.BREAK.value),
    ("ZXY_ANG", "ZXY_ANG", "", njcm.NinjaEvalFlag.ZXY_ANG.value),
    ("SKIP", "SKIP", "", njcm.NinjaEvalFlag.SKIP.value),
    ("SHAPE_SKIP", "SHAPE_SKIP", "", njcm.NinjaEvalFlag.SHAPE_SKIP.value),
    ("CLIP", "CLIP", "", njcm.NinjaEvalFlag.CLIP.value),
    ("MODIFIER", "MODIFIER", "", njcm.NinjaEvalFlag.MODIFIER.value),
]


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
class NjcmNodeSettings(bpy.types.PropertyGroup):
    eval_flags: EnumProperty(
        name="Eval Flags",
        default={"UNIT_ANG", "UNIT_SCL", "BREAK"},
        items=NinjaEvalFlag_items,
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
    def poll(cls, context: Context):
        return context.object is not None
    
    def draw(self, context: Context):
        if self.layout is not None and context.object is not None:
            settings = cast(ObjectWithNjcmSettings, context.object).njcm_settings
            self.layout.prop_menu_enum(settings, "eval_flags")
