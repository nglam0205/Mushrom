#!/usr/bin/env python3
"""Skip the package verifier (Play Protect) for installs made by VSTeam.

Play Protect rejects VSTeam's overlays/plugins server-side even when the local
scan says SAFE. VerifyingSession.isVerificationEnabled is the AOSP gate on both
HyperOS and ColorOS; return false when the session's installer is VSTeam.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


CLASS_RELATIVE_PATH = Path("com/android/server/pm/VerifyingSession.smali")
PATCH_MARKER = "VS_VSTEAM_SKIP_VERIFIER"
METHOD_HEADER = (
    ".method private isVerificationEnabled("
    "Landroid/content/pm/PackageInfoLite;ILjava/util/List;)Z"
)

GATE_BLOCK = f"""    # {PATCH_MARKER}
    iget-object v0, p0, Lcom/android/server/pm/VerifyingSession;->mInstallSource:Lcom/android/server/pm/InstallSource;

    iget-object v0, v0, Lcom/android/server/pm/InstallSource;->mInstallerPackageName:Ljava/lang/String;

    const-string v1, "com.bhlnk.vsteam"

    invoke-virtual {{v1, v0}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v0

    if-eqz v0, :cond_vs_vsteam_verify

    const/4 v0, 0x0

    return v0

    :cond_vs_vsteam_verify
"""


def find_target(decoded_root: Path) -> Path:
    matches = sorted(decoded_root.glob(f"smali*/{CLASS_RELATIVE_PATH}"))
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one VerifyingSession.smali, found {len(matches)}"
        )
    return matches[0]


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
    raise RuntimeError("isVerificationEnabled has no code")


def registers_ok(lines: list[str], start: int) -> bool:
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith(".registers"):
            # p0 + 3 params, plus the two locals the gate uses.
            return int(stripped.split()[1]) >= 6
        if stripped.startswith(".locals"):
            return int(stripped.split()[1]) >= 2
        if stripped == ".end method":
            break
    return False


def patch(decoded_root: Path) -> tuple[Path, str]:
    target = find_target(decoded_root)
    text = target.read_text(encoding="utf-8")
    if PATCH_MARKER in text:
        return target, "already-patched"
    if text.count(METHOD_HEADER) != 1:
        raise RuntimeError("isVerificationEnabled(PackageInfoLite, int, List) not found")
    if "Lcom/android/server/pm/InstallSource;->mInstallerPackageName:Ljava/lang/String;" not in text:
        raise RuntimeError("InstallSource.mInstallerPackageName not referenced")

    lines = text.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.startswith(METHOD_HEADER))
    if not registers_ok(lines, start):
        raise RuntimeError("isVerificationEnabled: not enough registers")
    lines.insert(first_instruction(lines, start), GATE_BLOCK + "\n")

    patched = "".join(lines)
    if patched.count(PATCH_MARKER) != 1:
        raise RuntimeError("verifier skip patch verification failed")
    target.write_text(patched, encoding="utf-8")
    return target, "patched"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("decoded_root", type=Path)
    args = parser.parse_args()

    try:
        target, status = patch(args.decoded_root.resolve())
    except (OSError, RuntimeError) as exc:
        print(f"vsteam verifier skip patch failed: {exc}", file=sys.stderr)
        return 1

    print(f"vsteam verifier skip {status}: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
