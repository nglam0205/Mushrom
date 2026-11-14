#!/usr/bin/env python3
"""Flash super as sparse pieces composed from a layout image + partition images.

The layout is an lpmake --sparse image holding only the LP metadata, with the
real extents of every partition already allocated (partitions declared with
their sizes, no --image). Each partition image is then placed at the offset
the metadata gives it and the result is emitted as a series of Android sparse
files no larger than the bootloader's max-download-size, so no full super.img
and no lpmake/lpadd run is needed on the flashing side.

Every piece spans the whole device (leading/trailing DONT_CARE), exactly like
the pieces fastboot itself produces when it resparses a large image.

Usage:
  super_chunks.py <layout.img> <images_dir> <slot> <out_dir>
                  [--max-size BYTES] [--flash]
"""

from __future__ import annotations

import argparse
import struct
import subprocess
import sys
from pathlib import Path

BLOCK = 4096
SECTOR = 512
SPARSE_MAGIC = 0xED26FF3A
CHUNK_RAW, CHUNK_FILL, CHUNK_DONT_CARE, CHUNK_CRC32 = 0xCAC1, 0xCAC2, 0xCAC3, 0xCAC4
LP_GEOMETRY_MAGIC = 0x616C4467
LP_HEADER_MAGIC = 0x414C5030
LP_RESERVED = 4096
LP_GEOMETRY_SIZE = 4096
LP_TARGET_LINEAR = 0
COPY = 8 * 1024 * 1024


def read_layout(path: Path):
    """Return (total_blocks, segments) of the sparse layout image.

    A segment is (device_offset, length, source); source is (path, offset)
    for data or a 4-byte fill pattern.
    """
    segments = []
    with path.open("rb") as f:
        magic, major, _, file_hdr, chunk_hdr, blk, total_blocks, chunks, _ = \
            struct.unpack("<IHHHHIIII", f.read(28))
        if magic != SPARSE_MAGIC or major != 1:
            raise ValueError(f"{path.name}: not an Android sparse image")
        if blk != BLOCK:
            raise ValueError(f"{path.name}: unsupported block size {blk}")
        f.seek(file_hdr)
        block = 0
        for _ in range(chunks):
            kind, _, count, total = struct.unpack("<HHII", f.read(12))
            f.seek(chunk_hdr - 12, 1)
            body = total - chunk_hdr
            if kind == CHUNK_RAW:
                segments.append((block * BLOCK, count * BLOCK, (path, f.tell())))
            elif kind == CHUNK_FILL:
                segments.append((block * BLOCK, count * BLOCK, f.read(4)))
                body = 0
            elif kind not in (CHUNK_DONT_CARE, CHUNK_CRC32):
                raise ValueError(f"{path.name}: unknown chunk type {kind:#x}")
            f.seek(body, 1)
            if kind != CHUNK_CRC32:
                block += count
    return total_blocks, segments


def read_head(segments, size: int) -> bytes:
    """First `size` device bytes of the layout (holes read as zero)."""
    head = bytearray(size)
    for offset, length, source in segments:
        if offset >= size:
            continue
        n = min(length, size - offset)
        if isinstance(source, bytes):
            head[offset:offset + n] = (source * (n // 4 + 1))[:n]
        else:
            with source[0].open("rb") as f:
                f.seek(source[1])
                head[offset:offset + n] = f.read(n)
    return bytes(head)


def partition_extents(segments, slot: str) -> dict:
    """{partition base name: [(device_offset, length), ...]} for one slot."""
    geometry = read_head(segments, LP_RESERVED + LP_GEOMETRY_SIZE)[LP_RESERVED:]
    magic, _ = struct.unpack_from("<II", geometry)
    if magic != LP_GEOMETRY_MAGIC:
        raise ValueError("layout has no LP geometry")
    max_size, slots, _ = struct.unpack_from("<III", geometry, 40)
    start = LP_RESERVED + 2 * LP_GEOMETRY_SIZE
    head = read_head(segments, start + max_size * slots)
    magic, _, _, header_size = struct.unpack_from("<IHHI", head, start)
    if magic != LP_HEADER_MAGIC:
        raise ValueError("layout has no LP metadata header")
    parts_off, parts_n, parts_sz, ext_off, ext_n, ext_sz = \
        struct.unpack_from("<6I", head, start + 80)
    tables = start + header_size

    extents = []
    for i in range(ext_n):
        sectors, kind, data, _ = struct.unpack_from("<QIQI", head, tables + ext_off + i * ext_sz)
        extents.append((sectors, kind, data))

    result = {}
    suffix = f"_{slot}"
    for i in range(parts_n):
        name, _, first, count, _ = struct.unpack_from(
            "<36sIIII", head, tables + parts_off + i * parts_sz)
        name = name.rstrip(b"\0").decode()
        if not name.endswith(suffix) or not count:
            continue
        runs = []
        for sectors, kind, data in extents[first:first + count]:
            if kind != LP_TARGET_LINEAR:
                raise ValueError(f"{name}: unsupported extent type {kind}")
            runs.append((data * SECTOR, sectors * SECTOR))
        result[name[:-len(suffix)]] = runs
    return result


def plan_segments(layout: Path, images: Path, slot: str):
    total_blocks, segments = read_layout(layout)
    for base, runs in partition_extents(segments, slot).items():
        image = images / f"{base}.img"
        if not image.is_file():
            raise ValueError(f"{image.name}: missing image for {base}_{slot}")
        with image.open("rb") as f:
            if struct.unpack("<I", f.read(4).ljust(4, b"\0"))[0] == SPARSE_MAGIC:
                raise ValueError(f"{image.name}: sparse image; a raw image is required")
        size = image.stat().st_size
        if size > sum(length for _, length in runs):
            raise ValueError(f"{image.name}: larger than {base}_{slot} in the layout")
        pos = 0
        for offset, length in runs:
            n = min(length, size - pos)
            if n <= 0:
                break
            segments.append((offset, n, (image, pos)))
            pos += n

    segments.sort(key=lambda s: s[0])
    end = 0
    for offset, length, _ in segments:
        if offset % BLOCK or offset < end:
            raise ValueError(f"misaligned or overlapping data at {offset}")
        end = offset + -(-length // BLOCK) * BLOCK
    if end > total_blocks * BLOCK:
        raise ValueError("data exceeds the super size")
    return total_blocks, segments


def split_pieces(segments, max_size: int):
    """Group segments into pieces holding at most max_size bytes of data."""
    budget = (max_size - 28) // BLOCK * BLOCK
    pieces, current, used = [], [], 0
    for offset, length, source in segments:
        if isinstance(source, bytes):
            cost = 16
            if current and used + cost > budget:
                pieces.append(current)
                current, used = [], 0
            current.append((offset, length, source))
            used += cost
            continue
        path, pos = source
        while length > 0:
            room = (budget - used - 24) // BLOCK * BLOCK
            if room <= 0:
                pieces.append(current)
                current, used = [], 0
                continue
            n = min(length, room)
            current.append((offset, n, (path, pos)))
            used += 12 + -(-n // BLOCK) * BLOCK
            offset, pos, length = offset + n, pos + n, length - n
    if current:
        pieces.append(current)
    return pieces


def write_piece(path: Path, total_blocks: int, piece) -> None:
    chunks = []
    block = 0
    for offset, length, source in piece:
        start = offset // BLOCK
        count = -(-length // BLOCK)
        if start > block:
            chunks.append((CHUNK_DONT_CARE, start - block, None, 0))
        chunks.append((CHUNK_FILL if isinstance(source, bytes) else CHUNK_RAW,
                       count, source, length))
        block = start + count
    if total_blocks > block:
        chunks.append((CHUNK_DONT_CARE, total_blocks - block, None, 0))

    with path.open("wb") as out:
        out.write(struct.pack("<IHHHHIIII", SPARSE_MAGIC, 1, 0, 28, 12, BLOCK,
                              total_blocks, len(chunks), 0))
        for kind, count, source, length in chunks:
            body = {CHUNK_RAW: count * BLOCK, CHUNK_FILL: 4}.get(kind, 0)
            out.write(struct.pack("<HHII", kind, 0, count, 12 + body))
            if kind == CHUNK_FILL:
                out.write(source)
            elif kind == CHUNK_RAW:
                with source[0].open("rb") as f:
                    f.seek(source[1])
                    left = length
                    while left:
                        data = f.read(min(COPY, left))
                        if not data:
                            raise IOError(f"{source[0].name}: unexpected end of data")
                        out.write(data)
                        left -= len(data)
                out.write(bytes(count * BLOCK - length))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("layout")
    parser.add_argument("images_dir")
    parser.add_argument("slot", choices=("a", "b"))
    parser.add_argument("out_dir")
    parser.add_argument("--max-size", type=int, default=256 * 1024 * 1024,
                        help="largest piece in bytes (default: 256 MiB)")
    parser.add_argument("--flash", action="store_true",
                        help="fastboot flash super each piece, then delete it")
    args = parser.parse_args()

    total_blocks, segments = plan_segments(Path(args.layout), Path(args.images_dir), args.slot)
    pieces = split_pieces(segments, args.max_size)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for index, piece in enumerate(pieces, 1):
        path = out_dir / f"super_{index:03d}.img"
        write_piece(path, total_blocks, piece)
        print(f"piece {index}/{len(pieces)}: {path.name} {path.stat().st_size} bytes", flush=True)
        if args.flash:
            status = subprocess.run(["fastboot", "flash", "super", str(path)]).returncode
            path.unlink()
            if status:
                print(f"super_chunks: fastboot flash failed on piece {index}", file=sys.stderr)
                return status
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, struct.error) as exc:
        print(f"super_chunks: {exc}", file=sys.stderr)
        raise SystemExit(1)
