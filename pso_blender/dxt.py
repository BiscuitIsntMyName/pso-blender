import functools, multiprocessing, struct
from typing import cast


type RGB = tuple[int, int, int]


def rgb8_to_rgb565(rgb: RGB) -> int:
    return ((rgb[0] & 0xf8) << 8) | ((rgb[1] & 0xfc) << 3) | (rgb[2] >> 3)


def decompose_rgb565(rgb: int) -> RGB:
    r = rgb >> 11
    g = (rgb >> 5) & 0x3f
    b = rgb & 0x1f
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def dxt_get_block_bounds(
        pixels: list[float],
        x: int, y: int,
        img_width: int, block_dim: int,
        src_channels: int
    ) -> tuple[RGB, RGB]:
    dst_channels = 3
    min_values = [0xff] * dst_channels
    max_values = [0] * dst_channels
    for block_y in range(block_dim):
        for block_x in range(block_dim):
            src_offset = img_width * ((y + block_y) * src_channels) + ((x + block_x) * src_channels)
            for chan in range(dst_channels):
                val = int(pixels[src_offset + chan] * 0xff)
                if max_values[chan] < val:
                    max_values[chan] = val
                if min_values[chan] > val:
                    min_values[chan] = val
    return (cast(RGB, tuple(min_values)), cast(RGB, tuple(max_values)))


def dxt_quantize(min_rgb: RGB, max_rgb: RGB) -> tuple[int, int]:
    inset = tuple(map(lambda a, b: (a - b) >> 4, max_rgb, min_rgb))
    max_rgb565 = rgb8_to_rgb565(cast(RGB, tuple(map(lambda a, b: a - b if a >= b else 0, max_rgb, inset))))
    min_rgb565 = rgb8_to_rgb565(cast(RGB, tuple(map(lambda a, b: a + b if a + b < 0xff else 0xff, min_rgb, inset))))
    return (min_rgb565, max_rgb565)


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


def dxt1_palettize_block(
        pixels: list[float],
        palette: tuple[RGB, RGB, RGB],
        x: int, y: int,
        img_width: int, block_dim: int,
        src_channels: int,
        with_alpha: int
    ) -> int:
    palette_indices = 0
    px_idx = 0
    palette_len = len(palette)
    for block_y in range(block_dim):
        for block_x in range(block_dim):
            src_offset = img_width * ((y + block_y) * src_channels) + ((x + block_x) * src_channels)
            best_color_dist = float("inf")
            best_palette_idx = 0
            if with_alpha and pixels[src_offset + 3] < 1.0:
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
    # Find RGB bounds of block
    color0, color1 = dxt_get_block_bounds(pixels, coords[0], coords[1], img_width, block_dim, src_channels)
    # Quantize
    color0_565, color1_565 = dxt_quantize(color0, color1)
    if with_alpha:
        if color0_565 > color1_565:
            # Swap colors to indicate alpha format
            color0_565, color1_565 = color1_565, color0_565
    # Colors might get swapped by quantization
    elif color0_565 <= color1_565:
        color0_565, color1_565 = color1_565, color0_565
    # Compute palette
    palette = dxt_make_color_palette(color0_565, color1_565)
    # Compute pixel palette indices of block
    palette_indices = dxt1_palettize_block(pixels, palette[0:3], coords[0], coords[1], img_width, block_dim, src_channels, with_alpha)
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
    with multiprocessing.Pool() as pool:
        results = pool.map(worker_fn, block_coords)
    # Write results into buffer
    for (block_idx, result) in enumerate(results):
        dst_offset = block_idx * DXT1_BLOCK_SZ
        (color0, color1, color_indices) = result
        struct.pack_into("<HHL", dst_buf, dst_offset, color0, color1, color_indices)
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
            else:
                if alpha_idx == 6:
                    alpha_value = 0
                elif alpha_idx == 7:
                    alpha_value = 0xff
                # Interpolation method 2
                alpha_value = (((6 - alpha_idx) * alpha0 + (alpha_idx - 1) * alpha1) / 5)
            dst_buf[px_i + 3] = alpha_value / 0xff

    return dst_buf
