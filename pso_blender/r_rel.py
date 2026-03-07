import math
from dataclasses import dataclass
from typing import cast
import bpy.types
from .rel import Rel
from .serialization import Serializable, Numeric, Ptr32
from . import util, tristrip
from .nj import (
    Vertex,
    Mesh,
    VertexListNode,
    IndexArray,
    IndexListNode)


U8 = Numeric.U8
U16 = Numeric.U16
U32 = Numeric.U32
I8 = Numeric.I8
I16 = Numeric.I16
I32 = Numeric.I32
F32 = Numeric.F32
NULLPTR = Numeric.NULLPTR


@dataclass
class MeshContainer(Serializable):
    unk1: U32 = 0
    mesh: Ptr32[Mesh] = Ptr32(NULLPTR)


@dataclass
class Room(Serializable):
    id: U16 = 0
    flags: U16 = 0
    x: F32 = 0.0
    y: F32 = 0.0
    z: F32 = 0.0
    rot_x: I32 = 0
    rot_y: I32 = 0
    rot_z: I32 = 0
    color_alpha: F32 = 0.0
    discovery_radius: F32 = 0.0
    mesh_container: Ptr32[MeshContainer] = Ptr32(NULLPTR)


@dataclass
class Minimap(Serializable):
    rooms: Ptr32[Room] = Ptr32(NULLPTR)
    unk1: U32 = 0 # Maybe textures
    room_count: U32 = 0
    unk2: U32 = 0


def write(path: str, room_objects: list[bpy.types.Object]):
    rel = Rel()
    minimap = Minimap()
    minimap.room_count = len(room_objects)
    rooms: list[Room] = []
    for (i, obj) in enumerate(room_objects):
        blender_mesh = obj.to_mesh()

        geom_center = util.from_blender_axes(util.geometry_world_center(obj)) * util.get_pso_world_scale()
        room = Room(
            id=i,
            flags=1,
            x=geom_center[0],
            y=geom_center[1],
            z=geom_center[2])

        faces = util.mesh_faces(blender_mesh)
        vertices: list[Vertex] = []
        farthest_sq = float("-inf")
        for local_vert in blender_mesh.vertices:
            # Apply transforms from object but translate position back to local
            world_vert = util.from_blender_axes(obj.matrix_world @ local_vert.co) * util.get_pso_world_scale() - geom_center
            farthest_sq = max(farthest_sq, util.distance_squared(geom_center.to_tuple(), world_vert.to_tuple()))
            vertices.append(Vertex(
                x=world_vert[0], y=world_vert[1], z=world_vert[2],
                nx=0.0, ny=1.0, nz=0.0))
        room.discovery_radius = math.sqrt(farthest_sq)
        strips = cast(list[list[int]], tristrip.stripify(faces, stitchstrips=True))  # pyright: ignore[reportUnknownMemberType]

        container = MeshContainer()
        mesh = Mesh(
            x=geom_center[0],
            y=geom_center[1],
            z=geom_center[2])

        vertex_node = VertexListNode(
            flags=0x29,
            offset_to_next=Vertex.type_size() * len(vertices) // 4 + 1,
            vertex_count=len(vertices),
            vertices=vertices)
        vertex_node_ptr = rel.write(vertex_node)
        _ = rel.write(VertexListNode(flags=0xff)) # Terminator

        # Indices
        indices: list[IndexArray] = []
        indices_size = 0
        for strip in strips:
            indices.append(IndexArray(length=len(strip), indices=strip))
            indices_size += 2
            indices_size += len(strip) * 2

        index_node = IndexListNode(
                flags=0x0340,
                offset_to_next=indices_size // 2 + 1,
                strip_count=len(strips),
                indices=indices)

        # Due to variable amount of 16bit values we need to ensure alignment
        padding = None
        if (rel.buf.offset + IndexListNode.type_size() + indices_size) % 4 != 0:
            index_node.offset_to_next += 1
            padding = Numeric.endianness_prefix + "H"

        index_node_ptr = rel.write(index_node)

        if padding:
            _ = rel.buf.pack(padding, 0)

        _ = rel.write(IndexListNode(flags=0xff)) # Terminator

        mesh.vertex_list = vertex_node_ptr
        mesh.index_list = index_node_ptr
        mesh_ptr = rel.write(mesh)

        container.mesh = Ptr32(mesh_ptr)
        container_ptr = rel.write(container)

        room.mesh_container = Ptr32(container_ptr)
        rooms.append(room)

        obj.to_mesh_clear() # Delete temporary mesh
    # Write rooms
    first_room_ptr = None
    for room in rooms:
        room_ptr = rel.write(room)
        if first_room_ptr is None:
            first_room_ptr = room_ptr
    if first_room_ptr is not None:
        minimap.rooms = Ptr32(first_room_ptr)
    minimap_ptr = rel.write(minimap)
    file_contents = rel.finish(minimap_ptr)
    with open(path, "wb") as f:
        _ = f.write(file_contents)
