import os
from typing import cast, final
from warnings import catch_warnings
import bpy
from bpy_extras.io_utils import ExportHelper
from bpy.props import StringProperty  # pyright: ignore[reportUnknownVariableType]
from bpy.types import Context, Operator, Object

from . import r_rel, n_rel, c_rel
from .util import variantless_map_basename


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
@final
class ExportRel(Operator, ExportHelper):  # pyright: ignore[reportIncompatibleMethodOverride]
    """Export render geometry (n.rel), collision geometry (c.rel), and minimap geometry (r.rel)"""


    bl_idname = "export_scene.rel"
    bl_label = "Export REL(s)"

    # ExportHelper mixin class uses this
    filename_ext = ".rel"

    filter_glob: StringProperty(
        default="*.rel",
        options={"HIDDEN"},
        maxlen=255,  # Max internal buffer length, longer would be clamped.
    )

    filepath: StringProperty(subtype="FILE_PATH")

    def cancel_with_error(self, ex: Exception):
        self.report({"ERROR"}, str(ex))
        return {"CANCELLED"}
    
    def cancel_with_warning(self, msg: str):
        self.report({"WARNING"}, msg)
        return {"CANCELLED"}
    
    def export_all(self, minimap_objs: list[Object], render_objs: list[Object], collision_objs: list[Object], chunk_markers: list[Object], animated_render_objs: list[Object]):
        filepath = cast(str, self.filepath)
        noext, ext = os.path.splitext(filepath)
        # The file browser naturally leads users to pick one of the three existing sibling files
        # to overwrite (e.g. map_foo01n.rel), not a "variantless" base path - strip a trailing
        # r/n/c REL-variant letter if present, so all three siblings are built from the same base
        # instead of doubling up the suffix (map_foo01n.rel -> map_foo01nn.rel, a new file instead
        # of overwriting the one the user selected).
        if noext and noext[-1] in "rnc":
            noext = noext[:-1]
        if minimap_objs and len(minimap_objs) > 0:
            r_rel.write(noext + "r" + ext, minimap_objs)
        if (render_objs and len(render_objs) > 0) or (animated_render_objs and len(animated_render_objs) > 0):
            # A multi-segment map (e.g. map_acity00_00n.rel) shares a single .xvm across every
            # segment, named WITHOUT the "_00" segment suffix (map_acity00.xvm, not
            # map_acity00_00.xvm) - unlike the .rel/.tam siblings, which do carry it. Using noext
            # directly here would write the .xvm under the wrong filename: a brand new file the
            # game never loads, silently leaving the stale original .xvm (with mismatched texture
            # ids) as what actually pairs with the freshly exported .rel in-game.
            (basedir, base_noext) = os.path.split(noext)
            xvm_noext = os.path.join(basedir, variantless_map_basename(base_noext))
            n_rel.write(noext + "n" + ext, xvm_noext + ".xvm", noext + ".tam", render_objs, chunk_markers, animated_render_objs)
        if collision_objs and len(collision_objs):
            c_rel.write(noext + "c" + ext, collision_objs)
        return {"FINISHED"}
    
    def export_all_by_tags(self):
        # Avoid circular dependency
        from .rel_properties_menu import ObjectWithRelSettings

        render_objs: list[Object] = []
        collision_objs: list[Object] = []
        minimap_objs: list[Object] = []
        chunk_markers: list[Object] = []
        animated_render_objs: list[Object] = []
        view_layer = bpy.context.view_layer
        if view_layer:
            for obj in view_layer.objects:
                obj = cast(ObjectWithRelSettings, obj)
                if obj.rel_settings.is_nrel:
                    # is_animated_nrel is a sub-classifier of is_nrel, not an independent flag - an
                    # object checks BOTH (import sets them together, see n_rel.py), so the N.REL
                    # header checkbox stays visually honest about "is this object part of N.REL at
                    # all" while still routing to a different write path under the hood. Routing an
                    # animated object into render_objs too would sweep it into the ordinary
                    # proximity-based chunk-regrouping path - the exact bug already hit once this
                    # session - so it's one or the other, never both lists.
                    if obj.rel_settings.is_animated_nrel:
                        animated_render_objs.append(obj)
                    else:
                        render_objs.append(obj)
                if obj.rel_settings.is_crel:
                    collision_objs.append(obj)
                if obj.rel_settings.is_rrel:
                    minimap_objs.append(obj)
                if obj.rel_settings.is_chunk:
                    chunk_markers.append(obj)
        else:
            return {"CANCELLED"}
        return self.export_all(minimap_objs, render_objs, collision_objs, chunk_markers, animated_render_objs)

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        with catch_warnings(record=True) as warnings:
            try:
                result = self.export_all_by_tags()
            finally:
                # Display warnings in the GUI
                for warning in warnings:
                    self.report({"WARNING"}, str(warning.message))
                    print(warning)
            return result
