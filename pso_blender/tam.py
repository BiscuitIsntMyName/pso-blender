from typing import final
import bpy, struct
from warnings import warn
from dataclasses import dataclass, field
from .serialization import Serializable, Numeric, ResizableBuffer
from .xvm import TextureManager


U8 = Numeric.U8
U16 = Numeric.U16
U32 = Numeric.U32
I8 = Numeric.I8
I16 = Numeric.I16
I32 = Numeric.I32
F32 = Numeric.F32
NULLPTR = Numeric.NULLPTR


@final
class FrameType:
    UNKNOWN = 1
    SLIDESHOW = 2
    TERMINATOR = 0xffff


@dataclass
class Keyframe(Serializable):
    texture_index: U16 = 0
    frame_delay: U16 = 0


# .tam files are big endian on blue burst.....
@dataclass
class TamEntry(Serializable):
    frame_type: U16 = 0
    body_size: U16 = 0
    animation_id: I16 = 0
    frame_count: U16 = 0
    frames: list[Keyframe] = field(default_factory=list)


def read(tam_path: str) -> dict[int, TamEntry]:
    """Parses a real, game-authored .tam file. Real files are NOT what write() above produces
    byte-for-byte: they use a variable-layout entry format - a frame_type other than
    SLIDESHOW/TERMINATOR (e.g. UNKNOWN) carries an opaque body_size-byte payload that isn't
    Keyframe pairs, and the real terminator is a bare 2-byte 0xffff, not a full zeroed TamEntry
    struct like write() emits. This peeks frame_type first (2 bytes) to detect the terminator
    before ever trying to read a body_size/animation_id/frame_count that isn't there, and uses
    each entry's own body_size to skip past whatever it doesn't understand rather than assuming
    every entry is a SLIDESHOW.

    Returns only SLIDESHOW entries (the only kind this addon can currently interpret), keyed by
    animation_id - the same id a HAS_TEXTURE_ANIMATION tree's TextureAnimationInfo references.
    """
    with open(tam_path, "rb") as f:
        buf = bytearray(f.read())

    Numeric.use_big_endian()
    try:
        entries: dict[int, TamEntry] = {}
        offset = 0
        while offset + 2 <= len(buf):
            (frame_type,) = struct.unpack_from(">H", buf, offset)
            if frame_type == FrameType.TERMINATOR:
                break
            if offset + 8 > len(buf):
                break  # Truncated file - bail rather than reading past the end.
            (entry, keyframes_offset) = TamEntry.deserialize_from(buf, offset)
            if entry.frame_type == FrameType.SLIDESHOW:
                entry.frames = Keyframe.read_sequence(buf, keyframes_offset, entry.frame_count)
                entries[entry.animation_id] = entry
            # body_size covers everything after the frame_type/body_size header, regardless of
            # entry type - use it to reach the next entry even for types we don't interpret.
            offset += 4 + entry.body_size
        return entries
    finally:
        Numeric.use_little_endian()


def write(tam_path: str, texture_man: TextureManager, objs: list[bpy.types.Object]):
    Numeric.use_big_endian()

    tam = ResizableBuffer(size=0)

    for obj in objs:
        anim_tex = texture_man.get_object_animated_texture(obj)
        if not anim_tex or anim_tex.animation_frames < 1:
            continue

        # xj.py's importer stashes the real per-keyframe delay it read from the original .tam as a
        # custom property on the sequence's base image (Blender's Image datablock has nowhere else
        # to keep it) - use it here so re-exporting an imported animation preserves its original
        # timing instead of collapsing every frame to a uniform 1-tick delay. Only trusted when its
        # length matches the current frame count: a user adding/removing frames in the sequence
        # (e.g. via the file browser) invalidates the stashed per-index mapping.
        stashed_delays = anim_tex.image.get("pso_tam_frame_delays")
        if stashed_delays is not None and len(stashed_delays) != anim_tex.animation_frames:
            warn("TAM Warning: stashed frame delays for '{}' don't match its current frame count "
                 "({} vs {}) - falling back to a uniform 1-tick delay for this animation.".format(
                     anim_tex.image.name, len(stashed_delays), anim_tex.animation_frames))
            stashed_delays = None

        # Each frame's real (possibly shared) texture id, resolved from the export's content
        # registry - NOT anim_tex.id + i, which assumed every animation's frames occupy a
        # contiguous block of ids that nothing else could ever share. A frame reused byte-for-byte
        # by another animation now correctly points at that shared id instead of getting its own
        # redundant copy (see xvm.TextureRegistry).
        frame_ids = texture_man.get_animated_texture_frame_ids(anim_tex)
        base_id = texture_man.get_base_id()
        frames: list[Keyframe] = []
        for i in range(anim_tex.animation_frames):
            delay = int(stashed_delays[i]) if stashed_delays is not None else 1
            frames.append(Keyframe(texture_index=frame_ids[i] - base_id, frame_delay=delay))

        entry = TamEntry(
            frame_type=FrameType.SLIDESHOW,
            body_size=Keyframe.type_size() * len(frames) + 4,
            # animation_instance_id, NOT anim_tex.id - see the matching comment in n_rel.py
            # (_write_impl) for why these two must stay unique per placement, independent of
            # whether the underlying texture content is shared with another placement.
            animation_id=anim_tex.animation_instance_id & 0x7fff,
            frame_count=len(frames),
            frames=frames)
        
        _ = entry.serialize_into(tam)

    _ = TamEntry(frame_type=FrameType.TERMINATOR).serialize_into(tam)

    with open(tam_path, "wb") as f:
        _ = f.write(tam.buffer)

    Numeric.use_little_endian()
