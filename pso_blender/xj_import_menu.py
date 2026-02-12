import os, bpy
from typing import cast, final, override
from bpy.stub_internal.rna_enums import OperatorReturnItems
from bpy_extras.io_utils import ImportHelper
from bpy.types import Context, Operator
from bpy.props import StringProperty
from . import xj, xvm


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
@final
class ImportXj(Operator, ImportHelper):  # pyright: ignore[reportIncompatibleMethodOverride]
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

    @override
    def execute(self, context: Context) -> set[OperatorReturnItems]:
        xj_path = None
        xvm_path = None
        selected_files = cast(bpy.types.OperatorFileListElement, self.files)
        selected_files_dir = cast(str, self.directory)
        for f in selected_files:
            _noext, ext = os.path.splitext(f.name)
            ext = ext.lower()
            filepath = os.path.join(selected_files_dir, f.name)
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
        if xj_path is None:
            self.report({"ERROR"}, "No .xj selected")
            return {"CANCELLED"}
        xj_xvm = xvm.read(xvm_path) if xvm_path else None
        collections = xj.read(xj_path, xj_xvm)
        if bpy.context.scene is not None:
            for coll in collections:
                bpy.context.scene.collection.children.link(coll)
        return {"FINISHED"}
