#!/usr/bin/env python3
"""Allow Android init to update existing ro.* system properties.

The patch is deliberately fail-closed.  It identifies PropertySet from its
"ro." prefix check and the AOSP read-only error string, validates the AArch64
control flow, and changes only the false-condition TBZ into an unconditional
branch to the normal property update path.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


ELF_MAGIC = b"\x7fELF"
EM_AARCH64 = 183
PT_LOAD = 1
PF_X = 1

RO_PREFIX = b"ro.\0"
READ_ONLY_ERROR = b"Read-only property was already set\0"


class PatchError(ValueError):
    pass


@dataclass(frozen=True)
class Segment:
    offset: int
    vaddr: int
    filesz: int
    flags: int

    def contains_offset(self, offset: int) -> bool:
        return self.offset <= offset < self.offset + self.filesz

    def contains_vaddr(self, vaddr: int) -> bool:
        return self.vaddr <= vaddr < self.vaddr + self.filesz

    def offset_to_vaddr(self, offset: int) -> int:
        return self.vaddr + offset - self.offset

    def vaddr_to_offset(self, vaddr: int) -> int:
        return self.offset + vaddr - self.vaddr


@dataclass(frozen=True)
class Candidate:
    branch_offset: int
    branch_vaddr: int
    target_vaddr: int
    original: bytes
    replacement: bytes
    state: str


def sign_extend(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return (value ^ sign) - sign


def read_u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise PatchError("instruction is outside the ELF file")
    return struct.unpack_from("<I", data, offset)[0]


def parse_elf(data: bytes) -> list[Segment]:
    if len(data) < 64 or data[:4] != ELF_MAGIC:
        raise PatchError("input is not an ELF binary")
    if data[4] != 2 or data[5] != 1:
        raise PatchError("only little-endian ELF64 binaries are supported")
    if struct.unpack_from("<H", data, 18)[0] != EM_AARCH64:
        raise PatchError("only AArch64 init binaries are supported")

    phoff = struct.unpack_from("<Q", data, 32)[0]
    phentsize = struct.unpack_from("<H", data, 54)[0]
    phnum = struct.unpack_from("<H", data, 56)[0]
    if phentsize < 56 or phnum == 0:
        raise PatchError("ELF has no usable program headers")

    segments: list[Segment] = []
    for index in range(phnum):
        offset = phoff + index * phentsize
        if offset + 56 > len(data):
            raise PatchError("truncated ELF program headers")
        p_type, flags, file_offset, vaddr, _, filesz, _, _ = struct.unpack_from(
            "<IIQQQQQQ", data, offset
        )
        if p_type != PT_LOAD or filesz == 0:
            continue
        if file_offset + filesz > len(data):
            raise PatchError("ELF load segment extends beyond the file")
        segments.append(Segment(file_offset, vaddr, filesz, flags))
    if not segments or not any(segment.flags & PF_X for segment in segments):
        raise PatchError("ELF has no executable load segment")
    return segments


def string_vaddrs(data: bytes, segments: Sequence[Segment], value: bytes) -> set[int]:
    offsets: list[int] = []
    start = 0
    while True:
        offset = data.find(value, start)
        if offset < 0:
            break
        offsets.append(offset)
        start = offset + 1
    mapped = {
        segment.offset_to_vaddr(offset)
        for offset in offsets
        for segment in segments
        if segment.contains_offset(offset)
    }
    if not mapped:
        label = value.rstrip(b"\0").decode("ascii", "replace")
        raise PatchError(f"mapped {label!r} string not found")
    return mapped


def decode_adrp_add(data: bytes, segment: Segment, offset: int) -> int | None:
    if offset + 8 > segment.offset + segment.filesz:
        return None
    adrp = read_u32(data, offset)
    add = read_u32(data, offset + 4)
    if adrp & 0x9F000000 != 0x90000000:
        return None
    if add & 0xFF000000 != 0x91000000:
        return None
    register = adrp & 0x1F
    if (add & 0x1F) != register or ((add >> 5) & 0x1F) != register:
        return None

    imm21 = ((adrp >> 5) & 0x7FFFF) << 2 | ((adrp >> 29) & 0x3)
    page_delta = sign_extend(imm21, 21) << 12
    pc = segment.offset_to_vaddr(offset)
    immediate = (add >> 10) & 0xFFF
    if add & (1 << 22):
        immediate <<= 12
    return (pc & ~0xFFF) + page_delta + immediate


def decode_tbz_w0_bit0(word: int, pc: int) -> int | None:
    if word & 0x7F00001F != 0x36000000:
        return None
    bit = ((word >> 31) & 1) << 5 | ((word >> 19) & 0x1F)
    if bit != 0:
        return None
    immediate = sign_extend((word >> 5) & 0x3FFF, 14) << 2
    return pc + immediate


def decode_b(word: int, pc: int) -> int | None:
    if word & 0xFC000000 != 0x14000000:
        return None
    return pc + (sign_extend(word & 0x03FFFFFF, 26) << 2)


def encode_b(pc: int, target: int) -> bytes:
    delta = target - pc
    if delta % 4:
        raise PatchError("unaligned branch target")
    immediate = delta // 4
    if not -(1 << 25) <= immediate < (1 << 25):
        raise PatchError("branch target is out of range")
    return struct.pack("<I", 0x14000000 | (immediate & 0x03FFFFFF))


def instruction_offsets(segment: Segment) -> Iterable[int]:
    start = (segment.offset + 3) & ~3
    return range(start, segment.offset + segment.filesz - 7, 4)


def find_candidates(data: bytes, segments: Sequence[Segment]) -> list[Candidate]:
    ro_vaddrs = string_vaddrs(data, segments, RO_PREFIX)
    error_vaddrs = string_vaddrs(data, segments, READ_ONLY_ERROR)
    candidates: list[Candidate] = []

    for segment in segments:
        if not segment.flags & PF_X:
            continue
        for ro_offset in instruction_offsets(segment):
            if decode_adrp_add(data, segment, ro_offset) not in ro_vaddrs:
                continue

            # Clang emits the StartsWith call and its boolean branch shortly
            # after loading "ro.".  The rejected path immediately loads the
            # exact AOSP error string.
            scan_end = min(ro_offset + 0x50, segment.offset + segment.filesz - 8)
            for branch_offset in range(ro_offset + 8, scan_end + 1, 4):
                if decode_adrp_add(data, segment, branch_offset + 4) not in error_vaddrs:
                    continue
                branch_word = read_u32(data, branch_offset)
                pc = segment.offset_to_vaddr(branch_offset)
                target = decode_tbz_w0_bit0(branch_word, pc)
                state = "original"
                if target is None:
                    target = decode_b(branch_word, pc)
                    state = "patched"
                if target is None or target <= pc or target - pc > 0x1000:
                    continue
                if not segment.contains_vaddr(target):
                    continue
                replacement = encode_b(pc, target)
                candidates.append(
                    Candidate(
                        branch_offset,
                        pc,
                        target,
                        data[branch_offset:branch_offset + 4],
                        replacement,
                        state,
                    )
                )
    return candidates


def plan_patch(data: bytes) -> tuple[bytes, Candidate]:
    segments = parse_elf(data)
    candidates = find_candidates(data, segments)
    if len(candidates) != 1:
        raise PatchError(f"expected one validated ro.* guard, found {len(candidates)}")
    candidate = candidates[0]
    if candidate.state == "patched":
        return data, candidate
    output = bytearray(data)
    output[candidate.branch_offset:candidate.branch_offset + 4] = candidate.replacement
    return bytes(output), candidate


def patch_file(source: Path, output: Path | None, dry_run: bool) -> int:
    data = source.read_bytes()
    patched, candidate = plan_patch(data)
    print(f"Input: {source}")
    print(f"  SHA-256: {hashlib.sha256(data).hexdigest()}")
    print(f"  guard VA: 0x{candidate.branch_vaddr:x}; update path: 0x{candidate.target_vaddr:x}")

    if candidate.state == "patched":
        print("Result: init already permits updates to existing ro.* properties; no output written")
        return 0
    print(
        f"  PATCH file+0x{candidate.branch_offset:x}: "
        f"{candidate.original.hex()} -> {candidate.replacement.hex()}"
    )
    if dry_run:
        print("Result: dry-run passed; one instruction would be patched")
        return 0
    if output is None:
        output = source.with_name(f"{source.name}_ro_patched")
    if source.resolve() == output.resolve():
        raise PatchError("refusing to overwrite the input binary")
    if output.exists():
        raise PatchError(f"output already exists: {output}")

    # Re-run structural validation on the generated binary before writing it.
    verified, check = plan_patch(patched)
    if verified != patched or check.state != "patched":
        raise PatchError("internal verification failed")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(patched)
    os.chmod(output, source.stat().st_mode & 0o7777)
    print(f"Output: {output}")
    print(f"  SHA-256: {hashlib.sha256(patched).hexdigest()}")
    print("WARNING: SELinux property permissions and framework property caches still apply.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Patch AArch64 Android init to update existing ro.* properties."
    )
    parser.add_argument("input", type=Path, help="stock or already-patched init ELF")
    parser.add_argument("output", type=Path, nargs="?", help="new patched init path")
    parser.add_argument("--dry-run", action="store_true", help="validate without writing")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return patch_file(args.input, args.output, args.dry_run)
    except (OSError, PatchError, struct.error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
