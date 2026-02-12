from typing import cast, final, override
import bpy, os
from bpy.stub_internal.rna_enums import OperatorReturnItems
from bpy_extras.io_utils import ImportHelper
from bpy.types import Context, Operator
from bpy.props import StringProperty  # pyright: ignore[reportUnknownVariableType]
from . import bml, xvm

# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
@final
class ImportBml(Operator, ImportHelper):  # pyright: ignore[reportIncompatibleMethodOverride]
    "Import BML"


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

    @override
    def execute(self, context: Context) -> set[OperatorReturnItems]:
        bml_path = None
        xvm_path = None
        selected_files = cast(bpy.types.OperatorFileListElement, self.files)
        selected_files_dir = cast(str, self.directory)
        for f in selected_files:
            _noext, ext = os.path.splitext(f.name)
            ext = ext.lower()
            filepath = os.path.join(selected_files_dir, f.name)
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
        if bml_path is None:
            self.report({"ERROR"}, "No .bml selected")
            return {"CANCELLED"}
        bml_xvm = xvm.read(xvm_path) if xvm_path else None
        collections = bml.read(bml_path, bml_xvm)
        if bpy.context.scene is not None:
            for coll in collections:
                bpy.context.scene.collection.children.link(coll)
        return {"FINISHED"}
