import bpy, os, re
from bpy_extras.io_utils import ImportHelper
from bpy.types import Operator
from bpy.props import StringProperty
from . import c_rel, n_rel, xvm


class ImportRel(Operator, ImportHelper):
    bl_idname = "import_scene.rel"
    bl_label = "Import REL"

    filter_glob: StringProperty(
        default="*n.rel;*c.rel;*r.rel",
        options={"HIDDEN"},
        maxlen=255,
    )

    filepath: StringProperty(subtype="FILE_PATH")

    def execute(self, context):
        collection = None
        filename = os.path.basename(self.filepath)
        dirname = os.path.dirname(self.filepath)
        filename_no_ext, filename_ext = os.path.splitext(filename)
        map_type_suffix = filename_no_ext[-1]

        match_variantless = re.match("map_[a-z]+[0-9]{2}", filename_no_ext)
        filename_variantless = match_variantless.group() if match_variantless else filename_no_ext

        if map_type_suffix == "c":
            collection = c_rel.read(self.filepath)
        elif map_type_suffix == "n":
            nrel = n_rel.read(self.filepath)
            nrel_xvm = xvm.read(os.path.join(dirname, filename_variantless + ".xvm"))
            collection = n_rel.to_blender(filename, nrel, nrel_xvm)
        elif map_type_suffix == "r":
            self.report({"ERROR"}, "Unimplemented file type for import")
        else:
            self.report({"ERROR"}, "Could not detect REL file type based on filename. Expected a filename ending in 'n.rel', 'c.rel', or 'r.rel'.")
        
        if collection is None:
            return {"CANCELLED"}

        bpy.context.scene.collection.children.link(collection)
        return {"FINISHED"}
