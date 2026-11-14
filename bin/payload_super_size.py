#!/usr/bin/env python3
"""Read the super partition size (dynamic groups + reserve) from an Android OTA payload.

The result must match the real partition exactly: libsnapshot refuses to write
the target slot metadata of an OTA when the super size in the metadata differs.
--reserve-mib N overrides the 4 MiB default reserve (OPlus); Xiaomi uses 10.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Manifest decoding is shared with ota_payload.py, which reads the protobuf
# wire format directly: no protobuf package is needed (CI runners lack it).
from ota_payload import parse_manifest, read_manifest


MIB = 1024 * 1024


def read_super_size(payload_path: Path, reserve: int = 4 * MIB) -> tuple[int, int, int]:
    manifest = parse_manifest(read_manifest(payload_path))
    group_sizes = [
        int(group["size"])
        for group in manifest["dynamic"]["groups"]
        if int(group["size"]) > 0
    ]
    if not group_sizes:
        raise ValueError("dynamic partition group size not found")

    group_total = sum(group_sizes)
    super_size = group_total + reserve
    if super_size % 4096 != 0:
        raise ValueError(f"derived super size is not 4096-byte aligned: {super_size}")

    return super_size, group_total, len(group_sizes)


def main() -> int:
    args = sys.argv[1:]
    reserve = 4 * MIB
    if len(args) == 3 and args[0] == "--reserve-mib" and args[1].isdigit():
        reserve = int(args[1]) * MIB
        args = args[2:]
    if len(args) != 1:
        print(f"Usage: {Path(sys.argv[0]).name} [--reserve-mib N] <payload.bin>", file=sys.stderr)
        return 2

    payload_path = Path(args[0])
    if not payload_path.is_file():
        print(f"payload_super_size: file not found: {payload_path}", file=sys.stderr)
        return 1

    try:
        super_size, _, _ = read_super_size(payload_path, reserve)
    except (IndexError, OSError, ValueError) as exc:
        print(f"payload_super_size: {exc}", file=sys.stderr)
        return 1

    print(super_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
