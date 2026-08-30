import functools, struct
from typing import cast, TypeAlias


RGB: TypeAlias = tuple[int, int, int]


def _expand5(v5: int) -> int:
    return (v5 << 3) | (v5 >> 2)


def _expand6(v6: int) -> int:
    return (v6 << 2) | (v6 >> 4)


def _build_round_table(bits: int, expand: "callable[[int], int]") -> tuple[int, ...]:
    """For each possible 8-bit channel value, the N-bit code that - once expanded back to 8 bits
    via the exact same bit-replication `expand` the decoder uses (decompose_rgb565 below) - lands
    closest to it. Replaces plain masking (`v & 0xf8`, i.e. floor-toward-zero truncation), which is
    not the value that round-trips best: confirmed live on a real map texture (acity position 212)
    that masking left G/B systematically ~0.012 brighter than the original after nothing but a
    decode+recompress round trip, while R (whose bits happened to already round-trip losslessly on
    that texture) stayed put - a real, measurable color-accuracy bug, not compression noise."""
    max_code = (1 << bits) - 1
    table: list[int] = []
    for v8 in range(256):
        best_code = 0
        best_dist = 256
        for code in range(max_code + 1):
            dist = abs(expand(code) - v8)
            if dist < best_dist:
                best_dist = dist
                best_code = code
        table.append(best_code)
    return tuple(table)


_ROUND5_TABLE: tuple[int, ...] = _build_round_table(5, _expand5)
_ROUND6_TABLE: tuple[int, ...] = _build_round_table(6, _expand6)


def rgb8_to_rgb565(rgb: RGB) -> int:
    return (_ROUND5_TABLE[rgb[0]] << 11) | (_ROUND6_TABLE[rgb[1]] << 5) | _ROUND5_TABLE[rgb[2]]


def _decompose_rgb565_uncached(rgb: int) -> RGB:
    r = rgb >> 11
    g = (rgb >> 5) & 0x3f
    b = rgb & 0x1f
    return (_expand5(r), _expand6(g), _expand5(b))


# Only 65536 possible rgb565 values exist, and decoding hits this on every DXT block's two
# endpoint colors - precomputing the table once avoids repeating the same bit-shift arithmetic
# for the same handful of distinct colors over and over across every block.
_DECOMPOSE_RGB565_TABLE: tuple[RGB, ...] = tuple(_decompose_rgb565_uncached(rgb) for rgb in range(0x10000))


def decompose_rgb565(rgb: int) -> RGB:
    return _DECOMPOSE_RGB565_TABLE[rgb]


def _dxt_block_colors(
        pixels: list[float],
        x: int, y: int,
        img_width: int, block_dim: int,
        src_channels: int
    ) -> list[RGB]:
    colors: list[RGB] = []
    for block_y in range(block_dim):
        for block_x in range(block_dim):
            src_offset = img_width * ((y + block_y) * src_channels) + ((x + block_x) * src_channels)
            colors.append(cast(RGB, tuple(int(pixels[src_offset + chan] * 0xff) for chan in range(3))))
    return colors


def dxt_get_block_endpoints(block_colors: list[RGB]) -> tuple[RGB, RGB]:
    """Picks a DXT1 block's two endpoint colors as the two actual pixels furthest apart along the
    block's own axis of greatest color variance (found via a few power-iteration steps on the
    color covariance matrix - the standard fast technique real-time encoders use, cheap enough to
    run on every block of every texture in a full map export).

    An exact "cluster fit" (try every way to split the block's colors into ordered groups, solve
    least-squares endpoints for each, keep the lowest-error one) was tried and does measurably
    better on the rare block whose colors don't lie along one clear axis - but costs 100-1000x
    more per block, turning a full map export from seconds into multiple minutes. Given
    Texture.force_uncompressed now exists as a manual, exact escape hatch for exactly those rare
    blocks/textures (xj_material_properties_menu.py - guarantees perfect color at the cost of
    file size, applied only where actually needed), the fast approximation here is the right
    default again - see xj_material_properties_menu.AlphaCompression's neighbor,
    force_uncompressed, for what to reach for on a specific texture that still looks wrong."""
    n = len(block_colors)
    mean = [sum(c[i] for c in block_colors) / n for i in range(3)]
    # 3x3 symmetric covariance matrix of the block's colors.
    cov = [[0.0] * 3 for _ in range(3)]
    for c in block_colors:
        d = [c[i] - mean[i] for i in range(3)]
        for i in range(3):
            for j in range(3):
                cov[i][j] += d[i] * d[j]
    # Power iteration converges to the dominant eigenvector (the axis of greatest variance) in a
    # handful of steps - more than enough given DXT1 only needs a good-enough axis, not an exact
    # one, to pick better endpoints than a naive per-channel box.
    axis = [1.0, 1.0, 1.0]
    for _ in range(4):
        next_axis = [sum(cov[i][j] * axis[j] for j in range(3)) for i in range(3)]
        length = sum(v * v for v in next_axis) ** 0.5
        if length < 1e-6:
            # Flat/near-uniform block (very common in smaller mip levels) - no dominant axis, any
            # two identical endpoints are equally correct.
            flat = cast(RGB, tuple(max(0, min(255, round(v))) for v in mean))
            return (flat, flat)
        axis = [v / length for v in next_axis]
    projections = [sum((c[i] - mean[i]) * axis[i] for i in range(3)) for c in block_colors]
    min_idx = projections.index(min(projections))
    max_idx = projections.index(max(projections))
    return (block_colors[min_idx], block_colors[max_idx])


def dxt_make_color_palette(color0: int, color1: int) -> tuple[RGB, RGB, RGB, RGB]:
    """The two interpolated colors (palette2/palette3, or just palette2 for punch-through) must
    match, as closely as an integer can, what decode_dxt_colors will actually reconstruct from the
    stored color0/color1 - it does the same interpolation in exact floating point (ratios 2/3,
    1/3, or 1/2), never storing palette2/palette3 itself. The previous version used floor division
    (`// 3`, `// 2`) here, which *always* rounds down - so classification (dxt1_palettize_block,
    which picks the nearest of these 4 colors for each source pixel) was working off colors
    systematically darker than what actually gets displayed once decoded, silently biasing every
    pixel classified into an interpolated slot (most of a typical block) toward reconstructing
    brighter than intended. Confirmed live on a real map texture (acity position 212): G/B came
    back a consistent ~0.012 brighter after nothing but a decode+recompress round trip, with zero
    edits - traced to exactly this mismatch, unaffected by also fixing rgb8_to_rgb565's own
    quantization (a real, smaller, separate rounding gap, but not this one's cause). round() instead
    of floor division fixes the mismatch at its source."""
    palette0 = decompose_rgb565(color0)
    palette1 = decompose_rgb565(color1)
    if color0 <= color1:
        palette2 = cast(RGB, tuple(map(lambda a, b: round((a + b) / 2), palette0, palette1)))
        palette3 = (0, 0, 0)
    else:
        palette2 = cast(RGB, tuple(map(lambda a, b: round((2 * a + b) / 3), palette0, palette1)))
        palette3 = cast(RGB, tuple(map(lambda a, b: round((2 * b + a) / 3), palette0, palette1)))
    return (palette0, palette1, palette2, palette3)


def dxt1_block_needs_alpha(
        pixels: list[float],
        x: int, y: int,
        img_width: int, block_dim: int,
        src_channels: int
    ) -> bool:
    """True if this specific 4x4 block contains at least one meaningfully-transparent texel
    (alpha < 0.5 - the conventional DXT1 punch-through cutoff, matching mainstream encoders).
    Punch-through mode only has 3 usable opaque palette colors instead of 4, so it should only be
    forced on the blocks that actually need it - applying it image-wide (keyed off the whole
    texture's has_alpha flag rather than this per-block check) needlessly throws away color
    fidelity everywhere else, and - combined with too strict a per-texel cutoff - can misclassify
    ordinary antialiased near-opaque edges (alpha like 0.996) as meant to be transparent."""
    for block_y in range(block_dim):
        for block_x in range(block_dim):
            src_offset = img_width * ((y + block_y) * src_channels) + ((x + block_x) * src_channels)
            if pixels[src_offset + 3] < 0.5:
                return True
    return False


def image_has_smooth_alpha(pixels: list[float], channels: int) -> bool:
    """True if this image's alpha channel has genuine intermediate values (not just a hard
    cutout mask near-fully-transparent or near-fully-opaque) - used to decide whether a texture
    needs DXT3's explicit 16-level alpha instead of DXT1's 1-bit punch-through alpha. The 0.06/0.94
    margin mirrors dxt1_block_needs_alpha's 0.5 punch-through cutoff in spirit: real cutout masks
    (leaves, grates with hard edges) still have some antialiasing noise right at their edges, so
    the threshold has to tolerate that without triggering, while catching a genuine soft gradient
    (e.g. a fading decal or soft shadow)."""
    if channels < 4:
        return False
    for i in range(3, len(pixels), channels):
        if 0.06 < pixels[i] < 0.94:
            return True
    return False


def premultiply_alpha(pixels: list[float], channels: int) -> list[float]:
    """Multiplies each texel's RGB by its own alpha, in place in the same (already gamma-encoded,
    not linearized) value space image.pixels provides - matching how this engine's existing DXT2
    textures are actually stored (no linear-light round trip), so a freshly force-compressed DXT2
    texture stays byte-for-byte consistent with how an original, already-premultiplied DXT2
    texture behaves once decompressed."""
    if channels < 4:
        return pixels
    result = list(pixels)
    for i in range(0, len(result), channels):
        alpha = result[i + 3]
        result[i] *= alpha
        result[i + 1] *= alpha
        result[i + 2] *= alpha
    return result


def dxt1_palettize_block(
        pixels: list[float],
        palette: "tuple[RGB, ...]",
        x: int, y: int,
        img_width: int, block_dim: int,
        src_channels: int,
        with_alpha: bool
    ) -> int:
    palette_indices = 0
    px_idx = 0
    palette_len = len(palette)
    for block_y in range(block_dim):
        for block_x in range(block_dim):
            src_offset = img_width * ((y + block_y) * src_channels) + ((x + block_x) * src_channels)
            best_color_dist = float("inf")
            best_palette_idx = 0
            if with_alpha and pixels[src_offset + 3] < 0.5:
                best_palette_idx = 3
            else:
                # Find best palette color for pixel
                for palette_idx in range(palette_len):
                    # Compute distance between pixel and palette color
                    dist = 0
                    for chan in range(3):
                        val = int(pixels[src_offset + chan] * 0xff)
                        delta = val - palette[palette_idx][chan]
                        dist += delta * delta
                    if dist < best_color_dist:
                        best_color_dist = dist
                        best_palette_idx = palette_idx
            # Pack indices
            palette_indices |= best_palette_idx << (px_idx * 2)
            px_idx += 1
    return palette_indices


def dxt1_compress_block(
        pixels: list[float],
        img_width: int, block_dim: int,
        src_channels: int,
        with_alpha: bool,
        coords: tuple[int, int]
    ) -> tuple[int, int, int]:
    # Punch-through mode (color0 <= color1) is a per-block decision, not the whole image's - a
    # block with no meaningfully-transparent texel of its own should stay in normal 4-color opaque
    # mode (one more usable color) even inside an otherwise alpha-enabled image. Decided before
    # endpoint selection (not after, as before) since cluster fit needs to know whether it's
    # optimizing for 4 opaque colors or 3 real colors + a transparent slot.
    block_needs_alpha = with_alpha and dxt1_block_needs_alpha(pixels, coords[0], coords[1], img_width, block_dim, src_channels)
    block_colors = _dxt_block_colors(pixels, coords[0], coords[1], img_width, block_dim, src_channels)
    color0, color1 = dxt_get_block_endpoints(block_colors)
    color0_565 = rgb8_to_rgb565(color0)
    color1_565 = rgb8_to_rgb565(color1)
    if block_needs_alpha:
        if color0_565 > color1_565:
            # Swap colors to indicate alpha format
            color0_565, color1_565 = color1_565, color0_565
    else:
        # Colors might get swapped by quantization
        if color0_565 <= color1_565:
            color0_565, color1_565 = color1_565, color0_565
        if color0_565 == color1_565:
            # A flat/near-uniform block (very common in small mip levels, where heavy downsampling
            # smooths detail away) can quantize both endpoints to the identical RGB565 value. Real
            # DXT1 decoders determine 4-color-opaque vs punch-through-alpha purely from color0 <=
            # color1 (see decode_dxt_colors) - with color0 == color1 that's still true, so this
            # block would decode in punch-through mode even though this texture has no alpha at
            # all, and any texel classified to index 3 in that mode comes back fully transparent
            # (alpha 0, RGB left as whatever the destination buffer was pre-filled with) instead of
            # this block's actual color - showing through to whatever is rendered behind it. Nudge
            # color0 up by one 565 step to break the tie and force 4-color opaque mode.
            if color0_565 < 0xffff:
                color0_565 += 1
            else:
                color1_565 -= 1
    # Compute palette
    palette = dxt_make_color_palette(color0_565, color1_565)
    # Punch-through mode only has 3 real colors (palette[3] is a (0,0,0) placeholder for the
    # transparent slot, handled separately below via the alpha check) - opaque 4-color mode has 4
    # genuinely distinct usable colors, and must classify against all of them. Previously always
    # sliced to palette[0:3] regardless of mode - silently discarded palette[3] (the (2*color1 +
    # color0)/3 interpolated color) in ordinary opaque blocks, wasting a quarter of DXT1's
    # available palette capacity on every non-alpha block in every texture this addon compresses.
    usable_palette = palette[0:3] if block_needs_alpha else palette
    palette_indices = dxt1_palettize_block(pixels, usable_palette, coords[0], coords[1], img_width, block_dim, src_channels, block_needs_alpha)
    return (color0_565, color1_565, palette_indices)


DXT_BLOCK_DIM = 4
DXT1_BLOCK_SZ = 8
DXT3_BLOCK_SZ = 16
DXT5_BLOCK_SZ = 16


def compress_image(pixels: list[float], img_width: int, img_height: int, src_channels: int, with_alpha: bool) -> bytearray:
    if src_channels < 3 or (with_alpha and src_channels < 4):
        raise Exception("XVR error: Image must have either 3 or 4 channels")
    if img_width % DXT_BLOCK_DIM != 0 or img_height % DXT_BLOCK_DIM != 0:
        raise Exception("XVR error: Image dimensions must be multiples of {}".format(DXT_BLOCK_DIM))
    dst_buf = bytearray(img_width * img_height // (DXT_BLOCK_DIM * DXT_BLOCK_DIM) * DXT1_BLOCK_SZ)
    # Create work
    block_coords: list[tuple[int, int]] = []
    for y in range(0, img_height, DXT_BLOCK_DIM):
        for x in range(0, img_width, DXT_BLOCK_DIM):
            block_coords.append((x, y))
    # Start workers
    worker_fn = functools.partial(dxt1_compress_block, pixels, img_width, DXT_BLOCK_DIM, src_channels, with_alpha)
    results = list(map(worker_fn, block_coords))
    # Write results into buffer
    for (block_idx, result) in enumerate(results):
        dst_offset = block_idx * DXT1_BLOCK_SZ
        (color0, color1, color_indices) = result
        struct.pack_into("<HHL", dst_buf, dst_offset, color0, color1, color_indices)
    return dst_buf


def dxt3_alpha_block(
        pixels: list[float],
        img_width: int, block_dim: int,
        src_channels: int,
        coords: tuple[int, int]
    ) -> int:
    """Packs a block's 16 alpha values into the 64-bit explicit 4-bit-per-pixel table that
    dxt3_decompress reads back (alpha_step = 0x11, i.e. nibble * 17 == byte value)."""
    x, y = coords
    alpha_table = 0
    for block_px_i in range(block_dim * block_dim):
        block_x = block_px_i % block_dim
        block_y = block_px_i // block_dim
        src_offset = img_width * ((y + block_y) * src_channels) + ((x + block_x) * src_channels)
        alpha_float = pixels[src_offset + 3]
        nibble = max(0, min(15, round(alpha_float * 15)))
        alpha_table |= nibble << (block_px_i * 4)
    return alpha_table


def pack_a8r8g8b8(pixels: list[float], img_width: int, img_height: int, channels: int) -> bytearray:
    """Packs pixels directly as 32-bit-per-pixel A8R8G8B8 - no compression, so no per-block color
    budget to run out of, unlike every DXT variant (all of which share the same 4-colors-per-4x4-
    block RGB limit - DXT2/DXT3 only add alpha precision over DXT1, never RGB). Intended as a
    manual per-texture escape hatch (Texture.force_uncompressed) for content that genuinely needs
    it - confirmed the original game already does exactly this for a handful of its own textures
    (real map data, aforest01: 2 of 49 textures are stored as raw 16-bit R5G6B5/A1R5G5B5, not DXT,
    both high-frequency noise/foam-style content that would band or shift hue under DXT1) - so this
    isn't a deviation from the format, just reaching for the same tool the original assets use.

    Byte order (B, G, R, A per pixel) matches this module's existing D3D-format decoders
    (decompose_rgb565's callers, read_argb1555_texture) - not independently verified against a
    real A8R8G8B8 file, since none turned up in the maps checked so far; matches decode_a8r8g8b8
    in xvm.py, which this format's own read path uses, so at minimum the two stay round-trip
    consistent with each other regardless."""
    data = bytearray(img_width * img_height * 4)
    for i in range(img_width * img_height):
        src = i * channels
        dst = i * 4
        r = max(0, min(255, round(pixels[src + 0] * 0xff)))
        g = max(0, min(255, round(pixels[src + 1] * 0xff)))
        b = max(0, min(255, round(pixels[src + 2] * 0xff)))
        a = max(0, min(255, round(pixels[src + 3] * 0xff))) if channels >= 4 else 0xff
        data[dst + 0] = b
        data[dst + 1] = g
        data[dst + 2] = r
        data[dst + 3] = a
    return data


def decode_a8r8g8b8(src_buf: bytearray, img_width: int, img_height: int) -> list[float]:
    """Inverse of pack_a8r8g8b8 above - see that function's docstring for the byte-order caveat."""
    dst_chans = 4
    dst_buf = [0.0] * (img_width * img_height * dst_chans)
    for i in range(img_width * img_height):
        src = i * 4
        dst = i * dst_chans
        b, g, r, a = src_buf[src], src_buf[src + 1], src_buf[src + 2], src_buf[src + 3]
        dst_buf[dst + 0] = r / 0xff
        dst_buf[dst + 1] = g / 0xff
        dst_buf[dst + 2] = b / 0xff
        dst_buf[dst + 3] = a / 0xff
    return dst_buf


def dxt3_compress_block(
        pixels: list[float],
        img_width: int, block_dim: int,
        src_channels: int,
        coords: tuple[int, int]
    ) -> tuple[int, tuple[int, int, int]]:
    alpha_table = dxt3_alpha_block(pixels, img_width, block_dim, src_channels, coords)
    # DXT3's color block is always standard 4-color mode - alpha is stored explicitly above, so
    # there's no punch-through-alpha color ordering trick to apply here.
    color_block = dxt1_compress_block(pixels, img_width, block_dim, src_channels, False, coords)
    return (alpha_table, color_block)


def compress_image_dxt3(pixels: list[float], img_width: int, img_height: int, src_channels: int) -> bytearray:
    if src_channels < 4:
        raise Exception("XVR error: DXT3 requires an image with an alpha channel")
    if img_width % DXT_BLOCK_DIM != 0 or img_height % DXT_BLOCK_DIM != 0:
        raise Exception("XVR error: Image dimensions must be multiples of {}".format(DXT_BLOCK_DIM))
    dst_buf = bytearray(img_width * img_height // (DXT_BLOCK_DIM * DXT_BLOCK_DIM) * DXT3_BLOCK_SZ)
    block_coords: list[tuple[int, int]] = []
    for y in range(0, img_height, DXT_BLOCK_DIM):
        for x in range(0, img_width, DXT_BLOCK_DIM):
            block_coords.append((x, y))
    worker_fn = functools.partial(dxt3_compress_block, pixels, img_width, DXT_BLOCK_DIM, src_channels)
    results = list(map(worker_fn, block_coords))
    for (block_idx, result) in enumerate(results):
        dst_offset = block_idx * DXT3_BLOCK_SZ
        (alpha_table, (color0, color1, color_indices)) = result
        struct.pack_into("<Q", dst_buf, dst_offset, alpha_table)
        struct.pack_into("<HHL", dst_buf, dst_offset + 8, color0, color1, color_indices)
    return dst_buf


def decode_dxt_colors(src_buf: bytearray, src_offset: int, block_idx: int, img_width: int, _img_height: int, dst_buf: list[float], dst_chans: int, is_dxt1: bool):
    palette_ratios_no_alpha = [1.0, 0.0, 2.0 / 3.0, 1.0 / 3.0]
    palette_ratios_with_alpha = [1.0, 0.0, 0.5]

    (color0, color1, color_indices) = cast(tuple[int, int, int], struct.unpack_from("<HHL", src_buf, src_offset))

    # Color order indicates if color3 is 1bit alpha
    palette_with_alpha = is_dxt1 and color0 <= color1
    palette_ratios = palette_ratios_with_alpha if palette_with_alpha else palette_ratios_no_alpha

    color0 = decompose_rgb565(color0)
    color1 = decompose_rgb565(color1)

    # Pixel coords of block corner
    px_x0 = block_idx % (img_width // DXT_BLOCK_DIM) * DXT_BLOCK_DIM
    px_y0 = block_idx // (img_width // DXT_BLOCK_DIM) * DXT_BLOCK_DIM

    # Iterate over block pixels
    for block_px_i in range(DXT_BLOCK_DIM * DXT_BLOCK_DIM):
        block_x = block_px_i % DXT_BLOCK_DIM
        block_y = block_px_i // DXT_BLOCK_DIM
        # Calculate pixel index (basically y*w+x)
        px_i = ((px_y0 + block_y) * img_width + (px_x0 + block_x)) * dst_chans

        # Get color for this pixel
        color_idx = color_indices & 0b11
        color_indices = color_indices >> 2

        if palette_with_alpha and color_idx == 3:
            # Color3 is 1-bit alpha
            dst_buf[px_i + 3] = 0.0
        else:
            # Get color from palette
            ratio = palette_ratios[color_idx]
            for chan in range(len(color0)):
                # Add together portion of color0 and inverse portion of color1
                part0 = ratio * (color0[chan] / 0xff)
                part1 = (1.0 - ratio) * (color1[chan] / 0xff)
                dst_buf[px_i + chan] = part0 + part1
            # No alpha
            dst_buf[px_i + 3] = 1.0


def dxt1_decompress(src_buf: bytearray, img_width: int, img_height: int) -> list[float]:
    dst_chans = 4 # Blender always wants 4 channels
    dst_buf = img_width * img_height * dst_chans * [0.0]
    num_blocks = img_width * img_height // (DXT_BLOCK_DIM * DXT_BLOCK_DIM)

    # Iterate over compressed blocks
    for block_idx in range(num_blocks):
        colors_offset = block_idx * DXT1_BLOCK_SZ
        decode_dxt_colors(src_buf, colors_offset, block_idx, img_width, img_height, dst_buf, dst_chans, True)

    return dst_buf


def dxt3_decompress(src_buf: bytearray, img_width: int, img_height: int) -> list[float]:
    dst_chans = 4 # Blender always wants 4 channels
    dst_buf = img_width * img_height * dst_chans * [0.0]
    num_blocks = img_width * img_height // (DXT_BLOCK_DIM * DXT_BLOCK_DIM)
    alpha_table_size = 8

    # Iterate over compressed blocks
    for block_idx in range(num_blocks):
        # Alpha palette is first then colors
        alphas_offset = block_idx * DXT3_BLOCK_SZ
        colors_offset = block_idx * DXT3_BLOCK_SZ + alpha_table_size
        # Read and write colors first
        decode_dxt_colors(src_buf, colors_offset, block_idx, img_width, img_height, dst_buf, dst_chans, False)
        # Pixel coords of block corner
        px_x0 = block_idx % (img_width // DXT_BLOCK_DIM) * DXT_BLOCK_DIM
        px_y0 = block_idx // (img_width // DXT_BLOCK_DIM) * DXT_BLOCK_DIM
        # Read and write alphas
        (alpha_table, ) = cast(tuple[int], struct.unpack_from("<Q", src_buf, alphas_offset))
        for block_px_i in range(DXT_BLOCK_DIM * DXT_BLOCK_DIM):
            block_x = block_px_i % DXT_BLOCK_DIM
            block_y = block_px_i // DXT_BLOCK_DIM
            # Calculate pixel index (basically y*w+x)
            px_i = ((px_y0 + block_y) * img_width + (px_x0 + block_x)) * dst_chans
            # Unpack 4-bit alpha value
            alpha_step = 0x11 # Distance between possible values when stretching 4-bit to 8-bit
            alpha_value = ((alpha_table >> (block_px_i * 4)) & 0b1111) * alpha_step
            dst_buf[px_i + 3] = alpha_value / 0xff

    return dst_buf


def dxt5_decompress(src_buf: bytearray, img_width: int, img_height: int) -> list[float]:
    dst_chans = 4 # Blender always wants 4 channels
    dst_buf = img_width * img_height * dst_chans * [0.0]
    num_blocks = img_width * img_height // (DXT_BLOCK_DIM * DXT_BLOCK_DIM)
    alpha_data_size = 8
    alpha_palette_size = 6

    # Iterate over compressed blocks
    for block_idx in range(num_blocks):
        # Alpha palette is first then colors
        alphas_offset = block_idx * DXT5_BLOCK_SZ
        colors_offset = block_idx * DXT5_BLOCK_SZ + alpha_data_size
        # Read and write colors first
        decode_dxt_colors(src_buf, colors_offset, block_idx, img_width, img_height, dst_buf, dst_chans, False)
        # Read and write alphas
        alpha0 = src_buf[alphas_offset]
        alpha1 = src_buf[alphas_offset + 1]
        # Read 48-bit value
        alpha_indices = 0
        for i in range(alpha_palette_size):
            alpha_indices = (alpha_indices << 8) | src_buf[alphas_offset + alpha_data_size - 1 - i]
        # Pixel coords of block corner
        px_x0 = block_idx % (img_width // DXT_BLOCK_DIM) * DXT_BLOCK_DIM
        px_y0 = block_idx // (img_width // DXT_BLOCK_DIM) * DXT_BLOCK_DIM
        for block_px_i in range(DXT_BLOCK_DIM * DXT_BLOCK_DIM):
            block_x = block_px_i % DXT_BLOCK_DIM
            block_y = block_px_i // DXT_BLOCK_DIM
            # Calculate pixel index (basically y*w+x)
            px_i = ((px_y0 + block_y) * img_width + (px_x0 + block_x)) * dst_chans
            # Lookup alpha with 3-bit index
            alpha_idx = (alpha_indices >> (block_px_i * 3)) & 0b111
            if alpha_idx == 0:
                alpha_value = alpha0
            elif alpha_idx == 1:
                alpha_value = alpha1
            elif alpha0 > alpha1:
                # Interpolation method 1
                alpha_value = (((8 - alpha_idx) * alpha0 + (alpha_idx - 1) * alpha1) / 7)
            elif alpha_idx == 6:
                alpha_value = 0
            elif alpha_idx == 7:
                alpha_value = 0xff
            else:
                # Interpolation method 2
                alpha_value = (((6 - alpha_idx) * alpha0 + (alpha_idx - 1) * alpha1) / 5)
            dst_buf[px_i + 3] = alpha_value / 0xff

    return dst_buf