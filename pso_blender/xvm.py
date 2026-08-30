from typing import Any, Literal, cast, final
import os, pathlib, marshal, json, hashlib, warnings, time
from dataclasses import dataclass, field
import bpy
import bpy.types
from mathutils import Vector, Matrix, Euler
from .serialization import Serializable, Numeric, ResizableBuffer, FixedArray
from .util import magic_bytes, Texture, get_object_diffuse_textures, find_material_img_group_tree
from .xj_material_properties_menu import MaterialWithXjSettings, AlphaCompression
from .iff import IffHeader


from . import xvm_dxt  # pyright: ignore[reportImplicitRelativeImport]


U8 = Numeric.U8
U16 = Numeric.U16
U32 = Numeric.U32
I8 = Numeric.I8
I16 = Numeric.I16
I32 = Numeric.I32
F32 = Numeric.F32
NULLPTR = Numeric.NULLPTR


# Can't figure out how to get all of the frames out of an image sequence shader node, so we need to do it like this
def get_image_sequence_images(img: bpy.types.Image) -> list[bpy.types.Image]:
    # filepath_from_user() resolves to whatever frame Blender's CURRENT scene frame + default
    # ImageUser (frame_start=1, frame_offset=0) maps to - not the custom frame_start=0/
    # frame_offset=-1 that lives on this sequence's specific node (get_or_create_texture_node_
    # group, xj.py), which filepath_from_user() has no way to know about since ImageUser belongs
    # to the node, not the Image datablock. That mismatch resolves to frame 1 instead of frame 0
    # as soon as the sequence has actually been evaluated for display (e.g. Material Preview
    # shading) - confirmed via a live Blender session, every animated texture's filepath pointed
    # at "1.png" instead of "0.png" after switching to Material Preview and reading back
    # img.filepath_from_user(). filepath (no _from_user) is the static, load-time path - "0.png",
    # as originally passed to bpy.data.images.load() - and is never re-resolved based on the
    # current frame, so it's immune to this entirely.
    dirname = os.path.dirname(bpy.path.abspath(img.filepath))
    frame_names: list[str] = []
    for item in os.listdir(dirname):
        if os.path.isfile(os.path.join(dirname, item)):
            frame_names.append(item)
    return [
        # check_existing: re-running export against the same sequence directory (e.g. repeated
        # test exports) reuses the datablocks already loaded instead of piling up fresh duplicates
        # every time.
        bpy.data.images.load(os.path.join(dirname, name), check_existing=True)
        # Sort filenames in numeric order
        for name in sorted(frame_names, key=lambda key: int(os.path.basename(os.path.splitext(key)[0])))]


# Keyed by the live SEQUENCE image's own name -> the NAME (not a direct object reference - a
# reloaded .blend/file, e.g. bpy.ops.wm.read_homefile, frees every existing Image datablock,
# leaving any cached reference to one dangling and raising ReferenceError on next access) of an
# independent, non-SEQUENCE copy of its frame 0 file. Shared by every caller that needs stable
# pixels for a SEQUENCE-source image (make_xvr, texture_checksum) so the substitute is loaded once
# and reused, not once per call site.
_stable_sequence_images: dict[str, str] = {}


def get_stable_source_image(image: bpy.types.Image) -> bpy.types.Image:
    """Reading .pixels/.size/.channels/.alpha_mode directly off a SEQUENCE-source image is
    unreliable: Blender buffers whichever frame the scene's CURRENT timeline frame resolves to for
    that image (and only starts doing so once it's been evaluated for display, e.g. Material
    Preview) - not necessarily frame 0. Confirmed live: toggling Material Preview changed an
    otherwise-untouched animated texture's exported content in both the full REL export (via
    TextureManager, fixed once already) AND the standalone "Export XVM" operator - the latter
    builds its own Texture objects directly (util.get_object_diffuse_textures), bypassing whatever
    TextureManager does entirely, so a per-caller fix doesn't cover it. Centralizing the fix here,
    where the actual unstable read happens (make_xvr, texture_checksum), covers every current and
    future caller instead of requiring each one to pre-stabilize its own Texture objects. No-op for
    any image whose source isn't SEQUENCE."""
    if image.source != "SEQUENCE":
        return image
    stable_name = _stable_sequence_images.get(image.name)
    if stable_name is not None:
        existing = bpy.data.images.get(stable_name)
        if existing is not None:
            return existing
    stable = bpy.data.images.load(bpy.path.abspath(image.filepath), check_existing=False)
    stable.name = "{}_stable_frame0".format(image.name)
    _stable_sequence_images[image.name] = stable.name
    return stable


@final
class XvrFormat:
    A8R8G8B8 = 1
    R5G6B5 = 2
    A1R5G5B5 = 3
    A4R4G4B4 = 4
    P8 = 5
    DXT1 = 6
    DXT2 = 7
    DXT3 = 8
    DXT4 = 9
    DXT5 = 10
    # Duplicates
    #A8R8G8B8 = 11
    #R5G6B5 = 12
    #A1R5G5B5 = 13
    #A4R4G4B4 = 14
    YUY2 = 15
    V8U8 = 16
    A8 = 17
    X1R5G5B5 = 18
    X8R8G8B8 = 19


# Empirically measured (not a documented part of the format): the fixed size of the all-zero
# trailer real game .xvm files append after a texture's data when its MIPMAPS flag is set.
MIPMAP_TAIL_SIZE_BY_FORMAT = {
    XvrFormat.DXT1: 22,
    XvrFormat.DXT2: 43,
    # Not independently measured against a real file (no real Ephinea .xvm uses DXT3) - assumed
    # equal to DXT2's tail size since DXT2 and DXT3 share an identical block byte layout, and the
    # tail is a property of that layout, not of the alpha semantics.
    XvrFormat.DXT3: 43,
}


@final
class XvrFlags:
    MIPMAPS = 1
    ALPHA = 2


@dataclass
class Xvr(Serializable):
    magic: FixedArray[U8, Literal[4]] = field(default_factory=lambda: FixedArray(magic_bytes("XVRT")))
    body_size: U32 = 0
    flags: U32 = 0
    format: U32 = 0
    id: U32 = 0
    width: U16 = 0
    height: U16 = 0
    data_size: U32 = 0
    unk1: U32 = 0
    unk2: U32 = 0
    unk3: U32 = 0
    unk4: U32 = 0
    unk5: U32 = 0
    unk6: U32 = 0
    unk7: U32 = 0
    unk8: U32 = 0
    unk9: U32 = 0
    data: list[U8] = field(default_factory=list)

XVM_ITEM_ALIGNMENT = 64

@dataclass
class Xvm(Serializable):
    magic: FixedArray[U8, Literal[4]] = field(default_factory=lambda: FixedArray(magic_bytes("XVMH")))
    body_size: U32 = 0
    xvr_count: U32 = 0
    unk1: U32 = 0
    unk2: U32 = 0
    unk3: U32 = 0
    unk4: U32 = 0
    unk5: U32 = 0
    unk6: U32 = 0
    unk7: U32 = 0
    unk8: U32 = 0
    unk9: U32 = 0
    unk10: U32 = 0
    unk11: U32 = 0
    unk12: U32 = 0
    unk13: U32 = 0
    xvrs: list[Xvr] = field(default_factory=list)
    _filename: str = ""
    _full_path: str = ""

    def set_filename(self, filename: str):
        self._filename = filename

    def get_filename(self) -> str:
        return self._filename

    def set_full_path(self, path: str):
        self._full_path = path

    def get_full_path(self) -> str:
        return self._full_path

class TextureManager:
    _base_id: int
    _registry: "TextureRegistry"
    # Per animated base texture (keyed by its own .name, same as before), the ordered list of its
    # frames' final ids (post content-dedup, so a frame shared with a different animation_id's
    # identical content already reflects that) - tam.write() needs these directly instead of
    # assuming a contiguous id range starting at the base texture's own id (see
    # get_animated_texture_frame_ids).
    _frame_ids_by_base_name: dict[str, list[int]]
    _has_anim_tex: bool = False
    # Tracked by name, not by Image reference: the same animated texture is shared by many
    # objects, so this list can and does accumulate the same frame more than once (each already
    # deduplicated to one datablock via check_existing) - removing by name lets a second, stale
    # occurrence be a harmless no-op lookup instead of touching an already-freed struct.
    _ephemeral_frame_images: list[str]

    def __init__(self, objects: list[bpy.types.Object]):
        # Create "unique" texture IDs
        self._base_id = int(time.time()) & 0xffffffff
        self._registry = TextureRegistry(self._base_id)
        self._frame_ids_by_base_name = dict()
        # Frame images loaded below purely to read their pixels into the exported .xvm - not
        # referenced by any material/node, so nothing else keeps them alive once export is done.
        # Tracked here so cleanup_ephemeral_images() can remove them afterward instead of leaving
        # them to accumulate in bpy.data.images across repeated exports.
        self._ephemeral_frame_images = []

        get_object_diffuse_textures.cache_clear()

        # A fresh, always-unique id per animated placement (one per object, assigned below) -
        # deliberately separate from the content-deduped .id a Texture gets from _registry, so two
        # placements whose frames happen to be byte-identical still get their own .tam entry (their
        # playback timing is a genuine per-instance property, independent of texture content).
        animation_instance_id_counter = self._base_id

        # First find all textures in given objects
        all_textures: list[Texture] = []
        # Frame-expansion order per base texture, tracked separately from all_textures (which gets
        # sorted below) - get_animated_texture_frame_ids() needs each animation's frames in actual
        # playback order (frame 0, 1, 2, ...), not final array order.
        frames_by_base_name: dict[str, list[Texture]] = {}
        for obj in objects:
            textures = get_object_diffuse_textures(obj)
            for tex in textures:
                if tex.image.source == "SEQUENCE":
                    self._has_anim_tex = True
                    # Get animated textures
                    frames = get_image_sequence_images(tex.image)
                    tex.animation_frames = len(frames)
                    tex.animation_instance_id = animation_instance_id_counter
                    animation_instance_id_counter += 1
                    orig_seq_image = tex.image
                    all_textures.append(tex)
                    frames_by_base_name[tex.name] = [tex]
                    for i, frame in enumerate(frames):
                        # check_existing (see get_image_sequence_images) means frame 0 resolves to
                        # the exact same datablock as tex.image itself (loaded from the identical
                        # file path at import time), still source == 'SEQUENCE' - already covered
                        # by `tex` above (which keeps its real identity/animation_frames/custom
                        # properties intact), nothing more to do for it here. The instability this
                        # used to work around here (reading .pixels straight off a SEQUENCE image
                        # depends on Blender's current scene frame / whether it's been evaluated
                        # for display, e.g. Material Preview) is now handled once, centrally, in
                        # make_xvr()/texture_checksum() via get_stable_source_image() - which also
                        # covers the standalone "Export XVM" operator, since it builds its own
                        # Texture objects directly and never went through this __init__ at all
                        # (confirmed live: Export XVM's animated-texture content still changed with
                        # Material Preview toggled, even after this exact case was fixed here).
                        if frame == orig_seq_image:
                            continue
                        frame.name = "{}_frame{}".format(orig_seq_image.name, i)
                        self._ephemeral_frame_images.append(frame.name)
                        # Same original-order sort key as the base/frame-0 image (which
                        # already carries this, stamped at import - see make_material and
                        # get_or_build_animated_texture_image in xj.py) - a frame image has no
                        # tex_id of its own, but should still sort adjacent to its animation's
                        # other frames instead of falling back to alphabetical.
                        orig_tex_id = orig_seq_image.get("pso_orig_tex_id")
                        if orig_tex_id is not None:
                            frame["pso_orig_tex_id"] = orig_tex_id
                        frame_tex = Texture(id=-1, material_name=tex.material_name, generate_mipmaps=tex.generate_mipmaps, image=frame)
                        all_textures.append(frame_tex)
                        frames_by_base_name[tex.name].append(frame_tex)
                else:
                    all_textures.append(tex)

        # Preserve any original texture-array position the current scene doesn't reference at all.
        # TEXTURE_ID is a raw array index (xj_xvm.xvrs[tex_id], xj.py:1343) - silently dropping an
        # unreferenced position would shift every later one, corrupting anything else that indexes
        # into this same source .xvm by original position (another map segment, or c.rel/r.rel,
        # sharing it). Confirmed real on map_acity00: 21 of 246 original positions have no
        # material/image anywhere in the scene, even at import time - genuinely orphaned as far as
        # this map segment's own geometry is concerned, but still real content elsewhere.
        source_xvm_path = _find_common_source_xvm_path(all_textures)
        if source_xvm_path is not None:
            # Two reads of the same file: decoded (read(), cached by mtime/size - cheap to call
            # again) to build a real Image for the pixel-existence check and content-registry
            # checksum below, and raw (read_raw(), never re-encoded) to carry the *exact original
            # compressed bytes* through untouched via Texture.prebuilt_xvr - decoding and
            # recompressing content nothing edited would be pure, avoidable DXT1 loss (confirmed
            # live: a freshly recompressed passthrough measurably differed from the original), and
            # would silently convert any raw R5G6B5/A1R5G5B5 original into DXT since make_xvr()
            # can't re-encode those formats at all.
            decoded_base_xvm = read(source_xvm_path)
            raw_base_xvm = read_raw(source_xvm_path)
            covered_positions: set[int] = set()
            for tex in all_textures:
                orig_id = tex.image.get("pso_orig_tex_id")
                if orig_id is not None:
                    covered_positions.add(int(orig_id))
            base_name = os.path.splitext(os.path.basename(source_xvm_path))[0]
            for i, base_xvr in enumerate(decoded_base_xvm.xvrs):
                if i in covered_positions:
                    continue
                passthrough_img = bpy.data.images.new(
                    "{}_xvr_{}_orig".format(base_name, i),
                    width=base_xvr.width, height=base_xvr.height,
                    alpha=bool(base_xvr.flags & XvrFlags.ALPHA))
                passthrough_img.pixels = base_xvr.data  # pyright: ignore[reportAttributeAccessIssue]
                passthrough_img["pso_orig_tex_id"] = i
                # Not referenced by any material/node - cleaned up the same way as the per-frame
                # animated images (see cleanup_ephemeral_images), not left to accumulate.
                self._ephemeral_frame_images.append(passthrough_img.name)
                passthrough_tex = Texture(
                    id=-1, material_name="", generate_mipmaps=bool(base_xvr.flags & XvrFlags.MIPMAPS),
                    image=passthrough_img)
                passthrough_tex.prebuilt_xvr = raw_base_xvm.xvrs[i]
                all_textures.append(passthrough_tex)

        # Sort textures close to their original relative position in the source .xvm (tracked via
        # pso_orig_tex_id, stashed on import - see make_material/get_or_build_animated_texture_
        # image in xj.py) instead of purely alphabetically by material name, which restructured
        # every texture's position on every single export regardless of whether anything was
        # actually edited. Textures with no original id (freshly authored, never imported) sort
        # after every originally-ordered one, by material name as before - the previous behavior,
        # unchanged for that case.
        def texture_sort_key(tex: Texture) -> tuple[int, int, str]:
            orig_id = tex.image.get("pso_orig_tex_id")
            if orig_id is None:
                return (1, 0, tex.material_name)
            return (0, int(orig_id), tex.material_name)
        all_textures.sort(key=texture_sort_key)

        # Deduplicate by actual content (texture_checksum - pixels, mipmap flag, Mapping
        # transform, alpha_compression: everything that affects the final compressed bytes), not
        # by image path - two different animation_id's that happen to play byte-identical frames
        # must collapse to one shared texture id, matching how the original game's own files never
        # duplicate texture content (confirmed on real map data: 0 duplicates across 246 entries),
        # no matter how many places one animation is placed. See TextureRegistry above.
        for tex in all_textures:
            w, h = tex.image.size
            # If the image file is not found on disk the texture will still exist but without pixels
            if w == 0 or h == 0 or len(tex.image.pixels) < 1:  # pyright: ignore[reportArgumentType]
                raise Exception("Error in texture '{}': Texture has no pixels. Does the image file exist on disk?".format(tex.image.filepath))
            self._registry.register(tex)  # Mutates tex.id in place, even when tex ends up a duplicate.

        # Now that every Texture's .id reflects its real (possibly shared) content id, record each
        # animation's frame ids in playback order for get_animated_texture_frame_ids() below.
        for base_name, frame_texs in frames_by_base_name.items():
            self._frame_ids_by_base_name[base_name] = [t.id for t in frame_texs]

    def get_object_textures(self, obj: bpy.types.Object) -> list[Texture]:
        # Multiple material variants (different blend mode/addressing) of the same physical
        # texture can share one underlying image, deduplicated by content in __init__ - only the
        # FIRST material encountered for a given content gets registered as canonical, and its
        # .material_name reflects that first material only. Returning that shared instance as-is
        # here would silently carry the wrong .material_name for every other object/material using
        # the same content - write_index_buffers matches strips to textures by material name, so a
        # wrong .material_name there causes a strip's texture lookup to fail (dropped texture) or,
        # if the wrong name happens to also collide with a different real material on this same
        # object, latch onto that unrelated texture instead. get_object_diffuse_textures(obj)
        # already builds a fresh Texture per this object's own material slots with the correct
        # .material_name - only .id needs to come from the deduplicated/canonical entry (the actual
        # shared texture id every variant must agree on).
        textures: list[Texture] = []
        all_textures = get_object_diffuse_textures(obj)
        for tex in all_textures:
            canonical = self._registry.get_by_checksum(tex)
            if canonical is not None:
                tex.id = canonical.id
                textures.append(tex)
        return textures

    def get_animated_texture_frame_ids(self, anim_tex: Texture) -> list[int]:
        """The real, possibly-shared texture id for each of anim_tex's frames, in playback order -
        tam.write() must reference these directly instead of assuming a contiguous id range
        starting at anim_tex.id (frame content is now deduped independently of animation
        instance - see TextureRegistry / Texture.animation_instance_id)."""
        return self._frame_ids_by_base_name[anim_tex.name]

    def get_all_textures(self) -> list[Texture]:
        return self._registry.canonical_textures()

    def get_base_id(self) -> int:
        return self._base_id

    def has_textures(self) -> bool:
        return len(self._registry.canonical_textures()) > 0
    
    def object_has_textures(self, obj: bpy.types.Object) -> bool:
        return len(get_object_diffuse_textures(obj)) > 0
    
    def has_animated_textures(self) -> bool:
        return self._has_anim_tex
    
    def get_object_animated_texture(self, obj: bpy.types.Object) -> Texture | None:
        # animation_frames, not tex.image.source == "SEQUENCE": both work today, but
        # animation_frames is the more direct signal - it's set once, right here in __init__, on
        # the Texture object itself, rather than being inferred from whatever Image .image happens
        # to point at.
        for tex in self.get_object_textures(obj):
            if tex.animation_frames > 0:
                return tex
        return None

    def cleanup_ephemeral_images(self):
        """Removes the per-frame images loaded solely to read animated textures' pixel data into
        the exported .xvm (see __init__) - they're not referenced by any material/node, so nothing
        else keeps them alive, and leaving them in bpy.data.images just accumulates orphaned
        datablocks across repeated exports. The users == 0 check is what makes this safe even if a
        frame happens to double as a real, in-use image somewhere else (e.g. shared with another
        animation) - only genuinely unreferenced images are removed."""
        for name in self._ephemeral_frame_images:
            img = bpy.data.images.get(name)
            if img is not None and img.users == 0:
                bpy.data.images.remove(img)
        self._ephemeral_frame_images = []


# Bump whenever make_xvr()'s encoding (mip generation, DXT compression, alpha handling, etc.)
# changes in a way that alters output bytes for otherwise-unchanged input - texture_checksum has
# no other way to know the cached .xvr was written by an older, since-improved version of that
# code, so a stale cache entry would otherwise keep being served forever (see write()'s cache
# lookup) even after a fix. Confirmed as the cause of REL exports (which go through write()'s
# cache) looking pixelated/lower quality than a standalone Export XVM (which always calls
# make_xvr() directly, bypassing the cache) on maps exported before the gamma-correct mip
# averaging / DXT1 punch-through fixes landed.
CACHE_FORMAT_VERSION = 13


def texture_checksum(tex: Texture) -> str:
    # Mirrors make_xvr()'s own pixel-source logic exactly (bake_texture_group first, tex.image
    # otherwise) - a previous version of this function read straight from the material's shared
    # ImgGroup node tree whenever one existed, regardless of tex.image, which silently computed the
    # SAME checksum for every distinct frame Texture of one animation (they all share one
    # material_name/group_tree) - confirmed live: a 3-frame animation's frames all collapsed onto
    # one shared texture_index instead of the 3 distinct ones they need. bake_texture_group()
    # itself already has a cheap early-exit for the common "not customized" case (most textures),
    # so calling it here too costs nothing extra there.
    mat = bpy.data.materials.get(tex.material_name)
    group_tree = find_material_img_group_tree(mat) if mat is not None else None
    img_width, img_height = tex.image.size
    baked = bake_texture_group(group_tree, img_width, img_height) if group_tree is not None else None
    data: list[float] = [float(CACHE_FORMAT_VERSION)]
    if baked is not None:
        data.extend(baked)
    else:
        data.extend(list(cast(Any, get_stable_source_image(tex.image)).pixels))
    data.append(float(tex.generate_mipmaps))
    # make_xvr() also bakes in the material's Mapping node transform - without folding that into
    # the checksum too, editing only the Mapping node (not the image) would leave the checksum
    # unchanged and the cache would keep serving a stale .xvr baked from before the edit.
    if mat is not None:
        transform = get_material_mapping_transform(mat)
        if transform is not None:
            data.extend(float(v) for row in transform for v in row)
        # Flipping alpha_compression (Auto/Force DXT1/Force DXT3) changes which DXT format
        # make_xvr() picks without touching any pixel - without this, the cache would keep
        # serving a stale .xvr compressed under the old format.
        data.append(float(AlphaCompression[cast(MaterialWithXjSettings, mat).xj_settings.alpha_compression].value))
        # Same reasoning - toggling force_uncompressed switches the whole format (DXT vs raw
        # A8R8G8B8) without touching a single pixel.
        data.append(float(cast(MaterialWithXjSettings, mat).xj_settings.force_uncompressed))
    return hashlib.md5(marshal.dumps(data)).hexdigest()


def get_original_pso_id_and_source(material_name: str) -> "tuple[int, str] | None":
    """Recovers the exact Xvr.id a material's texture had, and the full path of the .xvm it was
    originally imported from (xj.py's importer stores both on the material as
    xj_settings.pso_id / xj_settings.source_xvm_path). Shared by ExportXvm (standalone - needs to
    carry an existing .xvm's untouched slots through unchanged) and TextureManager (REL export -
    same need, see the position-preserving pass in __init__ below)."""
    mat = bpy.data.materials.get(material_name)
    if mat is None:
        return None
    settings = cast(MaterialWithXjSettings, mat).xj_settings
    if settings.pso_id < 0 or not settings.source_xvm_path:
        return None
    return (settings.pso_id, settings.source_xvm_path)


def _find_common_source_xvm_path(textures: "list[Texture]") -> "str | None":
    """The single .xvm this export's materials were all originally imported from, if there is
    exactly one and it's unambiguous - used to preserve untouched original texture-array positions
    (see TextureManager.__init__ below). Mirrors ExportXvm's own ambiguity handling: silently skips
    (falls back to today's scene-only behavior) rather than guessing, whenever there's no
    resolvable source (freshly authored content) or more than one (e.g. multiple maps loaded into
    the same Blender file at once)."""
    source_paths: set[str] = set()
    for tex in textures:
        resolved = get_original_pso_id_and_source(tex.material_name)
        if resolved is not None:
            source_paths.add(resolved[1])
    if len(source_paths) != 1:
        return None
    path = next(iter(source_paths))
    return path if os.path.isfile(path) else None


class TextureRegistry:
    """Content-addressed dedup for one export run - two Texture objects with the same
    texture_checksum() (same pixels, mipmap flag, Mapping transform, and alpha_compression, i.e.
    everything that actually affects the compressed bytes) are the same texture regardless of which
    material/animation/placement reached them, and must share one id. Reused by both TextureManager
    (REL map export) and ExportXvm (standalone) instead of each maintaining its own, weaker,
    identity-based dedup (by image path, or by pso_id+image name) - confirmed on real map data that
    those miss real duplicates (e.g. two different animation_id's that happen to play byte-identical
    frames), while the original game's own .xvm files never duplicate texture content at all."""
    _next_id: int
    _by_checksum: dict[str, Texture]

    def __init__(self, base_id: int):
        self._next_id = base_id
        self._by_checksum = {}

    def register(self, tex: Texture) -> Texture:
        """Assigns tex.id - reusing an existing entry's id if this exact content was already seen,
        or minting a fresh one otherwise - and returns the canonical Texture for this content
        (either tex itself, or the earlier entry it now duplicates)."""
        checksum = texture_checksum(tex)
        existing = self._by_checksum.get(checksum)
        if existing is not None:
            tex.id = existing.id
            return existing
        tex.id = self._next_id
        self._next_id += 1
        self._by_checksum[checksum] = tex
        return tex

    def get_by_checksum(self, tex: Texture) -> Texture | None:
        return self._by_checksum.get(texture_checksum(tex))

    def canonical_textures(self) -> list[Texture]:
        return list(self._by_checksum.values())


def load_cache_index(path: str) -> dict[str, str]:
    if os.path.isfile(path):
        with open(path, "r") as f:
            return json.load(f)
    return dict()


def save_cache_index(path: str, index: dict[str, str]):
    with open(path, "w") as f:
        json.dump(index, f)


def get_cached_xvr(path: str) -> Xvr:
    print("XVM Notice: Loading texture from cache '{}'".format(path))
    with open(path, "rb") as f:
        file_contents = bytearray(f.read())
        (xvr, offset) = Xvr.deserialize_from(file_contents)
        xvr.data = file_contents[offset:]  # pyright: ignore[reportAttributeAccessIssue]
    return xvr


def cache_xvr(path: str, xvr: Xvr):
    buf = ResizableBuffer(size=0)
    _ = xvr.serialize_into(buf)
    with open(path, "wb") as f:
        print("XVM Notice: Saving texture to cache '{}'".format(path))
        _ = f.write(buf.buffer)


MIP_MIN_DIM = 4


def _srgb_to_linear(c: float) -> float:
    if c <= 0.04045:
        return c / 12.92
    return ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    if c <= 0.0:
        return 0.0
    if c <= 0.0031308:
        return c * 12.92
    return 1.055 * (c ** (1.0 / 2.4)) - 0.055


# image.pixels are sRGB-encoded (gamma-compressed), not linear light - precomputed once per
# 8-bit level (matching the precision DXT compression quantizes to anyway, see
# _DECOMPOSE_RGB565_TABLE in xvm_dxt.py) since downsampling calls this 4x per output texel per
# color channel, and it's on the hot path for every mip level of every mipmapped texture.
_SRGB_TO_LINEAR_TABLE: tuple[float, ...] = tuple(_srgb_to_linear(i / 255.0) for i in range(256))


def downsample_pixels_2x(pixels: list[float], width: int, height: int, channels: int) -> tuple[list[float], int, int]:
    """2x2 box filter, halving both dimensions. Operates on plain pixel lists rather than
    Blender Image datablocks - Image.copy() + Image.scale() was tried first but produced blank
    (all-black) results, at least in a --background context.

    Averages color channels (not alpha) in linear light rather than directly on the sRGB-encoded
    source values - a plain arithmetic mean of gamma-encoded values isn't the same as the true
    (linear-light) average, and systematically under-represents small, bright, high-contrast
    detail (a specular highlight or glow against a darker background) at each successive mip
    level, making such effects look artificially dimmer at a distance in-game than up close.
    """
    new_width, new_height = width // 2, height // 2
    result = [0.0] * (new_width * new_height * channels)
    color_channels = min(channels, 3)
    for y in range(new_height):
        src_y = y * 2
        for x in range(new_width):
            src_x = x * 2
            dst_i = (y * new_width + x) * channels
            for c in range(channels):
                is_color = c < color_channels
                total = 0.0
                for dy in range(2):
                    row_i = (src_y + dy) * width
                    for dx in range(2):
                        val = pixels[(row_i + src_x + dx) * channels + c]
                        if is_color:
                            val = _SRGB_TO_LINEAR_TABLE[max(0, min(255, int(val * 255.0)))]
                        total += val
                avg = total / 4.0
                result[dst_i + c] = _linear_to_srgb(avg) if is_color else avg
    return result, new_width, new_height


def generate_mip_levels(pixels: list[float], width: int, height: int, channels: int) -> list[tuple[list[float], int, int]]:
    """Box-filtered mip pyramid, stopping once a level reaches 4x4 (the smallest a DXT block can
    encode)."""
    levels: list[tuple[list[float], int, int]] = []
    cur_pixels, w, h = pixels, width, height
    while w > MIP_MIN_DIM and h > MIP_MIN_DIM:
        cur_pixels, w, h = downsample_pixels_2x(cur_pixels, w, h, channels)
        levels.append((cur_pixels, w, h))
    return levels


# How dark a fully-tilted normal-map pixel / fully-metal pixel can push a diffuse pixel to, at
# most. Fixed defaults for now - can become tunable once the effect's been seen on real assets.
# Read into the relief-composite node graph's default values at creation time (see
# xj._wire_relief_composite) rather than hand-copied there, so there's exactly one place these
# numbers are defined even though the formula itself is expressed as nodes, not Python.
_RELIEF_MIN_DARKEN = 0.6
_METAL_MAX_DARKEN = 0.7


_MAPPING_IDENTITY_LOCATION = (0.0, 0.0, 0.0)
_MAPPING_IDENTITY_ROTATION = (0.0, 0.0, 0.0)
_MAPPING_IDENTITY_SCALE = (1.0, 1.0, 1.0)


def get_material_mapping_transform(mat: bpy.types.Material) -> Matrix | None:
    """The transform of a material's Mapping node (Location/Rotation/Scale), or None if there's
    no Mapping node or it's still at its default identity values (nothing to bake).

    The actual PSO file format has no concept of a UV transform - the game just samples the
    texture with the mesh's raw UV. So a Mapping node someone dials in by hand in Blender (to
    preview a different tiling/offset/rotation) only affects the Blender viewport unless its
    effect is baked into the exported texture's pixels themselves.
    """
    if mat.node_tree is None:
        return None
    mapping_node = next((n for n in mat.node_tree.nodes if n.type == "MAPPING"), None)
    if mapping_node is None:
        # No inline Mapping node - it may be inside the shared per-texture group instead (see
        # get_or_create_mapping_node_group in xj.py), same two-level search as
        # util.find_diffuse_image uses for the shared Image Texture node.
        for node in mat.node_tree.nodes:
            if node.type != "GROUP":
                continue
            group_node_tree = cast(bpy.types.ShaderNodeGroup, node).node_tree
            if group_node_tree is None:
                continue
            mapping_node = next((n for n in group_node_tree.nodes if n.type == "MAPPING"), None)
            if mapping_node is not None:
                break
    if mapping_node is None:
        return None
    location = cast(Any, mapping_node.inputs["Location"]).default_value
    rotation = cast(Any, mapping_node.inputs["Rotation"]).default_value
    scale = cast(Any, mapping_node.inputs["Scale"]).default_value
    if (tuple(location) == _MAPPING_IDENTITY_LOCATION
            and tuple(rotation) == _MAPPING_IDENTITY_ROTATION
            and tuple(scale) == _MAPPING_IDENTITY_SCALE):
        return None
    # Matches the "Point" mapping type's own semantics (the default, and the only type this
    # importer ever creates): scale first, then rotate, then translate.
    return Matrix.LocRotScale(Vector(location), Euler(rotation, "XYZ"), Vector(scale))


def _is_default_texture_group_wiring(group_tree: bpy.types.ShaderNodeTree) -> bool:
    """True if the shared ImgGroup's Color output is still linked directly from the diffuse
    (PSO_Diffuse) Image Texture node - i.e. nothing (no relief composite, no other manual node
    edit) has been wired in between. Named-based check, not identity (`is`) - separate attribute
    accesses on the same underlying node can return distinct Python wrapper objects in bpy, so
    `from_node is diffuse_node` is not a reliable comparison, only `.name` is."""
    group_output = next((n for n in group_tree.nodes if n.type == "GROUP_OUTPUT"), None)
    if group_output is None:
        return True
    color_input = group_output.inputs.get("Color")
    if color_input is None or not color_input.is_linked:
        return True
    return color_input.links[0].from_node.name == "PSO_Diffuse"


def bake_texture_group(group_tree: bpy.types.ShaderNodeTree, width: int, height: int) -> "list[float] | None":
    """Bakes group_tree's actual Color/Alpha output - whatever it currently computes, generically,
    not a hardcoded formula - into a flat pixel buffer, or returns None if the group is still the
    plain default (Color fed directly from PSO_Diffuse) so the caller can just read the diffuse
    image's pixels directly instead, exactly like every texture without a relief composite already
    does. Only textures actually customized via _wire_relief_composite (xj.py) - or any future
    manual node edit inside the group - pay the cost of a real bake; ordinary textures are
    completely unaffected, both in output (byte-for-byte identical to today) and performance.

    Two separate Cycles EMIT bake passes, verified necessary: a single bake capturing color through
    an Emission shader always comes back with alpha pinned to 1.0 regardless of the graph feeding
    it (confirmed empirically - not fixable via render/film settings) - Cycles' EMIT pass type only
    ever represents emitted light color, with no concept of the transparency channel an exported
    image needs. Treating the group's Alpha output as a plain grayscale "color" and baking it
    through its own Emission, separately, sidesteps this entirely and reproduces the source alpha
    exactly (also verified empirically). Interpolation is left at each Image Texture node's own
    setting (matches whatever the live viewport already shows) and the color pass baked at whatever
    Blender pixel precision it naturally computes at - not attempting to force bit-exact parity
    with a raw pixel read on purpose, since this path is only reached once the user has already
    deliberately customized the texture, not for anything reachable through the "unchanged" path
    above.
    """
    if _is_default_texture_group_wiring(group_tree):
        return None

    context = bpy.context
    scene = context.scene
    if scene is None:
        return None

    # Save every scene setting this touches so baking a texture never leaves the user's actual
    # file in a different state than before the export ran.
    original_engine = scene.render.engine
    original_samples = scene.cycles.samples
    original_view_transform = scene.view_settings.view_transform
    original_selected = list(context.selected_objects) if context.selected_objects else []
    original_active = context.view_layer.objects.active if context.view_layer else None

    temp_obj: bpy.types.Object | None = None
    temp_mesh: bpy.types.Mesh | None = None
    temp_mat: bpy.types.Material | None = None
    color_target: bpy.types.Image | None = None
    alpha_target: bpy.types.Image | None = None
    try:
        scene.render.engine = "CYCLES"
        scene.cycles.samples = 1
        scene.view_settings.view_transform = "Standard"

        temp_mesh = bpy.data.meshes.new("PSO_BakePlane")
        temp_mesh.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [[0, 1, 2, 3]])
        uv_layer = temp_mesh.uv_layers.new(name="UVMap")
        for i, uv in enumerate([(0, 0), (1, 0), (1, 1), (0, 1)]):
            uv_layer.data[i].uv = uv
        temp_mesh.update()
        temp_obj = bpy.data.objects.new("PSO_BakeObj", temp_mesh)
        scene.collection.objects.link(temp_obj)

        temp_mat = bpy.data.materials.new("PSO_BakeMat")
        temp_mat.use_nodes = True
        tree = temp_mat.node_tree
        for n in list(tree.nodes):
            tree.nodes.remove(n)
        group_node = cast(bpy.types.ShaderNodeGroup, tree.nodes.new(type="ShaderNodeGroup"))
        group_node.node_tree = group_tree
        tex_coord = tree.nodes.new(type="ShaderNodeTexCoord")
        emission = cast(bpy.types.ShaderNodeEmission, tree.nodes.new(type="ShaderNodeEmission"))
        output = tree.nodes.new(type="ShaderNodeOutputMaterial")
        _ = tree.links.new(tex_coord.outputs["UV"], group_node.inputs["Vector"])
        _ = tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
        temp_obj.data.materials.append(temp_mat)

        context.view_layer.objects.active = temp_obj
        for obj in context.selected_objects:
            obj.select_set(False)
        temp_obj.select_set(True)

        # Pass 1: color
        _ = tree.links.new(group_node.outputs["Color"], emission.inputs["Color"])
        color_target = bpy.data.images.new("PSO_BakeColor", width, height, alpha=True)
        color_target.colorspace_settings.name = "sRGB"
        color_tex_node = cast(bpy.types.ShaderNodeTexImage, tree.nodes.new(type="ShaderNodeTexImage"))
        color_tex_node.image = color_target
        tree.nodes.active = color_tex_node
        bpy.ops.object.bake(type="EMIT")
        color_pixels = list(cast(Any, color_target).pixels)

        # Pass 2: alpha, treated as a plain grayscale "color" so it survives the bake (see
        # docstring) - duplicated across R/G/B via Combine Color, read back from channel 0.
        combine = tree.nodes.new(type="ShaderNodeCombineColor")
        _ = tree.links.new(group_node.outputs["Alpha"], combine.inputs["Red"])
        _ = tree.links.new(group_node.outputs["Alpha"], combine.inputs["Green"])
        _ = tree.links.new(group_node.outputs["Alpha"], combine.inputs["Blue"])
        _ = tree.links.new(combine.outputs["Color"], emission.inputs["Color"])
        alpha_target = bpy.data.images.new("PSO_BakeAlpha", width, height, alpha=True)
        alpha_target.colorspace_settings.name = "Non-Color"
        alpha_tex_node = cast(bpy.types.ShaderNodeTexImage, tree.nodes.new(type="ShaderNodeTexImage"))
        alpha_tex_node.image = alpha_target
        tree.nodes.active = alpha_tex_node
        bpy.ops.object.bake(type="EMIT")
        alpha_pixels = list(cast(Any, alpha_target).pixels)

        result = [0.0] * (width * height * 4)
        for i in range(width * height):
            result[i * 4 + 0] = color_pixels[i * 4 + 0]
            result[i * 4 + 1] = color_pixels[i * 4 + 1]
            result[i * 4 + 2] = color_pixels[i * 4 + 2]
            result[i * 4 + 3] = alpha_pixels[i * 4 + 0]
        return result
    finally:
        if temp_obj is not None:
            bpy.data.objects.remove(temp_obj, do_unlink=True)
        if temp_mesh is not None and temp_mesh.name in bpy.data.meshes:
            bpy.data.meshes.remove(temp_mesh)
        if temp_mat is not None and temp_mat.name in bpy.data.materials:
            bpy.data.materials.remove(temp_mat)
        if color_target is not None and color_target.name in bpy.data.images:
            bpy.data.images.remove(color_target)
        if alpha_target is not None and alpha_target.name in bpy.data.images:
            bpy.data.images.remove(alpha_target)
        scene.render.engine = original_engine
        scene.cycles.samples = original_samples
        scene.view_settings.view_transform = original_view_transform
        if context.view_layer:
            for obj in context.view_layer.objects:
                obj.select_set(obj in original_selected)
            context.view_layer.objects.active = original_active


def bake_material_mapping(mat: bpy.types.Material, pixels: list[float], width: int, height: int, channels: int) -> list[float]:
    """Resamples pixels (nearest-neighbor) so sampling the result with the mesh's raw UV
    reproduces what the material's Mapping node currently shows in Blender. No-op (returns
    pixels unchanged) if the material has no Mapping node or it's at its default identity.

    Folding a coordinate the Mapping transform pushed outside [0, 1) back into a valid position
    to read from the base image is a property of the image itself (does it tile seamlessly?),
    not of any particular mesh's texture addressing (WRAP/CLAMP/MIRROR is a per-mesh render
    state, unrelated to how this bake reads from the source image) - so it's always folded as if
    the source image tiles on itself, the same assumption an image editor's "wrap around" canvas
    transform would make, regardless of which addressing mode any variant material actually uses.
    """
    transform = get_material_mapping_transform(mat)
    if transform is None:
        return pixels
    result = [0.0] * (width * height * channels)
    for y in range(height):
        v = (y + 0.5) / height
        for x in range(width):
            u = (x + 0.5) / width
            sample = transform @ Vector((u, v, 0.0))
            su = sample.x % 1.0
            sv = sample.y % 1.0
            src_x = min(width - 1, int(su * width))
            src_y = min(height - 1, int(sv * height))
            src_i = (src_y * width + src_x) * channels
            dst_i = (y * width + x) * channels
            for c in range(channels):
                result[dst_i + c] = pixels[src_i + c]
    return result


def make_xvr(tex: Texture) -> Xvr:
    # get_stable_source_image: reading a SEQUENCE-source image's own properties/pixels directly
    # depends on Blender's current scene frame / whether it's been evaluated for display (e.g.
    # Material Preview) - see that function's docstring. A no-op for any other image source.
    stable_image = get_stable_source_image(tex.image)
    img_width, img_height = stable_image.size
    flags = 0
    if tex.generate_mipmaps:
        flags |= XvrFlags.MIPMAPS
    is_premultiplied = False
    if tex.has_alpha:
        if stable_image.alpha_mode == "PREMUL":
            is_premultiplied = True
        elif stable_image.alpha_mode != "STRAIGHT":
            raise Exception("XVR Error in Image '{}': Image has unsupported alpha mode '{}'".format(stable_image.filepath, stable_image.alpha_mode))
        flags |= XvrFlags.ALPHA

    channels = stable_image.channels
    mat = bpy.data.materials.get(tex.material_name)
    group_tree = find_material_img_group_tree(mat) if mat is not None else None
    baked = bake_texture_group(group_tree, img_width, img_height) if group_tree is not None else None
    if baked is not None:
        # The texture's shared ImgGroup has been customized (e.g. a relief composite - see
        # xj._wire_relief_composite) beyond its plain default wiring - use what it actually
        # computes instead of the diffuse image's raw pixels. Always 4-channel RGBA regardless of
        # tex.image.channels, since bake_texture_group's two bake passes always produce RGBA.
        pixels = baked
        channels = 4
    else:
        pixels = list(cast(Any, stable_image).pixels)
    if mat is not None:
        pixels = bake_material_mapping(mat, pixels, img_width, img_height, channels)

    # DXT1's alpha is 1-bit (transparent or opaque) via a punch-through color-ordering trick.
    # A PREMUL-alpha source (imported from DXT2/DXT4) has smooth alpha and pixels already
    # multiplied by alpha, matching DXT2's explicit 4-bit-per-pixel alpha block - use that
    # instead of collapsing it down to DXT1's binary alpha. A STRAIGHT-alpha source (the common
    # case - any freshly imported PNG) gets the same explicit-alpha treatment, but as DXT3
    # (unmultiplied, unlike DXT2), whenever its alpha channel turns out to have genuine gradation
    # rather than just a hard cutout mask - see xj_material_properties_menu.AlphaCompression for
    # the per-texture override that lets this auto-detection be forced either way.
    force_uncompressed = mat is not None and cast(MaterialWithXjSettings, mat).xj_settings.force_uncompressed
    if force_uncompressed:
        # Manual per-texture escape hatch (xj_material_properties_menu.XjMaterialSettings) for
        # content DXT1/2/3's shared 4-colors-per-4x4-block RGB limit visibly degrades (confirmed
        # live: a saturated, high-contrast icon-style texture) - checked before any DXT branch
        # below, since none of that logic applies once nothing is being compressed at all. Matches
        # what the original game already does for a handful of its own textures (real map data,
        # aforest01: 2 high-frequency noise/foam textures stored raw instead of DXT), not a
        # deviation from the format.
        xvr_format = XvrFormat.A8R8G8B8
    elif is_premultiplied:
        xvr_format = XvrFormat.DXT2
    elif not tex.has_alpha:
        xvr_format = XvrFormat.DXT1
    else:
        alpha_compression = cast(MaterialWithXjSettings, mat).xj_settings.alpha_compression if mat is not None else "AUTO"
        if alpha_compression == "FORCE_DXT1":
            xvr_format = XvrFormat.DXT1
        elif alpha_compression == "FORCE_DXT3":
            xvr_format = XvrFormat.DXT3
        elif alpha_compression == "FORCE_DXT2":
            # Unlike FORCE_DXT1/FORCE_DXT3, this isn't just a different codec over the same
            # pixels - DXT2's colors are premultiplied by alpha, a real data transform this
            # texture's source pixels haven't gone through (is_premultiplied is False here by
            # construction - an already-premultiplied source takes the branch above instead).
            # Intended for deliberately matching a premultiplied-alpha blend mode
            # (src=ONE, dst=INVSRCALPHA) - print a heads-up if this material's blend mode looks
            # like it isn't set up for that, since a mismatch would look visibly wrong in-game.
            if mat is not None:
                xj_settings = cast(MaterialWithXjSettings, mat).xj_settings
                if xj_settings.src_blend != "D3DBLEND_ONE" or xj_settings.dst_blend != "D3DBLEND_INVSRCALPHA":
                    print("XVM Notice: Material '{}' forces DXT2 (premultiplied alpha) but its blend "
                          "mode is {}/{}, not the usual ONE/INVSRCALPHA premultiplied pair - the "
                          "in-game result may look wrong.".format(mat.name, xj_settings.src_blend, xj_settings.dst_blend))
            pixels = xvm_dxt.premultiply_alpha(pixels, channels)
            xvr_format = XvrFormat.DXT2
        elif xvm_dxt.image_has_smooth_alpha(pixels, channels):
            xvr_format = XvrFormat.DXT3
        else:
            xvr_format = XvrFormat.DXT1

    def compress(px: list[float], w: int, h: int, channels: int) -> bytearray:
        if xvr_format == XvrFormat.A8R8G8B8:
            return xvm_dxt.pack_a8r8g8b8(px, w, h, channels)
        if xvr_format in (XvrFormat.DXT2, XvrFormat.DXT3):
            return xvm_dxt.compress_image_dxt3(px, w, h, channels)
        return xvm_dxt.compress_image(px, w, h, channels, tex.has_alpha)

    data = compress(list(pixels), img_width, img_height, channels)
    if tex.generate_mipmaps:
        # Real game .xvm files store an actual compressed mip pyramid immediately after the base
        # level's data (verified by decoding the real bytes: a standard halve-until-4x4 chain
        # decodes correctly with no gap between levels). A fixed 2-byte pad follows the last
        # (4x4) level - confirmed by byte-counting real files, content doesn't appear to matter.
        # This whole region lives inside data_size, unlike the separate fixed all-zero trailer
        # below (which sits outside data_size, only inside body_size).
        for (level_pixels, level_width, level_height) in generate_mip_levels(list(pixels), img_width, img_height, channels):
            data += compress(level_pixels, level_width, level_height, channels)
        data += bytes(2)
    data_size = len(data)
    if tex.generate_mipmaps:
        # Fixed all-zero trailer after the base level + mip chain (verified against ~1100 real
        # textures across 40 of Ephinea's own map .xvm files: always exactly 22 bytes for DXT1,
        # 43 bytes for DXT2, regardless of the texture's dimensions or mip chain length).
        tail_size = MIPMAP_TAIL_SIZE_BY_FORMAT.get(xvr_format, 22)
        data += bytes(tail_size)
    return Xvr(
        body_size=len(data) + Xvr.type_size() - IffHeader.type_size(),
        id=tex.id,
        flags=flags,
        format=xvr_format,
        width=img_width,
        height=img_height,
        data_size=data_size,
        data=data)  # pyright: ignore[reportArgumentType]


def write_xvrs(path: str, xvrs: list[Xvr]):
    """Serializes already-built Xvr chunks (e.g. from make_xvr(), or passed through unchanged
    from a source file) into a complete .xvm file."""
    buf = ResizableBuffer(size=0)
    # I'll just explicitly write the lists because it's easier
    xvm = Xvm(
        body_size=Xvm.type_size() - IffHeader.type_size(),
        xvr_count=len(xvrs))
    _ = xvm.serialize_into(buf)
    for xvr in xvrs:
        data = xvr.data
        xvr.data = []
        _ = xvr.serialize_into(buf)
        _ = buf.append(bytearray(data))
        buf.seek_to_end()
        xvr.data = data  # pyright: ignore[reportAttributeAccessIssue]
    with open(path, "wb") as f:
        _ = f.write(buf.buffer)


def xvr_cache_base_dir() -> str:
    """Parent of every per-map xvr_cache_root() folder - the whole thing this addon's "Clear
    Texture Cache" preferences button wipes (see preferences_menu.py), as opposed to any single
    map's subfolder."""
    return bpy.utils.user_resource("DATAFILES", path=os.path.join("pso_blender_cache", "xvr"), create=True)


def xvr_cache_root(xvm_filename: str) -> str:
    """Where cached compressed .xvr textures for one .xvm live - Blender's per-user data
    directory, not next to the exported .xvm. Same reasoning and same top-level "pso_blender_cache"
    resource dir as xj.animated_texture_cache_root (kept in its own "xvr" subfolder so the two
    caches' contents - compressed textures here, decoded animation-frame PNGs there - never mix):
    a destination folder is very often the game's own install folder, and this cache has no
    business creating files there. Scoped per xvm_filename so two different maps that happen to
    both have a texture named e.g. "wall.png" don't clobber each other's cached encode."""
    return os.path.join(xvr_cache_base_dir(), xvm_filename)


def get_or_make_xvr(tex: Texture, cache_dir_path: str) -> Xvr:
    """Single entry point for turning a Texture into an Xvr that both the full-rebuild REL/XVM
    export (write(), below) and the standalone selective-replace Export XVM operator
    (xvm_export_menu.py) go through - so a cache hit/miss decision is made exactly once, the same
    way, regardless of which export triggered it. Deliberately not two separate cached/uncached
    code paths: that split is what let a stale pre-quality-fix cache entry silently keep being
    served by REL export while a standalone Export XVM (which used to call make_xvr() directly,
    bypassing the cache) always looked correct - see CACHE_FORMAT_VERSION and texture_checksum().
    """
    if tex.prebuilt_xvr is not None:
        # Already-compressed bytes carried through untouched from a source .xvm (see
        # Texture.prebuilt_xvr) - reuse them exactly rather than decoding and recompressing
        # through make_xvr(), which would introduce real, avoidable DXT1 loss for content nothing
        # actually changed. Never goes through the disk cache either - there's nothing to encode.
        xvr = tex.prebuilt_xvr
        xvr.id = tex.id
        return xvr
    xvr_ext = ".xvr"
    pathlib.Path(cache_dir_path).mkdir(parents=True, exist_ok=True)  # Create dir if not exist
    (_, basename) = os.path.split(tex.name)
    xvr_basename = basename + xvr_ext
    cached_xvr_path = os.path.join(cache_dir_path, xvr_basename)
    cache_index_path = os.path.join(cache_dir_path, "index.json")
    cache_index = load_cache_index(cache_index_path)
    # Try to load cached textures from destination directory if pixels have not changed
    checksum = texture_checksum(tex)
    if os.path.isfile(cached_xvr_path) and checksum == cache_index.get(xvr_basename):
        xvr = get_cached_xvr(cached_xvr_path)
        xvr.id = tex.id  # Use new texture id
    else:
        xvr = make_xvr(tex)
        cache_xvr(cached_xvr_path, xvr)
    cache_index[xvr_basename] = checksum
    save_cache_index(cache_index_path, cache_index)
    return xvr


def write(path: str, textures: list[Texture]):
    cache_dir_path = xvr_cache_root(os.path.basename(path))
    xvrs = [get_or_make_xvr(tex, cache_dir_path) for tex in textures]
    write_xvrs(path, xvrs)


def read_rgb565_texture(src_buf: bytearray) -> list[float]:
    dst_chans = 4
    dst_buf = len(src_buf) // 2 * dst_chans * [0.0]
    for i in range(0, len(src_buf), 2):
        (r, g, b) = xvm_dxt.decompose_rgb565(src_buf[i + 1] | src_buf[i + 0])
        dst_i = i // 2 * dst_chans
        dst_buf[dst_i + 0] = r / 0xff
        dst_buf[dst_i + 1] = g / 0xff
        dst_buf[dst_i + 2] = b / 0xff
        dst_buf[dst_i + 3] = 1.0
    return dst_buf


def read_argb1555_texture(src_buf: bytearray) -> list[float]:
    dst_chans = 4
    dst_buf = len(src_buf) // 2 * dst_chans * [0.0]
    for i in range(0, len(src_buf), 2):
        rgb = src_buf[i + 1] | src_buf[i + 0]
        a = rgb & 0b1
        b = (rgb >> 5) & 0b11111
        g = (rgb >> 10) & 0b11111
        r = (rgb >> 15) & 0b11111
        r = (r << 3) | (r >> 2)
        g = (g << 3) | (g >> 2)
        b = (b << 3) | (b >> 2)
        dst_i = i // 2 * dst_chans
        dst_buf[dst_i + 0] = r / 0xff
        dst_buf[dst_i + 1] = g / 0xff
        dst_buf[dst_i + 2] = b / 0xff
        dst_buf[dst_i + 3] = a
    return dst_buf


# Keyed by absolute path -> (mtime, size, decoded Xvm). Importing a multi-chunk map (n.rel for
# several chunks) re-reads the same shared .xvm file once per chunk; decoding every texture in it
# each time is wasted work. Invalidated by mtime/size rather than cached forever, since texture
# pack work means these files get regenerated and re-imported within the same Blender session.
_read_cache: dict[str, tuple[float, int, "Xvm"]] = {}


def read(path: str) -> Xvm:
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        warnings.warn("XVM not found: \"{}\"".format(path))
        return Xvm()

    abs_path = os.path.abspath(path)
    cached = _read_cache.get(abs_path)
    if cached is not None and cached[0] == stat.st_mtime and cached[1] == stat.st_size:
        return cached[2]

    with open(path, "rb") as f:
        file_contents = bytearray(f.read())

    (xvm, xvr_offset) = Xvm.deserialize_from(file_contents)
    for _ in range(xvm.xvr_count):
        (xvr, data_offset) = Xvr.deserialize_from(file_contents, xvr_offset)
        compressed_data = file_contents[data_offset : data_offset + xvr.data_size]
        if xvr.format == XvrFormat.DXT1:
            pixels = xvm_dxt.dxt1_decompress(compressed_data, xvr.width, xvr.height)
        elif xvr.format == XvrFormat.DXT2 or xvr.format == XvrFormat.DXT3:
            pixels = xvm_dxt.dxt3_decompress(compressed_data, xvr.width, xvr.height)
        elif xvr.format == XvrFormat.DXT4 or xvr.format == XvrFormat.DXT5:
            pixels = xvm_dxt.dxt5_decompress(compressed_data, xvr.width, xvr.height)
        elif xvr.format == XvrFormat.R5G6B5:
            pixels = read_rgb565_texture(compressed_data)
        elif xvr.format == XvrFormat.A1R5G5B5:
            pixels = read_argb1555_texture(compressed_data)
        elif xvr.format == XvrFormat.A8R8G8B8:
            pixels = xvm_dxt.decode_a8r8g8b8(compressed_data, xvr.width, xvr.height)
        else:
            warnings.warn("Unsupported XVR format: {}".format(xvr.format))
            pixels = bytearray()
        xvr.data = pixels  # pyright: ignore[reportAttributeAccessIssue]
        xvm.xvrs.append(xvr)
        xvr_offset += xvr.body_size + IffHeader.type_size()

    xvm.set_filename(os.path.basename(path))
    xvm.set_full_path(abs_path)
    _read_cache[abs_path] = (stat.st_mtime, stat.st_size, xvm)
    return xvm


def read_raw(path: str) -> Xvm:
    """Like read(), but Xvr.data is left as the exact original compressed bytes (the full
    per-texture payload: base level + mip chain + trailer, if present) instead of being decoded
    to pixels. Used to carry a texture through a standalone XVM export completely unchanged -
    there's no need to decode+recompress a texture nothing touched, and doing so would lose
    fidelity (or fail entirely) for formats this addon can't re-encode, like the raw R5G6B5/
    A1R5G5B5 textures real map .xvm files sometimes contain alongside the DXT-compressed ones."""
    with open(path, "rb") as f:
        file_contents = bytearray(f.read())

    (xvm, xvr_offset) = Xvm.deserialize_from(file_contents)
    header_remainder_size = Xvr.type_size() - IffHeader.type_size()
    for _ in range(xvm.xvr_count):
        (xvr, data_offset) = Xvr.deserialize_from(file_contents, xvr_offset)
        payload_size = xvr.body_size - header_remainder_size
        xvr.data = file_contents[data_offset : data_offset + payload_size]  # pyright: ignore[reportAttributeAccessIssue]
        xvm.xvrs.append(xvr)
        xvr_offset += xvr.body_size + IffHeader.type_size()

    xvm.set_filename(os.path.basename(path))
    return xvm
