from typing import cast, final
import bpy
import bmesh
from mathutils import Vector
from bpy.types import Context, Operator, Panel


@final
class PreparePolygonDecal(Operator):  # pyright: ignore[reportIncompatibleMethodOverride]
    """Automates the mechanical setup steps of the dedicated-texture-decal workflow (see
    next_subjects_to_work.md, branch decal_light_experiment) on the currently selected faces:
    separate them into their own object, give that object a single clean UV layer (a flat
    orthogonal projection along the selected faces' average normal - computed directly rather
    than via Project From View, so it doesn't depend on the 3D viewport's current camera angle
    at all), a brand new dedicated material, and an Image Texture node wired to a brand new blank
    image through a UV Map node pointing at that new layer.

    Deliberately does NOT bake anything - lighting must already be set up in the scene by hand
    first, and baking is a separate, explicit user action (Render Properties > Bake) once this
    object is ready.
    """

    bl_idname = "object.pso_prepare_decal_polygon"
    bl_label = "Prepare Selected Triangles as Decal"
    bl_options = {"REGISTER", "UNDO"}

    image_size: bpy.props.IntProperty(name="Image Size", default=512, min=8, max=8192)  # pyright: ignore[reportInvalidTypeForm]

    @classmethod
    def poll(cls, context: Context):
        return (
            context.active_object is not None
            and context.active_object.type == "MESH"
            and context.active_object.mode == "EDIT")

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        source_obj = context.active_object
        if source_obj is None:
            self.report({"ERROR"}, "No active object")
            return {"CANCELLED"}
        source_mesh = cast(bpy.types.Mesh, source_obj.data)

        bm = bmesh.from_edit_mesh(source_mesh)
        selected_faces = [f for f in bm.faces if f.select]
        if not selected_faces:
            self.report({"ERROR"}, "No faces selected")
            return {"CANCELLED"}
        if len(selected_faces) == len(bm.faces):
            # Separating every face would leave the source object completely empty - almost
            # certainly a stale/accidental "select all" rather than an intentional decal on the
            # object's entire surface, and an empty mesh fails loudly at export time rather than
            # doing anything useful - cheaper to catch it here.
            self.report({"ERROR"}, "All faces on '{}' are selected - this would empty the source object entirely. Select only the intended faces first.".format(source_obj.name))
            return {"CANCELLED"}

        # Average normal in WORLD space (mesh normals are local) - this is what the new UV
        # layer's projection axis is built from, matching what aiming an orthographic viewport
        # straight at the surface and using Project From View would produce, without needing an
        # actual viewport aligned to it.
        world_matrix = source_obj.matrix_world
        normal_matrix = world_matrix.to_3x3().inverted().transposed()
        avg_normal = Vector((0.0, 0.0, 0.0))
        for f in selected_faces:
            avg_normal += (normal_matrix @ f.normal).normalized()
        if avg_normal.length < 1e-8:
            self.report({"ERROR"}, "Selected faces have no well-defined average normal (cancelling normals out)")
            return {"CANCELLED"}
        avg_normal.normalize()

        bpy.ops.mesh.separate(type="SELECTED")
        bpy.ops.object.mode_set(mode="OBJECT")

        new_obj = next((o for o in context.selected_objects if o is not source_obj and o.type == "MESH"), None)
        if new_obj is None:
            self.report({"ERROR"}, "Could not find the newly separated object")
            return {"CANCELLED"}
        new_mesh = cast(bpy.types.Mesh, new_obj.data)
        base_name = source_obj.name + "_decal"
        new_obj.name = base_name
        new_mesh.name = base_name + "_mesh"

        # Clean single UV layer: any layers inherited from the source mesh through Separate are
        # dropped and replaced with exactly one, so it's unambiguously at list position 0 - see
        # the write_vertex_buffer note in xj.py (export always reads uv_layers[0] by list
        # position, regardless of which layer any material's own nodes point to) and this
        # session's investigation of the resulting tiling bug when that wasn't guaranteed.
        for layer in list(new_mesh.uv_layers):
            new_mesh.uv_layers.remove(layer)
        uv_layer = new_mesh.uv_layers.new(name="UVMap")

        # Flat orthogonal projection along avg_normal - equivalent to Project From View with the
        # viewport aimed straight at the surface, computed directly instead so it can't depend on
        # (or be broken by) whatever the 3D viewport happens to be looking at when this runs.
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

        # Dedicated material and image - genuinely new datablocks, not shared with anything else
        # in the file, so nothing else can be affected by a later bake into this image.
        new_mat = bpy.data.materials.new(base_name + "_mat")
        new_mat.use_nodes = True
        node_tree = new_mat.node_tree
        assert node_tree is not None
        bsdf_node = next(n for n in node_tree.nodes if n.type == "BSDF_PRINCIPLED")

        uv_map_node = cast(bpy.types.ShaderNodeUVMap, node_tree.nodes.new(type="ShaderNodeUVMap"))
        uv_map_node.uv_map = uv_layer.name
        uv_map_node.location = (bsdf_node.location.x - 600, bsdf_node.location.y)

        image_node = cast(bpy.types.ShaderNodeTexImage, node_tree.nodes.new(type="ShaderNodeTexImage"))
        new_image = bpy.data.images.new(base_name + "_bake", self.image_size, self.image_size)
        image_node.image = new_image
        image_node.location = (bsdf_node.location.x - 300, bsdf_node.location.y)

        node_tree.links.new(uv_map_node.outputs["UV"], image_node.inputs["Vector"])
        node_tree.links.new(image_node.outputs["Color"], bsdf_node.inputs["Base Color"])

        new_mesh.materials.clear()
        new_mesh.materials.append(new_mat)

        for o in context.selected_objects:
            o.select_set(False)
        new_obj.select_set(True)
        if context.view_layer:
            context.view_layer.objects.active = new_obj

        self.report({"INFO"}, "'{}' ready: {} faces, dedicated material and image '{}' ({}x{}) - set up lighting, then bake by hand".format(
            new_obj.name, len(selected_faces), new_image.name, self.image_size, self.image_size))
        return {"FINISHED"}


@final
class PSO_PT_decal_prep(Panel):  # pyright: ignore[reportIncompatibleMethodOverride]
    bl_idname = "PSO_PT_decal_prep"
    bl_label = "Decal Texture Prep"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "pso-blender"

    def draw(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        layout = self.layout
        if not layout:
            return
        layout.label(text="Select faces in Edit Mode first")
        layout.operator(PreparePolygonDecal.bl_idname, icon="MOD_UVPROJECT")
