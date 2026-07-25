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
from . import tristrip, util, xvm
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
            # Exclude translation from transform
            local_vert = blender_mesh.vertices[vert_idx]
            world_vert = obj.matrix_world @ local_vert.co
            world_vert = local_vert.co.to_4d()
            world_vert.w = 0
            world_vert = util.from_blender_axes((obj.matrix_world @ world_vert).to_3d())
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
                # BGRA
                # Need to clamp because light baking can cause values to go higher than normal
                vertex_attributes.b = int(util.clamp(col[0], 0.0, 1.0) * 0xff)
                vertex_attributes.g = int(util.clamp(col[1], 0.0, 1.0) * 0xff)
                vertex_attributes.r = int(util.clamp(col[2], 0.0, 1.0) * 0xff)
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
                normal = util.from_blender_axes((obj.matrix_world @ normal).to_3d().normalized())
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
    renderstate_args: list[RenderStateArgs]
    strips: list[list[int]]

    def __init__(self, material_index: int, material: bpy.types.Material | None, strips: list[list[int]]):
        self.material_index = material_index
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
            if len(textures) > 0:
                tex = textures[material_strip_data.material_index]
                has_alpha = has_alpha or tex.has_alpha
                rs_args += make_renderstate_args(
                    # XXX: Assumes material index matches index of texture in this array
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


def make_material(name: str, material_settings: list[RenderStateArgs], node_id: int, material_id: int, xj_xvm: xvm.Xvm | None) -> Material:
    # First pass: parse settings to find tex_id (needed for material deduplication and naming)
    tex_id = None
    for setting in material_settings:
        if setting.state_type == RenderStateType.TEXTURE_ID:
            tex_id = setting.arg1
            break

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
        img_name = "{}_xvr_{}".format(xj_xvm.get_filename(), tex_id)
        img_idx = bpy.data.images.find(img_name)
        if img_idx == -1:
            img = bpy.data.images.new(img_name, width=xvr.width, height=xvr.height)
            assert img is not None
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

    # Texture Coordinate and Mapping nodes for clean UV input chain
    tex_coord_node = cast(bpy.types.ShaderNodeTexCoord, mat.node_tree.nodes.new(type="ShaderNodeTexCoord"))
    mapping_node = cast(bpy.types.ShaderNodeMapping, mat.node_tree.nodes.new(type="ShaderNodeMapping"))

    # Blender's image texture node only has a single "Extension" setting shared by both U and V,
    # but PSO materials frequently use a different addressing mode per axis (e.g. wrap on U,
    # clamp on V). To honor both independently, U and V are split out and each run through their
    # own wrap/mirror/clamp math node before being recombined and fed into the texture.
    separate_uv_node: bpy.types.ShaderNodeSeparateXYZ | None = None
    combine_uv_node: bpy.types.ShaderNodeCombineXYZ | None = None
    addr_u_node: bpy.types.ShaderNodeMath | None = None
    addr_v_node: bpy.types.ShaderNodeMath | None = None

    if img is None:
        tex_node = None
    else:
        tex_node = cast(bpy.types.ShaderNodeTexImage, mat.node_tree.nodes.new(type="ShaderNodeTexImage"))
        tex_node.image = img
        # The math nodes below pre-wrap U/V into [0, 1], so the image node's own extension is
        # just a safe fallback for floating point edge cases right at the boundary.
        tex_node.extension = "EXTEND"

        separate_uv_node = cast(bpy.types.ShaderNodeSeparateXYZ, mat.node_tree.nodes.new(type="ShaderNodeSeparateXYZ"))
        combine_uv_node = cast(bpy.types.ShaderNodeCombineXYZ, mat.node_tree.nodes.new(type="ShaderNodeCombineXYZ"))
        addr_u_node = make_texture_addressing_node(mat.node_tree, xj_settings.tex_addr_u)
        addr_v_node = make_texture_addressing_node(mat.node_tree, xj_settings.tex_addr_v)

    # Organize nodes horizontally from left to right with ~300px spacing
    # Texture chain (top row, Y = 300)
    tex_coord_node.location = (-1500, 300)
    mapping_node.location = (-1200, 300)
    if separate_uv_node is not None and combine_uv_node is not None and addr_u_node is not None and addr_v_node is not None:
        separate_uv_node.location = (-900, 300)
        addr_u_node.location = (-600, 400)
        addr_v_node.location = (-600, 200)
        combine_uv_node.location = (-300, 300)
    if tex_node is not None:
        tex_node.location = (0, 300)
    # Vertex color chain (bottom row, Y = -100)
    vcol_node.location = (0, -100)
    # Mix and alpha modulate (middle column, X = 300)
    mix_node.location = (300, 100)
    alpha_modulate_node.location = (300, -200)
    # BSDF and transparency (X = 600)
    bsdf_node.location = (600, 100)
    transparency_node.location = (600, -200)
    # Shader mix (X = 900)
    shader_mix_node.location = (900, 0)
    # Output (X = 1200)
    output_node.location = (1200, 0)

    # Connect UV -> Mapping -> (per-axis wrap) -> Texture
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


def xj_node_to_blender_mesh(name: str, node: XjMeshTreeNode, node_id: int, xj_xvm: xvm.Xvm | None) -> bpy.types.Object:
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
        mat = make_material(name, accumulated_material_settings, node_id, i, xj_xvm)
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


def xj_to_blender_mesh(name: str, root_node: XjMeshTreeNode, xvm: xvm.Xvm | None) -> bpy.types.Collection:
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
            obj = xj_node_to_blender_mesh(name, node, node_counter, xvm)
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
            # Create XJ mesh
            blender_mesh = obj.to_mesh()
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
    xj_buf = make_xj(root_objs, texture_man)

    with open(xj_path, "wb") as f:
        _ = f.write(xj_buf)

    if xvm_path and texture_man.has_textures():
        xvm.write(xvm_path, texture_man.get_all_textures())

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
