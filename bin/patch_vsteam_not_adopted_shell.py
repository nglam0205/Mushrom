#!/usr/bin/env python3
"""Keep VSTeam's PackageInstaller sessions out of the "adopted shell" path.

The VS framework patch makes ActivityManager.canAccessUnexportedComponents()
true for VSTeam's uid, and since Android 15 checkComponentPermission() grants
every permission to such a uid. PackageManagerServiceUtils.isAdoptedShell()
asks for USE_SYSTEM_DATA_LOADERS, so createSessionInternal() handled VSTeam's
sessions like `adb install`: no INSTALL_REPLACE_EXISTING (updates failed with
INSTALL_FAILED_ALREADY_EXISTS), initiating package com.android.shell and
INSTALL_FROM_ADB (ColorOS then runs its adb-install checks). Return false for
VSTeam's app id so its sessions take the normal installer path.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


CLASS_RELATIVE_PATH = Path("com/android/server/pm/PackageManagerServiceUtils.smali")
PATCH_MARKER = "VS_VSTEAM_NOT_ADOPTED_SHELL"
METHOD_HEADER = ".method public static isAdoptedShell(ILandroid/content/Context;)Z"

# p0 is an app id (createSessionInternal passes UserHandle.getAppId(callingUid)).
GATE_BLOCK = f"""    # {PATCH_MARKER}
    invoke-static {{}}, Lcom/android/vsteam/Toolbox;->getVsteamUid()I

    move-result v0

    invoke-static {{v0}}, Landroid/os/UserHandle;->getAppId(I)I

    move-result v0

    if-ne p0, v0, :cond_vs_not_vsteam_shell

    const/4 v0, 0x0

    return v0

    :cond_vs_not_vsteam_shell
"""


def find_target(decoded_root: Path) -> Path | None:
    matches = sorted(decoded_root.glob(f"smali*/{CLASS_RELATIVE_PATH}"))
    if len(matches) > 1:
        raise RuntimeError(f"expected one PackageManagerServiceUtils.smali, found {len(matches)}")
    return matches[0] if matches else None


def first_instruction(lines: list[str], start: int) -> int:
    """Index of the first code line of the method starting at ``start``."""
    in_annotation = False
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped == ".end method":
            break
        if stripped.startswith(".annotation"):
            in_annotation = True
            continue
        if stripped.startswith(".end annotation"):
            in_annotation = False
            continue
        if in_annotation or not stripped or stripped.startswith((".", "#", ":")):
            continue
        return i
    raise RuntimeError("isAdoptedShell has no code")


def registers_ok(lines: list[str], start: int) -> bool:
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith(".registers"):
            # p0, p1 plus the one local the gate uses.
            return int(stripped.split()[1]) >= 3
        if stripped.startswith(".locals"):
            return int(stripped.split()[1]) >= 1
        if stripped == ".end method":
            break
    return False


def patch(decoded_root: Path) -> tuple[Path | None, str]:
    target = find_target(decoded_root)
    if target is None:
        return None, "skipped (no PackageManagerServiceUtils)"
    text = target.read_text(encoding="utf-8")
    if PATCH_MARKER in text:
        return target, "already-patched"
    if text.count(METHOD_HEADER) == 0:
        return target, "skipped (no isAdoptedShell on this Android version)"
    if text.count(METHOD_HEADER) != 1:
        raise RuntimeError("isAdoptedShell(int, Context) found more than once")

    lines = text.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.startswith(METHOD_HEADER))
    if not registers_ok(lines, start):
        raise RuntimeError("isAdoptedShell: not enough registers")
    lines.insert(first_instruction(lines, start), GATE_BLOCK + "\n")

    patched = "".join(lines)
    if patched.count(PATCH_MARKER) != 1:
        raise RuntimeError("adopted shell patch verification failed")
    target.write_text(patched, encoding="utf-8")
    return target, "patched"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("decoded_root", type=Path)
    args = parser.parse_args()

    try:
        target, status = patch(args.decoded_root.resolve())
    except (OSError, RuntimeError) as exc:
        print(f"vsteam adopted shell patch failed: {exc}", file=sys.stderr)
        return 1

    print(f"vsteam adopted shell {status}: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
