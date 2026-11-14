#!/usr/bin/env python3
"""Drop HyperOS's per-app refresh-rate cap so every app runs at the panel max.

HyperOS throttles the foreground app's refresh rate in
com.android.server.wm.MiuiRefreshRatePolicy (miui-services, running in
system_server). A per-package "scene refresh rate" (e.g. 90 for Liên Quân, fed
from the cloud fps list / Game Turbo) is returned by
getSceneRefreshRate(WindowState) and stored by DisplayContent as the window's
preferredSceneMaxRefreshRate, which SurfaceFlinger then enforces as a 90Hz
frame-rate override on the game surface even while the panel runs at 120.

DisplayContent only applies the value when it is not -1.0f (the "no scene
preference" sentinel the method already returns for unfocused windows). This
rewrites getSceneRefreshRate to always return -1.0f, so no window ever gets a
scene cap and the app is free to run up to the panel's maximum. Idempotent:
re-running is a no-op.
"""
import sys
from pathlib import Path

MARKER = "VS_MAX_FPS"
CLASS = "MiuiRefreshRatePolicy.smali"
METHOD = ".method public getSceneRefreshRate(Lcom/android/server/wm/WindowState;)F"

# v0 is a local in this method; returning immediately needs nothing else.
BLOCK = f"""
    # {MARKER}: never cap a window's refresh rate; -1.0f means "no scene preference".
    const/high16 v0, -0x40800000    # -1.0f

    return v0
"""


def smali_files(root: Path):
    return sorted(
        path
        for directory in root.glob("smali*") if directory.is_dir()
        for path in directory.rglob(CLASS)
    )


def patch(text: str) -> str | None:
    start = text.find(METHOD)
    if start < 0:
        return None
    end = text.find("\n.end method", start)
    if end < 0:
        return None
    if MARKER in text[start:end]:
        return text  # already patched

    lines = text[start:end].splitlines(keepends=True)
    insert_at = 1
    i = 1
    in_annotation = False
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith(".end annotation"):
            in_annotation = False
            i += 1
            insert_at = i
            continue
        if in_annotation:
            i += 1
            continue
        if stripped.startswith(".annotation"):
            in_annotation = True
            i += 1
            continue
        if stripped == "" or stripped.startswith((".registers", ".locals", ".param", ".prologue")):
            i += 1
            insert_at = i
            continue
        break

    body = lines[:insert_at] + [BLOCK.lstrip("\n")] + lines[insert_at:]
    return text[:start] + "".join(body) + text[end:]


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: patch_miui_max_fps.py <miui-services.jar.out>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    targets = smali_files(root)
    if not targets:
        print("patch_miui_max_fps: MiuiRefreshRatePolicy.smali not found", file=sys.stderr)
        return 1

    patched = 0
    for path in targets:
        text = path.read_text(encoding="utf-8")
        result = patch(text)
        if result is None:
            continue
        if result != text:
            path.write_text(result, encoding="utf-8")
            patched += 1
        elif MARKER in text:
            patched += 1  # already applied, count as success

    if patched == 0:
        print("patch_miui_max_fps: getSceneRefreshRate not found", file=sys.stderr)
        return 1
    # Quiet on success: the caller already logs the action line.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
