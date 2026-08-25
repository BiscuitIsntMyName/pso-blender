from typing import cast, final
import bpy
from bpy.types import Context, Material, Node, NodeSocket, Operator, Panel
from .rel_properties_menu import ObjectWithRelSettings
from .util import find_diffuse_image


class _MaterialBakeRewire:
    __slots__ = ("material", "surface_from", "color_from", "temp_image_node")

    def __init__(
            self, material: Material, surface_from: "NodeSocket | None", color_from: "NodeSocket | None",
            temp_image_node: "Node | None"):
        self.material = material
        self.surface_from = surface_from
        self.color_from = color_from
        self.temp_image_node = temp_image_node


def _simplify_material_for_bake(mat: Material) -> "_MaterialBakeRewire | None":
    """Temporarily rewires a pso-blender-generated material so a Cycles lighting bake reads
    through it correctly - see caller for why this is needed. Returns None (nothing rewired,
    nothing to restore) if the material doesn't have the expected node shape (e.g. a user-made
    material this addon didn't generate) rather than guessing at an unfamiliar graph.

    Three independent Cycles bake problems, all confirmed live against real pso-blender materials
    (none reproducible on a plain material with a bare Image Texture node wired straight into a
    Diffuse BSDF):

    1. This addon's per-axis UV addressing math (see make_material, xj.py - Separate XYZ -> one
       Math node per axis for independent WRAP/CLAMP/MIRROR -> Combine XYZ, needed since Blender's
       Image Texture node only has one "Extension" setting shared by both axes), and the shared
       Mapping node group upstream of it, make Cycles bake come back all-zero for anything
       downstream. Rendering (including EEVEE/Cycles viewport preview) is unaffected - this is
       specific to the bake operator.
    2. `make_material` always multiplies the texture color by the mesh's EXISTING
       "vertex_color" attribute before feeding the Diffuse BSDF (`mix_node`, MULTIPLY blend,
       Fac=1.0 - see xj.py around the `mix_node.outputs[2] -> bsdf_node.inputs[0]` link) - this is
       what makes painted vertex color visibly darken/tint the texture in the viewport. But it
       also means a lighting bake is circular: it reads a Color input that already depends on the
       very same "vertex_color" attribute the bake is about to overwrite. On a mesh whose existing
       vertex color is black (the common case - either never painted, or already dark), the BSDF
       Color input is black regardless of scene lighting, so every bake comes back black too, no
       matter how bright the scene is.
    3. The Transparent/Diffuse Mix Shader wiring (for texture alpha) makes the baked vertex
       color's alpha channel come back 0 instead of 1. A lighting bake has nothing to do with
       transparency, so this branch is irrelevant to it anyway.

    All three are worked around the same way: for the duration of the bake, the Diffuse BSDF's
    Color input is fed directly from a plain, temporary Image Texture node (raw UV in, "Repeat"
    extension - the addon's shared ImgGroup/Mapping node group and the vertex-color multiply are
    both skipped entirely), found via find_diffuse_image (same helper the rest of the addon uses
    to locate "the" diffuse image of a material, whether it sits directly in the material or one
    level inside a shared ImgGroup). If the material has no diffuse image at all (untextured slot),
    the Color input is simply left at its own default value instead. Either way the BSDF is then
    wired straight to Output Surface, bypassing the Mix Shader/Transparent branch too.

    Bypassing the addressing math this way only matters for meshes whose UV coordinates actually
    leave [0, 1) (tiled/repeating textures) - within [0, 1) "Repeat" and the addon's own
    WRAP/CLAMP/MIRROR math agree anyway, so a lighting bake is visually unaffected by skipping it
    for the vast majority of meshes.
    """
    if mat.node_tree is None:
        return None
    nodes = mat.node_tree.nodes
    output_node = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None)
    bsdf_node = next((n for n in nodes if n.type == "BSDF_DIFFUSE"), None)
    # UV_MAP: current materials (make_material now uses ShaderNodeUVMap instead of
    # ShaderNodeTexCoord, so its UV layer is visible/selectable in the node itself - see xj.py).
    # TEX_COORD: kept for materials generated before that change, already sitting in someone's
    # .blend file - both node types expose the same "UV" output socket, so the rest of this
    # function doesn't need to care which one it found.
    tex_coord_node = next((n for n in nodes if n.type in ("UVMAP", "TEX_COORD")), None)
    if output_node is None or bsdf_node is None:
        return None

    surface_input = output_node.inputs.get("Surface")
    surface_from = surface_input.links[0].from_socket if surface_input and surface_input.is_linked else None
    color_input = bsdf_node.inputs.get("Color")
    color_from = color_input.links[0].from_socket if color_input and color_input.is_linked else None

    temp_image_node: "Node | None" = None
    if color_input is not None:
        diffuse_image = find_diffuse_image(mat)
        if diffuse_image is not None and tex_coord_node is not None:
            temp_image_node = mat.node_tree.nodes.new(type="ShaderNodeTexImage")
            cast(bpy.types.ShaderNodeTexImage, temp_image_node).image = diffuse_image
            mat.node_tree.links.new(tex_coord_node.outputs["UV"], temp_image_node.inputs["Vector"])
            mat.node_tree.links.new(temp_image_node.outputs["Color"], color_input)
        elif color_input.is_linked:
            mat.node_tree.links.remove(color_input.links[0])

    if surface_input is not None:
        mat.node_tree.links.new(bsdf_node.outputs["BSDF"], surface_input)
    return _MaterialBakeRewire(mat, surface_from, color_from, temp_image_node)


def _restore_material_after_bake(rewire: "_MaterialBakeRewire | None"):
    if rewire is None or rewire.material.node_tree is None:
        return
    tree = rewire.material.node_tree
    output_node = next((n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
    bsdf_node = next((n for n in tree.nodes if n.type == "BSDF_DIFFUSE"), None)
    if output_node is not None and rewire.surface_from is not None:
        surface_input = output_node.inputs.get("Surface")
        if surface_input is not None:
            tree.links.new(rewire.surface_from, surface_input)
    if bsdf_node is not None:
        color_input = bsdf_node.inputs.get("Color")
        if color_input is not None and rewire.color_from is not None:
            tree.links.new(rewire.color_from, color_input)
    if rewire.temp_image_node is not None:
        tree.nodes.remove(rewire.temp_image_node)


# pyright: reportInvalidTypeForm=false, reportUninitializedInstanceVariable=false
@final
class BakeLightingToVertexColors(Operator):  # pyright: ignore[reportIncompatibleMethodOverride]
    "Bake the scene's current lighting (Cycles, Combined - diffuse+glossy, direct+indirect) into each selected mesh object's vertex color, so the existing REL/XJ export picks it up automatically without any new export-side code"


    bl_idname = "object.pso_bake_lighting_to_vertex_colors"
    bl_label = "Bake Rendered Lights"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        scene = context.scene
        if scene is None:
            self.report({"ERROR"}, "No active scene")
            return {"CANCELLED"}

        selected_meshes = [obj for obj in context.selected_objects if obj.type == "MESH"]
        targets = [obj for obj in selected_meshes if not cast(ObjectWithRelSettings, obj).rel_settings.exclude_from_lighting_bake]
        skipped_excluded = len(selected_meshes) - len(targets)
        skipped_non_mesh = len(context.selected_objects) - len(selected_meshes)

        if not targets:
            self.report({"WARNING"}, "No mesh objects to bake (selection is empty, non-mesh only, or all excluded)")
            return {"CANCELLED"}

        # Save every scene setting this touches, same discipline as xvm.bake_texture_group - baking
        # lighting must never leave the user's actual file in a different state than before this
        # operator ran.
        original_engine = scene.render.engine
        original_view_transform = scene.view_settings.view_transform
        original_bake_target = scene.render.bake.target
        original_use_pass_direct = scene.render.bake.use_pass_direct
        original_use_pass_indirect = scene.render.bake.use_pass_indirect
        original_use_pass_diffuse = scene.render.bake.use_pass_diffuse
        original_use_pass_glossy = scene.render.bake.use_pass_glossy
        original_selected = list(context.selected_objects)
        original_active = context.view_layer.objects.active if context.view_layer else None

        baked_count = 0
        try:
            scene.render.engine = "CYCLES"
            scene.view_settings.view_transform = "Standard"
            # Confirmed live: bpy.ops.object.bake() bakes to an Image Texture node by default
            # regardless of whether one is actually wired in (silently produces an all-white/
            # unchanged result if it can't find one) - render.bake.target must be explicitly set
            # to "VERTEX_COLORS" or nothing meaningful gets written at all.
            scene.render.bake.target = "VERTEX_COLORS"
            scene.render.bake.use_pass_direct = True
            scene.render.bake.use_pass_indirect = True
            scene.render.bake.use_pass_diffuse = True
            scene.render.bake.use_pass_glossy = True

            for obj in targets:
                mesh = cast(bpy.types.Mesh, obj.data)

                # Ensure "vertex_color" exists, matching xj.py's importer exactly (the same name/
                # domain/type it creates on import), and make it the ACTIVE color attribute -
                # confirmed live that Cycles bakes into whichever attribute is active, independent
                # of its position in the list.
                attr = next((a for a in mesh.color_attributes if a.name == "vertex_color"), None)
                if attr is None:
                    attr = mesh.color_attributes.new("vertex_color", "FLOAT_COLOR", "POINT")
                attr_index = next(i for i, a in enumerate(mesh.color_attributes) if a.name == attr.name)
                mesh.color_attributes.active_color_index = attr_index
                if attr_index != 0:
                    # xj.py's exporter reads color_attributes[0] by list position, not by name or
                    # active status - baking correctly into "vertex_color" here doesn't help if
                    # some OTHER attribute still occupies index 0, since export would keep reading
                    # that other one instead. No safe reorder API exists without rebuilding the
                    # mesh, so surface it instead of silently doing the wrong thing.
                    self.report({"WARNING"}, (
                        "'{}': baked into 'vertex_color' but it isn't the first color attribute "
                        "on this mesh - export reads the first one, so this bake won't be picked "
                        "up until the other attribute is removed or moved after it."
                    ).format(obj.name))

                # An active Image Texture node in the object's own material(s) could in principle
                # compete for the bake target - defensively clear it. pso-blender's own generated
                # materials keep their diffuse image nested inside a shared ImgGroup node group,
                # never at the top level, so this shouldn't normally trigger - only relevant for a
                # user-added top-level image node.
                original_active_nodes: list[tuple[bpy.types.Material, "bpy.types.Node | None"]] = []
                # Temporarily bypass this addon's per-axis UV addressing math and the Transparent/
                # Diffuse Mix Shader - see _simplify_material_for_bake's docstring for the two
                # confirmed Cycles bake bugs this works around. Only rewires materials whose node
                # shape matches what this addon generates; anything else (a user-authored
                # material) is left completely untouched, both for baking and restoration.
                rewires: list["_MaterialBakeRewire | None"] = []
                seen_materials: set[Material] = set()
                for slot in obj.material_slots:
                    mat = slot.material
                    if mat is None or mat.node_tree is None or mat in seen_materials:
                        continue
                    seen_materials.add(mat)
                    original_active_nodes.append((mat, mat.node_tree.nodes.active))
                    mat.node_tree.nodes.active = None
                    rewires.append(_simplify_material_for_bake(mat))

                for o in context.selected_objects:
                    o.select_set(False)
                obj.select_set(True)
                if context.view_layer:
                    context.view_layer.objects.active = obj

                try:
                    bpy.ops.object.bake(type="COMBINED")
                    baked_count += 1
                except RuntimeError as e:
                    self.report({"ERROR"}, "'{}': bake failed - {}".format(obj.name, e))
                finally:
                    for rewire in rewires:
                        _restore_material_after_bake(rewire)
                    for mat, active_node in original_active_nodes:
                        mat.node_tree.nodes.active = active_node
        finally:
            scene.render.engine = original_engine
            scene.view_settings.view_transform = original_view_transform
            scene.render.bake.target = original_bake_target
            scene.render.bake.use_pass_direct = original_use_pass_direct
            scene.render.bake.use_pass_indirect = original_use_pass_indirect
            scene.render.bake.use_pass_diffuse = original_use_pass_diffuse
            scene.render.bake.use_pass_glossy = original_use_pass_glossy
            for o in context.selected_objects:
                o.select_set(False)
            for o in original_selected:
                o.select_set(True)
            if context.view_layer:
                context.view_layer.objects.active = original_active

        self.report({"INFO"}, "Baked lighting into vertex colors for {} object(s); skipped {} excluded, {} non-mesh".format(
            baked_count, skipped_excluded, skipped_non_mesh))
        return {"FINISHED"}


@final
class PSO_PT_bake_lighting(Panel):  # pyright: ignore[reportIncompatibleMethodOverride]
    bl_idname = "PSO_PT_bake_lighting"
    bl_label = "Lighting Bake"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "pso-blender"

    def draw(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        layout = self.layout
        if not layout:
            return
        layout.operator(BakeLightingToVertexColors.bl_idname, icon="RENDER_STILL")
