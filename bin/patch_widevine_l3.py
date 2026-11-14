#!/usr/bin/env python3
"""Force Widevine L3 in MediaDrm for models whose L1 cannot provision.

Usage: patch_widevine_l3.py <decoded framework.jar dir>

MediaDrm(UUID) ends with com.android.vsteam.WidevineL3.onCreate(this, uuid), which sets the
plugin property securityLevel=L3 on Widevine, and openSession(int) starts with
WidevineL3.sessionLevel(this, level), which lowers hardware levels to SW_SECURE_DECODE.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


CLASS_RELATIVE_PATH = Path("android/media/MediaDrm.smali")
PATCH_MARKER = "VS_WIDEVINE_L3"
CONSTRUCTOR = ".method public constructor <init>(Ljava/util/UUID;)V"
OPEN_SESSION = ".method public openSession(I)[B"

CREATE_BLOCK = f"""    # {PATCH_MARKER}
    invoke-static {{p0, p1}}, Lcom/android/vsteam/WidevineL3;->onCreate(Landroid/media/MediaDrm;Ljava/util/UUID;)V

"""

LEVEL_BLOCK = f"""    # {PATCH_MARKER}
    invoke-static {{p0, p1}}, Lcom/android/vsteam/WidevineL3;->sessionLevel(Landroid/media/MediaDrm;I)I

    move-result p1

"""


def find_target(decoded_root: Path) -> Path | None:
    matches = sorted(decoded_root.glob(f"smali*/{CLASS_RELATIVE_PATH}"))
    if len(matches) > 1:
        raise RuntimeError(f"expected one MediaDrm.smali, found {len(matches)}")
    return matches[0] if matches else None


def method_range(lines: list[str], header: str) -> tuple[int, int]:
    starts = [i for i, line in enumerate(lines) if line.rstrip("\n") == header]
    if len(starts) != 1:
        raise RuntimeError(f"expected one {header}, found {len(starts)}")
    start = starts[0]
    for i in range(start + 1, len(lines)):
        if lines[i].strip() == ".end method":
            return start, i
    raise RuntimeError(f"{header}: no .end method")


def patch(decoded_root: Path) -> tuple[Path | None, str]:
    target = find_target(decoded_root)
    if target is None:
        return None, "skipped (no MediaDrm)"
    text = target.read_text(encoding="utf-8")
    if PATCH_MARKER in text:
        return target, "already-patched"

    lines = text.splitlines(keepends=True)

    # Constructor: before its only return-void, after native_setup.
    start, end = method_range(lines, CONSTRUCTOR)
    returns = [i for i in range(start, end) if lines[i].strip() == "return-void"]
    if len(returns) != 1:
        raise RuntimeError(f"MediaDrm(UUID): expected one return-void, found {len(returns)}")
    lines.insert(returns[0], CREATE_BLOCK)

    # openSession(int): right after the register directive (p0, p1 only).
    start, end = method_range(lines, OPEN_SESSION)
    for i in range(start + 1, end):
        stripped = lines[i].strip()
        if stripped.startswith(".registers") or stripped.startswith(".locals"):
            lines.insert(i + 1, "\n" + LEVEL_BLOCK)
            break
    else:
        raise RuntimeError("openSession(I): no register directive")

    target.write_text("".join(lines), encoding="utf-8")
    return target, "patched"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("decoded_root", type=Path)
    args = parser.parse_args()

    try:
        target, status = patch(args.decoded_root.resolve())
    except (OSError, RuntimeError) as exc:
        print(f"widevine l3 patch failed: {exc}", file=sys.stderr)
        return 1

    print(f"widevine l3 {status}: {target}")
    return 0 if target is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
