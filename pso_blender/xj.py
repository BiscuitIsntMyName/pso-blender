from collections.abc import Callable
from typing import ClassVar, TypeAlias, TypeGuard, cast, final
from warnings import warn
import bpy, os, bmesh, math, hashlib
from dataclasses import dataclass, field

from bpy.types import Collection, FloatColorAttribute, Material

from .njcm_node_properties_menu import ObjectWithNjcmSettings
from .rel_properties_menu import ObjectWithRelSettings
from .xj_material_properties_menu import MaterialWithXjSettings, TextureAddressingMode, NormalType, BlendMode, MaterialColorSource
from .serialization import Serializable, Numeric, AlignedString, Ptr32
from struct import pack_into
from .njcm import MeshTreeNode, NinjaEvalFlag
from . import tristrip, util, xvm, tam
from .iff import IffHeader, IffChunk, parse_pof0
from .njtl import TextureList, TextureListEntry


U8 = Numeric.U8
U16 = Numeric.U16
U32 = Numeric.U32
I8 = Numeric.I8
I16 = Numeric.I16
I32 = Numeric.I32
F32 = Numeric.F32
NULLPTR = Numeric.NULLPTR


def vertex_fmt_has_pos(fmt: int) -> bool:  # pyright: ignore[reportUnusedParameter]
    return True

def vertex_fmt_has_color(fmt: int) -> bool:
    fmt = fmt & 0xffff
    return fmt == 4 or fmt == 5 or fmt == 6 or fmt == 7

def vertex_fmt_has_normals(fmt: int) -> bool:
    fmt = fmt & 0xffff
    return fmt == 2 or fmt == 3 or fmt == 6 or fmt == 7

def vertex_fmt_has_uvs(fmt: int) -> bool:
    fmt = fmt & 0xffff
    return fmt == 1 or fmt == 3 or fmt == 5 or fmt == 7


@dataclass
class VertexBase(Serializable):
    _fmt: ClassVar[int] = -1

    @classmethod
    def get_fmt(cls) -> int:
        return cls._fmt

    @classmethod
    def has_pos(cls) -> bool:
        return vertex_fmt_has_pos(cls._fmt)

    @classmethod
    def has_color(cls) -> bool:
        return vertex_fmt_has_color(cls._fmt)

    @classmethod
    def has_normals(cls) -> bool:
        return vertex_fmt_has_normals(cls._fmt)

    @classmethod
    def has_uvs(cls) -> bool:
        return vertex_fmt_has_uvs(cls._fmt)


@dataclass
class VertexWithPos(VertexBase):
    x: F32 = 0.0
    y: F32 = 0.0
    z: F32 = 0.0

    def get_pos(self) -> tuple[F32, F32, F32]:
        return (self.x, self.x, self.y)
    
    def set_pos(self, pos: tuple[F32, F32, F32]):
        self.x = pos[0]
        self.y = pos[1]
        self.z = pos[2]


@dataclass
class VertexWithUv(VertexBase):
    u: F32 = 0.0
    v: F32 = 0.0

    def get_uv(self) -> tuple[F32, F32]:
        return (self.u, self.v)
    
    def set_uv(self, uv: tuple[F32, F32]):
        self.u = uv[0]
        self.v = uv[1]


@dataclass
class VertexWithColor(VertexBase):
    r: U8 = 0
    g: U8 = 0
    b: U8 = 0
    a: U8 = 0

    def get_color(self) -> tuple[U8, U8, U8, U8]:
        return (self.r, self.g, self.b, self.a)
    
    def set_color(self, color: tuple[U8, U8, U8, U8]):
        self.r = color[0]
        self.g = color[1]
        self.b = color[2]
        self.a = color[3]


@dataclass
class VertexWithNormal(VertexBase):
    nx: F32 = 0.0
    ny: F32 = 0.0
    nz: F32 = 0.0

    def get_normal(self) -> tuple[F32, F32, F32]:
        return (self.nx, self.nx, self.ny)
    
    def set_normal(self, n: tuple[F32, F32, F32]):
        self.nx = n[0]
        self.ny = n[1]
        self.nz = n[2]


def vertex_has_pos(v: VertexBase) -> TypeGuard[VertexWithPos]:
    return v.has_pos()


def vertex_has_uvs(v: VertexBase) -> TypeGuard[VertexWithUv]:
    return v.has_uvs()


def vertex_has_color(v: VertexBase) -> TypeGuard[VertexWithColor]:
    return v.has_color()


def vertex_has_normals(v: VertexBase) -> TypeGuard[VertexWithNormal]:
    return v.has_normals()


@dataclass
class VertexFormat1(VertexWithUv, VertexWithPos): # Inheritance order is important for correct field order
    _fmt: ClassVar[int] = 1
    x: F32 = 0.0
    y: F32 = 0.0
    z: F32 = 0.0
    u: F32 = 0.0
    v: F32 = 0.0


@dataclass
class VertexFormat2(VertexWithNormal, VertexWithPos):
    _fmt: ClassVar[int] = 2
    x: F32 = 0.0
    y: F32 = 0.0
    z: F32 = 0.0
    nx: F32 = 0.0
    ny: F32 = 0.0
    nz: F32 = 0.0


@dataclass
class VertexFormat3(VertexWithUv, VertexWithNormal, VertexWithPos):
    _fmt: ClassVar[int] = 3
    x: F32 = 0.0
    y: F32 = 0.0
    z: F32 = 0.0
    nx: F32 = 0.0
    ny: F32 = 0.0
    nz: F32 = 0.0
    u: F32 = 0.0
    v: F32 = 0.0


@dataclass
class VertexFormat4(VertexWithColor, VertexWithPos):
    _fmt: ClassVar[int] = 4
    x: F32 = 0.0
    y: F32 = 0.0
    z: F32 = 0.0
    # Purple default
    r: U8 = 0xff
    g: U8 = 0
    b: U8 = 0xff
    a: U8 = 0xff


@dataclass
class VertexFormat5(VertexWithUv, VertexWithColor, VertexWithPos):
    _fmt: ClassVar[int] = 5
    x: F32 = 0.0
    y: F32 = 0.0
    z: F32 = 0.0
    # Purple default
    r: U8 = 0xff
    g: U8 = 0
    b: U8 = 0xff
    a: U8 = 0xff
    u: F32 = 0.0
    v: F32 = 0.0


@dataclass
class VertexFormat6(VertexWithNormal, VertexWithColor, VertexWithPos):
    _fmt: ClassVar[int] = 6
    x: F32 = 0.0
    y: F32 = 0.0
    z: F32 = 0.0
    r: U8 = 0xff
    g: U8 = 0
    b: U8 = 0xff
    a: U8 = 0xff
    nx: F32 = 0.0
    ny: F32 = 0.0
    nz: F32 = 0.0


@dataclass
class VertexFormat7(VertexWithUv, VertexWithColor, VertexWithNormal, VertexWithPos):
    _fmt: ClassVar[int] = 7
    x: F32 = 0.0
    y: F32 = 0.0
    z: F32 = 0.0
    nx: F32 = 0.0
    ny: F32 = 0.0
    nz: F32 = 0.0
    # Purple default
    r: U8 = 0xff
    g: U8 = 0
    b: U8 = 0xff
    a: U8 = 0xff
    u: F32 = 0.0
    v: F32 = 0.0


@dataclass
class VertexBufferContainer(Serializable):
    vertex_format: U32 = 0
    vertices: Ptr32[VertexBase] = Ptr32(NULLPTR)
    vertex_size: U32 = 0
    vertex_count: U32 = 0



@dataclass
@final
class RenderStateType:
    BLEND_MODE = 2
    TEXTURE_ID = 3
    TEXTURE_ADDRESSING = 4
    MATERIAL = 5
    LIGHTING = 6
    CAMERA_SPACE_NORMALS = 7
    MATERIAL_SOURCE = 8


@dataclass
class RenderStateArgs(Serializable):
    state_type: U32 = 0
    arg1: U32 = 0
    arg2: U32 = 0
    unk2: U32 = 0


# Helper class for writing array, not needed for reading
@dataclass
class IndexBuffer(Serializable):
    indices: list[U16] = field(default_factory=list)


@dataclass
class IndexBufferContainer(Serializable):
    renderstate_args: Ptr32[RenderStateArgs] = Ptr32(NULLPTR)
    renderstate_args_count: U32 = 0
    indices: Ptr32[U16] = Ptr32(NULLPTR)
    index_count: U32 = 0
    vertex_buffer_index: U32 = 0


@dataclass
class XjMesh(Serializable):
    flags: U32 = 0
    vertex_buffers: Ptr32[VertexBufferContainer] = Ptr32(NULLPTR)
    vertex_buffer_count: U32 = 0
    index_buffers: Ptr32[IndexBufferContainer] = Ptr32(NULLPTR)
    index_buffer_count: U32 = 0
    alpha_index_buffers: Ptr32[IndexBufferContainer] = Ptr32(NULLPTR)
    alpha_index_buffer_count: U32 = 0


# Specialize node
@final
@dataclass
class XjMeshTreeNode(MeshTreeNode[XjMesh]):
    mesh: Ptr32[XjMesh] = Ptr32(NULLPTR)
    child: Ptr32["XjMeshTreeNode"] = Ptr32(NULLPTR)  # pyright: ignore[reportIncompatibleVariableOverride]
    next: Ptr32["XjMeshTreeNode"] = Ptr32(NULLPTR)  # pyright: ignore[reportIncompatibleVariableOverride]


@dataclass
class VertexAttributes:
    idx: int = 0
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    nx: float = 0.0
    ny: float = 0.0
    nz: float = 0.0
    u: float = 0.0
    v: float = 0.0
    r: int = 0
    g: int = 0
    b: int = 0
    a: int = 0

    def __hash__(self) -> int:
        rounding = 5
        return hash((self.idx,
            round(self.u, rounding), round(self.v, rounding),
            self.r, self.g, self.b, self.a,
            round(self.nx, rounding), round(self.ny, rounding), round(self.nz, rounding)))


def determine_vertex_format(has_textures: bool, has_vertex_colors: bool, use_normals: bool) -> int:
    # Figure out the right vertex format based on what data mesh has.
    if has_textures:
        if has_vertex_colors:
            if use_normals:
                # Coords + Normals + color + UVs
                return 7
            else:
                # Coords + color + UVs
                return 5
        else:
            if use_normals:
                # Coords + normals + UVs
                return 3
            else:
                # Coords + UVs
                return 1
    else:
        if use_normals:
            if has_vertex_colors:
                # Coords + color + normals
                return 6
            else:
                # Coords + normals
                return 2
        else:
            # Coords + color
            return 4


def get_vertex_constructor(vertex_format: int):
    ctors = [
        VertexFormat1,
        VertexFormat2,
        VertexFormat3,
        VertexFormat4,
        VertexFormat5,
        VertexFormat6,
        VertexFormat7]
    vertex_format = vertex_format & 0xffff
    return ctors[vertex_format - 1]


VertexFactory: TypeAlias = Callable[[VertexAttributes],
    VertexFormat1 |
    VertexFormat2 |
    VertexFormat3 |
    VertexFormat4 |
    VertexFormat5 |
    VertexFormat6 |
    VertexFormat7]


def get_vertex_factory(vertex_format: int) -> VertexFactory:
    factories: list[VertexFactory] = [
        lambda attrs: VertexFormat1(
            x=attrs.x,
            y=attrs.y,
            z=attrs.z,
            u=attrs.u,
            v=attrs.v),
        lambda attrs: VertexFormat2(
            x=attrs.x,
            y=attrs.y,
            z=attrs.z,
            nx=attrs.nx,
            ny=attrs.ny,
            nz=attrs.nz),
        lambda attrs: VertexFormat3(
            x=attrs.x,
            y=attrs.y,
            z=attrs.z,
            u=attrs.u,
            v=attrs.v),
        lambda attrs: VertexFormat4(
            x=attrs.x,
            y=attrs.y,
            z=attrs.z,
            r=attrs.r,
            g=attrs.g,
            b=attrs.b,
            a=attrs.a),
        lambda attrs: VertexFormat5(
            x=attrs.x,
            y=attrs.y,
            z=attrs.z,
            r=attrs.r,
            g=attrs.g,
            b=attrs.b,
            a=attrs.a,
            u=attrs.u,
            v=attrs.v),
        lambda attrs: VertexFormat6(
            x=attrs.x,
            y=attrs.y,
            z=attrs.z,
            nx=attrs.nx,
            ny=attrs.ny,
            nz=attrs.nz,
            r=attrs.r,
            g=attrs.g,
            b=attrs.b,
            a=attrs.a),
        lambda attrs: VertexFormat7(
            x=attrs.x,
            y=attrs.y,
            z=attrs.z,
            nx=attrs.nx,
            ny=attrs.ny,
            nz=attrs.nz,
            r=attrs.r,
            g=attrs.g,
            b=attrs.b,
            a=attrs.a,
            u=attrs.u,
            v=attrs.v)]
    vertex_format = vertex_format & 0xffff
    return factories[vertex_format - 1]


# Helper class for writing array, not needed for reading
@dataclass
class VertexBuffer(Serializable):
    vertices: list[VertexBase] = field(default_factory=list)


@dataclass
class TempFace:
    verts: tuple[int, int, int]
    material_idx: int


def write_vertex_buffer(
    destination: util.AbstractFileArchive,
    obj: bpy.types.Object,
    blender_mesh: bpy.types.Mesh,
    xj_mesh: XjMesh,
    has_textures: bool,
    vertex_colors: bpy.types.FloatColorAttribute | None,
    normal_type: int | None) -> list[TempFace]:
    """Returns triangles matching indices of created vertices"""

    use_normals = normal_type is not None
    vertex_format = determine_vertex_format(has_textures, bool(vertex_colors), use_normals)
    create_vertex = get_vertex_factory(vertex_format)

    if cast(ObjectWithRelSettings, obj).rel_settings.is_translucent:
        vertex_format |= 0x10000

    # If mesh has per-loop data then we must produce one vertex for each loop, otherwise one vertex for each vertex
    has_per_loop_data = normal_type == NormalType.Face or (vertex_colors is not None and vertex_colors.domain == "CORNER")

    # Our vertex list might not match blender's vertices and triangles if we split or deduplicate vertices
    # so let's create new vertex and triangle lists
    vertices: list[VertexBase] = []
    triangles: list[TempFace] = []
    vertex_registry: dict[VertexAttributes, int] = {}

    for face in blender_mesh.loop_triangles:
        face_indices: list[int] = []
        for (vert_idx, loop_idx) in zip(face.vertices, face.loops):
            # Gather all required and optional vertex attributes
            vertex_attributes = VertexAttributes(idx=vert_idx)
            # Exclude translation from transform. Use matrix_local (relative to the object's own
            # parent, i.e. its chunk_root), not matrix_world - the chunk's own rotation
            # (chunk_root.rotation_euler, set from the file's chunk.rot_x/y/z) is already applied
            # separately via the parent/child scene-graph relationship on import and in-game, so
            # baking it into the vertex data too double-applies it. Invisible for the vast
            # majority of chunks (which have zero rotation, so matrix_local and matrix_world's
            # rotation component coincide), but confirmed on real map_acity00_00 data: chunk 30
            # (the reported "commerce district") has an genuine 180-degree chunk-level rotation,
            # and every mesh in it was being doubly-rotated on export, corrupting its geometry.
            local_vert = blender_mesh.vertices[vert_idx]
            world_vert = local_vert.co.to_4d()
            world_vert.w = 0
            world_vert = util.from_blender_axes((obj.matrix_local @ world_vert).to_3d())
            vertex_attributes.x = world_vert[0]
            vertex_attributes.y = world_vert[1]
            vertex_attributes.z = world_vert[2]
            # Get UVs
            if has_textures:
                uv = blender_mesh.uv_layers[0].uv[loop_idx].vector
                vertex_attributes.u = uv[0]
                vertex_attributes.v = uv[1]
            # Get colors
            if vertex_colors:
                if has_per_loop_data:
                    if vertex_colors.domain == "POINT":
                        col = vertex_colors.data[vert_idx].color
                    elif vertex_colors.domain == "CORNER":
                        col = vertex_colors.data[loop_idx].color
                    else:
                        raise Exception("XJ error in object '{}': Invalid vertex color domain '{}'.".format(obj.name, vertex_colors.domain))
                else:
                    col = vertex_colors.data[vert_idx].color
                # VertexWithColor.get_color() (the read path) returns (self.r, self.g, self.b,
                # self.a) with no channel reordering, so a pristine game-authored file's "r"
                # field really does decode straight into Blender's red channel - the format is
                # plain RGBA, not BGRA. This previously assigned col[0] (Blender's red) into the
                # "b" field and col[2] (blue) into "r", a swap read never undid - harmless while
                # only ever reading original files, but any mesh with real baked vertex-color
                # lighting got its red/blue channels swapped on every export, visibly changing
                # its overall tint. Need to clamp because light baking can cause values to go
                # higher than normal.
                vertex_attributes.r = int(util.clamp(col[0], 0.0, 1.0) * 0xff)
                vertex_attributes.g = int(util.clamp(col[1], 0.0, 1.0) * 0xff)
                vertex_attributes.b = int(util.clamp(col[2], 0.0, 1.0) * 0xff)
                vertex_attributes.a = int(util.clamp(col[3], 0.0, 1.0) * 0xff)
            if use_normals:
                # Vertex or face normal
                if has_per_loop_data:
                    normal = local_vert.normal if normal_type == NormalType.Vertex else face.normal
                else:
                    # Face normals cannot be encoded as per-vertex data
                    normal = local_vert.normal
                normal = normal.to_4d()
                normal.w = 0
                # Same matrix_local reasoning as the position transform above - the chunk's
                # rotation must not be double-applied to normals either.
                normal = util.from_blender_axes((obj.matrix_local @ normal).to_3d().normalized())
                vertex_attributes.nx = normal[0]
                vertex_attributes.ny = normal[1]
                vertex_attributes.nz = normal[2]
            # Can we reuse an existing vertex or do we need to create a new one?
            if has_per_loop_data or vertex_attributes not in vertex_registry:
                # Create new vertex
                new_idx = len(vertices)
                vertex_registry[vertex_attributes] = new_idx
                # Construct actual vertex from attributes
                vertices.append(create_vertex(vertex_attributes))
            # Use new indices to create triangles
            face_indices.append(vertex_registry[vertex_attributes])
        triangles.append(TempFace(material_idx=face.material_index, verts=cast(tuple[int, int, int], tuple(face_indices))))
    
    vertex_size = vertices[0].type_size()
    vertex_buffer = VertexBuffer(vertices=vertices)
    vb_ptr_diff = vertex_size * len(vertices) // 4
    if vb_ptr_diff >= 0xffff:
        warn("XJ Warning: Mesh \"{}\" is too large to fit inside a REL file (mesh size is {}, maximum size is {})".format(obj.name, vb_ptr_diff, 0xffff))

    # Put all vertices in one buffer
    xj_mesh.vertex_buffer_count = 1
    xj_mesh.vertex_buffers = Ptr32(destination.write(VertexBufferContainer(
        vertex_format=vertex_format,
        vertices=Ptr32(destination.write(vertex_buffer)),
        vertex_size=vertex_size,
        vertex_count=len(vertex_buffer.vertices))))

    return triangles

class MaterialStrips:
    material_index: int
    material_name: str | None
    renderstate_args: list[RenderStateArgs]
    strips: list[list[int]]

    def __init__(self, material_index: int, material: bpy.types.Material | None, strips: list[list[int]]):
        self.material_index = material_index
        # Used by write_index_buffers to look up this strip's texture by material identity
        # instead of assuming positional alignment with texture_man.get_object_textures(obj) -
        # that list additionally skips any material with no resolvable diffuse image, so a
        # position-based lookup silently misaligns (or goes out of range) as soon as any earlier
        # slot on the object has no texture.
        self.material_name = material.name if material else None
        if material:
            xj_settings = cast(MaterialWithXjSettings, material).xj_settings
            self.renderstate_args = make_renderstate_args(
                blend_modes=(getattr(BlendMode, xj_settings.src_blend).value, getattr(BlendMode, xj_settings.dst_blend).value),
                texture_addressing=(getattr(TextureAddressingMode, xj_settings.tex_addr_u).value, getattr(TextureAddressingMode, xj_settings.tex_addr_v).value),
                lighting=xj_settings.lighting,
                material=(xj_settings.material1, xj_settings.material2),
                camera_space_normals=xj_settings.camera_space_normals,
                diffuse_color_source=getattr(MaterialColorSource, xj_settings.diffuse_color_source).value)
        else:
            # Empty slot
            self.renderstate_args = []
        self.strips = strips


def create_tristrips_grouped_by_material(obj: bpy.types.Object, triangles: list[TempFace], texture_man: xvm.TextureManager) -> list[MaterialStrips]:
    """Returns vertex indices"""
    material_strips: list[MaterialStrips] = []
    if texture_man.object_has_textures(obj):
        material_faces: list[list[tuple[int, int, int]]] = []
        # Remove empty slots
        material_slots = [slot for slot in obj.material_slots if slot.material is not None]
        # Get all faces grouped by their material, then stripify them
        for (mat_idx, mat_slot) in enumerate(material_slots):
            material_faces.append([])
            material_strips.append(MaterialStrips(mat_idx, mat_slot.material, []))
        for face in triangles:
            material_faces[face.material_idx].append((face.verts[0], face.verts[1], face.verts[2]))
        for mat_idx in range(len(material_slots)):
            strips = cast(list[list[int]], tristrip.stripify(material_faces[mat_idx], stitchstrips=True))  # pyright: ignore[reportUnknownMemberType]
            material_strips[mat_idx].strips = strips
    else:
        faces: list[tuple[int, int, int]] = []
        for face in triangles:
            faces.append((face.verts[0], face.verts[1], face.verts[2]))
        strips = cast(list[list[int]], tristrip.stripify(faces, stitchstrips=True))  # pyright: ignore[reportUnknownMemberType]
        material_strips.append(MaterialStrips(-1, None, strips))
    return material_strips


def write_index_buffers(
    destination: util.AbstractFileArchive,
    obj: bpy.types.Object,
    triangles: list[TempFace],
    xj_mesh: XjMesh,
    texture_man: xvm.TextureManager,
    has_vertex_alpha: bool):
    # Texture IDs must be 0-based for the render settings
    # One buffer per strip
    material_strips = create_tristrips_grouped_by_material(obj, triangles, texture_man)
    opaque_index_buffer_containers: list[IndexBufferContainer] = []
    alpha_index_buffer_containers: list[IndexBufferContainer] = []
    textures = texture_man.get_object_textures(obj)
    # Looked up by material identity (name), not position - textures skips any material slot with
    # no resolvable diffuse image (see util.get_object_diffuse_textures), which material_strips
    # does not, so the two lists aren't guaranteed to line up positionally as soon as any slot on
    # this object has a material but no texture (a legitimate, real case - e.g. a solid-color
    # material) - a positional index lookup here previously raised IndexError, or worse silently
    # paired a strip with the wrong texture.
    textures_by_material_name = {tex.material_name: tex for tex in textures}
    texture_id_base = texture_man.get_base_id()
    for material_strip_data in material_strips:
        for strip in material_strip_data.strips:
            # Strips can be empty due to unused material slots, skip them
            if len(strip) < 1:
                continue
            has_alpha = cast(bool, cast(ObjectWithRelSettings, obj).rel_settings.is_translucent) or has_vertex_alpha
            # Create render state args
            first_rs_arg_ptr = NULLPTR
            rs_args = material_strip_data.renderstate_args
            tex = textures_by_material_name.get(material_strip_data.material_name)
            if tex is not None:
                has_alpha = has_alpha or tex.has_alpha
                rs_args += make_renderstate_args(
                    texture_id=tex.id - texture_id_base)
            rs_arg_count = len(rs_args)
            for rs_arg in rs_args:
                ptr = destination.write(rs_arg)
                if first_rs_arg_ptr == NULLPTR:
                    first_rs_arg_ptr = ptr
            # Write Indices
            buf_ptr = destination.write(IndexBuffer(indices=strip), True)
            container = IndexBufferContainer(
                indices=Ptr32(buf_ptr),
                index_count=len(strip),
                renderstate_args=Ptr32(first_rs_arg_ptr),
                renderstate_args_count=rs_arg_count)
            if has_alpha:
                alpha_index_buffer_containers.append(container)
            else:
                opaque_index_buffer_containers.append(container)
    # Index buffer containers need to be written back to back
    first_alpha_index_buffer_container_ptr = NULLPTR
    for buf in alpha_index_buffer_containers:
        ptr = destination.write(buf)
        if first_alpha_index_buffer_container_ptr == NULLPTR:
            first_alpha_index_buffer_container_ptr = ptr
    first_opaque_index_buffer_container_ptr = NULLPTR
    for buf in opaque_index_buffer_containers:
        ptr = destination.write(buf)
        if first_opaque_index_buffer_container_ptr == NULLPTR:
            first_opaque_index_buffer_container_ptr = ptr
    xj_mesh.alpha_index_buffer_count = len(alpha_index_buffer_containers)
    xj_mesh.alpha_index_buffers = Ptr32(first_alpha_index_buffer_container_ptr)
    xj_mesh.index_buffer_count = len(opaque_index_buffer_containers)
    xj_mesh.index_buffers = Ptr32(first_opaque_index_buffer_container_ptr)


def make_mesh(destination: util.AbstractFileArchive, obj: bpy.types.Object, blender_mesh: bpy.types.Mesh, texture_man: xvm.TextureManager) -> XjMesh:
    if texture_man.object_has_textures(obj) and len(blender_mesh.uv_layers) < 1:
        raise Exception("XJ error in object '{}': Object has texture but is missing UVs".format(obj.name))

    mesh = XjMesh()

    normal_type = None
    for mat_slot in obj.material_slots:
        if not mat_slot.material:
            # Empty slot
            continue
        # Lighting requires normals
        xj_settings = cast(MaterialWithXjSettings, mat_slot.material).xj_settings
        if xj_settings.lighting or xj_settings.camera_space_normals:
            if not xj_settings.normal_type:
                xj_settings.normal_type = NormalType.Vertex.name
            # XXX: Camera projection setting is applied to entire mesh instead of material vertex group
            normal_type = getattr(NormalType, xj_settings.normal_type)
            break

    vertex_colors: FloatColorAttribute | None = None
    if len(blender_mesh.color_attributes) > 0:
        vertex_colors = cast(FloatColorAttribute, blender_mesh.color_attributes[0])
    elif normal_type is not None:
        # Effects that need normals usually also need vcol. Let's add a blank white color attribute.
        vertex_colors = cast(FloatColorAttribute, blender_mesh.color_attributes.new("xj_default_vcol", "FLOAT_COLOR", "POINT"))
        assert vertex_colors is not None
        for attr in vertex_colors.data:
            attr.color[0] = 1
            attr.color[1] = 1
            attr.color[2] = 1
    else:
        vertex_colors = None
    has_vertex_color = vertex_colors is not None
    has_vertex_alpha = False
    if has_vertex_color:
        # Despite the names of the types, they appear to be identical
        if vertex_colors.data_type != "FLOAT_COLOR" and vertex_colors.data_type != "BYTE_COLOR":
            raise Exception("XJ error in object '{}': Invalid vertex color format '{}'.".format(obj.name, vertex_colors.data_type))
        if vertex_colors.domain != "CORNER" and vertex_colors.domain != "POINT":
            raise Exception("XJ error in object '{}': Invalid vertex color type '{}'. Please select 'Vertex' or 'Face Corner' when creating color attribute.".format(obj.name, vertex_colors.domain))
        for attr in vertex_colors.data:
            if attr.color[3] < 1:
                has_vertex_alpha = True
                break
    # Write various mesh data
    triangles = write_vertex_buffer(destination, obj, blender_mesh, mesh, texture_man.object_has_textures(obj), vertex_colors, normal_type)
    write_index_buffers(destination, obj, triangles, mesh, texture_man, has_vertex_alpha)
    return mesh


def make_renderstate_args(
    *,
    texture_id: int | None=None,
    texture_addressing: tuple[int, int] | None=None,
    blend_modes: tuple[int, int] | None=None,
    lighting: bool | None=None,
    material: tuple[int, int] | None=None,
    camera_space_normals: bool | None=None,
    diffuse_color_source: int | None=None
) -> list[RenderStateArgs]:
    rs_args: list[RenderStateArgs] = []
    if texture_id is not None:
        rs_args.append(RenderStateArgs(
            state_type=RenderStateType.TEXTURE_ID,
            arg1=texture_id))
    if texture_addressing is not None:
        rs_args.append(RenderStateArgs(
            state_type=RenderStateType.TEXTURE_ADDRESSING,
            arg1=int(texture_addressing[0]),
            arg2=int(texture_addressing[1])))
    if blend_modes is not None:
        rs_args.append(RenderStateArgs(
            state_type=RenderStateType.BLEND_MODE,
            arg1=int(blend_modes[0]),
            arg2=int(blend_modes[1])))
    if lighting is not None:
        rs_args.append(RenderStateArgs(
            state_type=RenderStateType.LIGHTING,
            arg1=int(lighting)))
    if material is not None:
        rs_args.append(RenderStateArgs(
            state_type=RenderStateType.MATERIAL,
            arg1=int(material[0]),
            arg2=int(material[1])))
    if camera_space_normals is not None:
        rs_args.append(RenderStateArgs(
            state_type=RenderStateType.CAMERA_SPACE_NORMALS,
            arg1=int(camera_space_normals)))
    if diffuse_color_source is not None:
        rs_args.append(RenderStateArgs(
            state_type=RenderStateType.MATERIAL_SOURCE,
            arg1=int(diffuse_color_source)))
    return rs_args


def make_texture_addressing_node(node_tree: bpy.types.ShaderNodeTree, addr_mode: str) -> bpy.types.ShaderNodeMath:
    """A Math node that maps one UV component into [0, 1] according to a D3D texture addressing mode."""
    math_node = cast(bpy.types.ShaderNodeMath, node_tree.nodes.new(type="ShaderNodeMath"))
    if addr_mode in (TextureAddressingMode.D3DTADDRESS_MIRROR.name, TextureAddressingMode.D3DTADDRESS_MIRRORONCE.name):
        math_node.operation = "PINGPONG"
        math_node.inputs[1].default_value = 1.0
    elif addr_mode in (TextureAddressingMode.D3DTADDRESS_CLAMP.name, TextureAddressingMode.D3DTADDRESS_BORDER.name):
        # No dedicated border color is available, so BORDER is approximated as CLAMP (extend
        # edge texel) rather than a hard transparent cutoff.
        math_node.operation = "ADD"
        math_node.inputs[1].default_value = 0.0
        math_node.use_clamp = True
    else:
        # WRAP (also the fallback default)
        math_node.operation = "WRAP"
        math_node.inputs[1].default_value = 0.0
        math_node.inputs[2].default_value = 1.0
    return math_node


def get_or_create_texture_node_group(xvm_filename: str, tex_id: "int | str", img: bpy.types.Image, generate_mipmaps: bool, frame_count: "int | None" = None) -> bpy.types.ShaderNodeTree:
    """A tiny node group wrapping just this texture's Image Texture node, shared by every
    material variant of this texture (different blend mode / addressing settings, see
    make_material) so that swapping which image represents this texture - the whole point of a
    texture pack - only has to be done once, in one shared place, instead of on every variant
    material separately.

    Deliberately does NOT include the UV addressing chain (Mapping / per-axis wrap math nodes) -
    that varies legitimately per variant (e.g. CLAMP addressing on one placement of a texture,
    WRAP on another), so it stays inline in each material rather than being shared here.

    Also carries "generate_mipmaps" as a custom property, stamped once here at creation time. Like
    the image itself, whether the exported texture includes a mip chain is a property of the one
    shared physical texture, not of any particular mesh placement - see
    XjMaterialSettings.generate_mipmaps in xj_material_properties_menu.py, which reads/writes this
    property instead of storing its own per-material value.
    """
    group_name = "ImgGroup_{}_{}".format(xvm_filename, tex_id)
    existing = bpy.data.node_groups.get(group_name)
    if existing is not None:
        return cast(bpy.types.ShaderNodeTree, existing)

    group = cast(bpy.types.ShaderNodeTree, bpy.data.node_groups.new(group_name, "ShaderNodeTree"))
    group["generate_mipmaps"] = generate_mipmaps
    group.interface.new_socket(name="Vector", in_out="INPUT", socket_type="NodeSocketVector")
    group.interface.new_socket(name="Color", in_out="OUTPUT", socket_type="NodeSocketColor")
    group.interface.new_socket(name="Alpha", in_out="OUTPUT", socket_type="NodeSocketFloat")

    group_input = group.nodes.new(type="NodeGroupInput")
    group_output = group.nodes.new(type="NodeGroupOutput")
    tex_image_node = cast(bpy.types.ShaderNodeTexImage, group.nodes.new(type="ShaderNodeTexImage"))
    # Named explicitly (rather than relying on "the only TEX_IMAGE node in this group") so
    # find_diffuse_image can reliably tell this apart from the optional PSO_Normal/PSO_Metal
    # nodes a relief composite (see _wire_relief_composite below) may add to the same group.
    tex_image_node.name = "PSO_Diffuse"
    tex_image_node.image = img
    # The addressing math nodes upstream (per material) pre-wrap U/V into [0, 1], so this is just
    # a safe fallback for floating point edge cases right at the boundary.
    tex_image_node.extension = "EXTEND"
    if frame_count is not None:
        # A SEQUENCE-source image's ImageUser defaults to frame_duration=1 (only sequence-frame 1,
        # the first file, is ever considered "in range") and use_auto_refresh=False - without this,
        # frame 1 shows correctly but every other scene frame has no frame mapped to it at all
        # (Blender's "missing image data" pink placeholder), and nothing ever advances anyway.
        # frame_start=0 and frame_offset=-1 (not Blender's defaults of 1 and 0): confirmed by real
        # in-Blender testing - at the scene's common resting playhead position (frame 0/1),
        # nothing showed the real first frame until both were adjusted together.
        tex_image_node.image_user.frame_start = 0
        tex_image_node.image_user.frame_offset = -1
        tex_image_node.image_user.frame_duration = frame_count
        tex_image_node.image_user.use_auto_refresh = True

    group_input.location = (-300, 0)
    tex_image_node.location = (0, 0)
    group_output.location = (300, 0)

    _ = group.links.new(group_input.outputs["Vector"], tex_image_node.inputs["Vector"])
    _ = group.links.new(tex_image_node.outputs["Color"], group_output.inputs["Color"])
    _ = group.links.new(tex_image_node.outputs["Alpha"], group_output.inputs["Alpha"])
    return group


# Relief-composite node names, shared between _wire_relief_composite (build/teardown) and
# xvm.bake_texture_group (detects "has this group been customized" by checking whether the
# group's Color output is still directly linked to PSO_Diffuse, or fed through nodes like these).
_RELIEF_IMAGE_NODE_NAMES = ("PSO_Normal", "PSO_Metal", "PSO_Roughness")
_RELIEF_NODE_PREFIX = "PSO_Relief_"


def _wire_relief_composite(
    group_tree: bpy.types.ShaderNodeTree,
    diffuse_node: bpy.types.ShaderNodeTexImage,
    normal_image: "bpy.types.Image | None",
    metal_image: "bpy.types.Image | None",
    roughness_image: "bpy.types.Image | None" = None,
) -> None:
    """(Re)builds the shared texture group's internal Color pipeline to optionally composite a
    normal map's relief and/or a metal map's low-diffuse-response cue directly into the diffuse
    color - live, using only Math/Mix/Map Range nodes (no BSDF, no light), so it's visible in the
    viewport under any shading mode without depending on scene lighting, and so export (see
    xvm.bake_texture_group) can reproduce exactly the same result by baking this group's actual
    output instead of needing a separately-maintained formula.

    roughness_image, if present, modulates the *normal* relief factor only (not metal's, a
    different physical property): rough/matte areas show the full relief darkening, smooth areas
    are pulled back toward neutral (no darkening) - a fake "occlusion" cue makes little sense on a
    mirror-smooth surface. Has no effect if normal_image is None (nothing to modulate).

    Always tears down and rebuilds from scratch (removing any node from a previous call, found by
    name) rather than trying to patch existing wiring incrementally - simpler, and avoids leftover
    nodes from an earlier state (e.g. metal removed but normal kept) accidentally staying wired in.

    normal_image=None, metal_image=None restores the group's original default wiring (diffuse
    node's Color/Alpha linked directly to the group's outputs) - this is also what every "Send to
    ImgGroup" operator calls when sending a plain image, so a previously-wired composite is
    correctly torn down rather than left stale.
    """
    for name in _RELIEF_IMAGE_NODE_NAMES:
        node = group_tree.nodes.get(name)
        if node is not None:
            group_tree.nodes.remove(node)
    for node in list(group_tree.nodes):
        if node.name.startswith(_RELIEF_NODE_PREFIX):
            group_tree.nodes.remove(node)

    group_input = next(n for n in group_tree.nodes if n.type == "GROUP_INPUT")
    group_output = next(n for n in group_tree.nodes if n.type == "GROUP_OUTPUT")

    # Alpha always passes straight through from the diffuse node - the relief composite only ever
    # affects color.
    for link in list(group_output.inputs["Alpha"].links):
        group_tree.links.remove(link)
    _ = group_tree.links.new(diffuse_node.outputs["Alpha"], group_output.inputs["Alpha"])

    for link in list(group_output.inputs["Color"].links):
        group_tree.links.remove(link)

    if normal_image is None and metal_image is None:
        _ = group_tree.links.new(diffuse_node.outputs["Color"], group_output.inputs["Color"])
        group_output.location = (300, 0)  # back to its original spot next to PSO_Diffuse
        return

    # xvm._RELIEF_MIN_DARKEN / _METAL_MAX_DARKEN define the strengths once, in Python, and get read
    # into these nodes' default values here - the numbers stay in one place even though the
    # formula itself is expressed twice (once as nodes for the live viewport, once implicitly via
    # baking this same graph for export - see xvm.bake_texture_group).
    factor_socket: bpy.types.NodeSocket | None = None

    if normal_image is not None:
        normal_tex = cast(bpy.types.ShaderNodeTexImage, group_tree.nodes.new(type="ShaderNodeTexImage"))
        normal_tex.name = "PSO_Normal"
        normal_tex.image = normal_image
        normal_tex.location = (0, -300)
        _ = group_tree.links.new(group_input.outputs["Vector"], normal_tex.inputs["Vector"])

        separate_normal = group_tree.nodes.new(type="ShaderNodeSeparateColor")
        separate_normal.name = _RELIEF_NODE_PREFIX + "SeparateNormal"
        separate_normal.location = (250, -300)
        _ = group_tree.links.new(normal_tex.outputs["Color"], separate_normal.inputs["Color"])

        # Normal maps store tangent-space direction with components 0-1 mapping to -1..1 (standard
        # OpenGL convention) - so stored Blue (Z) of 0.5 decodes to 0 (fully tilted away from
        # straight up) and 1.0 decodes to 1 (straight up, untilted). A single clamped Map Range
        # from [0.5, 1.0] to [_RELIEF_MIN_DARKEN, 1.0] does the decode+clamp+remap in one step:
        # straight-up areas (Blue=1.0) are untouched, fully-tilted areas (Blue<=0.5) are darkened
        # to _RELIEF_MIN_DARKEN - the more a pixel's normal leans away from straight up, the more
        # it's darkened, approximating how a crease or bump catches/loses ambient light regardless
        # of which way any particular light happens to be pointed.
        remap_normal = cast(bpy.types.ShaderNodeMapRange, group_tree.nodes.new(type="ShaderNodeMapRange"))
        remap_normal.name = _RELIEF_NODE_PREFIX + "MapRangeNormal"
        remap_normal.location = (500, -300)
        remap_normal.clamp = True
        remap_normal.inputs[1].default_value = 0.5  # From Min
        remap_normal.inputs[2].default_value = 1.0  # From Max
        remap_normal.inputs[3].default_value = xvm._RELIEF_MIN_DARKEN  # To Min  # pyright: ignore[reportPrivateUsage]
        remap_normal.inputs[4].default_value = 1.0  # To Max
        _ = group_tree.links.new(separate_normal.outputs["Blue"], remap_normal.inputs[0])  # Value
        factor_socket = remap_normal.outputs["Result"]

        if roughness_image is not None:
            roughness_tex = cast(bpy.types.ShaderNodeTexImage, group_tree.nodes.new(type="ShaderNodeTexImage"))
            roughness_tex.name = "PSO_Roughness"
            roughness_tex.image = roughness_image
            roughness_tex.location = (0, -900)
            _ = group_tree.links.new(group_input.outputs["Vector"], roughness_tex.inputs["Vector"])

            separate_roughness = group_tree.nodes.new(type="ShaderNodeSeparateColor")
            separate_roughness.name = _RELIEF_NODE_PREFIX + "SeparateRoughness"
            separate_roughness.location = (250, -900)
            _ = group_tree.links.new(roughness_tex.outputs["Color"], separate_roughness.inputs["Color"])

            # lerp(1.0, normal_factor, roughness): at roughness=0 (smooth) the result is 1.0 - no
            # darkening at all; at roughness=1 (matte) it's the normal factor unmodified, same as
            # when there's no roughness map; values in between scale proportionally.
            modulate = cast(bpy.types.ShaderNodeMix, group_tree.nodes.new(type="ShaderNodeMix"))
            modulate.name = _RELIEF_NODE_PREFIX + "ModulateByRoughness"
            modulate.location = (750, -300)
            modulate.data_type = "FLOAT"
            _ = group_tree.links.new(separate_roughness.outputs["Red"], modulate.inputs[0])  # Factor
            modulate.inputs[2].default_value = 1.0  # A - neutral/no-effect
            _ = group_tree.links.new(factor_socket, modulate.inputs[3])  # B - the raw normal factor
            factor_socket = modulate.outputs[0]  # Result

    if metal_image is not None:
        metal_tex = cast(bpy.types.ShaderNodeTexImage, group_tree.nodes.new(type="ShaderNodeTexImage"))
        metal_tex.name = "PSO_Metal"
        metal_tex.image = metal_image
        metal_tex.location = (0, -600)
        _ = group_tree.links.new(group_input.outputs["Vector"], metal_tex.inputs["Vector"])

        separate_metal = group_tree.nodes.new(type="ShaderNodeSeparateColor")
        separate_metal.name = _RELIEF_NODE_PREFIX + "SeparateMetal"
        separate_metal.location = (250, -600)
        _ = group_tree.links.new(metal_tex.outputs["Color"], separate_metal.inputs["Color"])

        # Metal value of 0 (dielectric) leaves diffuse untouched; 1 (fully metal) darkens toward
        # _METAL_MAX_DARKEN, approximating a metal's low diffuse response.
        remap_metal = cast(bpy.types.ShaderNodeMapRange, group_tree.nodes.new(type="ShaderNodeMapRange"))
        remap_metal.name = _RELIEF_NODE_PREFIX + "MapRangeMetal"
        remap_metal.location = (500, -600)
        remap_metal.clamp = True
        remap_metal.inputs[1].default_value = 0.0  # From Min
        remap_metal.inputs[2].default_value = 1.0  # From Max
        remap_metal.inputs[3].default_value = 1.0  # To Min
        remap_metal.inputs[4].default_value = xvm._METAL_MAX_DARKEN  # To Max  # pyright: ignore[reportPrivateUsage]
        _ = group_tree.links.new(separate_metal.outputs["Red"], remap_metal.inputs[0])  # Value

        if factor_socket is not None:
            multiply = group_tree.nodes.new(type="ShaderNodeMath")
            multiply.name = _RELIEF_NODE_PREFIX + "Multiply"
            multiply.location = (1000, -450)
            multiply.operation = "MULTIPLY"
            _ = group_tree.links.new(factor_socket, multiply.inputs[0])
            _ = group_tree.links.new(remap_metal.outputs["Result"], multiply.inputs[1])
            factor_socket = multiply.outputs["Value"]
        else:
            factor_socket = remap_metal.outputs["Result"]

    assert factor_socket is not None

    # Duplicate the scalar darkening factor across R/G/B so it can multiply the diffuse color
    # (Mix's Multiply blend mode needs an RGB "B" input, not a scalar).
    combine = group_tree.nodes.new(type="ShaderNodeCombineColor")
    combine.name = _RELIEF_NODE_PREFIX + "Combine"
    combine.location = (1250, -300)
    _ = group_tree.links.new(factor_socket, combine.inputs["Red"])
    _ = group_tree.links.new(factor_socket, combine.inputs["Green"])
    _ = group_tree.links.new(factor_socket, combine.inputs["Blue"])

    mix = cast(bpy.types.ShaderNodeMix, group_tree.nodes.new(type="ShaderNodeMix"))
    mix.name = _RELIEF_NODE_PREFIX + "Mix"
    mix.location = (1500, 0)
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.inputs[0].default_value = 1.0  # Factor
    _ = group_tree.links.new(diffuse_node.outputs["Color"], mix.inputs[6])  # A (RGBA)
    _ = group_tree.links.new(combine.outputs["Color"], mix.inputs[7])  # B (RGBA)
    _ = group_tree.links.new(mix.outputs[2], group_output.inputs["Color"])  # Result (RGBA)
    group_output.location = (1800, 0)  # pushed clear of the composite chain so nothing overlaps


def get_or_create_mapping_node_group(xvm_filename: str, tex_id: "int | str") -> bpy.types.ShaderNodeTree:
    """A tiny node group wrapping just this texture's Mapping node, shared by every material
    variant of this texture (see get_or_create_texture_node_group) so there's exactly one
    Location/Rotation/Scale transform per original texture - matching that there's exactly one
    physical texture to bake it into at export (see bake_material_mapping in xvm.py). Without
    this, each variant would have its own independent Mapping node and editing only one of them
    (the normal case - a user has no reason to know a texture has several variants) would be
    ambiguous or silently ignored at export time. Edit the Mapping by entering this group (same
    workflow as swapping the shared Image).
    """
    group_name = "MappingGroup_{}_{}".format(xvm_filename, tex_id)
    existing = bpy.data.node_groups.get(group_name)
    if existing is not None:
        return cast(bpy.types.ShaderNodeTree, existing)

    group = cast(bpy.types.ShaderNodeTree, bpy.data.node_groups.new(group_name, "ShaderNodeTree"))
    group.interface.new_socket(name="Vector", in_out="INPUT", socket_type="NodeSocketVector")
    group.interface.new_socket(name="Vector", in_out="OUTPUT", socket_type="NodeSocketVector")

    group_input = group.nodes.new(type="NodeGroupInput")
    group_output = group.nodes.new(type="NodeGroupOutput")
    mapping_node = cast(bpy.types.ShaderNodeMapping, group.nodes.new(type="ShaderNodeMapping"))

    group_input.location = (-300, 0)
    mapping_node.location = (0, 0)
    group_output.location = (300, 0)

    _ = group.links.new(group_input.outputs["Vector"], mapping_node.inputs["Vector"])
    _ = group.links.new(mapping_node.outputs["Vector"], group_output.inputs["Vector"])
    return group


def _write_frame_png(pixels: "list[float]", width: int, height: int, path: str):
    """Writes one already-decoded RGBA frame (xvm.read() fully decodes every Xvr's pixel data up
    front, so xj_xvm.xvrs[i].data is ready to use directly - no separate decode step needed) to a
    real numbered file on disk - the exact on-disk shape get_image_sequence_images (xvm.py) later
    re-discovers at export time. Goes through a throwaway Image datablock since Image.save() is
    the only piece of this addon that already knows how to encode decoded pixels to a file."""
    # alpha=True: bpy.data.images.new() defaults to alpha=False (no alpha channel at all), which
    # made every frame PNG save out fully opaque regardless of the source pixels' real alpha -
    # confirmed on real map_acity data (a genuine 0.0/1.0 punch-through mask decoded from the
    # original texture came back as a uniform 1.0 after the import round-trip, and the re-export
    # correspondingly dropped XvrFlags.ALPHA entirely).
    tmp_img = bpy.data.images.new("__pso_blender_tam_frame_tmp", width=width, height=height, alpha=True)
    try:
        tmp_img.pixels = pixels  # pyright: ignore[reportAttributeAccessIssue]
        tmp_img.filepath_raw = path
        tmp_img.file_format = "PNG"
        tmp_img.save()
    finally:
        bpy.data.images.remove(tmp_img)


def animated_texture_cache_root(xvm_filename: str) -> str:
    """Where cached animated-texture frame PNGs for one .xvm live - Blender's standard per-user
    data directory, not next to the source .xvm. That used to be the game's own (often read-only,
    always shared) install folder, which repeatedly collided with concurrent test/import runs
    against the same real data. bpy.app.tempdir is wiped every Blender restart, which would break
    any Image Sequence a *saved* .blend still references - user_resource is the one place that's
    both writable and persists across restarts without polluting the game folder."""
    base = bpy.utils.user_resource("DATAFILES", path="pso_blender_cache", create=True)
    return os.path.join(base, xvm_filename)


def get_or_build_animated_texture_image(xj_xvm: xvm.Xvm, tam_entry: "tam.TamEntry", base_xvr: xvm.Xvr, tex_id: int) -> bpy.types.Image:
    """Reconstructs a real, on-disk numbered-file image sequence for one HAS_TEXTURE_ANIMATION
    tree's animation, and returns it loaded with .source == "SEQUENCE" - the exact state
    xvm.py's TextureManager (export side) already detects via tex.image.source == "SEQUENCE" and
    re-serializes via get_image_sequence_images, with zero export-side changes needed.

    Frames are written in tam_entry.frames order, NOT deduplicated by texture_index: a real .tam
    can revisit an earlier frame's texture_index later (a ping-pong pattern - confirmed on real
    map data, e.g. Ephinea's map_desert03 animation_id 24). Blender's Image Sequence has no
    "replay frame N again" concept, so a repeated texture_index is written out as its own new
    numbered file, physically duplicating that frame's pixel data.

    Per-keyframe frame_delay IS preserved across a round-trip: it's stashed as a custom property
    (see below) on the returned image and read back by tam.write(), since Blender's Image
    datablock has no native per-frame timing slot of its own.
    """
    xvrs_by_index = xj_xvm.xvrs
    content_key = hashlib.md5(str([kf.texture_index for kf in tam_entry.frames]).encode()).hexdigest()[:12]
    # Namespaced by content hash, not just animation_id: get_image_sequence_images does an
    # unfiltered directory listing sorted by numeric filename, so two animations' frames landing
    # in the same directory would silently merge into one bogus sequence - and animation_id
    # numbering isn't guaranteed unique across a shared .xvm's several segment .tam files.
    seq_dir_name = "tam_anim_{}_{}".format(tam_entry.animation_id, content_key)
    cache_root = os.path.join(animated_texture_cache_root(xj_xvm.get_filename()), seq_dir_name)
    first_frame_path = os.path.join(cache_root, "0.png")
    try:
        os.makedirs(cache_root, exist_ok=True)
        # Check every expected frame file, not just frame 0: a cache dir can be left with only
        # SOME frames on disk (e.g. an earlier run that hit a mid-loop OSError, like a transient
        # Windows file lock, and fell through to the static-fallback path below without finishing
        # the write) - checking frame 0 alone would then wrongly treat that partial set as "already
        # fully cached" forever, leaving every missing frame index showing as Blender's pink
        # "missing image data" placeholder once ImageUser.frame_duration expects it to exist.
        if not all(os.path.isfile(os.path.join(cache_root, "{}.png".format(i))) for i in range(len(tam_entry.frames))):
            for i, keyframe in enumerate(tam_entry.frames):
                xvr = xvrs_by_index[keyframe.texture_index]
                _write_frame_png(xvr.data, xvr.width, xvr.height, os.path.join(cache_root, "{}.png".format(i)))
    except OSError as ex:
        # Import's source directory (e.g. an unpacked game-data folder) isn't guaranteed writable,
        # unlike export's user-chosen destination - fall back to a static image for this texture
        # rather than failing the whole import, matching this addon's existing convention for a
        # missing/bad xvm_path/tam_path (warn and continue, never hard-block).
        warn("XJ Warning: Could not write animated texture cache to '{}' ({}) - importing "
             "animation_id {} as a static texture instead.".format(cache_root, ex, tam_entry.animation_id))
        img = bpy.data.images.new("{}_xvr_{}_anim{}".format(xj_xvm.get_filename(), tex_id, tam_entry.animation_id),
                                   width=base_xvr.width, height=base_xvr.height)
        img["pso_orig_tex_id"] = tex_id
        if len(base_xvr.data) > 0:
            img.pixels = base_xvr.data  # pyright: ignore[reportAttributeAccessIssue]
        return img

    img = bpy.data.images.load(first_frame_path, check_existing=True)
    img.source = "SEQUENCE"
    # Blender names a freshly-loaded sequence image after its first file ("0.png") by default -
    # opaque and colliding across animations. Rename to match the static-texture naming convention
    # ("{xvm}_xvr_{tex_id}") plus an animation suffix, so the node group, material, and image all
    # point at the same identifiable texture instead of just the node group.
    img.name = "{}_xvr_{}_anim{}".format(xj_xvm.get_filename(), tex_id, tam_entry.animation_id)
    # Same reasoning as the static path in make_material - lets TextureManager (xvm.py) re-export
    # this animation's frames close to their original relative position instead of an arbitrary
    # alphabetical-by-material-name sort.
    img["pso_orig_tex_id"] = tex_id
    has_alpha = bool(base_xvr.flags & xvm.XvrFlags.ALPHA)
    has_premul_alpha = base_xvr.format in (xvm.XvrFormat.DXT2, xvm.XvrFormat.DXT4)
    img.alpha_mode = "NONE" if not has_alpha else ("PREMUL" if has_premul_alpha else "STRAIGHT")
    # Blender's Image datablock has no native per-frame timing concept, so the real per-keyframe
    # delay (game ticks between this frame and the next) has nowhere else to live - stash it as a
    # custom property on the sequence's base image so tam.write() can read it back on export
    # instead of assuming a uniform 1-tick delay for every frame.
    img["pso_tam_frame_delays"] = [kf.frame_delay for kf in tam_entry.frames]
    return img


def make_material(name: str, material_settings: list[RenderStateArgs], node_id: int, material_id: int, xj_xvm: xvm.Xvm | None, tam_entry: "tam.TamEntry | None" = None) -> Material:
    # First pass: parse settings to find tex_id (needed for material deduplication and naming)
    tex_id = None
    for setting in material_settings:
        if setting.state_type == RenderStateType.TEXTURE_ID:
            tex_id = setting.arg1
            break

    # Two different animations can legitimately share the same base tex_id (confirmed on real
    # map_desert03 data: animation_id 1 and 2 both use tex_id 1, differing only in playback
    # speed) - without folding animation_id into the dedup key below, the second animation's tree
    # would silently reuse the first's already-created material/image, dropping its own frames
    # entirely (material dedup only ever looked at tex_id + render state, with no way to tell two
    # different animations sharing one tex_id apart).
    is_animated_tex_id = (
        tam_entry is not None and tex_id is not None
        and tex_id in {kf.texture_index for kf in tam_entry.frames})
    # Same reasoning applies to the shared texture/mapping node groups (get_or_create_texture_
    # node_group/get_or_create_mapping_node_group below) - those are also keyed by tex_id alone,
    # and would otherwise hand animation_id=2's material back animation_id=1's already-created
    # group (and thus its image), discarding animation_id=2's own frames entirely.
    group_key: "int | str" = "{}_anim{}".format(tex_id, tam_entry.animation_id) if is_animated_tex_id and tam_entry is not None else tex_id

    # Materials are keyed by texture + full render state (blend mode, texture addressing, etc,
    # everything except the texture id itself which is represented separately). The same texture
    # can legitimately appear with different render state in different places on a map (e.g. an
    # additive-blended glow effect vs a normally-blended surface, or different UV wrap modes), so
    # collapsing those into one material would silently pick one look and apply it everywhere.
    # Keying on the full settings signature means identical (texture, render state) pairs still
    # share one material datablock, while genuinely different variants each get their own.
    #
    # "Which material(s) use this texture" is answered separately (see
    # XjSelectMaterialEverywhere in xj_material_properties_menu.py), by comparing the image
    # datablock plugged into each material's texture node - not by material identity/name. That's
    # what lets the user find every occurrence of a texture across the whole map regardless of
    # which render-state variant it's using in each spot.
    if tex_id is not None and xj_xvm is not None:
        settings_signature = sorted(
            (s.state_type, s.arg1, s.arg2)
            for s in material_settings
            if s.state_type != RenderStateType.TEXTURE_ID)
        settings_hash = hashlib.md5(repr(settings_signature).encode()).hexdigest()[:8]
        mat_name = "Mat_{}_{}_{}".format(xj_xvm.get_filename(), tex_id, settings_hash)
        if is_animated_tex_id:
            assert tam_entry is not None
            mat_name += "_anim{}".format(tam_entry.animation_id)
    else:
        mat_name = "{}_node_{}_mat_{}".format(name, node_id, material_id)

    # Check if a material with this exact name already exists (deduplication by name + texture number)
    existing_mat_idx = bpy.data.materials.find(mat_name)
    if existing_mat_idx != -1:
        return bpy.data.materials[existing_mat_idx]

    # Find or create the image from xvr
    img: bpy.types.Image | None = None
    if tex_id is not None and xj_xvm is not None:
        xvr = xj_xvm.xvrs[tex_id]
        # Build a real animated Image Sequence instead of a static single-frame image, so the
        # existing export path (TextureManager detecting image.source=="SEQUENCE") picks it back
        # up automatically on the next export.
        if is_animated_tex_id:
            assert tam_entry is not None
            img = get_or_build_animated_texture_image(xj_xvm, tam_entry, xvr, tex_id)
        else:
            img_name = "{}_xvr_{}".format(xj_xvm.get_filename(), tex_id)
            img_idx = bpy.data.images.find(img_name)
            if img_idx == -1:
                img = bpy.data.images.new(img_name, width=xvr.width, height=xvr.height)
                assert img is not None
                # Stashed so TextureManager (xvm.py) can re-export textures close to their
                # original relative order instead of an arbitrary alphabetical-by-material-name
                # sort, which restructures every texture's position in the .xvm on every export
                # even with zero edits made.
                img["pso_orig_tex_id"] = tex_id
                # Determine alpha mode
                has_alpha = xvr.flags & xvm.XvrFlags.ALPHA
                has_premul_alpha = xvr.format == xvm.XvrFormat.DXT2 or xvr.format == xvm.XvrFormat.DXT4
                if not has_alpha:
                    img.alpha_mode = "NONE"
                elif has_premul_alpha:
                    img.alpha_mode = "PREMUL"
                else:
                    img.alpha_mode = "STRAIGHT"
            else:
                img = bpy.data.images[img_idx]
                assert img is not None
            if len(xvr.data) > 0:
                # Why is the type of Image.pixels just "float"?? It should be list[float] or something. Anyway...
                img.pixels = xvr.data  # pyright: ignore[reportAttributeAccessIssue]

    # No existing material found, create a new one
    mat = bpy.data.materials.new(mat_name)
    mat.use_nodes = True
    if mat.node_tree:
        mat.node_tree.links.clear()
        mat.node_tree.nodes.clear()

    # Parse xj material settings
    xj_settings = cast(MaterialWithXjSettings, mat).xj_settings
    xj_settings.lighting = False
    if tex_id is not None and xj_xvm is not None:
        # generate_mipmaps is NOT set here - it's a property of the shared texture (see
        # get_or_create_texture_node_group), stamped once when that group is actually created,
        # further down once the node tree - and thus a place to share it - exists.
        xj_settings.pso_id = xvr.id
        xj_settings.source_xvm_path = xj_xvm.get_full_path()
    for setting in material_settings:
        t = setting.state_type
        arg1 = setting.arg1
        arg2 = setting.arg2
        if t == RenderStateType.BLEND_MODE:
            xj_settings.src_blend = BlendMode(arg1).name
            xj_settings.dst_blend = BlendMode(arg2).name
        elif t == RenderStateType.TEXTURE_ID:
            tex_id = arg1
        elif t == RenderStateType.TEXTURE_ADDRESSING:
            # Enum doesn't match array in client because indices 0-2 are duplicates
            # so we use this map to translate to the right enum values
            modes = [
                TextureAddressingMode.D3DTADDRESS_CLAMP,
                TextureAddressingMode.D3DTADDRESS_MIRROR,
                TextureAddressingMode.D3DTADDRESS_WRAP,
                TextureAddressingMode.D3DTADDRESS_WRAP,
                TextureAddressingMode.D3DTADDRESS_MIRROR,
                TextureAddressingMode.D3DTADDRESS_CLAMP,
                TextureAddressingMode.D3DTADDRESS_BORDER,
                TextureAddressingMode.D3DTADDRESS_MIRRORONCE]
            xj_settings.tex_addr_u = TextureAddressingMode(modes[arg1]).name
            xj_settings.tex_addr_v = TextureAddressingMode(modes[arg2]).name
        elif t == RenderStateType.MATERIAL:
            xj_settings.material1 = arg1
            xj_settings.material2 = arg2
        elif t == RenderStateType.LIGHTING:
            xj_settings.lighting = bool(arg1)
        elif t == RenderStateType.CAMERA_SPACE_NORMALS:
            xj_settings.camera_space_normals = bool(arg1)
        elif t == RenderStateType.MATERIAL_SOURCE:
            xj_settings.diffuse_color_source = MaterialColorSource(arg1).name

    if mat.node_tree is None:
        raise Exception("XJ error in object '{}': Material has no node tree".format(name))

    # Link texture and vcol as inputs with a mix node
    output_node = cast(bpy.types.ShaderNodeOutputMaterial, mat.node_tree.nodes.new(type="ShaderNodeOutputMaterial"))
    bsdf_node = cast(bpy.types.ShaderNodeBsdfDiffuse, mat.node_tree.nodes.new(type="ShaderNodeBsdfDiffuse"))
    transparency_node = cast(bpy.types.ShaderNodeBsdfTransparent, mat.node_tree.nodes.new(type="ShaderNodeBsdfTransparent"))
    shader_mix_node = cast(bpy.types.ShaderNodeMixShader, mat.node_tree.nodes.new(type="ShaderNodeMixShader"))

    vcol_node = cast(bpy.types.ShaderNodeVertexColor, mat.node_tree.nodes.new(type="ShaderNodeVertexColor"))
    vcol_node.layer_name = "vertex_color"
    alpha_modulate_node = cast(bpy.types.ShaderNodeMath, mat.node_tree.nodes.new(type="ShaderNodeMath"))
    alpha_modulate_node.operation = "MULTIPLY"

    mix_node = cast(bpy.types.ShaderNodeMix, mat.node_tree.nodes.new(type="ShaderNodeMix"))
    mix_node.data_type = "RGBA"
    mix_node.blend_type = "MULTIPLY"
    cast(bpy.types.NodeSocketFloat, mix_node.inputs[0]).default_value = 1.0

    # Texture Coordinate and Mapping nodes for clean UV input chain. mapping_node ends up being
    # either a plain ShaderNodeMapping (no texture - see img is None below, nothing to key a
    # shared group on) or a ShaderNodeGroup wrapping one shared Mapping node per texture (see
    # get_or_create_mapping_node_group) - both expose "Vector" input/output sockets, which is all
    # the wiring below ever needs, so the rest of this function doesn't need to care which one it
    # got.
    tex_coord_node = cast(bpy.types.ShaderNodeTexCoord, mat.node_tree.nodes.new(type="ShaderNodeTexCoord"))
    mapping_node: bpy.types.ShaderNodeMapping | bpy.types.ShaderNodeGroup

    # Blender's image texture node only has a single "Extension" setting shared by both U and V,
    # but PSO materials frequently use a different addressing mode per axis (e.g. wrap on U,
    # clamp on V). To honor both independently, U and V are split out and each run through their
    # own wrap/mirror/clamp math node before being recombined and fed into the texture.
    separate_uv_node: bpy.types.ShaderNodeSeparateXYZ | None = None
    combine_uv_node: bpy.types.ShaderNodeCombineXYZ | None = None
    addr_u_node: bpy.types.ShaderNodeMath | None = None
    addr_v_node: bpy.types.ShaderNodeMath | None = None

    # A second, identical addressing chain placed BEFORE the Mapping node, folding the mesh's
    # raw UV into a single [0, 1) tile before any Mapping deformation is applied. This mirrors
    # what the game itself does (it has no Mapping node - it just repeats one texture tile
    # identically forever) and what xvm.py's export-time bake_material_mapping() already assumes
    # (it only ever evaluates the transform over a single tile). Without this, editing the
    # Mapping node here would preview a smooth deformation that slides differently on every
    # repeat of a tiled surface - a look no exported texture could ever reproduce in-game, since
    # the game always repeats the exact same texture unchanged. With it, what's previewed here
    # matches what a texture-only export is actually capable of producing.
    pre_separate_uv_node: bpy.types.ShaderNodeSeparateXYZ | None = None
    pre_combine_uv_node: bpy.types.ShaderNodeCombineXYZ | None = None
    pre_addr_u_node: bpy.types.ShaderNodeMath | None = None
    pre_addr_v_node: bpy.types.ShaderNodeMath | None = None

    if img is None:
        tex_node = None
        mapping_node = cast(bpy.types.ShaderNodeMapping, mat.node_tree.nodes.new(type="ShaderNodeMapping"))
    else:
        # A Group node referencing a texture-specific shared node group (see
        # get_or_create_texture_node_group), instead of a plain Image Texture node inline here -
        # every material variant of this same texture shares the same group, so there's only
        # ever one place to swap the image when replacing a texture.
        assert tex_id is not None and xj_xvm is not None
        tex_node = cast(bpy.types.ShaderNodeGroup, mat.node_tree.nodes.new(type="ShaderNodeGroup"))
        tex_node.node_tree = get_or_create_texture_node_group(
            xj_xvm.get_filename(), group_key, img, bool(xvr.flags & xvm.XvrFlags.MIPMAPS),
            frame_count=len(tam_entry.frames) if is_animated_tex_id and tam_entry is not None else None)

        mapping_node = cast(bpy.types.ShaderNodeGroup, mat.node_tree.nodes.new(type="ShaderNodeGroup"))
        mapping_node.node_tree = get_or_create_mapping_node_group(xj_xvm.get_filename(), group_key)

        # Post-Mapping fold: brings a coordinate the Mapping transform pushed outside [0, 1) back
        # to a valid position to read from the base image - a property of the image itself (does
        # it tile seamlessly?), not of this variant's own texture addressing, so this always
        # wraps regardless of xj_settings.tex_addr_u/v (see bake_material_mapping in xvm.py,
        # which the export side mirrors exactly the same way).
        separate_uv_node = cast(bpy.types.ShaderNodeSeparateXYZ, mat.node_tree.nodes.new(type="ShaderNodeSeparateXYZ"))
        combine_uv_node = cast(bpy.types.ShaderNodeCombineXYZ, mat.node_tree.nodes.new(type="ShaderNodeCombineXYZ"))
        addr_u_node = make_texture_addressing_node(mat.node_tree, TextureAddressingMode.D3DTADDRESS_WRAP.name)
        addr_v_node = make_texture_addressing_node(mat.node_tree, TextureAddressingMode.D3DTADDRESS_WRAP.name)

        pre_separate_uv_node = cast(bpy.types.ShaderNodeSeparateXYZ, mat.node_tree.nodes.new(type="ShaderNodeSeparateXYZ"))
        pre_combine_uv_node = cast(bpy.types.ShaderNodeCombineXYZ, mat.node_tree.nodes.new(type="ShaderNodeCombineXYZ"))
        pre_addr_u_node = make_texture_addressing_node(mat.node_tree, xj_settings.tex_addr_u)
        pre_addr_v_node = make_texture_addressing_node(mat.node_tree, xj_settings.tex_addr_v)

    # Organize nodes horizontally from left to right with ~300px spacing
    # Texture chain (top row, Y = 300)
    tex_coord_node.location = (-1500, 300)
    if pre_separate_uv_node is not None and pre_combine_uv_node is not None and pre_addr_u_node is not None and pre_addr_v_node is not None:
        pre_separate_uv_node.location = (-1200, 300)
        pre_addr_u_node.location = (-900, 400)
        pre_addr_v_node.location = (-900, 200)
        pre_combine_uv_node.location = (-600, 300)
    # The Mapping group and the Image group are the two nodes someone actually needs to Tab into
    # to edit a texture (transform or swap the image) - placed right next to each other so both
    # are one click away, with the per-variant post-fold addressing chain (not something you'd
    # normally need to open) tucked underneath instead of visually sitting between them.
    mapping_node.location = (-300, 300)
    if tex_node is not None:
        tex_node.location = (0, 300)
    if separate_uv_node is not None and combine_uv_node is not None and addr_u_node is not None and addr_v_node is not None:
        separate_uv_node.location = (-300, 0)
        addr_u_node.location = (-150, 100)
        addr_v_node.location = (-150, -100)
        combine_uv_node.location = (0, 0)
    # Vertex color chain (bottom row, Y = -100)
    vcol_node.location = (900, -100)
    # Mix and alpha modulate (middle column, X = 1200)
    mix_node.location = (1200, 100)
    alpha_modulate_node.location = (1200, -200)
    # BSDF and transparency (X = 1500)
    bsdf_node.location = (1500, 100)
    transparency_node.location = (1500, -200)
    # Shader mix (X = 1800)
    shader_mix_node.location = (1800, 0)
    # Output (X = 2100)
    output_node.location = (2100, 0)

    # Connect UV -> (per-axis wrap, native repeat) -> Mapping -> (per-axis wrap again, in case
    # Mapping pushed the already-folded coordinate back out of [0, 1)) -> Texture
    if pre_separate_uv_node is not None and pre_combine_uv_node is not None and pre_addr_u_node is not None and pre_addr_v_node is not None:
        _ = mat.node_tree.links.new(tex_coord_node.outputs["UV"], pre_separate_uv_node.inputs["Vector"])
        _ = mat.node_tree.links.new(pre_separate_uv_node.outputs["X"], pre_addr_u_node.inputs[0])
        _ = mat.node_tree.links.new(pre_separate_uv_node.outputs["Y"], pre_addr_v_node.inputs[0])
        _ = mat.node_tree.links.new(pre_addr_u_node.outputs[0], pre_combine_uv_node.inputs["X"])
        _ = mat.node_tree.links.new(pre_addr_v_node.outputs[0], pre_combine_uv_node.inputs["Y"])
        _ = mat.node_tree.links.new(pre_separate_uv_node.outputs["Z"], pre_combine_uv_node.inputs["Z"])
        _ = mat.node_tree.links.new(pre_combine_uv_node.outputs["Vector"], mapping_node.inputs["Vector"])
    else:
        _ = mat.node_tree.links.new(tex_coord_node.outputs["UV"], mapping_node.inputs["Vector"])
    if tex_node is not None and separate_uv_node is not None and combine_uv_node is not None and addr_u_node is not None and addr_v_node is not None:
        _ = mat.node_tree.links.new(mapping_node.outputs["Vector"], separate_uv_node.inputs["Vector"])
        _ = mat.node_tree.links.new(separate_uv_node.outputs["X"], addr_u_node.inputs[0])
        _ = mat.node_tree.links.new(separate_uv_node.outputs["Y"], addr_v_node.inputs[0])
        _ = mat.node_tree.links.new(addr_u_node.outputs[0], combine_uv_node.inputs["X"])
        _ = mat.node_tree.links.new(addr_v_node.outputs[0], combine_uv_node.inputs["Y"])
        _ = mat.node_tree.links.new(separate_uv_node.outputs["Z"], combine_uv_node.inputs["Z"])
        _ = mat.node_tree.links.new(combine_uv_node.outputs["Vector"], tex_node.inputs["Vector"])

    _ = mat.node_tree.links.new(shader_mix_node.outputs[0], output_node.inputs[0])
    _ = mat.node_tree.links.new(mix_node.outputs[2], bsdf_node.inputs[0])
    _ = mat.node_tree.links.new(vcol_node.outputs[1], alpha_modulate_node.inputs[0])
    _ = mat.node_tree.links.new(alpha_modulate_node.outputs[0], shader_mix_node.inputs[0])
    if tex_node is None:
        cast(bpy.types.NodeSocketFloat, alpha_modulate_node.inputs[1]).default_value = 1.0
    else:
        _ = mat.node_tree.links.new(tex_node.outputs[0], mix_node.inputs[6])
        _ = mat.node_tree.links.new(tex_node.outputs[1], alpha_modulate_node.inputs[1])
    _ = mat.node_tree.links.new(transparency_node.outputs[0], shader_mix_node.inputs[1])
    _ = mat.node_tree.links.new(bsdf_node.outputs[0], shader_mix_node.inputs[2])
    _ = mat.node_tree.links.new(vcol_node.outputs[0], mix_node.inputs[7])

    return mat


def set_obj_transforms_from_xj_node(obj: bpy.types.Object, node: XjMeshTreeNode):
    """Does not apply, only sets transforms"""
    world_scale = util.get_pso_world_scale()
    if (node.eval_flags & NinjaEvalFlag.UNIT_SCL.value) == 0:
        obj.scale = (node.scale_x, node.scale_z, node.scale_y)
    if (node.eval_flags & NinjaEvalFlag.UNIT_ANG.value) == 0:
        obj.rotation_mode = "XZY"
        obj.rotation_euler = (node.rot_x / 0x7fff * math.pi, node.rot_z / 0x7fff * -math.pi, node.rot_y / 0x7fff * math.pi)
    if (node.eval_flags & NinjaEvalFlag.UNIT_POS.value) == 0:
        obj.location = (node.x / world_scale, -node.z / world_scale, node.y / world_scale)


def xj_node_to_blender_mesh(name: str, node: XjMeshTreeNode, node_id: int, xj_xvm: xvm.Xvm | None, tam_entry: "tam.TamEntry | None" = None) -> bpy.types.Object:
    world_scale = util.get_pso_world_scale()

    # Group index buffers by their vertex buffer
    grouped_alpha_index_buffers: dict[int, list[IndexBufferContainer]] = {}
    mesh = node.mesh.deref()
    for index_buffer in mesh.alpha_index_buffers.deref_array(mesh.alpha_index_buffer_count):
        if index_buffer.vertex_buffer_index in grouped_alpha_index_buffers:
            grouped_alpha_index_buffers[index_buffer.vertex_buffer_index].append(index_buffer)
        else:
            grouped_alpha_index_buffers[index_buffer.vertex_buffer_index] = [index_buffer]

    grouped_opaque_index_buffers: dict[int, list[IndexBufferContainer]] = {}
    for index_buffer in mesh.index_buffers.deref_array(mesh.index_buffer_count):
        if index_buffer.vertex_buffer_index in grouped_opaque_index_buffers:
            grouped_opaque_index_buffers[index_buffer.vertex_buffer_index].append(index_buffer)
        else:
            grouped_opaque_index_buffers[index_buffer.vertex_buffer_index] = [index_buffer]
    
    grouped_index_buffers = list(grouped_alpha_index_buffers.items()) + list(grouped_opaque_index_buffers.items())
    all_index_buffers: list[IndexBufferContainer] = mesh.alpha_index_buffers.deref_array(mesh.alpha_index_buffer_count) + mesh.index_buffers.deref_array(mesh.index_buffer_count)

    # Create one material for each index buffer
    index_buffer_materials: dict[int, tuple[int, Material]] = {}
    # Sometimes a mesh assumes that it shares a material setting with the previously rendered mesh and omits that setting from its own settings.
    # To replicate this we need to create a settings object that is the accumulation of all the previous settings.
    accumulated_material_settings: list[RenderStateArgs] = []
    for i, index_buffer in enumerate(all_index_buffers):
        for setting in index_buffer.renderstate_args.deref_array(index_buffer.renderstate_args_count):
            found = False
            for old_setting in accumulated_material_settings:
                if old_setting.state_type == setting.state_type:
                    old_setting.arg1 = setting.arg1
                    old_setting.arg2 = setting.arg2
                    found = True
                    break
            if not found:
                accumulated_material_settings.append(setting)
        mat = make_material(name, accumulated_material_settings, node_id, i, xj_xvm, tam_entry)
        index_buffer_materials[index_buffer.get_offset()] = (i, mat)

    # Get the attributes of each vertex buffer
    vertex_sets: list[list[tuple[float, float, float]]] = []
    normal_sets: list[list[list[float]]] = []
    uv_sets: list[list[tuple[float, float]]] = []
    color_sets: list[list[tuple[int, int, int, int]]] = []

    has_translucent_flag = False
    for vertex_buffer in mesh.vertex_buffers.deref_array(mesh.vertex_buffer_count):
        vertices: list[tuple[float, float, float]] = []
        colors: list[tuple[int, int, int, int]] = []
        normals: list[list[float]] = []
        uvs: list[tuple[float, float]] = []
        if vertex_buffer.vertex_format & 0x10000:
            has_translucent_flag = True
        vert_ctor = get_vertex_constructor(vertex_buffer.vertex_format)
        vert_ptr = vertex_buffer.vertices.retype(vert_ctor)
        for vertex in vert_ptr.deref_array(vertex_buffer.vertex_count):
            if vertex_has_pos(vertex):
                vertices.append((vertex.x, -vertex.z, vertex.y))
            if vertex_has_color(vertex):
                colors.append((vertex.r, vertex.g, vertex.b, vertex.a))
            if vertex_has_normals(vertex):
                normals.append([vertex.nx, -vertex.nz, vertex.ny])
            if vertex_has_uvs(vertex):
                uvs.append((vertex.u, vertex.v))
        vertex_sets.append(vertices)
        color_sets.append(colors)
        normal_sets.append(normals)
        uv_sets.append(uvs)

    # Create one mesh for each vertex buffer (opaque and alpha separated)
    # Then combine them into one object
    # Index buffers that use the same vertex buffer can be combined into one mesh
    combined_bmesh = bmesh.new()
    for vertex_buffer_index, index_buffers in grouped_index_buffers:
        vertices = vertex_sets[vertex_buffer_index]
        colors = color_sets[vertex_buffer_index]
        normals = normal_sets[vertex_buffer_index]
        uvs = uv_sets[vertex_buffer_index]

        faces: list[tuple[int, int, int]] = []

        for index_buffer in index_buffers:
            indices = index_buffer.indices.deref_array(index_buffer.index_count)
            for i in range(len(indices) - 2):
                # Parsing a triangle strip
                i0 = indices[i + 0]
                i1 = indices[i + 1]
                i2 = indices[i + 2]
                # Ignore degenerate triangles
                if i0 == i1 or i1 == i2 or i2 == i0:
                    continue
                if vertices[i0] == vertices[i1] or vertices[i1] == vertices[i2] or vertices[i2] == vertices[i0]:
                    continue
                if i % 2 == 1:
                    i1, i2 = i2, i1
                faces.append((i0, i1, i2))
        
        # Put geometry into blender object
        mesh_name = "{}_node_{}_vb_{}".format(name, node_id, vertex_buffer_index)
        blender_mesh = bpy.data.meshes.new(mesh_name)
        blender_mesh.from_pydata(vertices, [], faces)
        blender_mesh.update()

        # Assign materials to polys
        # We need to do this before combining the bmesh because the indices will get scrambled.
        # But we can't add the materials to the mesh yet because they are not saved in the bmesh.
        for index_buffer in index_buffers:
            indices = index_buffer.indices.deref_array(index_buffer.index_count)
            # Assume mat slot index will hopefully match when we actually add the material later
            (mat_slot_idx, mat) = index_buffer_materials[index_buffer.get_offset()]
            for poly in blender_mesh.polygons:
                if all(i in indices for i in poly.vertices):
                    poly.material_index = mat_slot_idx

        # Add UVs if any
        if len(uvs) > 0:
            uv_attribute = blender_mesh.uv_layers.new()
            # Convert from per-vertex to per-loop
            for loop in blender_mesh.loops:
                uv_attribute.uv[loop.index].vector[0] = uvs[loop.vertex_index][0]
                uv_attribute.uv[loop.index].vector[1] = uvs[loop.vertex_index][1]

        # Add normals if any
        if len(normals) > 0:
            # This function automatically converts from per-vertex to per-loop
            blender_mesh.normals_split_custom_set_from_vertices(normals)  # pyright: ignore[reportUnknownMemberType]

        # Add vertex colors
        color_attribute = cast(FloatColorAttribute, blender_mesh.color_attributes.new("vertex_color", "FLOAT_COLOR", "POINT"))
        if len(colors) > 0:
            for i in range(len(colors)):
                color_attribute.data[i].color[0] = colors[i][0] / 0xff
                color_attribute.data[i].color[1] = colors[i][1] / 0xff
                color_attribute.data[i].color[2] = colors[i][2] / 0xff
                color_attribute.data[i].color[3] = colors[i][3] / 0xff

        # Combine with other vertex buffers
        combined_bmesh.from_mesh(blender_mesh)

    # Create object
    mesh_name = "{}_node_{}_mesh".format(name, node_id)
    obj_name = "{}_node_{}".format(name, node_id)
    combined_mesh = bpy.data.meshes.new(mesh_name)
    combined_bmesh.to_mesh(combined_mesh)
    util.scale_mesh(combined_mesh, 1.0 / world_scale) # Apply world scale
    if len(combined_mesh.color_attributes) > 0:
        # Color attribute needs to be activated to make it render in viewport
        combined_mesh.color_attributes.active_color_index = 0
    obj = bpy.data.objects.new(obj_name, combined_mesh)
    set_obj_transforms_from_xj_node(obj, node) # Set (not apply) transforms
    rel_settings = cast(ObjectWithRelSettings, obj).rel_settings
    rel_settings.is_translucent = has_translucent_flag

    # Add material and vertex group for each index buffer
    for i, index_buffer in enumerate(all_index_buffers):
        indices = index_buffer.indices.deref_array(index_buffer.index_count)
        vertex_group = obj.vertex_groups.new(name="{}_node_{}_ib_{}".format(name, node_id, i))
        vertex_group.add(indices, 1.0, "ADD")
        (_, mat) = index_buffer_materials[index_buffer.get_offset()]
        cast(bpy.types.Mesh, obj.data).materials.append(mat)
    
    return obj


def xj_to_blender_mesh(name: str, root_node: XjMeshTreeNode, xvm: xvm.Xvm | None, tam_entry: "tam.TamEntry | None" = None) -> bpy.types.Collection:
    collection = bpy.data.collections.new(name)

    node_counter = 0
    # Iterate tree and create a blender object for each node
    # Nodes with meshes are turned into mesh objects and empty nodes into empty objects
    # Hierarchy is maintained with blender's parenting system
    tree_stack: list[tuple[XjMeshTreeNode, bpy.types.Object | None]] = [(root_node, None)] # (current_node, current_parent_object)
    while len(tree_stack) > 0:
        (node, parent_object) = tree_stack.pop()

        if node.mesh == NULLPTR:
            # Create empty object
            obj = bpy.data.objects.new("{}_node_{}".format(name, node_counter), None)
            obj.empty_display_type = "SPHERE"
            obj.empty_display_size = 0.01
            set_obj_transforms_from_xj_node(obj, node)
        else:
            obj = xj_node_to_blender_mesh(name, node, node_counter, xvm, tam_entry)
            obj["mesh_offset"] = hex(node.mesh.get_offset())
        
        cast(ObjectWithNjcmSettings, obj).njcm_settings.eval_flags = set(NinjaEvalFlag(x).name for x in util.get_set_bits(node.eval_flags))
        obj["node_offset"] = hex(node.get_offset())

        collection.objects.link(obj)

        if parent_object is not None:
            obj.parent = parent_object

        node_counter += 1

        # Iterate children and siblings
        if node.child != NULLPTR:
            tree_stack.append((node.child.deref(), obj))
        if node.next != NULLPTR:
            tree_stack.append((node.next.deref(), parent_object))

    return collection


def make_mesh_tree(njcm_chunk: IffChunk, siblings: list[bpy.types.Object], texture_man: xvm.TextureManager):
    world_scale = util.get_pso_world_scale()
    first_node_ptr = None
    prev_node_link_offset = None

    for i in range(len(siblings)):
        obj = siblings[i]

        has_mesh = obj.data is not None
        has_next = i < len(siblings) - 1
        has_children = len(obj.children) > 0

        # Transform eval flags from blender format into actual bitfield
        eval_flags = 0
        for flag_name in cast(set[str], cast(ObjectWithNjcmSettings, obj).njcm_settings.eval_flags):
            eval_flags |= getattr(NinjaEvalFlag, flag_name).value

        mesh_node = XjMeshTreeNode(
            eval_flags=eval_flags,
            scale_x=1.0,
            scale_y=1.0,
            scale_z=1.0)
        
        if not has_mesh:
            mesh_node.x = obj.location[0] * world_scale
            mesh_node.y = obj.location[2] * world_scale
            mesh_node.z = -obj.location[1] * world_scale

        # Pointers will be overwritten later but we need to mark them as non-null or they won't be saved in the POF0 table
        if has_mesh:
            mesh_node.mesh = Ptr32(0xdeadbeef)
        if has_next:
            mesh_node.next = Ptr32(0xdeadbeef)
        if has_children:
            mesh_node.child = Ptr32(0xdeadbeef)

        # Write node first, mainly just because the root 
        node_ptr = njcm_chunk.write(mesh_node)

        if first_node_ptr is None:
            first_node_ptr = node_ptr

        if has_mesh:
            # Create XJ mesh - evaluated through the dependency graph, not obj.to_mesh() directly
            # on the original object, which silently ignores any live/unapplied modifier
            # (Decimate, Mirror, Subsurf, ...) and exports the raw base mesh instead.
            depsgraph = bpy.context.evaluated_depsgraph_get()
            eval_obj = obj.evaluated_get(depsgraph)
            blender_mesh = eval_obj.to_mesh()
            util.scale_mesh(blender_mesh, util.get_pso_world_scale())
            mesh = make_mesh(njcm_chunk, obj, blender_mesh, texture_man)

            # Write mesh into chunk and mesh pointer into node
            mesh_pointer_offset = IffHeader.type_size() + node_ptr + MeshTreeNode.offset_of("mesh")
            mesh_ptr = njcm_chunk.write(mesh)
            pack_into(Numeric.endianness_prefix + "L", njcm_chunk.buf.buffer, mesh_pointer_offset, mesh_ptr)
        
        # Link previous node to this one
        if prev_node_link_offset is not None:
            pack_into(Numeric.endianness_prefix + "L", njcm_chunk.buf.buffer, prev_node_link_offset, node_ptr)
        prev_node_link_offset = IffHeader.type_size() + node_ptr + MeshTreeNode.offset_of("next")

        if has_children:
            # Write children into chunk and child pointer into node
            child_pointer_offset = IffHeader.type_size() + node_ptr + MeshTreeNode.offset_of("child")
            child_ptr = make_mesh_tree(njcm_chunk, list(obj.children), texture_man)
            pack_into(Numeric.endianness_prefix + "L", njcm_chunk.buf.buffer, child_pointer_offset, child_ptr)

    return first_node_ptr


def make_xj(root_objs: list[bpy.types.Object], texture_man: xvm.TextureManager) -> bytearray:
    textures = texture_man.get_all_textures()
    xj_buf = bytearray()

    # Apparently client wants NJTL to come first
    if len(textures) > 0:
        # Make NJTL chunk (doesn't contain pixel data)
        njtl_chunk = IffChunk("NJTL")
        texlist = TextureList(
            elements=Ptr32(0xdeadbeef),
            count=len(textures))
        texlist_elements_offset = njtl_chunk.write(texlist) + IffHeader.type_size()

        first_texlist_entry_ptr = NULLPTR
        for texture in textures:
            tex_name = texture.image.name[0:31]
            name_ptr = njtl_chunk.write(AlignedString(tex_name, IffChunk.ALIGNMENT))
            ptr = njtl_chunk.write(TextureListEntry(name=Ptr32(name_ptr)))
            if first_texlist_entry_ptr == NULLPTR:
                first_texlist_entry_ptr = ptr
        # Rewrite pointer
        pack_into(Numeric.endianness_prefix + "L", njtl_chunk.buf.buffer, texlist_elements_offset, first_texlist_entry_ptr)

        # Append NJTL
        xj_buf += njtl_chunk.finish()

    for obj in root_objs:
        # Write root nodes as one chunk each
        njcm_chunk = IffChunk("NJCM")
        _ = make_mesh_tree(njcm_chunk, [obj], texture_man)
        xj_buf += njcm_chunk.finish()

    return xj_buf

def write(xj_path: str, xvm_path: str, root_objs: list[bpy.types.Object]):
    all_objs = root_objs.copy()
    for obj in root_objs:
        all_objs += obj.children_recursive
    texture_man = xvm.TextureManager(all_objs)
    try:
        xj_buf = make_xj(root_objs, texture_man)

        with open(xj_path, "wb") as f:
            _ = f.write(xj_buf)

        if xvm_path and texture_man.has_textures():
            xvm.write(xvm_path, texture_man.get_all_textures())
    finally:
        texture_man.cleanup_ephemeral_images()

def read(xj_path: str, xj_xvm: xvm.Xvm | None) -> list[Collection]:
    filename = os.path.basename(xj_path)
    collections: list[Collection] = []
    chunk_header_size = IffHeader.type_size()

    with open(xj_path, "rb") as f:
        file_contents = bytearray(f.read())

    # Read iff chunks
    chunk_offset = 0
    prev_chunk_offset = None
    need_pof0 = False
    while chunk_offset < len(file_contents):
        (chunk_header, _) = IffHeader.deserialize_from(file_contents, offset=chunk_offset)
        chunk_type = util.bytes_to_string(chunk_header.type_name)
        if chunk_type == "NJCM":
            need_pof0 = True
        elif chunk_type == "POF0":
            if prev_chunk_offset is None:
                raise Exception("XJ error in file '{}': Encountered POF0 but there was no previous chunk".format(filename))
            if need_pof0:
                # Could maybe check if pointers to 0 are valid?
                _ = parse_pof0(filename, file_contents, prev_chunk_offset, chunk_offset, chunk_header.body_size)
                # Read a NJCM
                (root_node, _) = XjMeshTreeNode.deserialize_from(file_contents[prev_chunk_offset + chunk_header_size:], 0)
                models = xj_to_blender_mesh(filename, root_node, xj_xvm)
                collections.append(models)
                need_pof0 = False
        prev_chunk_offset = chunk_offset
        chunk_offset += chunk_header.body_size + chunk_header_size
    return collections
