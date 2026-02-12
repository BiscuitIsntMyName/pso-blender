from dataclasses import dataclass
from typing import final
from .serialization import Serializable, Numeric


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
class MeshTreeNode(Serializable):
    eval_flags: U32 = 0
    mesh: Ptr32 = NULLPTR # Mesh
    x: F32 = 0.0
    y: F32 = 0.0
    z: F32 = 0.0
    rot_x: I32 = 0
    rot_y: I32 = 0
    rot_z: I32 = 0
    scale_x: F32 = 0.0
    scale_y: F32 = 0.0
    scale_z: F32 = 0.0
    child: Ptr32 = NULLPTR # MeshTreeNode
    next: Ptr32 = NULLPTR # MeshTreeNode

    @staticmethod
    def read_tree(mesh_type: type[Serializable], buf: bytearray, offset: int):
        (node, after) = MeshTreeNode.deserialize_from(buf, offset=offset)
        if node.mesh != NULLPTR:
            node.mesh = mesh_type.deserialize_from(buf, node.mesh)[0]
        if (node.eval_flags & NinjaEvalFlag.BREAK) == 0 and node.child != NULLPTR:
            node.child = MeshTreeNode.read_tree(mesh_type, buf, node.child)[0]
        if node.next != NULLPTR:
            node.next = MeshTreeNode.read_tree(mesh_type, buf, node.next)[0]
        return (node, after)
