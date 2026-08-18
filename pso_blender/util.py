from collections.abc import Generator, Sequence
from functools import cache
import math
from typing import Any, ClassVar, TypeVar, cast
from mathutils import Euler, Vector, Matrix
import bpy.types
from abc import ABC, abstractmethod

from .serialization import Serializable


T = TypeVar("T", bound=int | float)


class ModalStepOperator:
    """Mixin for Operator subclasses whose real work is a series of discrete steps (a generator)
    rather than a single call, driven via a modal timer instead of one big blocking loop.

    This matters because a plain blocking `execute()` loop never gives Blender's UI a chance to
    redraw until the whole operator finishes - `wm.progress_begin`/`progress_update` calls made
    from inside such a loop are silently pointless, the indicator never actually appears on
    screen. Splitting the work into steps and returning control to Blender between each one (via
    a `TIMER` event) is what actually lets the progress indicator - and the rest of the UI - update
    while the operator runs.

    Subclasses call `self.start_modal_steps(context, steps, total)` from `execute()`, once any
    fast synchronous validation has already run and passed, instead of doing the work and
    returning `{'FINISHED'}` directly. Override `finish(context)` for whatever needs to happen
    once every step has completed successfully (e.g. writing out the final assembled file).
    """
    _modal_timer: bpy.types.Timer | None = None
    _modal_steps: Generator[None, None, None] | None = None
    _modal_done: int = 0

    def start_modal_steps(self, context: bpy.types.Context, steps: Generator[None, None, None], total: int):
        if bpy.app.background:
            # No window/event loop to pump TIMER events through in headless (--background) mode -
            # and no UI to show a progress bar in either way - so just run every step
            # synchronously, exactly like a plain blocking loop would. Only interactive sessions
            # actually need (and can show) the modal/timer-driven version below.
            return self._run_steps_synchronously(context, steps)
        wm = context.window_manager
        self._modal_steps = steps
        self._modal_done = 0
        # A popup (wm.invoke_popup) showing a live progress bar was tried here and crashed
        # Blender: the popup can outlive the modal operator (it isn't guaranteed to close the
        # moment the operator finishes), and Blender segfaulted when interacted with while showing
        # a popup tied to an already-finished operator. Popups are built for interactive property
        # editing, not an externally-timer-driven live status display, so the two don't mix safely
        # - sticking with the cursor's built-in percentage overlay instead, which is the one
        # variant that's actually been confirmed to run to completion and clean up without issue.
        wm.progress_begin(0, max(1, total))
        self._modal_timer = wm.event_timer_add(1e-4, window=context.window)
        wm.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _run_steps_synchronously(self, context: bpy.types.Context, steps: Generator[None, None, None]):
        try:
            for _ in steps:
                pass
        except Exception as e:
            cast(Any, self).report({"ERROR"}, str(e))
            return {"CANCELLED"}
        try:
            self.finish(context)
        except Exception as e:
            cast(Any, self).report({"ERROR"}, str(e))
            return {"CANCELLED"}
        return {"FINISHED"}

    def modal(self, context: bpy.types.Context, event: bpy.types.Event):
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        wm = context.window_manager
        try:
            assert self._modal_steps is not None
            next(self._modal_steps)
            self._modal_done += 1
            wm.progress_update(self._modal_done)
            return {"RUNNING_MODAL"}
        except StopIteration:
            self._cleanup_modal_steps(context)
            try:
                self.finish(context)
            except Exception as e:
                cast(Any, self).report({"ERROR"}, str(e))
                return {"CANCELLED"}
            return {"FINISHED"}
        except Exception as e:
            self._cleanup_modal_steps(context)
            cast(Any, self).report({"ERROR"}, str(e))
            return {"CANCELLED"}

    def _cleanup_modal_steps(self, context: bpy.types.Context):
        wm = context.window_manager
        wm.progress_end()
        if self._modal_timer is not None:
            wm.event_timer_remove(self._modal_timer)
            self._modal_timer = None

    def finish(self, context: bpy.types.Context):
        """Called once every step has completed successfully - override for any final assembly
        step (e.g. serializing accumulated results out to a file)."""
        pass


def mesh_faces(mesh: bpy.types.Mesh) -> list[tuple[int, int, int]]:
    """Returns vertex indices of triangulated faces"""
    faces: list[tuple[int, int, int]] = []
    for tri in mesh.loop_triangles:
        v = tri.vertices
        faces.append((v[0], v[1], v[2]))
    return faces


class Texture:
    id: int
    name: str
    material_name: str
    generate_mipmaps: bool
    has_alpha: bool
    image: bpy.types.Image
    animation_frames: int

    _alpha_check_cache: ClassVar[dict[str, bool]] = dict()

    def __init__(self, *, id: int, material_name: str, image: bpy.types.Image, generate_mipmaps: bool=False, animation_frames: int=0):
        self.id = id
        self.name = image.filepath_from_user() or image.name # Path can be empty if texture was created programmatically
        self.material_name = material_name
        self.image = image
        self.generate_mipmaps = generate_mipmaps
        self.animation_frames = animation_frames
        # Check if texture uses alpha
        self.has_alpha = image.channels == 4
        if self.name in Texture._alpha_check_cache:
            self.has_alpha = Texture._alpha_check_cache[self.name]
        elif self.has_alpha:
            # This is a surprisingly slow operation so let's use a cache
            pixels = list(cast(Any, image.pixels))
            self.has_alpha = False
            for i in range(0, len(pixels), 4):
                if pixels[i + 3] < 1:
                    self.has_alpha = True
                    break
            Texture._alpha_check_cache[self.name] = self.has_alpha


def find_diffuse_image(mat: bpy.types.Material) -> bpy.types.Image | None:
    """The image a material's texture node plugs in - either a plain Image Texture node found
    directly in the material, or (for material variants created by this addon's XJ/REL importer)
    one found one level inside a Group node. Multiple material variants of the same original
    texture (different blend mode / addressing settings) share a single node group wrapping just
    that Image Texture node, so swapping the image only has to be done in one shared place - see
    get_or_create_texture_node_group in xj.py.

    Assumes the first image node found directly in the material is the correct one. Inside a
    shared ImgGroup, prefers a node explicitly named "PSO_Diffuse" (see
    get_or_create_texture_node_group) - needed because that group can also carry PSO_Normal/
    PSO_Metal Image Texture nodes for a relief composite (see _wire_relief_composite in xj.py),
    so "the only TEX_IMAGE node in the group" is no longer a safe assumption. Falls back to "first
    TEX_IMAGE node found" for groups created before this naming existed.
    """
    if mat.node_tree is None:
        return None
    for node in mat.node_tree.nodes:
        if node.type == "TEX_IMAGE":
            image = cast(bpy.types.ShaderNodeTexImage, node).image
            if image is not None:
                return image
        elif node.type == "GROUP":
            group_node_tree = cast(bpy.types.ShaderNodeGroup, node).node_tree
            if group_node_tree is None:
                continue
            named_node = group_node_tree.nodes.get("PSO_Diffuse")
            if named_node is not None and named_node.type == "TEX_IMAGE":
                image = cast(bpy.types.ShaderNodeTexImage, named_node).image
                if image is not None:
                    return image
            for inner_node in group_node_tree.nodes:
                if inner_node.type == "TEX_IMAGE":
                    image = cast(bpy.types.ShaderNodeTexImage, inner_node).image
                    if image is not None:
                        return image
    return None


def find_material_img_group_tree(mat: bpy.types.Material) -> bpy.types.ShaderNodeTree | None:
    """The shared ImgGroup_* node tree this material's texture group node references, if any -
    see get_or_create_texture_node_group in xj.py. Used everywhere something needs to read/write
    state that's shared across every material variant of the same texture instead of stored
    separately per material (generate_mipmaps, a relief composite's normal/metal nodes, ...)."""
    if mat.node_tree is None:
        return None
    for node in mat.node_tree.nodes:
        if node.type != "GROUP":
            continue
        node_tree = cast(bpy.types.ShaderNodeGroup, node).node_tree
        if node_tree is not None and node_tree.name.startswith("ImgGroup_"):
            return node_tree
    return None


def find_material_base_color_image(mat: bpy.types.Material) -> bpy.types.Image | None:
    """The Base Color / diffuse image of an arbitrary, foreign material (e.g. one dropped in by
    an asset-browser addon like Poly Haven) - unlike find_diffuse_image, this makes no assumption
    about the material's structure beyond "a normal Principled BSDF setup", since the material
    wasn't necessarily created by this addon. A PBR material typically has several Image Texture
    nodes (base color, normal, roughness, displacement, AO...), so naively grabbing the first one
    found - or even the first one reachable by walking a single path back from Base Color - risks
    picking the wrong one: many PBR materials multiply the actual color texture together with a
    grayscale AO texture right before Base Color, and which of the two inputs comes first on that
    Mix node isn't something we can assume. So instead this collects every Image Texture node
    reachable from Base Color (not just the first path found) and prefers one whose colorspace
    isn't "Non-Color" - the standard tag for channel-data maps (AO/roughness/normal/displacement)
    as opposed to actual color textures - only falling back to "first Image Texture node found
    anywhere in the material" if Base Color isn't linked to any image at all.
    """
    if mat.node_tree is None:
        return None

    def is_color_data(image: bpy.types.Image) -> bool:
        return image.colorspace_settings.name != "Non-Color"

    def collect_images(node: bpy.types.Node, depth: int, visited: set[int]) -> list[bpy.types.Image]:
        if id(node) in visited or depth > 4:
            return []
        visited.add(id(node))
        if node.type == "TEX_IMAGE":
            image = cast(bpy.types.ShaderNodeTexImage, node).image
            return [image] if image is not None else []
        found: list[bpy.types.Image] = []
        for input_socket in node.inputs:
            if input_socket.is_linked:
                found.extend(collect_images(input_socket.links[0].from_node, depth + 1, visited))
        return found

    def pick_best(images: list[bpy.types.Image]) -> bpy.types.Image | None:
        if not images:
            return None
        color_images = [img for img in images if is_color_data(img)]
        return color_images[0] if color_images else images[0]

    principled_node = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if principled_node is not None:
        base_color_input = cast(bpy.types.ShaderNodeBsdfPrincipled, principled_node).inputs["Base Color"]
        if base_color_input.is_linked:
            candidates = collect_images(base_color_input.links[0].from_node, 0, set())
            best = pick_best(candidates)
            if best is not None:
                return best

    all_images = [
        cast(bpy.types.ShaderNodeTexImage, n).image for n in mat.node_tree.nodes
        if n.type == "TEX_IMAGE" and cast(bpy.types.ShaderNodeTexImage, n).image is not None]
    return pick_best(all_images)


def find_material_normal_and_metal_images(mat: bpy.types.Material) -> tuple["bpy.types.Image | None", "bpy.types.Image | None"]:
    """The Normal Map and Metallic images of an arbitrary, foreign PBR material (e.g. one dropped
    in by an asset-browser addon like Poly Haven, or a Source-engine material imported via an addon
    like SourceIO/Plumber) - companion to find_material_base_color_image above, same assumption of
    "a normal Principled BSDF setup" with no dependency on which addon actually built the graph.

    Normal: expects the Principled BSDF's Normal input to be linked to a Normal Map node, whose
    Color input is in turn linked to an Image Texture - this is the standard way every PBR-material
    addon wires a normal map, not something specific to any one of them.

    Metallic: expects the Principled BSDF's Metallic input to be linked directly to an Image
    Texture. Deliberately does not attempt to detect or unpack a combined ARM
    (AO/Roughness/Metalness) texture some asset sets use instead of a dedicated Metallic map - that
    would need channel-splitting logic this doesn't have yet.

    Either or both can come back None if not present/not found - the caller is expected to handle
    missing normal, missing metal, or both, independently.
    """
    if mat.node_tree is None:
        return (None, None)

    principled_node = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if principled_node is None:
        return (None, None)
    principled = cast(bpy.types.ShaderNodeBsdfPrincipled, principled_node)

    normal_image: bpy.types.Image | None = None
    normal_input = principled.inputs.get("Normal")
    if normal_input is not None and normal_input.is_linked:
        normal_map_node = normal_input.links[0].from_node
        if normal_map_node.type == "NORMAL_MAP":
            color_input = cast(bpy.types.ShaderNodeNormalMap, normal_map_node).inputs.get("Color")
            if color_input is not None and color_input.is_linked:
                tex_node = color_input.links[0].from_node
                if tex_node.type == "TEX_IMAGE":
                    normal_image = cast(bpy.types.ShaderNodeTexImage, tex_node).image

    metal_image: bpy.types.Image | None = None
    metallic_input = principled.inputs.get("Metallic")
    if metallic_input is not None and metallic_input.is_linked:
        tex_node = metallic_input.links[0].from_node
        if tex_node.type == "TEX_IMAGE":
            metal_image = cast(bpy.types.ShaderNodeTexImage, tex_node).image

    return (normal_image, metal_image)


def find_material_roughness_image(mat: bpy.types.Material) -> "bpy.types.Image | None":
    """The Roughness image of an arbitrary, foreign PBR material, if any - same graph-walking
    pattern as find_material_normal_and_metal_images (kept separate rather than folded in there,
    same precedent as metal being additive to normal): expects the Principled BSDF's Roughness
    input to be linked directly to an Image Texture, same shape as the Metallic case."""
    if mat.node_tree is None:
        return None
    principled_node = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if principled_node is None:
        return None
    roughness_input = cast(bpy.types.ShaderNodeBsdfPrincipled, principled_node).inputs.get("Roughness")
    if roughness_input is not None and roughness_input.is_linked:
        tex_node = roughness_input.links[0].from_node
        if tex_node.type == "TEX_IMAGE":
            return cast(bpy.types.ShaderNodeTexImage, tex_node).image
    return None


@cache # This is a surprisingly slow operation so let's use a cache
def get_object_diffuse_textures(obj: bpy.types.Object) -> list[Texture]:
    """Assumes the first image node of each material is the correct one"""
    # Avoid circular dependency
    from .xj_material_properties_menu import MaterialWithXjSettings

    textures: list[Texture] = []
    for mat_slot in obj.material_slots:
        if not mat_slot.material or not mat_slot.material.node_tree:
            continue
        image = find_diffuse_image(mat_slot.material)
        if image is not None:
            xj_settings = cast(MaterialWithXjSettings, mat_slot.material).xj_settings
            generate_mipmaps = cast(bool, xj_settings.generate_mipmaps)
            textures.append(Texture(
                id=-1,
                material_name=mat_slot.material.name, generate_mipmaps=generate_mipmaps, image=image))
    return textures


def magic_bytes(s: str) -> list[int]:
    return list(map(ord, s))


def from_blender_axes(tup: Sequence[float] | Vector | Euler, invert_z: bool=True) -> Vector:
    """Swaps second and third component"""
    x, z, y = tup
    if invert_z:
        z *= -1
    return Vector((x, y, z))


def distance_squared(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(map(lambda a_, b_: (b_ - a_) ** 2, a, b))


def distance(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(distance_squared(a, b))


def geometry_world_center(obj: bpy.types.Object) -> Vector:
    bound_box = obj.bound_box
    local = 1 / 8 * sum((Vector(corner) for corner in bound_box), Vector())
    return obj.matrix_world @ local


def clamp(n: T, min_val: T, max_val: T) -> T:
    return max(min(n, max_val), min_val)


class AbstractFileArchive(ABC):
    @abstractmethod
    def write(self, item: Serializable, ensure_aligned: bool=False) -> int:
        pass


def bytes_to_string(b: list[int]) -> str:
    return bytes(b).decode().rstrip("\0")


def align_up(n: int, to: int) -> int:
    return (n + to - 1) // to * to


def scale_mesh(mesh: bpy.types.Mesh, x: float, y: float | None=None, z: float | None=None):
    if y is None:
        y = x
    if z is None:
        z = x
    mesh.transform(Matrix.LocRotScale(None, None, Vector((x, y, z))))


def get_pso_world_scale() -> float:
    return 33.0


def apply_transform(ob: bpy.types.Object, use_location: bool=False, use_rotation: bool=False, use_scale: bool=False):
    mb = ob.matrix_basis
    ident = Matrix()
    loc, _rot, scale = mb.decompose()

    # rotation
    T = Matrix.Translation(loc)
    #R = rot.to_matrix().to_4x4()
    R = mb.to_3x3().normalized().to_4x4()
    S = Matrix.Diagonal(scale).to_4x4()

    transform = [ident, ident, ident]
    basis = [T, R, S]

    def swap(i: int):
        transform[i], basis[i] = basis[i], transform[i]

    if use_location:
        swap(0)
    if use_rotation:
        swap(1)
    if use_scale:
        swap(2)
        
    M = transform[0] @ transform[1] @ transform[2]
    if hasattr(ob.data, "transform"):
        cast(Any, ob.data).transform(M)
    for c in ob.children:
        c.matrix_local = M @ c.matrix_local
        
    ob.matrix_basis = basis[0] @ basis[1] @ basis[2]


def get_set_bits(n: int) -> list[int]:
    bits: list[int] = []
    i = 0
    while n:
        if n & 1:
            bits.append(1 << i)
        n >>= 1
        i += 1
    return bits


def get_parent_collection(child_collection: bpy.types.Collection) -> bpy.types.Collection | None:
    for collection in bpy.data.collections:
        if child_collection.name in collection.children.keys():
            return collection
    return None
