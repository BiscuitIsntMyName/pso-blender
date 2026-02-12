import os
from typing import cast, final, override
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

    @override
    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        filepath = cast(str, self.filepath)
        noext, _ext = os.path.splitext(filepath)
        bml.write(filepath, noext + ".xvm")
        return {"FINISHED"}
