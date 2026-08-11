from typing import cast, final
import bpy, os, re
from bpy.types import Context, Event, Operator
from bpy.props import BoolProperty, StringProperty  # pyright: ignore[reportUnknownVariableType]
from . import c_rel, n_rel, xvm
from .util import ModalStepOperator


def _guess_xvm_path(selected_files_dir: str, rel_filename: str) -> str | None:
    filename_no_ext, _filename_ext = os.path.splitext(rel_filename)
    match_variantless = re.match("map_[a-z]+[0-9]{2}", filename_no_ext)
    filename_variantless = match_variantless.group() if match_variantless else filename_no_ext
    guessed = os.path.join(selected_files_dir, filename_variantless + ".xvm")
    return guessed if os.path.isfile(guessed) else None


def _guess_xvm_from_rel(self: "ImportRel", context: Context):
    if cast(bool, self.xvm_path_touched):
        return
    rel_path = cast(str, self.rel_path)
    if not rel_path:
        return
    guessed = _guess_xvm_path(os.path.dirname(rel_path), os.path.basename(rel_path))
    self.xvm_path = guessed or ""


def _mark_xvm_touched(self: "ImportRel", context: Context):
    self.xvm_path_touched = True


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
@final
class ImportRel(ModalStepOperator, Operator):  # pyright: ignore[reportIncompatibleMethodOverride]
    "Import REL"


    bl_idname = "import_scene.rel"
    bl_label = "Import REL"

    # A standalone dialog (invoke_props_dialog, opened directly from invoke() below) rather than
    # Blender's native file browser: Blender can't have two file-select browsers open at once, so
    # the rel and xvm pickers can't both live inside one native browser's sidebar - each field's
    # own browse button opens its own single-file picker instead, which doesn't have that problem.
    rel_path: StringProperty(name="REL", subtype="FILE_PATH", update=_guess_xvm_from_rel)
    xvm_path: StringProperty(name="XVM", subtype="FILE_PATH", update=_mark_xvm_touched)
    # Tracks whether the user has ever edited xvm_path themselves, so the auto-guess (triggered
    # when rel_path changes) only ever fills it in once - otherwise re-guessing after the user
    # deliberately clears the field (to import without a texture) would immediately refill it.
    xvm_path_touched: BoolProperty(options={"HIDDEN", "SKIP_SAVE"}, default=False)  # pyright: ignore[reportUnknownVariableType]

    def invoke(self, context: Context, event: Event):  # pyright: ignore[reportIncompatibleMethodOverride]
        return context.window_manager.invoke_props_dialog(self, width=400, title="Import REL", confirm_text="Import")

    def draw(self, context: Context):
        layout = self.layout
        rel_path = cast(str, self.rel_path)
        if not (rel_path and os.path.isfile(rel_path)):
            layout.alert = True
        layout.prop(self, "rel_path")
        layout.alert = False
        xvm_path = cast(str, self.xvm_path)
        if not (xvm_path and os.path.isfile(xvm_path)):
            layout.alert = True
        layout.prop(self, "xvm_path")
        layout.alert = False

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        rel_filename = cast(str, self.rel_path)
        if not rel_filename or not os.path.isfile(rel_filename):
            self.report({"ERROR"}, "REL file not found: '{}'".format(rel_filename))
            return {"CANCELLED"}
        filename_no_ext, _filename_ext = os.path.splitext(os.path.basename(rel_filename))
        map_type_suffix = filename_no_ext[-1] if filename_no_ext else ""

        xvm_filename = cast(str, self.xvm_path) or None
        if xvm_filename and not os.path.isfile(xvm_filename):
            # The field is free-text-editable, so - unlike a file-browser selection - it can't be
            # assumed to point at a real file; never block the import over it (importing without a
            # texture is a valid, deliberate choice), just drop it and let the "n" case's own
            # per-texture handling deal with a missing image the same way it already does today.
            self.report({"WARNING"}, "XVM file not found: '{}' - importing without it".format(xvm_filename))
            xvm_filename = None

        if map_type_suffix == "c":
            # Fast (no textures/mesh-tree-per-tree work comparable to n.rel) - stays a plain
            # blocking call, no modal/progress bar needed.
            collection = c_rel.read(rel_filename)
            if collection is None:
                return {"CANCELLED"}
            if bpy.context.scene is not None:
                bpy.context.scene.collection.children.link(collection)
            return {"FINISHED"}
        elif map_type_suffix == "n":
            # The heavy path (per-tree mesh building, textures) - driven one tree at a time via a
            # modal timer (see ModalStepOperator in util.py) so a real progress indicator can be
            # shown instead of Blender just appearing frozen for the whole import.
            nrel_xvm = xvm.read(xvm_filename) if xvm_filename else None
            self._nrel_result: dict[str, object] = {}
            steps = n_rel.read_steps(rel_filename, nrel_xvm, self._nrel_result)
            # Prime the generator once, synchronously - this only parses the file header and
            # creates the (still-empty) Collection, cheap regardless of how many trees exist, and
            # is what makes self._nrel_result["total"] available to size the progress bar with.
            next(steps)
            total = cast(int, self._nrel_result.get("total", 0))
            return self.start_modal_steps(context, steps, total)
        elif map_type_suffix == "r":
            self.report({"ERROR"}, "Unimplemented file type for import")
            return {"CANCELLED"}
        else:
            self.report({"ERROR"}, "Could not detect REL file type based on filename. Expected a filename ending in 'n.rel', 'c.rel', or 'r.rel'.")
            return {"CANCELLED"}

    def finish(self, context: Context):
        collection = cast("bpy.types.Collection | None", self._nrel_result.get("collection"))
        if collection is not None and bpy.context.scene is not None:
            bpy.context.scene.collection.children.link(collection)
