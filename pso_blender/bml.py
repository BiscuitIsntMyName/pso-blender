from dataclasses import dataclass, field
from typing import Literal, final
import bpy
from warnings import warn

from bpy.types import Collection
from .serialization import Serializable, Numeric, FixedArray, ResizableBuffer
from . import prs, xvm, xj, util, njm
from .nj import nj_to_blender_mesh
from .iff import IffHeader, parse_pof0


U8 = Numeric.U8
U16 = Numeric.U16
U32 = Numeric.U32
I8 = Numeric.I8
I16 = Numeric.I16
I32 = Numeric.I32
F32 = Numeric.F32
NULLPTR = Numeric.NULLPTR


@final
class CompressionType:
    NONE = 0
    PRS = ord("P")


@dataclass
class BmlHeader(Serializable):
    unk1: U32 = 0
    file_count: U32 = 0
    compression_type: U8 = 0
    has_textures: U8 = 0
    unk2: U16 = 0
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
    unk14: U32 = 0
    unk15: U32 = 0


@dataclass
class FileDescription(Serializable):
    name: FixedArray[U8, Literal[32]] = field(default_factory=FixedArray)
    compressed_size: U32 = 0
    unk1: U32 = 0
    decompressed_size: U32 = 0
    textures_compressed_size: U32 = 0
    textures_decompressed_size: U32 = 0
    unk2: U32 = 0
    unk3: U32 = 0
    unk4: U32 = 0


@dataclass
class BmlItem:
    name: str
    models: list[bytearray] = field(default_factory=list)
    texture_list: bytearray | None = None
    texture_archive: bytearray | None = None
    animation: bytearray | None = None
    pointer_table: list[tuple[int, int]] = field(default_factory=list)


def parse_bml(path: str) -> list[BmlItem]:
    with open(path, "rb") as f:
        bml = bytearray(f.read())

    items: list[BmlItem] = []
    (bml_header, _) = BmlHeader.deserialize_from(bml)
    file_description_offset = BmlHeader.type_size()
    file_alignment = 0x20 if bml_header.has_textures else 0x800
    file_offset = util.align_up(bml_header.file_count * 0x40, 0x800)
    chunk_header_size = IffHeader.type_size()

    for _ in range(bml_header.file_count):
        # Read file description
        (file_description, file_description_offset) = FileDescription.deserialize_from(bml, offset=file_description_offset)
        item = BmlItem(name=util.bytes_to_string(file_description.name))
        items.append(item)

        # Get file from offset, decompress if needed
        file_data = bml[file_offset:file_offset + file_description.compressed_size]
        if bml_header.compression_type == CompressionType.PRS:
            file_data = prs.decompress(file_data)
        
        # Read iff chunks
        chunk_offset = 0
        prev_chunk_offset = None
        prev_chunk_size = None
        while chunk_offset < file_description.decompressed_size:
            (chunk_header, _) = IffHeader.deserialize_from(file_data, offset=chunk_offset)
            chunk_type = util.bytes_to_string(chunk_header.type_name)
            chunk_body = file_data[chunk_offset + chunk_header_size:chunk_offset + chunk_header_size + chunk_header.body_size]

            if chunk_type == "NJCM":
                item.models.append(chunk_body)
            elif chunk_type == "NJTL":
                item.texture_list = chunk_body
            elif chunk_type == "NMDM":
                item.animation = chunk_body
            elif chunk_type == "POF0":
                if prev_chunk_offset is None or prev_chunk_size is None:
                    # POF0 must always come after another chunk
                    warn("BML Warning: File '{}' contains a POF0 chunk at index zero".format(item.name))
                else:
                    item.pointer_table = parse_pof0(item.name, file_data, prev_chunk_offset, chunk_offset, chunk_header.body_size)
            else:
                warn("BML Warning: File '{}' has an unknown IFF chunk type '{}'".format(item.name, chunk_type))

            prev_chunk_offset = chunk_offset
            prev_chunk_size = int(chunk_header.body_size)
            chunk_offset += chunk_header.body_size + chunk_header_size

        file_offset += util.align_up(file_description.compressed_size, file_alignment)

        if file_description.textures_compressed_size > 0:
            # Texture archive is always PRS compressed
            item.texture_archive = prs.decompress(bml[file_offset:file_offset + file_description.textures_compressed_size])
            file_offset += util.align_up(file_description.textures_compressed_size, file_alignment)

    return items


def to_blender_mesh(bml_item: BmlItem, xvm: xvm.Xvm | None) -> list[Collection]:
    collections: list[Collection] = []
    if bml_item.name.endswith(".xj"):
        for model in bml_item.models:
            (root_node, _) = xj.XjMeshTreeNode.deserialize_from(model, 0)
            collections.append(xj.xj_to_blender_mesh(bml_item.name, root_node, xvm))
    elif bml_item.name.endswith(".nj"):
        for model in bml_item.models:
            coll = nj_to_blender_mesh(bml_item.name, model, 0)
            if coll is not None:
                collections.append(coll)
    else:
        raise Exception("BML Error: Failed to identify model format. Expected filename to end with '.nj' or '.xj', but it was '{}'.".format(bml_item.name))
    return collections


def read(bml_path: str, bml_xvm: xvm.Xvm | None) -> list[Collection]:
    bml = parse_bml(bml_path)
    collections: list[Collection] = []
    for bml_item in bml:
        if len(bml_item.models) > 0:
            collections += to_blender_mesh(bml_item, bml_xvm)
        else:
            warn("BML Warning: Skipping unsupported file '{}'.".format(bml_item.name))
    return collections


def write(bml_path: str, xvm_path: str, root_objs: list[bpy.types.Object]):
    root_objs_by_collection: dict[str, list[bpy.types.Object]] = dict()
    all_objs: list[bpy.types.Object] = root_objs.copy()
    for obj in root_objs:
        all_objs += obj.children_recursive
        coll = obj.users_collection[0]
        if coll.name not in root_objs_by_collection:
            root_objs_by_collection[coll.name] = []
        root_objs_by_collection[coll.name].append(obj)

    bml_buf = ResizableBuffer(size=0)
    files_buf = ResizableBuffer(size=0)
    texture_man = xvm.TextureManager(all_objs)

    # Write BML header at the beginning of the file
    bml_header = BmlHeader(
        file_count=len(root_objs_by_collection),
        compression_type=CompressionType.NONE,
        has_textures=0)
    _ = bml_header.serialize_into(bml_buf)

    file_alignment = 0x20 if bml_header.has_textures else 0x800

    chunks_size_sum = 0
    # Collection = file inside BML
    for coll_name in root_objs_by_collection:
        coll_models = root_objs_by_collection[coll_name]
        files_sum_before = files_buf.offset
        njcm_chunk_buf = xj.make_xj(coll_models, texture_man)
        _ = files_buf.append(njcm_chunk_buf)
        chunks_size_sum = len(njcm_chunk_buf)
        compressed_size = chunks_size_sum

        # Add padding between files
        files_buf.grow_to(files_sum_before + util.align_up(compressed_size, file_alignment))
        files_buf.seek_to_end()

        # Write file descriptions after BML header
        file_desc = FileDescription(
            name=FixedArray(bytes(coll_name[0:28] + ".xj", "ascii")),
            compressed_size=compressed_size,
            decompressed_size=chunks_size_sum,
            textures_compressed_size=0,
            textures_decompressed_size=0)
        _ = file_desc.serialize_into(bml_buf)

    # Write njm's into bml
    if len(bpy.data.actions) > 0:
        for coll_name in root_objs_by_collection:
            # One njm for each root node
            for model in root_objs_by_collection[coll_name]:
                nmdm_chunk_buf = njm.make_njm(model)
                # Chunk (+POF0) is done
                files_sum_before = files_buf.offset
                _ = files_buf.append(nmdm_chunk_buf)
                chunks_size_sum += len(nmdm_chunk_buf)
                compressed_size = chunks_size_sum

                # Add padding between files
                files_buf.grow_to(files_sum_before + util.align_up(compressed_size, file_alignment))
                files_buf.seek_to_end()

                # Write file descriptions after BML header
                file_desc = FileDescription(
                    name=FixedArray(bytes(coll_name[0:27] + ".njm", "ascii")),
                    compressed_size=compressed_size,
                    decompressed_size=chunks_size_sum,
                    textures_compressed_size=0,
                    textures_decompressed_size=0)
                _ = file_desc.serialize_into(bml_buf)
    
    # Add padding after file descriptions
    bml_buf.grow_to(util.align_up(bml_header.file_count * 0x40, 0x800))
    files_buf.seek_to_end()
    # Write files after descriptions
    _ = bml_buf.append(files_buf.buffer)
    
    with open(bml_path, "wb") as f:
        _ = f.write(bml_buf.buffer)

    if xvm_path and texture_man.has_textures():
        xvm.write(xvm_path, texture_man.get_all_textures())
