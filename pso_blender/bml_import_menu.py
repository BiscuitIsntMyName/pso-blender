import bpy, os
from bpy_extras.io_utils import ImportHelper
from bpy.types import Operator
from bpy.props import StringProperty
from . import bml, xvm


class ImportBml(Operator, ImportHelper):
    bl_idname = "import_scene.bml"
    bl_label = "Import BML"

    filter_glob: StringProperty(
        default="*.bml;*.xvm",
        options={"HIDDEN"},
        maxlen=255,
    )

    # Needed for multifile import
    files: bpy.props.CollectionProperty(
        type=bpy.types.OperatorFileListElement,
        options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH")

    def execute(self, context):
        bml_path = None
        xvm_path = None
        for f in self.files:
            noext, ext = os.path.splitext(f.name)
            ext = ext.lower()
            filepath = os.path.join(self.directory, f.name)
            if ext == ".bml":
                if bml_path:
                    self.report({"ERROR"}, "Only one .bml may be imported at once")
                    return {"CANCELLED"}
                bml_path = filepath
            elif ext == ".xvm":
                if xvm_path:
                    self.report({"ERROR"}, "Only one .xvm may be imported at once")
                    return {"CANCELLED"}
                xvm_path = filepath
            else:
                self.report({"ERROR"}, "Expected .bml or .xvm, was \"{}\"".format(ext))
                return {"CANCELLED"}

        bml_xvm = xvm.read(xvm_path) if xvm_path else None
        collections = bml.read(bml_path, bml_xvm)
        for coll in collections:
            bpy.context.scene.collection.children.link(coll)
        return {"FINISHED"}
