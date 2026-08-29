from typing import Any, cast, final
import bpy
from bpy.props import BoolProperty  # pyright: ignore[reportUnknownVariableType]
from bpy.types import Context, Material, Operator, Panel
from .decal_prep_menu import BatchPrepareDecalsForScene
from .util import find_diffuse_image


_image_has_real_alpha_cache: dict[str, bool] = {}


def _image_has_real_alpha(image: "bpy.types.Image | None") -> bool:
    """True if any pixel's alpha is below 1.0 - i.e. this image is actually used for real
    transparency somewhere, not just nominally having an alpha channel. Cached by image name
    (mirrors Texture.has_alpha's cache in util.py) since scanning every pixel of every material's
    image on every bake would otherwise be repeated needlessly."""
    if image is None:
        return False
    cached = _image_has_real_alpha_cache.get(image.name)
    if cached is not None:
        return cached
    pixels = image.pixels[:]  # pyright: ignore[reportArgumentType]
    result = any(pixels[i] < 1.0 for i in range(3, len(pixels), 4))
    _image_has_real_alpha_cache[image.name] = result
    return result


def _mesh_has_real_vertex_alpha(mesh: bpy.types.Mesh) -> bool:
    """True if the mesh's "vertex_color" attribute has any alpha below 1.0 anywhere - vertex
    color alpha is multiplied with texture alpha to drive the Mix Shader's Diffuse/Transparent
    blend (see make_material, xj.py), so this is just as capable of real transparency as the
    texture's own alpha channel is."""
    vc = mesh.color_attributes.get("vertex_color")
    if vc is None:
        return False
    return any(d.color[3] < 1.0 for d in vc.data)


def _object_is_fully_opaque(obj: bpy.types.Object) -> bool:
    """True only if EVERY material on this object has fully-opaque texture alpha, AND the mesh's
    own vertex color alpha (if any) is fully opaque too - i.e. genuinely safe to bypass the
    Transparent BSDF branch on this object without changing anything that's meant to actually be
    see-through (windows, cutout foliage, etc.)."""
    mesh = cast(bpy.types.Mesh, obj.data)
    if _mesh_has_real_vertex_alpha(mesh):
        return False
    for slot in obj.material_slots:
        mat = slot.material
        if mat is None or mat.node_tree is None:
            continue
        if _image_has_real_alpha(find_diffuse_image(mat)):
            return False
    return True


def _iter_opaque_scene_materials(scene: bpy.types.Scene, exclude: "set[bpy.types.Object]"):
    """Yields each distinct material (no duplicates - materials are frequently shared across many
    objects in this addon's output) on a fully-opaque mesh object in the scene, skipping objects
    in `exclude` (typically the bake's own target(s), left untouched by design)."""
    seen_materials: set[Material] = set()
    for obj in scene.objects:
        if obj.type != "MESH" or obj in exclude or not _object_is_fully_opaque(obj):
            continue
        for slot in obj.material_slots:
            mat = slot.material
            if mat is None or mat.node_tree is None or mat in seen_materials:
                continue
            seen_materials.add(mat)
            yield mat


def _bypass_opaque_scene_objects(scene: bpy.types.Scene, exclude: "set[bpy.types.Object]") -> int:
    """Bypasses the Transparent/Diffuse Mix Shader (wires the Diffuse BSDF straight to Output
    Surface) on every fully-opaque mesh's material in the scene except `exclude` - so surrounding
    geometry casts correct shadows and bounces correct indirect light during a bake. Confirmed
    live that a Mix Shader driven by a dynamically-computed Fac (even one that always evaluates to
    1.0 - fully opaque) makes Cycles treat the object as needing "transparent shadow" handling,
    which behaves inconsistently during a bake and can leak light through walls that should fully
    block it. Only touches materials _object_is_fully_opaque already confirmed have no real
    transparency to lose by skipping that branch.

    Deliberately stateless: doesn't return or need anything to be undone later - see
    _restore_opaque_scene_objects, which re-derives the correct wiring directly from the material's
    own Mix Shader node rather than remembering what was there before. This is what makes an ON/OFF
    checkbox safe to use instead of relying on Blender's Undo (survives a file save/reload, or the
    two calls being made from different Blender sessions entirely).

    Returns how many distinct materials were touched.
    """
    count = 0
    for mat in _iter_opaque_scene_materials(scene, exclude):
        nodes = mat.node_tree.nodes  # pyright: ignore[reportOptionalMemberAccess]
        output_node = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None)
        bsdf_node = next((n for n in nodes if n.type == "BSDF_DIFFUSE"), None)
        if output_node is None or bsdf_node is None:
            continue
        surface_input = output_node.inputs.get("Surface")
        if surface_input is not None:
            mat.node_tree.links.new(bsdf_node.outputs["BSDF"], surface_input)  # pyright: ignore[reportOptionalMemberAccess]
            count += 1
    return count


def _restore_opaque_scene_objects(scene: bpy.types.Scene, exclude: "set[bpy.types.Object]") -> int:
    """Re-links each fully-opaque mesh's material back through its Mix Shader (Mix Shader ->
    Output Surface) - the standard wiring make_material always builds, found directly by node
    type rather than by remembering what _bypass_opaque_scene_objects saw. Safe to call even if
    nothing was actually bypassed (relinking an already-correct link is a harmless no-op).

    Returns how many distinct materials were touched.
    """
    count = 0
    for mat in _iter_opaque_scene_materials(scene, exclude):
        nodes = mat.node_tree.nodes  # pyright: ignore[reportOptionalMemberAccess]
        output_node = next((n for n in nodes if n.type == "OUTPUT_MATERIAL"), None)
        mix_shader_node = next((n for n in nodes if n.type == "MIX_SHADER"), None)
        if output_node is None or mix_shader_node is None:
            continue
        surface_input = output_node.inputs.get("Surface")
        if surface_input is not None:
            mat.node_tree.links.new(mix_shader_node.outputs[0], surface_input)  # pyright: ignore[reportOptionalMemberAccess]
            count += 1
    return count


def _update_bypass_transparent_shadows(self: bpy.types.Scene, _context: Context):
    scene = self
    if cast(bool, scene.pso_bypass_transparent_shadows):
        _bypass_opaque_scene_objects(scene, exclude=set())
    else:
        _restore_opaque_scene_objects(scene, exclude=set())


class SceneWithBypassTransparentShadows(bpy.types.Scene):
    pso_bypass_transparent_shadows: bool


def register_scene_properties():
    # A bare property on bpy.types.Scene (like rel_settings/xj_settings elsewhere in this addon,
    # but a plain BoolProperty rather than a whole PropertyGroup - this is the only setting here)
    # instead of an Operator, so it behaves as a real ON/OFF checkbox: toggling it off calls
    # _restore_opaque_scene_objects immediately via `update`, no separate action needed.
    cast(Any, bpy.types.Scene).pso_bypass_transparent_shadows = BoolProperty(
        name="Bypass Transparent Shadows For Bake",
        description=(
            "While on, every fully-opaque mesh's material has its Transparent/Diffuse Mix Shader "
            "bypassed, so Cycles casts correct shadows and bounces correct indirect light during "
            "a manual bake (Render Properties > Bake) - e.g. the dedicated-texture-decal workflow, "
            "which doesn't go through any pso-blender bake operator. Turn back off once done "
            "baking to restore normal transparency handling"),
        default=False,
        update=_update_bypass_transparent_shadows)


def unregister_scene_properties():
    del cast(Any, bpy.types.Scene).pso_bypass_transparent_shadows


@final
class BatchBakeDecals(Operator):  # pyright: ignore[reportIncompatibleMethodOverride]
    """Bakes every decal object created by BatchPrepareDecalsForScene (decal_prep_menu.py) - found
    by the "pso_is_decal" custom property that operator tags them with, not by selection, so this
    can run as its own separate step afterward without needing to be chained in one call. Each
    object's material has two Image Texture nodes (see _separate_and_build_decal): a reference to
    the original texture feeding Base Color, and "PSO_DecalBakeTarget" - a brand new blank image,
    the actual bake destination. Cycles bakes into whichever node is active AND selected, so this
    finds "PSO_DecalBakeTarget" by name and makes it so for each object in turn.

    Temporarily turns on scene.pso_bypass_transparent_shadows (bake_lighting_menu.py) for every
    fully-opaque surrounding object, restoring it to whatever it was before once done - confirmed
    live this session that Cycles otherwise miscomputes shadows/indirect light from this addon's
    materials during any bake, not just the object actually being baked.
    """

    bl_idname = "object.pso_batch_bake_decals"
    bl_label = "Bake All Decals"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context: Context):
        return context.scene is not None

    def execute(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        scene = context.scene
        if scene is None:
            self.report({"ERROR"}, "No active scene")
            return {"CANCELLED"}

        targets = [o for o in scene.objects if o.type == "MESH" and o.get("pso_is_decal")]
        if not targets:
            self.report({"WARNING"}, "No decal objects found - run 'Prepare Selection As Decals' first")
            return {"CANCELLED"}

        original_engine = scene.render.engine
        original_view_transform = scene.view_settings.view_transform
        original_bake_target = scene.render.bake.target
        original_use_pass_direct = scene.render.bake.use_pass_direct
        original_use_pass_indirect = scene.render.bake.use_pass_indirect
        original_use_pass_diffuse = scene.render.bake.use_pass_diffuse
        original_use_pass_glossy = scene.render.bake.use_pass_glossy
        original_selected = list(context.selected_objects)
        original_active = context.view_layer.objects.active if context.view_layer else None
        original_bypass = cast(bool, scene.pso_bypass_transparent_shadows)

        # Deliberately no wm.progress_begin/update/end wrapping here, unlike
        # BatchPrepareDecalsForScene - bpy.ops.object.bake() already shows its own native
        # per-bake progress in the status bar while it runs (confirmed by the user), and taking
        # over the cursor's progress indicator for the whole batch hid that instead of helping.

        baked_count = 0
        failed_count = 0
        try:
            scene.render.engine = "CYCLES"
            scene.view_settings.view_transform = "Standard"
            scene.render.bake.target = "IMAGE_TEXTURES"
            scene.render.bake.use_pass_direct = True
            scene.render.bake.use_pass_indirect = True
            scene.render.bake.use_pass_diffuse = True
            scene.render.bake.use_pass_glossy = True
            if not original_bypass:
                scene.pso_bypass_transparent_shadows = True

            for obj in targets:
                mat = obj.active_material
                if mat is None or mat.node_tree is None:
                    failed_count += 1
                    self.report({"WARNING"}, "'{}': no material to bake into".format(obj.name))
                    continue
                target_node = mat.node_tree.nodes.get("PSO_DecalBakeTarget")
                if target_node is None:
                    failed_count += 1
                    self.report({"WARNING"}, "'{}': no PSO_DecalBakeTarget node found on its material".format(obj.name))
                    continue

                for o in context.selected_objects:
                    o.select_set(False)
                obj.select_set(True)
                if context.view_layer:
                    context.view_layer.objects.active = obj

                # Setting nodes.active clears every node's .select, including the one just made
                # active - confirmed live this session - so active must be set BEFORE select, with
                # select the very last thing touched before the bake call itself.
                mat.node_tree.nodes.active = target_node
                for n in mat.node_tree.nodes:
                    n.select = False
                target_node.select = True

                try:
                    bpy.ops.object.bake(type="COMBINED")
                    baked_count += 1
                except RuntimeError as e:
                    failed_count += 1
                    self.report({"ERROR"}, "'{}': bake failed - {}".format(obj.name, e))
                    continue

                # PSO_DecalBakeTarget is deliberately NOT wired to Base Color before this point -
                # Cycles bakes into whichever node is active+selected regardless of whether it's
                # actually connected to anything, and it mustn't feed Base Color during the bake
                # itself (that would make the bake read its own blank output). Now that baking is
                # done, THIS is what the material should actually show/export going forward - wire
                # it in for real, and remove the original-texture reference (PSO_OriginalTexture
                # Reference + its UV Map node), now unused, so find_diffuse_image (util.py) has
                # only one TEX_IMAGE node left to find instead of picking between two arbitrarily.
                bsdf_node = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
                if bsdf_node is not None:
                    mat.node_tree.links.new(target_node.outputs["Color"], bsdf_node.inputs["Base Color"])
                reference_node = mat.node_tree.nodes.get("PSO_OriginalTextureReference")
                if reference_node is not None:
                    vector_input = reference_node.inputs.get("Vector")
                    reference_uv_node = vector_input.links[0].from_node if vector_input and vector_input.is_linked else None
                    mat.node_tree.nodes.remove(reference_node)
                    if reference_uv_node is not None:
                        mat.node_tree.nodes.remove(reference_uv_node)
        finally:
            if not original_bypass:
                scene.pso_bypass_transparent_shadows = False
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
                if o.name in bpy.data.objects:
                    o.select_set(True)
            if context.view_layer and original_active is not None and original_active.name in bpy.data.objects:
                context.view_layer.objects.active = original_active

        self.report({"INFO"}, "Baked {} decal object(s); {} failed".format(baked_count, failed_count))
        return {"FINISHED"}


@final
class PSO_PT_bake_lighting(Panel):  # pyright: ignore[reportIncompatibleMethodOverride]
    """Everything from this session's dedicated-texture-decal + lighting-bake exploration, kept
    together under one clearly-marked "Experimental" section - the whole approach is slow (a real
    Cycles bake per decal object) and its outcome is still under evaluation, not yet a settled
    part of the addon's normal workflow."""

    bl_idname = "PSO_PT_bake_lighting"
    bl_label = "Bake Cycle Lights (Experimental)"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "pso-blender"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context: Context):  # pyright: ignore[reportIncompatibleMethodOverride]
        layout = self.layout
        if not layout or context.scene is None:
            return
        layout.operator(BatchPrepareDecalsForScene.bl_idname, icon="MOD_UVPROJECT")
        layout.separator()
        layout.prop(
            cast(SceneWithBypassTransparentShadows, context.scene), "pso_bypass_transparent_shadows",
            text="Bypass Transparent Shadows For Bake", icon="MOD_MASK")
        layout.operator(BatchBakeDecals.bl_idname, icon="RENDER_STILL")
