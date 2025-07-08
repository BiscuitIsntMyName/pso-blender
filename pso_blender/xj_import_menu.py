import os, bpy
from bpy_extras.io_utils import ImportHelper
from bpy.types import Operator
from bpy.props import StringProperty
from . import xj, xvm


class ImportXj(Operator, ImportHelper):
    # This is the tooltip when you hover over the import button. Blender 4.4 seems to have a bug that causes a crash if the tooltip is empty lol
    "Import XJ"


    bl_idname = "import_scene.xj"
    bl_label = "Import XJ"

    filter_glob: StringProperty(
        default="*.xj;*.xvm",
        options={"HIDDEN"},
        maxlen=255,
    )

    # Needed for multifile import
    files: bpy.props.CollectionProperty(
        type=bpy.types.OperatorFileListElement,
        options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH")

    def execute(self, context):
        xj_path = None
        xvm_path = None
        for f in self.files:
            noext, ext = os.path.splitext(f.name)
            ext = ext.lower()
            filepath = os.path.join(self.directory, f.name)
            if ext == ".xj":
                if xj_path:
                    self.report({"ERROR"}, "Only one .xj may be imported at once")
                    return {"CANCELLED"}
                xj_path = filepath
            elif ext == ".xvm":
                if xvm_path:
                    self.report({"ERROR"}, "Only one .xvm may be imported at once")
                    return {"CANCELLED"}
                xvm_path = filepath
            else:
                self.report({"ERROR"}, "Expected .xj or .xvm, was \"{}\"".format(ext))
                return {"CANCELLED"}

        xj_xvm = xvm.read(xvm_path) if xvm_path else None
        collections = xj.read(xj_path, xj_xvm)
        for coll in collections:
            bpy.context.scene.collection.children.link(coll)
        return {"FINISHED"}
