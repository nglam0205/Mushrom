#!/usr/bin/env python3
"""Patch the eight basic MT6993/Oplus LK fastboot gates for Find X9/X9 Pro.

The patch locations are derived from AArch64 control-flow and string xrefs,
not firmware-build-specific offsets.  Every accepted input must also match
either its signed cert2 hashes or its current lkpatcher override hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import struct
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence


LK_MAGIC = 0x58881688
LK_EXT_MAGIC = 0x58891689
HEADER_SIZE = 512
HEADER_HASH_OID = bytes.fromhex("060760867693160204")
IMAGE_HASH_OID = bytes.fromhex("060760867693160201")
HASH_BITS = bytes.fromhex("032100")
OVERRIDE_PREFIX_SIZE = 90

NOP = bytes.fromhex("1f2003d5")


@dataclass(frozen=True)
class Patch:
    offset: int
    original: bytes
    replacement: bytes
    purpose: str


REQUIRED_ANCHORS = (
    b"k6993v1_64",
    b"fastboot_unlock_verify pass",
    b"fastboot_unlock_verify ok",
)


class PatchError(ValueError):
    pass


def align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def padded_sha256(data: bytes, alignment: int = 1) -> bytes:
    if alignment > 1:
        data += b"\0" * ((-len(data)) % alignment)
    return hashlib.sha256(data).digest()


def hashes_after_oid(data: bytes, oid: bytes) -> List[bytes]:
    result: List[bytes] = []
    start = 0
    while True:
        pos = data.find(oid, start)
        if pos < 0:
            return result
        value = pos + len(oid)
        if data[value:value + len(HASH_BITS)] == HASH_BITS:
            digest = data[value + len(HASH_BITS):value + len(HASH_BITS) + 32]
            if len(digest) == 32:
                result.append(digest)
        start = pos + 1


@dataclass
class Entry:
    header: bytearray
    data: bytes
    name: str
    alignment: int
    original_offset: int

    def encode(self) -> bytes:
        header = bytearray(self.header)
        size = len(self.data)
        struct.pack_into("<I", header, 4, size & 0xFFFFFFFF)
        if struct.unpack_from("<I", header, 48)[0] == LK_EXT_MAGIC:
            struct.pack_into("<I", header, 72, size >> 32)
        raw = bytes(header) + self.data
        return raw + b"\0" * ((-len(raw)) % self.alignment)


def parse_image(image: bytes) -> tuple[List[Entry], bytes]:
    entries: List[Entry] = []
    offset = 0
    while offset + HEADER_SIZE <= len(image):
        if struct.unpack_from("<I", image, offset)[0] != LK_MAGIC:
            break
        header = bytearray(image[offset:offset + HEADER_SIZE])
        size_low = struct.unpack_from("<I", header, 4)[0]
        name = bytes(header[8:40]).split(b"\0", 1)[0].decode("ascii", "strict")
        extended = struct.unpack_from("<I", header, 48)[0] == LK_EXT_MAGIC
        header_size = struct.unpack_from("<I", header, 52)[0] if extended else HEADER_SIZE
        if header_size != HEADER_SIZE:
            raise PatchError(f"unsupported header size {header_size} for {name}")
        size_high = struct.unpack_from("<I", header, 72)[0] if extended else 0
        size = (size_high << 32) | size_low
        alignment = struct.unpack_from("<I", header, 68)[0] if extended else 8
        if not alignment or alignment > 0x100000:
            raise PatchError(f"invalid alignment {alignment} for {name}")
        data_start = offset + header_size
        data_end = data_start + size
        if data_end > len(image):
            raise PatchError(f"truncated {name} data")
        entries.append(Entry(header, image[data_start:data_end], name,
                             alignment, offset))
        offset = align_up(data_end, alignment)
        if struct.unpack_from("<I", header, 64)[0] == 1:
            break
    if not entries:
        raise PatchError("not a supported MediaTek LK container")
    return entries, image[offset:]


def find_lk_and_cert(entries: Sequence[Entry]) -> tuple[Entry, Entry]:
    lk_indices = [i for i, entry in enumerate(entries) if entry.name == "lk"]
    if len(lk_indices) != 1:
        raise PatchError(f"expected one lk entry, found {len(lk_indices)}")
    index = lk_indices[0]
    certificates: List[Entry] = []
    for entry in entries[index + 1:]:
        if not entry.name.startswith("cert"):
            break
        certificates.append(entry)
    cert2 = [entry for entry in certificates if entry.name.startswith("cert2")]
    if len(cert2) != 1:
        raise PatchError(f"lk must have exactly one adjacent cert2 entry, found {len(cert2)}")
    return entries[index], cert2[0]


def original_cert2(cert2: bytes) -> tuple[bytes, str]:
    # lkpatcher override is exactly: A0 58, two OID+BIT STRING hashes,
    # followed by the untouched signed certificate.
    if (len(cert2) >= OVERRIDE_PREFIX_SIZE + 2
            and cert2[:2] == bytes.fromhex("a058")
            and cert2[2:11] == HEADER_HASH_OID
            and cert2[11:14] == HASH_BITS
            and cert2[46:55] == IMAGE_HASH_OID
            and cert2[55:58] == HASH_BITS):
        return cert2[OVERRIDE_PREFIX_SIZE:], "override"
    if cert2.startswith(b"\x30"):
        return cert2, "standard"
    raise PatchError("unsupported or malformed lk cert2")


def validate_signed_input(lk: Entry, cert2: bytes, signed_cert: bytes,
                          cert_mode: str) -> None:
    header_hashes = hashes_after_oid(signed_cert, HEADER_HASH_OID)
    image_hashes = hashes_after_oid(signed_cert, IMAGE_HASH_OID)
    if not header_hashes or not image_hashes:
        raise PatchError("cannot parse signed hashes from lk cert2")
    current_header = hashlib.sha256(bytes(lk.header)).digest()
    current_image = padded_sha256(lk.data, 16)
    if header_hashes[-1] != current_header:
        raise PatchError("lk header does not match its original signed cert2")
    if cert_mode == "standard":
        if image_hashes[-1] != current_image:
            raise PatchError("lk data does not match its signed cert2; refusing unsafe input")
        return

    advertised_headers = hashes_after_oid(cert2[:OVERRIDE_PREFIX_SIZE], HEADER_HASH_OID)
    advertised_images = hashes_after_oid(cert2[:OVERRIDE_PREFIX_SIZE], IMAGE_HASH_OID)
    if (len(advertised_headers) != 1 or advertised_headers[0] != current_header
            or len(advertised_images) != 1 or advertised_images[0] != current_image):
        raise PatchError("lk data/header does not match its cert2 override; refusing unsafe input")


def word(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def is_bl(value: int) -> bool:
    return value & 0xFC000000 == 0x94000000


def branch_target(offset: int, value: int, bits: int, shift: int = 5) -> int:
    immediate = (value >> shift) & ((1 << bits) - 1)
    if immediate & (1 << (bits - 1)):
        immediate -= 1 << bits
    return offset + immediate * 4


def decode_adrp_add(data: bytes, offset: int) -> int | None:
    """Return the file-relative address formed by an ADRP/ADD pair for x0."""
    adrp = word(data, offset)
    add = word(data, offset + 4)
    if adrp & 0x9F00001F != 0x90000000:  # ADRP x0, ...
        return None
    if add & 0xFFC003FF != 0x91000000:  # ADD x0, x0, #imm{, LSL #12}
        return None
    imm = ((adrp >> 5) & 0x7FFFF) << 2 | ((adrp >> 29) & 0x3)
    if imm & (1 << 20):
        imm -= 1 << 21
    page = (offset & ~0xFFF) + imm * 0x1000
    immediate = (add >> 10) & 0xFFF
    if add & (1 << 22):
        immediate <<= 12
    return page + immediate


def points_to(data: bytes, offset: int, expected: bytes) -> bool:
    target = decode_adrp_add(data, offset)
    return target is not None and data[target:target + len(expected)] == expected


def locate_boot_gates(data: bytes) -> List[Patch]:
    candidates: List[int] = []
    for offset in range(4, len(data) - 0x108, 4):
        first = data[offset:offset + 4]
        second = data[offset + 0xFC:offset + 0x100]
        if first not in {NOP, bytes.fromhex("a0f1ff35")}:
            continue
        if second not in {NOP, bytes.fromhex("a0020054")}:
            continue
        if (data[offset - 4:offset] == bytes.fromhex("cce2ff97")
                and data[offset + 4:offset + 8] == bytes.fromhex("34000014")
                and data[offset + 0xF4:offset + 0xFC]
                == bytes.fromhex("e80740b91f050071")):
            candidates.append(offset)
    if len(candidates) != 1:
        raise PatchError(f"expected one boot/recovery gate block, found {len(candidates)}")
    base = candidates[0]
    return [
        Patch(base, bytes.fromhex("a0f1ff35"), NOP,
              "ignore boot/recovery gate result"),
        Patch(base + 0xFC, bytes.fromhex("a0020054"), NOP,
              "skip boot/recovery blocking branch"),
    ]


def locate_fastboot_gates(data: bytes) -> List[Patch]:
    candidates: List[int] = []
    for offset in range(0, len(data) - 0x68, 4):
        first = data[offset:offset + 4]
        first_word = word(data, offset)
        first_original = (
            first_word & 0xFF00001F == 0x35000000
            and branch_target(offset, first_word, 19) == offset + 0x2C
        )
        if first != bytes.fromhex("0b000014") and not first_original:
            continue
        if (data[offset + 4:offset + 12] != bytes.fromhex("e80740b91f050071")
                or data[offset + 0x18:offset + 0x1C] != bytes.fromhex("e00313aa")
                or data[offset + 0x20:offset + 0x24] != bytes.fromhex("e00313aa")
                or data[offset + 0x28:offset + 0x2C] != bytes.fromhex("0d000014")
                or data[offset + 0x38:offset + 0x3C] != bytes.fromhex("80000036")
                or data[offset + 0x44:offset + 0x48] != bytes.fromhex("05000014")
                or data[offset + 0x4C:offset + 0x50] != bytes.fromhex("c0020035")):
            continue
        if (points_to(data, offset + 0x50, b"fastboot_unlock_verify pass")
                and points_to(data, offset + 0x5C, b"fastboot_unlock_verify ok")):
            candidates.append(offset)
    if len(candidates) != 1:
        raise PatchError(f"expected one fastboot authorization block, found {len(candidates)}")

    base = candidates[0]
    deltas = (0, 0xC, 0x2C, 0x30, 0x34, 0x48)
    originals = [word(data, base + delta) for delta in deltas]
    replacements = [
        bytes.fromhex("0b000014"), NOP, bytes.fromhex("e003002a"), NOP, NOP, NOP
    ]
    expected_patched = [0x1400000B, 0xD503201F, 0x2A0003E0,
                        0xD503201F, 0xD503201F, 0xD503201F]
    first_original = (
        originals[0] & 0xFF00001F == 0x35000000
        and branch_target(base, originals[0], 19) == base + 0x2C
    )
    reject_original = (
        originals[1] & 0xFF00001F == 0x54000000
        and branch_target(base + 0xC, originals[1], 19) == base + 0x2C
    )
    result_original = (
        originals[3] & 0xFFF8001F == 0x37000000
        and branch_target(base + 0x30, originals[3], 14) == base + 0x98
    )
    if originals[0] != expected_patched[0] and not first_original:
        raise PatchError("unexpected first fastboot authorization branch")
    if originals[1] != expected_patched[1] and not reject_original:
        raise PatchError("unexpected fastboot rejection branch")
    for index in (2, 4, 5):
        if originals[index] != expected_patched[index] and not is_bl(originals[index]):
            raise PatchError("unexpected fastboot verifier call")
    if originals[3] != expected_patched[3] and not result_original:
        raise PatchError("unexpected fastboot verifier result branch")

    purposes = (
        "force the fastboot authorization path",
        "skip conditional fastboot rejection",
        "skip the first fastboot verifier call",
        "ignore the verifier failure bit",
        "skip the secondary fastboot check",
        "skip fastboot_unlock_verify",
    )
    return [
        Patch(base + delta, original.to_bytes(4, "little"), replacement, purpose)
        for delta, original, replacement, purpose in zip(
            deltas, originals, replacements, purposes
        )
    ]


def locate_patches(lk_data: bytes) -> List[Patch]:
    for anchor in REQUIRED_ANCHORS:
        if lk_data.count(anchor) != 1:
            raise PatchError(
                f"expected one k6993 LK anchor {anchor!r}, "
                f"found {lk_data.count(anchor)}"
            )
    return locate_boot_gates(lk_data) + locate_fastboot_gates(lk_data)


def build_override(header_hash: bytes, image_hash: bytes,
                   signed_cert: bytes) -> bytes:
    block = (bytes.fromhex("a058") + HEADER_HASH_OID + HASH_BITS + header_hash
             + IMAGE_HASH_OID + HASH_BITS + image_hash)
    if len(block) != OVERRIDE_PREFIX_SIZE:
        raise AssertionError("bad cert2 override size")
    return block + signed_cert


def plan_and_patch(
    lk_data: bytes, patches: Sequence[Patch]
) -> tuple[bytes, List[Patch], List[Patch]]:
    changed: List[Patch] = []
    existing: List[Patch] = []
    output = bytearray(lk_data)
    for item in patches:
        actual = lk_data[item.offset:item.offset + 4]
        if actual == item.replacement:
            existing.append(item)
        elif actual == item.original:
            output[item.offset:item.offset + 4] = item.replacement
            changed.append(item)
        else:
            raise PatchError(
                f"LK+0x{item.offset:x}: expected {item.original.hex()} or "
                f"{item.replacement.hex()}, found {actual.hex()}"
            )
    return bytes(output), changed, existing


def patch_image(source: Path, output: Path | None, dry_run: bool) -> int:
    image = source.read_bytes()
    entries, trailing = parse_image(image)
    lk, cert2 = find_lk_and_cert(entries)
    signed_cert, cert_mode = original_cert2(cert2.data)
    validate_signed_input(lk, cert2.data, signed_cert, cert_mode)
    patches = locate_patches(lk.data)
    patched_lk, changed, existing = plan_and_patch(lk.data, patches)

    allowed_changes = {
        byte_offset
        for item in changed
        for byte_offset in range(item.offset, item.offset + len(item.replacement))
    }
    actual_changes = {
        offset for offset, (before, after) in enumerate(zip(lk.data, patched_lk))
        if before != after
    }
    if actual_changes - allowed_changes:
        raise PatchError("internal verification failed: LK diff exceeds the planned instructions")

    print(f"Input: {source}")
    print(f"  SHA-256: {hashlib.sha256(image).hexdigest()}")
    print(f"  LK size: {len(lk.data)} bytes; cert2: {cert_mode}")
    for item in changed:
        print(f"  PATCH LK+0x{item.offset:05x}: {item.original.hex()} -> "
              f"{item.replacement.hex()}  ({item.purpose})")
    for item in existing:
        print(f"  OK    LK+0x{item.offset:05x}: already patched")

    if not changed:
        print("Result: all eight basic bootloader patches are already present; no output written")
        return 0
    if dry_run:
        print(f"Result: dry-run passed; {len(changed)} instruction(s) would be patched")
        return 0
    if output is None:
        output = source.with_name(f"{source.stem}_patched{source.suffix}")
    if source.resolve() == output.resolve():
        raise PatchError("refusing to overwrite the input image")
    if output.exists():
        raise PatchError(f"output already exists: {output}")

    lk.data = patched_lk
    cert2.data = build_override(
        hashlib.sha256(bytes(lk.header)).digest(),
        padded_sha256(lk.data, 16),
        signed_cert,
    )
    rebuilt = b"".join(entry.encode() for entry in entries) + trailing

    # Parse the rebuilt image again and verify both patch bytes and advertised hashes.
    check_entries, _ = parse_image(rebuilt)
    check_lk, check_cert = find_lk_and_cert(check_entries)
    check_patches = locate_patches(check_lk.data)
    _, missing, present = plan_and_patch(check_lk.data, check_patches)
    advertised_headers = hashes_after_oid(check_cert.data[:OVERRIDE_PREFIX_SIZE], HEADER_HASH_OID)
    advertised_images = hashes_after_oid(check_cert.data[:OVERRIDE_PREFIX_SIZE], IMAGE_HASH_OID)
    if missing or len(present) != len(check_patches):
        raise PatchError("internal verification failed: patch set is incomplete")
    if (not advertised_headers
            or advertised_headers[0] != hashlib.sha256(bytes(check_lk.header)).digest()
            or not advertised_images
            or advertised_images[0] != padded_sha256(check_lk.data, 16)):
        raise PatchError("internal verification failed: cert2 override hash mismatch")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=output.parent, prefix=f".{output.name}.", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(rebuilt)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print(f"Output: {output}")
    print(f"  SHA-256: {hashlib.sha256(rebuilt).hexdigest()}")
    print(f"  cert2: override/bypass-valid ({len(check_cert.data)} bytes)")
    print("WARNING: this bypasses LK fastboot checks; it does not change seccfg unlock state.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Safely apply the eight basic Oppo Find X9/X9 Pro MT6993 "
            "LK fastboot patches."
        )
    )
    parser.add_argument("input", type=Path, help="stock or partially patched LK container")
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        help="new patched image path (default: <input>_patched.<ext>)",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="validate and show patches without writing an image")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return patch_image(args.input, args.output, args.dry_run)
    except (OSError, PatchError, struct.error, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
