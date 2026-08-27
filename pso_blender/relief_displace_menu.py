import heapq
from typing import Any, cast, final
import bpy
import bmesh
from bpy.props import FloatProperty, IntProperty  # pyright: ignore[reportUnknownVariableType]
from bpy.types import Context, Operator, Panel
from .rel_properties_menu import ObjectWithRelSettings
from .util import find_diffuse_image, find_material_img_group_tree


# Shared between modifier/vertex-group names and the idempotency check on the legacy whole-object
# path below - an object already carrying any modifier with this prefix is assumed to already have
# been processed by that path, mirroring the _RELIEF_NODE_PREFIX convention in xj.py.
_MODIFIER_NAME_PREFIX = "PSO_Relief_"
_FALLOFF_GROUP_NAME = _MODIFIER_NAME_PREFIX + "Falloff"

_HEIGHT_IMAGE_SUFFIX = "_PSO_Height"
_HEIGHT_TEXTURE_SUFFIX = "_PSO_HeightTex"


def _find_material_relief_image(mat: bpy.types.Material, node_name: str) -> "bpy.types.Image | None":
    """The image behind a named node (e.g. "PSO_Height", "PSO_Normal") inside this material's
    shared ImgGroup node tree, if any (see _wire_relief_composite in xj.py, which builds these) -
    deliberately not find_material_normal_and_metal_images/find_material_displacement_image
    (util.py), which assume a foreign PBR material's standard Principled-BSDF/Material-Output
    wiring; this addon's own materials wire these images directly into nodes inside the shared
    group instead (a Separate Color node for PSO_Normal, nothing further for PSO_Height), so those
    helpers wouldn't find them here."""
    group_tree = find_material_img_group_tree(mat)
    if group_tree is None:
        return None
    node = group_tree.nodes.get(node_name)
    if node is not None and node.type == "TEX_IMAGE":
        return cast(bpy.types.ShaderNodeTexImage, node).image
    return None


def _box_blur(values: "list[float]", width: int, height: int, radius: int) -> "list[float]":
    """Separable box blur (edge-clamped) over a flat width*height scalar array - smooths out the
    high-frequency per-pixel noise that otherwise turns directly into spiky, uncorrelated
    per-vertex displacement once the mesh is subdivided finely enough to sample individual texels
    (a normal map's blue channel in particular carries a lot of this - it encodes local slope, not
    smooth elevation, and Displace has no awareness of neighboring vertices to smooth between
    them). radius <= 0 returns values unchanged."""
    if radius <= 0:
        return values

    horizontal = [0.0] * len(values)
    for y in range(height):
        row = y * width
        for x in range(width):
            total = 0.0
            for dx in range(-radius, radius + 1):
                sx = min(max(x + dx, 0), width - 1)
                total += values[row + sx]
            horizontal[row + x] = total / (radius * 2 + 1)

    result = [0.0] * len(values)
    for y in range(height):
        for x in range(width):
            total = 0.0
            for dy in range(-radius, radius + 1):
                sy = min(max(y + dy, 0), height - 1)
                total += horizontal[sy * width + x]
            result[y * width + x] = total / (radius * 2 + 1)
    return result


def _get_or_build_height_image(
        cache: "dict[str, bpy.types.Image]", source_image: bpy.types.Image, mode: str,
        blur_radius: int) -> bpy.types.Image:
    """A single-channel grayscale Image derived from source_image, suitable as a Displace
    modifier's height texture - reused by name (bpy.data.images.get) if one already exists rather
    than torn down and rebuilt, and cached by source_image.name for the remainder of one operator
    run on top of that. Confirmed live that removing+rebuilding breaks any OTHER object's Displace
    modifier still pointing at the old datablock (Blender nulls a modifier's texture reference the
    moment the datablock it points to is removed) - this bit hard the very first time two
    different objects sharing one material were processed in separate operator runs (one button
    click each): processing the second object silently blanked the first one's already-working
    Displace. The name encodes mode/blur_radius (see below) so changing either produces a
    distinctly-named image instead of colliding with/invalidating a previous one built with
    different settings.

    mode="normal" reads the blue channel and remaps [0.5, 1.0] -> [0.0, 1.0] (clamped) - the exact
    interpretation _wire_relief_composite (xj.py) already uses for its shader-level relief
    darkening (stored blue 0.5 = fully tilted, 1.0 = straight up), reused here instead of inventing
    a new heuristic - only a real approximation, since a normal map encodes local slope, not true
    elevation (confirmed live: on a real Poly Haven asset, the blue channel only ranged 0.67-1.02,
    crowding this remap into the top third of [0,1] and flattening the result well below what the
    material's own real geometry actually looks like).

    mode="direct" reads the red channel as-is, no remap - for a genuine displacement/height image
    (util.find_material_displacement_image, stored as "PSO_Height" by _wire_relief_composite),
    already meaningful 0-1 height data with no slope-vs-elevation ambiguity - strictly more
    accurate than "normal" when available, which relief_displace_menu.py prefers it over.

    mode="luminance" (no normal or displacement map available) uses standard luminance - a
    coarser, generic height proxy for materials with no real height data at all.

    blur_radius smooths the derived scalar field (see _box_blur) before it's written - meant to be
    paired with a higher Displace strength than an unblurred map would use, trading fine surface
    detail for a coarser, spike-free relief that needs less subdivision to read correctly.
    """
    mode_suffix = {"normal": "N", "direct": "D", "luminance": "L"}[mode]
    height_name = "{}{}_{}_{}".format(source_image.name, _HEIGHT_IMAGE_SUFFIX, mode_suffix, blur_radius)

    cached = cache.get(height_name)
    if cached is not None:
        return cached

    existing = bpy.data.images.get(height_name)
    if existing is not None:
        cache[height_name] = existing
        return existing

    width, height = source_image.size
    count = width * height
    src_pixels = [0.0] * (count * 4)
    source_image.pixels.foreach_get(src_pixels)  # pyright: ignore[reportArgumentType]

    values = [0.0] * count
    for i in range(count):
        r = src_pixels[i * 4]
        g = src_pixels[i * 4 + 1]
        b = src_pixels[i * 4 + 2]
        if mode == "normal":
            values[i] = max(0.0, min(1.0, (b - 0.5) * 2.0))
        elif mode == "direct":
            values[i] = r
        else:
            values[i] = 0.2126 * r + 0.7152 * g + 0.0722 * b

    values = _box_blur(values, width, height, blur_radius)

    out_pixels = [0.0] * (count * 4)
    for i in range(count):
        out_pixels[i * 4] = values[i]
        out_pixels[i * 4 + 1] = values[i]
        out_pixels[i * 4 + 2] = values[i]
        out_pixels[i * 4 + 3] = 1.0

    height_image = bpy.data.images.new(height_name, width, height)
    # Colorspace MUST be set before foreach_set, not after - confirmed live that changing
    # colorspace_settings.name on a freshly created (GENERATED-source) image resets its pixel
    # buffer back to the default blank fill, silently discarding whatever foreach_set had just
    # written if the colorspace change came afterward.
    height_image.colorspace_settings.name = "Non-Color"
    height_image.pixels.foreach_set(out_pixels)  # pyright: ignore[reportArgumentType]
    height_image.update()

    cache[height_name] = height_image
    return height_image


def _get_or_build_height_texture(cache: "dict[str, bpy.types.Texture]", height_image: bpy.types.Image) -> bpy.types.Texture:
    """The legacy bpy.data.textures Image texture a Displace modifier actually needs (its
    `texture` property can't point at an Image directly) - one per height_image, reused by name if
    one already exists (see _get_or_build_height_image - same reasoning: removing+rebuilding would
    null out any OTHER object's Displace modifier still pointing at the old one), on top of a
    per-operator-run cache."""
    tex_name = height_image.name + _HEIGHT_TEXTURE_SUFFIX

    cached = cache.get(tex_name)
    if cached is not None:
        return cached

    existing = bpy.data.textures.get(tex_name)
    if existing is not None:
        cache[tex_name] = existing
        return existing

    texture = cast(bpy.types.ImageTexture, bpy.data.textures.new(tex_name, type="IMAGE"))
    texture.image = height_image
    texture.extension = "REPEAT"

    cache[tex_name] = texture
    return texture


def _copy_rel_settings(source_obj: bpy.types.Object, new_obj: bpy.types.Object):
    """Copies the REL export flags that matter onto a newly separated piece - without this,
    export would either ignore the piece entirely (is_nrel defaults to False) or treat it with
    default settings instead of whatever the source object actually had."""
    src = cast(ObjectWithRelSettings, source_obj).rel_settings
    dst = cast(ObjectWithRelSettings, new_obj).rel_settings
    dst.is_nrel = True
    dst.receives_shadows = src.receives_shadows
    dst.receives_fog = src.receives_fog
    dst.is_translucent = src.is_translucent
    dst.always_rendered = src.always_rendered
    dst.is_stencil_viewer = src.is_stencil_viewer
    dst.is_stenciled = src.is_stenciled


def _resolve_height_source(material: bpy.types.Material) -> "tuple[bpy.types.Image, str] | None":
    """Picks the best available height data source for a material, in priority order: a genuine
    "PSO_Height" displacement image (mode "direct", no remap needed - see
    util.find_material_displacement_image/_wire_relief_composite in xj.py, the most accurate when
    present), then "PSO_Normal"'s blue channel (mode "normal", a real approximation - a normal map
    encodes local slope, not true elevation), then the material's own diffuse image by luminance
    (mode "luminance", coarsest fallback for materials with no height/normal data at all). Returns
    None only if the material has no usable image whatsoever (not even a diffuse texture)."""
    height_image = _find_material_relief_image(material, "PSO_Height")
    if height_image is not None:
        return (height_image, "direct")
    normal_image = _find_material_relief_image(material, "PSO_Normal")
    if normal_image is not None:
        return (normal_image, "normal")
    diffuse_image = find_diffuse_image(material)
    if diffuse_image is not None:
        return (diffuse_image, "luminance")
    return None


def _apply_subdivide_and_displace(obj: bpy.types.Object, material: bpy.types.Material, strength: float,
        subdivisions: int, blur_radius: int, height_image_cache: "dict[str, bpy.types.Image]",
        height_texture_cache: "dict[str, bpy.types.Texture]") -> "str | None":
    """Adds the Simple Subdivision Surface + single Displace modifier pair to obj (assumed to
    have exactly one material - see callers). Returns None on success, or an error message string
    if the material has no usable image at all."""
    resolved = _resolve_height_source(material)
    if resolved is None:
        return "material '{}' has no usable image".format(material.name)
    source_image, mode = resolved
    height_image = _get_or_build_height_image(height_image_cache, source_image, mode, blur_radius)
    texture = _get_or_build_height_texture(height_texture_cache, height_image)

    subsurf = cast(bpy.types.SubsurfModifier, obj.modifiers.new(
        name=_MODIFIER_NAME_PREFIX + "Subdivide", type="SUBSURF"))
    subsurf.subdivision_type = "SIMPLE"
    subsurf.levels = subdivisions
    subsurf.render_levels = subdivisions

    displace = cast(bpy.types.DisplaceModifier, obj.modifiers.new(
        name=_MODIFIER_NAME_PREFIX + "Displace", type="DISPLACE"))
    displace.texture = texture
    # Deliberately NOT wired to _FALLOFF_GROUP_NAME even when present - confirmed live that at
    # single-face-per-object granularity, many interior faces have every vertex sitting exactly on
    # a seam (weight 0 no matter what falloff_distance is, since distance is exactly 0 for a true
    # seam vertex), flattening the whole piece. No falloff_distance value fixes this - it's not a
    # tuning problem. Left unwired (anti-crack blending is effectively unsolved for now) rather
    # than silently killing the effect on affected pieces - see project memory for the open
    # problem if revisited.
    displace.direction = "NORMAL"
    displace.mid_level = 0.5
    displace.strength = strength
    displace.texture_coords = "UV"
    return None


def _compute_seam_falloff_distances(
        selected_faces: "list[bmesh.types.BMFace]", falloff_distance: float) -> "dict[int, float]":
    """Dijkstra (edge length as weight) from every edge shared by two SELECTED faces - i.e. every
    edge that's about to become a cut between two separately-displaced objects once
    _split_selection_by_face runs. Returns {bmesh vertex index: distance}, capped at
    falloff_distance (vertices farther than that, or never reached, simply don't appear - callers
    treat a missing entry as "far enough to get full displacement strength").
    """
    selected_indices = {f.index for f in selected_faces}
    verts_by_index: "dict[int, bmesh.types.BMVert]" = {}
    for f in selected_faces:
        for v in f.verts:
            verts_by_index[v.index] = v

    dist: "dict[int, float]" = {}
    heap: "list[tuple[float, int]]" = []
    for f in selected_faces:
        for e in f.edges:
            linked_in_selection = [lf for lf in e.link_faces if lf.index in selected_indices]
            if len(linked_in_selection) == 2:
                for v in e.verts:
                    if dist.get(v.index, float("inf")) > 0.0:
                        dist[v.index] = 0.0
                        heapq.heappush(heap, (0.0, v.index))

    while heap:
        d, vi = heapq.heappop(heap)
        if d > dist.get(vi, float("inf")) or d > falloff_distance:
            continue
        v = verts_by_index.get(vi)
        if v is None:
            continue
        for e in v.link_edges:
            other = e.other_vert(v)
            if other.index not in verts_by_index:
                continue
            nd = d + e.calc_length()
            if nd < dist.get(other.index, float("inf")):
                dist[other.index] = nd
                heapq.heappush(heap, (nd, other.index))

    return dist


def _build_falloff_vertex_group(obj: bpy.types.Object, vert_distances: "dict[int, float]", falloff_distance: float):
    """Writes _FALLOFF_GROUP_NAME on obj.data (Object Mode API - obj must not be in Edit Mode),
    weight 0.0 right on a seam rising to 1.0 at falloff_distance and beyond. Every vertex not
    present in vert_distances (too far from any seam to have been reached) defaults to full
    weight 1.0. mesh.separate() copies named vertex groups (remapped) onto each new piece, same
    guarantee already relied on for UV layers elsewhere in this addon - so this only needs to be
    built once, before splitting starts."""
    mesh = cast(bpy.types.Mesh, obj.data)
    existing = obj.vertex_groups.get(_FALLOFF_GROUP_NAME)
    if existing is not None:
        obj.vertex_groups.remove(existing)
    group = obj.vertex_groups.new(name=_FALLOFF_GROUP_NAME)
    for v in mesh.vertices:
        distance = vert_distances.get(v.index)
        weight = 1.0 if distance is None else min(1.0, distance / falloff_distance)
        group.add([v.index], weight, "REPLACE")


_SPLIT_PENDING_ATTR_NAME = _MODIFIER_NAME_PREFIX + "SplitPending"


def _split_selection_by_face(context: Context, obj: bpy.types.Object) -> "list[bpy.types.Object]":
    """Assumes obj is in Object Mode with the intended faces already flagged via polygon.select.
    Separates one selected face at a time into its own new object, stopping instead of emptying
    obj entirely if every remaining face is part of the group - the last one stays in place and is
    returned as one of the pieces, unseparated, same "don't empty the source" guard used by
    _separate_and_build_decal (decal_prep_menu.py).

    Tracks which faces still need separating via a dedicated boolean FACE attribute
    (_SPLIT_PENDING_ATTR_NAME), not polygon.select - isolating one face at a time for
    mesh.ops.separate requires overwriting .select on every other face each iteration, which would
    destroy any "still pending" information .select itself was also being used to carry.
    mesh.separate() remaps custom face attributes onto the new piece correctly, same guarantee
    already relied on for UV layers/vertex groups elsewhere in this addon - the attribute is
    removed from both obj and each new piece once no longer needed.
    """
    mesh = cast(bpy.types.Mesh, obj.data)
    existing_attr = mesh.attributes.get(_SPLIT_PENDING_ATTR_NAME)
    if existing_attr is not None:
        mesh.attributes.remove(existing_attr)
    pending_attr = mesh.attributes.new(name=_SPLIT_PENDING_ATTR_NAME, type="BOOLEAN", domain="FACE")
    for p in mesh.polygons:
        pending_attr.data[p.index].value = p.select

    pieces: "list[bpy.types.Object]" = []
    while True:
        mesh = cast(bpy.types.Mesh, obj.data)
        pending_attr = mesh.attributes[_SPLIT_PENDING_ATTR_NAME]
        pending_indices = [p.index for p in mesh.polygons if pending_attr.data[p.index].value]
        if not pending_indices:
            break
        if len(mesh.polygons) == 1:
            break
        target_index = pending_indices[0]

        for o in context.selected_objects:
            o.select_set(False)
        obj.select_set(True)
        if context.view_layer:
            context.view_layer.objects.active = obj
        # Deselect via Edit Mode first - confirmed live that setting polygon.select directly in
        # Object Mode without first clearing Blender's edit-mesh selection state through this exact
        # dance leaves a stale selection behind, and mesh.ops.separate(type="SELECTED") ends up
        # acting on that stale (larger) selection instead of the one just set. Same sequence
        # BatchPrepareDecalsForScene (decal_prep_menu.py) already relies on.
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="DESELECT")
        bpy.ops.object.mode_set(mode="OBJECT")
        for p in mesh.polygons:
            p.select = p.index == target_index
        mesh.update()

        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.separate(type="SELECTED")
        bpy.ops.object.mode_set(mode="OBJECT")

        new_piece = next((o for o in context.selected_objects if o is not obj and o.type == "MESH"), None)
        if new_piece is not None:
            _copy_rel_settings(obj, new_piece)
            new_mesh = cast(bpy.types.Mesh, new_piece.data)
            stale = new_mesh.attributes.get(_SPLIT_PENDING_ATTR_NAME)
            if stale is not None:
                new_mesh.attributes.remove(stale)
            pieces.append(new_piece)

    mesh = cast(bpy.types.Mesh, obj.data)
    pending_attr = mesh.attributes.get(_SPLIT_PENDING_ATTR_NAME)
    still_pending = pending_attr is not None and any(pending_attr.data[p.index].value for p in mesh.polygons)
    if pending_attr is not None:
        mesh.attributes.remove(pending_attr)
    if still_pending:
        pieces.append(obj)
    return pieces


class SceneWithReliefDisplaceSettings(bpy.types.Scene):
    pso_relief_displace_strength: float
    pso_relief_displace_subdivisions: int
    pso_relief_height_blur_radius: int
    pso_relief_safety_face_limit: int


def register_scene_properties():
    cast(Any, bpy.types.Scene).pso_relief_displace_strength = FloatProperty(
        name="Strength",
        description=(
            "Displace modifier strength - fine-tune an individual object afterward via Blender's "
            "own Modifier panel"),
        default=0.2,
        soft_min=-1.0,
        soft_max=1.0)
    # Subdivision growth is exponential (4x per level), not linear - confirmed live it's the
    # dominant factor in a real memory-exhaustion crash on BatchApplyReliefDisplacementForActive
    # Material (see pso_relief_safety_face_limit below), so unlike Strength this genuinely needs
    # per-material judgment, not just a one-time "settled" value - kept as a real slider.
    cast(Any, bpy.types.Scene).pso_relief_displace_subdivisions = IntProperty(
        name="Subdivisions",
        description=(
            "Simple Subdivision Surface level added before displacing - each level roughly "
            "quadruples the face count, so raise this carefully on a material used by many faces"),
        default=4,
        min=1,
        soft_max=8)
    # No pso_relief_falloff_distance property - the anti-crack falloff it would control isn't
    # wired to anything right now (see _apply_subdivide_and_displace), so a slider for it would
    # just be misleading; _compute_seam_falloff_distances/_build_falloff_vertex_group are kept in
    # this module, unused, in case a working approach is found later.
    cast(Any, bpy.types.Scene).pso_relief_height_blur_radius = IntProperty(
        name="Height Blur Radius",
        description=(
            "Box blur radius (in source texture pixels) applied to the derived height data before "
            "displacing - smooths out high-frequency noise/spikes, meant to be paired with a "
            "higher Strength to still get a marked relief with less subdivision"),
        default=2,
        min=0,
        soft_max=8)
    cast(Any, bpy.types.Scene).pso_relief_safety_face_limit = IntProperty(
        name="Safety Face Limit",
        description=(
            "Apply Relief Displacement (Whole Map, Active Material) refuses to run if the "
            "estimated evaluated face count (matching faces x 4^Subdivisions) exceeds this - "
            "confirmed live that a material spanning only 545 faces at level 6 produced 2.2 "
            "million evaluated faces and crashed Blender from memory exhaustion. Raise only if "
            "you're sure your machine can handle it"),
        default=500000,
        min=1000,
        soft_max=5000000)


def unregister_scene_properties():
    del cast(Any, bpy.types.Scene).pso_relief_displace_strength
    del cast(Any, bpy.types.Scene).pso_relief_displace_subdivisions
    del cast(Any, bpy.types.Scene).pso_relief_height_blur_radius
    del cast(Any, bpy.types.Scene).pso_relief_safety_face_limit


@final
class BatchApplyReliefDisplacement(Operator):  # pyright: ignore[reportIncompatibleMethodOverride]
    """Adds a non-destructive Simple Subdivision Surface + Displace modifier pair so the relief
    already painted into a material's normal/diffuse texture becomes real 3D geometry instead of
    just a shader-level darkening trick (_wire_relief_composite, xj.py). Never applies/bakes
    anything into the base mesh - every export path already reads geometry through
    obj.evaluated_get(depsgraph).to_mesh() (c_rel.py, n_rel.py, r_rel.py, xj.py), which evaluates
    live modifiers, so this stays fully live/inspectable in the viewport and undoable with a plain
    Ctrl+Z or by just deleting the modifiers.

    Primary path (mirrors BatchPrepareDecalsForScene's Edit-Mode-selection priority,
    decal_prep_menu.py): if the active object is a mesh in Edit Mode with faces selected, those
    faces must share a single texture (validated the same way as there), get split one-per-face
    into their own objects (a real REL file-format constraint - see rel.py:61's pointer table,
    ~262KB max gap between two consecutive pointers - made splitting into many small objects
    necessary on large objects, not just a Displace-modifier convenience). No anti-crack blending
    between pieces right now - confirmed live that a distance-based vertex group
    (_compute_seam_falloff_distances/_build_falloff_vertex_group, still in this module but unused)
    doesn't work at single-face granularity: most interior faces have every vertex sitting exactly
    on a seam, which always gets weight 0 regardless of the falloff distance, flattening the whole
    piece.

    See also BatchApplyReliefDisplacementForActiveMaterial below, for running this same per-face
    treatment across every object in the scene that shares the active material's texture, instead
    of one object at a time.

    Fallback path (legacy, from before per-face splitting was needed - kept as-is, NOT updated to
    split by face, so large objects run through it can still hit the rel.py:61 export crash):
    whatever mesh objects are selected, or every N.REL mesh object in the scene if nothing is
    selected, gets one Displace per material actually used, restricted via an exclusive
    per-material vertex group - no object splitting.
    """

    bl_idname = "object.pso_batch_relief_displace"
    bl_label = "Apply Relief Displacement"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context):
        return context.view_layer is not None and context.scene is not None

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        view_layer = context.view_layer
        scene = context.scene
        if view_layer is None or scene is None:
            self.report({"ERROR"}, "No active scene/view layer")
            return {"CANCELLED"}

        scene_settings = cast(SceneWithReliefDisplaceSettings, scene)
        strength = cast(float, scene_settings.pso_relief_displace_strength)
        subdivisions = cast(int, scene_settings.pso_relief_displace_subdivisions)
        blur_radius = cast(int, scene_settings.pso_relief_height_blur_radius)

        height_image_cache: dict[str, bpy.types.Image] = {}
        height_texture_cache: dict[str, bpy.types.Texture] = {}

        active_obj = context.active_object
        if active_obj is not None and active_obj.type == "MESH" and active_obj.mode == "EDIT":
            edit_mesh = cast(bpy.types.Mesh, active_obj.data)
            bm = bmesh.from_edit_mesh(edit_mesh)
            selected_faces = [f for f in bm.faces if f.select]
            if selected_faces:
                material_indices = {f.material_index for f in selected_faces}
                images_used: set[str] = set()
                material_by_index: dict[int, bpy.types.Material] = {}
                for mi in material_indices:
                    if mi >= len(active_obj.material_slots):
                        continue
                    mat = active_obj.material_slots[mi].material
                    if mat is None:
                        continue
                    material_by_index[mi] = mat
                    image = find_diffuse_image(mat)
                    images_used.add(image.name if image is not None else "")
                if len(images_used) > 1:
                    self.report({"ERROR"}, (
                        "Selected faces use {} different textures - a single texture is needed so "
                        "the right material can be applied to every separated piece. Select faces "
                        "using just one texture."
                    ).format(len(images_used)))
                    return {"CANCELLED"}
                if not material_by_index:
                    self.report({"ERROR"}, "No material found on selected faces")
                    return {"CANCELLED"}
                material = next(iter(material_by_index.values()))

                # Anti-crack falloff (_compute_seam_falloff_distances/_build_falloff_vertex_group)
                # deliberately not called here - confirmed live it doesn't work at single-face
                # granularity (see _apply_subdivide_and_displace) and building it would just be
                # wasted work.
                bpy.ops.object.mode_set(mode="OBJECT")

                pieces = _split_selection_by_face(context, active_obj)

                failed: list[str] = []
                for piece in pieces:
                    error = _apply_subdivide_and_displace(
                        piece, material, strength, subdivisions, blur_radius, height_image_cache, height_texture_cache)
                    if error is not None:
                        failed.append(error)

                if failed:
                    self.report({"WARNING"}, "{} piece(s) had no usable texture: {}".format(len(failed), failed[0]))
                self.report({"INFO"}, "Created {} piece(s) from the selection".format(len(pieces)))
                return {"FINISHED"}

        # --- Legacy whole-object/scene batch path (see class docstring) ---
        selected_meshes = [o for o in context.selected_objects if o.type == "MESH"]
        source_objects = selected_meshes if selected_meshes else [o for o in view_layer.objects if o.type == "MESH"]

        wm = context.window_manager
        wm.progress_begin(0, max(1, len(source_objects)))

        processed_count = 0
        skipped_count = 0
        fallback_luminance_count = 0
        no_texture_material_count = 0
        try:
            for i, obj in enumerate(source_objects):
                wm.progress_update(i)

                rel_settings = cast(ObjectWithRelSettings, obj).rel_settings
                if not cast(bool, rel_settings.is_nrel):
                    skipped_count += 1
                    continue
                if cast(bool, rel_settings.is_crel) or cast(bool, rel_settings.is_rrel):
                    skipped_count += 1
                    continue
                if cast(bool, rel_settings.exclude_from_relief_displacement):
                    skipped_count += 1
                    continue
                if any(m.name.startswith(_MODIFIER_NAME_PREFIX) for m in obj.modifiers):
                    skipped_count += 1
                    continue

                mesh = cast(bpy.types.Mesh, obj.data)
                material_indices = {p.material_index for p in mesh.polygons}
                per_material_texture: dict[int, bpy.types.Texture] = {}
                for mi in material_indices:
                    if mi >= len(obj.material_slots):
                        continue
                    mat = obj.material_slots[mi].material
                    if mat is None:
                        continue

                    resolved = _resolve_height_source(mat)
                    if resolved is None:
                        no_texture_material_count += 1
                        continue
                    source_image, mode = resolved
                    if mode == "luminance":
                        fallback_luminance_count += 1
                    height_image = _get_or_build_height_image(height_image_cache, source_image, mode, blur_radius)

                    per_material_texture[mi] = _get_or_build_height_texture(height_texture_cache, height_image)

                if not per_material_texture:
                    skipped_count += 1
                    continue

                vertex_groups = _build_material_vertex_groups(obj)

                for stale_name in [m.name for m in obj.modifiers if m.name.startswith(_MODIFIER_NAME_PREFIX)]:
                    obj.modifiers.remove(obj.modifiers[stale_name])

                subsurf = cast(bpy.types.SubsurfModifier, obj.modifiers.new(
                    name=_MODIFIER_NAME_PREFIX + "Subdivide", type="SUBSURF"))
                subsurf.subdivision_type = "SIMPLE"
                subsurf.levels = subdivisions
                subsurf.render_levels = subdivisions

                for mi, texture in per_material_texture.items():
                    group = vertex_groups.get(mi)
                    if group is None:
                        continue
                    displace = cast(bpy.types.DisplaceModifier, obj.modifiers.new(
                        name="{}Displace_Mat{}".format(_MODIFIER_NAME_PREFIX, mi), type="DISPLACE"))
                    displace.texture = texture
                    displace.vertex_group = group.name
                    displace.direction = "NORMAL"
                    displace.mid_level = 0.5
                    displace.strength = strength
                    displace.texture_coords = "UV"

                processed_count += 1
        finally:
            wm.progress_end()

        self.report({"INFO"}, (
            "Applied relief displacement to {} object(s); {} skipped; {} material(s) used the "
            "diffuse luminance fallback (no normal map); {} material(s) had no texture data at all"
        ).format(processed_count, skipped_count, fallback_luminance_count, no_texture_material_count))
        return {"FINISHED"}


def _count_matching_faces(obj: bpy.types.Object, target_image_name: str) -> int:
    """How many of obj's faces use a material whose diffuse image matches target_image_name - the
    read-only counting half of _select_and_split_by_image's matching logic, used to estimate cost
    before actually touching anything."""
    mesh = cast(bpy.types.Mesh, obj.data)
    matching_slot_indices: set[int] = set()
    for i, slot in enumerate(obj.material_slots):
        mat = slot.material
        if mat is None:
            continue
        image = find_diffuse_image(mat)
        if image is not None and image.name == target_image_name:
            matching_slot_indices.add(i)
    if not matching_slot_indices:
        return 0
    return sum(1 for p in mesh.polygons if p.material_index in matching_slot_indices)


def _select_and_split_by_image(
        context: Context, obj: bpy.types.Object, target_image_name: str) -> "tuple[list[bpy.types.Object], bpy.types.Material | None]":
    """Selects every face on obj whose material slot's diffuse image matches target_image_name
    (by name, not material reference - the same physical texture routinely gets several material
    variants in this addon's imported materials, different blend mode/addressing render state, see
    the identical matching already done in BatchPrepareDecalsForScene's Edit-Mode path), then
    splits them one-per-face via _split_selection_by_face. Returns ([], None) if obj has no
    matching faces at all - a normal, expected outcome for most objects on a real map, not an
    error. Returns (pieces, one of the matching materials) otherwise - any matching variant works
    as the source for height data, they're visually equivalent by construction.
    """
    mesh = cast(bpy.types.Mesh, obj.data)
    matching_slot_indices: set[int] = set()
    material_used: "bpy.types.Material | None" = None
    for i, slot in enumerate(obj.material_slots):
        mat = slot.material
        if mat is None:
            continue
        image = find_diffuse_image(mat)
        if image is not None and image.name == target_image_name:
            matching_slot_indices.add(i)
            material_used = mat
    if not matching_slot_indices or not any(p.material_index in matching_slot_indices for p in mesh.polygons):
        return ([], None)

    for o in context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    if context.view_layer:
        context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.object.mode_set(mode="OBJECT")
    for p in mesh.polygons:
        p.select = p.material_index in matching_slot_indices
    mesh.update()

    pieces = _split_selection_by_face(context, obj)
    return (pieces, material_used)


@final
class BatchApplyReliefDisplacementForActiveMaterial(Operator):  # pyright: ignore[reportIncompatibleMethodOverride]
    """Runs the exact same per-face split + Subdivide/Displace treatment as
    BatchApplyReliefDisplacement's Edit-Mode path, but scoped to every N.REL mesh object in the
    scene that has at least one face using the active object's active material's texture, instead
    of requiring the user to hand-select faces on every single object that happens to share it - a
    texture is routinely reused across dozens of objects on a real map (e.g. 124/361 materials in
    map_acity00 shared more than one object).

    Naturally idempotent: a face gets physically moved out of its source object the first time
    it's processed, so re-running this after already covering an object simply finds no more
    matching faces there.
    """

    bl_idname = "object.pso_batch_relief_displace_material"
    bl_label = "Apply Relief Displacement (Whole Map, Active Material)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context):
        obj = context.object
        return (
            context.view_layer is not None and context.scene is not None
            and obj is not None and obj.type == "MESH" and obj.active_material is not None)

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        view_layer = context.view_layer
        scene = context.scene
        active_obj = context.object
        if view_layer is None or scene is None or active_obj is None:
            self.report({"ERROR"}, "No active scene/view layer/object")
            return {"CANCELLED"}

        active_material = active_obj.active_material
        if active_material is None:
            self.report({"ERROR"}, "Active object has no active material")
            return {"CANCELLED"}
        target_image = find_diffuse_image(active_material)
        if target_image is None:
            self.report({"ERROR"}, "Active material has no usable image")
            return {"CANCELLED"}

        scene_settings = cast(SceneWithReliefDisplaceSettings, scene)
        strength = cast(float, scene_settings.pso_relief_displace_strength)
        subdivisions = cast(int, scene_settings.pso_relief_displace_subdivisions)
        blur_radius = cast(int, scene_settings.pso_relief_height_blur_radius)
        safety_limit = cast(int, scene_settings.pso_relief_safety_face_limit)

        height_image_cache: dict[str, bpy.types.Image] = {}
        height_texture_cache: dict[str, bpy.types.Texture] = {}

        # Snapshot before starting - new pieces get created and linked as this loop runs,
        # iterating a live objects collection here would pick them up too (same reasoning as
        # BatchPrepareDecalsForScene, decal_prep_menu.py).
        source_objects = [o for o in view_layer.objects if o.type == "MESH"]

        # Safety check BEFORE touching anything - confirmed live that this operator can produce
        # enough evaluated geometry to exhaust memory and crash Blender outright (a material used
        # by only 545 faces across 5 objects, at subdivision level 6, produced 2.2 million
        # evaluated faces and a ~46GB allocation). Subsurf growth is exponential per level (4x),
        # not linear, so this has to be checked up front rather than discovered mid-run.
        matching_face_count = 0
        for obj in source_objects:
            rel_settings = cast(ObjectWithRelSettings, obj).rel_settings
            if not cast(bool, rel_settings.is_nrel):
                continue
            if cast(bool, rel_settings.is_crel) or cast(bool, rel_settings.is_rrel):
                continue
            if cast(bool, rel_settings.exclude_from_relief_displacement):
                continue
            matching_face_count += _count_matching_faces(obj, target_image.name)
        estimated_evaluated_faces = matching_face_count * (4 ** subdivisions)
        if estimated_evaluated_faces > safety_limit:
            self.report({"ERROR"}, (
                "This material covers {} face(s) across the map - at subdivision level {}, that's "
                "an estimated {} evaluated faces, above the {} safety limit (Safety Face Limit in "
                "the panel) and likely to exhaust memory. Lower the subdivision level, narrow the "
                "selection, or raise the safety limit if you're sure."
            ).format(matching_face_count, subdivisions, estimated_evaluated_faces, safety_limit))
            return {"CANCELLED"}

        wm = context.window_manager
        wm.progress_begin(0, max(1, len(source_objects)))

        total_pieces = 0
        objects_touched = 0
        failed: list[str] = []
        try:
            for i, obj in enumerate(source_objects):
                wm.progress_update(i)

                rel_settings = cast(ObjectWithRelSettings, obj).rel_settings
                if not cast(bool, rel_settings.is_nrel):
                    continue
                if cast(bool, rel_settings.is_crel) or cast(bool, rel_settings.is_rrel):
                    continue
                if cast(bool, rel_settings.exclude_from_relief_displacement):
                    continue

                pieces, material = _select_and_split_by_image(context, obj, target_image.name)
                if not pieces or material is None:
                    continue
                objects_touched += 1
                for piece in pieces:
                    error = _apply_subdivide_and_displace(
                        piece, material, strength, subdivisions, blur_radius, height_image_cache, height_texture_cache)
                    if error is not None:
                        failed.append(error)
                total_pieces += len(pieces)
        finally:
            wm.progress_end()

        if failed:
            self.report({"WARNING"}, "{} piece(s) had no usable texture: {}".format(len(failed), failed[0]))
        self.report({"INFO"}, "Created {} piece(s) across {} object(s) using this material's texture".format(
            total_pieces, objects_touched))
        return {"FINISHED"}


def _build_material_vertex_groups(obj: bpy.types.Object) -> "dict[int, bpy.types.VertexGroup]":
    """Legacy path only (see BatchApplyReliefDisplacement's fallback branch) - one exclusive
    vertex group per material_index actually used on obj.data.polygons, named
    "PSO_Relief_Mat<index>". A vertex touching faces of more than one material (a seam) is
    assigned only to the group of its majority adjacent material (ties broken toward the lower
    material_index) - each vertex therefore ends up in at most one of the stacked Displace
    modifiers, avoiding double-displacement at material boundaries.

    Removes any stale "PSO_Relief_*" vertex groups from a previous run first, so re-running this
    doesn't accumulate duplicates.
    """
    mesh = cast(bpy.types.Mesh, obj.data)

    vertex_material_counts: dict[int, dict[int, int]] = {}
    for poly in mesh.polygons:
        for vert_index in poly.vertices:
            counts = vertex_material_counts.setdefault(vert_index, {})
            counts[poly.material_index] = counts.get(poly.material_index, 0) + 1

    verts_by_material: dict[int, list[int]] = {}
    for vert_index, counts in vertex_material_counts.items():
        best_material_index = max(counts.items(), key=lambda kv: (kv[1], -kv[0]))[0]
        verts_by_material.setdefault(best_material_index, []).append(vert_index)

    for stale_name in [vg.name for vg in obj.vertex_groups if vg.name.startswith(_MODIFIER_NAME_PREFIX)]:
        obj.vertex_groups.remove(obj.vertex_groups[stale_name])

    groups: dict[int, bpy.types.VertexGroup] = {}
    for material_index, vert_indices in verts_by_material.items():
        group = obj.vertex_groups.new(name="{}Mat{}".format(_MODIFIER_NAME_PREFIX, material_index))
        group.add(vert_indices, 1.0, "REPLACE")
        groups[material_index] = group
    return groups


@final
class PSO_PT_relief_displace(Panel):  # pyright: ignore[reportIncompatibleMethodOverride]
    """A new, separate direction from the lighting-bake/decal work (bake_lighting_menu.py,
    decal_prep_menu.py) - makes the relief already painted into each material's textures a real,
    non-destructive geometric bump instead of just a shader-level darkening trick, kept in its own
    clearly-marked "Experimental" section since the visual payoff is still being evaluated."""

    bl_idname = "PSO_PT_relief_displace"
    bl_label = "Mesh Relief Displacement (Experimental)"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "pso-blender"

    def draw(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        layout = self.layout
        if not layout or context.scene is None:
            return
        scene = cast(SceneWithReliefDisplaceSettings, context.scene)
        col = layout.column(align=True)
        col.prop(scene, "pso_relief_displace_strength")
        col.prop(scene, "pso_relief_displace_subdivisions")
        col.prop(scene, "pso_relief_height_blur_radius")
        col.prop(scene, "pso_relief_safety_face_limit")
        layout.operator(BatchApplyReliefDisplacement.bl_idname, icon="MOD_DISPLACE")
        layout.operator(BatchApplyReliefDisplacementForActiveMaterial.bl_idname, icon="MATERIAL")
