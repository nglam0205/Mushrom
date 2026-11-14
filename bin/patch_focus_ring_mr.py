#!/usr/bin/env python3
"""Make the camera ring report its detents outside the camera while a VSTeam action is set.

Usage: patch_focus_ring_mr.py <decoded miui-services.jar dir>

CameraMrEventHandler (com.android.server.input.specialevent) sets the mode of the ring's
sensor hub (Calibrate.nativeSetConfig 0x1fa26c0): 1 = each detent, only while the camera is
focused; 2 = a long rotation (quick launch of the camera) and 4 = haptics elsewhere. So outside
the camera no detent ever reaches InputManagerService, and com.android.vsteam.FocusRing has
nothing to act on. Three hooks:
- determineCameraMrSensorStateLocked(), which runs whenever the camera gains or loses focus,
  first tells FocusRing.setInCamera() whether it has it, so the camera keeps its own events;
- it then passes the state it built for outside the camera through FocusRing.cameraMrState(),
  which asks for the detents while vs_focus_ring_cw/ccw is set;
- <clinit> adds those two keys to SETTING_GLOBAL_LIST, so the handler's own settings observer
  sets the mode again when VSTeam changes them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


CLASS_RELATIVE_PATH = Path("com/android/server/input/specialevent/CameraMrEventHandler.smali")
HANDLER = "Lcom/android/server/input/specialevent/CameraMrEventHandler;"
RING = "Lcom/android/vsteam/FocusRing;"
PATCH_MARKER = "VS_FOCUS_RING_MR"
KEYS = ("vs_focus_ring_cw", "vs_focus_ring_ccw")

DETERMINE = ".method private determineCameraMrSensorStateLocked()V"
CLINIT = ".method static constructor <clinit>()V"
VIBRATION_CALL = f"invoke-direct {{p0, v0}}, {HANDLER}->handleVibrationSetting(I)I"

# v1 is free at entry: the method's first use of it is a load.
ENTRY_BLOCK = f"""    # {PATCH_MARKER}
    iget-boolean v1, p0, {HANDLER}->mIsInCamera:Z

    invoke-static {{v1}}, {RING}->setInCamera(Z)V

"""

# v1 is free here: the next instruction loads mIsCameraMrSettingEnable into it.
STATE_BLOCK = f"""
    # {PATCH_MARKER}
    iget-object v1, p0, {HANDLER}->mContext:Landroid/content/Context;

    invoke-static {{v1, v0}}, {RING}->cameraMrState(Landroid/content/Context;I)I

    move-result v0
"""


def keys_block() -> str:
    lines = [f"    # {PATCH_MARKER}\n"]
    for key in KEYS:
        lines.append(f"    sget-object v0, {HANDLER}->SETTING_GLOBAL_LIST:Ljava/util/ArrayList;\n\n")
        lines.append(f'    const-string v1, "{key}"\n\n')
        lines.append("    invoke-virtual {v0, v1}, Ljava/util/ArrayList;->add(Ljava/lang/Object;)Z\n\n")
    return "".join(lines)


def find_target(decoded_root: Path) -> Path | None:
    matches = sorted(decoded_root.glob(f"smali*/{CLASS_RELATIVE_PATH}"))
    if len(matches) > 1:
        raise RuntimeError(f"expected one CameraMrEventHandler.smali, found {len(matches)}")
    return matches[0] if matches else None


def method_range(lines: list[str], header: str) -> tuple[int, int]:
    found = [i for i, line in enumerate(lines) if line.rstrip("\n") == header]
    if len(found) != 1:
        raise RuntimeError(f"expected one {header}, found {len(found)}")
    for end in range(found[0] + 1, len(lines)):
        if lines[end].strip() == ".end method":
            return found[0], end
    raise RuntimeError(f"{header} has no end")


def locals_count(lines: list[str], start: int, end: int, params: int) -> int:
    """Number of v registers: baksmali writes .registers (params included), apktool .locals."""
    for i in range(start + 1, end):
        stripped = lines[i].strip()
        if stripped.startswith(".registers"):
            return int(stripped.split()[1]) - params
        if stripped.startswith(".locals"):
            return int(stripped.split()[1])
    raise RuntimeError(f"no .registers or .locals in {lines[start].strip()}")


def hook_state(lines: list[str]) -> None:
    start, end = method_range(lines, DETERMINE)
    if locals_count(lines, start, end, 1) < 2:
        raise RuntimeError("determineCameraMrSensorStateLocked: v1 is not a local")
    calls = [i for i in range(start, end) if lines[i].strip() == VIBRATION_CALL]
    if len(calls) != 1:
        raise RuntimeError(f"expected one handleVibrationSetting call, found {len(calls)}")
    for i in range(calls[0] + 1, end):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith(".line"):
            continue
        if stripped != "move-result v0":
            raise RuntimeError(f"unexpected line after handleVibrationSetting: {stripped}")
        lines.insert(i + 1, STATE_BLOCK)
        break
    else:
        raise RuntimeError("handleVibrationSetting result not found")
    for i in range(start + 1, end):
        if lines[i].strip().startswith((".registers", ".locals")):
            lines.insert(i + 1, "\n" + ENTRY_BLOCK)
            return
    raise RuntimeError("determineCameraMrSensorStateLocked: no register directive")


def add_keys(lines: list[str]) -> None:
    start, end = method_range(lines, CLINIT)
    if locals_count(lines, start, end, 0) < 2:
        raise RuntimeError("<clinit>: needs v0 and v1")
    returns = [i for i in range(start, end) if lines[i].strip() == "return-void"]
    if len(returns) != 1:
        raise RuntimeError(f"<clinit>: expected one return-void, found {len(returns)}")
    if not any(f"{HANDLER}->SETTING_GLOBAL_LIST" in lines[i] for i in range(start, end)):
        raise RuntimeError("<clinit> does not fill SETTING_GLOBAL_LIST")
    lines.insert(returns[0], keys_block())


def patch(decoded_root: Path) -> tuple[Path | None, str]:
    target = find_target(decoded_root)
    if target is None:
        return None, "skipped (no CameraMrEventHandler)"
    text = target.read_text(encoding="utf-8")
    if PATCH_MARKER in text:
        return target, "already-patched"

    lines = text.splitlines(keepends=True)
    hook_state(lines)
    add_keys(lines)

    patched = "".join(lines)
    if patched.count(f"# {PATCH_MARKER}") != 3:
        raise RuntimeError("camera ring mode patch verification failed")
    target.write_text(patched, encoding="utf-8")
    return target, "patched"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("decoded_root", type=Path)
    args = parser.parse_args()

    try:
        target, status = patch(args.decoded_root.resolve())
    except (OSError, RuntimeError) as exc:
        print(f"camera ring mode patch failed: {exc}", file=sys.stderr)
        return 1

    print(f"camera ring mode {status}: {target}")
    return 0 if target is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
