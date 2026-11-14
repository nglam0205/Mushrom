#!/usr/bin/env python3
"""Give apps the DPI picked in their VSTeam spoof profile ("density" of vs_spoof_<package>).

Usage: patch_app_density.py <decoded services.jar dir>

InputManagerService#systemRunning() calls com.android.vsteam.AppDensity.attach(mContext), which
registers a CompatScaleProvider with the window manager (the way GameManager downscales games):
each app process then starts with the density of its profile. systemRunning comes late enough
for ActivityTaskManagerInternal and the settings provider, on HyperOS and ColorOS alike.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


CLASS_RELATIVE_PATH = Path("com/android/server/input/InputManagerService.smali")
IMS = "Lcom/android/server/input/InputManagerService;"
PATCH_MARKER = "VS_APP_DENSITY"
SYSTEM_RUNNING = ".method public systemRunning()V"

ATTACH_BLOCK = f"""    # {PATCH_MARKER}
    iget-object v0, p0, {IMS}->mContext:Landroid/content/Context;

    invoke-static {{v0}}, Lcom/android/vsteam/AppDensity;->attach(Landroid/content/Context;)V

"""


def find_target(decoded_root: Path) -> Path | None:
    matches = sorted(decoded_root.glob(f"smali*/{CLASS_RELATIVE_PATH}"))
    if len(matches) > 1:
        raise RuntimeError(f"expected one InputManagerService.smali, found {len(matches)}")
    return matches[0] if matches else None


def patch(decoded_root: Path) -> tuple[Path | None, str]:
    target = find_target(decoded_root)
    if target is None:
        return None, "skipped (no InputManagerService)"
    text = target.read_text(encoding="utf-8")
    if PATCH_MARKER in text:
        return target, "already-patched"

    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip("\n") == SYSTEM_RUNNING]
    if len(starts) != 1:
        raise RuntimeError(f"expected one systemRunning(), found {len(starts)}")
    start = starts[0]
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped == ".end method":
            break
        # baksmali writes .registers (p0 included), apktool .locals: v0 must be a local.
        if stripped.startswith(".registers") or stripped.startswith(".locals"):
            count = int(stripped.split()[1])
            if count < (2 if stripped.startswith(".registers") else 1):
                raise RuntimeError("systemRunning(): no free v0")
            lines.insert(i + 1, "\n" + ATTACH_BLOCK)
            target.write_text("".join(lines), encoding="utf-8")
            return target, "patched"
    raise RuntimeError("systemRunning(): no register directive")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("decoded_root", type=Path)
    args = parser.parse_args()

    try:
        target, status = patch(args.decoded_root.resolve())
    except (OSError, RuntimeError) as exc:
        print(f"app density patch failed: {exc}", file=sys.stderr)
        return 1

    print(f"app density {status}: {target}")
    return 0 if target is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
