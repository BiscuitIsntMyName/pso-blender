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
from dataclasses import dataclass


@dataclass
class GslEntry:
    filename: str = ""
    data: bytes = b""


@dataclass
class GslTocEntry:
    """Table-of-contents-only metadata for one archived file - no payload bytes, unlike GslEntry
    above. Cheap to build a list of these even for an archive with thousands of entries, since
    nothing beyond the header table itself is ever read (see list_entries)."""
    filename: str = ""
    byte_offset: int = 0
    length: int = 0


_ENTRY_STRUCT_SIZE = 0x28  # 32-byte name + 2x U32, then 8 bytes padding read separately
_ENTRY_PADDING_SIZE = 0x08
_SECTOR_SIZE = 2048
# Comfortably larger than any real header table (Data.gsl's 1524 entries -> ~61KB of table) while
# still tiny relative to a real archive (Data.gsl itself is ~71.7MB) - lets list_entries() below
# avoid reading the whole file just to list filenames, in the overwhelmingly common case.
_HEADER_PREFETCH_SIZE = 4 * 1024 * 1024


def _parse_table(buf: bytes, file_size: int) -> tuple[list[tuple[str, int, int]], bool]:
    """Parses (filename, byte_offset, length) triples out of buf. Returns (entries,
    buffer_exhausted) - buffer_exhausted is True only when parsing had to stop because `buf` ran
    out before the table's real end could be determined (not because the table itself ended),
    signaling a caller that read a partial prefix (see list_entries) to retry with more data."""
    offset = 0
    data_start = file_size  # Shrinks to the first real file's offset as the table is read.
    table_entries: list[tuple[str, int, int]] = []
    while offset < data_start:
        if offset + _ENTRY_STRUCT_SIZE > len(buf):
            return table_entries, True
        name_bytes, sector_offset, length = struct.unpack_from("<32sII", buf, offset)
        offset += _ENTRY_STRUCT_SIZE + _ENTRY_PADDING_SIZE

        name = name_bytes.decode("ascii", errors="replace").rstrip(" \t\r\n\0")
        if not name:
            break

        byte_offset = sector_offset * _SECTOR_SIZE
        data_start = min(data_start, byte_offset)
        table_entries.append((name, byte_offset, length))

    return table_entries, False


def read(path: str) -> list[GslEntry]:
    """Parses a real, game-authored .gsl archive into its contained files, including every file's
    full payload bytes. Loads the whole archive into memory - for just listing what's inside
    (e.g. an interactive browser), use list_entries() below instead."""
    with open(path, "rb") as f:
        buf = f.read()
    table_entries, _truncated = _parse_table(buf, len(buf))
    return [GslEntry(filename=name, data=buf[off:off + length]) for (name, off, length) in table_entries]


def list_entries(path: str) -> list[GslTocEntry]:
    """Parses just Data.gsl's header table - filenames/offsets/lengths only, no payload bytes ever
    read. Cheap enough to call interactively even on a large archive (unlike read() above, which
    loads the entire file)."""
    file_size = os.path.getsize(path)
    with open(path, "rb") as f:
        buf = f.read(min(file_size, _HEADER_PREFETCH_SIZE))
    table_entries, truncated = _parse_table(buf, file_size)
    if truncated:
        # The real table turned out bigger than the prefetch guessed (unusual - real archives'
        # tables are a tiny fraction of this size) - fall back to a full read rather than silently
        # returning a partial listing.
        with open(path, "rb") as f:
            buf = f.read()
        table_entries, _truncated = _parse_table(buf, file_size)
    return [GslTocEntry(filename=name, byte_offset=off, length=length) for (name, off, length) in table_entries]


def extract_entry(path: str, entry: GslTocEntry) -> bytes:
    """Reads only one entry's payload bytes directly off disk (seek + read of exactly
    entry.length bytes) - unlike read() above, never loads the rest of the archive."""
    with open(path, "rb") as f:
        f.seek(entry.byte_offset)
        return f.read(entry.length)


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
