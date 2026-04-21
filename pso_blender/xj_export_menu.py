from typing import cast, final
import bpy, os
from bpy_extras.io_utils import ExportHelper
from bpy.types import Context, Operator
from bpy.props import StringProperty  # pyright: ignore[reportUnknownVariableType]
from . import xj


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
@final
class ExportXj(Operator, ExportHelper):  # pyright: ignore[reportIncompatibleMethodOverride]
    "Export XJ"


    bl_idname = "export_scene.xj"
    bl_label = "Export xj"

    # ExportHelper mixin class uses this
    filename_ext = ".xj"

    filter_glob: StringProperty(
        default="*.xj",
        options={"HIDDEN"},
        maxlen=255,  # Max internal buffer length, longer would be clamped.
    )

    filepath: StringProperty(subtype="FILE_PATH")

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        filepath = cast(str, self.filepath)
        noext, _ext = os.path.splitext(filepath)
        # Valid objects are top-level objects that either have a mesh or are empty
        view_layer = bpy.context.view_layer
        if view_layer:
            objs = [obj for obj in view_layer.objects if obj.parent is None and (obj.type == "MESH" or obj.type == "EMPTY")]
        else:
            return {"CANCELLED"}
        xj.write(filepath, noext + ".xvm", objs)
        return {"FINISHED"}
