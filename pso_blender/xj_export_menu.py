from typing import cast, final, override
import bpy, os
from bpy.stub_internal.rna_enums import OperatorReturnItems
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

    @override
    def execute(self, context: Context) -> set[OperatorReturnItems]:
        filepath = cast(str, self.filepath)
        noext, _ext = os.path.splitext(filepath)
        # Valid objects are top-level objects that either have a mesh or are empty
        objs = [obj for obj in bpy.data.objects if obj.parent is None and (obj.type == "MESH" or obj.type == "EMPTY")]
        xj.write(filepath, noext + ".xvm", objs)
        return {"FINISHED"}
