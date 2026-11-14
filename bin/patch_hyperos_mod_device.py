#!/usr/bin/env python3
"""Make HyperOS native (Rust) apps read ro.hypervs.mod_device instead of
ro.product.mod_device.

libhyper_os_core_system_private.dylib.so is the Rust port of miui.os.Build
(MiuiOsBuild::is_international_build/is_global_build...) used by the
Rust/Flutter apps such as the HyperOS 3 launcher. It reads
ro.product.mod_device as a 21-byte &str (adr + len 0x15), so swapping the
bytes for the equally long ro.hypervs.mod_device lets the ROM decide the
region of those apps (e.g. ro.hypervs.mod_device=popsicle_global) without
touching the real ro.product.mod_device used by Java/C++ code.

The launcher's Dart AOT snapshot (MiuiHome lib/arm64-v8a/libapp.so) reads the
same property directly and holds it once as a plain 21-byte string, so the
same swap applies to it.

Usage: patch_hyperos_mod_device.py <libhyper_os_core_system_private.dylib.so|libapp.so>
Prints 1 when patched, 0 when already patched; exit 1 on error.
"""
import sys

OLD = b"ro.product.mod_device"
NEW = b"ro.hypervs.mod_device"
assert len(OLD) == len(NEW)


def main(path):
    data = bytearray(open(path, "rb").read())
    if data[:4] != b"\x7fELF":
        raise ValueError("not an ELF file")

    old_count = data.count(OLD)
    if old_count == 0:
        if data.count(NEW) == 1:
            print(0)
            return
        raise ValueError(f"{OLD.decode()} not found")
    if old_count != 1:
        raise ValueError(f"{OLD.decode()} found {old_count} times, expected 1")

    pos = data.find(OLD)
    data[pos:pos + len(OLD)] = NEW
    open(path, "wb").write(data)
    print(1)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    try:
        main(sys.argv[1])
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
