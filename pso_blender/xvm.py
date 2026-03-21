from typing import Any, Literal, cast, final
import os, pathlib, marshal, json, hashlib, warnings, time, sys
from dataclasses import dataclass, field
import bpy
import bpy.types
from .serialization import Serializable, Numeric, ResizableBuffer, FixedArray
from .util import magic_bytes, Texture, get_object_diffuse_textures
from .iff import IffHeader


# Workaround for getting multiprocessing to work
worker_path = os.path.dirname(os.path.abspath(__file__))
if worker_path not in sys.path:
    sys.path.insert(0, worker_path)
import dxt  # pyright: ignore[reportImplicitRelativeImport]


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

    def set_filename(self, filename: str):
        self._filename = filename
    
    def get_filename(self) -> str:
        return self._filename

class TextureManager:
    _base_id: int
    _textures_by_name: dict[str, Texture]
    _has_anim_tex: bool = False

    def __init__(self, objects: list[bpy.types.Object]):
        # Create "unique" texture IDs
        self._base_id = int(time.time()) & 0xffffffff
        id_counter = self._base_id
        self._textures_by_name = dict()

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


def generate_mipmaps(image: bpy.types.Image, has_alpha: bool) -> list[bpy.types.Image]:
    mip_dim, _ = image.size
    levels: list[bpy.types.Image] = []
    level_idx = 0
    alpha_test = 0.75 # Value used by the game
    pixels = cast(list[float], cast(Any, image).pixels)

    alpha_test_count = 0
    if has_alpha:
        for px_idx in range(0, len(pixels), 4):
            alpha = pixels[px_idx + 3]
            if alpha > alpha_test:
                alpha_test_count += 1
    orig_coverage = alpha_test_count / (mip_dim * mip_dim)

    while True:
        level_idx += 1
        mip_dim = mip_dim // 2
        if mip_dim <= 2:
            break
        level = image.copy()
        pixels = cast(list[float], cast(Any, level).pixels)
        level.scale(mip_dim, mip_dim)
        if has_alpha:
            alpha_threshold = orig_coverage * alpha_test * mip_dim
            for px_idx in range(0, len(pixels), 4):
                alpha = pixels[px_idx + 3]
                if alpha > alpha_threshold:
                    pixels[px_idx + 3] = 1.0
        levels.append(level)
    return levels


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


def make_xvr(tex: Texture) -> Xvr:
    img_width, img_height = tex.image.size
    flags = 0
    if tex.generate_mipmaps:
        flags |= XvrFlags.MIPMAPS
    if tex.has_alpha:
        if tex.image.alpha_mode != "STRAIGHT":
            raise Exception("XVR Error in Image '{}': Image has unsupported alpha mode '{}'".format(tex.image.filepath, tex.image.alpha_mode))
        flags |= XvrFlags.ALPHA
    xvr_format = XvrFormat.DXT1
    pixels = cast(list[float], cast(Any, tex.image).pixels)
    data = dxt.compress_image(list(pixels), img_width, img_height, tex.image.channels, tex.has_alpha)
    if tex.generate_mipmaps:
        # Concat mipmaps into data
        mipmaps = generate_mipmaps(tex.image, tex.has_alpha)
        for level in mipmaps:
            pixels = cast(list[float], cast(Any, level).pixels)
            level_width, level_height = level.size
            data += dxt.compress_image(list(pixels), level_width, level_height, level.channels, tex.has_alpha)
            # Remove temporary copies because Blender automatically saves them in the scene
            bpy.data.images.remove(level)
    return Xvr(
        body_size=len(data) + Xvr.type_size() - IffHeader.type_size(),
        id=tex.id,
        flags=flags,
        format=xvr_format,
        width=img_width,
        height=img_height,
        data_size=len(data),
        data=data)  # pyright: ignore[reportArgumentType]


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
    with open(path, "wb") as f:
        _ = f.write(buf.buffer)


def read_rgb565_texture(src_buf: bytearray) -> list[float]:
    dst_chans = 4
    dst_buf = len(src_buf) // 2 * dst_chans * [0.0]
    for i in range(0, len(src_buf), 2):
        (r, g, b) = dxt.decompose_rgb565(src_buf[i + 1] | src_buf[i + 0])
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


def read(path: str) -> Xvm:
    try:
        with open(path, "rb") as f:
            file_contents = bytearray(f.read())
    except FileNotFoundError:
        warnings.warn("XVM not found: \"{}\"".format(path))
        return Xvm()

    (xvm, xvr_offset) = Xvm.deserialize_from(file_contents)
    for _ in range(xvm.xvr_count):
        (xvr, data_offset) = Xvr.deserialize_from(file_contents, xvr_offset)
        compressed_data = file_contents[data_offset : data_offset + xvr.data_size]
        if xvr.format == XvrFormat.DXT1:
            pixels = dxt.dxt1_decompress(compressed_data, xvr.width, xvr.height)
        elif xvr.format == XvrFormat.DXT2 or xvr.format == XvrFormat.DXT3:
            pixels = dxt.dxt3_decompress(compressed_data, xvr.width, xvr.height)
        elif xvr.format == XvrFormat.DXT4 or xvr.format == XvrFormat.DXT5:
            pixels = dxt.dxt5_decompress(compressed_data, xvr.width, xvr.height)
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
    return xvm
