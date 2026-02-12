import math
from typing import cast, final, override
from mathutils import Vector
from dataclasses import dataclass, field
from warnings import warn
from struct import unpack_from
import bpy.types

from .njcm_node_properties_menu import ObjectWithNjcmSettings
from .rel_properties_menu import ObjectWithRelSettings
from .rel import Rel
from .serialization import Serializable, Numeric, AlignedString, FixedArray
from . import util, xvm, xj, tam
from .njcm import MeshTreeNode
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


@final
class MeshTreeFlag:
    NO_FOG = 0x1
    RECEIVES_SHADOWS = 0x10
    HAS_UV_ANIMATION = 0x20
    HAS_TEXTURE_ANIMATION = 0x40
    IS_STENCIL_VIEWER = 0x80
    IS_STENCILED = 0x100
    HAS_DOUBLE_POINTER_ROOT_NODE = 0x200


@dataclass
class TextureAnimationInfo(Serializable):
    animation_id: I16 = 0 # Needs to match .tam entry
    unk1: U16 = 0
    current_texture_index: U16 = 0 # Set at runtime
    frame_counter: U16 = 0 # ...
    frame_delay: U32 = 0 # ...


@dataclass
class MeshTree(Serializable):
    root_node: Ptr32 = NULLPTR # MeshTreeNode
    unk1: U32 = 0 # Has somethign to do with UV animations
    texture_animation_info: Ptr32 = NULLPTR # TextureAnimationInfo
    tree_flags: U32 = 0


@dataclass
class Chunk(Serializable):
    """Chunks are used for view distance"""
    id: I32 = 0
    x: F32 = 0.0
    y: F32 = 0.0
    z: F32 = 0.0
    rot_x: I32 = 0
    rot_y: I32 = 0
    rot_z: I32 = 0
    radius: F32 = 0.0
    static_mesh_trees: Ptr32 = NULLPTR # MeshTree
    animated_mesh_trees: Ptr32 = NULLPTR # Unknown
    static_mesh_tree_count: U32 = 0
    animated_mesh_tree_count: U32 = 0
    flags: U32 = 0

    @override
    def __hash__(self):
        return self.id

    @override
    def __eq__(self, other: object) -> bool:
        return self.id == other.id


@dataclass
class NrelFmt2(Serializable):
    magic: FixedArray(U8, 4) = field(default_factory=list)
    unk1: U32 = 0
    chunk_count: U16 = 0
    unk2: U16 = 0
    radius: F32 = 0.0 # Overwritten at runtime
    chunks: Ptr32 = NULLPTR # Chunk
    texture_data: Ptr32 = NULLPTR # TextureList


class NrelError(Exception):
    def __init__(self, msg: str, *, obj: bpy.types.Object | None=None, texture: bpy.types.Image | None=None):
        s = "N.REL Error"
        if obj:
            s += " in Object '{}'".format(obj.name)
        elif texture:
            s += " in Texture '{}'".format(texture.filepath)
        s += ": " + msg
        super().__init__(s)


def assign_objects_to_chunks(objects: list[bpy.types.Object], chunk_markers: list[bpy.types.Object]) -> dict[Chunk, list[bpy.types.Object]]:
    max_chunk_radius = 1200 # Approximation based on lowest value used by game
    chunk_to_children: dict[Chunk, list[bpy.types.Object]] = dict()
    collection_to_chunk: dict[str, Chunk] = dict()
    chunk_flags = 0x00010000
    chunk_counter = 0
    # Chunk -1 will always be visible
    always_rendered_chunk = Chunk(
        id=-1,
        flags=0xffffffff,
        static_mesh_tree_count=0,
        x=0.0,
        y=0.0,
        z=0.0,
        radius=999999.0)
    chunk_to_children[always_rendered_chunk] = []

    ungrouped_objects: list[bpy.types.Object] = []
    for obj in objects:
        parent_coll = obj.users_collection[0]
        # If object is set to be always rendered then put it in chunk -1
        if obj.rel_settings.always_rendered:
            chunk_to_children[always_rendered_chunk].append(obj)
        elif parent_coll.name.startswith("chunk"):
            # Create a chunk if object belongs to a collection whose name starts with "chunk"
            if parent_coll.name not in collection_to_chunk:
                # Set chunk center later
                new_chunk = Chunk(
                    id=chunk_counter,
                    flags=chunk_flags)
                chunk_counter += 1
                collection_to_chunk[parent_coll.name] = new_chunk
                chunk_to_children[new_chunk] = []
            chunk_to_children[collection_to_chunk[parent_coll.name]].append(obj)
        else:
            ungrouped_objects.append(obj)

    for coll_name in collection_to_chunk:
        # Calculate center point of each chunk
        chunk = collection_to_chunk[coll_name]
        objs = chunk_to_children[chunk]
        chunk_center = Vector((0.0, 0.0, 0.0))
        for obj in objs:
            chunk_center += obj.location
        chunk_center /= len(objs)
        chunk_center *= util.get_pso_world_scale()
        chunk_center = util.from_blender_axes(chunk_center)
        chunk.x = chunk_center.x
        chunk.y = chunk_center.y
        chunk.z = chunk_center.z
        # Calculate chunk radius
        furthest_dist_sq = 0
        for obj in objs:
            obj_center = util.from_blender_axes(util.geometry_world_center(obj)) * util.get_pso_world_scale()
            dist_sq = util.distance_squared(obj_center.xy, Vector((chunk_center.x, chunk_center.z, 0.0)).xy)
            if dist_sq > furthest_dist_sq:
                furthest_dist_sq = dist_sq
        chunk.radius = math.sqrt(furthest_dist_sq)
        if chunk.radius > max_chunk_radius:
            warn("N.REL Warning: Distance between objects in chunk '{}' might be too large (expected maximum distance of {:.1f}, was {:.1f}).".format(
                    coll_name, max_chunk_radius, chunk.radius))

    if len(chunk_markers) < 1:
        # No markers, put all meshes in the same chunk at 0,0,0
        warn("N.REL Warning: No chunk markers found in scene. Placing all meshes in default chunk.")
        chunk_to_children[always_rendered_chunk] += ungrouped_objects
        always_rendered_chunk.static_mesh_tree_count += len(ungrouped_objects)
        return chunk_to_children
    else:
        # Create a chunk for each marker
        for marker in chunk_markers:
            marker_center = util.from_blender_axes(marker.location) * util.get_pso_world_scale()
            chunk = Chunk(
                id=chunk_counter,
                flags=chunk_flags,
                radius=float("-inf"),
                x=marker_center.x,
                y=0.0,
                z=marker_center.y)
            chunk_to_children[chunk] = []
            chunk_counter += 1
        # Find each object's nearest chunk marker
        for obj in ungrouped_objects:
            if obj.rel_settings.always_rendered:
                continue
            obj_center = util.from_blender_axes(util.geometry_world_center(obj)) * util.get_pso_world_scale()
            nearest_chunk = None
            nearest_dist_sq = float("inf")
            for chunk in chunk_to_children:
                if chunk.id == -1:
                    continue
                dist_sq = util.distance_squared(obj_center.xz, Vector((chunk.x, 0.0, chunk.z)).xz)
                if dist_sq < nearest_dist_sq:
                    nearest_dist_sq = dist_sq
                    nearest_chunk = chunk
            # Add object to chunk
            chunk_to_children[nearest_chunk].append(obj)
            nearest_chunk.static_mesh_tree_count += 1
            # Also calculate chunk radius. Ensure object is definitely within radius by adding its greatest XZ dimension.
            greatest_obj_dim = max((obj.dimensions.xy * util.get_pso_world_scale()).to_tuple())
            radius = math.sqrt(nearest_dist_sq) + greatest_obj_dim
            if radius > nearest_chunk.radius:
                nearest_chunk.radius = radius
                if radius > max_chunk_radius:
                    warn("N.REL Warning: Object '{}' might be too far away from a chunk marker (expected maximum distance of {:.1f}, was {:.1f}).".format(
                        obj.name, max_chunk_radius, radius))

    # Set chunk mesh counts
    for chunk in list(chunk_to_children.keys()):
        mesh_count = len(chunk_to_children[chunk])
        chunk.static_mesh_tree_count = mesh_count
        # Discard empty chunks
        if mesh_count < 1:
            del chunk_to_children[chunk]

    return chunk_to_children


def write(nrel_path: str, xvm_path: str, tam_path: str, objects: list[bpy.types.Object], chunk_markers: list[bpy.types.Object]):
    rel = Rel()
    nrel = NrelFmt2(magic=util.magic_bytes("fmt2"))
    texture_man = xvm.TextureManager(objects)
    # Create chunks
    chunk_to_children = assign_objects_to_chunks(objects, chunk_markers)
    nrel.chunk_count = len(chunk_to_children)
    # Create chunk data.
    # Chunk coords are world, MeshNodes are local to chunks, mesh vertices are local to MeshNode
    for chunk in chunk_to_children:
        chunk_world_pos = Vector((chunk.x, chunk.y, chunk.z))
        chunk_objects = chunk_to_children[chunk]
        static_mesh_trees: list[MeshTree] = []
        for obj in chunk_objects:
            blender_mesh = obj.to_mesh()
            util.scale_mesh(blender_mesh, util.get_pso_world_scale())
            if len(blender_mesh.loop_triangles) < 1:
                raise NrelError("Object has no faces.", obj=obj)

            anim_tex = texture_man.get_object_animated_texture(obj)

            # One mesh per tree. Create tree, a node, and the mesh.
            tree_flags = 0
            rel_settings = cast(ObjectWithRelSettings, obj).rel_settings
            if not rel_settings.receives_fog:
                tree_flags |= MeshTreeFlag.NO_FOG
            if rel_settings.receives_shadows:
                tree_flags |= MeshTreeFlag.RECEIVES_SHADOWS
            if anim_tex:
                tree_flags |= MeshTreeFlag.HAS_TEXTURE_ANIMATION
            if rel_settings.is_stencil_viewer:
                tree_flags |= MeshTreeFlag.IS_STENCIL_VIEWER
            if rel_settings.is_stenciled:
                tree_flags |= MeshTreeFlag.IS_STENCILED

            static_mesh_tree = MeshTree(tree_flags=tree_flags)

            if anim_tex:
                # Just use the texture id as the animation id
                static_mesh_tree.texture_animation_info = rel.write(
                    TextureAnimationInfo(animation_id=anim_tex.id & 0x7fff))
            
            eval_flags = 0
            for x in cast(set[str], cast(ObjectWithNjcmSettings, obj.njcm_settings).eval_flags):
                eval_flags |= int(x)

            mesh_world_pos = util.from_blender_axes(obj.location * util.get_pso_world_scale())
            mesh_node = MeshTreeNode(
                eval_flags=eval_flags,
                # Make coords relative to chunk
                x=mesh_world_pos.x - chunk_world_pos.x,
                y=mesh_world_pos.y - chunk_world_pos.y,
                z=mesh_world_pos.z - chunk_world_pos.z)
            mesh = xj.make_mesh(rel, obj, blender_mesh, texture_man)
            mesh_node.mesh = rel.write(mesh)
            static_mesh_tree.root_node = rel.write(mesh_node)
            static_mesh_trees.append(static_mesh_tree)
            obj.to_mesh_clear() # Delete temporary mesh
        # Write mesh trees back to back
        first_static_mesh_tree_ptr = NULLPTR
        for tree in static_mesh_trees:
            ptr = rel.write(tree)
            if first_static_mesh_tree_ptr == NULLPTR:
                first_static_mesh_tree_ptr = ptr
        chunk.static_mesh_trees = first_static_mesh_tree_ptr
    # Write chunks back to back
    first_chunk_ptr = NULLPTR
    for chunk in chunk_to_children:
        ptr = rel.write(chunk)
        if first_chunk_ptr == NULLPTR:
            first_chunk_ptr = ptr
    nrel.chunks = first_chunk_ptr
    # Texture metadata
    textures = texture_man.get_all_textures()
    if len(textures) > 0:
        first_texlist_entry_ptr = NULLPTR
        texlist = TextureList(count=len(textures))
        for tex in textures:
            tex_name = tex.image.name[0:10]
            name_ptr = rel.write(AlignedString(tex_name, Rel.ALIGNMENT))
            ptr = rel.write(TextureListEntry(name=name_ptr))
            if first_texlist_entry_ptr == NULLPTR:
                first_texlist_entry_ptr = ptr
        texlist.elements = first_texlist_entry_ptr
        nrel.texture_data = rel.write(texlist)
    # Write files
    file_contents = rel.finish(rel.write(nrel))
    with open(nrel_path, "wb") as f:
        _ = f.write(file_contents)
    if xvm_path and len(textures) > 0:
        xvm.write(xvm_path, textures)
    if tam_path and texture_man.has_animated_textures():
        tam.write(tam_path, texture_man, objects)


def read(path: str) -> NrelFmt2:
    with open(path, "rb") as f:
        rel = Rel.read_from(bytearray(f.read()))
    (nrel, _) = rel.read(NrelFmt2, rel.payload_offset)

    fmt_magic = util.bytes_to_string(nrel.magic)
    if fmt_magic == "fmt1":
        raise Exception("n.rel fmt1 is unimplemented")
    elif fmt_magic == "fmt2":
        pass
    else:
        raise Exception("Unknown n.rel format '{}'".format(fmt_magic))
    
    chunks = Chunk.read_sequence(rel.buf.buffer, nrel.chunks, nrel.chunk_count)
    nrel.chunks = chunks

    for chunk in chunks:
        static_trees = MeshTree.read_sequence(rel.buf.buffer, chunk.static_mesh_trees, chunk.static_mesh_tree_count)
        anim_trees = MeshTree.read_sequence(rel.buf.buffer, chunk.animated_mesh_trees, chunk.animated_mesh_tree_count)

        chunk.static_mesh_trees = static_trees
        chunk.animated_mesh_trees = anim_trees

        for tree in static_trees:
            if tree.tree_flags & MeshTreeFlag.HAS_DOUBLE_POINTER_ROOT_NODE and tree.root_node != NULLPTR:
                # Resolve first pointer of double pointer
                fmt = cast(str, Numeric.format_of_type(Ptr32))
                tree.root_node = cast(tuple[int], unpack_from(fmt, rel.buf.buffer, tree.root_node))[0]
                tree.tree_flags &= ~MeshTreeFlag.HAS_DOUBLE_POINTER_ROOT_NODE

            (root_node, _) = MeshTreeNode.read_tree(xj.Mesh, rel.buf.buffer, tree.root_node)
            tree.root_node = root_node

    return nrel


def to_blender(name: str, nrel: NrelFmt2, nrel_xvm: xvm.Xvm | None) -> bpy.types.Collection:
    collection = bpy.data.collections.new(name)
    world_scale = util.get_pso_world_scale()

    chunk_markers = bpy.data.collections.new("chunk_markers")
    collection.children.link(chunk_markers)
    for chunk in nrel.chunks:
        chunk_coll = bpy.data.collections.new("chunk_" + str(chunk.id))
        collection.children.link(chunk_coll)
        chunk_coll["chunk_offset"] = hex(chunk.get_offset())

        (chunk.y, chunk.z) = (-chunk.z, chunk.y)
        chunk.x /= world_scale
        chunk.y /= world_scale
        chunk.z /= world_scale

        bpy.ops.mesh.primitive_uv_sphere_add(
            segments=4,
            ring_count=4,
            location=(chunk.x, chunk.y, chunk.z))
        obj = cast(ObjectWithRelSettings, bpy.context.active_object)
        obj.name = "chunk_marker_" + str(chunk.id)
        obj.rel_settings.is_chunk = True
        # Primitives get automatically added to default collection, remove it and add it to our collection
        obj.users_collection[0].objects.unlink(obj)
        chunk_markers.objects.link(obj)

        tree_counter = 0
        for tree in chunk.static_mesh_trees:
            models = xj.xj_to_blender_mesh("{}_{}".format(chunk.id, tree_counter), tree.root_node, nrel_xvm)
            for obj in models.objects:
                if not obj.parent:
                    obj["tree_offset"] = hex(tree.get_offset())
                    util.apply_transform(obj, use_location=True, use_rotation=True)
                    obj.location = (
                        chunk.x + obj.location.x,
                        chunk.y + obj.location.y,
                        chunk.z + obj.location.z)
                    obj.rotation_euler = (chunk.rot_x / 0x7fff * 3.14, chunk.rot_z / 0x7fff * -3.14, chunk.rot_y / 0x7fff * 3.14)

                rel_settings = cast(ObjectWithRelSettings, obj).rel_settings
                rel_settings.is_nrel = True
                # Read settings
                rel_settings.receives_fog = not bool(tree.tree_flags & MeshTreeFlag.NO_FOG)
                rel_settings.receives_shadows = bool(tree.tree_flags & MeshTreeFlag.RECEIVES_SHADOWS)
                rel_settings.is_stencil_viewer = bool(tree.tree_flags & MeshTreeFlag.IS_STENCIL_VIEWER)
                rel_settings.is_stenciled = bool(tree.tree_flags & MeshTreeFlag.IS_STENCILED)
                # Make objects direct children of chunk collection instead
                chunk_coll.objects.link(obj)
            # Remove now empty collection
            bpy.data.collections.remove(models)
            tree_counter += 1

    return collection
