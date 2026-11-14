#!/usr/bin/env python3
"""Let VSTeam pick what the stylus button does (vs_pencil_*_action).

Usage: patch_oplus_pencil_button.py <decoded OplusExSystemService dir>

ExSystemService turns the pencil button into gestures in two places:
- the KEY_F21 handler of pencils with a physical button (OPPO Pencil 2/3,
  OnePlus Pencil): handleMessage gets what=1 single click, 2 double click,
  3 long press start, 4 long press end; every press and release also goes
  to healthservice for smart collect, which a custom long press replaces;
- the GATT notify of the Pro pencils: handlePencilSingleClickFromGatt and
  handlePencilDoubleClickFromGatt.
Each one now asks Toolbox's sibling com.android.vsteam.PencilKey first and
returns when it ran the action picked in VSTeam; otherwise the stock
broadcast follows. Classes are obfuscated, so the methods are found by
their log strings.
"""
import re
import sys
from pathlib import Path


PENCIL = "Lcom/android/vsteam/PencilKey;"
MARKER = PENCIL
METHOD = re.compile(r"(?ms)^\.method (?P<header>[^\n]*)\n(?P<body>.*?)^\.end method$")
REGISTERS = re.compile(r"^(?P<indent>[ \t]*)\.(?P<kind>registers|locals) (?P<n>\d+)$", re.M)
KEY_HANDLER = "keycode_F21"
SMART_COLLECT = "KEY_PENCIL_LONG_CLICK settingValue "
# (label, string the method logs, PencilKey gesture)
GATT = (
    ("GATT single click", "handlePencilSingleClickFromGatt clickMode=", 1),
    ("GATT double click", "handlePencilDoubleClickFromGatt clickMode=", 2),
)


def param_regs(header):
    """Registers taken by the parameters, including `this`."""
    args = re.search(r"\((.*?)\)", header)[1]
    count = 0 if " static " in f" {header}" else 1
    for arg in re.findall(r"\[*(?:L[^;]+;|.)", args):
        count += 2 if arg in ("J", "D") else 1
    return count


def add_scratch(body, header):
    """Grow the frame by one local and return (body, that register, its insert point).

    Named locals alias the parameters, so a fresh register is the only one
    sure to be dead; it goes right after the .locals line."""
    match = REGISTERS.search(body)
    n = int(match["n"])
    local = n if match["kind"] == "locals" else n - param_regs(header)
    if local > 15:
        return None
    line = f"{match['indent']}.{match['kind']} {n + 1}"
    body = body[: match.start()] + line + body[match.end():]
    return body, f"v{local}", match.start() + len(line) + 1


def hook(body, at, lines, scratch, label):
    skip = f":vs_pencil_stock_{label}"
    lines = [line.replace("{s}", scratch) for line in lines]
    lines += [f"if-eqz {scratch}, {skip}", "return-void"]
    return body[:at] + "\n" + "".join(f"    {line}\n\n" for line in lines) + f"    {skip}\n" + body[at:]


def patch_method(text, match, lines, label):
    """Return text with `lines` run first in the method, or None."""
    grown = add_scratch(match["body"], match["header"])
    if grown is None:
        print(f"[pencil] {label}: no low register free; skip")
        return None
    body, scratch, at = grown
    print(f"[pencil] {label}: hooked")
    return text[: match.start("body")] + hook(body, at, lines, scratch, label) + text[match.end("body"):]


def patch_file(path):
    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    if MARKER in text:
        print(f"[pencil] already hooked: {path.name}")
        return 0, True
    done = 0
    pos = 0
    while match := METHOD.search(text, pos):
        header, body = match["header"], match["body"]
        patched = None
        if ("handleMessage(Landroid/os/Message;)V" in header and KEY_HANDLER in body
                and "Landroid/os/Message;->what:I" in body):
            patched = patch_method(text, match, [
                "iget {s}, p1, Landroid/os/Message;->what:I",
                "invoke-static {{s}}, " + PENCIL + "->onKeyMessage(I)Z",
                "move-result {s}",
            ], "key")
        elif header.endswith("(I)V") and f'"{SMART_COLLECT}"' in body:
            patched = patch_method(text, match, [
                "invoke-static {}, " + PENCIL + "->ownsLongPress()Z",
                "move-result {s}",
            ], "smart_collect")
        else:
            for label, log, gesture in GATT:
                if f'"{log}"' in body and header.endswith(")V"):
                    patched = patch_method(text, match, [
                        f"const/4 {{s}}, 0x{gesture}",
                        "invoke-static {{s}}, " + PENCIL + "->onGattClick(I)Z",
                        "move-result {s}",
                    ], label.replace(" ", "_").lower())
                    break
        if patched is None:
            pos = match.end()
            continue
        pos = match.end() + len(patched) - len(text)
        text = patched
        done += 1
    if done:
        path.write_text(text, encoding="utf-8", errors="surrogateescape")
    return done, False


def main():
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2

    root = Path(sys.argv[1])
    candidates = set()
    for smali in root.glob("smali*/**/*.smali"):
        data = smali.read_bytes()
        if any(s in data for s in (KEY_HANDLER.encode(), SMART_COLLECT.encode(), b"FromGatt clickMode=")):
            candidates.add(smali)
    if not candidates:
        print("[pencil] stylus button handlers not present in this build")
        return 1

    total = 0
    for smali in sorted(candidates):
        done, already = patch_file(smali)
        if already:
            return 0
        total += done
    if not total:
        print("[pencil] no stylus button handler matched")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
