import math, os, re, shutil
from collections.abc import Generator
from typing import Any, Literal, cast, final
from mathutils import Vector
from dataclasses import dataclass, field
from warnings import warn
import bpy.types

from .njcm import NinjaEvalFlag
from .njcm_node_properties_menu import ObjectWithNjcmSettings
from .rel_properties_menu import ObjectWithRelSettings
from .rel import Rel
from .serialization import Serializable, Numeric, AlignedString, FixedArray, Ptr32
from . import util, xvm, xj, tam
from .njtl import TextureList, TextureListEntry


U8 = Numeric.U8
U16 = Numeric.U16
U32 = Numeric.U32
I8 = Numeric.I8
I16 = Numeric.I16
I32 = Numeric.I32
F32 = Numeric.F32
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


# Bits write() actively recomputes from this addon's own settings/behavior on every export -
# everything else that real map data is observed to use (e.g. map_acity's chunk 10/20 buildings
# all carry bit 0x200000, whose exact meaning isn't reverse-engineered yet) must be preserved from
# the original file instead of silently dropped - see write()'s tree_flags construction and
# read_steps' "orig_tree_flags" custom property below.
#
# HAS_UV_ANIMATION is included here (force-cleared, NOT preserved) even though this addon has no
# setting for it, unlike every other unhandled bit: MeshTree.unk1 ("has something to do with UV
# animations") is a real, meaningful payload for that flag specifically - confirmed empirically on
# map_acity00_00n.rel, unk1 is non-zero (values like 24, 48, 72, ... - some kind of index/offset
# into a UV animation table this addon doesn't parse or reconstruct) on every single tree that
# carries HAS_UV_ANIMATION, and exactly 0 on every tree that doesn't. write() has never populated
# unk1 (always the dataclass default 0). Before this bit was added to the preserved set, that was
# harmless - the flag was also always cleared, so nothing ever told the game to go look at unk1.
# Once tree_flags-preservation started carrying HAS_UV_ANIMATION through unchanged, every
# UV-animated tree started exporting as "has UV animation data" (flag set) while still pointing at
# unk1=0 (no real data) - an inconsistent state that crashed the game on real map_acity data. Until
# unk1's actual table is understood and reconstructed, force-clearing this bit on every export (its
# pre-existing, safe behavior) is the correct tradeoff over preserving a flag whose payload we
# cannot provide.
KNOWN_TREE_FLAGS_MASK = (
    MeshTreeFlag.NO_FOG | MeshTreeFlag.RECEIVES_SHADOWS | MeshTreeFlag.HAS_UV_ANIMATION
    | MeshTreeFlag.HAS_TEXTURE_ANIMATION | MeshTreeFlag.IS_STENCIL_VIEWER | MeshTreeFlag.IS_STENCILED
    | MeshTreeFlag.HAS_DOUBLE_POINTER_ROOT_NODE)


@dataclass
class TextureAnimationInfo(Serializable):
    animation_id: I16 = 0 # Needs to match .tam entry
    unk1: U16 = 0
    current_texture_index: U16 = 0 # Set at runtime
    frame_counter: U16 = 0 # ...
    frame_delay: U32 = 0 # ...


@dataclass
class MeshTree(Serializable):
    root_node: Ptr32[xj.XjMeshTreeNode] = Ptr32(NULLPTR)
    unk1: U32 = 0 # Has somethign to do with UV animations
    texture_animation_info: Ptr32[TextureAnimationInfo] = Ptr32(NULLPTR)
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
    static_mesh_trees: Ptr32[MeshTree] = Ptr32(NULLPTR)
    animated_mesh_trees: Ptr32[MeshTree] = Ptr32(NULLPTR)
    static_mesh_tree_count: U32 = 0
    animated_mesh_tree_count: U32 = 0
    flags: U32 = 0

    def __hash__(self):
        return self.id

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Chunk) and self.id == other.id


@dataclass
class NrelFmt2(Serializable):
    magic: FixedArray[U8, Literal[4]] = field(default_factory=FixedArray)
    unk1: U32 = 0
    chunk_count: U16 = 0
    unk2: U16 = 0
    radius: F32 = 0.0 # Overwritten at runtime
    chunks: Ptr32[Chunk] = Ptr32(NULLPTR)
    texture_data: Ptr32[TextureList] = Ptr32(NULLPTR)


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
    # Chunk -1 will always be visible. Flags default to 0xffffffff for a chunk with no round-trip
    # data to recover the real value from (a manually-flagged always_rendered object with no
    # chunk_root history) - overridden below with the file's real value when available, since it
    # isn't actually 0xffffffff in every map (e.g. aforest01's is 0x90000).
    always_rendered_chunk = Chunk(
        id=-1,
        flags=0xffffffff,
        static_mesh_tree_count=0,
        x=0.0,
        y=0.0,
        z=0.0,
        radius=999999.0)
    chunk_to_children[always_rendered_chunk] = []

    # Re-exporting an already-imported map: each "chunk_N" collection's name encodes the file's
    # original chunk id (read_steps below names it "chunk_" + str(chunk.id)) - reuse that id
    # instead of handing out a fresh sequential one, so anything outside this file that refers to
    # a chunk by its numeric id (e.g. inter-map door/barrier linkage, or a spawn point tied to a
    # specific chunk) keeps working after a round-trip. Only reused when unambiguous - if two
    # different collections in the scene both parse to the same original id (e.g. two different
    # maps loaded into one Blender file, each with their own "chunk_1"), reusing either would
    # create a real id collision in the exported file, so both fall back to a fresh id. The
    # collection's "chunk_flags" custom property (set on import, read_steps below) is reused the
    # same way, for the same reason - flags aren't always the same fixed constant either.
    #
    # -1 is handled separately, never through this ambiguity check: it's a non-unique sentinel in
    # the file format, not a real per-chunk id - a map can legitimately contain several
    # independent "always visible" chunk entries that all share id -1 (confirmed in
    # map_acave01_00, e.g. collections "chunk_-1", "chunk_-1.001", "chunk_-1.004", ...), so
    # treating repeats of it as an ambiguous collision would wrongly demote all of them to
    # ordinary, position/radius-culled chunks instead of merging them into always_rendered_chunk.
    seen_original_ids: dict[int, set[str]] = {}
    seen_coll_names: set[str] = set()
    coll_name_to_original_flags: dict[str, int] = {}
    always_rendered_coll_names: set[str] = set()
    for obj in objects:
        coll_name = obj.users_collection[0].name
        if not coll_name.startswith("chunk") or coll_name in seen_coll_names:
            continue
        seen_coll_names.add(coll_name)
        coll = obj.users_collection[0]
        if "chunk_flags" in coll:
            coll_name_to_original_flags[coll_name] = cast(int, coll["chunk_flags"])
        match = re.match(r"^chunk_(-?\d+)$", coll_name.split(".")[0])
        if match:
            original_id = int(match.group(1))
            if original_id == -1:
                always_rendered_coll_names.add(coll_name)
                if coll_name in coll_name_to_original_flags:
                    # Several original entries may disagree on flags - always_rendered_chunk can
                    # only carry one value, so the first one found wins (best achievable without
                    # representing each original entry as a fully separate chunk).
                    always_rendered_chunk.flags = coll_name_to_original_flags[coll_name]
            else:
                seen_original_ids.setdefault(original_id, set()).add(coll_name)
    coll_name_to_reused_id: dict[str, int] = {}
    for original_id, colls in seen_original_ids.items():
        if len(colls) == 1:
            coll_name_to_reused_id[next(iter(colls))] = original_id
        else:
            warn("N.REL Warning: multiple chunk collections in the scene correspond to the same "
                 "original chunk id {} ({}) - assigning fresh ids to avoid a collision in the "
                 "exported file (this usually means more than one map is loaded into this "
                 "Blender file at once).".format(original_id, ", ".join(sorted(colls))))

    chunk_counter = 0
    used_ids = set(coll_name_to_reused_id.values())

    def next_fresh_id() -> int:
        nonlocal chunk_counter
        while chunk_counter in used_ids or chunk_counter == -1:
            chunk_counter += 1
        result = chunk_counter
        used_ids.add(result)
        chunk_counter += 1
        return result

    ungrouped_objects: list[bpy.types.Object] = []
    for obj in objects:
        parent_coll = obj.users_collection[0]
        reused_id = coll_name_to_reused_id.get(parent_coll.name)
        # If object is set to be always rendered (manual override, or its collection's original
        # chunk id was -1) then put it in chunk -1
        if cast(ObjectWithRelSettings, obj).rel_settings.always_rendered or parent_coll.name in always_rendered_coll_names:
            chunk_to_children[always_rendered_chunk].append(obj)
        elif parent_coll.name.startswith("chunk"):
            # Create a chunk if object belongs to a collection whose name starts with "chunk"
            if parent_coll.name not in collection_to_chunk:
                # Set chunk center later
                new_chunk = Chunk(
                    id=reused_id if reused_id is not None else next_fresh_id(),
                    flags=coll_name_to_original_flags.get(parent_coll.name, chunk_flags))
                collection_to_chunk[parent_coll.name] = new_chunk
                chunk_to_children[new_chunk] = []
            chunk_to_children[collection_to_chunk[parent_coll.name]].append(obj)
        else:
            ungrouped_objects.append(obj)

    for coll_name in collection_to_chunk:
        # Calculate center point of each chunk
        chunk = collection_to_chunk[coll_name]
        objs = chunk_to_children[chunk]
        coll = bpy.data.collections.get(coll_name)
        chunk_root = next((o for o in coll.objects if o.name.startswith("chunk_root_")), None) if coll is not None else None
        if chunk_root is not None:
            # Re-exporting an already-imported map: trust the chunk_root empty's transform
            # (set on import straight from the file's real chunk.x/y/z/rot_x/y/z, see
            # read_steps below) as the authoritative chunk position/rotation, instead of
            # recomputing an approximation from the member objects - those are parented under
            # chunk_root, so their .location is local to it (small, near-zero), not a world
            # position; averaging .location directly (the previous approach) collapsed every
            # chunk to roughly (0, 0, 0) on any round-trip export.
            chunk_center = util.from_blender_axes(chunk_root.location * util.get_pso_world_scale())
            rot = chunk_root.rotation_euler
            chunk.rot_x = round(rot.x / math.pi * 0x7fff)
            chunk.rot_z = round(rot.y / math.pi * -0x7fff)
            chunk.rot_y = round(rot.z / math.pi * 0x7fff)
        else:
            # Freshly authored chunk collection with no chunk_root marker - approximate the
            # center from the members' actual world positions. Must use matrix_world, not
            # .location directly - the latter is only equal to world position for an
            # unparented object, which no longer holds once a member has been reparented
            # (e.g. under some other empty) by hand.
            chunk_center = Vector((0.0, 0.0, 0.0))
            for obj in objs:
                chunk_center += obj.matrix_world.translation
            chunk_center /= len(objs)
            chunk_center *= util.get_pso_world_scale()
            chunk_center = util.from_blender_axes(chunk_center)
        chunk.x = chunk_center.x
        chunk.y = chunk_center.y
        chunk.z = chunk_center.z
        # Calculate chunk radius - horizontal (X/Z) ground-plane distance only, matching how the
        # marker-based path below already does it (obj_center.xz, not .xy - Y is height, mixing
        # it in against the chunk's Z position previously produced wildly inflated radii for any
        # chunk positioned far from the origin along Z, e.g. map_acity's chunk 41 at z=-2000
        # computed a radius of ~2200 instead of the real file's 374.5).
        furthest_dist_sq = 0
        for obj in objs:
            obj_center = util.from_blender_axes(util.geometry_world_center(obj)) * util.get_pso_world_scale()
            dist_sq = util.distance_squared(obj_center.xz.to_tuple(), Vector((chunk_center.x, 0.0, chunk_center.z)).xz.to_tuple())
            if dist_sq > furthest_dist_sq:
                furthest_dist_sq = dist_sq
        chunk.radius = math.sqrt(furthest_dist_sq)
        if chunk.radius > max_chunk_radius:
            warn("N.REL Warning: Distance between objects in chunk '{}' might be too large (expected maximum distance of {:.1f}, was {:.1f}).".format(
                    coll_name, max_chunk_radius, chunk.radius))

    if len(chunk_markers) < 1 and len(ungrouped_objects) > 0:
        # No markers, put all meshes in the same chunk at 0,0,0
        warn("N.REL Warning: No chunk markers found in scene. Placing all ungrouped meshes in default chunk.")
        chunk_to_children[always_rendered_chunk] += ungrouped_objects
        always_rendered_chunk.static_mesh_tree_count += len(ungrouped_objects)
        return chunk_to_children
    else:
        # Create a chunk for each marker
        for marker in chunk_markers:
            marker_center = util.from_blender_axes(marker.location) * util.get_pso_world_scale()
            chunk = Chunk(
                id=next_fresh_id(),
                flags=chunk_flags,
                radius=float("-inf"),
                x=marker_center.x,
                y=0.0,
                z=marker_center.y)
            chunk_to_children[chunk] = []
        # Find each object's nearest chunk marker
        for obj in ungrouped_objects:
            if cast(ObjectWithRelSettings, obj).rel_settings.always_rendered:
                continue
            obj_center = util.from_blender_axes(util.geometry_world_center(obj)) * util.get_pso_world_scale()
            nearest_chunk = always_rendered_chunk
            nearest_dist_sq = float("inf")
            for chunk in chunk_to_children:
                if chunk.id == -1:
                    continue
                dist_sq = util.distance_squared(obj_center.xz.to_tuple(), Vector((chunk.x, 0.0, chunk.z)).xz.to_tuple())
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
    texture_man = xvm.TextureManager(objects)
    try:
        _write_impl(nrel_path, xvm_path, tam_path, objects, chunk_markers, texture_man)
    finally:
        # Per-frame images TextureManager loaded solely to read animated textures' pixels into
        # the exported .xvm are never referenced by any material/node - remove them here rather
        # than leaving them to accumulate in bpy.data.images across repeated exports, even if
        # _write_impl raised partway through.
        texture_man.cleanup_ephemeral_images()


def _write_impl(nrel_path: str, xvm_path: str, tam_path: str, objects: list[bpy.types.Object], chunk_markers: list[bpy.types.Object], texture_man: xvm.TextureManager):
    # obj.matrix_world (relied on below and in assign_objects_to_chunks for every object's real
    # world position) is lazily recomputed by Blender's dependency graph - it can still reflect a
    # stale pre-edit transform immediately after a script sets .location/.parent (e.g. right after
    # an import) without an intervening redraw. Force it current here rather than trusting every
    # caller to have triggered one.
    bpy.context.view_layer.update()
    # Evaluate through the dependency graph (not obj.to_mesh() directly on the original object,
    # which silently ignores any live/unapplied modifier - Decimate, Mirror, Subsurf, etc. - and
    # exports the raw base mesh instead) so what gets exported matches what's actually shown in
    # the viewport.
    depsgraph = bpy.context.evaluated_depsgraph_get()
    rel = Rel()
    nrel = NrelFmt2(magic=FixedArray(util.magic_bytes("fmt2")))
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
            eval_obj = obj.evaluated_get(depsgraph)
            blender_mesh = eval_obj.to_mesh()
            util.scale_mesh(blender_mesh, util.get_pso_world_scale())
            if len(blender_mesh.loop_triangles) < 1:
                raise NrelError("Object has no faces.", obj=obj)

            anim_tex = texture_man.get_object_animated_texture(obj)

            # One mesh per tree. Create tree, a node, and the mesh.
            # Start from the original file's tree_flags (if this object came from an import),
            # minus the bits this addon actually recomputes below - carries through
            # HAS_UV_ANIMATION and any undocumented bit real map data uses (see
            # KNOWN_TREE_FLAGS_MASK) instead of silently dropping it on every export. A freshly
            # authored object with no prior import starts from 0, same as before.
            tree_flags = cast(int, obj.get("orig_tree_flags", 0)) & ~KNOWN_TREE_FLAGS_MASK
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
                static_mesh_tree.texture_animation_info = Ptr32(rel.write(
                    TextureAnimationInfo(animation_id=anim_tex.id & 0x7fff)))
            
            eval_flags = 0
            for flag_name in cast(set[str], cast(ObjectWithNjcmSettings, obj).njcm_settings.eval_flags):
                eval_flags |= getattr(NinjaEvalFlag, flag_name).value

            # matrix_world.translation, not .location - the object may be parented (e.g. under
            # a chunk_root empty from a prior import), in which case .location is local to that
            # parent (small, near-zero) rather than the object's real world position, which
            # previously collapsed every re-exported chunk down to roughly (0, 0, 0).
            mesh_world_pos = util.from_blender_axes(obj.matrix_world.translation * util.get_pso_world_scale())
            # Inverse of set_obj_transforms_from_xj_node's rotation_euler assignment - only
            # meaningful while obj.rotation_mode is still "XZY" as import leaves it (reads the
            # raw per-axis values directly, not a decomposition of matrix_world, so a manual
            # rotation_mode change after import would be misinterpreted here).
            rot = obj.rotation_euler
            mesh_node = xj.XjMeshTreeNode(
                eval_flags=eval_flags,
                rot_x=round(rot.x / math.pi * 0x7fff),
                rot_z=round(rot.y / math.pi * -0x7fff),
                rot_y=round(rot.z / math.pi * 0x7fff),
                # Inverse of set_obj_transforms_from_xj_node's obj.scale assignment - was
                # previously never set at all (always serialized as the dataclass default,
                # 0.0), which is harmless while a node's eval_flags carries UNIT_SCL (scale is
                # ignored on import) but would zero out geometry entirely for any node that
                # legitimately relies on a non-unit stored scale.
                scale_x=obj.scale.x,
                scale_z=obj.scale.y,
                scale_y=obj.scale.z,
                # Make coords relative to chunk
                x=mesh_world_pos.x - chunk_world_pos.x,
                y=mesh_world_pos.y - chunk_world_pos.y,
                z=mesh_world_pos.z - chunk_world_pos.z)
            try:
                mesh = xj.make_mesh(rel, obj, blender_mesh, texture_man)
            except Exception as ex:
                # A single malformed object (e.g. a texture but no UV layer - a real, pre-existing
                # data quirk confirmed on real map_acity data) previously aborted the ENTIRE export
                # with no output file at all, silently discarding every other object in the map.
                # Skip just this one object instead, matching this addon's established convention
                # elsewhere (warn and continue rather than hard-fail the whole export).
                warn("N.REL Warning: skipping object '{}' - {}".format(obj.name, ex))
                eval_obj.to_mesh_clear()
                continue
            mesh_node.mesh = Ptr32(rel.write(mesh))
            static_mesh_tree.root_node = Ptr32(rel.write(mesh_node))
            static_mesh_trees.append(static_mesh_tree)
            eval_obj.to_mesh_clear() # Delete temporary mesh
        # Write mesh trees back to back
        first_static_mesh_tree_ptr = NULLPTR
        for tree in static_mesh_trees:
            ptr = rel.write(tree)
            if first_static_mesh_tree_ptr == NULLPTR:
                first_static_mesh_tree_ptr = ptr
        chunk.static_mesh_trees = Ptr32(first_static_mesh_tree_ptr)
        # May be fewer than assign_objects_to_chunks originally counted, if any object above was
        # skipped - must match the real number of trees actually written, not the intended one.
        chunk.static_mesh_tree_count = len(static_mesh_trees)
    # Write chunks back to back
    first_chunk_ptr = NULLPTR
    for chunk in chunk_to_children:
        ptr = rel.write(chunk)
        if first_chunk_ptr == NULLPTR:
            first_chunk_ptr = ptr
    nrel.chunks = Ptr32(first_chunk_ptr)
    # Texture metadata
    textures = texture_man.get_all_textures()
    if len(textures) > 0:
        first_texlist_entry_ptr = NULLPTR
        texlist = TextureList(count=len(textures))
        for tex in textures:
            tex_name = tex.image.name[0:10]
            name_ptr = rel.write(AlignedString(tex_name, Rel.ALIGNMENT))
            ptr = rel.write(TextureListEntry(name=Ptr32(name_ptr)))
            if first_texlist_entry_ptr == NULLPTR:
                first_texlist_entry_ptr = ptr
        texlist.elements = Ptr32(first_texlist_entry_ptr)
        nrel.texture_data = Ptr32(rel.write(texlist))
    # Write files
    file_contents = rel.finish(rel.write(nrel))
    with open(nrel_path, "wb") as f:
        _ = f.write(file_contents)
    if xvm_path and len(textures) > 0:
        xvm.write(xvm_path, textures)
    if tam_path and texture_man.has_animated_textures():
        tam.write(tam_path, texture_man, objects)


def read_steps(path: str, nrel_xvm: xvm.Xvm | None, result: dict[str, Any], tam_path: str | None = None) -> Generator[None, None, None]:
    """Does the exact same work as read() below, but one mesh tree at a time, yielding after
    each one - lets a caller (see ModalStepOperator in util.py) drive this via a modal timer
    instead of one big blocking call, so a real progress indicator can be shown for what's
    usually the slowest part of an import (building each tree's mesh geometry).

    Since a generator can't just be called and immediately handed a return value the way a plain
    function can, results are written into the caller-supplied `result` dict instead: `"total"`
    (the number of trees, for sizing a progress bar) as soon as it's known - before any of the
    actual per-tree work below has run - and `"collection"` (the finished top-level Collection)
    from that point on, filled in with new content on every subsequent step.
    """
    with open(path, "rb") as f:
        rel = Rel.read_from(bytearray(f.read()))
    if rel.payload_offset is None:
        filename = os.path.basename(path)
        raise Exception("Rel error in file '{}': Missing payload".format(filename))
    (nrel, _) = rel.read(NrelFmt2, rel.payload_offset)

    fmt_magic = util.bytes_to_string(nrel.magic)
    if fmt_magic == "fmt1":
        raise Exception("n.rel fmt1 is unimplemented")
    elif fmt_magic == "fmt2":
        pass
    else:
        raise Exception("Unknown n.rel format '{}'".format(fmt_magic))

    chunks = nrel.chunks.deref_array(nrel.chunk_count)
    result["total"] = sum(chunk.static_mesh_tree_count for chunk in chunks)

    collection = bpy.data.collections.new(path)
    result["collection"] = collection
    world_scale = util.get_pso_world_scale()
    tam_entries: dict[int, tam.TamEntry] = tam.read(tam_path) if tam_path and os.path.isfile(tam_path) else {}
    if tam_entries and nrel_xvm is not None:
        # Every fresh import rebuilds this map's animated-texture cache from scratch instead of
        # reusing whatever a previous run left behind - matches the addon's actual real-world
        # testing workflow (reinstall -> restart Blender -> reimport, never saving), and avoids
        # ever silently serving stale frame content from an earlier version of this same .tam/.xvm.
        # Scoped to just this map's own cache subfolder (see animated_texture_cache_root in
        # xj.py) - other maps' cached frames are left untouched.
        shutil.rmtree(xj.animated_texture_cache_root(nrel_xvm.get_filename()), ignore_errors=True)
    yield  # Header parsed, result["total"] is now valid - no per-tree work has happened yet.

    for chunk in chunks:
        chunk_coll = bpy.data.collections.new("chunk_" + str(chunk.id))
        collection.children.link(chunk_coll)
        chunk_coll["chunk_offset"] = hex(chunk.get_offset())
        chunk_coll["chunk_flags"] = chunk.flags

        chunk_root = bpy.data.objects.new("chunk_root_" + str(chunk.id), None)
        chunk_root.empty_display_type = "SPHERE"
        chunk_root.empty_display_size = 0.01
        chunk_root.rotation_mode = "XZY"
        chunk_root.rotation_euler = (chunk.rot_x / 0x7fff * math.pi, chunk.rot_z / 0x7fff * -math.pi, chunk.rot_y / 0x7fff * math.pi)
        chunk_root.location = (chunk.x / world_scale, -chunk.z / world_scale, chunk.y / world_scale)
        chunk_coll.objects.link(chunk_root)

        tree_counter = 0
        for tree in chunk.static_mesh_trees.deref_array(chunk.static_mesh_tree_count):
            if tree.tree_flags & MeshTreeFlag.HAS_DOUBLE_POINTER_ROOT_NODE:
                dbl_ptr = tree.root_node.retype(cast(type[int], U32))
                tree.root_node = tree.root_node.clone(dbl_ptr.deref())
            root_node = tree.root_node.deref()
            tam_entry: tam.TamEntry | None = None
            if tree.tree_flags & MeshTreeFlag.HAS_TEXTURE_ANIMATION and tree.texture_animation_info != NULLPTR:
                anim_info = tree.texture_animation_info.deref()
                tam_entry = tam_entries.get(anim_info.animation_id)
                if tam_entry is None:
                    warn("N.REL Warning: tree at {} has HAS_TEXTURE_ANIMATION set (animation_id={}) "
                         "but no matching entry was found in the .tam file - importing its texture "
                         "as static.".format(hex(tree.get_offset()), anim_info.animation_id))
            models = xj.xj_to_blender_mesh("{}_{}".format(chunk.id, tree_counter), root_node, nrel_xvm, tam_entry)
            for obj in models.objects:
                if not obj.parent:
                    # Make top-level objects children of chunk root object
                    obj.parent = chunk_root
                    obj["tree_offset"] = hex(tree.get_offset())

                rel_settings = cast(ObjectWithRelSettings, obj).rel_settings
                rel_settings.is_nrel = True
                # Read settings
                rel_settings.receives_fog = not bool(tree.tree_flags & MeshTreeFlag.NO_FOG)
                rel_settings.receives_shadows = bool(tree.tree_flags & MeshTreeFlag.RECEIVES_SHADOWS)
                rel_settings.is_stencil_viewer = bool(tree.tree_flags & MeshTreeFlag.IS_STENCIL_VIEWER)
                rel_settings.is_stenciled = bool(tree.tree_flags & MeshTreeFlag.IS_STENCILED)
                # Round-trip safety net: this addon only exposes a handful of tree_flags bits as
                # actual settings (above) - stash the file's real, complete value so write() can
                # carry through everything else (HAS_UV_ANIMATION, and any undocumented bit real
                # map data uses) unchanged instead of silently dropping it on export.
                obj["orig_tree_flags"] = tree.tree_flags
                # Make objects direct children of chunk collection instead
                chunk_coll.objects.link(obj)
            # Remove now empty collection
            bpy.data.collections.remove(models)
            tree_counter += 1
            yield


def read(path: str, nrel_xvm: xvm.Xvm | None, tam_path: str | None = None) -> bpy.types.Collection:
    result: dict[str, Any] = {}
    for _ in read_steps(path, nrel_xvm, result, tam_path):
        pass
    return cast(bpy.types.Collection, result["collection"])
