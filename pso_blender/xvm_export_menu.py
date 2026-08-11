from typing import cast, final
import bpy, os
from bpy_extras.io_utils import ExportHelper
from bpy.types import Context, Operator
from bpy.props import StringProperty  # pyright: ignore[reportUnknownVariableType]
from . import xvm, util
from .util import ModalStepOperator
from .xj_material_properties_menu import MaterialWithXjSettings


def get_original_pso_id_and_source(material_name: str) -> tuple[int, str] | None:
    """Recovers the exact Xvr.id a material's texture had, and the full path of the .xvm it was
    originally imported from (xj.py's importer stores both on the material as
    xj_settings.pso_id / xj_settings.source_xvm_path). Needed because a standalone XVM export is
    meant to replace an existing game .xvm while leaving the .xj/.rel mesh files untouched -
    those still reference the *original* PSO ids and slot positions, so the new .xvm must be
    built by carrying that original file's untouched textures through unchanged and only
    substituting the replaced ones.
    """
    mat = bpy.data.materials.get(material_name)
    if mat is None:
        return None
    settings = cast(MaterialWithXjSettings, mat).xj_settings
    if settings.pso_id < 0 or not settings.source_xvm_path:
        return None
    return (settings.pso_id, settings.source_xvm_path)


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
@final
class ExportXvm(ModalStepOperator, Operator, ExportHelper):  # pyright: ignore[reportIncompatibleMethodOverride]
    "Export XVM"


    bl_idname = "export_scene.xvm"
    bl_label = "Export XVM"

    # ExportHelper mixin class uses this
    filename_ext = ".xvm"

    filter_glob: StringProperty(
        default="*.xvm",
        options={"HIDDEN"},
        maxlen=255,  # Max internal buffer length, longer would be clamped.
    )

    filepath: StringProperty(subtype="FILE_PATH")

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        filepath = cast(str, self.filepath)
        # Valid objects are top-level objects that either have a mesh or are empty - same
        # selection as ExportXj, since a standalone .xvm should contain the same textures an
        # XJ export of the same objects would have produced as its companion file.
        view_layer = bpy.context.view_layer
        if not view_layer:
            return {"CANCELLED"}
        root_objs = [obj for obj in view_layer.objects if obj.parent is None and (obj.type == "MESH" or obj.type == "EMPTY")]
        all_objs = root_objs.copy()
        for obj in root_objs:
            all_objs += obj.children_recursive

        # Collect textures per *material*, not deduplicated by image - TextureManager collapses
        # every material that currently points at the same image into a single entry, which is
        # exactly wrong here: replacing several different original textures with the same new
        # image (a very normal thing to do) would then only carry through ONE of them, silently
        # dropping the rest back to their original, unreplaced content.
        util.get_object_diffuse_textures.cache_clear()
        seen_material_names: set[str] = set()
        by_pso_id: dict[int, util.Texture] = {}
        conflicting_pso_ids: dict[int, set[str]] = {}
        unresolved: list[str] = []
        source_paths: set[str] = set()
        for obj in all_objs:
            for tex in util.get_object_diffuse_textures(obj):
                if tex.material_name in seen_material_names:
                    continue
                seen_material_names.add(tex.material_name)
                resolved = get_original_pso_id_and_source(tex.material_name)
                if resolved is None:
                    unresolved.append("{} (image: {})".format(tex.material_name, tex.image.name))
                    continue
                original_id, source_path = resolved
                tex.id = original_id
                source_paths.add(source_path)
                existing = by_pso_id.get(original_id)
                if existing is not None and existing.image.name != tex.image.name:
                    conflicting_pso_ids.setdefault(original_id, {existing.image.name}).add(tex.image.name)
                by_pso_id[original_id] = tex

        if not by_pso_id and not unresolved:
            self.report({"WARNING"}, "No textures found on selected objects")
            return {"CANCELLED"}
        if unresolved:
            self.report({"ERROR"}, (
                "Could not determine the original PSO texture ID for: {}. These materials "
                "weren't created by this addon's XJ/REL import (or were renamed), so a "
                "standalone XVM export can't know which slot the existing .xj/.rel files expect "
                "them in.").format(", ".join(unresolved)))
            return {"CANCELLED"}
        if conflicting_pso_ids:
            details = "; ".join(
                "PSO id {} has both {}".format(pso_id, " and ".join(sorted(names)))
                for pso_id, names in conflicting_pso_ids.items())
            self.report({"ERROR"}, (
                "Some material variants of the same original texture now point at different "
                "images, so it's ambiguous what to export for that texture slot: {}. Make sure "
                "every material sharing a texture (see \"Select Objects Using This Texture\") "
                "was updated to the same replacement image.").format(details))
            return {"CANCELLED"}
        if len(source_paths) > 1:
            self.report({"ERROR"}, (
                "The materials being exported came from more than one source .xvm ({}) - can't "
                "build a single consistent export from them.").format(", ".join(sorted(source_paths))))
            return {"CANCELLED"}
        base_xvm_path = next(iter(source_paths))
        if not os.path.isfile(base_xvm_path):
            self.report({"ERROR"}, (
                "The original .xvm this map was imported from is no longer at its recorded "
                "location ('{}'). A standalone XVM export needs it to preserve the texture slots "
                "of anything you haven't replaced.").format(base_xvm_path))
            return {"CANCELLED"}
        base_xvm = xvm.read_raw(base_xvm_path)

        # Walk every slot of the base file, in its original order: substitute in a replaced
        # texture where the scene has one, otherwise carry the original chunk through byte for
        # byte. This keeps every slot position exactly as the untouched .xj/.rel files expect,
        # even for textures this particular scene doesn't reference at all (other mesh files
        # sharing this same .xvm might). Done one xvr at a time via a modal timer (see
        # ModalStepOperator in util.py) rather than one big blocking loop, so a real progress
        # indicator can actually be shown for what's usually the slowest part of an export
        # (DXT compression, optionally with a full mip chain per texture).
        self._filepath = filepath
        self._base_xvrs = base_xvm.xvrs
        self._by_pso_id = by_pso_id
        self._output_xvrs = []
        return self.start_modal_steps(context, self._build_output_xvrs(), len(base_xvm.xvrs))

    def _build_output_xvrs(self):
        for base_xvr in self._base_xvrs:
            if base_xvr.id in self._by_pso_id:
                self._output_xvrs.append(xvm.make_xvr(self._by_pso_id[base_xvr.id]))
            else:
                self._output_xvrs.append(base_xvr)
            yield

    def finish(self, context: Context):
        xvm.write_xvrs(self._filepath, self._output_xvrs)
