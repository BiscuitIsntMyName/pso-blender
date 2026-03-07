from dataclasses import dataclass, field
from typing import Final, Literal, ReadOnly, Self, cast
import unittest
from pso_blender.serialization import Ptr32, Serializable, Numeric, ResizableBuffer, FixedArray


U8 = Numeric.U8
U16 = Numeric.U16
U32 = Numeric.U32
I8 = Numeric.I8
I16 = Numeric.I16
I32 = Numeric.I32
F32 = Numeric.F32
NULLPTR = Numeric.NULLPTR


@dataclass
class MyBasicStruct(Serializable):
    x: F32 = 0.0
    y: F32 = 0.0
    z: F32 = 0.0


@dataclass
class MyFlexStruct(Serializable):
    flags: U32 = 0
    vertices: list[MyBasicStruct] = field(default_factory=list)


@dataclass
class MyUnalignedStruct(Serializable):
    foo: U16 = 0
    bar: U16 = 0
    buz: U16 = 0


@dataclass
class MyPointingStruct(Serializable):
    data_count: U32 = 0
    data: list[U8] = field(default_factory=list)
    child: Ptr32[MyPointingStruct] = Ptr32(NULLPTR)
    sibling: Ptr32[MyPointingStruct] = Ptr32(NULLPTR)


@dataclass
class MyBufferStruct(Serializable):
    data_count: U32 = 0
    data: bytearray = field(default_factory=bytearray)


@dataclass
class MyFixedArrayStruct(Serializable):
    name: FixedArray[U8, Literal[16]] = field(default_factory=list)
    flags: U32 = 0


@dataclass
class MyOtherPointingStruct(Serializable):
    data: Ptr32[MyBasicStruct] = Ptr32(NULLPTR)


@dataclass
class MyRecursiveGenericStruct[T: Serializable](Serializable):
    data: Ptr32[T] = Ptr32(NULLPTR)
    bar: U8 = 0
    child: Ptr32[Self] = Ptr32(NULLPTR)


# Explicit specialization required for serialization framework to work
@dataclass
class MySpecializedRecursiveGenericStruct(MyRecursiveGenericStruct[MyBasicStruct]):
    data: Ptr32[MyBasicStruct] = Ptr32(NULLPTR)
    child: Ptr32[MySpecializedRecursiveGenericStruct] = Ptr32(NULLPTR)  # pyright: ignore[reportIncompatibleVariableOverride]


class TestSerialization(unittest.TestCase):
    def test_basic_struct_instance_size(self):
        self.assertEqual(MyBasicStruct().instance_size(), 12)
    
    def test_basic_struct_type_size(self):
        self.assertEqual(MyBasicStruct.type_size(), 12)
    
    def test_basic_struct_member_offset(self):
        self.assertEqual(MyBasicStruct.offset_of("z"), 8)
    
    def test_flex_struct_instance_size(self):
        vertices = [MyBasicStruct(), MyBasicStruct()]
        self.assertEqual(MyFlexStruct(vertices=vertices).instance_size(), 4 + 12 * 2)
    
    def test_flex_struct_type_size(self):
        self.assertEqual(MyFlexStruct.type_size(), 4)

    def test_resizable_buffer_pack(self):
        buf = ResizableBuffer(size=0)
        fmt = Numeric.format_of_type(U32)
        self.assertTrue(fmt)
        fmt = cast(str, fmt)
        size = Numeric.size_of_format(fmt)
        _ = buf.pack(fmt, 123)
        self.assertEqual(buf.capacity, size)
        self.assertEqual(buf.offset, size)
    
    def test_serialize_basic_struct_unaligned(self):
        buf = ResizableBuffer(size=0)
        item = MyUnalignedStruct()
        _ = item.serialize_into(buf)
        self.assertEqual(buf.offset, 6)
    
    def test_serialize_basic_struct_aligned(self):
        buf = ResizableBuffer(size=0)
        item = MyUnalignedStruct()
        _ = item.serialize_into(buf, 4)
        self.assertEqual(buf.offset, 8)

    def test_struct_nonnull_pointer_member_offsets(self):
        data = [1, 2, 3]
        item = MyPointingStruct(data_count=len(data), data=data, sibling=Ptr32(1337))
        self.assertEqual(item.nonnull_pointer_member_offsets(), [11])
    
    def test_serialize_buffer_member(self):
        buf = ResizableBuffer(size=0)
        data = bytearray(b"\xde\xad\xbe\xef")
        item = MyBufferStruct(data=data, data_count=len(data))
        offset = item.serialize_into(buf)
        self.assertEqual(offset, 0)
        self.assertEqual(buf.buffer[0], len(data))
        self.assertEqual(buf.buffer[4], 0xde)
        self.assertEqual(buf.buffer[5], 0xad)
        self.assertEqual(buf.buffer[6], 0xbe)
        self.assertEqual(buf.buffer[7], 0xef)
    
    def test_fixed_array(self):
        buf = ResizableBuffer(size=0)
        item = MyFixedArrayStruct(name=list(str.encode("deadbeef")), flags=0xdeadbeef)
        _ = item.serialize_into(buf)
        self.assertEqual(buf.buffer[0:8], b"deadbeef")
        self.assertEqual(buf.buffer[8:16], b"\0\0\0\0\0\0\0\0")
        self.assertEqual(buf.buffer[16:24], b"\xef\xbe\xad\xde")


class TestDeserialization(unittest.TestCase):
    def test_basic_struct_deserialize(self):
        buf = b"\x00\x00\x80\x3f\x00\x00\x80\x3f\x00\x00\x80\x3f"
        (result, offset) = MyBasicStruct.deserialize_from(buf)
        self.assertEqual(offset, 12)
        self.assertEqual(result.x, 1.0)
        self.assertEqual(result.y, 1.0)
        self.assertEqual(result.z, 1.0)

    def test_fixed_array(self):
        buf = b"deadbeef\0\0\0\0\0\0\0\0\xef\xbe\xad\xde"
        (result, _offset) = MyFixedArrayStruct.deserialize_from(buf)
        self.assertEqual(bytes(result.name[0:8]).decode(), "deadbeef")
        self.assertEqual(result.flags, 0xdeadbeef)

    def test_deref_pointer(self):
        buf = b"\x04\x00\x00\x00\x00\x00\x80\x3f\x00\x00\x80\x3f\x00\x00\x80\x3f"
        (root_struct, _offset) = MyOtherPointingStruct.deserialize_from(buf)
        self.assertEqual(root_struct.data, 4)
        pointee = root_struct.data.deref()
        self.assertEqual(pointee.x, 1.0)
        self.assertEqual(pointee.y, 1.0)
        self.assertEqual(pointee.z, 1.0)
    
    def test_recursive_generic(self):
        buf = b"\x00\x00\x80\x3f\x00\x00\x80\x3f\x00\x00\x80\x3f" # MyBasicStruct
        buf += b"\x00\x00\x00\x00" # MyRecursiveGenericStruct.data
        buf += b"\x07" # MyRecursiveGenericStruct.bar
        buf += b"\x15\x00\x00\x00" # MyRecursiveGenericStruct.child
        buf += b"\x00\x00\x00\x00" # MyRecursiveGenericStruct.data
        buf += b"\x05" # MyRecursiveGenericStruct.bar
        buf += b"\x00\x00\x00\x00" # MyRecursiveGenericStruct.child
        (root_struct, _offset) = MySpecializedRecursiveGenericStruct.deserialize_from(buf, offset=12)
        self.assertEqual(root_struct.data.deref().x, 1.0)
        self.assertEqual(root_struct.bar, 7)
        child = root_struct.child.deref()
        self.assertEqual(child.data.deref().x, 1.0)
        self.assertEqual(child.bar, 5)
        self.assertEqual(child.data, NULLPTR)


if __name__ == '__main__':
    _ = unittest.main()
