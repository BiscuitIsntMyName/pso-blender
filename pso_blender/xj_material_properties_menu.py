from enum import Enum
from typing import Callable, cast, final
import os
from warnings import warn
import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty  # pyright: ignore[reportUnknownVariableType]
from bpy.types import Context
from . import util


class BlendMode(Enum):
    # Not the actual d3d enum values, but indices into an array containing the enum values
    D3DBLEND_ZERO = 0
    D3DBLEND_ONE = 1
    D3DBLEND_SRCCOLOR = 2
    D3DBLEND_INVSRCCOLOR = 3
    D3DBLEND_SRCALPHA = 4
    D3DBLEND_INVSRCALPHA = 5
    D3DBLEND_DESTALPHA = 6
    D3DBLEND_INVDESTALPHA = 7
    D3DBLEND_DESTCOLOR = 8
    D3DBLEND_INVDESTCOLOR = 9


BlendMode_items = [
    ("D3DBLEND_ZERO", "D3DBLEND_ZERO", "", 0),
    ("D3DBLEND_ONE", "D3DBLEND_ONE", "", 1),
    ("D3DBLEND_SRCCOLOR", "D3DBLEND_SRCCOLOR", "", 2),
    ("D3DBLEND_INVSRCCOLOR", "D3DBLEND_INVSRCCOLOR", "", 3),
    ("D3DBLEND_SRCALPHA", "D3DBLEND_SRCALPHA", "", 4),
    ("D3DBLEND_INVSRCALPHA", "D3DBLEND_INVSRCALPHA", "", 5),
    ("D3DBLEND_DESTALPHA", "D3DBLEND_DESTALPHA", "", 6),
    ("D3DBLEND_INVDESTALPHA", "D3DBLEND_INVDESTALPHA", "", 7),
    ("D3DBLEND_DESTCOLOR", "D3DBLEND_DESTCOLOR", "", 8),
    ("D3DBLEND_INVDESTCOLOR", "D3DBLEND_INVDESTCOLOR", "", 9),
]


class TextureAddressingMode(Enum):
    D3DTADDRESS_WRAP = 3
    D3DTADDRESS_MIRROR = 4
    D3DTADDRESS_CLAMP = 5
    D3DTADDRESS_BORDER = 6
    D3DTADDRESS_MIRRORONCE = 7


TextureAddressingMode_items = [
    ("D3DTADDRESS_WRAP", "D3DTADDRESS_WRAP", "", 0),
    ("D3DTADDRESS_MIRROR", "D3DTADDRESS_MIRROR", "", 1),
    ("D3DTADDRESS_CLAMP", "D3DTADDRESS_CLAMP", "", 2),
    ("D3DTADDRESS_BORDER", "D3DTADDRESS_BORDER", "", 3),
    ("D3DTADDRESS_MIRRORONCE", "D3DTADDRESS_MIRRORONCE", "", 4),
]


class MaterialColorSource(Enum):
    D3DMCS_MATERIAL = 0
    D3DMCS_COLOR1 = 1
    D3DMCS_COLOR2 = 3


MaterialColorSource_items = [
    ("D3DMCS_MATERIAL", "D3DMCS_MATERIAL", "", 0),
    ("D3DMCS_COLOR1", "D3DMCS_COLOR1", "", 1),
    ("D3DMCS_COLOR2", "D3DMCS_COLOR2", "", 2),
]


class NormalType(Enum):
    Vertex = 1
    Face = 2


NormalType_items = [
    ("Vertex", "Vertex", "", 0),
    ("Face", "Face", "", 1)
]


class AlphaCompression(Enum):
    AUTO = 0
    FORCE_DXT1 = 1
    FORCE_DXT3 = 2
    FORCE_DXT2 = 3


AlphaCompression_items = [
    ("AUTO", "Auto", "Use DXT3 (smooth alpha) only if the texture's alpha actually has gradients; DXT1 (binary alpha, lighter) otherwise", 0),
    ("FORCE_DXT1", "Force DXT1", "Always compress with 1-bit punch-through alpha, even if the texture has a smooth alpha gradient", 1),
    ("FORCE_DXT2", "Force DXT2 (premultiplied)", "Always compress with explicit 16-level alpha and premultiply RGB by alpha - matches the format some original PSO glow/effect textures use, intended for use with a premultiplied-alpha blend mode (src=ONE, dst=INVSRCALPHA)", 3),
    ("FORCE_DXT3", "Force DXT3", "Always compress with explicit 16-level alpha, even if the texture's alpha is just a hard cutout mask", 2),
]


def _get_alpha_compression(self: bpy.types.PropertyGroup) -> int:
    group_tree = util.find_material_img_group_tree(cast(bpy.types.Material, self.id_data))
    if group_tree is None:
        return AlphaCompression.AUTO.value
    return int(group_tree.get("alpha_compression", AlphaCompression.AUTO.value))


def _set_alpha_compression(self: bpy.types.PropertyGroup, value: int):
    group_tree = util.find_material_img_group_tree(cast(bpy.types.Material, self.id_data))
    if group_tree is not None:
        group_tree["alpha_compression"] = value


def _get_generate_mipmaps(self: bpy.types.PropertyGroup) -> bool:
    group_tree = util.find_material_img_group_tree(cast(bpy.types.Material, self.id_data))
    if group_tree is None:
        return False
    return bool(group_tree.get("generate_mipmaps", False))


def _set_generate_mipmaps(self: bpy.types.PropertyGroup, value: bool):
    group_tree = util.find_material_img_group_tree(cast(bpy.types.Material, self.id_data))
    if group_tree is not None:
        group_tree["generate_mipmaps"] = value


def _get_force_uncompressed(self: bpy.types.PropertyGroup) -> bool:
    group_tree = util.find_material_img_group_tree(cast(bpy.types.Material, self.id_data))
    if group_tree is None:
        return False
    return bool(group_tree.get("force_uncompressed", False))


def _set_force_uncompressed(self: bpy.types.PropertyGroup, value: bool):
    group_tree = util.find_material_img_group_tree(cast(bpy.types.Material, self.id_data))
    if group_tree is not None:
        group_tree["force_uncompressed"] = value


def _get_animation_frame_delay(self: bpy.types.PropertyGroup) -> int:
    # Real animated .tam data (map_desert03, map_acity) always uses one uniform delay across
    # every frame of a given animation - never a different value per frame - so a single shared
    # value is enough to represent every real case seen so far, rather than a per-frame list.
    image = util.find_diffuse_image(cast(bpy.types.Material, self.id_data))
    if image is None or image.source != "SEQUENCE":
        return 1
    delays = image.get("pso_tam_frame_delays")
    return int(delays[0]) if delays else 1


def _set_animation_frame_delay(self: bpy.types.PropertyGroup, value: int):
    # pso_tam_frame_delays (see get_or_build_animated_texture_image in xj.py) is what tam.write()
    # (tam.py) reads back on export - overwriting it here, uniformly across every existing frame,
    # is the entire mechanism: no other code needs to change for an edited speed to reach the
    # exported .tam and, from there, the game.
    image = util.find_diffuse_image(cast(bpy.types.Material, self.id_data))
    if image is not None and image.source == "SEQUENCE":
        frame_count = len(image.get("pso_tam_frame_delays") or [1])
        image["pso_tam_frame_delays"] = [value] * frame_count


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
class XjMaterialSettings(bpy.types.PropertyGroup):
    # Shared across every material variant of this texture (stored on the texture's ImgGroup node
    # tree, see get_or_create_texture_node_group in xj.py / util.find_material_img_group_tree) -
    # whether the exported texture includes a mip chain is a property of that one shared physical
    # texture, not of any particular mesh placement, so there's nothing to keep in sync per
    # material variant here; toggling it on any variant is immediately visible on every other one.
    generate_mipmaps: BoolProperty(
        name="Generate Mipmaps",
        description="Generate mipmaps for this texture. Can make exporting very slow.",
        get=_get_generate_mipmaps,
        set=_set_generate_mipmaps)
    # Shared across every material variant of this texture, same reasoning as generate_mipmaps
    # above - which DXT format a texture's alpha needs is a property of the physical texture
    # itself, not of any particular mesh placement.
    alpha_compression: EnumProperty(
        name="Alpha Compression",
        items=AlphaCompression_items,
        get=_get_alpha_compression,
        set=_set_alpha_compression)
    # Shared across every material variant of this texture, same reasoning as generate_mipmaps.
    # Manual escape hatch for content DXT1/2/3's shared 4-colors-per-4x4-block RGB limit visibly
    # degrades (confirmed live: a saturated icon, high-frequency noise/foam patterns) - checked
    # before any DXT format decision in make_xvr() (xvm.py), so alpha_compression above becomes
    # moot when this is on. Not automatic on purpose (matches this addon's established
    # auto-detect-plus-manual-override pattern elsewhere) - deliberately per-texture, not a global
    # switch, since uncompressed is 4-8x the size of DXT1 for the same texture.
    force_uncompressed: BoolProperty(
        name="Force Uncompressed",
        description="Store this texture as raw A8R8G8B8 instead of DXT-compressing it - guarantees "
                     "exact color, at roughly 4-8x the size of DXT1/DXT3 for the same texture. Matches "
                     "what the original game itself does for a handful of its own textures.",
        get=_get_force_uncompressed,
        set=_set_force_uncompressed)
    # Shared across every material variant of this texture, same reasoning as generate_mipmaps -
    # only meaningful (and only shown, see XjMaterialSettingsPanel.draw) when the texture is an
    # imported animated sequence (image.source == "SEQUENCE").
    animation_frame_delay: IntProperty(
        name="Animation Frame Delay",
        description="Game ticks each frame is shown before advancing to the next - higher is slower. "
                     "Applies uniformly to every frame of this animation.",
        min=1,
        default=1,
        get=_get_animation_frame_delay,
        set=_set_animation_frame_delay)
    src_blend: EnumProperty(
        name="Source",
        default=str(BlendMode.D3DBLEND_SRCALPHA.name),
        items=BlendMode_items)
    dst_blend: EnumProperty(
        name="Destination",
        default=str(BlendMode.D3DBLEND_INVSRCALPHA.name),
        items=BlendMode_items)
    tex_addr_u: EnumProperty(
        name="U",
        default=str(TextureAddressingMode.D3DTADDRESS_MIRROR.name),
        items=TextureAddressingMode_items)
    tex_addr_v: EnumProperty(
        name="V",
        default=str(TextureAddressingMode.D3DTADDRESS_MIRROR.name),
        items=TextureAddressingMode_items)
    material1: IntProperty(name="Unknown 1", default=0)
    material2: IntProperty(name="Unknown 2", default=0)
    lighting: BoolProperty(name="Affected by lighting", default=True)
    camera_space_normals: BoolProperty(name="Camera space normals", default=False)
    normal_type: EnumProperty(
        name="Normal type",
        default=str(NormalType.Vertex.name),
        items=NormalType_items)
    diffuse_color_source: EnumProperty(
        name="Diffuse color source",
        default=str(MaterialColorSource.D3DMCS_COLOR1.name),
        items=MaterialColorSource_items)
    # The Xvr.id this material's texture had in the .xvm it was imported from. -1 means unknown
    # (material wasn't created by this addon's importer) - real PSO ids are never 0 or negative,
    # so -1 is a safe "not set" sentinel. A standalone XVM export needs this to write textures
    # back under the same id the existing, untouched .rel/.xj files expect them under.
    pso_id: IntProperty(name="PSO Texture ID", default=-1)
    # Full path to the .xvm this material's texture was imported from. Lets a standalone XVM
    # export find the original file on its own (to carry through textures the user hasn't
    # touched) without asking the user to re-locate it - Blender can't open a file browser
    # inside another file browser anyway, so there's no good way to ask for it interactively at
    # export time.
    source_xvm_path: StringProperty(name="Source XVM Path", subtype="FILE_PATH", default="")


class MaterialWithXjSettings(bpy.types.Material):
    xj_settings: XjMaterialSettings


def _resolve_target_imggroup(context: Context) -> tuple[bpy.types.ShaderNodeTree, bpy.types.ShaderNodeTexImage, bpy.types.Material] | str:
    """The shared ImgGroup tree and its diffuse Image Texture node for the PSO texture currently
    active on the selected object, or an error message string if none could be resolved. Shared by
    every "Send to ImgGroup" operator regardless of where the source image comes from (a Shader
    Editor node, an Asset Browser asset...) - only the source differs between them.
    """
    obj = context.active_object
    target_mat = obj.active_material if obj is not None else None
    if target_mat is None or target_mat.node_tree is None:
        return "Select the object (and material slot) that should receive this texture first"

    settings = cast(MaterialWithXjSettings, target_mat).xj_settings
    if settings.pso_id < 0:
        return (
            "'{}' isn't a PSO texture created by this addon's import - select the object/face "
            "whose material is the texture you want to replace.").format(target_mat.name)

    group_node_tree = util.find_material_img_group_tree(target_mat)
    if group_node_tree is None:
        return "'{}' has no shared texture group to send this image into".format(target_mat.name)

    tex_image_node = group_node_tree.nodes.get("PSO_Diffuse") or next(
        (n for n in group_node_tree.nodes if n.type == "TEX_IMAGE"), None)
    if tex_image_node is None:
        return "'{}' texture group has no Image Texture node".format(target_mat.name)

    return (group_node_tree, cast(bpy.types.ShaderNodeTexImage, tex_image_node), target_mat)


@final
class XjSendImageToImgGroup(bpy.types.Operator):
    "Send this Image Texture node's image into the shared ImgGroup of the PSO texture currently active on the selected object - lets a texture from any external source (e.g. an asset browser addon) be adopted as a texture-pack replacement without rebuilding a material's node graph by hand"

    bl_idname = "node.xj_send_to_imggroup"
    bl_label = "Send to ImgGroup (PSO)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context):
        node = context.active_node
        if node is None or node.type != "TEX_IMAGE":
            return False
        return cast(bpy.types.ShaderNodeTexImage, node).image is not None

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        source_node = cast(bpy.types.ShaderNodeTexImage, context.active_node)
        source_image = source_node.image
        if source_image is None:
            self.report({"ERROR"}, "This node has no image")
            return {"CANCELLED"}

        resolved = _resolve_target_imggroup(context)
        if isinstance(resolved, str):
            self.report({"ERROR"}, resolved)
            return {"CANCELLED"}
        group_tree, tex_image_node, target_mat = resolved

        tex_image_node.image = source_image
        # Avoid circular import - xj.py imports from this module at load time.
        from . import xj
        xj._wire_relief_composite(group_tree, tex_image_node, None, None)  # pyright: ignore[reportPrivateUsage]
        settings = cast(MaterialWithXjSettings, target_mat).xj_settings
        self.report({"INFO"}, "Sent '{}' to PSO texture id {} ('{}')".format(source_image.name, settings.pso_id, target_mat.name))
        return {"FINISHED"}


def draw_send_to_imggroup_menu_item(self: bpy.types.Menu, context: Context):
    node = context.active_node
    if node is not None and node.type == "TEX_IMAGE" and self.layout is not None:
        self.layout.separator()
        _ = self.layout.operator(XjSendImageToImgGroup.bl_idname, icon="EXPORT")


def _resolve_asset_datablock(asset: bpy.types.AssetRepresentation) -> "bpy.types.ID | None":
    """The real Blender datablock behind an Asset Browser entry - directly if already local to this
    file, or appended from its source library otherwise (the normal case for an external library
    like Poly Haven's, where the library file already contains the actual content). Shared by every
    "Send (Asset) to ImgGroup" operator instead of each re-deriving it separately - returns the raw
    datablock with no further interpretation (could be an Asset Bridge dummy, a real Image, a real
    Material - the caller decides what to do with it, see _try_force_download_asset_bridge_dummy for
    the Asset Bridge dummy case specifically)."""
    datablock = asset.local_id
    if datablock is not None:
        return datablock
    if not asset.full_library_path:
        return None
    with bpy.data.libraries.load(asset.full_library_path, link=False) as (data_from, data_to):
        if asset.id_type == "IMAGE" and asset.name in data_from.images:
            data_to.images = [asset.name]
        elif asset.id_type == "MATERIAL" and asset.name in data_from.materials:
            data_to.materials = [asset.name]
    if asset.id_type == "IMAGE":
        return data_to.images[0] if data_to.images else None
    elif asset.id_type == "MATERIAL":
        return data_to.materials[0] if data_to.materials else None
    return None


_AMBIENTCG_ROLE_SUFFIXES = {
    "diffuse": "Color",
    "normal": "NormalGL",
    "metal": "Metalness",
    "roughness": "Roughness",
    "displacement": "Displacement",
}


def _load_image_for_role(file_path: object, role: str) -> "bpy.types.Image | None":
    """Loads an image and sets its colorspace explicitly rather than trusting Blender's own
    filename-heuristic auto-detection - only "diffuse" is a real color image (sRGB, Blender's
    normal default); normal/metal/roughness/displacement are all data maps that must be Non-Color,
    or _wire_relief_composite's darkening-factor math reads gamma-corrected values it shouldn't,
    visibly discoloring the result (confirmed live 2026-09-01 - a foil material came out muddy
    brown instead of neutral aluminum with this unset). Matches the explicit is_data handling
    xj.py's own new_image() already does for images built from a REL/XJ import - this is the same
    thing for images loaded here from a local file instead."""
    try:
        img = bpy.data.images.load(str(file_path), check_existing=True)
    except RuntimeError:
        return None
    img.colorspace_settings.is_data = role != "diffuse"
    return img


def _find_ambientcg_style_images(download_dir: object, file_name: str) -> "dict[str, bpy.types.Image]":
    """ambientCG's own download bundle names every texture map `<file_name>_<Role>.<ext>` (e.g.
    "Foil001_1K-JPG_Color.jpg", "Foil001_1K-JPG_NormalGL.jpg") - `file_name` is the same
    `<name>_<quality>` stem ambientCG uses for its own .tres/.blend files (e.g. "Foil001_1K-JPG").
    Confirmed live 2026-09-02 against a real download folder that this suffix alone fully
    disambiguates every role - ambientCG also ships a `.tres` Godot resource file with the same
    mapping spelled out explicitly, but it turned out to be entirely redundant with this, so this
    replaces the earlier .tres-parsing approach (simpler, one fewer file format to depend on). Same
    matching principle as _find_polyhaven_style_images just below, only the suffix position differs
    (ambientCG: role after the quality; Poly Haven: role before it). Returns {our role name: loaded
    Image}, missing roles simply absent - never raises, returns {} if the folder doesn't exist."""
    from pathlib import Path
    dir_path = cast(Path, download_dir)
    if not dir_path.is_dir():
        return {}
    files_by_stem = {f.stem: f for f in dir_path.iterdir() if f.is_file()}
    images: dict[str, bpy.types.Image] = {}
    for our_key, suffix in _AMBIENTCG_ROLE_SUFFIXES.items():
        file_path = files_by_stem.get("{}_{}".format(file_name, suffix))
        if file_path is not None:
            img = _load_image_for_role(file_path, our_key)
            if img is not None:
                images[our_key] = img
    return images


def _find_asset_bridge_images(mat: bpy.types.Material) -> "dict[str, bpy.types.Image]":
    """Best-effort fallback for when there's no ambientCG .tres to read (e.g. a Poly Haven asset
    imported through Asset Bridge) - walk the already-built material's node graph the normal way."""
    images: dict[str, bpy.types.Image] = {}
    diffuse = util.find_material_base_color_image(mat)
    if diffuse is not None:
        images["diffuse"] = diffuse
    normal, metal = util.find_material_normal_and_metal_images(mat)
    if normal is not None:
        images["normal"] = normal
    if metal is not None:
        images["metal"] = metal
    roughness = util.find_material_roughness_image(mat)
    if roughness is not None:
        images["roughness"] = roughness
    displacement = util.find_material_displacement_image(mat)
    if displacement is not None:
        images["displacement"] = displacement
    return images


# Same association table Asset Bridge's own Poly Haven support uses (apis/polyhaven/ph_asset.py,
# PH_Asset.import_asset) - kept identical rather than reinvented, since it's just the real, known
# Poly Haven filename convention (<name>_<ph_name>_<quality>.<ext>). No "metal" entry - Poly Haven
# materials don't ship a separate metalness map this way either, matching that same source.
_POLYHAVEN_FILENAME_ROLES = {
    "diffuse": {"diff", "col_1", "coll1", "col", "col_01"},
    "displacement": {"disp"},
    "normal": {"nor_gl"},
    "roughness": {"rough"},
}


def _find_polyhaven_style_images(download_dir: object, name: str, quality_level: str) -> "dict[str, bpy.types.Image]":
    """Fallback for Poly Haven assets pulled in through Asset Bridge - these ship as plain texture
    files with no shared "<name>_<quality>" stem alongside them (confirmed live 2026-09-01: a real
    Poly Haven download folder only has the bare image files), so _find_ambientcg_style_images'
    suffix-after-quality convention doesn't apply. Matches files directly by Poly Haven's own
    well-known naming convention instead (role token before the quality), exactly as Asset Bridge's
    own Poly Haven code does it."""
    from pathlib import Path
    dir_path = cast(Path, download_dir)
    if not dir_path.is_dir():
        return {}
    files_by_stem = {f.stem: f for f in dir_path.iterdir() if f.is_file()}
    images: dict[str, bpy.types.Image] = {}
    for our_key, ph_names in _POLYHAVEN_FILENAME_ROLES.items():
        for ph_name in ph_names:
            file_path = files_by_stem.get("{}_{}_{}".format(name, ph_name, quality_level))
            if file_path is not None:
                img = _load_image_for_role(file_path, our_key)
                if img is not None:
                    images[our_key] = img
                break
    return images


def _try_force_download_asset_bridge_dummy(
        asset: bpy.types.AssetRepresentation,
        on_ready: "Callable[[dict[str, bpy.types.Image]], None]",
        candidate_mat: "bpy.types.Material | None" = None) -> bool:
    """If `asset` is an Asset Bridge dummy, trigger Asset Bridge's own download+import pipeline for
    it directly - the same one a manual drag-and-drop into the scene would use - instead of
    requiring the user to do that drag themselves first just so a later click can find something.
    Once the download completes, resolves the real texture images (preferring a direct filename-
    convention scan of the download folder - see _find_ambientcg_style_images/
    _find_polyhaven_style_images - over Asset Bridge's own built material, since that material can
    be stale/incomplete from an earlier interrupted import attempt still lingering in the file) and
    calls `on_ready({role: Image})`. This can fire well after this function itself has returned -
    downloads happen in the background.

    `candidate_mat` lets a caller that has already resolved a material (possibly freshly appended
    from the asset's library this same call, e.g. _load_asset_material) pass it in directly rather
    than this function re-deriving it from `asset.local_id` independently, which isn't guaranteed
    to reflect a just-appended datablock the same way. Falls back to `asset.local_id` if omitted.

    Returns True if a download was actually kicked off - the caller should report "downloading..."
    and finish, not its usual "not found" error, since this either lands real images via `on_ready`
    shortly, or fails and reports its own error through Asset Bridge's own messaging.
    Returns False if Asset Bridge isn't installed, this isn't one of its dummies, or it couldn't be
    resolved for some other reason - the caller should fall through to its normal error message.
    """
    datablock = candidate_mat if candidate_mat is not None else asset.local_id
    if not isinstance(datablock, bpy.types.Material):
        return False
    ab = getattr(datablock, "asset_bridge", None)
    if ab is None or not getattr(ab, "is_dummy", False):
        return False
    idname = getattr(ab, "idname", "")
    if not idname:
        return False

    try:
        from asset_bridge.api import get_asset_lists  # pyright: ignore[reportMissingImports]
        from asset_bridge.helpers.assets import download_and_import_asset  # pyright: ignore[reportMissingImports]
        from asset_bridge.settings import get_ab_settings as get_asset_bridge_settings  # pyright: ignore[reportMissingImports]
    except ImportError:
        return False

    asset_list_item = get_asset_lists().all_assets.get(idname)
    if asset_list_item is None:
        return False

    context = bpy.context
    quality = get_asset_bridge_settings(context).asset_quality
    ab_asset = asset_list_item.to_asset(quality, "APPEND")
    # file_name only exists on some Asset subclasses (e.g. ambientCG's, the "<name>_<quality>" file
    # stem) - a Poly Haven asset pulled in through Asset Bridge has no such shared stem (confirmed
    # live 2026-09-01, a real download folder only has the bare texture files), so there's nothing
    # to look for that way, and it falls through to the Poly Haven suffix convention below instead.
    file_name = getattr(ab_asset, "file_name", None)
    download_dir = ab_asset.download_dir

    def on_completion(imported: object):
        images = _find_ambientcg_style_images(download_dir, file_name) if file_name else {}
        if not images:
            images = _find_polyhaven_style_images(download_dir, ab_asset.name, quality)
        if not images and isinstance(imported, bpy.types.Material):
            images = _find_asset_bridge_images(imported)
        # Always call on_ready, even with an empty dict - each caller's on_ready already reports a
        # clear "no usable image found in '<asset name>'" error via warn() when the role it needs is
        # missing. Used to only call on_ready if images was non-empty, so a total failure (none of
        # the 3 lookup methods found anything) silently produced no error at all - confirmed 2026-09-02.
        on_ready(images)

    download_and_import_asset(context, ab_asset, draw=True, on_completion=on_completion)
    return True


def _resolve_asset_image(asset: bpy.types.AssetRepresentation) -> bpy.types.Image | None:
    """The usable Image behind an Asset Browser asset - directly if it's an Image asset, or its
    Base Color texture if it's a Material asset (see util.find_material_base_color_image)."""
    datablock = _resolve_asset_datablock(asset)
    if isinstance(datablock, bpy.types.Image):
        return datablock
    if isinstance(datablock, bpy.types.Material):
        return util.find_material_base_color_image(datablock)
    return None


@final
class XjSendAssetToImgGroup(bpy.types.Operator):
    "Send this Asset Browser asset's image into the shared ImgGroup of the PSO texture currently active on the selected object - works directly from the Asset Browser, no need to drop the asset into the scene first"

    bl_idname = "asset.xj_send_to_imggroup"
    bl_label = "Send to ImgGroup (PSO)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context):
        asset = context.asset
        return asset is not None and asset.id_type in {"IMAGE", "MATERIAL"}

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        asset = context.asset
        if asset is None:
            self.report({"ERROR"}, "No asset selected")
            return {"CANCELLED"}

        resolved = _resolve_target_imggroup(context)
        if isinstance(resolved, str):
            self.report({"ERROR"}, resolved)
            return {"CANCELLED"}
        group_tree, tex_image_node, target_mat = resolved

        asset_name = asset.name

        def on_ready(images: "dict[str, bpy.types.Image]"):
            image = images.get("diffuse")
            if image is None:
                warn("Asset Bridge downloaded '{}' but no usable base color image was found in it.".format(asset_name))
                return
            tex_image_node.image = image
            from . import xj
            xj._wire_relief_composite(group_tree, tex_image_node, None, None)  # pyright: ignore[reportPrivateUsage]

        # Resolved once here (local, or freshly appended from the library) so both the dummy check
        # below and the normal fallback path see the exact same datablock, instead of each
        # re-resolving independently - a freshly-appended-this-call datablock isn't guaranteed to
        # show up via asset.local_id again on a second, separate resolve.
        datablock = _resolve_asset_datablock(asset)

        # Always force a fresh download+resolve for an Asset Bridge dummy, even if a "real" material
        # matching it already exists somewhere in the file - confirmed live (2026-09-01) that reusing
        # an already-existing one risks reusing a stale/incomplete result left behind by an earlier
        # interrupted import (missing texture layers a fresh resolve correctly finds).
        candidate_mat = datablock if isinstance(datablock, bpy.types.Material) else None
        if _try_force_download_asset_bridge_dummy(asset, on_ready, candidate_mat=candidate_mat):
            self.report({"INFO"}, "Downloading '{}' via Asset Bridge...".format(asset_name))
            return {"FINISHED"}

        source_image: bpy.types.Image | None = None
        if isinstance(datablock, bpy.types.Image):
            source_image = datablock
        elif isinstance(datablock, bpy.types.Material):
            source_image = util.find_material_base_color_image(datablock)
        if source_image is None:
            self.report({"ERROR"}, "Could not find a usable image in '{}'".format(asset.name))
            return {"CANCELLED"}

        tex_image_node.image = source_image
        from . import xj
        xj._wire_relief_composite(group_tree, tex_image_node, None, None)  # pyright: ignore[reportPrivateUsage]
        settings = cast(MaterialWithXjSettings, target_mat).xj_settings
        self.report({"INFO"}, "Sent '{}' to PSO texture id {} ('{}')".format(source_image.name, settings.pso_id, target_mat.name))
        return {"FINISHED"}


def draw_send_asset_to_imggroup_menu_item(self: bpy.types.Menu, context: Context):
    asset = context.asset
    if asset is not None and asset.id_type in {"IMAGE", "MATERIAL"} and self.layout is not None:
        self.layout.separator()
        _ = self.layout.operator(XjSendAssetToImgGroup.bl_idname, icon="EXPORT")


def _load_asset_material(asset: bpy.types.AssetRepresentation) -> bpy.types.Material | None:
    """The Material datablock behind an Asset Browser asset - like _resolve_asset_image, but returns
    the Material itself (to also look for Normal/Metallic/Roughness/Displacement), not just its
    Base Color image."""
    if asset.id_type != "MATERIAL":
        return None
    datablock = _resolve_asset_datablock(asset)
    return datablock if isinstance(datablock, bpy.types.Material) else None


@final
class XjSendAssetPackToImgGroup(bpy.types.Operator):
    "Send this Asset Browser Material asset's diffuse, normal, metal, and roughness maps into the shared ImgGroup of the PSO texture currently active on the selected object, wiring a live relief composite (visible immediately in the viewport, reproduced exactly at export by baking this same node graph) - use Send to ImgGroup (PSO) instead for sending just a single image with nothing composited"

    bl_idname = "asset.xj_send_asset_pack_to_imggroup"
    bl_label = "Send Asset to ImgGroup (PSO)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context):
        asset = context.asset
        return asset is not None and asset.id_type == "MATERIAL"

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        asset = context.asset
        if asset is None:
            self.report({"ERROR"}, "No asset selected")
            return {"CANCELLED"}

        resolved = _resolve_target_imggroup(context)
        if isinstance(resolved, str):
            self.report({"ERROR"}, resolved)
            return {"CANCELLED"}
        group_tree, tex_image_node, target_mat = resolved

        asset_name = asset.name

        def on_ready(images: "dict[str, bpy.types.Image]"):
            image = images.get("diffuse")
            if image is None:
                warn("Asset Bridge downloaded '{}' but no usable base color image was found in it.".format(asset_name))
                return
            tex_image_node.image = image
            from . import xj
            xj._wire_relief_composite(  # pyright: ignore[reportPrivateUsage]
                group_tree, tex_image_node,
                images.get("normal"), images.get("metal"), images.get("roughness"), images.get("displacement"))

        # Resolved once here (local, or freshly appended from the library) so both the dummy check
        # below and the normal fallback path see the exact same datablock, instead of each
        # re-resolving independently - a freshly-appended-this-call datablock isn't guaranteed to
        # show up via asset.local_id again on a second, separate resolve.
        source_mat = _load_asset_material(asset)

        # Always force a fresh download+resolve for an Asset Bridge dummy, even if a "real" material
        # matching it already exists somewhere in the file - confirmed live (2026-09-01) that reusing
        # an already-existing one risks reusing a stale/incomplete result left behind by an earlier
        # interrupted import (missing texture layers a fresh resolve correctly finds).
        if _try_force_download_asset_bridge_dummy(asset, on_ready, candidate_mat=source_mat):
            self.report({"INFO"}, "Downloading '{}' via Asset Bridge...".format(asset_name))
            return {"FINISHED"}

        diffuse_image = util.find_material_base_color_image(source_mat) if source_mat is not None else None
        if diffuse_image is None:
            if source_mat is None:
                self.report({"ERROR"}, "Could not load a material from '{}'".format(asset.name))
            else:
                self.report({"ERROR"}, "Could not find a base color image in '{}'".format(asset.name))
            return {"CANCELLED"}

        normal_image, metal_image = util.find_material_normal_and_metal_images(source_mat)
        roughness_image = util.find_material_roughness_image(source_mat)
        displacement_image = util.find_material_displacement_image(source_mat)

        tex_image_node.image = diffuse_image
        from . import xj
        xj._wire_relief_composite(  # pyright: ignore[reportPrivateUsage]
            group_tree, tex_image_node, normal_image, metal_image, roughness_image, displacement_image)

        settings = cast(MaterialWithXjSettings, target_mat).xj_settings
        if normal_image is None and metal_image is None and displacement_image is None:
            self.report({"WARNING"}, "'{}' has no normal, metal, or displacement map - sent the diffuse image as-is".format(asset.name))
        elif displacement_image is not None:
            self.report({"INFO"}, "Sent '{}' to PSO texture id {} ('{}'), with relief composited live and a real displacement map available".format(asset.name, settings.pso_id, target_mat.name))
        else:
            self.report({"INFO"}, "Sent '{}' to PSO texture id {} ('{}'), with relief composited live".format(asset.name, settings.pso_id, target_mat.name))
        return {"FINISHED"}


def draw_send_asset_pack_to_imggroup_menu_item(self: bpy.types.Menu, context: Context):
    asset = context.asset
    if asset is not None and asset.id_type == "MATERIAL" and self.layout is not None:
        _ = self.layout.operator(XjSendAssetPackToImgGroup.bl_idname, icon="EXPORT")


@final
class XjSendFileToImgGroup(bpy.types.Operator):
    "Send this file browser selection into the shared ImgGroup of the PSO texture currently active on the selected object - works directly on a texture file on disk, no need to load it as an asset first"

    bl_idname = "file.xj_send_to_imggroup"
    bl_label = "Send to ImgGroup (PSO)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context):
        return bool(context.selected_files)

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        selected_files = context.selected_files
        space = context.space_data
        if not selected_files or space is None or space.type != "FILE_BROWSER":
            self.report({"ERROR"}, "No file selected")
            return {"CANCELLED"}

        directory = cast(bpy.types.SpaceFileBrowser, space).params.directory
        if isinstance(directory, bytes):
            directory = directory.decode("utf-8")
        filepath = os.path.join(directory, selected_files[0].name)
        if not os.path.isfile(filepath):
            self.report({"ERROR"}, "'{}' is not a file".format(filepath))
            return {"CANCELLED"}

        try:
            source_image = bpy.data.images.load(filepath, check_existing=True)
        except RuntimeError as e:
            self.report({"ERROR"}, "Could not load '{}' as an image: {}".format(filepath, e))
            return {"CANCELLED"}

        resolved = _resolve_target_imggroup(context)
        if isinstance(resolved, str):
            self.report({"ERROR"}, resolved)
            return {"CANCELLED"}
        group_tree, tex_image_node, target_mat = resolved

        tex_image_node.image = source_image
        from . import xj
        xj._wire_relief_composite(group_tree, tex_image_node, None, None)  # pyright: ignore[reportPrivateUsage]
        settings = cast(MaterialWithXjSettings, target_mat).xj_settings
        self.report({"INFO"}, "Sent '{}' to PSO texture id {} ('{}')".format(source_image.name, settings.pso_id, target_mat.name))
        return {"FINISHED"}


def draw_send_file_to_imggroup_menu_item(self: bpy.types.Menu, context: Context):
    if context.selected_files and self.layout is not None:
        self.layout.separator()
        _ = self.layout.operator(XjSendFileToImgGroup.bl_idname, icon="EXPORT")


@final
class XjMaterialSettingsPanel(bpy.types.Panel):
    bl_label = "XJ Settings"
    bl_idname = "MATERIAL_PT_XjMaterialSettingsPanel"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"

    @classmethod
    def poll(cls, context: Context):
        return context.material is not None
    
    def draw(self, context: Context):
        if self.layout is not None and context.material is not None:
            self.layout.use_property_split = True
            self.layout.use_property_decorate = False
            settings = cast(MaterialWithXjSettings, context.material).xj_settings
            self.layout.prop(settings, "generate_mipmaps")
            self.layout.prop(settings, "alpha_compression")
            self.layout.prop(settings, "force_uncompressed")
            diffuse_image = util.find_diffuse_image(context.material)
            if diffuse_image is not None and diffuse_image.source == "SEQUENCE":
                self.layout.prop(settings, "animation_frame_delay")
            self.layout.prop(settings, "lighting")
            # Alpha blending
            blend_box = self.layout.box()
            blend_box.label(text="Alpha blending mode")
            blend_input_col = blend_box.column(align=True)
            blend_input_col.prop(settings, "src_blend")
            blend_input_col.prop(settings, "dst_blend")
            # Texture addressing
            tex_addr_box = self.layout.box()
            tex_addr_box.label(text="Texture addressing mode")
            tex_addr_input_col = tex_addr_box.column(align=True)
            tex_addr_input_col.prop(settings, "tex_addr_u")
            tex_addr_input_col.prop(settings, "tex_addr_v")
            # Other
            self.layout.prop(settings, "camera_space_normals")
            self.layout.prop(settings, "normal_type")
            self.layout.prop(settings, "diffuse_color_source")
            self.layout.prop(settings, "material1")
            self.layout.prop(settings, "material2")
            self.layout.prop(settings, "pso_id")
            self.layout.prop(settings, "source_xvm_path")
