from typing import Any, Literal, cast, final
import os, pathlib, marshal, json, hashlib, warnings, time
from dataclasses import dataclass, field
import bpy
import bpy.types
from mathutils import Vector, Matrix, Euler
from .serialization import Serializable, Numeric, ResizableBuffer, FixedArray
from .util import magic_bytes, Texture, get_object_diffuse_textures
from .iff import IffHeader
from .xj_material_properties_menu import MaterialWithXjSettings


from . import xvm_dxt  # pyright: ignore[reportImplicitRelativeImport]


U8 = Numeric.U8
U16 = Numeric.U16
U32 = Numeric.U32
I8 = Numeric.I8
I16 = Numeric.I16
I32 = Numeric.I32
F32 = Numeric.F32
NULLPTR = Numeric.NULLPTR


# Can't figure out how to get all of the frames out of an image sequence shader node, so we need to do it like this
def get_image_sequence_images(img: bpy.types.Image) -> list[bpy.types.Image]:
    dirname = os.path.dirname(img.filepath_from_user())
    frame_names: list[str] = []
    for item in os.listdir(dirname):
        if os.path.isfile(os.path.join(dirname, item)):
            frame_names.append(item)
    return [
        bpy.data.images.load(os.path.join(dirname, name))
        # Sort filenames in numeric order
        for name in sorted(frame_names, key=lambda key: int(os.path.basename(os.path.splitext(key)[0])))]


@final
class XvrFormat:
    A8R8G8B8 = 1
    R5G6B5 = 2
    A1R5G5B5 = 3
    A4R4G4B4 = 4
    P8 = 5
    DXT1 = 6
    DXT2 = 7
    DXT3 = 8
    DXT4 = 9
    DXT5 = 10
    # Duplicates
    #A8R8G8B8 = 11
    #R5G6B5 = 12
    #A1R5G5B5 = 13
    #A4R4G4B4 = 14
    YUY2 = 15
    V8U8 = 16
    A8 = 17
    X1R5G5B5 = 18
    X8R8G8B8 = 19


# Empirically measured (not a documented part of the format): the fixed size of the all-zero
# trailer real game .xvm files append after a texture's data when its MIPMAPS flag is set.
MIPMAP_TAIL_SIZE_BY_FORMAT = {
    XvrFormat.DXT1: 22,
    XvrFormat.DXT2: 43,
}


@final
class XvrFlags:
    MIPMAPS = 1
    ALPHA = 2


@dataclass
class Xvr(Serializable):
    magic: FixedArray[U8, Literal[4]] = field(default_factory=lambda: FixedArray(magic_bytes("XVRT")))
    body_size: U32 = 0
    flags: U32 = 0
    format: U32 = 0
    id: U32 = 0
    width: U16 = 0
    height: U16 = 0
    data_size: U32 = 0
    unk1: U32 = 0
    unk2: U32 = 0
    unk3: U32 = 0
    unk4: U32 = 0
    unk5: U32 = 0
    unk6: U32 = 0
    unk7: U32 = 0
    unk8: U32 = 0
    unk9: U32 = 0
    data: list[U8] = field(default_factory=list)

XVM_ITEM_ALIGNMENT = 64

@dataclass
class Xvm(Serializable):
    magic: FixedArray[U8, Literal[4]] = field(default_factory=lambda: FixedArray(magic_bytes("XVMH")))
    body_size: U32 = 0
    xvr_count: U32 = 0
    unk1: U32 = 0
    unk2: U32 = 0
    unk3: U32 = 0
    unk4: U32 = 0
    unk5: U32 = 0
    unk6: U32 = 0
    unk7: U32 = 0
    unk8: U32 = 0
    unk9: U32 = 0
    unk10: U32 = 0
    unk11: U32 = 0
    unk12: U32 = 0
    unk13: U32 = 0
    xvrs: list[Xvr] = field(default_factory=list)
    _filename: str = ""
    _full_path: str = ""

    def set_filename(self, filename: str):
        self._filename = filename

    def get_filename(self) -> str:
        return self._filename

    def set_full_path(self, path: str):
        self._full_path = path

    def get_full_path(self) -> str:
        return self._full_path

class TextureManager:
    _base_id: int
    _textures_by_name: dict[str, Texture]
    _has_anim_tex: bool = False

    def __init__(self, objects: list[bpy.types.Object]):
        # Create "unique" texture IDs
        self._base_id = int(time.time()) & 0xffffffff
        id_counter = self._base_id
        self._textures_by_name = dict()

        get_object_diffuse_textures.cache_clear()

        # First find all textures in given objects
        all_textures: list[Texture] = []
        for obj in objects:
            textures = get_object_diffuse_textures(obj)
            for tex in textures:
                if tex.image.source == "SEQUENCE":
                    self._has_anim_tex = True
                    # Get animated textures
                    frames = get_image_sequence_images(tex.image)
                    tex.animation_frames = len(frames)
                    all_textures.append(tex)
                    for frame in frames:
                        all_textures.append(
                            Texture(id=-1, material_name=tex.material_name, generate_mipmaps=tex.generate_mipmaps, image=frame))
                else:
                    all_textures.append(tex)
        
        # Sort textures by material name
        all_textures.sort(key=lambda x: x.material_name)

        # Try deduplicate textures
        for tex in all_textures:
            w, h = tex.image.size
            # If the image file is not found on disk the texture will still exist but without pixels
            if w == 0 or h == 0 or len(tex.image.pixels) < 1:  # pyright: ignore[reportArgumentType]
                raise Exception("Error in texture '{}': Texture has no pixels. Does the image file exist on disk?".format(tex.image.filepath))
            else:
                image_name = tex.name
                if image_name not in self._textures_by_name:
                    tex.id = id_counter # Assign ID
                    self._textures_by_name[image_name] = tex
                    id_counter += 1

    def get_object_textures(self, obj: bpy.types.Object) -> list[Texture]:
        textures: list[Texture] = []
        all_textures = get_object_diffuse_textures(obj)
        for tex in all_textures:
            if tex.name in self._textures_by_name:
                textures.append(self._textures_by_name[tex.name])
        return textures
    
    def get_all_textures(self) -> list[Texture]:
        return list(self._textures_by_name.values())

    def get_base_id(self) -> int:
        return self._base_id
    
    def has_textures(self) -> bool:
        return len(self._textures_by_name) > 0
    
    def object_has_textures(self, obj: bpy.types.Object) -> bool:
        return len(get_object_diffuse_textures(obj)) > 0
    
    def has_animated_textures(self) -> bool:
        return self._has_anim_tex
    
    def get_object_animated_texture(self, obj: bpy.types.Object) -> Texture | None:
        for tex in self.get_object_textures(obj):
            if tex.image.source == "SEQUENCE":
                return tex
        return None


def texture_checksum(tex: Texture) -> str:
    data = list(cast(Any, tex.image).pixels)
    data.append(float(tex.generate_mipmaps))
    return hashlib.md5(marshal.dumps(data)).hexdigest()


def load_cache_index(path: str) -> dict[str, str]:
    if os.path.isfile(path):
        with open(path, "r") as f:
            return json.load(f)
    return dict()


def save_cache_index(path: str, index: dict[str, str]):
    with open(path, "w") as f:
        json.dump(index, f)


def get_cached_xvr(path: str) -> Xvr:
    print("XVM Notice: Loading texture from cache '{}'".format(path))
    with open(path, "rb") as f:
        file_contents = bytearray(f.read())
        (xvr, offset) = Xvr.deserialize_from(file_contents)
        xvr.data = file_contents[offset:]  # pyright: ignore[reportAttributeAccessIssue]
    return xvr


def cache_xvr(path: str, xvr: Xvr):
    buf = ResizableBuffer(size=0)
    _ = xvr.serialize_into(buf)
    with open(path, "wb") as f:
        print("XVM Notice: Saving texture to cache '{}'".format(path))
        _ = f.write(buf.buffer)


MIP_MIN_DIM = 4


def downsample_pixels_2x(pixels: list[float], width: int, height: int, channels: int) -> tuple[list[float], int, int]:
    """2x2 box filter, halving both dimensions. Operates on plain pixel lists rather than
    Blender Image datablocks - Image.copy() + Image.scale() was tried first but produced blank
    (all-black) results, at least in a --background context."""
    new_width, new_height = width // 2, height // 2
    result = [0.0] * (new_width * new_height * channels)
    for y in range(new_height):
        src_y = y * 2
        for x in range(new_width):
            src_x = x * 2
            dst_i = (y * new_width + x) * channels
            for c in range(channels):
                total = 0.0
                for dy in range(2):
                    row_i = (src_y + dy) * width
                    for dx in range(2):
                        total += pixels[(row_i + src_x + dx) * channels + c]
                result[dst_i + c] = total / 4.0
    return result, new_width, new_height


def generate_mip_levels(pixels: list[float], width: int, height: int, channels: int) -> list[tuple[list[float], int, int]]:
    """Box-filtered mip pyramid, stopping once a level reaches 4x4 (the smallest a DXT block can
    encode)."""
    levels: list[tuple[list[float], int, int]] = []
    cur_pixels, w, h = pixels, width, height
    while w > MIP_MIN_DIM and h > MIP_MIN_DIM:
        cur_pixels, w, h = downsample_pixels_2x(cur_pixels, w, h, channels)
        levels.append((cur_pixels, w, h))
    return levels


_MAPPING_IDENTITY_LOCATION = (0.0, 0.0, 0.0)
_MAPPING_IDENTITY_ROTATION = (0.0, 0.0, 0.0)
_MAPPING_IDENTITY_SCALE = (1.0, 1.0, 1.0)


def get_material_mapping_transform(mat: bpy.types.Material) -> Matrix | None:
    """The transform of a material's Mapping node (Location/Rotation/Scale), or None if there's
    no Mapping node or it's still at its default identity values (nothing to bake).

    The actual PSO file format has no concept of a UV transform - the game just samples the
    texture with the mesh's raw UV. So a Mapping node someone dials in by hand in Blender (to
    preview a different tiling/offset/rotation) only affects the Blender viewport unless its
    effect is baked into the exported texture's pixels themselves.
    """
    if mat.node_tree is None:
        return None
    mapping_node = next((n for n in mat.node_tree.nodes if n.type == "MAPPING"), None)
    if mapping_node is None:
        return None
    location = cast(Any, mapping_node.inputs["Location"]).default_value
    rotation = cast(Any, mapping_node.inputs["Rotation"]).default_value
    scale = cast(Any, mapping_node.inputs["Scale"]).default_value
    if (tuple(location) == _MAPPING_IDENTITY_LOCATION
            and tuple(rotation) == _MAPPING_IDENTITY_ROTATION
            and tuple(scale) == _MAPPING_IDENTITY_SCALE):
        return None
    # Matches the "Point" mapping type's own semantics (the default, and the only type this
    # importer ever creates): scale first, then rotate, then translate.
    return Matrix.LocRotScale(Vector(location), Euler(rotation, "XYZ"), Vector(scale))


def _fold_uv_coordinate(s: float, addr_mode: str) -> float:
    """Folds an arbitrary UV coordinate into [0, 1) the same way the live node graph's per-axis
    Wrap/Ping-Pong/Clamp math node would (see make_texture_addressing_node in xj.py)."""
    if addr_mode in ("D3DTADDRESS_MIRROR", "D3DTADDRESS_MIRRORONCE"):
        t = s % 2.0
        return t if t < 1.0 else 2.0 - t
    elif addr_mode in ("D3DTADDRESS_CLAMP", "D3DTADDRESS_BORDER"):
        return min(1.0, max(0.0, s))
    else:
        return s % 1.0


def bake_material_mapping(mat: bpy.types.Material, pixels: list[float], width: int, height: int, channels: int) -> list[float]:
    """Resamples pixels (nearest-neighbor) so sampling the result with the mesh's raw UV
    reproduces what the material's Mapping node currently shows in Blender. No-op (returns
    pixels unchanged) if the material has no Mapping node or it's at its default identity."""
    transform = get_material_mapping_transform(mat)
    if transform is None:
        return pixels
    settings = cast(MaterialWithXjSettings, mat).xj_settings
    addr_u = settings.tex_addr_u
    addr_v = settings.tex_addr_v
    result = [0.0] * (width * height * channels)
    for y in range(height):
        v = (y + 0.5) / height
        for x in range(width):
            u = (x + 0.5) / width
            sample = transform @ Vector((u, v, 0.0))
            su = _fold_uv_coordinate(sample.x, addr_u)
            sv = _fold_uv_coordinate(sample.y, addr_v)
            src_x = min(width - 1, int(su * width))
            src_y = min(height - 1, int(sv * height))
            src_i = (src_y * width + src_x) * channels
            dst_i = (y * width + x) * channels
            for c in range(channels):
                result[dst_i + c] = pixels[src_i + c]
    return result


def make_xvr(tex: Texture) -> Xvr:
    img_width, img_height = tex.image.size
    flags = 0
    if tex.generate_mipmaps:
        flags |= XvrFlags.MIPMAPS
    is_premultiplied = False
    if tex.has_alpha:
        if tex.image.alpha_mode == "PREMUL":
            is_premultiplied = True
        elif tex.image.alpha_mode != "STRAIGHT":
            raise Exception("XVR Error in Image '{}': Image has unsupported alpha mode '{}'".format(tex.image.filepath, tex.image.alpha_mode))
        flags |= XvrFlags.ALPHA
    # DXT1's alpha is 1-bit (transparent or opaque) via a punch-through color-ordering trick.
    # A PREMUL-alpha source (imported from DXT2/DXT4) has smooth alpha and pixels already
    # multiplied by alpha, matching DXT2's explicit 4-bit-per-pixel alpha block - use that
    # instead of collapsing it down to DXT1's binary alpha.
    xvr_format = XvrFormat.DXT2 if is_premultiplied else XvrFormat.DXT1

    def compress(px: list[float], w: int, h: int, channels: int) -> bytearray:
        if is_premultiplied:
            return xvm_dxt.compress_image_dxt3(px, w, h, channels)
        return xvm_dxt.compress_image(px, w, h, channels, tex.has_alpha)

    pixels = cast(list[float], cast(Any, tex.image).pixels)
    mat = bpy.data.materials.get(tex.material_name)
    if mat is not None:
        pixels = bake_material_mapping(mat, list(pixels), img_width, img_height, tex.image.channels)
    data = compress(list(pixels), img_width, img_height, tex.image.channels)
    if tex.generate_mipmaps:
        # Real game .xvm files store an actual compressed mip pyramid immediately after the base
        # level's data (verified by decoding the real bytes: a standard halve-until-4x4 chain
        # decodes correctly with no gap between levels). A fixed 2-byte pad follows the last
        # (4x4) level - confirmed by byte-counting real files, content doesn't appear to matter.
        # This whole region lives inside data_size, unlike the separate fixed all-zero trailer
        # below (which sits outside data_size, only inside body_size).
        for (level_pixels, level_width, level_height) in generate_mip_levels(list(pixels), img_width, img_height, tex.image.channels):
            data += compress(level_pixels, level_width, level_height, tex.image.channels)
        data += bytes(2)
    data_size = len(data)
    if tex.generate_mipmaps:
        # Fixed all-zero trailer after the base level + mip chain (verified against ~1100 real
        # textures across 40 of Ephinea's own map .xvm files: always exactly 22 bytes for DXT1,
        # 43 bytes for DXT2, regardless of the texture's dimensions or mip chain length).
        tail_size = MIPMAP_TAIL_SIZE_BY_FORMAT.get(xvr_format, 22)
        data += bytes(tail_size)
    return Xvr(
        body_size=len(data) + Xvr.type_size() - IffHeader.type_size(),
        id=tex.id,
        flags=flags,
        format=xvr_format,
        width=img_width,
        height=img_height,
        data_size=data_size,
        data=data)  # pyright: ignore[reportArgumentType]


def write_xvrs(path: str, xvrs: list[Xvr]):
    """Serializes already-built Xvr chunks (e.g. from make_xvr(), or passed through unchanged
    from a source file) into a complete .xvm file."""
    buf = ResizableBuffer(size=0)
    # I'll just explicitly write the lists because it's easier
    xvm = Xvm(
        body_size=Xvm.type_size() - IffHeader.type_size(),
        xvr_count=len(xvrs))
    _ = xvm.serialize_into(buf)
    for xvr in xvrs:
        data = xvr.data
        xvr.data = []
        _ = xvr.serialize_into(buf)
        _ = buf.append(bytearray(data))
        buf.seek_to_end()
        xvr.data = data  # pyright: ignore[reportAttributeAccessIssue]
    with open(path, "wb") as f:
        _ = f.write(buf.buffer)


def write(path: str, textures: list[Texture]):
    # Cache xvr files in a subdirectory inside the destination directory
    cache_dir = "pso-blender-cache"
    xvr_ext = ".xvr"
    (dirname, _) = os.path.split(path)
    # Index contains checksums of files
    cache_index_path = os.path.join(dirname, cache_dir, "index.json")
    cache_index = load_cache_index(cache_index_path)
    xvrs: list[Xvr] = []
    for tex in textures:
        (_, basename) = os.path.split(tex.name)
        cache_dir_path = os.path.join(dirname, cache_dir)
        pathlib.Path(cache_dir_path).mkdir(exist_ok=True) # Create dir if not exist
        xvr_basename = basename + xvr_ext
        cached_xvr_path = os.path.join(cache_dir_path, xvr_basename)
        # Try to load cached textures from destination directory if pixels have not changed
        checksum = texture_checksum(tex)
        if os.path.isfile(cached_xvr_path) and checksum == cache_index.get(xvr_basename):
            xvr = get_cached_xvr(cached_xvr_path)
            xvr.id = tex.id # Use new texture id
        else:
            xvr = make_xvr(tex)
            cache_xvr(cached_xvr_path, xvr)
        cache_index[xvr_basename] = checksum
        save_cache_index(cache_index_path, cache_index)
        xvrs.append(xvr)
    write_xvrs(path, xvrs)


def read_rgb565_texture(src_buf: bytearray) -> list[float]:
    dst_chans = 4
    dst_buf = len(src_buf) // 2 * dst_chans * [0.0]
    for i in range(0, len(src_buf), 2):
        (r, g, b) = xvm_dxt.decompose_rgb565(src_buf[i + 1] | src_buf[i + 0])
        dst_i = i // 2 * dst_chans
        dst_buf[dst_i + 0] = r / 0xff
        dst_buf[dst_i + 1] = g / 0xff
        dst_buf[dst_i + 2] = b / 0xff
        dst_buf[dst_i + 3] = 1.0
    return dst_buf


def read_argb1555_texture(src_buf: bytearray) -> list[float]:
    dst_chans = 4
    dst_buf = len(src_buf) // 2 * dst_chans * [0.0]
    for i in range(0, len(src_buf), 2):
        rgb = src_buf[i + 1] | src_buf[i + 0]
        a = rgb & 0b1
        b = (rgb >> 5) & 0b11111
        g = (rgb >> 10) & 0b11111
        r = (rgb >> 15) & 0b11111
        r = (r << 3) | (r >> 2)
        g = (g << 3) | (g >> 2)
        b = (b << 3) | (b >> 2)
        dst_i = i // 2 * dst_chans
        dst_buf[dst_i + 0] = r / 0xff
        dst_buf[dst_i + 1] = g / 0xff
        dst_buf[dst_i + 2] = b / 0xff
        dst_buf[dst_i + 3] = a
    return dst_buf


# Keyed by absolute path -> (mtime, size, decoded Xvm). Importing a multi-chunk map (n.rel for
# several chunks) re-reads the same shared .xvm file once per chunk; decoding every texture in it
# each time is wasted work. Invalidated by mtime/size rather than cached forever, since texture
# pack work means these files get regenerated and re-imported within the same Blender session.
_read_cache: dict[str, tuple[float, int, "Xvm"]] = {}


def read(path: str) -> Xvm:
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        warnings.warn("XVM not found: \"{}\"".format(path))
        return Xvm()

    abs_path = os.path.abspath(path)
    cached = _read_cache.get(abs_path)
    if cached is not None and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return cached[2]

    with open(path, "rb") as f:
        file_contents = bytearray(f.read())

    (xvm, xvr_offset) = Xvm.deserialize_from(file_contents)
    for _ in range(xvm.xvr_count):
        (xvr, data_offset) = Xvr.deserialize_from(file_contents, xvr_offset)
        compressed_data = file_contents[data_offset : data_offset + xvr.data_size]
        if xvr.format == XvrFormat.DXT1:
            pixels = xvm_dxt.dxt1_decompress(compressed_data, xvr.width, xvr.height)
        elif xvr.format == XvrFormat.DXT2 or xvr.format == XvrFormat.DXT3:
            pixels = xvm_dxt.dxt3_decompress(compressed_data, xvr.width, xvr.height)
        elif xvr.format == XvrFormat.DXT4 or xvr.format == XvrFormat.DXT5:
            pixels = xvm_dxt.dxt5_decompress(compressed_data, xvr.width, xvr.height)
        elif xvr.format == XvrFormat.R5G6B5:
            pixels = read_rgb565_texture(compressed_data)
        elif xvr.format == XvrFormat.A1R5G5B5:
            pixels = read_argb1555_texture(compressed_data)
        else:
            warnings.warn("Unsupported XVR format: {}".format(xvr.format))
            pixels = bytearray()
        xvr.data = pixels  # pyright: ignore[reportAttributeAccessIssue]
        xvm.xvrs.append(xvr)
        xvr_offset += xvr.body_size + IffHeader.type_size()

    xvm.set_filename(os.path.basename(path))
    xvm.set_full_path(abs_path)
    _read_cache[abs_path] = (stat.st_mtime, stat.st_size, xvm)
    return xvm


def read_raw(path: str) -> Xvm:
    """Like read(), but Xvr.data is left as the exact original compressed bytes (the full
    per-texture payload: base level + mip chain + trailer, if present) instead of being decoded
    to pixels. Used to carry a texture through a standalone XVM export completely unchanged -
    there's no need to decode+recompress a texture nothing touched, and doing so would lose
    fidelity (or fail entirely) for formats this addon can't re-encode, like the raw R5G6B5/
    A1R5G5B5 textures real map .xvm files sometimes contain alongside the DXT-compressed ones."""
    with open(path, "rb") as f:
        file_contents = bytearray(f.read())

    (xvm, xvr_offset) = Xvm.deserialize_from(file_contents)
    header_remainder_size = Xvr.type_size() - IffHeader.type_size()
    for _ in range(xvm.xvr_count):
        (xvr, data_offset) = Xvr.deserialize_from(file_contents, xvr_offset)
        payload_size = xvr.body_size - header_remainder_size
        xvr.data = file_contents[data_offset : data_offset + payload_size]  # pyright: ignore[reportAttributeAccessIssue]
        xvm.xvrs.append(xvr)
        xvr_offset += xvr.body_size + IffHeader.type_size()

    xvm.set_filename(os.path.basename(path))
    return xvm
