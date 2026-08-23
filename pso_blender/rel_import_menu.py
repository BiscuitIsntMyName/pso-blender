from typing import cast, final
import bpy, os
from bpy.types import Context, Event, Operator
from bpy.props import BoolProperty, StringProperty  # pyright: ignore[reportUnknownVariableType]
from . import c_rel, n_rel, xvm, recent_rel_menu
from .util import ModalStepOperator, variantless_map_basename


def _guess_xvm_path(selected_files_dir: str, rel_filename: str) -> str | None:
    filename_no_ext, _filename_ext = os.path.splitext(rel_filename)
    filename_variantless = variantless_map_basename(filename_no_ext)
    guessed = os.path.join(selected_files_dir, filename_variantless + ".xvm")
    return guessed if os.path.isfile(guessed) else None


def _guess_tam_path(selected_files_dir: str, rel_filename: str) -> str | None:
    # Unlike .xvm, a .tam keeps the same segment suffix as its .rel sibling (one .tam per map
    # segment, not shared across segments like .xvm is) - e.g. map_desert03_00n.rel pairs with
    # map_desert03_00.tam, matching how ExportRel.export_all builds this same sibling path on
    # export (rel_export_menu.py: noext + ".tam", only the trailing r/n/c letter stripped).
    filename_no_ext, _filename_ext = os.path.splitext(rel_filename)
    if filename_no_ext and filename_no_ext[-1] in "rnc":
        filename_no_ext = filename_no_ext[:-1]
    guessed = os.path.join(selected_files_dir, filename_no_ext + ".tam")
    return guessed if os.path.isfile(guessed) else None


def _on_rel_path_changed(self: "ImportRel", context: Context):
    rel_path = cast(str, self.rel_path)
    if not rel_path:
        return
    dirname, basename = os.path.dirname(rel_path), os.path.basename(rel_path)
    if not cast(bool, self.xvm_path_touched):
        self.xvm_path = _guess_xvm_path(dirname, basename) or ""
    if not cast(bool, self.tam_path_touched):
        self.tam_path = _guess_tam_path(dirname, basename) or ""


def _mark_xvm_touched(self: "ImportRel", context: Context):
    self.xvm_path_touched = True


def _mark_tam_touched(self: "ImportRel", context: Context):
    self.tam_path_touched = True


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
@final
class ImportRel(ModalStepOperator, Operator):  # pyright: ignore[reportIncompatibleMethodOverride]
    "Import REL"


    bl_idname = "import_scene.rel"
    bl_label = "Import REL"
    # Deliberately excludes 'UNDO'. A real map import creates hundreds of interdependent
    # datablocks (objects, materials, shared ImgGroup node groups, images) across many separate
    # modal timer steps (see ModalStepOperator in util.py) rather than one atomic call - redoing
    # that many cross-referencing datablocks from a single undo step has been observed to corrupt
    # shared node group wiring (Image Texture nodes losing their .image reference, materials
    # rendering solid black) after Ctrl+Z followed by Redo. Not participating in undo at all means
    # an import can't be corrupted this way - to remove an import, delete its Collection manually.
    bl_options = {"REGISTER"}

    # A standalone dialog (invoke_props_dialog, opened directly from invoke() below) rather than
    # Blender's native file browser: Blender can't have two file-select browsers open at once, so
    # the rel and xvm pickers can't both live inside one native browser's sidebar - each field's
    # own browse button opens its own single-file picker instead, which doesn't have that problem.
    rel_path: StringProperty(name="REL", subtype="FILE_PATH", update=_on_rel_path_changed)
    xvm_path: StringProperty(name="XVM", subtype="FILE_PATH", update=_mark_xvm_touched)
    # Tracks whether the user has ever edited xvm_path themselves, so the auto-guess (triggered
    # when rel_path changes) only ever fills it in once - otherwise re-guessing after the user
    # deliberately clears the field (to import without a texture) would immediately refill it.
    xvm_path_touched: BoolProperty(options={"HIDDEN", "SKIP_SAVE"}, default=False)  # pyright: ignore[reportUnknownVariableType]
    # Optional - only meshes with animated ("screen"/neon-style) textures need it. Same
    # guess/touched pattern as xvm_path.
    tam_path: StringProperty(name="TAM (animated textures)", subtype="FILE_PATH", update=_mark_tam_touched)
    tam_path_touched: BoolProperty(options={"HIDDEN", "SKIP_SAVE"}, default=False)  # pyright: ignore[reportUnknownVariableType]

    def invoke(self, context: Context, event: Event):  # pyright: ignore[reportIncompatibleMethodOverride]
        return context.window_manager.invoke_props_dialog(self, width=400, title="Import REL", confirm_text="Import")

    def draw(self, context: Context):
        layout = self.layout
        layout.prop(self, "rel_path")
        layout.prop(self, "xvm_path")
        layout.prop(self, "tam_path")

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        rel_filename = cast(str, self.rel_path)
        if not rel_filename or not os.path.isfile(rel_filename):
            self.report({"ERROR"}, "REL file not found: '{}'".format(rel_filename))
            return {"CANCELLED"}
        # Recorded up front (not only on a confirmed-successful finish()) so the File > Import >
        # pso-blender > REL submenu and the viewport N-panel (see recent_rel_menu.py) offer a
        # one-click reimport of this same combination even if the import itself later fails
        # partway through - the paths themselves were still valid enough to attempt.
        recent_rel_menu.record_recent_rel(rel_filename, cast(str, self.xvm_path), cast(str, self.tam_path))
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

        tam_filename = cast(str, self.tam_path) or None
        if tam_filename and not os.path.isfile(tam_filename):
            self.report({"WARNING"}, "TAM file not found: '{}' - importing without animated textures".format(tam_filename))
            tam_filename = None

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
            steps = n_rel.read_steps(rel_filename, nrel_xvm, self._nrel_result, tam_filename)
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
