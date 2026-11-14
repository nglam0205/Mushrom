#!/usr/bin/env python3
"""Let VSTeam pick what turning the camera ring does (vs_focus_ring_cw/ccw).

Usage: patch_focus_ring.py <decoded services.jar dir>

The ring of the Xiaomi 17 Ultra by Leica is a rotary encoder (focus_ring_bridge.ko,
idc device.type = rotaryEncoder): every detent goes to the focused window as an
ACTION_SCROLL of SOURCE_ROTARY_ENCODER. While the screen is on, the only Java code
that sees such an event before dispatch, and may drop it, is the input filter of
InputManagerService. Three hooks hand it to com.android.vsteam.FocusRing:
- systemRunning() attaches it (settings observer, focused task listener);
- filterInputEvent() asks it first and drops the ring's events it consumed;
- setInputFilter() keeps the native filter on while a ring action is set, also
  when accessibility removes its filter.
With no ring action picked the filter stays off as in stock, so other input pays
nothing.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


CLASS_RELATIVE_PATH = Path("com/android/server/input/InputManagerService.smali")
IMS = "Lcom/android/server/input/InputManagerService;"
RING = "Lcom/android/vsteam/FocusRing;"
PATCH_MARKER = "VS_FOCUS_RING"

SYSTEM_RUNNING = ".method public systemRunning()V"
FILTER_EVENT = re.compile(r"^\.method (?:[\w-]+ )*filterInputEvent\(Landroid/view/InputEvent;I\)Z$")
SET_FILTER = ".method public setInputFilter(Landroid/view/IInputFilter;)V"
SET_ENABLED = re.compile(
    r"^(?P<indent>[ \t]*)invoke-interface \{(?P<native>[vp]\d+), (?P<flag>[vp]\d+)\}, "
    r"Lcom/android/server/input/NativeInputManagerService;->setInputFilterEnabled\(Z\)V$")

ATTACH_BLOCK = f"""    # {PATCH_MARKER}
    iget-object v0, p0, {IMS}->mContext:Landroid/content/Context;

    invoke-static {{p0, v0}}, {RING}->attach(Ljava/lang/Object;Landroid/content/Context;)V

"""

# p1 is the event, p2 its policy flags.
FILTER_BLOCK = f"""    # {PATCH_MARKER}
    invoke-static {{p1, p2}}, {RING}->filter(Landroid/view/InputEvent;I)Z

    move-result v0

    if-eqz v0, :cond_vs_focus_ring_pass

    const/4 v0, 0x0

    return v0

    :cond_vs_focus_ring_pass
"""


def find_target(decoded_root: Path) -> Path | None:
    matches = sorted(decoded_root.glob(f"smali*/{CLASS_RELATIVE_PATH}"))
    if len(matches) > 1:
        raise RuntimeError(f"expected one InputManagerService.smali, found {len(matches)}")
    return matches[0] if matches else None


def method_start(lines: list[str], matches) -> int:
    found = [i for i, line in enumerate(lines) if matches(line.rstrip("\n"))]
    if len(found) != 1:
        raise RuntimeError(f"expected one method, found {len(found)}")
    return found[0]


def method_end(lines: list[str], start: int) -> int:
    for i in range(start + 1, len(lines)):
        if lines[i].strip() == ".end method":
            return i
    raise RuntimeError("method has no end")


def first_instruction(lines: list[str], start: int) -> int:
    """Index of the first code line of the method starting at ``start``."""
    in_annotation = False
    for i in range(start + 1, method_end(lines, start)):
        stripped = lines[i].strip()
        if stripped.startswith(".annotation"):
            in_annotation = True
            continue
        if stripped.startswith(".end annotation"):
            in_annotation = False
            continue
        if in_annotation or not stripped or stripped.startswith((".", "#", ":")):
            continue
        return i
    raise RuntimeError("method has no code")


def has_local_v0(lines: list[str], start: int, params: int) -> bool:
    """Whether v0 is a local (not a parameter) and p0..p2 stay below v16."""
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith(".registers"):
            n = int(stripped.split()[1])
            return n - params >= 1 and n <= 16
        if stripped.startswith(".locals"):
            n = int(stripped.split()[1])
            return n >= 1 and n + params <= 16
        if stripped == ".end method":
            break
    return False


def insert_at_entry(lines: list[str], matches, params: int, block: str, name: str) -> None:
    start = method_start(lines, matches)
    if not has_local_v0(lines, start, params):
        raise RuntimeError(f"{name}: no free v0")
    lines.insert(first_instruction(lines, start), block + "\n")


def gate_filter_enabled(lines: list[str]) -> None:
    start = method_start(lines, lambda line: line == SET_FILTER)
    end = method_end(lines, start)
    hits = [i for i in range(start, end) if SET_ENABLED.match(lines[i].rstrip("\n"))]
    if len(hits) != 1:
        raise RuntimeError(f"setInputFilter: expected one setInputFilterEnabled, found {len(hits)}")
    match = SET_ENABLED.match(lines[hits[0]].rstrip("\n"))
    indent, flag = match["indent"], match["flag"]
    lines.insert(hits[0], (
        f"{indent}# {PATCH_MARKER}\n"
        f"{indent}invoke-static {{{flag}}}, {RING}->filterEnabled(Z)Z\n\n"
        f"{indent}move-result {flag}\n\n"))


def patch(decoded_root: Path) -> tuple[Path | None, str]:
    target = find_target(decoded_root)
    if target is None:
        return None, "skipped (no InputManagerService)"
    text = target.read_text(encoding="utf-8")
    if PATCH_MARKER in text:
        return target, "already-patched"

    lines = text.splitlines(keepends=True)
    # setInputFilter first: the entry hooks below shift its lines.
    gate_filter_enabled(lines)
    insert_at_entry(lines, lambda line: FILTER_EVENT.match(line) is not None, 3, FILTER_BLOCK,
                    "filterInputEvent")
    insert_at_entry(lines, lambda line: line == SYSTEM_RUNNING, 1, ATTACH_BLOCK, "systemRunning")

    patched = "".join(lines)
    if patched.count(f"# {PATCH_MARKER}") != 3:
        raise RuntimeError("focus ring patch verification failed")
    target.write_text(patched, encoding="utf-8")
    return target, "patched"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("decoded_root", type=Path)
    args = parser.parse_args()

    try:
        target, status = patch(args.decoded_root.resolve())
    except (OSError, RuntimeError) as exc:
        print(f"focus ring patch failed: {exc}", file=sys.stderr)
        return 1

    print(f"focus ring {status}: {target}")
    return 0 if target is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
