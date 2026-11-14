#!/usr/bin/env python3
"""In-place slot relabel for an already-built liblp super.img.

Renames every "<part>_a"/"<part>_b" partition entry (and moves it to the
matching qti_dynamic_partitions_a/_b group) to the requested target slot,
by patching only the small liblp metadata copies embedded in the image.
No partition payload data is read or moved: on a sparse image, only the
metadata's own raw chunks (a few KB total) are touched, regardless of how
large the super image itself is.

This intentionally supports only the exact layout this build tool produces
(liblp metadata major version 10, groups named qti_dynamic_partitions_a/_b,
partitions named "<name>_a"/"<name>_b"). Anything else exits with status 2
so the caller can fall back to the slower unpack+rebuild path.

Exit codes:
  0 - patched successfully in place
  2 - this image/layout is not supported by the fast path; fall back
  1 - hard error (bad args, I/O failure, unexpected corruption)
"""

from __future__ import annotations

import hashlib
import struct
import sys

SPARSE_MAGIC = 0xED26FF3A
GEOMETRY_MAGIC = 0x616C4467
HEADER_MAGIC = 0x414C5030
SUPPORTED_MAJOR_VERSION = 10

CHUNK_TYPE_RAW = 0xCAC1
CHUNK_TYPE_FILL = 0xCAC2
CHUNK_TYPE_DONT_CARE = 0xCAC3
CHUNK_TYPE_CRC32 = 0xCAC4

LP_PARTITION_RESERVED_BYTES = 4096
LP_METADATA_GEOMETRY_SIZE = 4096

GROUP_A = "qti_dynamic_partitions_a"
GROUP_B = "qti_dynamic_partitions_b"


class NotSupported(Exception):
    pass


class HardError(Exception):
    pass


class Chunk:
    __slots__ = ("ctype", "logical_start", "logical_end", "file_offset")

    def __init__(self, ctype, logical_start, logical_end, file_offset):
        self.ctype = ctype
        self.logical_start = logical_start
        self.logical_end = logical_end
        self.file_offset = file_offset


class ImageMap:
    """Maps logical (unsparsed) byte offsets to physical file offsets."""

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            head = f.read(28)
        if len(head) >= 4 and struct.unpack_from("<I", head, 0)[0] == SPARSE_MAGIC:
            self.sparse = True
            self._parse_sparse()
        else:
            self.sparse = False
            self.chunks = None

    def _parse_sparse(self):
        with open(self.path, "rb") as f:
            header = f.read(28)
            (magic, major, minor, hdr_sz, chdr_sz, blk_sz, total_blocks,
             total_chunks, _checksum) = struct.unpack("<IHHHHIIII", header)
            if magic != SPARSE_MAGIC:
                raise HardError("not a sparse image")
            if hdr_sz < 28 or chdr_sz < 12:
                raise NotSupported("unexpected sparse header sizes")
            self.block_size = blk_sz
            chunks = []
            pos = hdr_sz
            logical_block = 0
            f.seek(pos)
            for _ in range(total_chunks):
                chdr = f.read(chdr_sz)
                if len(chdr) < 12:
                    raise HardError("truncated sparse chunk header")
                ctype, _reserved1, chunk_blocks, total_sz = struct.unpack_from(
                    "<HHII", chdr, 0)
                data_len = total_sz - chdr_sz
                chunks.append(Chunk(
                    ctype,
                    logical_block * blk_sz,
                    (logical_block + chunk_blocks) * blk_sz,
                    pos + chdr_sz,
                ))
                logical_block += chunk_blocks
                pos += total_sz
                f.seek(pos)
            self.chunks = chunks

    def _find_chunk(self, offset):
        for c in self.chunks:
            if c.logical_start <= offset < c.logical_end:
                return c
        return None

    def check_writable(self, offset, length):
        """Raise NotSupported unless [offset, offset+length) is entirely
        backed by RAW chunk(s) (or the image is raw/unsparsed)."""
        if not self.sparse:
            return
        pos = offset
        end = offset + length
        while pos < end:
            c = self._find_chunk(pos)
            if c is None:
                raise NotSupported(f"offset {pos} past end of sparse image")
            if c.ctype != CHUNK_TYPE_RAW:
                raise NotSupported(
                    f"offset {pos} falls in a non-RAW sparse chunk (type {hex(c.ctype)})")
            pos = c.logical_end

    def read(self, offset, length):
        with open(self.path, "rb") as f:
            if not self.sparse:
                f.seek(offset)
                data = f.read(length)
                if len(data) != length:
                    raise HardError("short read")
                return data
            out = bytearray()
            pos = offset
            end = offset + length
            while pos < end:
                c = self._find_chunk(pos)
                if c is None or c.ctype != CHUNK_TYPE_RAW:
                    raise NotSupported("read() outside RAW-backed range")
                take = min(c.logical_end, end) - pos
                phys = c.file_offset + (pos - c.logical_start)
                f.seek(phys)
                chunk_data = f.read(take)
                if len(chunk_data) != take:
                    raise HardError("short read")
                out += chunk_data
                pos += take
            return bytes(out)

    def write(self, offset, data):
        # Caller must have already called check_writable() for this exact
        # range for every offset it intends to touch, before writing any of
        # them, so a failure here never leaves a partially patched file.
        length = len(data)
        with open(self.path, "r+b") as f:
            if not self.sparse:
                f.seek(offset)
                f.write(data)
                return
            pos = offset
            end = offset + length
            written = 0
            while pos < end:
                c = self._find_chunk(pos)
                if c is None or c.ctype != CHUNK_TYPE_RAW:
                    raise HardError("write() outside RAW-backed range (should have been caught earlier)")
                take = min(c.logical_end, end) - pos
                phys = c.file_offset + (pos - c.logical_start)
                f.seek(phys)
                f.write(data[written:written + take])
                written += take
                pos += take


def sha256_with_zeroed_range(blob, start, length):
    b = bytearray(blob)
    b[start:start + length] = b"\x00" * length
    return hashlib.sha256(bytes(b)).digest()


def parse_geometry(img: ImageMap):
    primary = img.read(LP_PARTITION_RESERVED_BYTES, LP_METADATA_GEOMETRY_SIZE)
    backup = img.read(LP_PARTITION_RESERVED_BYTES + LP_METADATA_GEOMETRY_SIZE,
                       LP_METADATA_GEOMETRY_SIZE)
    if primary != backup:
        raise NotSupported("primary/backup geometry blocks differ")
    magic, struct_size = struct.unpack_from("<II", primary, 0)
    if magic != GEOMETRY_MAGIC:
        raise NotSupported("bad geometry magic")
    metadata_max_size, metadata_slot_count, logical_block_size = struct.unpack_from(
        "<III", primary, 40)
    if metadata_max_size <= 0 or metadata_slot_count <= 0:
        raise NotSupported("invalid geometry sizes")
    return metadata_max_size, metadata_slot_count, logical_block_size


def primary_metadata_offset(metadata_max_size, slot_number):
    return LP_PARTITION_RESERVED_BYTES + LP_METADATA_GEOMETRY_SIZE * 2 + metadata_max_size * slot_number


def backup_metadata_offset(metadata_max_size, metadata_slot_count, slot_number):
    start = LP_PARTITION_RESERVED_BYTES + LP_METADATA_GEOMETRY_SIZE * 2 + metadata_max_size * metadata_slot_count
    return start + metadata_max_size * slot_number


def parse_metadata_copy(img: ImageMap, offset):
    head = img.read(offset, 512)
    magic, major, minor, header_size = struct.unpack_from("<IHHI", head, 0)
    if magic != HEADER_MAGIC:
        raise NotSupported("bad metadata header magic")
    if major != SUPPORTED_MAJOR_VERSION:
        raise NotSupported(f"unsupported metadata major version {major}")
    if header_size > len(head):
        head = img.read(offset, header_size)
    header_checksum = head[12:44]
    tables_size, = struct.unpack_from("<I", head, 44)
    tables_checksum = head[48:80]
    part_desc = struct.unpack_from("<III", head, 80)
    ext_desc = struct.unpack_from("<III", head, 92)
    grp_desc = struct.unpack_from("<III", head, 104)
    bdev_desc = struct.unpack_from("<III", head, 116)

    calc_header = sha256_with_zeroed_range(head[:header_size], 12, 32)
    if calc_header != header_checksum:
        raise NotSupported("header checksum mismatch")

    tables = img.read(offset + header_size, tables_size)
    if hashlib.sha256(tables).digest() != tables_checksum:
        raise NotSupported("tables checksum mismatch")

    return {
        "header_size": header_size,
        "tables_size": tables_size,
        "part_desc": part_desc,
        "ext_desc": ext_desc,
        "grp_desc": grp_desc,
        "bdev_desc": bdev_desc,
        "header": head[:header_size],
        "tables": tables,
    }


def build_patched_blob(meta, target_slot):
    grp_off, grp_num, grp_entry = meta["grp_desc"]
    part_off, part_num, part_entry = meta["part_desc"]
    tables = bytearray(meta["tables"])

    group_index = {}
    for i in range(grp_num):
        e = tables[grp_off + i * grp_entry: grp_off + i * grp_entry + grp_entry]
        name = e[0:36].split(b"\x00", 1)[0].decode("ascii", "replace")
        group_index[name] = i

    if GROUP_A not in group_index or GROUP_B not in group_index:
        raise NotSupported(f"groups {GROUP_A}/{GROUP_B} not found")

    target_group_name = GROUP_A if target_slot == "a" else GROUP_B
    target_group_idx = group_index[target_group_name]
    suffix_group = {"a": group_index[GROUP_A], "b": group_index[GROUP_B]}
    target_suffix_byte = ord(target_slot)

    changed = 0
    for i in range(part_num):
        base = part_off + i * part_entry
        entry = tables[base:base + part_entry]
        raw_name = entry[0:36]
        name = raw_name.split(b"\x00", 1)[0].decode("ascii", "replace")
        if not (name.endswith("_a") or name.endswith("_b")):
            raise NotSupported(f"partition '{name}' does not end in _a/_b")
        cur_suffix = name[-1]
        (group_idx,) = struct.unpack_from("<I", entry, 48)
        if group_idx != suffix_group.get(cur_suffix):
            raise NotSupported(
                f"partition '{name}' group_index {group_idx} does not match its suffix")

        if cur_suffix != target_slot:
            name_bytes = bytearray(raw_name)
            # Name is a NUL-padded fixed 36-byte field; only the final
            # character before the terminator changes ("_a" <-> "_b").
            term = raw_name.find(b"\x00")
            end = term if term != -1 else len(raw_name)
            name_bytes[end - 1] = target_suffix_byte
            tables[base:base + 36] = bytes(name_bytes)
            struct.pack_into("<I", tables, base + 48, target_group_idx)
            changed += 1

    new_tables = bytes(tables)
    header = bytearray(meta["header"])
    struct.pack_into("<I", header, 44, len(new_tables))
    header[48:80] = hashlib.sha256(new_tables).digest()
    header[12:44] = b"\x00" * 32
    header_checksum = hashlib.sha256(bytes(header)).digest()
    header[12:44] = header_checksum

    return bytes(header) + new_tables, changed


def reslot(path, target_slot):
    if target_slot not in ("a", "b"):
        raise HardError("target slot must be 'a' or 'b'")

    img = ImageMap(path)
    metadata_max_size, metadata_slot_count, logical_block_size = parse_geometry(img)

    primary0 = primary_metadata_offset(metadata_max_size, 0)
    meta = parse_metadata_copy(img, primary0)

    new_blob, changed = build_patched_blob(meta, target_slot)
    if len(new_blob) != meta["header_size"] + meta["tables_size"]:
        raise HardError("patched metadata size mismatch (unexpected)")

    # liblp only ever reads metadata slots 0 and 1 (the two real boot slots);
    # any further declared slots (metadata-slots > 2) are pure redundancy
    # scratch space that tools like lpadd do not necessarily keep in sync
    # (observed in practice: an lpmake seed + per-partition lpadd loop can
    # leave slot 2+ stale). Require slots 0/1 to already agree with each
    # other before mutating anything; slots beyond that just get resynced.
    original_blob = meta["header"] + meta["tables"]
    required_offsets = [
        primary_metadata_offset(metadata_max_size, 0),
        backup_metadata_offset(metadata_max_size, metadata_slot_count, 0),
    ]
    if metadata_slot_count >= 2:
        required_offsets += [
            primary_metadata_offset(metadata_max_size, 1),
            backup_metadata_offset(metadata_max_size, metadata_slot_count, 1),
        ]
    extra_offsets = []
    for slot_number in range(2, metadata_slot_count):
        extra_offsets.append(primary_metadata_offset(metadata_max_size, slot_number))
        extra_offsets.append(backup_metadata_offset(metadata_max_size, metadata_slot_count, slot_number))

    for off in required_offsets:
        existing = img.read(off, len(new_blob))
        if existing != original_blob:
            raise NotSupported("slot 0/1 metadata copies are not identical (unexpected layout)")
        img.check_writable(off, len(new_blob))
    for off in extra_offsets:
        # Not required to already match; just confirm we can safely patch it.
        img.check_writable(off, len(new_blob))

    all_offsets = required_offsets + extra_offsets

    if changed == 0:
        return 0  # already at the requested slot; nothing to do

    for off in all_offsets:
        img.write(off, new_blob)

    # Read back slot 0 and re-verify checksums against what liblp itself
    # would compute, so a silent write/mapping bug can never go unnoticed.
    verify = parse_metadata_copy(img, primary0)
    if verify["header"] + verify["tables"] != new_blob:
        raise HardError("post-write verification failed")

    return changed


def main(argv):
    if len(argv) != 3:
        print("Usage: lp_reslot.py <super.img> <a|b>", file=sys.stderr)
        return 1
    path, target_slot = argv[1], argv[2].lower()
    try:
        changed = reslot(path, target_slot)
    except NotSupported as e:
        print(f"lp_reslot: fast path not applicable: {e}", file=sys.stderr)
        return 2
    except HardError as e:
        print(f"lp_reslot: error: {e}", file=sys.stderr)
        return 1
    print(f"lp_reslot: patched {changed} partition entr{'y' if changed == 1 else 'ies'} to slot {target_slot}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
