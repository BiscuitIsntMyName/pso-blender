import functools, struct
from typing import cast, TypeAlias


RGB: TypeAlias = tuple[int, int, int]


def rgb8_to_rgb565(rgb: RGB) -> int:
    return ((rgb[0] & 0xf8) << 8) | ((rgb[1] & 0xfc) << 3) | (rgb[2] >> 3)


def _decompose_rgb565_uncached(rgb: int) -> RGB:
    r = rgb >> 11
    g = (rgb >> 5) & 0x3f
    b = rgb & 0x1f
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


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
    color covariance matrix - the standard technique real-time encoders like stb_dxt/squish use,
    simple enough for pure Python without needing a full eigendecomposition or a numpy dependency).

    Replaces an earlier independent-per-channel min/max ("bounding box corners" + an inset
    correction to compensate for those corners not being real pixels) - that approach constructs
    endpoints that don't correspond to any actual pixel in the block, and can visibly shift hue
    whenever a block's R/G/B channels aren't well correlated with the box's axes (confirmed on
    real map textures: a saturated, near-flat green background came back visibly more olive/
    desaturated after compression, even with zero alpha/mip involvement - traced all the way down
    to this exact function). Endpoints chosen this way are real pixel colors already inside the
    block's actual color distribution, so no inset/bias correction is needed afterward."""
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
    palette0 = decompose_rgb565(color0)
    palette1 = decompose_rgb565(color1)
    if color0 <= color1:
        palette2 = cast(RGB, tuple(map(lambda a, b: (a + b) // 2, palette0, palette1)))
        palette3 = (0, 0, 0)
    else:
        palette2 = cast(RGB, tuple(map(lambda a, b: (((a << 1) + b) // 3) | 0, palette0, palette1)))
        palette3 = cast(RGB, tuple(map(lambda a, b: (((b << 1) + a) // 3) | 0, palette0, palette1)))
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
        palette: tuple[RGB, RGB, RGB],
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
    # Pick the block's two endpoint colors along its own axis of greatest color variance.
    block_colors = _dxt_block_colors(pixels, coords[0], coords[1], img_width, block_dim, src_channels)
    color0, color1 = dxt_get_block_endpoints(block_colors)
    color0_565 = rgb8_to_rgb565(color0)
    color1_565 = rgb8_to_rgb565(color1)
    # Punch-through mode (color0 <= color1) is a per-block decision, not the whole image's - a
    # block with no meaningfully-transparent texel of its own should stay in normal 4-color opaque
    # mode (one more usable color) even inside an otherwise alpha-enabled image.
    block_needs_alpha = with_alpha and dxt1_block_needs_alpha(pixels, coords[0], coords[1], img_width, block_dim, src_channels)
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
    # Compute pixel palette indices of block
    palette_indices = dxt1_palettize_block(pixels, palette[0:3], coords[0], coords[1], img_width, block_dim, src_channels, block_needs_alpha)
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