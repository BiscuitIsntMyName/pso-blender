from typing import cast, final
import bpy, os, time
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
        # pso_id -> {image name -> Texture}: a plain "pso_id -> Texture" dict would silently drop
        # every variant but the last-seen one whenever two DIFFERENT animations legitimately share
        # one base texture slot's pso_id (confirmed on real map data, e.g. Ephinea's map_acity00 -
        # animation_id 2 and 61 both declare tex_id 221/pso_id 1303213 as their starting texture,
        # each with its own distinct frame content) - keyed by image name so every distinct variant
        # survives instead of being treated as an unresolvable conflict.
        by_pso_id: dict[int, dict[str, util.Texture]] = {}
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
                by_pso_id.setdefault(original_id, {})[tex.image.name] = tex

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
        # sharing this same .xvm might). Any extra variant sharing a slot's pso_id (see by_pso_id
        # above) gets appended as a brand-new entry afterward instead of being dropped - mirrors
        # how the full REL/XVM export (TextureManager) already represents these. Done one xvr at a
        # time via a modal timer (see ModalStepOperator in util.py) rather than one big blocking
        # loop, so a real progress indicator can actually be shown for what's usually the slowest
        # part of an export (DXT compression, optionally with a full mip chain per texture).
        extra_count = sum(len(variants) - 1 for variants in by_pso_id.values())
        self._filepath = filepath
        self._base_xvrs = base_xvm.xvrs
        self._by_pso_id = by_pso_id
        self._output_xvrs = []
        return self.start_modal_steps(context, self._build_output_xvrs(), len(base_xvm.xvrs) + extra_count)

    def _build_output_xvrs(self):
        # Same cache the full-rebuild REL/XVM export path uses (xvm.write() / get_or_make_xvr()) -
        # a standalone re-export of an unchanged replacement texture reuses its cached encode
        # instead of always re-running DXT compression, and both export paths can never again
        # silently diverge in what encoding they serve for the same texture.
        cache_dir_path = xvm.xvr_cache_root(os.path.basename(self._filepath))
        consumed: set[tuple[int, str]] = set()
        for i, base_xvr in enumerate(self._base_xvrs):
            variants = self._by_pso_id.get(base_xvr.id)
            if not variants:
                self._output_xvrs.append(base_xvr)
                yield
                continue
            # Prefer the variant whose image was originally imported from exactly this array
            # position (pso_orig_tex_id, stashed at import time - see make_material/get_or_build_
            # animated_texture_image in xj.py) - that's the one that actually belongs at this
            # slot. Any other variant sharing this pso_id doesn't have a position of its own in
            # the original file (e.g. a second animation that legitimately starts from the same
            # base texture) and gets appended separately below instead of overwriting this slot.
            primary = next((tex for tex in variants.values() if tex.image.get("pso_orig_tex_id") == i), None)
            if primary is None:
                primary = next(iter(variants.values()))
            self._output_xvrs.append(xvm.get_or_make_xvr(primary, cache_dir_path))
            consumed.add((base_xvr.id, primary.image.name))
            yield
        extra_id_counter = int(time.time()) & 0xffffffff
        for pso_id, variants in self._by_pso_id.items():
            for image_name, tex in variants.items():
                if (pso_id, image_name) in consumed:
                    continue
                tex.id = extra_id_counter
                extra_id_counter += 1
                self._output_xvrs.append(xvm.get_or_make_xvr(tex, cache_dir_path))
                yield

    def finish(self, context: Context):
        xvm.write_xvrs(self._filepath, self._output_xvrs)
