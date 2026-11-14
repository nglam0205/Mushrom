#!/usr/bin/env python3
"""List every local theme, including the ones ThemeManager hides.

Usage: patch_theme_show_hidden.py <decoded MIUIThemeManager dir>

MemoryDataManager skips a scanned resource when its filter method returns
true: a hardcoded set of theme ids, plus the precust themes of other hwids
(PreCustomThemeConfig in the device's ThemeManager overlay, matched against
ro.boot.hwversion). On a K90 Pro Max that hides five of the six Lamborghini
themes in /product/etc/precust_theme; on any other device, all six. The
filter is renamed by R8 each release, so it is found by shape: the
`protected (Resource)Z` method of MemoryDataManager that asks
ProvisionApplyTheme. It now always returns false.
"""
import re
import sys
from pathlib import Path


TARGET = "com/android/thememanager/controller/MemoryDataManager.smali"
METHOD = re.compile(
    r"(?ms)^\.method protected (?P<name>\w+)\(Lcom/android/thememanager/basemodule/resource/model/Resource;\)Z\n"
    r"(?P<body>.*?)^\.end method$"
)
CALLS = "Lcom/android/thememanager/controller/local/ProvisionApplyTheme;->"
BODY = "    .registers 2\n\n    const/4 v0, 0x0\n\n    return v0\n"


def main():
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2

    targets = list(Path(sys.argv[1]).glob(f"smali*/{TARGET}"))
    if not targets:
        print("[theme] MemoryDataManager not present in this build")
        return 1

    for target in targets:
        text = target.read_text(encoding="utf-8", errors="surrogateescape")
        found = [m for m in METHOD.finditer(text) if CALLS in m["body"]]
        if not found:
            if any(m["body"] == BODY for m in METHOD.finditer(text)):
                print("[theme] hidden theme filter already disabled")
                return 0
            print("[theme] hidden theme filter not found", file=sys.stderr)
            return 1
        if len(found) > 1:
            print("[theme] more than one candidate filter; not touching any", file=sys.stderr)
            return 1
        match = found[0]
        target.write_text(
            text[: match.start("body")] + BODY + text[match.end("body"):],
            encoding="utf-8",
            errors="surrogateescape",
        )
        print(f"[theme] MemoryDataManager.{match['name']}: show hidden themes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
