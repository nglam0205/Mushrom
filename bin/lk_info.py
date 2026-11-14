#!/usr/bin/env python3
"""Inspect MediaTek LK images without third-party dependencies.

The script understands the multi-partition LK image format used by recent
MediaTek/Oplus bootloaders.  It reports partition metadata, current hashes,
hashes retained in cert2, lkpatcher-style certificate bypasses, build IDs,
and useful FDT/DTBO identifiers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


LK_MAGIC = 0x58881688
LK_EXT_MAGIC = 0x58891689
HEADER_SIZE = 512
DTB_MAGIC = 0xD00DFEED
DTBO_MAGIC = 0xD7B7AB1E

FDT_BEGIN_NODE = 1
FDT_END_NODE = 2
FDT_PROP = 3
FDT_NOP = 4
FDT_END = 9

IMAGE_HASH_OID = bytes.fromhex("060760867693160201")
HEADER_HASH_OID = bytes.fromhex("060760867693160204")
BIT_STRING_256_PREFIX = bytes.fromhex("032100")

BUILD_ID_RE = re.compile(rb"\b[0-9a-fA-F]{7,16}_20\d{10}\b")
SOC_RE = re.compile(rb"\b(?:MT|mt)\d{4}\b")
PLATFORM_RE = re.compile(rb"\bk\d{4}v\d+_64\b")
PANEL_RE = re.compile(rb"\b(?:oplus\d+|panel)_[A-Za-z0-9_,.-]{5,}\b")


class LkFormatError(ValueError):
    """Raised when a file is not a supported MediaTek LK image."""


def align_up(value: int, alignment: int) -> int:
    if alignment <= 0:
        return value
    return (value + alignment - 1) // alignment * alignment


def sha256_hex(data: bytes, alignment: int = 1) -> str:
    if alignment > 1:
        data += b"\0" * ((-len(data)) % alignment)
    return hashlib.sha256(data).hexdigest()


def unique(items: Iterable[Any]) -> List[Any]:
    result: List[Any] = []
    seen = set()
    for item in items:
        marker = tuple(item) if isinstance(item, list) else item
        if marker not in seen:
            seen.add(marker)
            result.append(item)
    return result


def ascii_matches(pattern: re.Pattern[bytes], data: bytes) -> List[str]:
    return unique(match.group().decode("ascii", "replace") for match in pattern.finditer(data))


def parse_build_info(build_ids: Sequence[str]) -> List[Dict[str, str]]:
    """Split ``commit_YYYYMMDDhhmm`` identifiers into readable fields."""
    builds: List[Dict[str, str]] = []
    for raw in build_ids:
        commit, separator, timestamp = raw.rpartition("_")
        item = {"raw": raw, "id": commit if separator else raw}
        if separator and len(timestamp) == 12 and timestamp.isdigit():
            try:
                parsed = datetime.strptime(timestamp, "%Y%m%d%H%M")
                item["time"] = parsed.strftime("%Y-%m-%d %H:%M")
                item["timestamp"] = timestamp
            except ValueError:
                pass
        builds.append(item)
    return builds


@dataclass
class Header:
    raw: bytes
    offset: int
    magic: int
    data_size: int
    name: str
    memory_address: int
    mode: int
    extended: bool
    header_size: int
    header_version: int
    image_list_end: int
    alignment: int

    @classmethod
    def parse(cls, image: bytes, offset: int) -> "Header":
        if offset < 0 or offset + HEADER_SIZE > len(image):
            raise LkFormatError(f"truncated header at 0x{offset:x}")

        raw = image[offset : offset + HEADER_SIZE]
        magic, size_low = struct.unpack_from("<II", raw, 0)
        if magic != LK_MAGIC:
            raise LkFormatError(f"bad LK magic 0x{magic:08x} at 0x{offset:x}")

        name_bytes = raw[8:40].split(b"\0", 1)[0]
        try:
            name = name_bytes.decode("ascii")
        except UnicodeDecodeError as exc:
            raise LkFormatError(f"invalid partition name at 0x{offset:x}") from exc

        maddr_low, mode, ext_magic, hdr_size, hdr_version = struct.unpack_from(
            "<IIIII", raw, 40
        )
        image_list_end, alignment, size_high, maddr_high = struct.unpack_from(
            "<IIII", raw, 64
        )
        extended = ext_magic == LK_EXT_MAGIC
        data_size = (size_high << 32 | size_low) if extended else size_low
        memory_address = (maddr_high << 32 | maddr_low) if extended else maddr_low
        actual_header_size = hdr_size if extended and hdr_size else HEADER_SIZE

        if actual_header_size < 80 or actual_header_size > 0x10000:
            raise LkFormatError(
                f"unreasonable header size {actual_header_size} at 0x{offset:x}"
            )
        if data_size > len(image):
            raise LkFormatError(
                f"unreasonable data size {data_size} for {name!r} at 0x{offset:x}"
            )

        return cls(
            raw=raw,
            offset=offset,
            magic=magic,
            data_size=data_size,
            name=name,
            memory_address=memory_address,
            mode=mode,
            extended=extended,
            header_size=actual_header_size,
            header_version=hdr_version,
            image_list_end=image_list_end,
            alignment=alignment if extended else 8,
        )


@dataclass
class Partition:
    header: Header
    data: bytes
    end_offset: int
    certificates: List["Partition"] = field(default_factory=list)

    @property
    def cert1(self) -> Optional["Partition"]:
        return next((p for p in self.certificates if p.header.name.startswith("cert1")), None)

    @property
    def cert2(self) -> Optional["Partition"]:
        return next((p for p in self.certificates if p.header.name.startswith("cert2")), None)

    @property
    def header_hash(self) -> str:
        return sha256_hex(self.header.raw)

    @property
    def data_hash(self) -> str:
        # MediaTek cert2 hashes partition data padded to a 16-byte boundary.
        return sha256_hex(self.data, 16)


def parse_partitions(image: bytes) -> Tuple[List[Partition], int]:
    offset = 0
    partitions: List[Partition] = []
    last_partition: Optional[Partition] = None

    while offset + HEADER_SIZE <= len(image):
        if struct.unpack_from("<I", image, offset)[0] != LK_MAGIC:
            break

        header = Header.parse(image, offset)
        data_start = offset + header.header_size
        data_end = data_start + header.data_size
        if data_end > len(image):
            raise LkFormatError(
                f"partition {header.name!r} extends beyond the end of the file"
            )
        end_offset = align_up(data_end, header.alignment)
        part = Partition(header, image[data_start:data_end], end_offset)

        if header.name.startswith("cert"):
            if last_partition is None:
                raise LkFormatError("certificate appears before a data partition")
            last_partition.certificates.append(part)
        else:
            if any(existing.header.name == header.name for existing in partitions):
                raise LkFormatError(f"duplicate partition name: {header.name}")
            partitions.append(part)
            last_partition = part

        offset = end_offset
        if header.image_list_end == 1:
            break

    if not partitions:
        raise LkFormatError("no MediaTek LK partitions found")
    version = 2 if any(p.header.name in {"aee", "bl2_ext"} for p in partitions) else 1
    return partitions, version


def hashes_after_oid(data: bytes, oid: bytes) -> List[str]:
    hashes: List[str] = []
    start = 0
    while True:
        pos = data.find(oid, start)
        if pos < 0:
            break
        value_pos = pos + len(oid)
        if data[value_pos : value_pos + 3] == BIT_STRING_256_PREFIX:
            digest = data[value_pos + 3 : value_pos + 35]
            if len(digest) == 32:
                hashes.append(digest.hex())
        start = pos + 1
    return hashes


def analyze_cert2(partition: Partition) -> Optional[Dict[str, Any]]:
    cert2 = partition.cert2
    if cert2 is None:
        return None

    data = cert2.data
    header_hashes = hashes_after_oid(data, HEADER_HASH_OID)
    image_hashes = hashes_after_oid(data, IMAGE_HASH_OID)
    current_header = partition.header_hash
    current_image = partition.data_hash

    mode = "standard"
    advertised_header: Optional[str] = None
    advertised_image: Optional[str] = None
    signed_header: Optional[str] = header_hashes[-1] if header_hashes else None
    signed_image: Optional[str] = image_hashes[-1] if image_hashes else None

    if data.startswith(b"\xa0") and len(header_hashes) >= 2 and len(image_hashes) >= 2:
        mode = "override"
        advertised_header, advertised_image = header_hashes[0], image_hashes[0]
        signed_header, signed_image = header_hashes[-1], image_hashes[-1]
    elif data.startswith(b"\x03") and len(header_hashes) >= 2 and len(image_hashes) >= 2:
        mode = "wrap"
        signed_header, signed_image = header_hashes[0], image_hashes[0]
        advertised_header, advertised_image = header_hashes[-1], image_hashes[-1]

    if mode in {"override", "wrap"}:
        advertised_matches = (
            advertised_header == current_header and advertised_image == current_image
        )
        status = "bypass-valid" if advertised_matches else "bypass-mismatch"
    elif signed_header is None or signed_image is None:
        status = "unparsed"
    elif signed_header == current_header and signed_image == current_image:
        status = "valid"
    else:
        status = "modified-or-mismatched"

    return {
        "length": len(data),
        "mode": mode,
        "status": status,
        "advertised_header_sha256": advertised_header,
        "advertised_data_sha256": advertised_image,
        "signed_header_sha256": signed_header,
        "signed_data_sha256": signed_image,
        "stock_data_sha256_candidate": (
            signed_image if mode in {"override", "wrap"} and signed_image != current_image else None
        ),
    }


def detect_lk_load_address(data: bytes, header_address: int) -> Optional[int]:
    if (header_address & 0xFFFFFFFF) != 0xFFFFFFFF:
        return header_address

    # AArch64 v2: ADR-like marker, literal LDR, then SUB pattern.
    marker = bytes.fromhex("0f000010")
    marker_after = bytes.fromhex("2f010fcb")
    start = 0
    while start < min(0x400, max(0, len(data) - 12)):
        pos = data.find(marker, start, 0x400)
        if pos < 0:
            break
        if data[pos + 8 : pos + 12] == marker_after:
            insn = struct.unpack_from("<I", data, pos + 4)[0]
            if (insn & 0xFF000000) == 0x58000000:
                imm19 = (insn >> 5) & 0x7FFFF
                if imm19 & (1 << 18):
                    imm19 -= 1 << 19
                literal = pos + 4 + imm19 * 4
                if 0 <= literal <= len(data) - 8:
                    address = struct.unpack_from("<Q", data, literal)[0]
                    if address >> 48 == 0xFFFF:
                        return address - pos
        start = pos + 4
    return None


def c_string(data: bytes) -> str:
    return data.split(b"\0", 1)[0].decode("utf-8", "replace")


def string_list(data: bytes) -> List[str]:
    return [chunk.decode("utf-8", "replace") for chunk in data.rstrip(b"\0").split(b"\0") if chunk]


def parse_fdt_properties(blob: bytes) -> List[Tuple[str, str, bytes]]:
    if len(blob) < 40 or struct.unpack_from(">I", blob, 0)[0] != DTB_MAGIC:
        return []
    (
        _magic,
        total_size,
        struct_off,
        strings_off,
        _reserve_off,
        _version,
        _last_version,
        _boot_cpu,
        strings_size,
        struct_size,
    ) = struct.unpack_from(">10I", blob, 0)
    if total_size > len(blob) or struct_off + struct_size > len(blob):
        return []
    if strings_off + strings_size > len(blob):
        return []

    strings = blob[strings_off : strings_off + strings_size]
    pos = struct_off
    end = struct_off + struct_size
    stack: List[str] = []
    properties: List[Tuple[str, str, bytes]] = []

    while pos + 4 <= end:
        token = struct.unpack_from(">I", blob, pos)[0]
        pos += 4
        if token == FDT_BEGIN_NODE:
            nul = blob.find(b"\0", pos, end)
            if nul < 0:
                break
            stack.append(blob[pos:nul].decode("utf-8", "replace"))
            pos = align_up(nul + 1, 4)
        elif token == FDT_END_NODE:
            if stack:
                stack.pop()
        elif token == FDT_PROP:
            if pos + 8 > end:
                break
            length, name_off = struct.unpack_from(">II", blob, pos)
            pos += 8
            if pos + length > end or name_off >= len(strings):
                break
            name_end = strings.find(b"\0", name_off)
            if name_end < 0:
                break
            name = strings[name_off:name_end].decode("utf-8", "replace")
            value = blob[pos : pos + length]
            path = "/" + "/".join(part for part in stack if part)
            properties.append((path or "/", name, value))
            pos = align_up(pos + length, 4)
        elif token == FDT_NOP:
            continue
        elif token == FDT_END:
            break
        else:
            break
    return properties


def parse_dtb_summary(blob: bytes) -> Dict[str, Any]:
    props = parse_fdt_properties(blob)
    root = {(name): value for path, name, value in props if path == "/"}
    return {
        "model": c_string(root["model"]) if "model" in root else None,
        "compatible": string_list(root["compatible"]) if "compatible" in root else [],
        "total_size": struct.unpack_from(">I", blob, 4)[0] if len(blob) >= 8 else None,
    }


def embedded_dtbs(data: bytes) -> Tuple[List[bytes], Optional[Dict[str, Any]]]:
    if len(data) >= 32 and struct.unpack_from(">I", data, 0)[0] == DTBO_MAGIC:
        magic, total, header_size, entry_size, count, entries_off, page_size, version = struct.unpack_from(
            ">8I", data, 0
        )
        dtbs: List[bytes] = []
        entries: List[Dict[str, int]] = []
        if header_size < 32 or entry_size < 32 or count > 4096:
            return [], None
        for index in range(count):
            pos = entries_off + index * entry_size
            if pos + 32 > len(data):
                break
            size, offset, ident, revision, c0, c1, c2, c3 = struct.unpack_from(">8I", data, pos)
            if offset + size > len(data):
                break
            dtbs.append(data[offset : offset + size])
            entries.append({
                "index": index,
                "size": size,
                "offset": offset,
                "id": ident,
                "revision": revision,
                "custom0": c0,
                "custom1": c1,
                "custom2": c2,
                "custom3": c3,
            })
        return dtbs, {
            "magic": f"0x{magic:08x}",
            "total_size": total,
            "entry_count": count,
            "page_size": page_size,
            "version": version,
            "entries": entries,
        }
    if len(data) >= 8 and struct.unpack_from(">I", data, 0)[0] == DTB_MAGIC:
        size = struct.unpack_from(">I", data, 4)[0]
        return [data[:size]], None
    return [], None


def analyze_device_trees(partitions: Sequence[Partition]) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "main_dtb": None,
        "dtbo": None,
        "project_ids": [],
        "project_codenames": [],
        "panel_commands": [],
    }
    project_ids: List[int] = []
    codenames: List[str] = []
    panel_commands: List[str] = []

    for partition in partitions:
        dtbs, container = embedded_dtbs(partition.data)
        if not dtbs:
            continue
        if partition.header.name == "lk_main_dtb":
            result["main_dtb"] = parse_dtb_summary(dtbs[0])
        if container is not None:
            result["dtbo"] = container

        for dtb in dtbs:
            for _path, name, value in parse_fdt_properties(dtb):
                if name in {"platform_support_project", "match-projects"} and len(value) % 4 == 0:
                    project_ids.extend(struct.unpack(f">{len(value) // 4}I", value))
                elif name == "hbp,project":
                    codenames.extend(string_list(value))
                elif name == "platform_support_project_commandline":
                    panel_commands.extend(string_list(value))

    result["project_ids"] = unique(project_ids)
    result["project_codenames"] = unique(codenames)
    result["panel_commands"] = unique(panel_commands)
    return result


def partition_dict(partition: Partition) -> Dict[str, Any]:
    cert = analyze_cert2(partition)
    return {
        "name": partition.header.name,
        "header_offset": partition.header.offset,
        "data_size": len(partition.data),
        "end_offset": partition.end_offset,
        "memory_address": f"0x{partition.header.memory_address:x}",
        "mode": f"0x{partition.header.mode:08x}",
        "header_version": partition.header.header_version,
        "alignment": partition.header.alignment,
        "sha256": sha256_hex(partition.data),
        "certificate_sha256": partition.data_hash,
        "cert1_length": len(partition.cert1.data) if partition.cert1 else None,
        "cert2": cert,
    }


def inspect_image(path: Path) -> Dict[str, Any]:
    data = path.read_bytes()
    partitions, version = parse_partitions(data)
    lk = next((part for part in partitions if part.header.name == "lk"), None)
    searchable = lk.data if lk else data
    load_address = (
        detect_lk_load_address(lk.data, lk.header.memory_address) if lk else None
    )

    build_ids = ascii_matches(BUILD_ID_RE, searchable)
    return {
        "file": str(path),
        "file_size": len(data),
        "file_sha256": sha256_hex(data),
        "image_version": version,
        "architecture": "AArch64" if PLATFORM_RE.search(searchable) else "unknown",
        "lk_load_address": f"0x{load_address:x}" if load_address is not None else None,
        "build_ids": build_ids,
        "build_info": parse_build_info(build_ids),
        "soc_ids": ascii_matches(SOC_RE, searchable),
        "platform_ids": ascii_matches(PLATFORM_RE, searchable),
        "panel_identifiers": ascii_matches(PANEL_RE, data),
        "partitions": [partition_dict(part) for part in partitions],
        "device_tree": analyze_device_trees(partitions),
    }


def extract_partitions(report: Dict[str, Any], source: Path, output_root: Path) -> None:
    data = source.read_bytes()
    partitions, _version = parse_partitions(data)
    target = output_root / source.stem
    target.mkdir(parents=True, exist_ok=True)
    for partition in partitions:
        (target / f"{partition.header.name}.bin").write_bytes(partition.data)


def short_hash(value: Optional[str]) -> str:
    return value if value is not None else "-"


def print_text(report: Dict[str, Any]) -> None:
    print(f"File: {report['file']}")
    print(f"  Size: {report['file_size']} bytes")
    print(f"  SHA-256: {report['file_sha256']}")
    print(f"  LK image version: {report['image_version']}")
    print(f"  Architecture: {report['architecture']}")
    print(f"  LK load address: {report['lk_load_address'] or '-'}")
    if report["build_info"]:
        for index, build in enumerate(report["build_info"]):
            label = "Build" if index == 0 else f"Build {index + 1}"
            print(f"  {label} string: {build['raw']}")
            print(f"  {label} ID: {build['id']}")
            print(f"  {label} time: {build.get('time', '-')}")
    else:
        print("  Build string: -")
        print("  Build ID: -")
        print("  Build time: -")
    print(f"  SoC: {', '.join(report['soc_ids']) or '-'}")
    print(f"  Platform: {', '.join(report['platform_ids']) or '-'}")

    print("  Partitions:")
    for part in report["partitions"]:
        cert = part["cert2"]
        cert_text = "no cert2"
        if cert:
            cert_text = f"cert2={cert['length']}B {cert['mode']}/{cert['status']}"
        print(
            f"    {part['name']:<12} size={part['data_size']:<9} "
            f"offset=0x{part['header_offset']:x} sha256={part['sha256']} {cert_text}"
        )
        if cert and cert.get("stock_data_sha256_candidate"):
            print(
                " " * 16
                + "stock SHA-256 candidate: "
                + short_hash(cert["stock_data_sha256_candidate"])
            )

    tree = report["device_tree"]
    if tree.get("main_dtb"):
        main = tree["main_dtb"]
        print(f"  Main DTB model: {main.get('model') or '-'}")
        print(f"  Main DTB compatible: {', '.join(main.get('compatible', [])) or '-'}")
    if tree.get("dtbo"):
        print(f"  DTBO entries: {tree['dtbo']['entry_count']}")
    if tree.get("project_ids"):
        formatted = ", ".join(
            f"{value} (0x{value:x})" for value in tree["project_ids"]
        )
        print(f"  Oplus project IDs: {formatted}")
    if tree.get("project_codenames"):
        print(f"  Project codenames: {', '.join(tree['project_codenames'])}")
    if tree.get("panel_commands"):
        print(f"  Panel commands: {', '.join(tree['panel_commands'])}")
    if report.get("panel_identifiers"):
        print(f"  Panel identifiers: {', '.join(report['panel_identifiers'])}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract identifying and certificate information from MediaTek LK images."
    )
    parser.add_argument("images", nargs="+", type=Path, help="LK image(s) to inspect")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument(
        "--extract-dir",
        type=Path,
        help="optionally extract data partitions under this directory",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    reports: List[Dict[str, Any]] = []
    failed = False

    for path in args.images:
        try:
            report = inspect_image(path)
            reports.append(report)
            if args.extract_dir:
                extract_partitions(report, path, args.extract_dir)
        except (OSError, LkFormatError, struct.error) as exc:
            failed = True
            print(f"error: {path}: {exc}", file=sys.stderr)

    if args.json:
        print(json.dumps(reports, indent=2, ensure_ascii=False))
    else:
        for index, report in enumerate(reports):
            if index:
                print()
            print_text(report)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
