from typing import cast, final
import bpy
import bmesh
from mathutils import Vector
from bpy.props import IntProperty  # pyright: ignore[reportUnknownVariableType]
from bpy.types import Context, Operator
from .util import find_diffuse_image


def _separate_and_build_decal(
        context: Context, source_obj: bpy.types.Object, image_size: int,
        original_material: "bpy.types.Material | None",
        original_uv_layer_name: "str | None") -> "tuple[bpy.types.Object, int] | str":
    """Assumes source_obj is already in Edit Mode with the intended faces selected. Separates
    them into their own object, gives that object a clean UV layer named "UVMap" at list position
    0 (a flat orthogonal projection along the selected faces' average normal - computed directly
    rather than via Project From View, so it doesn't depend on the 3D viewport's current camera
    angle at all), a brand new dedicated material, and an Image Texture node (a fresh blank image)
    wired through a UV Map node pointing at "UVMap" - this is the actual bake target.

    `original_uv_layer_name` must be captured by the CALLER while source_obj is still in Object
    Mode (e.g. `source_mesh.uv_layers.active.name` before switching to Edit Mode) - confirmed live
    that reading a mesh's UV layer name while already in Edit Mode returns an empty string, not
    the real name, so this function can't safely determine it itself.

    The ORIGINAL UV layer (whichever was active) is preserved too, rebuilt under the name
    "OriginalUV" and kept at a position other than 0 (uv_layers.new() always appends - "UVMap" is
    simply created first, before anything else touches the UV layer list, to land at 0
    unambiguously - see write_vertex_buffer in xj.py, which always reads uv_layers[0] by list
    position for export, regardless of what any material's own nodes point to). If
    `original_material` has a diffuse image, a second Image Texture node - Repeat extension, no
    addressing-math chain, matching the bake-only compromise already used elsewhere in this addon -
    reads it through a UV Map node pointing at "OriginalUV" and feeds it into Base Color. This is
    what lets a later Combined bake capture the real texture's color together with scene lighting
    in one pass, instead of baking lighting onto a blank/black surface.

    Returns (new_object, face_count) on success, or an error message string on failure - never
    raises, so a caller batching many of these can skip one failure without aborting the rest.
    """
    source_mesh = cast(bpy.types.Mesh, source_obj.data)

    bm = bmesh.from_edit_mesh(source_mesh)
    selected_faces = [f for f in bm.faces if f.select]
    if not selected_faces:
        return "No faces selected"
    if len(selected_faces) == len(bm.faces):
        # Separating every face would leave the source object completely empty - almost certainly
        # a stale/accidental "select all" (or, in a batch run, an object that only ever had one
        # material) rather than an intentional decal on the object's entire surface, and an empty
        # mesh fails loudly at export time rather than doing anything useful - cheaper to catch it
        # here.
        return "All faces on '{}' are selected - this would empty the source object entirely.".format(source_obj.name)

    # Average normal in WORLD space (mesh normals are local) - this is what the new UV layer's
    # projection axis is built from, matching what aiming an orthographic viewport straight at the
    # surface and using Project From View would produce, without needing an actual viewport
    # aligned to it.
    world_matrix = source_obj.matrix_world
    normal_matrix = world_matrix.to_3x3().inverted().transposed()
    avg_normal = Vector((0.0, 0.0, 0.0))
    for f in selected_faces:
        avg_normal += (normal_matrix @ f.normal).normalized()
    if avg_normal.length < 1e-8:
        return "Selected faces have no well-defined average normal (cancelling normals out)"
    avg_normal.normalize()

    bpy.ops.mesh.separate(type="SELECTED")
    bpy.ops.object.mode_set(mode="OBJECT")

    new_obj = next((o for o in context.selected_objects if o is not source_obj and o.type == "MESH"), None)
    if new_obj is None:
        return "Could not find the newly separated object"
    new_mesh = cast(bpy.types.Mesh, new_obj.data)
    base_name = source_obj.name + "_decal"
    new_obj.name = base_name
    new_mesh.name = base_name + "_mesh"

    # mesh.separate() copies every UV layer from the source mesh, correctly remapped to the new
    # object's own loop indices - read that copy's data by name before deleting everything.
    separated_original_data: list[tuple[float, float]] | None = None
    if original_uv_layer_name is not None:
        separated_layer = new_mesh.uv_layers.get(original_uv_layer_name)
        if separated_layer is not None:
            separated_original_data = [tuple(loop.uv) for loop in separated_layer.data]  # pyright: ignore[reportAssignmentType]
    for layer in list(new_mesh.uv_layers):
        new_mesh.uv_layers.remove(layer)

    # "UVMap" is created FIRST, before any other UV layer exists on this mesh, so it lands
    # unambiguously at list position 0 - uv_layers.new() always appends, there's no reorder API,
    # so creation order is the only way to control this.
    uv_layer = new_mesh.uv_layers.new(name="UVMap")
    new_mesh.uv_layers.active = uv_layer

    # Flat orthogonal projection along avg_normal - equivalent to Project From View with the
    # viewport aimed straight at the surface, computed directly instead so it can't depend on (or
    # be broken by) whatever the 3D viewport happens to be looking at when this runs.
    quat = avg_normal.to_track_quat("Z", "Y")
    inv_quat = quat.inverted()
    new_world_matrix = new_obj.matrix_world
    projected: list[tuple[float, float]] = []
    for loop in new_mesh.loops:
        world_co = new_world_matrix @ new_mesh.vertices[loop.vertex_index].co
        local_co = inv_quat @ world_co
        projected.append((local_co.x, local_co.y))

    min_x = min(p[0] for p in projected)
    max_x = max(p[0] for p in projected)
    min_y = min(p[1] for p in projected)
    max_y = max(p[1] for p in projected)
    # Uniform scale (not stretched per-axis) so the projection doesn't distort proportions -
    # matches what an orthographic Project From View naturally does.
    span = max(max_x - min_x, max_y - min_y, 1e-8)
    for i, (x, y) in enumerate(projected):
        uv_layer.data[i].uv = ((x - min_x) / span, (y - min_y) / span)

    # "OriginalUV" created second, so it's never at position 0 - restores the mesh's real UV
    # (used to correctly sample the original texture as a Base Color reference below) without
    # disturbing what export reads.
    if separated_original_data is not None and len(separated_original_data) == len(new_mesh.loops):
        original_layer = new_mesh.uv_layers.new(name="OriginalUV")
        for i, uv in enumerate(separated_original_data):
            original_layer.data[i].uv = uv

    # Dedicated material and image - genuinely new datablocks, not shared with anything else in
    # the file, so nothing else can be affected by a later bake into this image.
    new_mat = bpy.data.materials.new(base_name + "_mat")
    new_mat.use_nodes = True
    node_tree = new_mat.node_tree
    assert node_tree is not None
    bsdf_node = next(n for n in node_tree.nodes if n.type == "BSDF_PRINCIPLED")

    uv_map_node = cast(bpy.types.ShaderNodeUVMap, node_tree.nodes.new(type="ShaderNodeUVMap"))
    uv_map_node.uv_map = uv_layer.name
    uv_map_node.location = (bsdf_node.location.x - 600, bsdf_node.location.y + 200)

    image_node = cast(bpy.types.ShaderNodeTexImage, node_tree.nodes.new(type="ShaderNodeTexImage"))
    new_image = bpy.data.images.new(base_name + "_bake", image_size, image_size)
    image_node.image = new_image
    image_node.location = (bsdf_node.location.x - 300, bsdf_node.location.y + 200)
    image_node.name = "PSO_DecalBakeTarget"

    node_tree.links.new(uv_map_node.outputs["UV"], image_node.inputs["Vector"])

    # Base Color reference to the ORIGINAL texture (if there is one), read through "OriginalUV" -
    # a plain Repeat-extension Image Texture, not this addon's usual addressing-math chain (that
    # chain is confirmed to break Cycles bake evaluation - see bake_lighting_menu.py - and this is
    # only a rendering-equation input for the bake, never exported itself). Without this, Base
    # Color stays at the Principled BSDF's own default and a Combined bake only ever captures
    # specular/reflection response, not the real texture's color.
    if original_material is not None and separated_original_data is not None:
        original_image = find_diffuse_image(original_material)
        if original_image is not None:
            orig_uv_map_node = cast(bpy.types.ShaderNodeUVMap, node_tree.nodes.new(type="ShaderNodeUVMap"))
            orig_uv_map_node.uv_map = "OriginalUV"
            orig_uv_map_node.location = (bsdf_node.location.x - 600, bsdf_node.location.y - 200)

            orig_image_node = cast(bpy.types.ShaderNodeTexImage, node_tree.nodes.new(type="ShaderNodeTexImage"))
            orig_image_node.image = original_image
            orig_image_node.extension = "REPEAT"
            orig_image_node.location = (bsdf_node.location.x - 300, bsdf_node.location.y - 200)
            orig_image_node.name = "PSO_OriginalTextureReference"

            node_tree.links.new(orig_uv_map_node.outputs["UV"], orig_image_node.inputs["Vector"])
            node_tree.links.new(orig_image_node.outputs["Color"], bsdf_node.inputs["Base Color"])

    new_mesh.materials.clear()
    new_mesh.materials.append(new_mat)

    # Tagged so a later, separate "Bake All Decals" run (bake_lighting_menu.py) can find every
    # decal object without needing to be chained in the same operator call, or relying on naming.
    new_obj["pso_is_decal"] = True

    for o in context.selected_objects:
        o.select_set(False)
    new_obj.select_set(True)
    if context.view_layer:
        context.view_layer.objects.active = new_obj

    return (new_obj, len(selected_faces))


@final
class BatchPrepareDecalsForScene(Operator):  # pyright: ignore[reportIncompatibleMethodOverride]
    """Automates the mechanical setup steps of the dedicated-texture-decal workflow (see
    next_subjects_to_work.md, branch decal_light_experiment) across every mesh object it targets,
    for every material each one uses - no manual triangle selection needed. For each (object,
    material) pair with faces, this is the exact same separate-to-own-object / clean-UV /
    dedicated-material-and-image recipe _separate_and_build_decal already does interactively, just
    driven by material slot instead of a hand-made Edit Mode selection.

    Targets whatever mesh objects are selected when run, or every mesh object in the scene if
    nothing is selected - the usual Blender convention for "act on selection, or everything if
    there's no selection", so a quick test run on a couple of objects doesn't require reselecting
    their faces by hand first, and a real full-map run just needs Select All beforehand (or
    nothing selected at all).

    Deliberately does NOT bake anything - lighting must already be set up in the scene by hand
    first; baking is BatchBakeDecals (bake_lighting_menu.py), a separate, explicit step.
    """

    bl_idname = "object.pso_batch_prepare_decals"
    bl_label = "Prepare Selection As Decals"
    bl_options = {"REGISTER", "UNDO"}

    image_size: IntProperty(name="Image Size", default=512, min=8, max=8192)  # pyright: ignore[reportInvalidTypeForm]

    @classmethod
    def poll(cls, context: Context):
        return context.view_layer is not None

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        view_layer = context.view_layer
        if view_layer is None:
            self.report({"ERROR"}, "No active view layer")
            return {"CANCELLED"}

        # Hand-picked face selection takes priority: if the active object is in Edit Mode with
        # some faces actually selected, build exactly one decal from that selection instead of
        # the whole-object/material-driven batch below - this is what lets a mesh with a couple of
        # stray faces sharing a material by coincidence (a real case hit earlier this session,
        # 20_72_node_0) be excluded by hand instead of always being swept in automatically.
        active_obj = context.active_object
        if active_obj is not None and active_obj.type == "MESH" and active_obj.mode == "EDIT":
            edit_mesh = cast(bpy.types.Mesh, active_obj.data)
            bm = bmesh.from_edit_mesh(edit_mesh)
            selected_faces = [f for f in bm.faces if f.select]
            if selected_faces:
                material_indices = {f.material_index for f in selected_faces}
                # Checked by underlying diffuse IMAGE, not material slot index - this addon
                # routinely gives the same physical texture several material variants (different
                # blend mode/addressing render state, e.g. "..._173_f04bd6ce" and
                # "..._173_1d218e0b" both reading map_acity00.xvm_xvr_173), which look visually
                # identical but count as different material_index values. Faces from several such
                # variants of the SAME image are fine to combine into one decal; genuinely
                # different images are not, since there'd be no single answer for which one to use
                # as the Base Color reference.
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
                        "Selected faces use {} different textures - a decal needs faces using a "
                        "single texture only, so the right original texture can be found. Select "
                        "faces using just one texture."
                    ).format(len(images_used)))
                    return {"CANCELLED"}
                original_material = next(iter(material_by_index.values())) if material_by_index else None

                # Must be read here, in Object Mode - confirmed live that a UV layer's .name comes
                # back as an empty string once already in Edit Mode. Mode toggling preserves the
                # user's hand-made face selection on the same object.
                bpy.ops.object.mode_set(mode="OBJECT")
                active_uv_layer = edit_mesh.uv_layers.active
                original_uv_layer_name = active_uv_layer.name if active_uv_layer is not None else None
                bpy.ops.object.mode_set(mode="EDIT")

                result = _separate_and_build_decal(
                    context, active_obj, self.image_size, original_material, original_uv_layer_name)
                bpy.ops.object.mode_set(mode="OBJECT")

                if isinstance(result, str):
                    self.report({"ERROR"}, result)
                    return {"CANCELLED"}
                new_obj, face_count = result
                self.report({"INFO"}, "Created '{}' from {} selected face(s)".format(new_obj.name, face_count))
                return {"FINISHED"}

        # Snapshot before starting - new decal objects get created and linked/selected as this
        # loop runs, so iterating a live objects collection here would pick them up too. Scoped to
        # the current selection if there is one, matching Blender's usual "selection, or
        # everything" convention - lets a quick test target just a couple of objects without
        # needing to reselect their faces by hand.
        selected_meshes = [o for o in context.selected_objects if o.type == "MESH"]
        source_objects = selected_meshes if selected_meshes else [o for o in view_layer.objects if o.type == "MESH"]

        # Total (object, material) pairs with actual face coverage, counted up front purely to
        # give the progress cursor a real denominator - a batch run across a whole map has no way
        # for the user to otherwise guess how long it'll take. A popup progress bar was tried
        # elsewhere in this addon (see start_modal_steps, util.py) and caused enough extra screen
        # refresh to crash Blender - the built-in cursor percentage (wm.progress_begin/update/end)
        # is the one variant confirmed safe.
        total_pairs = 0
        for o in source_objects:
            slots_with_faces = {p.material_index for p in cast(bpy.types.Mesh, o.data).polygons}
            total_pairs += len(slots_with_faces)

        original_selected = list(context.selected_objects)
        original_active = view_layer.objects.active

        wm = context.window_manager
        wm.progress_begin(0, max(1, total_pairs))
        done_count = 0

        created_count = 0
        skipped_count = 0
        failed_count = 0
        try:
            for source_obj in source_objects:
                # Material indices that failed on this object - excluded from the search below so
                # a material _separate_and_build_decal can't handle (e.g. degenerate/cancelling
                # normals) doesn't get retried forever; its faces are left as-is on source_obj
                # (that call fails before mesh.separate() ever runs) and processing continues with
                # this object's OTHER materials instead of abandoning it entirely.
                unusable_material_indices: set[int] = set()
                # A material can end up with 0 remaining faces on this object partway through
                # (its faces already moved out by an earlier iteration below) - re-scan fresh
                # every time rather than trusting a count taken before this object's own loop
                # started.
                while True:
                    mesh = cast(bpy.types.Mesh, source_obj.data)
                    material_index = next(
                        (i for i, s in enumerate(source_obj.material_slots)
                         if i not in unusable_material_indices and s.material is not None
                         and any(p.material_index == i for p in mesh.polygons)),
                        None)
                    if material_index is None:
                        break
                    original_material = source_obj.material_slots[material_index].material

                    for o in context.selected_objects:
                        o.select_set(False)
                    source_obj.select_set(True)
                    view_layer.objects.active = source_obj
                    bpy.ops.object.mode_set(mode="EDIT")
                    bpy.ops.mesh.select_all(action="DESELECT")
                    bpy.ops.object.mode_set(mode="OBJECT")
                    for p in mesh.polygons:
                        p.select = p.material_index == material_index
                    mesh.update()
                    # Must be read here, in Object Mode - confirmed live that a UV layer's .name
                    # comes back as an empty string once already in Edit Mode.
                    active_uv_layer = mesh.uv_layers.active
                    original_uv_layer_name = active_uv_layer.name if active_uv_layer is not None else None
                    bpy.ops.object.mode_set(mode="EDIT")

                    result = _separate_and_build_decal(
                        context, source_obj, self.image_size, original_material, original_uv_layer_name)
                    bpy.ops.object.mode_set(mode="OBJECT")

                    if isinstance(result, str):
                        if "empty the source object" in result:
                            # Fires for an object with only one material used across 100% of its
                            # remaining faces - nothing left to meaningfully separate, not a real
                            # failure, just nothing more to do for this object.
                            skipped_count += 1
                            done_count += 1
                            wm.progress_update(done_count)
                            break
                        failed_count += 1
                        unusable_material_indices.add(material_index)
                        self.report({"WARNING"}, "'{}': {}".format(source_obj.name, result))
                        done_count += 1
                        wm.progress_update(done_count)
                        continue
                    created_count += 1
                    done_count += 1
                    wm.progress_update(done_count)
        finally:
            wm.progress_end()
            for o in context.selected_objects:
                o.select_set(False)
            for o in original_selected:
                if o.name in bpy.data.objects:
                    o.select_set(True)
            if view_layer and original_active is not None and original_active.name in bpy.data.objects:
                view_layer.objects.active = original_active

        self.report({"INFO"}, "Created {} decal object(s); {} (object, material) pair(s) skipped, {} failed".format(
            created_count, skipped_count, failed_count))
        return {"FINISHED"}
