from dataclasses import dataclass
from .serialization import AlignedString, Serializable, Numeric, Ptr32


U8 = Numeric.U8
U16 = Numeric.U16
U32 = Numeric.U32
I8 = Numeric.I8
I16 = Numeric.I16
I32 = Numeric.I32
F32 = Numeric.F32
NULLPTR = Numeric.NULLPTR


@dataclass
class TextureListEntry(Serializable):
    name: Ptr32[AlignedString] = Ptr32(NULLPTR)
    unk1: U32 = 0 # Pointer set by client
    data: U32 = 0 # Pointer set by client


@dataclass
class TextureList(Serializable):
    elements: Ptr32[TextureListEntry] = Ptr32(NULLPTR)
    count: U32 = 0
