from typing import final
import bpy
from struct import pack_into
from dataclasses import dataclass

from mathutils import Vector
from .serialization import Serializable, Numeric, Ptr32
from .iff import IffHeader, IffChunk
from . import util


U8 = Numeric.U8
U16 = Numeric.U16
U32 = Numeric.U32
I8 = Numeric.I8
I16 = Numeric.I16
I32 = Numeric.I32
F32 = Numeric.F32
NULLPTR = Numeric.NULLPTR


@dataclass
class KeyframeF(Serializable):
    id: U32 = 0
    x: F32 = 0.0
    y: F32 = 0.0
    z: F32 = 0.0


@dataclass
class KeyframeI(Serializable):
    id: U32 = 0
    x: I32 = 0
    y: I32 = 0
    z: I32 = 0


@dataclass
class MData3(Serializable):
    translations: Ptr32[KeyframeF] = Ptr32(NULLPTR)
    rotations: Ptr32[KeyframeI] = Ptr32(NULLPTR)
    scalings: Ptr32[KeyframeF] = Ptr32(NULLPTR)
    translation_count: U32 = 0
    rotation_count: U32 = 0
    scaling_count: U32 = 0


@dataclass
@final
class MotionFlag:
    NJD_MTYPE_POS_0         = 1 << 0
    NJD_MTYPE_ANG_1         = 1 << 1
    NJD_MTYPE_SCL_2         = 1 << 2
    NJD_MTYPE_VEC_3         = 1 << 3
    NJD_MTYPE_VERT_4        = 1 << 4
    NJD_MTYPE_NORM_5        = 1 << 5
    NJD_MTYPE_TARGET_3      = 1 << 6
    NJD_MTYPE_ROLL_6        = 1 << 7
    NJD_MTYPE_ANGLE_7       = 1 << 8
    NJD_MTYPE_RGB_8         = 1 << 9
    NJD_MTYPE_INTENSITY_9   = 1 << 10
    NJD_MTYPE_SPOT_10       = 1 << 11
    NJD_MTYPE_POINT_10      = 1 << 12
    NJD_MTYPE_QUAT_1        = 1 << 13


@dataclass
class Motion(Serializable):
    nodes_to_keyframes: Ptr32[MData3] = Ptr32(NULLPTR) # Array of MData in iteration order of model nodes (depth first). Each model node will use the corresponding keyframes from this array.
    frame_count: U32 = 0
    motion_flags: U16 = 0 # MotionFlag
    factor_count: U16 = 0 # Also contains some other flags


def get_object_actions(obj: bpy.types.Object) -> list[bpy.types.Action]:
    if not obj.animation_data:
        return []
    compatible_actions: list[bpy.types.Action] = []
    for action in bpy.data.actions:
        for slot in action.slots:
            if slot in list(obj.animation_data.action_suitable_slots):
                compatible_actions.append(action)
    return compatible_actions


def get_object_hierarchy_in_pso_order(obj: bpy.types.Object) -> list[bpy.types.Object]:
    """Order is depth first, first sibling first. Includes self."""
    ordered_nodes: list[bpy.types.Object] = []
    stack: list[bpy.types.Object] = [obj]
    while len(stack) > 0:
        cur = stack.pop()
        ordered_nodes.append(cur)
        stack += reversed(cur.children)
    return ordered_nodes


def make_njm(root_node: bpy.types.Object) -> bytearray:
    """Returns finished IffChunk"""
    if bpy.context.scene is None:
        raise Exception("NJCM error: Blender has no scene")
    world_scale = util.get_pso_world_scale()
    orig_frame = bpy.context.scene.frame_current
    ordered_nodes = get_object_hierarchy_in_pso_order(root_node)
    ordered_actions = [next(iter(get_object_actions(o)), None) for o in ordered_nodes]
    longest_duration = max(0 if not a else int(a.frame_range[1] - a.frame_range[0]) for a in ordered_actions)
    # Create chunk
    # Always include translation, rotation, and scaling, because it's easier to do
    nmdm_chunk = IffChunk("NMDM")
    motion = Motion(
        nodes_to_keyframes=Ptr32(0xdeadbeef),
        frame_count=longest_duration,
        motion_flags=MotionFlag.NJD_MTYPE_POS_0 | MotionFlag.NJD_MTYPE_ANG_1 | MotionFlag.NJD_MTYPE_SCL_2,
        factor_count=3)
    # Write this pointer later
    nodes_to_keyframes_ptr_offset = nmdm_chunk.write(motion) + IffHeader.type_size() + 0
    mdatas: list[MData3] = []
    # Iterate model nodes and create an mdata for each one
    for (node, action) in zip(ordered_nodes, ordered_actions):
        if not action:
            # Add empty track
            mdatas.append(MData3())
            continue
        translations: list[Vector] = []
        rotations: list[Vector] = []
        scalings: list[Vector] = []
        bpy.context.scene.frame_set(0)
        start_scale = util.from_blender_axes(node.matrix_world.to_scale())
        # Play animation in scene to have blender automatically apply animation transforms to object
        for frame_num in range(int(action.frame_range[0]), int(action.frame_range[1] + 1)):
            bpy.context.scene.frame_set(frame_num)
            translations.append(util.from_blender_axes(node.matrix_world.to_translation()) * world_scale)
            rotations.append(util.from_blender_axes(node.matrix_world.to_euler()))
            # Make scalings relative to scale at start
            scale_now = util.from_blender_axes(node.matrix_world.to_scale())
            scalings.append(Vector((1.0, 1.0, 1.0)) + (scale_now - start_scale))
        first_trans_ptr = NULLPTR
        first_rot_ptr = NULLPTR
        first_scale_ptr = NULLPTR
        # Write each transform type as continuous array
        for (i, vec) in enumerate(translations):
            ptr = nmdm_chunk.write(KeyframeF(
                id=i,
                x=vec[0],
                y=vec[1],
                z=vec[2]))
            if first_trans_ptr == NULLPTR:
                first_trans_ptr = ptr
        for (i, vec) in enumerate(rotations):
            ptr = nmdm_chunk.write(KeyframeI(
                id=i,
                x=int(vec[0] * 65535 / 360),
                y=int(vec[1] * 65535 / 360),
                z=int(vec[2] * 65535 / 360)))
            if first_rot_ptr == NULLPTR:
                first_rot_ptr = ptr
        for (i, vec) in enumerate(scalings):
            ptr = nmdm_chunk.write(KeyframeF(
                id=i,
                x=vec[0],
                y=vec[1],
                z=vec[2]))
            if first_scale_ptr == NULLPTR:
                first_scale_ptr = ptr
        # Finish mdata for this node
        mdatas.append(MData3(
            translations=Ptr32(first_trans_ptr),
            rotations=Ptr32(first_rot_ptr),
            scalings=Ptr32(first_scale_ptr),
            translation_count=len(translations),
            rotation_count=len(rotations),
            scaling_count=len(scalings)))
    # Write mdata array
    first_mdata_ptr = NULLPTR
    for mdata in mdatas:
        mdata_ptr = nmdm_chunk.write(mdata)
        if first_mdata_ptr == NULLPTR:
            first_mdata_ptr = mdata_ptr
    # Write pointer to mdata array into Motion struct
    pack_into(Numeric.endianness_prefix + "L", nmdm_chunk.buf.buffer, nodes_to_keyframes_ptr_offset, first_mdata_ptr)
    # Restore original scene state
    bpy.context.scene.frame_set(orig_frame)
    return nmdm_chunk.finish()
