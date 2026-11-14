#!/usr/bin/env python3
"""Show every bundled watermark in MiuiCamera / MediaEditor, whatever the device.

Usage: patch_camera_watermark_all.py <decoded MiuiCamera or MediaEditor dir>

Each watermark (assets/watermarks/<group>/<id>/meta.json) carries a
"limitation" block, and LimitationUtil drops the ones whose block does not
match the phone: the Leitzphone set (leica 88-92, 111) needs
ro.boot.product.theme_customize=lcc, Disney needs the WestCoast3 theme, others
are tied to a device_type (leica/poco), ro.product.device, the build region or
the per-device id list Xiaomi's cloud pushes (supported_watermark_list).

Every filter is a Kotlin lambda passed to removeIf, so returning false keeps
the item. The lambdas are R8-named, so each is found by its log tag; R8 also
renames invoke() itself in MediaEditor (e()), so any name is accepted. In
MediaEditor filterById also deletes the dropped watermarks from its data. The
validity and name-length filters stay: name length picks between twin
variants of one design (leica 1/104, classic 17/95) and must not show both.
"""
import re
import sys
from pathlib import Path

FILTERS = {
    "filterById",                # cloud supported_watermark_list
    "filterByDevice",            # ro.product.device include / exclude
    "filterByBuildRegion",       # ro.miui.build.region
    "filterByTheme",             # ro.boot.product.theme_customize (lcc = Leitzphone)
    "filterBySystemProperties",  # any other system_properties entry
    "filterByDeviceType",        # leica / poco / redmi
    "filterCustomProperties",    # custom_not_support (global + lcc)
}

INVOKE = re.compile(
    r"(?ms)^\.method public final (?P<name>\w+)\(Ljava/lang/Object;\)Ljava/lang/Object;\n.*?^\.end method$"
)
TAG = re.compile(r'const-string [vp]\d+, "(filter\w+): "')
KEEP = (
    ".method public final {name}(Ljava/lang/Object;)Ljava/lang/Object;\n"
    "    .registers 3\n\n"
    "    sget-object v0, Ljava/lang/Boolean;->FALSE:Ljava/lang/Boolean;\n\n"
    "    return-object v0\n"
    ".end method"
)


def main():
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2

    found = set()
    for path in Path(sys.argv[1]).glob("smali*/**/*.smali"):
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        if '"LimitationUtil"' not in text:
            continue

        def repl(m):
            tag = TAG.search(m.group(0))
            if not tag or tag.group(1) not in FILTERS:
                return m.group(0)
            found.add(tag.group(1))
            print(f"[watermark] {path.name}: {tag.group(1)} -> keep all")
            return KEEP.format(name=m.group("name"))

        new = INVOKE.sub(repl, text)
        if new != text:
            path.write_text(new, encoding="utf-8", errors="surrogateescape")

    if not found:
        print("[watermark] no LimitationUtil filter matched; skip")
        return 1
    missing = FILTERS - found
    if missing:
        print(f"[watermark] not found (left as is): {', '.join(sorted(missing))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
