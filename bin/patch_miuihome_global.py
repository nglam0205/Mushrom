#!/usr/bin/env python3
"""Make the HyperOS 3 (Rust/Flutter) launcher treat the ROM as a global build.

libapp_launcher.so imports MiuiOsBuild_is_international_build /
MiuiOsBuild_is_global_build from libhyper_os_system_public.so, which read
ro.product.mod_device natively (the Java miui.os.Build spoof never reaches
them). Every MiuiOsBuild_is_* export is the same `fn(&MiuiOsBuild) -> bool`
wrapper, so renaming the imports in .dynstr to MiuiOsBuild_is_miui (always
true on HyperOS) makes the launcher take its global code paths.

Usage: patch_miuihome_global.py <libapp_launcher.so>
Prints the number of patched imports; exit 1 on error.
"""
import struct
import sys

REPLACEMENT = b"MiuiOsBuild_is_miui"
TARGETS = [
    b"MiuiOsBuild_is_international_build",
    b"MiuiOsBuild_is_global_build",
]

DT_STRTAB = 5
DT_STRSZ = 10
DT_SYMTAB = 6
DT_NULL = 0
PT_LOAD = 1
PT_DYNAMIC = 2


def vaddr_to_offset(phdrs, vaddr):
    for p_type, p_offset, p_vaddr, p_filesz in phdrs:
        if p_type == PT_LOAD and p_vaddr <= vaddr < p_vaddr + p_filesz:
            return vaddr - p_vaddr + p_offset
    raise ValueError(f"vaddr 0x{vaddr:x} not in any PT_LOAD")


def main(path):
    data = bytearray(open(path, "rb").read())
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise ValueError("not a little-endian ELF64 file")

    e_phoff, = struct.unpack_from("<Q", data, 0x20)
    e_phentsize, e_phnum = struct.unpack_from("<HH", data, 0x36)
    phdrs = []
    dynamic = None
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        p_type, _flags, p_offset, p_vaddr, _paddr, p_filesz = struct.unpack_from("<IIQQQQ", data, off)
        phdrs.append((p_type, p_offset, p_vaddr, p_filesz))
        if p_type == PT_DYNAMIC:
            dynamic = (p_offset, p_filesz)
    if dynamic is None:
        raise ValueError("no PT_DYNAMIC")

    dyn = {}
    for off in range(dynamic[0], dynamic[0] + dynamic[1], 16):
        tag, val = struct.unpack_from("<qQ", data, off)
        if tag == DT_NULL:
            break
        dyn.setdefault(tag, val)
    strtab = vaddr_to_offset(phdrs, dyn[DT_STRTAB])
    strsz = dyn[DT_STRSZ]
    symtab = vaddr_to_offset(phdrs, dyn[DT_SYMTAB])

    # .dynsym sits right before .dynstr in these libs; collect every st_name.
    names = set()
    for off in range(symtab, strtab, 24):
        st_name, = struct.unpack_from("<I", data, off)
        names.add(st_name)

    patched = 0
    for target in TARGETS:
        needle = target + b"\x00"
        pos = data.find(needle, strtab, strtab + strsz)
        if pos < 0:
            continue
        # Must be a whole string, referenced only from its start.
        if pos > strtab and data[pos - 1] != 0:
            raise ValueError(f"{target.decode()} is a suffix of another string")
        start = pos - strtab
        if start not in names:
            raise ValueError(f"{target.decode()} not referenced by .dynsym")
        if any(start < n < start + len(target) for n in names):
            raise ValueError(f"{target.decode()} shares its tail with another symbol")
        data[pos:pos + len(needle)] = REPLACEMENT + b"\x00" * (len(needle) - len(REPLACEMENT))
        patched += 1

    if patched:
        open(path, "wb").write(data)
    print(patched)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    try:
        main(sys.argv[1])
    except (OSError, ValueError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
