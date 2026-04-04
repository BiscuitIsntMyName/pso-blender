import bpy
import os
from typing import cast, final
from bpy_extras.io_utils import ExportHelper
from bpy.types import Context, Operator
from bpy.props import StringProperty  # pyright: ignore[reportUnknownVariableType]
from . import bml


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
@final
class ExportBml(Operator, ExportHelper):  # pyright: ignore[reportIncompatibleMethodOverride]
    "Export BML"


    bl_idname = "export_scene.bml"
    bl_label = "Export BML"

    # ExportHelper mixin class uses this
    filename_ext = ".bml"

    filter_glob: StringProperty(
        default="*.bml",
        options={"HIDDEN"},
        maxlen=255,  # Max internal buffer length, longer would be clamped.
    )

    filepath: StringProperty(subtype="FILE_PATH")

    def execute(self, context: Context):
        filepath = cast(str, self.filepath)
        noext, _ext = os.path.splitext(filepath)
        # Valid objects are top-level objects that either have a mesh or are empty
        objs = [obj for obj in bpy.data.objects if obj.parent is None and (obj.type == "MESH" or obj.type == "EMPTY")]
        bml.write(filepath, noext + ".xvm", objs)
        return {"FINISHED"}
