from collections.abc import Sequence
import math
from typing import Any, TypeVar, cast
from mathutils import Euler, Vector, Matrix
import bpy.types 
from abc import ABC, abstractmethod

from .serialization import Serializable


T = TypeVar("T", bound=int | float)


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

    def __init__(self, *, id: int, material_name: str, image: bpy.types.Image, generate_mipmaps: bool=False, animation_frames: int=0):
        self.id = id
        self.name = image.filepath_from_user() or image.name # Path can be empty if texture was created programmatically
        self.material_name = material_name
        self.image = image
        self.generate_mipmaps = generate_mipmaps
        self.animation_frames = animation_frames
        # Check if texture uses alpha
        self.has_alpha = image.channels == 4
        if self.has_alpha:
            pixels = list(cast(Any, image.pixels))
            self.has_alpha = False
            for i in range(0, len(pixels), 4):
                if pixels[i + 3] < 1:
                    self.has_alpha = True
                    break


def get_object_diffuse_textures(obj: bpy.types.Object) -> list[Texture]:
    """Assumes the first image node of each material is the correct one"""
    # Avoid circular dependency
    from .xj_material_properties_menu import MaterialWithXjSettings

    textures: list[Texture] = []
    for mat_slot in obj.material_slots:
        if not mat_slot.material or not mat_slot.material.node_tree:
            continue
        for node in mat_slot.material.node_tree.nodes:
            if node.type == "TEX_IMAGE":
                tex_node = cast(bpy.types.ShaderNodeTexImage, node)
                if tex_node.image is not None:
                    xj_settings = cast(MaterialWithXjSettings, mat_slot.material).xj_settings
                    generate_mipmaps = cast(bool, xj_settings.generate_mipmaps)
                    textures.append(Texture(
                        id=-1,
                        material_name=mat_slot.material.name, generate_mipmaps=generate_mipmaps, image=tex_node.image))
                    break
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
