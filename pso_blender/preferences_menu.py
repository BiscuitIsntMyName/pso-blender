import shutil
from typing import final
import bpy
from bpy.types import Operator, AddonPreferences, Context
from . import xvm


@final
class PsoClearXvrCache(Operator):
    "Delete every cached compressed texture (.xvr) - the next export of each will recompress from scratch instead of reusing a cached copy"


    bl_idname = "pso_blender.clear_xvr_cache"
    bl_label = "Clear Texture Cache"

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        shutil.rmtree(xvm.xvr_cache_base_dir(), ignore_errors=True)
        self.report({"INFO"}, "PSO Blender: texture cache cleared.")
        return {"FINISHED"}


@final
class PsoBlenderAddonPreferences(AddonPreferences):
    # Must match this addon's actual root package name at runtime (whatever prefix the Blender
    # Extensions system assigns it, e.g. "bl_ext.user_default.pso_blender") - __package__ inside
    # this submodule always resolves to exactly that, so it can't drift out of sync the way a
    # hardcoded string could.
    bl_idname = __package__

    def draw(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        layout = self.layout
        if layout:
            layout.label(text="Exported textures (.xvr) are cached in Blender's user data folder to speed up repeated exports.")
            layout.operator(PsoClearXvrCache.bl_idname, icon="TRASH")
