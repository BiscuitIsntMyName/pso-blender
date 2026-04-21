import os
from typing import cast, final
from warnings import catch_warnings
import bpy
from bpy_extras.io_utils import ExportHelper
from bpy.props import StringProperty  # pyright: ignore[reportUnknownVariableType]
from bpy.types import Context, Operator, Object

from . import r_rel, n_rel, c_rel


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
@final
class ExportRel(Operator, ExportHelper):  # pyright: ignore[reportIncompatibleMethodOverride]
    """Export render geometry (n.rel), collision geometry (c.rel), and minimap geometry (r.rel)"""


    bl_idname = "export_scene.rel"
    bl_label = "Export REL(s)"

    # ExportHelper mixin class uses this
    filename_ext = ".rel"

    filter_glob: StringProperty(
        default="*.rel",
        options={"HIDDEN"},
        maxlen=255,  # Max internal buffer length, longer would be clamped.
    )

    filepath: StringProperty(subtype="FILE_PATH")

    def cancel_with_error(self, ex: Exception):
        self.report({"ERROR"}, str(ex))
        return {"CANCELLED"}
    
    def cancel_with_warning(self, msg: str):
        self.report({"WARNING"}, msg)
        return {"CANCELLED"}
    
    def export_all(self, minimap_objs: list[Object], render_objs: list[Object], collision_objs: list[Object], chunk_markers: list[Object]):
        filepath = cast(str, self.filepath)
        noext, ext = os.path.splitext(filepath)
        if minimap_objs and len(minimap_objs) > 0:
            r_rel.write(noext + "r" + ext, minimap_objs)
        if render_objs and len(render_objs) > 0:
            n_rel.write(noext + "n" + ext, noext + ".xvm", noext + ".tam", render_objs, chunk_markers)
        if collision_objs and len(collision_objs):
            c_rel.write(noext + "c" + ext, collision_objs)
        return {"FINISHED"}
    
    def export_all_by_tags(self):
        # Avoid circular dependency
        from .rel_properties_menu import ObjectWithRelSettings

        render_objs: list[Object] = []
        collision_objs: list[Object] = []
        minimap_objs: list[Object] = []
        chunk_markers: list[Object] = []
        view_layer = bpy.context.view_layer
        if view_layer:
            for obj in view_layer.objects:
                obj = cast(ObjectWithRelSettings, obj)
                if obj.rel_settings.is_nrel:
                    render_objs.append(obj)
                if obj.rel_settings.is_crel:
                    collision_objs.append(obj)
                if obj.rel_settings.is_rrel:
                    minimap_objs.append(obj)
                if obj.rel_settings.is_chunk:
                    chunk_markers.append(obj)
        else:
            return {"CANCELLED"}
        return self.export_all(minimap_objs, render_objs, collision_objs, chunk_markers)

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        with catch_warnings(record=True) as warnings:
            try:
                result = self.export_all_by_tags()
            finally:
                # Display warnings in the GUI
                for warning in warnings:
                    self.report({"WARNING"}, str(warning.message))
                    print(warning)
            return result
