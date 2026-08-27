*This is a fork of the work made by jtuu. Thanks for your job jtuu <3.
For more information check out (https://github.com/jtuu/pso-blender).*

My work is generaly texture oriented so the modifications i could add here will be focused on texture edition more than map creation. Some functionnalities may change by this way.

# pso-blender

A Blender add-on (v4.2+) for importing and exporting Phantasy Star Online Blue Burst's map and
model file formats, with a focus on building **texture packs**: replace a map's textures in
Blender, then export just the new textures back into the game's `.xvm` format without touching
the original mesh/collision files.

## Supported file formats

### Map geometry
* **n.rel** - Map render geometry
* **c.rel** - Map collision geometry
* **r.rel** - Minimap geometry

> [!NOTE]
> Exporting a file named `foo.rel` writes `foon.rel`/`fooc.rel`/`foor.rel` - but only whichever
> of the three actually have matching objects (REL Settings: render/collision/minimap) in the
> current scene. A scene with no collision objects, for example, won't produce a `c.rel` at all.

An `.xvm` is created alongside a `.rel`/`.xj` export whenever the exported objects contain
materials with image input nodes.

Object export is resilient to a few real, common map data quirks instead of aborting the whole
file over them:
- An object with a texture but no UV layer is skipped (with a warning) instead of failing the
  entire export - on real map data this can affect a meaningful fraction of a map's objects.
- Texture order in the exported `.xvm` follows each texture's original position instead of being
  resorted alphabetically by material name on every export, so a zero-edit re-export doesn't
  needlessly restructure the file.

### Animated textures (`.tam`)
Frame-swap texture animations (a fixed set of textures cycled over time - screens, neon signs,
lava, waterfalls, etc.) are imported as real Blender Image Sequences, reconstructed from the
map's `.tam` file, and preview correctly in the 3D viewport (Auto Refresh follows the scene's
current frame). Each animation's playback speed round-trips through export, and can be edited
per-object via **Animation Frame Delay** in Material Properties.

### GSL archives
`Data.gsl` and similar flat, uncompressed `.gsl` archives (bundles of `.rel`/`.xj`/`.xvm`/`.bml`
files) can be read and extracted - see `gsl.py`. Read-only for now; extracted files can be fed
straight into this add-on's existing import operators.

### BML/XJ - NPCs, objects, items, etc.
Textures are imported by selecting both the `.bml`/`.xj` file and its `.xvm` in the file select
menu. Object parenting creates a node hierarchy; empty objects create meshless nodes.

### XVM - textures
On import, `.xvm` files are decoded regardless of their internal format: DXT1/DXT2/DXT3/DXT4/DXT5,
or the uncompressed R5G6B5/A1R5G5B5 formats. On export, textures are (re-)compressed to DXT1
(opaque or punch-through alpha) or DXT2 (smooth, premultiplied alpha), matching whichever alpha
mode the source image has.

### NJM
Experimental support for exporting skeletal animations.

## Texture pack workflow

The tools below are built around one loop: import a map, replace some of its textures with a
texture pack, export just the textures (`Export XVM`) - leaving the original `.rel`/`.xj` mesh
files completely untouched.

### One texture, one shared source
The same original PSO texture can be used by several different materials on a map (different
blend mode / addressing per placement). This fork keeps the image and the Mapping transform for a
given texture in a single shared node group instead of duplicating them per material, so:
- Replacing the image on any one of those materials updates it everywhere that texture is used.
- Adjusting the shared Mapping node (Location/Rotation/Scale, under `Tab`/`Enter Group` on the
  texture's node group) is likewise a single edit that applies everywhere - there's no way for
  different placements of the same texture to end up with conflicting transforms.
- The Shader Editor preview folds tiled UVs the same way the game does (repeating one tile
  identically) before applying the Mapping transform, so what you see in Blender matches what a
  texture-only export can actually reproduce - including for surfaces where the texture repeats
  several times.

### "Send to ImgGroup (PSO)"
Send an image straight into a texture's shared slot with one right-click - no need to manually
enter the node group and swap the Image Texture node by hand. Select the object/face using the
texture you want to replace, then right-click:
- an Image Texture node in the Shader Editor,
- an asset in the Asset Browser (works with Material or Image assets, including ones from an
  external asset library like Poly Haven - for a Material asset it automatically picks out the
  Base Color texture, not a normal/roughness/AO map),
- or a file in the File Browser,

and choose **Send to ImgGroup (PSO)**.

### Export reliability
- `Export XVM` preserves the original file's texture IDs and slot order, only substituting the
  textures you actually replaced - other map files sharing the same `.xvm` keep working.
- Mipmap generation, when enabled per-texture in **XJ Settings**, produces a real compressed mip
  pyramid matching the game's own file layout.
- The export cache correctly detects changes to a texture's Mapping transform, not just its image,
  so re-exporting after only adjusting Location/Rotation/Scale won't silently reuse a stale result.

## Panels added by this plugin
* **REL Settings** (context: Object) - which of the three `.rel` files an object exports into.
* **NJCM Settings** (context: Object)
* **XJ Settings** (context: Material) - blend mode, per-axis texture addressing, Generate
  Mipmaps, the PSO texture ID/source `.xvm` path used by standalone `Export XVM`, and buttons to
  select every object/face using the active material's texture across the whole map.

## Running steps

Topics currently being investigated or intentionally left on hold across the project.

### Open / unresolved
- UV-scroll texture animation (`HAS_UV_ANIMATION`) is not yet imported or exported.
- Animated/moving mesh objects such as doors (`Chunk.animated_mesh_trees`) are not yet imported or exported.
- `Data.gsl` archives can be read, but how a map links to a specific object inside one isn't known yet.
- A zero-edit REL export is not yet byte-identical to the original file.
- Real mipmap generation quality/parity with the game's own encoder hasn't been reverified recently.
- An add-on uninstall cleanup gap hasn't been reverified recently.
- A 90°-Z axis mapping/UV correction bug hasn't been reverified recently.

### Deferred by choice (not started)
- Full map import beyond render/collision geometry (skybox, `bm.bin`, `fogentry.dat`).
- A map-wide "every animation" overview UI, beyond the current per-object field.
- Timing-accurate Blender preview of frame-swap animation speed (currently uniform in the viewport).

## Installation
1. Download or git clone this project.
2. Create a zip file from the `pso_blender` directory.
3. Open Blender and go to `Edit > Preferences > Add-ons > Install from disk` and select the zip
   you created.

For more on the original add-on, see [the wiki on Github](https://github.com/jtuu/pso-blender/wiki).
