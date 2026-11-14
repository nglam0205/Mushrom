#!/usr/bin/env python3
"""Adds <uses-permission> entries to a binary AndroidManifest.xml in place.

For APKs decoded without resources (apktool -r), where the manifest stays
binary and a full resource rebuild is not an option. Nothing else in the file
is re-encoded: the new strings go to the end of the string pool, so no existing
index moves, and each entry is a copy of an existing <uses-permission> element
pointing at its new name.

Usage: axml_add_permissions.py <AndroidManifest.xml> <permission>...
"""

import struct
import sys
from pathlib import Path

RES_XML = 0x0003
RES_STRING_POOL = 0x0001
RES_XML_START_ELEMENT = 0x0102
RES_XML_END_ELEMENT = 0x0103
UTF8_FLAG = 0x100
TYPE_STRING = 0x03


def chunks(data: bytes, start: int):
    offset = start
    while offset < len(data):
        kind, header_size, size = struct.unpack_from("<HHI", data, offset)
        if size < 8:
            raise ValueError("corrupt chunk")
        yield offset, kind, header_size, size
        offset += size


class StringPool:
    def __init__(self, chunk: bytes):
        (_, self.header_size, _, count, styles, self.flags,
         strings_start, styles_start) = struct.unpack_from("<HHIIIIII", chunk, 0)
        if styles or styles_start:
            raise ValueError("string pool with styles is not supported")
        self.header = bytearray(chunk[:self.header_size])
        self.offsets = list(struct.unpack_from(f"<{count}I", chunk, self.header_size))
        self.data = bytearray(chunk[strings_start:])
        self.utf8 = bool(self.flags & UTF8_FLAG)
        self.strings = [self._read(offset) for offset in self.offsets]

    def _length(self, offset: int, unit: int) -> tuple[int, int]:
        """(value, bytes used) of a 1- or 2-unit length prefix."""
        fmt, high = ("<B", 0x80) if unit == 1 else ("<H", 0x8000)
        first = struct.unpack_from(fmt, self.data, offset)[0]
        if not first & high:
            return first, unit
        second = struct.unpack_from(fmt, self.data, offset + unit)[0]
        return ((first & (high - 1)) << (8 * unit)) | second, 2 * unit

    def _read(self, offset: int) -> str:
        if self.utf8:
            _, used = self._length(offset, 1)
            size, used2 = self._length(offset + used, 1)
            begin = offset + used + used2
            return self.data[begin:begin + size].decode("utf-8", "replace")
        size, used = self._length(offset, 2)
        begin = offset + used
        return self.data[begin:begin + 2 * size].decode("utf-16-le", "replace")

    def add(self, text: str) -> int:
        if text in self.strings:
            return self.strings.index(text)
        if len(text) >= 0x80:
            raise ValueError("string too long")
        # Drop the padding of the previous end so the new string starts aligned
        # with the data it follows.
        last = max(self.offsets, default=0)
        end = last + self._encoded_size(last) if self.offsets else 0
        del self.data[end:]
        self.offsets.append(len(self.data))
        if self.utf8:
            raw = text.encode("utf-8")
            self.data += bytes((len(text), len(raw))) + raw + b"\0"
        else:
            self.data += struct.pack("<H", len(text)) + text.encode("utf-16-le") + b"\0\0"
        self.strings.append(text)
        return len(self.strings) - 1

    def _encoded_size(self, offset: int) -> int:
        if self.utf8:
            _, used = self._length(offset, 1)
            size, used2 = self._length(offset + used, 1)
            return used + used2 + size + 1
        size, used = self._length(offset, 2)
        return used + 2 * size + 2

    def pack(self) -> bytes:
        offsets = struct.pack(f"<{len(self.offsets)}I", *self.offsets)
        data = bytes(self.data) + b"\0" * (-len(self.data) % 4)
        strings_start = self.header_size + len(offsets)
        struct.pack_into("<I", self.header, 4, strings_start + len(data))
        struct.pack_into("<I", self.header, 8, len(self.offsets))
        struct.pack_into("<I", self.header, 20, strings_start)
        return bytes(self.header) + offsets + data


def add_permissions(data: bytes, permissions: list[str]) -> tuple[bytes, list[str]]:
    kind, header_size, _ = struct.unpack_from("<HHI", data, 0)
    if kind != RES_XML:
        raise ValueError("not a binary XML file")
    layout = list(chunks(data, header_size))
    pool_offset, pool_kind, _, pool_size = layout[0]
    if pool_kind != RES_STRING_POOL:
        raise ValueError("string pool not found")
    pool = StringPool(data[pool_offset:pool_offset + pool_size])

    # An existing <uses-permission android:name="..."/> to copy.
    template = None
    declared = set()
    for index, (offset, chunk_kind, _, size) in enumerate(layout):
        if chunk_kind != RES_XML_START_ELEMENT:
            continue
        name, _, _, attr_count = struct.unpack_from("<IHHH", data, offset + 20)
        if pool.strings[name] != "uses-permission" or attr_count != 1:
            continue
        raw_value = struct.unpack_from("<I", data, offset + 36 + 8)[0]
        declared.add(pool.strings[raw_value])
        end_offset, end_kind, _, end_size = layout[index + 1]
        if template is None and end_kind == RES_XML_END_ELEMENT:
            template = (offset, data[offset:offset + size], data[end_offset:end_offset + end_size])
    if template is None:
        raise ValueError("no <uses-permission> element to copy")

    added = []
    elements = b""
    for permission in permissions:
        if permission in declared:
            continue
        start = bytearray(template[1])
        index = pool.add(permission)
        struct.pack_into("<I", start, 36 + 8, index)        # raw value
        struct.pack_into("<HBBI", start, 36 + 12, 8, 0, TYPE_STRING, index)
        elements += bytes(start) + template[2]
        declared.add(permission)
        added.append(permission)
    if not added:
        return data, added

    body = (pool.pack() + data[pool_offset + pool_size:template[0]]
            + elements + data[template[0]:])
    header = bytearray(data[:header_size])
    struct.pack_into("<I", header, 4, header_size + len(body))
    return bytes(header) + body, added


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    try:
        patched, added = add_permissions(path.read_bytes(), sys.argv[2:])
    except (ValueError, struct.error, IndexError) as error:
        print(f"[axml_add_permissions] {path}: {error}", file=sys.stderr)
        return 1
    if added:
        path.write_bytes(patched)
    print(f"[axml_add_permissions] {path}: added {len(added)}"
          + (f" ({', '.join(added)})" if added else ", all declared already"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
