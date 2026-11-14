#!/usr/bin/env python3
"""Let the EXIF Model/Make written by MiuiCamera follow the VSTeam device name.

Usage: patch_camera_exif_name.py <decoded MiuiCamera dir>

Two writers have to be covered:

* Most shots are encoded by the camera HAL, which fills Make/Model from system
  properties the app cannot change. Those reach the app as finished JPEG data
  in a ByteBuffer handed to its Storage class, so every Storage method taking
  one passes it through Toolbox.exifPatch first.
* When the app builds the EXIF itself it reads ro.product.marketname (cached in
  a static field) or Build.MODEL, and Build.MANUFACTURER. The cached market
  name also drives cloud requests and feature checks, so only the reads inside
  the methods that write the two tags go through Toolbox.exifModel / exifMake.
"""
import re
import sys
from pathlib import Path


TOOLBOX = "Lcom/android/vsteam/Toolbox;"
STRING = "Ljava/lang/String;"
METHOD = re.compile(r"(?ms)^(?P<header>\.method [^\n]*\n)(?P<body>.*?)^\.end method$")
MARKET_NAME_FIELD = re.compile(
    r'const-string(?:/jumbo)? [vp]\d+, "ro\.product\.marketname"'
    r"(?:(?!\.end method).)*?sput-object [vp]\d+, (L[^;]+;->\w+:Ljava/lang/String;)",
    re.S,
)
SGET = r"(?m)^(?P<indent>\s*)sget-object (?P<reg>[vp]\d+), {field}\s*$"


def reroute(body, field, method):
    def add(match):
        reg = match.group("reg")
        return (
            f"{match.group(0)}\n\n"
            f"{match.group('indent')}invoke-static {{{reg}}}, {TOOLBOX}->{method}({STRING}){STRING}\n\n"
            f"{match.group('indent')}move-result-object {reg}"
        )

    return re.subn(SGET.format(field=re.escape(field)), add, body)


BUFFER = "Ljava/nio/ByteBuffer;"
PARAM = re.compile(r"\[*(?:[ZBCSIFJD]|L[^;]+;)")


def buffer_registers(header):
    """p-registers holding ByteBuffer parameters of a static method header."""
    signature = header.split()[-1]
    params = signature[signature.index("(") + 1: signature.index(")")]
    registers, index = [], 0
    for param in PARAM.findall(params):
        if param == BUFFER:
            registers.append(f"p{index}")
        index += 2 if param in ("J", "D") else 1
    return registers


def hook_storage(root, texts_by_path):
    """Route finished image data through Toolbox.exifPatch before it is saved."""
    hooked = 0
    for path, text in texts_by_path.items():
        if '"Storage.addImage"' not in text:
            continue
        if "Toolbox;->exifPatch" in text:
            print(f"[exif] already hooked: {path.relative_to(root)} (storage)")
            return 1

        def patch(match):
            nonlocal hooked
            header, body = match.group("header"), match.group("body")
            if " static " not in header or " abstract " in header or " native " in header:
                return match.group(0)
            lines = body.split("\n")
            first = next(
                (i for i, line in enumerate(lines)
                 if line.strip() and not line.strip().startswith(".")),
                None,
            )
            registers = buffer_registers(header.strip())
            if first is None or not registers:
                return match.group(0)
            # /range form: parameter registers can sit above v15.
            hook = "".join(
                f"    invoke-static/range {{{reg} .. {reg}}}, {TOOLBOX}->exifPatch({BUFFER}){BUFFER}\n\n"
                f"    move-result-object {reg}\n\n"
                for reg in registers
            )
            lines.insert(first, hook.rstrip("\n") + "\n")
            hooked += len(registers)
            return header + "\n".join(lines) + ".end method"

        patched = METHOD.sub(patch, text)
        if hooked:
            path.write_text(patched, encoding="utf-8", errors="surrogateescape")
            texts_by_path[path] = patched
            print(f"[exif] hooked {path.relative_to(root)}: {hooked} image buffer(s)")
        return hooked
    print("[exif] Storage class not found; HAL-encoded photos keep the stock name")
    return 0


def main():
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2

    root = Path(sys.argv[1])
    paths = list(root.glob("smali*/**/*.smali"))
    texts = {}
    storage = {}
    market_field = None
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        if '"ro.product.marketname"' in text and market_field is None:
            found = MARKET_NAME_FIELD.search(text)
            market_field = found.group(1) if found else None
        if '"Model"' in text or '"Make"' in text:
            texts[path] = text
        if '"Storage.addImage"' in text:
            storage[path] = text

    model_sources = ["Landroid/os/Build;->MODEL:Ljava/lang/String;"]
    if market_field:
        model_sources.append(market_field)
    else:
        print("[exif] cached market name field not found; only Build.MODEL is rerouted")

    models = makes = 0
    for path, text in texts.items():
        if "Toolbox;->exifM" in text:
            print(f"[exif] already hooked: {path.relative_to(root)}")
            models += 1
            continue

        def patch(match):
            nonlocal models, makes
            body = match.group("body")
            # Only methods that both name the tag and read the device identity.
            if '"Model"' in body:
                for field in model_sources:
                    body, count = reroute(body, field, "exifModel")
                    models += count
            if '"Make"' in body:
                body, count = reroute(
                    body, "Landroid/os/Build;->MANUFACTURER:Ljava/lang/String;", "exifMake"
                )
                makes += count
            return match.group("header") + body + ".end method"

        patched = METHOD.sub(patch, text)
        if patched != text:
            path.write_text(patched, encoding="utf-8", errors="surrogateescape")
            print(f"[exif] hooked {path.relative_to(root)}")

    print(f"[exif] model reads: {models}, make reads: {makes}")
    buffers = hook_storage(root, storage)
    return 0 if models or buffers else 1


if __name__ == "__main__":
    sys.exit(main())
