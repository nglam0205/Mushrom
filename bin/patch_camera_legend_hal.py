#!/usr/bin/env python3
"""Let the camera HAL configure Legendary (Leica Essential) on a plain SKU.

Usage: patch_camera_legend_hal.py <libmicamera_adapter.so>

libmicamera_adapter rejects the session of mode 0x100 (Legendary / Leica
Essential, M3/M9) unless ro.theme_customize is "LCC", a prop only the Leica SKU
sets. The app logs "processConfig()config failed:-1" / "Error configuring
streams: Function not implemented (-38)" and the mode never opens:

    ldr  wN, [theme_customize]
    sub  wN, wN, #0x434, lsl #12
    cmp  wN, #0x34c                 ; == "LCC\\0"
    b.eq ok
    cmp  wM, #0x100                 ; mode == Legendary
    b.ne ok
    ...                             ; return -1

Setting the prop instead is not an option: the app then treats the phone as
the Leica edition and crashes on the hardware it lacks. Turning the b.eq into
an unconditional b skips only this check; nothing else reads the prop.
"""
import struct
import sys
from pathlib import Path


def match(words, i):
    sub, cmp_lcc, beq, cmp_mode, bne = words[i:i + 5]
    rd = sub & 0x1F
    if (sub & 0xFFFFFC00) != 0x5150D000 or ((sub >> 5) & 0x1F) != rd:
        return None
    if (cmp_lcc & 0xFFFFFC1F) != 0x710D301F or ((cmp_lcc >> 5) & 0x1F) != rd:
        return None
    if (cmp_mode & 0xFFFFFC1F) != 0x7104001F or (bne & 0xFF00001F) != 0x54000001:
        return None
    bne_target = i + 4 + sign(bne >> 5, 19)
    if (beq & 0xFF00001F) == 0x54000000:
        if i + 2 + sign(beq >> 5, 19) != bne_target:
            return None
        return "patch"
    if (beq & 0xFC000000) == 0x14000000 and i + 2 + sign(beq, 26) == bne_target:
        return "done"
    return None


def sign(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def main():
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_file():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    data = bytearray(path.read_bytes())
    n = len(data) // 4
    words = struct.unpack_from(f"<{n}I", data)

    hits = [(i, match(words, i)) for i in range(n - 5)]
    hits = [(i, kind) for i, kind in hits if kind]
    if len(hits) != 1:
        print(f"[legend-hal] expected one LCC gate, found {len(hits)}; skip")
        return 1

    i, kind = hits[0]
    if kind == "done":
        print("[legend-hal] already patched")
        return 0

    beq = words[i + 2]
    struct.pack_into("<I", data, (i + 2) * 4, 0x14000000 | (sign(beq >> 5, 19) & 0x3FFFFFF))
    path.write_bytes(data)
    print(f"[legend-hal] LCC gate for mode 0x100 bypassed at 0x{(i + 2) * 4:x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
