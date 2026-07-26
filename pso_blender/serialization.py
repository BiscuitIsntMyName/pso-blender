from __future__ import annotations
from collections.abc import Callable
from functools import partial
from inspect import isclass
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Self, TypeAlias, cast, final, get_type_hints, get_args, get_origin, Annotated, TypeVar
from struct import pack_into, unpack_from, calcsize
from typing_extensions import TypeAliasType
from warnings import warn


T = TypeVar("T")
L = TypeVar("L")
S = TypeVar("S", bound="Serializable")


if TYPE_CHECKING:
    class FixedArray(list[T], Generic[T, L]):
        pass
else:
    class FixedArray(list[T], Generic[T, L]):
        def __subclasscheck__(cls, subclass):
            return type.__subclasscheck__(cls, subclass)

        def __class_getitem__(cls, params):
            T_type, L_val = params
            return TypeAliasType("FixedArray", Annotated[T_type, L_val])
        
        def __init__(*args, **kwargs):
            list.__init__(*args, **kwargs)


@dataclass
@final
class Numeric:
    """Contains numeric types"""
    NULLPTR: int = 0

    U8 = TypeAliasType("U8", int)
    U16 = TypeAliasType("U16", int)
    U32 = TypeAliasType("U32", int)
    I8 = TypeAliasType("I8", int)
    I16 = TypeAliasType("I16", int)
    I32 = TypeAliasType("I32", int)
    F32 = TypeAliasType("F32", float)
    AnyNumeric: TypeAlias = U8 | U16 | U32 | I8 | I16 | I32 | F32

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
    def format_of_types(types: list[Any]) -> str:
        fmt = ""
        for tp in types:
            fmt += Numeric.type_fmt[tp.__name__]
        return Numeric.endianness_prefix + fmt
    
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
    
    def pack_one(self, fmt: str, val: Any) -> int:
        offset_before = self.offset
        item_size = Numeric.size_of_format(fmt)
        remaining = self.capacity - self.offset
        # Grow if needed
        if item_size > remaining:
            need = item_size - remaining
            self.grow_by(need)
        pack_into(fmt, self.buffer, self.offset, val)
        self.offset += item_size
        return offset_before
    
    def pack_multiple(self, fmt: str, vals: list[Any]) -> int:
        """Returns absolute offset of where data was written"""
        offset_before = self.offset
        item_size = Numeric.size_of_format(fmt)
        remaining = self.capacity - self.offset
        # Grow if needed
        if item_size > remaining:
            need = item_size - remaining
            self.grow_by(need)
        pack_into(fmt, self.buffer, self.offset, *vals)
        self.offset += item_size
        return offset_before

    def pack(self, fmt: str, vals: Any) -> int:
        """Returns absolute offset of where data was written"""
        if hasattr(vals, "__iter__"):
            return self.pack_multiple(fmt, vals)
        else:
            return self.pack_one(fmt, vals)


@dataclass
class NumericFieldPlan:
    name: str
    tp: Any
    fmt: str
    value_getter: Callable[[Serializable], Any]
    # get_origin(tp), precomputed once when the plan is built instead of on every deserialize call
    type_origin: Any = None

@dataclass
class MultipleNumericFieldPlan:
    names: list[str]
    tp: list[Any]
    fmt: str
    value_getter: Callable[[Serializable], list[Any]]
    # get_origin(t) for each t in tp, precomputed once when the plan is built instead of on every
    # deserialize call
    type_origins: list[Any] = field(default_factory=list)

@dataclass
class DynamicNumericFieldPlan:
    name: str
    tp: Any
    fmt_getter: Callable[[Serializable], str]
    value_getter: Callable[[Serializable], list[Any]]

@dataclass
class ChildFieldPlan:
    name: str
    tp: Any
    value_getter: Callable[[Serializable], Any]

@dataclass
class DynamicChildFieldPlan:
    name: str
    tp: Any
    value_getter: Callable[[Serializable], list[Serializable]]

FieldPlan: TypeAlias = NumericFieldPlan | MultipleNumericFieldPlan | DynamicNumericFieldPlan | ChildFieldPlan | DynamicChildFieldPlan
VisitPlan: TypeAlias = list[FieldPlan]

TypeVisitor: TypeAlias = Callable[[type["Serializable"], FieldPlan, dict[str, Any]], None]
InstanceVisitor: TypeAlias = Callable[["Serializable", FieldPlan, dict[str, Any]], None]

class Serializable:
    _offset: int = -1
    _src_buf: bytearray | None = None
    
    @dataclass
    class MemberVisitResult:
        value: Any
        name: str
        tp: Any
        fmt: str
        offset: int

    _visit_plan_cache: ClassVar[dict[str, VisitPlan]] = dict()

    @staticmethod
    def _member_visitor(parent: Serializable | type[Serializable], plan: FieldPlan, ctx: dict[str, Any]):
        if isinstance(plan, NumericFieldPlan):
            value = None if isclass(parent) else plan.value_getter(parent)
            visit_result = Serializable.MemberVisitResult(value, plan.name, plan.tp, plan.fmt, ctx["size_sum"])
            ctx["members"].append(visit_result)
            ctx["size_sum"] += Numeric.size_of_format(plan.fmt)
        elif isinstance(plan, MultipleNumericFieldPlan):
            count = len(plan.names)
            values = [None] * count if isclass(parent) else plan.value_getter(parent)
            for i in range(count):
                fmt = Numeric.endianness_prefix + plan.fmt[-count + i]
                visit_result = Serializable.MemberVisitResult(values[i], plan.names[i], plan.tp[i], fmt, ctx["size_sum"])
                ctx["members"].append(visit_result)
                ctx["size_sum"] += Numeric.size_of_format(fmt)
        elif isinstance(plan, DynamicNumericFieldPlan):
            if isclass(parent):
                value = None
                fmt = ""
            else:
                value = plan.value_getter(parent)
                fmt = plan.fmt_getter(parent)
                if len(value) > 0:
                    ctx["size_sum"] += Numeric.size_of_format(fmt)
            visit_result = Serializable.MemberVisitResult(value, plan.name, plan.tp, fmt, ctx["size_sum"])
            ctx["members"].append(visit_result)
        elif isinstance(plan, DynamicChildFieldPlan):
            if isclass(parent):
                pass
            else:
                values = plan.value_getter(parent)
                for value in values:
                    ctx["size_sum"] += value.instance_size()
                visit_result = Serializable.MemberVisitResult(values, plan.name, plan.tp, "", ctx["size_sum"])
                ctx["members"].append(visit_result)
        else:
            raise Exception("Unimplemented visit plan " + plan.__class__.__name__)


    def nonnull_pointer_member_offsets(self) -> list[int]:
        members: list[Serializable.MemberVisitResult] = []
        ctx = {"size_sum": 0, "members": members}
        Serializable._visit_instance(self, ctx, Serializable._member_visitor)
        return [
            visit_result.offset for visit_result in members
            if visit_result.tp is not None and (visit_result.tp is Ptr32 or get_origin(visit_result.tp) is Ptr32) and int(visit_result.value) != Numeric.NULLPTR
        ]
    
    @classmethod
    def offset_of(cls: type[Self], member_name: str) -> int:
        members: list[Serializable.MemberVisitResult] = []
        ctx = {"size_sum": 0, "members": members}
        Serializable._visit_type(cls, ctx, Serializable._member_visitor)
        for visit_result in members:
            if visit_result.name == member_name:
                return visit_result.offset
        raise Exception("Type has no such member '{}'".format(member_name))

    def instance_size(self) -> int:
        """Will also include size of data inside any list type members."""
        members: list[Serializable.MemberVisitResult] = []
        ctx = {"size_sum": 0, "members": members}
        Serializable._visit_instance(self, ctx, Serializable._member_visitor)
        return cast(int, ctx["size_sum"])

    @classmethod
    def type_size(cls) -> int:
        """Similar to sizeof(). Size of lists is considered to be 0."""
        members: list[Serializable.MemberVisitResult] = []
        ctx = {"size_sum": 0, "members": members}
        Serializable._visit_type(cls, ctx, Serializable._member_visitor)
        return cast(int, ctx["size_sum"])
    
    @classmethod
    def _warn_unserializable(cls: type[Self], name: str | None):
        name = "<Unknown name>" if name is None else name
        warn("Serializable class \"{}\" has unserializable member \"{}\"".format(cls.__name__, name), stacklevel=2)
    
    @classmethod
    def get_visit_plan_cache_key(cls) -> str | None:
        return cls.__name__
    
    @staticmethod
    def _get_visit_plan(clazz: type[Serializable]) -> VisitPlan:
        key = clazz.get_visit_plan_cache_key()
        if key is None:
            plan = Serializable._build_visit_plan(clazz)
        else:
            plan = Serializable._visit_plan_cache.get(key)
            if plan is None:
                plan = Serializable._build_visit_plan(clazz)
                Serializable._visit_plan_cache[key] = plan
        return plan

    @staticmethod
    def _visit_instance(instance: Serializable, ctx: dict[str, Any], visitor: InstanceVisitor):
        plan = Serializable._get_visit_plan(instance.__class__)
        for item in plan:
            visitor(instance, item, ctx)
    
    @staticmethod
    def _visit_type(clazz: type[Serializable], ctx: dict[str, Any], visitor: TypeVisitor):
        plan = Serializable._get_visit_plan(clazz)
        for item in plan:
            visitor(clazz, item, ctx)
    
    @staticmethod
    def _build_visit_plan(root_value: type[Serializable]) -> VisitPlan:
        plan: VisitPlan = []
        field_types = get_type_hints(root_value, include_extras=True)
        numeric_fields_names_accum: list[str] = []
        numeric_fields_types_accum: list[Any] = []

        for (field_i, field_name) in enumerate(field_types):
            # Ignore fields with underscore prefix
            if field_name.startswith("_"):
                continue
            field_type = field_types[field_name]
            field_type_origin = get_origin(field_type)
            field_is_ptr = field_type_origin is Ptr32
            field_is_numeric = not field_is_ptr and Numeric.is_numeric_type(field_type)
            is_last = field_i == len(field_types) - 1

            if (len(numeric_fields_names_accum) > 0 and (not field_is_numeric or is_last)) or (field_is_numeric and is_last):
                # End of consecutive numeric fields
                if is_last and field_is_numeric:
                    numeric_fields_names_accum.append(field_name)
                    numeric_fields_types_accum.append(field_type)
                if len(numeric_fields_names_accum) == 1:
                    numeric_field_getter: Callable[[str, Serializable], Any] = lambda field_name, ser: ser.__dict__[field_name]
                    bound_numeric_field_getter = partial(numeric_field_getter, numeric_fields_names_accum[0])
                    field_fmt = Numeric.format_of_type(numeric_fields_types_accum[0])
                    assert(field_fmt)
                    plan.append(NumericFieldPlan(
                        numeric_fields_names_accum[0],
                        numeric_fields_types_accum[0],
                        field_fmt,
                        bound_numeric_field_getter,
                        get_origin(numeric_fields_types_accum[0])))
                else:
                    multiple_numeric_field_getter: Callable[[list[str], Serializable], list[Any]] = \
                        lambda numeric_fields, ser: [ser.__dict__[name] for name in numeric_fields]
                    bound_multiple_numeric_field_getter = partial(multiple_numeric_field_getter, numeric_fields_names_accum)
                    field_fmt = Numeric.format_of_types(numeric_fields_types_accum)
                    plan.append(MultipleNumericFieldPlan(
                        numeric_fields_names_accum,
                        numeric_fields_types_accum,
                        field_fmt,
                        bound_multiple_numeric_field_getter,
                        [get_origin(t) for t in numeric_fields_types_accum]))
                numeric_fields_names_accum = []
                numeric_fields_types_accum = []
                if is_last and field_is_numeric:
                    break
            # Determine type of field
            if field_is_ptr:
                # Pointer
                field_fmt = Numeric.format_of_type(field_type)
                assert(field_fmt)
                ptr_field_getter: Callable[[str, Serializable], Any] = lambda field_name, ser: ser.__dict__[field_name]
                bound_ptr_field_getter = partial(ptr_field_getter, field_name)
                plan.append(NumericFieldPlan(
                    field_name,
                    field_type,
                    field_fmt,
                    bound_ptr_field_getter,
                    field_type_origin))
            elif field_is_numeric:
                # Combine consecutive numeric fields
                numeric_fields_names_accum.append(field_name)
                numeric_fields_types_accum.append(field_type)
            elif field_type.__name__ == "FixedArray":
                # FixedArray
                fixarr_params = get_args(field_type.__value__)
                elem_type = fixarr_params[0]
                expected_len = cast(int, get_args(fixarr_params[1])[0])
                if Numeric.is_numeric_type(elem_type):
                    # FixedArray contains numeric elements
                    # Getter that validates array size
                    def numeric_fixed_array_getter(field_name: str, expected_len: int, ser: Serializable):
                        fields = ser.__dict__
                        field_value = fields[field_name]
                        real_len = len(field_value)
                        if real_len > expected_len:
                            # Truncate
                            warn("FixedArray member '{}' of class '{}' was truncated during serialization".format(field_name, root_value.__name__))
                            field_value = field_value[:expected_len]
                        elif real_len < expected_len:
                            # Pad with zeros
                            padding = [0] * (expected_len - real_len)
                            field_value += padding
                        return field_value
                    bound_numeric_fixed_array_getter = partial(numeric_fixed_array_getter, field_name, expected_len)
                    field_fmt = Numeric.format_of_types([elem_type] * expected_len)
                    plan.append(MultipleNumericFieldPlan(
                        [field_name] * expected_len,
                        [field_type] * expected_len,
                        field_fmt,
                        bound_numeric_fixed_array_getter,
                        [field_type_origin] * expected_len))
                else:
                    raise Exception("FixedArray member '{}' of class '{}' has unserializable element type".format(field_name, root_value.__name__))
            elif field_type_origin is list:
                # List
                elem_type = get_args(field_type)[0]
                if Numeric.is_numeric_type(elem_type):
                    # List contains numeric elements
                    fmt_getter: Callable[[str, Any, Serializable], str] = lambda field_name, elem_type, ser: \
                        cast(str, Numeric.format_of_type(elem_type, len(ser.__dict__[field_name])))
                    bound_fmt_getter = partial(fmt_getter, field_name, elem_type)
                    numeric_list_getter: Callable[[str, Serializable], Any] = lambda field_name, ser: ser.__dict__[field_name]
                    bound_numeric_list_getter = partial(numeric_list_getter, field_name)
                    plan.append(DynamicNumericFieldPlan(
                        field_name,
                        field_type,
                        bound_fmt_getter,
                        bound_numeric_list_getter))
                elif issubclass(elem_type, Serializable):
                    child_list_getter: Callable[[str, Serializable], Any] = lambda field_name, ser: ser.__dict__[field_name]
                    bound_child_list_getter = partial(child_list_getter, field_name)
                    plan.append(DynamicChildFieldPlan(
                        field_name,
                        field_type,
                        bound_child_list_getter))
                else:
                    raise Exception("List member '{}' of class '{}' has unserializable element type".format(field_name, root_value.__name__))
            elif field_type is bytearray or field_type is bytes:
                # Buffer
                buffer_fmt_getter: Callable[[str, Serializable], str] = lambda field_name, ser: \
                    cast(str, Numeric.format_of_type(Numeric.U8, len(ser.__dict__[field_name])))
                bound_buffer_fmt_getter = partial(buffer_fmt_getter, field_name)
                buffer_getter: Callable[[str, Serializable], Any] = lambda field_name, ser: ser.__dict__[field_name]
                bound_buffer_getter = partial(buffer_getter, field_name)
                plan.append(DynamicNumericFieldPlan(
                    field_name,
                    field_type,
                    bound_buffer_fmt_getter,
                    bound_buffer_getter))
            elif issubclass(field_type, Serializable):
                # Serializable
                child_getter: Callable[[str, Serializable], Any] = lambda field_name, ser: ser.__dict__[field_name]
                bound_child_getter = partial(child_getter, field_name)
                plan.append(ChildFieldPlan(
                    field_name,
                    field_type,
                    bound_child_getter))
            else:
                raise Exception("Member '{}' of class '{}' is unserializable type".format(field_name, root_value.__name__))
        return plan

    @staticmethod
    def _serializer_visitor(parent: Serializable, plan: FieldPlan, ctx: dict[str, Any]):
        buf: ResizableBuffer = ctx["buf"]
        if isinstance(plan, NumericFieldPlan):
            offset = buf.pack_one(plan.fmt, plan.value_getter(parent))
            if ctx["first_offset"] is None:
                ctx["first_offset"] = offset
        elif isinstance(plan, MultipleNumericFieldPlan):
            offset = buf.pack_multiple(plan.fmt, plan.value_getter(parent))
            if ctx["first_offset"] is None:
                ctx["first_offset"] = offset
        elif isinstance(plan, DynamicNumericFieldPlan):
            value = plan.value_getter(parent)
            if len(value) > 0:
                offset = buf.pack_multiple(plan.fmt_getter(parent), value)
                if ctx["first_offset"] is None:
                    ctx["first_offset"] = offset
        elif isinstance(plan, ChildFieldPlan):
            child = plan.value_getter(parent)
            offset = child.serialize_into(buf)
            if ctx["first_offset"] is None:
                ctx["first_offset"] = offset
        elif isinstance(plan, DynamicChildFieldPlan):  # pyright: ignore[reportUnnecessaryIsInstance]
            for child in plan.value_getter(parent):
                offset = child.serialize_into(buf)
                if ctx["first_offset"] is None:
                    ctx["first_offset"] = offset
        else:
            raise Exception("Unimplemented visit plan " + str(plan.__class__.__name__))  # pyright: ignore[reportUnreachable]

    def serialize_into(self, buf: ResizableBuffer, alignment: int | None=None) -> int:
        """Writes serializable members of this object into given buffer.
        Returns absolute offset of where data was written."""
        item = self
        ctx = {"first_offset": None, "buf": buf}
        Serializable._visit_instance(item, ctx, Serializable._serializer_visitor)
        first_offset = cast(int | None, ctx["first_offset"])
        if first_offset is None:
            raise Exception("Serialization error: Did not write anything")
        if alignment is not None:
            offset_after = buf.offset
            if offset_after % alignment != 0:
                padding = ((offset_after // alignment) + 1) * alignment - offset_after
                _ = AlignmentHelper(padding=[0] * padding).serialize_into(buf)
        return first_offset

    @staticmethod
    def _deserializer_visitor(_clazz: type[Serializable], plan: FieldPlan, ctx: dict[str, Any]):
        if isinstance(plan, NumericFieldPlan):
            sz = Numeric.size_of_format(plan.fmt)
            (deserialized, ) = unpack_from(plan.fmt, ctx["buf"], ctx["offset"])
            type_origin = plan.type_origin
            if type_origin is Ptr32:
                pointee_type = get_args(plan.tp)[0]
                ptr = Ptr32[pointee_type](deserialized)
                ptr.set_target_type(pointee_type)
                ptr.set_src_buf(ctx["buf"])
                ctx["result"].__dict__[plan.name] = ptr
            elif type_origin is list:
                ctx["result"].__dict__[plan.name].append(deserialized)
            else:
                ctx["result"].__dict__[plan.name] = deserialized
            ctx["offset"] += sz
        elif isinstance(plan, MultipleNumericFieldPlan):
            count = len(plan.names)
            values = unpack_from(plan.fmt, ctx["buf"], ctx["offset"])
            for i in range(count):
                fmt = Numeric.endianness_prefix + plan.fmt[-count + i]
                sz = Numeric.size_of_format(fmt)
                type_origin = plan.type_origins[i]
                if type_origin is list:
                    ctx["result"].__dict__[plan.names[i]].append(values[i])
                elif plan.tp[i].__name__ == "FixedArray":
                    # The default-constructed instance's field already holds its default value
                    # (e.g. Xvr.magic's default_factory) - reset it on the first element instead
                    # of appending onto it, or it ends up with default+deserialized values both.
                    if i == 0:
                        ctx["result"].__dict__[plan.names[i]] = []
                    ctx["result"].__dict__[plan.names[i]].append(values[i])
                else:
                    ctx["result"].__dict__[plan.names[i]] = values[i]
                ctx["offset"] += sz
        elif isinstance(plan, DynamicNumericFieldPlan):
            # Unsized field
            pass
        elif isinstance(plan, DynamicChildFieldPlan):
            # Unsized field
            pass
        else:
            raise Exception("Unimplemented visit plan " + str(plan.__class__.__name__))
    
    @classmethod
    def deserialize_from(cls: type[S], buf: bytearray, offset: int=0) -> tuple[S, int]:
        """Assumes class has default constructor"""
        result = cls() # Default construct
        result._offset = offset
        result._src_buf = buf
        ctx = {"result": result, "offset": offset, "buf": buf}
        Serializable._visit_type(cls, ctx, Serializable._deserializer_visitor)
        offset = cast(int, ctx["offset"])
        return (result, offset)
    
    @classmethod
    def read_sequence(cls: type[Self], buf: bytearray, offset: int, count: int) -> list[Self]:
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
    padding: list[Numeric.U8] = field(default_factory=list)


class AlignedString(Serializable):
    chars: list[Numeric.U8]
    _alignment: int

    def __init__(self, s: str, alignment: int):
        super().__init__()
        self.chars = [x for x in str.encode(s)]
        self.chars.append(0) # Null terminator
        self._alignment = alignment

    def serialize_into(self, buf: ResizableBuffer, _unused: int | None=None):  # pyright: ignore[reportIncompatibleMethodOverride]
        return super().serialize_into(buf, self._alignment)


P = TypeVar("P", bound=Serializable | Numeric.AnyNumeric)
PX = TypeVar("PX", bound=Serializable | Numeric.AnyNumeric)

class Ptr32(int, Serializable, Generic[P]):
    _src_buf: bytearray | None = None
    _target_type: type[P] | None = None
    _result_cache: P | None = None
    _arr_result_cache: list[P] | None = None

    def set_src_buf(self, buf: bytearray):
        self._src_buf = buf

    def set_target_type(self, tp: type[P]):
        self._target_type = tp

    def deref(self) -> P:
        if self._result_cache is not None:
            return self._result_cache
        if self._target_type is None:
            raise TypeError("Pointer has no target type")
        if self._src_buf is None:
            raise TypeError("Pointer has no source buffer")
        if isclass(self._target_type) and issubclass(self._target_type, Serializable):
            result = self._target_type.deserialize_from(self._src_buf, int(self))[0]
        else:
            fmt = Numeric.format_of_type(self._target_type)
            assert(fmt)
            result = cast(tuple[P], unpack_from(fmt, self._src_buf, int(self)))[0]
        self._result_cache = result
        return result

    def deref_array(self, count: int) -> list[P]:
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
            result = list(cast(tuple[P, ...], unpack_from(fmt, self._src_buf, int(self))))
        self._arr_result_cache = result
        return result

    def retype(self, new_type: type[PX]) -> "Ptr32[PX]":
        new_ptr = Ptr32[new_type](int(self))
        new_ptr.set_target_type(new_type)
        if self._src_buf is not None:
            new_ptr.set_src_buf(self._src_buf)
        return new_ptr

    def clone(self, new_value: int) -> "Ptr32[P]":
        new_ptr = Ptr32[P](new_value)
        if self._target_type is not None:
            new_ptr.set_target_type(self._target_type)
        if self._src_buf is not None:
            new_ptr.set_src_buf(self._src_buf)
        return new_ptr
