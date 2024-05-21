import bpy
from warnings import warn
from struct import pack_into
from dataclasses import dataclass
from .serialization import Serializable, Numeric
from .iff import IffHeader, IffChunk
from . import util


U8 = Numeric.U8
U16 = Numeric.U16
U32 = Numeric.U32
I8 = Numeric.I8
I16 = Numeric.I16
I32 = Numeric.I32
F32 = Numeric.F32
Ptr32 = Numeric.Ptr32
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
    translations: Ptr32 = NULLPTR # KeyframeF
    rotations: Ptr32 = NULLPTR # KeyframeI
    scalings: Ptr32 = NULLPTR # KeyframeF
    translation_count: U32 = 0
    rotation_count: U32 = 0
    scaling_count: U32 = 0


@dataclass
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
    nodes_to_keyframes: Ptr32 = NULLPTR # Array of MData in iteration order of model nodes (depth first). Each model node will use the corresponding keyframes from this array.
    frame_count: U32 = 0
    motion_flags: U16 = 0 # MotionFlag
    factor_count: U16 = 0 # Also contains some other flags


def make_njm(objs: list[bpy.types.Object], action: bpy.types.Action) -> bytearray:
    """Returns finished IffChunk"""
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE":
            armature = obj
            break
    else:
        raise Exception("NJM: No armature")
    orig_frame = bpy.context.scene.frame_current
    orig_action = armature.animation_data.action
    armature.animation_data.action = action
    # Check what kind of transforms animation contains
    has_translation = False
    has_rotation = False
    has_scaling = False
    for fcurve in action.fcurves:
        if fcurve.data_path.endswith("location"):
            has_translation = True
        elif fcurve.data_path.endswith("rotation_quaternion"):
            has_rotation = True
        elif fcurve.data_path.endswith("scale"):
            has_scaling = True
        else:
            warn("NJM: Unsupported fcurve type '{}'".format(fcurve.data_path))
    # Create chunk
    nmdm_chunk = IffChunk("NMDM")
    motion = Motion(
        nodes_to_keyframes=0xdeadbeef,
        frame_count=int(action.frame_range[1] - action.frame_range[0]),
        motion_flags=MotionFlag.NJD_MTYPE_POS_0 | MotionFlag.NJD_MTYPE_ANG_1 | MotionFlag.NJD_MTYPE_SCL_2,
        factor_count=3)
    # Write this pointer later
    nodes_to_keyframes_ptr_offset = nmdm_chunk.write(motion) + IffHeader.type_size() + 0
    mdatas = []
    # Iterate model nodes
    for obj in objs:
        translations = []
        rotations = []
        scalings = []
        # Play animation in scene to have blender automatically apply animation transforms to object
        for frame_num in range(int(action.frame_range[0]), int(action.frame_range[1] + 1)):
            bpy.context.scene.frame_set(frame_num)
            translations.append(util.from_blender_axes(obj.matrix_world.to_translation(), False))
            rotations.append(util.from_blender_axes(obj.matrix_world.to_euler(), False))
            scalings.append(util.from_blender_axes(obj.matrix_world.to_scale(), False))
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
            translations=first_trans_ptr,
            rotations=first_rot_ptr,
            scalings=first_scale_ptr,
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
    armature.animation_data.action = orig_action
    return nmdm_chunk.finish()
