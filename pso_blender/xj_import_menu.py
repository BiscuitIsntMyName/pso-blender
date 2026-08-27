import os, bpy
from typing import cast, final
from bpy_extras.io_utils import ImportHelper
from bpy.types import Context, Operator
from bpy.props import StringProperty  # pyright: ignore[reportUnknownVariableType]
from . import xj, xvm


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
@final
class ImportXj(Operator, ImportHelper):  # pyright: ignore[reportIncompatibleMethodOverride]
    # This is the tooltip when you hover over the import button. Blender 4.4 seems to have a bug that causes a crash if the tooltip is empty lol
    "Import XJ"


    bl_idname = "import_scene.xj"
    bl_label = "Import XJ"
    # Deliberately excludes 'UNDO' - see the identical comment on ImportRel (rel_import_menu.py):
    # importing creates many interdependent datablocks (objects, materials, shared ImgGroup node
    # groups, images), and redoing that many cross-referencing datablocks from a single undo step
    # has been observed to corrupt shared node group wiring. Delete the resulting objects manually
    # to remove an import instead of relying on Ctrl+Z.
    bl_options = {"REGISTER"}

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

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
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
        # See the identical use_global_undo comment on ModalStepOperator.start_modal_steps
        # (util.py) - this import creates many interdependent datablocks in one go, which has been
        # observed to corrupt shared node group wiring on a later Undo+Redo if global undo stays on.
        prev_use_global_undo = context.preferences.edit.use_global_undo
        context.preferences.edit.use_global_undo = False
        try:
            xj_xvm = xvm.read(xvm_path) if xvm_path else None
            collections = xj.read(xj_path, xj_xvm)
            if bpy.context.scene is not None:
                for coll in collections:
                    bpy.context.scene.collection.children.link(coll)
            return {"FINISHED"}
        finally:
            context.preferences.edit.use_global_undo = prev_use_global_undo
