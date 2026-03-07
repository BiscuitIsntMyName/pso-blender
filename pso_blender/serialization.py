from collections.abc import Buffer, Callable, Sequence
from inspect import isclass
import sys
from dataclasses import dataclass, field
from typing import Any, Self, Sized, TypedDict, Unpack, cast, final, get_type_hints, get_args, get_origin, Annotated, override
from struct import pack_into, unpack_from, error as StructError, calcsize
from warnings import warn


def typehint_of_name(name: str, ns: Any=sys.modules[__name__]):
    ns = ns if isclass(ns) else ns.__class__
    return get_type_hints(ns).get(name)


type FixedArray[T, L] = Annotated[list[T], L]


@dataclass
@final
class Numeric:
    """Contains numeric types"""
    NULLPTR: int = 0

    type U8 = int
    type U16 = int
    type U32 = int
    type I8 = int
    type I16 = int
    type I32 = int
    type F32 = float

    type_fmt = {
        "U8": "B",
        "U16": "H",
        "U32": "L",
        "I8": "b",
        "I16": "h",
        "I32": "l",
        "F32": "f",
        "Ptr32": "L"
    }

    type_sizes = {
        "B": 1,
        "H": 2,
        "L": 4,
        "b": 1,
        "h": 2,
        "l": 4,
        "f": 4,
    }

    endianness_prefix: str = "<"

    @staticmethod
    def use_little_endian():
        Numeric.endianness_prefix = "<"
    
    @staticmethod
    def use_big_endian():
        Numeric.endianness_prefix = ">"

    @staticmethod
    def format_of_type(tp: Any, repeat: int=1) -> str | None:
        """Returns the structlib format of the given type"""
        fmt = Numeric.type_fmt.get(tp.__name__)
        if not fmt:
            return None
        if repeat == 0:
            return None
        if repeat == 1:
            return Numeric.endianness_prefix + fmt
        return Numeric.endianness_prefix + str(repeat) + fmt
    
    @staticmethod
    def size_of_format(fmt: str) -> int:
        return calcsize(fmt)
    
    @staticmethod
    def is_numeric_type(tp: Any) -> bool:
        return tp.__name__ in Numeric.type_fmt


class ResizableBuffer:
    buffer: bytearray
    capacity: int
    offset: int

    def __init__(self, *, size: int=0, buf: bytearray | None=None):
        if buf is None:
            self.buffer = bytearray(size)
        else:
            self.buffer = buf
        self.capacity = size
        self.offset = 0
    
    def grow_by(self, by: int):
        self.buffer += bytearray(by)
        self.capacity += by
    
    def grow_to(self, to: int):
        if self.capacity > to:
            raise Exception("Failed to grow ResizableBuffer because it is already bigger than requested size ({}/{})".format(self.capacity, to))
        self.grow_by(to - self.capacity)

    def append(self, other: bytearray) -> int:
        offset_before = self.offset
        self.buffer += other
        self.capacity += len(other)
        return offset_before
    
    def seek_to_end(self):
        self.offset = self.capacity

    def pack(self, fmt: str, vals: Any) -> int:
        """Returns absolute offset of where data was written"""
        offset_before = self.offset
        item_size = Numeric.size_of_format(fmt)
        remaining = self.capacity - self.offset
        # Grow if needed
        if item_size > remaining:
            need = item_size - remaining
            self.grow_by(need)
        if hasattr(vals, "__iter__"):
            pack_into(fmt, self.buffer, self.offset, *vals)
        else:
            pack_into(fmt, self.buffer, self.offset, vals)
        self.offset += item_size
        return offset_before


class Serializable:
    _offset: int = -1
    _src_buf: Buffer | None = None

    @classmethod
    def format_of_member(cls, member: str) -> str | None:
        fmt = Numeric.format_of_type(typehint_of_name(member, cls))
        if fmt:
            return fmt
        return None
    
    class VisitorArguments(TypedDict):
        value: Any
        name: str
        tp: Any
        fmt: str
        ctx: dict[str, Any]
    
    @dataclass
    class MemberVisitResult:
        value: Any
        name: str
        tp: Any
        fmt: str
        offset: int

    @staticmethod
    def _member_visitor(**kwargs: Unpack[VisitorArguments]) -> bool:
        name = kwargs["name"]
        value = kwargs["value"]
        ctx = kwargs["ctx"]
        fmt = kwargs["fmt"]
        tp = kwargs["tp"]
        visit_result = Serializable.MemberVisitResult(value, name, tp, fmt, ctx["size_sum"])
        ctx["members"].append(visit_result)
        if fmt:
            ctx["size_sum"] += Numeric.size_of_format(fmt)
        return True

    def nonnull_pointer_member_offsets(self) -> list[int]:
        members: list[Serializable.MemberVisitResult] = []
        ctx = {"size_sum": 0, "members": members}
        _ = self._visit(self, ctx, Serializable._member_visitor)
        return [
            visit_result.offset for visit_result in members
            if visit_result.tp is not None and (visit_result.tp is Ptr32 or get_origin(visit_result.tp) is Ptr32) and int(visit_result.value) != Numeric.NULLPTR
        ]
    
    @classmethod
    def offset_of(cls: type[Self], member_name: str) -> int:
        members: list[Serializable.MemberVisitResult] = []
        ctx = {"size_sum": 0, "members": members}
        _ = cls._visit(cls, ctx, Serializable._member_visitor)
        for visit_result in members:
            if visit_result.name == member_name:
                return visit_result.offset
        raise Exception("Type has no such member '{}'".format(member_name))

    def instance_size(self) -> int:
        """Will also include size of data inside any list type members."""
        members: list[Serializable.MemberVisitResult] = []
        ctx = {"size_sum": 0, "members": members}
        _ = self._visit(self, ctx, Serializable._member_visitor)
        return cast(int, ctx["size_sum"])

    @classmethod
    def type_size(cls) -> int:
        """Similar to sizeof(). Size of lists is considered to be 0."""
        members: list[Serializable.MemberVisitResult] = []
        ctx = {"size_sum": 0, "members": members}
        _ = cls._visit(cls, ctx, Serializable._member_visitor)
        return cast(int, ctx["size_sum"])
    
    @classmethod
    def _warn_unserializable(cls: type[Self], name: str | None):
        name = "<Unknown name>" if name is None else name
        warn("Serializable class \"{}\" has unserializable member \"{}\"".format(cls.__name__, name), stacklevel=2)
    
    @classmethod
    def _visit(cls: type[Self], value: Any, ctx: dict[str, Any], visitor: Callable[..., bool], *, name: str | None=None, tp: type | None=None) -> bool:
        # Ignore fields prefixed with underscore
        if name is not None and name.startswith("_"):
            return True
        # Is an instance of Serializable?
        if isinstance(value, Serializable):
            if isinstance(value, Ptr32):
                return visitor(value=value, name=name, tp=tp, fmt=Numeric.format_of_type(Ptr32), ctx=ctx)
            should_continue = True
            members = value.__dict__
            for member_name in members:
                member_value = members[member_name]
                member_type = typehint_of_name(member_name, value)
                should_continue = value._visit(member_value, ctx, visitor, name=member_name, tp=member_type)
                if not should_continue:
                    break
            return should_continue
        if type(value) is type:
            if issubclass(value, Ptr32):
                return visitor(value=value, name=name, tp=tp, fmt=Numeric.format_of_type(Ptr32), ctx=ctx)
            # Is a class object?
            elif issubclass(value, Serializable):
                should_continue = True
                members = get_type_hints(value, include_extras=True)
                for member_name in members:
                    member_value = members[member_name]
                    member_type = member_value
                    should_continue = value._visit(member_value, ctx, visitor, name=member_name, tp=member_type)
                    if not should_continue:
                        break
                return should_continue
        # Is list-like?
        value_as_sized = cast(Sized, value)
        is_list = type(value_as_sized) is list
        is_tuple = type(value_as_sized) is tuple
        type_exists = tp is not None
        is_fixed_array = type_exists and tp.__name__ == "FixedArray"
        if is_list or is_tuple or is_fixed_array:
            container_type = None
            length = 0
            elem_types = tuple()
            # Need to get typehint to get the element type
            if type_exists and is_fixed_array:
                fixarr_params = get_args(tp) # Unwrap Annotated
                elem_type = fixarr_params[0]
                expected_length = cast(int, fixarr_params[1])
                if hasattr(expected_length, "__origin__"): # Unwrap Literal
                    expected_length = cast(int, get_args(expected_length)[0])
                elem_types = (elem_type, ) * expected_length
                length = expected_length
                if is_list:
                    if len(value_as_sized) > expected_length:
                        warn("FixedArray member '{}' of class '{}' was truncated during serialization".format(name, cls.__name__))
                        value = value_as_sized[:expected_length]
                    elif len(value_as_sized) < expected_length:
                        # Pad with zeros
                        value_as_list = cast(list[int], value)
                        padding = [0] * (expected_length - len(value_as_sized))
                        value_as_list += padding
            elif name is not None:
                container_type = typehint_of_name(name, cls)
                elem_types = get_args(container_type)
                length = len(value_as_sized)
            elif tp is not None:
                container_type = tp
                elem_types = get_args(container_type)
                length = len(value_as_sized)
            if len(elem_types) < 1:
                # Can't continue
                cls._warn_unserializable(name)
                return False
            # Fast-track arrays of numeric types
            if len(elem_types) == 1 and Numeric.is_numeric_type(elem_types[0]):
                return visitor(value=value, name=name, tp=tp, fmt=Numeric.format_of_type(elem_types[0], length), ctx=ctx)
            # For non-numeric types we need to visit each element separately
            should_continue = True
            for i in range(length):
                # Get type of current element
                # Lists have one element type, tuples have n
                elem_type = elem_types[i] if is_tuple else elem_types[0]
                elem_value = cast(Any, value[i] if isinstance(value, Sequence) else None)
                # Visit element
                should_continue = cls._visit(elem_value, ctx, visitor, name=name, tp=elem_type)
                if not should_continue:
                    break
            return should_continue
        fmt = None
        if isinstance(value, Buffer):
            tp = type(value)
        elif get_origin(value) is not list and get_origin(value) is not tuple:
            # Value is primitive
            # Determine format from name or type
            if tp is not None:
                fmt = Numeric.format_of_type(tp)
            elif name is not None:
                fmt = cls.format_of_member(name)
            if fmt is None:
                # Can't continue
                cls._warn_unserializable(name)
                return False
        # Call visitor on value
        return visitor(value=value, name=name, tp=tp, fmt=fmt, ctx=ctx)
    
    @staticmethod
    def _serializer_visitor(**kwargs: Unpack[VisitorArguments]) -> bool:
        name = kwargs["name"]
        value = kwargs["value"]
        ctx = kwargs["ctx"]
        fmt = kwargs["fmt"]
        tp = kwargs["tp"]
        offset = None
        if fmt:
            try:
                offset = ctx["buf"].pack(fmt, value)
            except StructError as err:
                # Rethrow with more info
                orig_msg = err.args[0]
                raise Exception("Serialization error in member '{}' with value '{}' and type '{}': {}".format(name, value, tp, orig_msg))
        elif tp is bytes or tp is bytearray:
            ctx["buf"].append(value)
            offset = value
        if ctx["first_offset"] is None:
            ctx["first_offset"] = offset
        return True

    def serialize_into(self, buf: ResizableBuffer, alignment: int | None=None) -> int:
        """Writes serializable members of this object into given buffer.
        Returns absolute offset of where data was written."""
        item = self
        if alignment is not None:
            offset_after = buf.offset + self.instance_size()
            if offset_after % alignment != 0:
                padding = ((offset_after // alignment) + 1) * alignment - offset_after
                item = AlignmentHelper(wrapped=self, padding=[0] * padding)
        ctx = {"first_offset": None, "buf": buf}
        _ = self._visit(item, ctx, Serializable._serializer_visitor)
        first_offset = cast(int | None, ctx["first_offset"])
        if first_offset is None:
            raise Exception("Serialization error: Did not write anything")
        return first_offset

    @staticmethod
    def _deserializer_visitor(**kwargs: Unpack[VisitorArguments]) -> bool:
        name = kwargs["name"]
        ctx = kwargs["ctx"]
        fmt = kwargs["fmt"]
        tp = kwargs["tp"]
        if fmt:
            sz = Numeric.size_of_format(fmt)
            (deserialized, ) = unpack_from(fmt, ctx["buf"], ctx["offset"])
            if get_origin(tp) is Ptr32:
                pointee_type = get_args(tp)[0]
                ptr = Ptr32[pointee_type](deserialized)
                ptr.set_target_type(pointee_type)
                ptr.set_src_buf(ctx["buf"])
                ctx["result"].__dict__[name] = ptr
            elif type(ctx["result"].__dict__[name]) is list:
                ctx["result"].__dict__[name].append(deserialized)
            else:
                ctx["result"].__dict__[name] = deserialized
            ctx["offset"] += sz
        return True
    
    @classmethod
    def deserialize_from[T: Serializable](cls: type[T], buf: Buffer, offset: int=0) -> tuple[T, int]:
        """Assumes class has default constructor"""
        result = cls() # Default construct
        result._offset = offset
        result._src_buf = buf
        ctx = {"result": result, "offset": offset, "buf": buf}
        _ = cls._visit(cls, ctx, Serializable._deserializer_visitor)
        offset = cast(int, ctx["offset"])
        return (result, offset)
    
    @classmethod
    def read_sequence(cls: type[Self], buf: Buffer, offset: int, count: int) -> list[Self]:
        items: list[Self] = []
        if count < 1:
            return items
        size = cls.type_size()
        for _ in range(count):
            (item, _) = cls.deserialize_from(buf, offset)
            items.append(item)
            offset += size
        return items
    
    def get_offset(self) -> int:
        return self._offset
        

@dataclass
class AlignmentHelper(Serializable):
    wrapped: Serializable
    padding: list[Numeric.U8] = field(default_factory=list)


class AlignedString(Serializable):
    chars: list[Numeric.U8]
    _alignment: int

    def __init__(self, s: str, alignment: int):
        super().__init__()
        self.chars = [x for x in str.encode(s)]
        self.chars.append(0) # Null terminator
        self._alignment = alignment

    @override
    def serialize_into(self, buf: ResizableBuffer, _unused: int | None=None):  # pyright: ignore[reportIncompatibleMethodOverride]
        return super().serialize_into(buf, self._alignment)


class Ptr32[T: Serializable | int | float](int, Serializable):
    _src_buf: Buffer | None = None
    _target_type: type[T] | None = None
    _result_cache: T | None = None
    _arr_result_cache: list[T] | None = None

    def set_src_buf(self, buf: Buffer):
        self._src_buf = buf

    def set_target_type(self, tp: type[T]):
        self._target_type = tp

    def deref(self) -> T:
        if self._result_cache is not None:
            return self._result_cache
        if self._target_type is None:
            raise TypeError("Pointer has no target type")
        if self._src_buf is None:
            raise TypeError("Pointer has no source buffer")
        if issubclass(self._target_type, Serializable):
            result = self._target_type.deserialize_from(self._src_buf, int(self))[0]
        else:
            fmt = Numeric.format_of_type(self._target_type)
            assert(fmt)
            result = cast(tuple[T], unpack_from(fmt, self._src_buf, int(self)))[0]
        self._result_cache = result
        return result

    def deref_array(self, count: int) -> list[T]:
        if self._arr_result_cache is not None:
            return self._arr_result_cache
        if self._target_type is None:
            raise TypeError("Pointer has no target type")
        if self._src_buf is None:
            raise TypeError("Pointer has no source buffer")
        if isclass(self._target_type) and issubclass(self._target_type, Serializable):
            result = self._target_type.read_sequence(self._src_buf, int(self), count)
        else:
            fmt = Numeric.format_of_type(self._target_type, count)
            assert(fmt)
            result = list(cast(tuple[T, ...], unpack_from(fmt, self._src_buf, int(self))))
        self._arr_result_cache = result
        return result

    def retype[X: Serializable | int | float](self, new_type: type[X]) -> "Ptr32[X]":
        new_ptr = Ptr32[new_type](int(self))
        new_ptr.set_target_type(new_type)
        if self._src_buf is not None:
            new_ptr.set_src_buf(self._src_buf)
        return new_ptr
