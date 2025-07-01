import bpy, os
from bpy_extras.io_utils import ExportHelper
from bpy.types import Operator
from bpy.props import StringProperty
from . import xj


class ExportXj(Operator, ExportHelper):
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

    def execute(self, context):
        noext, ext = os.path.splitext(self.filepath)
        # Valid objects are top-level objects that either have a mesh or are empty
        objs = [obj for obj in bpy.data.objects if obj.parent is None and (obj.type == "MESH" or obj.type == "EMPTY")]
        xj.write(self.filepath, noext + ".xvm", objs)
        return {"FINISHED"}
    
    def draw(self, context):
        pass
