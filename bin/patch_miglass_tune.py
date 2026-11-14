#!/usr/bin/env python3
"""Route HyperOS liquid glass parameters through Toolbox.tuneMiGlass.

Usage: patch_miglass_tune.py <decoded framework.jar dir>

Every app (SystemUI, Home, miuix widgets) hands its glass preset to
android.view.View.setMiGlass(float[]), which forwards it to RenderNode and the
MiGlass shader in libhwui. Hooking that one method lets the refraction strength
be scaled system-wide without touching the presets baked into each app.
"""
import re
import sys
from pathlib import Path


# apktool-vs keeps hidden-API flags in the header (".method public whitelist
# setMiGlass([F)V"), so only the name and descriptor are fixed.
TARGET = "setMiGlass([F)V"
HOOK = (
    "    invoke-static {p1}, Lcom/android/vsteam/Toolbox;->tuneMiGlass([F)[F\n"
    "\n"
    "    move-result-object p1\n"
    "\n"
)
METHOD = re.compile(
    r"(?ms)^(?P<header>\.method [^\n]*\b" + re.escape(TARGET) + r")\n(?P<body>.*?)^\.end method$"
)


def insert_hook(match):
    lines = match.group("body").split("\n")
    # First real instruction: directives (.registers, .param, .line) and blank
    # lines have to stay ahead of the hook.
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("."):
            lines.insert(index, HOOK.rstrip("\n") + "\n")
            return match.group("header") + "\n" + "\n".join(lines) + ".end method"
    return match.group(0)


def main():
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2

    views = [
        path
        for path in Path(sys.argv[1]).glob("smali*/android/view/View.smali")
        if METHOD.search(path.read_text(encoding="utf-8", errors="surrogateescape"))
    ]
    if not views:
        # Pre-OS4 and non-HyperOS frameworks have no bionic material.
        print("[miglass] View.setMiGlass not present; skip")
        return 0

    for view in views:
        text = view.read_text(encoding="utf-8", errors="surrogateescape")
        if "Toolbox;->tuneMiGlass" in text:
            print(f"[miglass] already hooked: {view}")
            continue
        patched = METHOD.sub(insert_hook, text, count=1)
        if patched == text:
            print(f"[miglass] could not hook {view}", file=sys.stderr)
            return 1
        view.write_text(patched, encoding="utf-8", errors="surrogateescape")
        print(f"[miglass] hooked View.setMiGlass in {view}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
