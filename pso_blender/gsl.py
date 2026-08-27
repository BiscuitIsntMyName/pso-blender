"""GSL archive support (e.g. Data.gsl) - a flat, uncompressed container format PSO uses to bundle
several files (typically .rel/.xj/.xvm) together, sourced from the game's CD-ROM-based sector
addressing. Not a relocatable/pointer format like .rel, so this doesn't use the Serializable/Rel
machinery those modules rely on - it's just a flat table of fixed-size entries read with plain
struct unpacking, the same approach the reference implementation below uses.

Format confirmed against real map_acity's containing Data.gsl and cross-checked against
theanine3D/pso_ultimate_importer (MIT licensed) - a community Blender addon whose gsl_read_archive
implements the identical format:
https://github.com/theanine3D/pso_ultimate_importer

Each 40-byte table entry is [32-byte ASCII filename][U32 sector offset][U32 length][8 bytes
padding]. There's no explicit entry count - the table is read until it would overlap the first
file's actual data (tracked as the smallest offset seen so far).
"""
import os
import struct
from dataclasses import dataclass, field


@dataclass
class GslEntry:
    filename: str = ""
    data: bytes = b""


_ENTRY_STRUCT_SIZE = 0x28  # 32-byte name + 2x U32, then 8 bytes padding read separately
_ENTRY_PADDING_SIZE = 0x08
_SECTOR_SIZE = 2048


def read(path: str) -> list[GslEntry]:
    """Parses a real, game-authored .gsl archive into its contained files."""
    with open(path, "rb") as f:
        buf = f.read()
    file_size = len(buf)

    offset = 0
    data_start = file_size  # Shrinks to the first real file's offset as the table is read.
    table_entries: list[tuple[str, int, int]] = []  # (filename, byte_offset, length)
    while offset < data_start:
        if offset + _ENTRY_STRUCT_SIZE > file_size:
            break
        name_bytes, sector_offset, length = struct.unpack_from("<32sII", buf, offset)
        offset += _ENTRY_STRUCT_SIZE + _ENTRY_PADDING_SIZE

        name = name_bytes.decode("ascii", errors="replace").rstrip(" \t\r\n\0")
        if not name:
            break

        byte_offset = sector_offset * _SECTOR_SIZE
        data_start = min(data_start, byte_offset)
        table_entries.append((name, byte_offset, length))

    return [GslEntry(filename=name, data=buf[off:off + length]) for (name, off, length) in table_entries]


def extract_to_directory(path: str, out_dir: str) -> list[str]:
    """Convenience wrapper: reads a .gsl and writes every contained file out to out_dir,
    returning the list of paths written - e.g. so the resulting .rel/.xj/.xvm files can be fed
    straight into this addon's existing import operators without any GSL-specific UI of their
    own."""
    os.makedirs(out_dir, exist_ok=True)
    written: list[str] = []
    for entry in read(path):
        out_path = os.path.join(out_dir, entry.filename)
        with open(out_path, "wb") as f:
            f.write(entry.data)
        written.append(out_path)
    return written
