import bpy, os, warnings, bmesh
from dataclasses import dataclass, field
from .serialization import Serializable, Numeric, AlignedString
from struct import unpack_from, pack_into
from .njcm import MeshTreeNode
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
Ptr32 = Numeric.Ptr32
NULLPTR = Numeric.NULLPTR


def vertex_has_color(fmt: int) -> bool:
    fmt = fmt & 0xffff
    return fmt == 4 or fmt == 5 or fmt == 6 or fmt == 7

def vertex_has_normals(fmt: int) -> bool:
    fmt = fmt & 0xffff
    return fmt == 2 or fmt == 3 or fmt == 6 or fmt == 7

def vertex_has_uvs(fmt: int) -> bool:
    fmt = fmt & 0xffff
    return fmt == 1 or fmt == 3 or fmt == 5 or fmt == 7

@dataclass
class VertexFormat1(Serializable):
    x: F32 = 0.0
    y: F32 = 0.0
    z: F32 = 0.0
    u: F32 = 0.0
    v: F32 = 0.0


@dataclass
class VertexFormat2(Serializable):
    x: F32 = 0.0
    y: F32 = 0.0
    z: F32 = 0.0
    nx: F32 = 0.0
    ny: F32 = 0.0
    nz: F32 = 0.0


@dataclass
class VertexFormat3(Serializable):
    x: F32 = 0.0
    y: F32 = 0.0
    z: F32 = 0.0
    nx: F32 = 0.0
    ny: F32 = 0.0
    nz: F32 = 0.0
    u: F32 = 0.0
    v: F32 = 0.0


@dataclass
class VertexFormat4(Serializable):
    x: F32 = 0.0
    y: F32 = 0.0
    z: F32 = 0.0
    # Purple default
    r: U8 = 0xff
    g: U8 = 0
    b: U8 = 0xff
    a: U8 = 0xff


@dataclass
class VertexFormat5(Serializable):
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
class VertexFormat6(Serializable):
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
class VertexFormat7(Serializable):
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
class VertexBufferFormat1(Serializable):
    vertices: list[VertexFormat1] = field(default_factory=list)

@dataclass
class VertexBufferFormat2(Serializable):
    vertices: list[VertexFormat2] = field(default_factory=list)


@dataclass
class VertexBufferFormat3(Serializable):
    vertices: list[VertexFormat3] = field(default_factory=list)


@dataclass
class VertexBufferFormat4(Serializable):
    vertices: list[VertexFormat4] = field(default_factory=list)


@dataclass
class VertexBufferFormat5(Serializable):
    vertices: list[VertexFormat5] = field(default_factory=list)


@dataclass
class VertexBufferFormat6(Serializable):
    vertices: list[VertexFormat6] = field(default_factory=list)


@dataclass
class VertexBufferFormat7(Serializable):
    vertices: list[VertexFormat7] = field(default_factory=list)


@dataclass
class IndexBuffer(Serializable):
    indices: list[U16] = field(default_factory=list)


@dataclass
class VertexBufferContainer(Serializable):
    vertex_format: U32 = 0
    vertex_buffer: Ptr32 = NULLPTR # VertexBuffer
    vertex_size: U32 = 0
    vertex_count: U32 = 0

    @classmethod
    def deserialize_from(cls, buf, offset):
        (container, after) = super(VertexBufferContainer, cls).deserialize_from(buf, offset)
        vertex_format = container.vertex_format & 0xffff
        if vertex_format == 1:
            vert_ctor = VertexFormat1
        elif vertex_format == 2:
            vert_ctor = VertexFormat2
        elif vertex_format == 3:
            vert_ctor = VertexFormat3
        elif vertex_format == 4:
            vert_ctor = VertexFormat4
        elif vertex_format == 5:
            vert_ctor = VertexFormat5
        elif vertex_format == 6:
            vert_ctor = VertexFormat6
        elif vertex_format == 7:
            vert_ctor = VertexFormat7
        else:
            raise Exception("Unimplemented vertex format {}".format(container.vertex_format))
        container.vertex_buffer = vert_ctor.read_sequence(buf, container.vertex_buffer, container.vertex_count)
        return (container, after)


@dataclass
class BlendMode:
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


@dataclass
class TextureAddressingMode:
    D3DTADDRESS_WRAP = 3
    D3DTADDRESS_MIRROR = 4
    D3DTADDRESS_CLAMP = 5
    D3DTADDRESS_BORDER = 6
    D3DTADDRESS_MIRRORONCE = 7


@dataclass
class MaterialColorSource:
    D3DMCS_MATERIAL = 0
    D3DMCS_COLOR1 = 1
    D3DMCS_COLOR2 = 3


@dataclass
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


@dataclass
class IndexBufferContainer(Serializable):
    renderstate_args: Ptr32 = NULLPTR # RenderStateArgs
    renderstate_args_count: U32 = 0
    index_buffer: Ptr32 = NULLPTR # IndexBuffer
    index_count: U32 = 0
    vertex_buffer_index: U32 = 0

    @classmethod
    def deserialize_from(cls, buf, offset):
        (container, after) = super(IndexBufferContainer, cls).deserialize_from(buf, offset)
        container.renderstate_args = RenderStateArgs.read_sequence(buf, container.renderstate_args, container.renderstate_args_count)
        [endian, typecode] = Numeric.format_of_type(U16)
        fmt = endian + str(container.index_count) + typecode
        container.index_buffer = unpack_from(fmt, buf, offset=container.index_buffer)
        return (container, after)


@dataclass
class Mesh(Serializable):
    flags: U32 = 0
    vertex_buffers: Ptr32 = NULLPTR # VertexBufferContainer
    vertex_buffer_count: U32 = 0
    index_buffers: Ptr32 = NULLPTR # IndexBufferContainer
    index_buffer_count: U32 = 0
    alpha_index_buffers: Ptr32 = NULLPTR # IndexBufferContainer
    alpha_index_buffer_count: U32 = 0

    @classmethod
    def deserialize_from(cls, buf, offset):
        (mesh, after) = super(Mesh, cls).deserialize_from(buf, offset=offset)
        mesh.vertex_buffers = VertexBufferContainer.read_sequence(buf, mesh.vertex_buffers, mesh.vertex_buffer_count)
        mesh.index_buffers = IndexBufferContainer.read_sequence(buf, mesh.index_buffers, mesh.index_buffer_count)
        mesh.alpha_index_buffers = IndexBufferContainer.read_sequence(buf, mesh.alpha_index_buffers, mesh.alpha_index_buffer_count)
        return (mesh, after)


class NinjaEvalFlag:
    UNIT_POS = 0b1 # Ignore translation
    UNIT_ANG = 0b10 # Ignore rotation
    UNIT_SCL = 0b100 # Ignore scaling
    HIDE = 0b1000 # Do not draw model
    BREAK = 0b10000 # Terimnate tracing children
    ZXY_ANG = 0b100000
    SKIP = 0b1000000
    SHAPE_SKIP = 0b10000000
    CLIP = 0b100000000
    MODIFIER = 0b1000000000


@dataclass
class NormalType:
    Vertex = 1
    Face = 2


def determine_vertex_format(has_textures: bool, has_vertex_colors: bool, use_normals: bool):
    # Figure out the right vertex format based on what data mesh has.
    if has_textures:
        if has_vertex_colors:
            if use_normals:
                # Coords + Normals + color + UVs
                vertex_format = 7
                vertex_size = VertexFormat7.type_size()
                vertex_buffer = VertexBufferFormat7()
                vertex_ctor = VertexFormat7
            else:
                # Coords + color + UVs
                vertex_format = 5
                vertex_size = VertexFormat5.type_size()
                vertex_buffer = VertexBufferFormat5()
                vertex_ctor = VertexFormat5
        else:
            if use_normals:
                # Coords + normals + UVs
                vertex_format = 3
                vertex_size = VertexFormat3.type_size()
                vertex_buffer = VertexBufferFormat3()
                vertex_ctor = VertexFormat3
            else:
                # Coords + UVs
                vertex_format = 1
                vertex_size = VertexFormat1.type_size()
                vertex_buffer = VertexBufferFormat1()
                vertex_ctor = VertexFormat1
    else:
        if use_normals:
            if has_vertex_colors:
                # Coords + color + normals
                vertex_format = 6
                vertex_size = VertexFormat6.type_size()
                vertex_buffer = VertexBufferFormat6()
                vertex_ctor = VertexFormat6
            else:
                # Coords + normals
                vertex_format = 2
                vertex_size = VertexFormat2.type_size()
                vertex_buffer = VertexBufferFormat2()
                vertex_ctor = VertexFormat2
        else:
            # Coords + color
            vertex_format = 4
            vertex_size = VertexFormat4.type_size()
            vertex_buffer = VertexBufferFormat4()
            vertex_ctor = VertexFormat4
    return (vertex_format, vertex_size, vertex_buffer, vertex_ctor)


def write_vertex_buffer(destination: util.AbstractFileArchive, obj: bpy.types.Object, blender_mesh: bpy.types.Mesh, xj_mesh: Mesh, has_textures: bool, vertex_colors, normal_type):
    use_normals = normal_type is not None

    # One vertex per loop
    # TODO: Should only use per-loop vertices when necessary
    (vertex_format, vertex_size, vertex_buffer, vertex_ctor) = determine_vertex_format(has_textures, bool(vertex_colors), use_normals)

    if obj.rel_settings.is_translucent:
        vertex_format |= 0x10000

    vertex_buffer.vertices = [None] * len(blender_mesh.loops)
    for face in blender_mesh.loop_triangles:
        for (vert_idx, loop_idx) in zip(face.vertices, face.loops):
            # Exclude translation from transform
            local_vert = blender_mesh.vertices[vert_idx]
            world_vert = obj.matrix_world @ local_vert.co
            world_vert = local_vert.co.to_4d()
            world_vert.w = 0
            world_vert = util.from_blender_axes((obj.matrix_world @ world_vert).to_3d())
            vertex = vertex_ctor(
                x=world_vert[0],
                y=world_vert[1],
                z=world_vert[2])
            vertex_buffer.vertices[loop_idx] = vertex
            # Get UVs
            if has_textures:
                u, v = blender_mesh.uv_layers[0].data[loop_idx].uv
                vertex.u = u
                vertex.v = 1.0 - v
            # Get colors
            if vertex_colors:
                if vertex_colors.domain == "POINT":
                    col = vertex_colors.data[vert_idx].color
                elif vertex_colors.domain == "CORNER":
                    col = vertex_colors.data[loop_idx].color
                # BGRA
                # Need to clamp because light baking can cause values to go higher than normal
                vertex.b = int(util.clamp(col[0], 0.0, 1.0) * 0xff)
                vertex.g = int(util.clamp(col[1], 0.0, 1.0) * 0xff)
                vertex.r = int(util.clamp(col[2], 0.0, 1.0) * 0xff)
                vertex.a = int(util.clamp(col[3], 0.0, 1.0) * 0xff)
            if use_normals:
                # Vertex or face normal
                normal = local_vert.normal if normal_type == NormalType.Vertex else face.normal
                normal = normal.to_4d()
                normal.w = 0
                normal = util.from_blender_axes((obj.matrix_world @ normal).to_3d().normalized())
                vertex.nx = normal[0]
                vertex.ny = normal[1]
                vertex.nz = normal[2]

    # Put all vertices in one buffer
    xj_mesh.vertex_buffer_count = 1
    xj_mesh.vertex_buffers = destination.write(VertexBufferContainer(
        vertex_format=vertex_format,
        vertex_buffer=destination.write(vertex_buffer),
        vertex_size=vertex_size,
        vertex_count=len(vertex_buffer.vertices)))


class MaterialStrips:
    def __init__(self, material_index: int, material: bpy.types.Material, strips: list[list[int]]):
        self.material_index = material_index
        if material:
            self.renderstate_args = make_renderstate_args(
                blend_modes=(material.xj_settings.src_blend, material.xj_settings.dst_blend),
                texture_addressing=(material.xj_settings.tex_addr_u, material.xj_settings.tex_addr_v),
                lighting=material.xj_settings.lighting,
                material=(material.xj_settings.material1, material.xj_settings.material2),
                camera_space_normals=material.xj_settings.camera_space_normals,
                diffuse_color_source=material.xj_settings.diffuse_color_source)
        else:
            # Empty slot
            self.renderstate_args = []
        self.strips = strips


def create_tristrips_grouped_by_material(obj: bpy.types.Object, blender_mesh: bpy.types.Mesh, texture_man: xvm.TextureManager) -> list[MaterialStrips]:
    material_strips = []
    if texture_man.object_has_textures(obj):
        material_faces = []
        # Get all faces grouped by their material, then stripify them
        for (mat_idx, mat_slot) in enumerate(obj.material_slots):
            material_faces.append([])
            material_strips.append(MaterialStrips(mat_idx, mat_slot.material, []))
        for face in blender_mesh.loop_triangles:
            material_faces[face.material_index].append(tuple(face.loops))
        for mat_idx in range(len(obj.material_slots)):
            if len(material_faces[mat_idx]) > 0:
                strips = tristrip.stripify(material_faces[mat_idx], stitchstrips=True)
                material_strips[mat_idx].strips = strips
    else:
        faces = []
        for face in blender_mesh.loop_triangles:
            faces.append(tuple(face.loops))
        strips = tristrip.stripify(faces, stitchstrips=True)
        material_strips.append(MaterialStrips(-1, None, strips))
    return material_strips


def write_index_buffers(destination: util.AbstractFileArchive, obj: bpy.types.Object, blender_mesh: bpy.types.Mesh, xj_mesh: Mesh, texture_man: xvm.TextureManager, has_vertex_alpha: bool):
    # Texture IDs must be 0-based for the render settings
    # One buffer per strip
    material_strips = create_tristrips_grouped_by_material(obj, blender_mesh, texture_man)
    opaque_index_buffer_containers = []
    alpha_index_buffer_containers = []
    textures = texture_man.get_object_textures(obj)
    texture_id_base = texture_man.get_base_id()
    for material_strip_data in material_strips:
        for strip in material_strip_data.strips:
            # Strips can be empty due to unused material slots, skip them
            if len(strip) < 1:
                continue
            has_alpha = obj.rel_settings.is_translucent or has_vertex_alpha
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
            containers = alpha_index_buffer_containers if has_alpha else opaque_index_buffer_containers
            containers.append(IndexBufferContainer(
                index_buffer=buf_ptr,
                index_count=len(strip),
                renderstate_args=first_rs_arg_ptr,
                renderstate_args_count=rs_arg_count))
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
    xj_mesh.alpha_index_buffers = first_alpha_index_buffer_container_ptr
    xj_mesh.index_buffer_count = len(opaque_index_buffer_containers)
    xj_mesh.index_buffers = first_opaque_index_buffer_container_ptr


def make_mesh(destination: util.AbstractFileArchive, obj: bpy.types.Object, blender_mesh: bpy.types.Mesh, texture_man: xvm.TextureManager) -> Mesh:
    if texture_man.object_has_textures(obj) and len(blender_mesh.uv_layers) < 1:
        raise Exception("XJ error in object '{}': Object has texture but is missing UVs".format(obj.name))

    mesh = Mesh()

    normal_type = None
    for mat_slot in obj.material_slots:
        if not mat_slot.material:
            # Empty slot
            continue
        # Lighting requires normals
        if mat_slot.material.xj_settings.lighting or mat_slot.material.xj_settings.camera_space_normals:
            if not mat_slot.material.xj_settings.normal_type:
                mat_slot.material.xj_settings.normal_type = str(NormalType.Vertex)
            # XXX: Camera projection setting is applied to entire mesh instead of material vertex group
            normal_type = int(mat_slot.material.xj_settings.normal_type)
            break
    
    if len(blender_mesh.color_attributes) > 0:
        vertex_colors = blender_mesh.color_attributes[0]
    elif normal_type is not None:
        # Effects that need normals usually also need vcol. Let's add a blank white color attribute.
        vertex_colors = blender_mesh.color_attributes.new("xj_default_vcol", "FLOAT_COLOR", "CORNER")
        for attr in vertex_colors.data:
            attr.color[0] = 1
            attr.color[1] = 1
            attr.color[2] = 1
    else:
        vertex_colors = None
    has_vertex_color = bool(vertex_colors)
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
    write_vertex_buffer(destination, obj, blender_mesh, mesh, texture_man.object_has_textures(obj), vertex_colors, normal_type)
    write_index_buffers(destination, obj, blender_mesh, mesh, texture_man, has_vertex_alpha)
    return mesh


def make_renderstate_args(
    *args,
    texture_id=None,
    texture_addressing=None,
    blend_modes=None,
    lighting=None,
    material=None,
    camera_space_normals=None,
    diffuse_color_source=None
) -> list[RenderStateArgs]:
    rs_args = []
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


def xj_node_to_blender_mesh(node: MeshTreeNode, node_id: int, materials: list[bpy.types.Material]) -> bpy.types.Object:
    world_scale = util.get_pso_world_scale()

    # Group index buffers by their vertex buffer
    grouped_alpha_index_buffers = {}
    for index_buffer in node.mesh.alpha_index_buffers:
        if index_buffer.vertex_buffer_index in grouped_alpha_index_buffers:
            grouped_alpha_index_buffers[index_buffer.vertex_buffer_index].append(index_buffer)
        else:
            grouped_alpha_index_buffers[index_buffer.vertex_buffer_index] = [index_buffer]

    grouped_opaque_index_buffers = {}
    for index_buffer in node.mesh.index_buffers:
        if index_buffer.vertex_buffer_index in grouped_opaque_index_buffers:
            grouped_opaque_index_buffers[index_buffer.vertex_buffer_index].append(index_buffer)
        else:
            grouped_opaque_index_buffers[index_buffer.vertex_buffer_index] = [index_buffer]
    
    grouped_index_buffers = list(grouped_alpha_index_buffers.items()) + list(grouped_opaque_index_buffers.items())
    all_index_buffers = node.mesh.alpha_index_buffers + node.mesh.index_buffers

    # Get the attributes of each vertex buffer
    vertex_sets = []
    normal_sets = []
    uv_sets = []
    color_sets = []

    for vertex_buffer in node.mesh.vertex_buffers:
        vertices = []
        colors = []
        normals = []
        uvs = []
        has_color = vertex_has_color(vertex_buffer.vertex_format)
        has_normals = vertex_has_normals(vertex_buffer.vertex_format)
        has_uvs = vertex_has_uvs(vertex_buffer.vertex_format)
        for vertex in vertex_buffer.vertex_buffer:
            vertices.append((vertex.x, -vertex.z, vertex.y))
            if has_color:
                colors.append((vertex.r, vertex.g, vertex.b))
            if has_normals:
                normals.append((vertex.nx, -vertex.nz, vertex.ny))
            if has_uvs:
                uvs.append((vertex.u, 1.0 - vertex.v))
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

        faces = []

        for index_buffer in index_buffers:
            indices = index_buffer.index_buffer
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
        mesh_name = "node_{}_vb_{}".format(node_id, vertex_buffer_index)
        blender_mesh = bpy.data.meshes.new(mesh_name)
        blender_mesh.from_pydata(vertices, [], faces)
        blender_mesh.update()

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
            blender_mesh.normals_split_custom_set_from_vertices(normals)

        # Add vertex colors
        color_attribute = blender_mesh.color_attributes.new("vertex_color", "FLOAT_COLOR", "POINT")
        if len(colors) > 0:
            for i in range(len(colors)):
                color_attribute.data[i].color[0] = colors[i][0] / 0xff
                color_attribute.data[i].color[1] = colors[i][1] / 0xff
                color_attribute.data[i].color[2] = colors[i][2] / 0xff

        # Combine with other vertex buffers
        combined_bmesh.from_mesh(blender_mesh)

    # Create object
    mesh_name = "node_{}_mesh".format(node_id)
    obj_name = "node_{}".format(node_id)
    combined_mesh = bpy.data.meshes.new(mesh_name)
    combined_bmesh.to_mesh(combined_mesh)
    obj = bpy.data.objects.new(obj_name, combined_mesh)
    # Apply transforms
    obj.scale = (node.scale_x / world_scale, node.scale_z / world_scale, node.scale_y / world_scale)
    util.apply_transfrom(obj, use_scale=True)
    obj.rotation_euler = (node.rot_x / 0x7fff * -3.14, node.rot_z / 0x7fff * 3.14, node.rot_y / 0x7fff * 3.14)
    util.apply_transfrom(obj, use_rotation=True)
    obj.location = (node.x / world_scale, -node.z / world_scale, node.y / world_scale)
    util.apply_transfrom(obj, use_location=True)

    tex_to_mat_slot = dict()
    # Create vertex groups for materials
    for i in range(len(all_index_buffers)):
        index_buffer = all_index_buffers[i]
        indices = index_buffer.index_buffer
        vertex_group = obj.vertex_groups.new(name="index_buffer_" + str(i))
        vertex_group.add(indices, 1.0, "ADD")
        tex_idx = next((r.arg1 for r in index_buffer.renderstate_args if r.state_type == RenderStateType.TEXTURE_ID), None)
        if tex_idx is None:
            continue
        slot_idx = None
        if tex_idx in tex_to_mat_slot:
            slot_idx = tex_to_mat_slot[tex_idx]
        elif tex_idx < len(materials):
            obj.data.materials.append(materials[tex_idx])
            slot_idx = len(obj.data.materials) - 1
            tex_to_mat_slot[tex_idx] = slot_idx
        if slot_idx is None:
            warnings.warn("Failed to apply material due to texture ID mismatch")
        else:
            for poly in obj.data.polygons:
                if all(i in indices for i in poly.vertices):
                    poly.material_index = slot_idx
    
    return obj


def xj_to_blender_mesh(name: str, node: MeshTreeNode, materials: list[bpy.types.Material]) -> bpy.types.Collection:
    collection = bpy.data.collections.new(name)

    world_scale = util.get_pso_world_scale()
    node_counter = 0
    # Iterate tree and create a blender object for each node
    # Nodes with meshes are turned into mesh objects and empty nodes into empty objects
    # Hierarchy is maintained with blender's parenting system
    tree_stack = [(node, None)] # (current_node, current_parent_object)
    while len(tree_stack) > 0:
        (node, parent_object) = tree_stack.pop()

        if node == NULLPTR:
            continue

        if node.mesh == NULLPTR:
            # Create empty object
            obj = bpy.data.objects.new("node_{}".format(node_counter), None)
            obj.empty_display_type = "SPHERE"
            obj.empty_display_size = 0.01
            obj.scale = (node.scale_x, node.scale_z, node.scale_y)
            obj.rotation_euler = (node.rot_x / 0x7fff * -3.14, node.rot_z / 0x7fff * 3.14, node.rot_y / 0x7fff * 3.14)
            obj.location = (node.x / world_scale, -node.z / world_scale, node.y / world_scale)
        else:
            obj = xj_node_to_blender_mesh(node, node_counter, materials)
        
        obj.njcm_settings.eval_flags = set(str(x) for x in util.get_set_bits(node.eval_flags))

        collection.objects.link(obj)

        if parent_object is not None:
            obj.parent = parent_object

        node_counter += 1

        # Iterate children and siblings
        tree_stack.append((node.child, obj))
        tree_stack.append((node.next, parent_object))

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
        for x in obj.njcm_settings.eval_flags:
            eval_flags |= int(x)

        mesh_node = MeshTreeNode(
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
            mesh_node.mesh = 0xdeadbeef
        if has_next:
            mesh_node.next = 0xdeadbeef
        if has_children:
            mesh_node.child = 0xdeadbeef

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
            child_ptr = make_mesh_tree(njcm_chunk, obj.children, texture_man)
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
            elements=0xdeadbeef,
            count=len(textures))
        texlist_elements_offset = njtl_chunk.write(texlist) + IffHeader.type_size()

        first_texlist_entry_ptr = NULLPTR
        for texture in textures:
            tex_name = texture.image.name[0:31]
            name_ptr = njtl_chunk.write(AlignedString(tex_name, IffChunk.ALIGNMENT))
            ptr = njtl_chunk.write(TextureListEntry(name=name_ptr))
            if first_texlist_entry_ptr == NULLPTR:
                first_texlist_entry_ptr = ptr
        # Rewrite pointer
        pack_into(Numeric.endianness_prefix + "L", njtl_chunk.buf.buffer, texlist_elements_offset, first_texlist_entry_ptr)

        # Append NJTL
        xj_buf += njtl_chunk.finish()

    for obj in root_objs:
        # Write root nodes as one chunk each
        njcm_chunk = IffChunk("NJCM")
        make_mesh_tree(njcm_chunk, [obj], texture_man)
        xj_buf += njcm_chunk.finish()

    return xj_buf

def write(xj_path: str, xvm_path: str, root_objs: list[bpy.types.Object]):
    all_objs = []
    for obj in root_objs:
        all_objs += obj.children_recursive
    texture_man = xvm.TextureManager(all_objs)
    xj_buf = make_xj(root_objs, texture_man)

    with open(xj_path, "wb") as f:
        f.write(xj_buf)

    if xvm_path and texture_man.has_textures():
        xvm.write(xvm_path, texture_man.get_all_textures())

def read(xj_path: str, xj_xvm: xvm.Xvm) -> list[bpy.types.Collection]:
    filename = os.path.basename(xj_path)
    materials = xj_xvm.to_blender_materials(filename) if xj_xvm else []
    collections = []
    chunk_header_size = IffHeader.type_size()

    with open(xj_path, "rb") as f:
        file_contents = bytearray(f.read())

    # Read iff chunks
    chunk_offset = 0
    prev_chunk_offset = None
    prev_chunk_size = None
    need_pof0 = False
    while chunk_offset < len(file_contents):
        (chunk_header, _) = IffHeader.deserialize_from(file_contents, offset=chunk_offset)
        chunk_type = util.bytes_to_string(chunk_header.type_name)
        if chunk_type == "NJCM":
            need_pof0 = True
        elif chunk_type == "POF0":
            if need_pof0:
                # Could maybe check if pointers to 0 are valid?
                _ = parse_pof0(filename, file_contents, prev_chunk_offset, prev_chunk_size, chunk_offset, chunk_header.body_size)
                # Read a NJCM
                (root_node, _) = MeshTreeNode.read_tree(Mesh, file_contents[prev_chunk_offset + chunk_header_size:], 0)
                models = xj_to_blender_mesh(filename, root_node, materials)
                collections.append(models)
                need_pof0 = False
        prev_chunk_offset = chunk_offset
        prev_chunk_size = chunk_header.body_size
        chunk_offset += chunk_header.body_size + chunk_header_size
    return collections
