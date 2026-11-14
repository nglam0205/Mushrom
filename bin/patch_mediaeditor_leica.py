#!/usr/bin/env python3
"""Make MediaEditor treat the phone as a Leica device.

Usage: patch_mediaeditor_leica.py <decoded MediaEditor dir>

The editor's watermark list comes from the cloud config watermark_config_v3,
where every category and watermark names the device groups it is offered to
(leica_devices, xiaomi_devices, lcc_cn_devices, ...). leica_devices is
answered by one check (ix/b.b() in this build): Build.DEVICE has to be in the
editor's Leica list AND Build.BRAND has to be "xiaomi". So a Redmi/POCO phone
never gets the Leica watermarks, even with DEVICE spoofed to a Leica model.
The same check also adds the local Leica frame tabs.

The class and its strings are obfuscated, so the check is found from its one
readable caller: CloudWatermarkMaker builds DeviceInfo with
    invoke-static {}, L<check>;-><name>()Z ... const-string vN, "leica"
That method is rewritten to return true.

The Leitzphone (lcc_*) checks want ro.theme_customize=LCC, which cannot be
set system-wide (MiuiCamera then takes the phone for a Leitzphone and
crashes). The editor reads it only through its SystemPropertiesCompat
wrapper, so that wrapper answers "LCC" for it. The other reader of that
prop turns on C2PA signing on save, which needs the Leitzphone's own
service; that flag is kept off as on stock.
"""
import re
import sys
from pathlib import Path

CALLER = re.compile(
    r'invoke-static \{\}, (L[^;]+;)->(\w+)\(\)Z\s+move-result (v\d+)\s+'
    r'if-eqz \3, :\w+\s+const-string \3, "leica"'
)
METHOD = r"(?ms)^\.method (public |private )?static (final )?{name}\(\)Z\n.*?^\.end method$"
FORCED = re.compile(r"\.locals \d+\s+const/4 v0, 0x1\s+return v0\s+\.end method$")


THEME_PROP = "ro.theme_customize"
PROP_GET = re.compile(
    r"(?ms)^\.method public static (?:final )?\w+\(Ljava/lang/String;Ljava/lang/String;\)Ljava/lang/String;\n"
    r"\s*\.locals \d+\n.*?^\.end method$"
)
THEME_HOOK = f"""
    const-string v0, "{THEME_PROP}"

    invoke-virtual {{v0, p0}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v0

    if-eqz v0, :vs_not_theme_customize

    const-string v0, "LCC"

    return-object v0

    :vs_not_theme_customize
"""
# C2PA on save: static flag = (theme_customize equalsIgnoreCase "LCC").
C2PA_FLAG = re.compile(r"(invoke-static \{(v\d+), v\d+, v\d+\}, L[^;]+;->\w+\(Ljava/lang/String;Ljava/lang/String;Z\)Z\s+"
                       r"move-result \2\s+)(sput-boolean \2, L[^;]+;->\w+:Z)")


def spoof_theme_customize(root):
    for path in root.glob("smali*/**/*.smali"):
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        if '"SystemPropertiesCompat"' not in text or '"android.os.SystemProperties"' not in text:
            continue
        for m in PROP_GET.finditer(text):
            method = m.group(0)
            if '"android.os.SystemProperties"' not in method:
                continue
            if THEME_PROP in method:
                return "already"
            head, rest = method.split("\n", 1)
            locals_line, body = rest.split("\n", 1)
            new = f"{head}\n{locals_line}\n{THEME_HOOK}{body}"
            path.write_text(text[:m.start()] + new + text[m.end():], encoding="utf-8", errors="surrogateescape")
            return path.name
    return None


def keep_c2pa_off(root):
    for path in root.glob("smali*/**/*.smali"):
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        if '"setTitle ret = "' not in text:
            continue
        clinit = re.search(r"(?ms)^\.method static constructor <clinit>\(\)V\n.*?^\.end method$", text)
        if not clinit:
            return None
        body = clinit.group(0)
        if "# vs: C2PA off" in body:
            return "already"
        new, n = C2PA_FLAG.subn(lambda m: f"{m.group(1)}const/4 {m.group(2)}, 0x0  # vs: C2PA off\n\n    {m.group(3)}", body, count=1)
        if not n:
            return None
        path.write_text(text[:clinit.start()] + new + text[clinit.end():], encoding="utf-8", errors="surrogateescape")
        return path.name
    return None


def find_check(root):
    for path in root.glob("smali*/**/CloudWatermarkMaker.smali"):
        m = CALLER.search(path.read_text(encoding="utf-8", errors="surrogateescape"))
        if m:
            return m.group(1), m.group(2)
    return None


def main():
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    root = Path(sys.argv[1])

    check = find_check(root)
    if not check:
        print("[editor-leica] Leica device check not found; skip")
        return 1
    cls, name = check

    # The C2PA flag first: without it the LCC answer would turn signing on.
    c2pa = keep_c2pa_off(root)
    if not c2pa:
        print("[editor-leica] C2PA flag not found; ro.theme_customize left alone")
    else:
        theme = spoof_theme_customize(root)
        print(f"[editor-leica] ro.theme_customize -> LCC: {theme or 'prop getter not found'}")

    rel = cls[1:-1] + ".smali"
    path = next((p for p in root.glob(f"smali*/{rel}")), None)
    if not path:
        print(f"[editor-leica] {cls} not found; skip")
        return 1

    text = path.read_text(encoding="utf-8", errors="surrogateescape")
    m = re.search(METHOD.format(name=re.escape(name)), text)
    if not m:
        print(f"[editor-leica] {cls}->{name}()Z not found; skip")
        return 1
    if FORCED.search(m.group(0)):
        print(f"[editor-leica] {cls}->{name}()Z already true")
        return 0

    header = m.group(0).split("\n", 1)[0]
    body = f"{header}\n    .locals 1\n\n    const/4 v0, 0x1\n\n    return v0\n.end method"
    path.write_text(text[:m.start()] + body + text[m.end():], encoding="utf-8", errors="surrogateescape")
    print(f"[editor-leica] {cls}->{name}()Z -> true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
