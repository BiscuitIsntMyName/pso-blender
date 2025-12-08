import bpy, os, re
from bpy_extras.io_utils import ImportHelper
from bpy.types import Operator
from bpy.props import StringProperty
from . import c_rel, n_rel, xvm


class ImportRel(Operator, ImportHelper):
    "Import REL"


    bl_idname = "import_scene.rel"
    bl_label = "Import REL"

    filter_glob: StringProperty(
        default="*n.rel;*c.rel;*r.rel;*.xvm",
        options={"HIDDEN"},
        maxlen=255,
    )

    # Needed for multifile import
    files: bpy.props.CollectionProperty(
        type=bpy.types.OperatorFileListElement,
        options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH")

    def execute(self, context):
        collection = None
        rel_filename = next((f.name for f in self.files if f.name.endswith(".rel")), None)
        if not rel_filename:
            self.report({"ERROR"}, "No .rel file selected")
            return {"CANCELLED"}
        filename_no_ext, filename_ext = os.path.splitext(rel_filename)
        map_type_suffix = filename_no_ext[-1]

        # Check if xvm explicitly selected
        xvm_filename = next((f.name for f in self.files if f.name.endswith(".xvm")), None)
        if xvm_filename:
            xvm_filename = os.path.join(self.directory, xvm_filename)
        else:
            # Try to guess xvm filename based on rel filename
            match_variantless = re.match("map_[a-z]+[0-9]{2}", filename_no_ext)
            filename_variantless = match_variantless.group() if match_variantless else filename_no_ext
            xvm_filename = os.path.join(self.directory, filename_variantless + ".xvm")
            if os.path.isfile(xvm_filename):
                self.report({"INFO"}, "Guessing xvm is '{}'".format(xvm_filename))
            else:
                # xvm doesn't exist
                xvm_filename = None
                self.report({"WARNING"}, "Failed to guess xvm filename. Select xvm manually.")

        rel_filename = os.path.join(self.directory, rel_filename)

        if map_type_suffix == "c":
            collection = c_rel.read(rel_filename)
        elif map_type_suffix == "n":
            nrel = n_rel.read(rel_filename)
            nrel_xvm = xvm.read(xvm_filename) if xvm_filename else None
            collection = n_rel.to_blender(rel_filename, nrel, nrel_xvm)
        elif map_type_suffix == "r":
            self.report({"ERROR"}, "Unimplemented file type for import")
        else:
            self.report({"ERROR"}, "Could not detect REL file type based on filename. Expected a filename ending in 'n.rel', 'c.rel', or 'r.rel'.")
        
        if collection is None:
            return {"CANCELLED"}

        bpy.context.scene.collection.children.link(collection)
        return {"FINISHED"}
