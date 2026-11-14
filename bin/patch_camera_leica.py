#!/usr/bin/env python3
"""Give a non-Leica device the Leica camera profile in MiuiCamera.

Usage: patch_camera_leica.py <decoded MiuiCamera dir> <device codename>

MiuiCamera picks one class per device (com.mi.device.<Codename>, class names
XOR-obfuscated) and asks it for every capability. Leica models differ from
their non-Leica siblings (Corot/Corot_pro, Rothko/Rothko_pro) in four answers:
Leica looks (isSupportCvType), the Leica brand flag behind watermark/guide/EXIF,
the filter set and the default watermark id. This overrides those four in the
target device's class.

Method names are R8 output and change per release, so each one is located by
the code that consumes it, and the Leica values are read from the Leica classes
shipped in the same APK instead of being hard-coded.
"""
import re
import sys
from collections import Counter
from pathlib import Path


COMMON = "com.mi.device.common.Common"
LEICA_REFERENCE = "com.mi.device.xiaomi.CommonFlagship"
METHOD = re.compile(r"(?ms)^\.method (?P<header>[^\n]*)\n(?P<body>.*?)^\.end method$")
SUPER = re.compile(r"(?m)^\.super L([^;]+);")
# Filter-set ids come in pairs within a generation: the non-Leica set and the
# Leica set one below it (Corot 0x11 / Corot_pro 0x10, Rothko 0x11 / Rothko_pro 0x10).
LEICA_FILTER_SET = {0x11: 0x10}


def decode(name):
    # Obfuscated names are the real name XORed with one constant; every device
    # class starts with "com.".
    if name.isascii() or not name:
        return name
    key = ord(name[0]) ^ ord("c")
    return "".join(chr(ord(char) ^ key) for char in name)


class Smali:
    def __init__(self, path):
        self.path = path
        self.text = path.read_text(encoding="utf-8", errors="surrogateescape")
        self.name = decode(path.stem)
        parent = SUPER.search(self.text)
        self.parent = decode(parent.group(1)) if parent else ""

    def method(self, signature):
        for match in METHOD.finditer(self.text):
            if match.group("header").split()[-1] == signature:
                return match
        return None


def load_device_classes(root):
    classes = {}
    for path in root.glob("smali*/*.smali"):
        if path.stem.isascii():
            continue
        smali = Smali(path)
        if smali.name.startswith("com.mi.device."):
            classes[smali.name] = smali
    return classes


def common_calls(body, common_descriptor, returns):
    pattern = re.compile(
        r"invoke-virtual \{[^}]*\}, " + re.escape(common_descriptor)
        + r"->(\w+)\(\)" + re.escape(returns) + r"\s*$",
        re.M,
    )
    return pattern.findall(body)


def resolve_methods(root, common_descriptor):
    """Find the four capability getters by what their callers do with them."""
    looks, brand, watermark, filters = Counter(), Counter(), Counter(), Counter()
    for path in root.glob("smali*/**/*.smali"):
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        if common_descriptor not in text:
            continue
        for match in METHOD.finditer(text):
            body = match.group("body")
            if common_descriptor not in body:
                continue
            if 'key = "isSupportCvType"' in body:
                calls = set(common_calls(body, common_descriptor, "Z"))
                if len(calls) == 1:
                    looks.update(calls)
            if '"NoMadrid"' in body:
                brand.update(common_calls(body, common_descriptor, "Z")[:1])
            if '"dynamic_ddfid: "' in body:
                head = body.split('"dynamic_ddfid: "')[0]
                watermark.update(common_calls(head, common_descriptor, "I")[-1:])
            if '"LEICA"' in body:
                filters.update(set(common_calls(body, common_descriptor, "[I")))

    def best(counter, suffix):
        return f"{counter.most_common(1)[0][0]}(){suffix}" if counter else None

    return {
        "leica looks": best(looks, "Z"),
        "leica brand": best(brand, "Z"),
        "default watermark": best(watermark, "I"),
        "filter set": best(filters, "[I"),
    }


def inherited(classes, name, signature):
    while name in classes:
        match = classes[name].method(signature)
        if match:
            return match
        name = classes[name].parent
    return None


def constant_in(match):
    found = re.search(r"const(?:/\w+)? [vp]\d+, (-?0x[0-9a-fA-F]+)", match.group("body")) if match else None
    return int(found.group(1), 16) if found else None


def build(signature, value):
    if signature.endswith(")[I"):
        body = (
            "    .locals 1\n\n"
            f"    const/16 v0, {hex(value)}\n\n"
            "    filled-new-array {v0}, [I\n\n"
            "    move-result-object v0\n\n"
            "    return-object v0\n"
        )
    else:
        body = f"    .locals 1\n\n    const v0, {hex(value)}\n\n    return v0\n"
    return f".method public {signature}\n{body}.end method"


def set_method(smali, signature, value):
    replacement = build(signature, value)
    existing = smali.method(signature)
    if existing:
        smali.text = smali.text[: existing.start()] + replacement + smali.text[existing.end():]
    else:
        smali.text = smali.text.rstrip("\n") + "\n\n" + replacement + "\n"


def unseal(classes, name, signature):
    # A final declaration in an ancestor would make the new override illegal.
    while name in classes:
        smali = classes[name]
        match = smali.method(signature)
        if match and " final " in f" {match.group('header')} ":
            header = match.group("header").replace("final ", "", 1)
            smali.text = (
                smali.text[: match.start("header")] + header + smali.text[match.end("header"):]
            )
            smali.dirty = True
        name = smali.parent


def main():
    if len(sys.argv) != 3 or not Path(sys.argv[1]).is_dir():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2

    root, codename = Path(sys.argv[1]), sys.argv[2].lower()
    classes = load_device_classes(root)
    target = next(
        (smali for name, smali in classes.items() if name.lower() == f"com.mi.device.{codename}"),
        None,
    )
    if COMMON not in classes or target is None:
        print(f"[leica] no device class for '{codename}' in this camera build; skip")
        return 0

    common_descriptor = f"L{classes[COMMON].path.stem};"
    methods = resolve_methods(root, common_descriptor)
    reference = LEICA_REFERENCE if LEICA_REFERENCE in classes else None

    wanted = {}
    for label in ("leica looks", "leica brand"):
        if methods[label]:
            wanted[label] = (methods[label], 1)
    if methods["default watermark"] and reference:
        value = constant_in(inherited(classes, reference, methods["default watermark"]))
        if value:
            wanted["default watermark"] = (methods["default watermark"], value)
    if methods["filter set"]:
        current = constant_in(inherited(classes, target.name, methods["filter set"]))
        if current in LEICA_FILTER_SET:
            wanted["filter set"] = (methods["filter set"], LEICA_FILTER_SET[current])
        elif current is not None:
            print(f"[leica] filter set {hex(current)} has no known Leica counterpart; left as is")

    for label in methods:
        if label not in wanted and label != "filter set":
            print(f"[leica] could not resolve {label}; left as is")

    for label, (signature, value) in wanted.items():
        current = constant_in(inherited(classes, target.name, signature))
        unseal(classes, target.parent, signature)
        set_method(target, signature, value)
        target.dirty = True
        was = "?" if current is None else hex(current)
        print(f"[leica] {target.name}: {label} {signature} {was} -> {hex(value)}")

    for smali in classes.values():
        if getattr(smali, "dirty", False):
            smali.path.write_text(smali.text, encoding="utf-8", errors="surrogateescape")

    return 0 if wanted else 1


if __name__ == "__main__":
    sys.exit(main())
