import os, pathlib, marshal, json, hashlib, warnings
from dataclasses import dataclass, field
import bpy
import bpy.types
from .serialization import Serializable, Numeric, ResizableBuffer, FixedArray
from . import dxt
from .util import magic_field, Texture, get_object_diffuse_textures
from .iff import IffHeader


U8 = Numeric.U8
U16 = Numeric.U16
U32 = Numeric.U32
I8 = Numeric.I8
I16 = Numeric.I16
I32 = Numeric.I32
F32 = Numeric.F32
Ptr32 = Numeric.Ptr32
NULLPTR = Numeric.NULLPTR


# Can't figure out how to get all of the frames out of an image sequence shader node, so we need to do it like this
def get_image_sequence_images(img: bpy.types.Image) -> list[bpy.types.Image]:
    dirname = os.path.dirname(img.filepath_from_user())
    frame_names = []
    for item in os.listdir(dirname):
        if os.path.isfile(os.path.join(dirname, item)):
            frame_names.append(item)
    return [
        bpy.data.images.load(os.path.join(dirname, name))
        # Sort filenames in numeric order
        for name in sorted(frame_names, key=lambda key: int(os.path.basename(os.path.splitext(key)[0])))]


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
    A8R8G8B8 = 11
    R5G6B5 = 12
    A1R5G5B5 = 13
    A4R4G4B4 = 14
    YUY2 = 15
    V8U8 = 16
    A8 = 17
    X1R5G5B5 = 18
    X8R8G8B8 = 19


class XvrFlags:
    MIPMAPS = 1
    ALPHA = 2


@dataclass
class Xvr(Serializable):
    magic: FixedArray(U8, 4) = magic_field("XVRT")
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
    magic: FixedArray(U8, 4) = magic_field("XVMH")
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

    def to_blender_materials(self, name: str) -> list[bpy.types.Material]:
        materials = []
        for i in range(len(self.xvrs)):
            xvr = self.xvrs[i]
            if len(xvr.data) < 1:
                # Dummy material
                mat = bpy.data.materials.new(name + "_dummy_" + str(i))
                mat.diffuse_color = (1.0, 0.0, 1.0, 1.0)
            else:
                # Create material that uses texture and vcol as input
                img = bpy.data.images.new(
                    name + "_xvr_" + str(i),
                    width=xvr.width, height=xvr.height)
                img.pixels = xvr.data

                mat = bpy.data.materials.new(name + "_mat_" + str(i))
                mat.use_nodes = True
                if mat.node_tree:
                    mat.node_tree.links.clear()
                    mat.node_tree.nodes.clear()

                output_node = mat.node_tree.nodes.new(type="ShaderNodeOutputMaterial")
                bsdf_node = mat.node_tree.nodes.new(type="ShaderNodeBsdfDiffuse")
                transparency_node = mat.node_tree.nodes.new(type="ShaderNodeBsdfTransparent")
                shader_mix_node = mat.node_tree.nodes.new(type="ShaderNodeMixShader")

                vcol_node = mat.node_tree.nodes.new(type="ShaderNodeVertexColor")
                vcol_node.layer_name = "vertex_color"

                mix_node = mat.node_tree.nodes.new(type="ShaderNodeMix")
                mix_node.data_type = "RGBA"
                mix_node.blend_type = "MULTIPLY"
                mix_node.inputs[0].default_value = 1.0

                tex_node = mat.node_tree.nodes.new(type="ShaderNodeTexImage")
                tex_node.image = img
                tex_node.extension = "MIRROR"

                mat.node_tree.links.new(shader_mix_node.outputs[0], output_node.inputs[0])
                mat.node_tree.links.new(mix_node.outputs[2], bsdf_node.inputs[0])
                mat.node_tree.links.new(tex_node.outputs[0], mix_node.inputs[6])
                mat.node_tree.links.new(tex_node.outputs[1], shader_mix_node.inputs[0])
                mat.node_tree.links.new(transparency_node.outputs[0], shader_mix_node.inputs[1])
                mat.node_tree.links.new(bsdf_node.outputs[0], shader_mix_node.inputs[2])
                mat.node_tree.links.new(vcol_node.outputs[0], mix_node.inputs[7])

            materials.append(mat)
        return materials

class TextureManager:
    def __init__(self, objects: list[bpy.types.Object]):
        import time
        # Create "unique" texture IDs
        self._base_id = int(time.time()) & 0xffffffff
        id_counter = self._base_id
        # Use file path as an identifier for deduplicating textures
        self._textures_by_path = dict()
        for obj in objects:
            textures = get_object_diffuse_textures(obj)
            including_animated_textures = []

            # Get animated textures
            for tex in textures:
                including_animated_textures.append(tex)
                if tex.image.source == "SEQUENCE":
                    other_frames = get_image_sequence_images(tex.image)[1:] # Skip first because we already added it
                    tex.animation_frames = len(other_frames) + 1
                    for frame in other_frames:
                        including_animated_textures.append(
                            Texture(generate_mipmaps=tex.generate_mipmaps, image=frame))

            for tex in including_animated_textures:
                w, h = tex.image.size
                # If the image file is not found on disk the texture will still exist but without pixels
                if w == 0 or h == 0 or len(tex.image.pixels) < 1:
                    raise Exception("Error in texture '{}': Texture has no pixels. Does the image file exist on disk?".format(tex.image.filepath))
                else:
                    # Deduplicate textures
                    image_abs_path = tex.image.filepath_from_user()
                    if image_abs_path not in self._textures_by_path:
                        tex.id = id_counter # Assign ID
                        self._textures_by_path[image_abs_path] = tex
                        id_counter += 1

    def get_object_textures(self, obj: bpy.types.Object) -> list[Texture]:
        texture_ids = []
        textures = get_object_diffuse_textures(obj)
        for tex in textures:
            path = tex.image.filepath_from_user()
            if path in self._textures_by_path:
                texture_ids.append(self._textures_by_path[path])
        return texture_ids
    
    def get_all_textures(self) -> list[Texture]:
        return list(self._textures_by_path.values())

    def get_base_id(self) -> int:
        return self._base_id
    
    def has_textures(self) -> bool:
        return len(self._textures_by_path) > 0
    
    def has_animated_textures(self) -> bool:
        for key in self._textures_by_path:
            if self._textures_by_path[key].image.source == "SEQUENCE":
                return True
        return False
    
    def get_object_animated_texture(self, obj: bpy.types.Object) -> Texture:
        for tex in self.get_object_textures(obj):
            if tex.image.source == "SEQUENCE":
                return tex
        return None


def generate_mipmaps(image: bpy.types.Image, has_alpha: bool) -> list[bpy.types.Image]:
    mip_dim, _ = image.size
    levels = []
    level_idx = 0
    alpha_test = 0.75 # Value used by the game

    alpha_test_count = 0
    if has_alpha:
        for px_idx in range(0, len(image.pixels), 4):
            alpha = image.pixels[px_idx + 3]
            if alpha > alpha_test:
                alpha_test_count += 1
    orig_coverage = alpha_test_count / (mip_dim * mip_dim)

    while True:
        level_idx += 1
        mip_dim = mip_dim // 2
        if mip_dim <= 2:
            break
        level = image.copy()
        level.scale(mip_dim, mip_dim)
        if has_alpha:
            alpha_threshold = orig_coverage * alpha_test * mip_dim
            for px_idx in range(0, len(level.pixels), 4):
                alpha = level.pixels[px_idx + 3]
                if alpha > alpha_threshold:
                    level.pixels[px_idx + 3] = 1.0
        levels.append(level)
    return levels


def texture_checksum(tex: Texture) -> str:
    data = list(tex.image.pixels)
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
        file_contents = f.read()
        (xvr, offset) = Xvr.deserialize_from(file_contents)
        xvr.data = file_contents[offset:]
    return xvr


def cache_xvr(path: str, xvr: Xvr):
    buf = ResizableBuffer(0)
    xvr.serialize_into(buf)
    with open(path, "wb") as f:
        print("XVM Notice: Saving texture to cache '{}'".format(path))
        f.write(buf.buffer)


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
    data = dxt.compress_image(list(tex.image.pixels), img_width, img_height, tex.image.channels, tex.has_alpha)
    if tex.generate_mipmaps:
        # Concat mipmaps into data
        mipmaps = generate_mipmaps(tex.image, tex.has_alpha)
        for level in mipmaps:
            level_width, level_height = level.size
            data += dxt.compress_image(list(level.pixels), level_width, level_height, level.channels, tex.has_alpha)
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
        data=data)


def write(path: str, textures: list[Texture]):
    # Cache xvr files in a subdirectory inside the destination directory
    cache_dir = "pso-blender-cache"
    xvr_ext = ".xvr"
    (dirname, _) = os.path.split(path)
    # Index contains checksums of files
    cache_index_path = os.path.join(dirname, cache_dir, "index.json")
    cache_index = load_cache_index(cache_index_path)
    xvrs = []
    for tex in textures:
        (_, basename) = os.path.split(tex.image.filepath)
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
    buf = ResizableBuffer(0)
    # I'll just explicitly write the lists because it's easier
    xvm = Xvm(
        body_size=Xvm.type_size() - IffHeader.type_size(),
        xvr_count=len(xvrs))
    xvm.serialize_into(buf)
    for xvr in xvrs:
        data = xvr.data
        xvr.data = []
        xvr.serialize_into(buf)
        buf.append(data)
        buf.seek_to_end()
    with open(path, "wb") as f:
        f.write(buf.buffer)


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
        xvr.data = file_contents[data_offset : data_offset + xvr.data_size]
        if xvr.format == XvrFormat.DXT1:
            xvr.data = dxt.dxt1_decompress(xvr.data, xvr.width, xvr.height)
        else:
            warnings.warn("Unsupported XVR format: {}".format(xvr.format))
            xvr.data = []
        xvm.xvrs.append(xvr)
        xvr_offset += xvr.body_size + IffHeader.type_size()

    return xvm
