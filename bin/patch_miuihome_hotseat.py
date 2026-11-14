#!/usr/bin/env python3
"""Points one default dock (hotseat) slot of the HyperOS launcher at another app.

Edits the binary res/xml/layout_hotseats.xml of an APK decoded without
resources (apktool -r). Every phone default_workspace*.xml includes that file,
and its third slot is com.android.browser, which global ROMs do not ship, so
the slot stays empty. Only the packageName/className values of the matching
<favorite container="-101" x="SLOT"> change; the new strings go to the end of
the string pool, so no existing index moves.

Usage: patch_miuihome_hotseat.py <layout_hotseats.xml> <slot> <package> <class>
Prints 1 when the file changed, 0 when the slot already pointed there.
"""

import struct
import sys
from pathlib import Path

from axml_add_permissions import (RES_STRING_POOL, RES_XML, RES_XML_START_ELEMENT,
                                  TYPE_STRING, StringPool, chunks)

HOTSEAT_CONTAINER = "-101"
NO_STRING = 0xFFFFFFFF
TYPE_INT_DEC = 0x10


def attributes(data: bytes, offset: int):
    """{name: (attribute offset, value as text)} of a start element."""
    start, size, count = struct.unpack_from("<HHH", data, offset + 24)
    base = offset + 16 + start
    for i in range(count):
        at = base + i * size
        _, name, raw, _, _, kind, value = struct.unpack_from("<IIIHBBI", data, at)
        if raw != NO_STRING:
            text = POOL.strings[raw]
        elif kind == TYPE_INT_DEC:
            text = str(struct.unpack("<i", struct.pack("<I", value))[0])
        else:
            text = None
        yield POOL.strings[name], at, text


def set_string(data: bytearray, at: int, index: int):
    struct.pack_into("<I", data, at + 8, index)  # raw value
    struct.pack_into("<HBBI", data, at + 12, 8, 0, TYPE_STRING, index)


def patch(data: bytes, slot: str, package: str, cls: str) -> tuple[bytes, bool]:
    global POOL
    kind, header_size, _ = struct.unpack_from("<HHI", data, 0)
    if kind != RES_XML:
        raise ValueError("not a binary XML file")
    layout = list(chunks(data, header_size))
    pool_offset, pool_kind, _, pool_size = layout[0]
    if pool_kind != RES_STRING_POOL:
        raise ValueError("string pool not found")
    POOL = StringPool(data[pool_offset:pool_offset + pool_size])

    targets = []
    for offset, chunk_kind, _, _ in layout:
        if chunk_kind != RES_XML_START_ELEMENT:
            continue
        if POOL.strings[struct.unpack_from("<I", data, offset + 20)[0]] != "favorite":
            continue
        attrs = {name: (at, text) for name, at, text in attributes(data, offset)}
        if attrs.get("container", (0, None))[1] != HOTSEAT_CONTAINER:
            continue
        if attrs.get("x", (0, None))[1] != slot:
            continue
        if "packageName" not in attrs or "className" not in attrs:
            raise ValueError(f"hotseat slot {slot} has no packageName/className")
        targets.append(attrs)
    if not targets:
        raise ValueError(f"hotseat slot {slot} not found")
    if all(t["packageName"][1] == package and t["className"][1] == cls for t in targets):
        return data, False

    out = bytearray(data)
    package_index, class_index = POOL.add(package), POOL.add(cls)
    for attrs in targets:
        set_string(out, attrs["packageName"][0], package_index)
        set_string(out, attrs["className"][0], class_index)

    body = POOL.pack() + bytes(out[pool_offset + pool_size:])
    header = bytearray(out[:header_size])
    struct.pack_into("<I", header, 4, header_size + len(body))
    return bytes(header) + body, True


if __name__ == "__main__":
    path, slot, package, cls = sys.argv[1:]
    file = Path(path)
    new, changed = patch(file.read_bytes(), slot, package, cls)
    if changed:
        file.write_bytes(new)
    print(int(changed))
