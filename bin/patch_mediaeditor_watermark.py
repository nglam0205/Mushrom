#!/usr/bin/env python3
"""Offer MediaEditor every watermark the camera can take.

Usage: patch_mediaeditor_watermark.py <decoded MediaEditor dir>

MediaEditor lists watermarks from files/watermarks in its data, copied once
from assets/cloud_watermark. That set is older than the camera's: leica stops
at 1-5, so the Leitzphone set (leica 88-92, 111) and the newer classic, film
and standard ids never show in the editor. The APK also ships the camera's
full set in assets/watermarks, but only reads ranges.json from there.

This copies every watermark folder missing from assets/cloud_watermark out of
assets/watermarks (the ones already there stay as they are), then renames the
"copied once" preference key in CloudWatermarkMaker so a phone that already
has the old data copy makes a fresh one with the added watermarks.

The LimitationUtil filters that hide most of them are opened up separately
by patch_camera_watermark_all.py.

The list shown in the editor is a second filter on top of that data: the
cloud config watermark_config_v3 offers each watermark only to some device
groups, regions, dates and apps (the Leitzphone set never reaches it, even
on a spoofed nezha with ro.theme_customize=LCC). Watermarks whose id is in
the exceptFilterWatermarkIds list (the one already on the photo) skip those
checks, so every watermark is sent down that path. The renderer version
check before it stays, and that path moves on to the next watermark, so none
is added twice.

Applying a watermark then checks the photo itself: a Leica one only goes on
a photo whose EXIF says it was shot by a Leica (or Leitzphone) Xiaomi, so on
Redmi/POCO it fails with "cannot recognize the parameters". The editor skips
that check when persist.mediaeditor.watermark.skip_exif_check is true (a
debug prop shell cannot set), so that read is made to return true. The
checks that the photo has the shooting parameters the watermark prints stay.
Its strings are XOR-obfuscated; the key is read from the decoder.
"""
import re
import shutil
import sys
from pathlib import Path

LOADED_KEY = '"watermark_is_local_resource_loaded"'
NEW_KEY = '"watermark_is_local_resource_loaded_vs"'


# if (renderVersion <= 2.13) { if (exceptIds.contains(id)) { add; continue } ...
EXCEPT = re.compile(
    r"(const-wide v\d+, 0x40010a3d70a3d70aL\s+# 2\.13\s+"
    r"cmpl-double (v\d+), v\d+, \2\s+if-ltz \2, :\w+\s+"
    r"iget-object \2, v\d+, L[^;]+;->\w+:Ljava/lang/String;\s+"
    r"move-object/from16 (v\d+), p1\s+"
    r"invoke-interface \{\3, \2\}, Ljava/util/List;->contains\(Ljava/lang/Object;\)Z\s+"
    r"move-result \2\s+)if-eqz \2, :\w+\s+"
    r"(?=invoke-virtual \{v\d+, v\d+\}, Ljava/util/ArrayList;->add\(Ljava/lang/Object;\)Z\s+goto)"
)


def open_cloud_filter(root):
    for path in root.glob("smali*/**/*.smali"):
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        if "0x40010a3d70a3d70aL" not in text:
            continue
        new, n = EXCEPT.subn(r"\1", text)
        if n:
            path.write_text(new, encoding="utf-8", errors="surrogateescape")
            return path.name
    return None


SKIP_EXIF_PROP = "persist.mediaeditor.watermark.skip_exif_check"
DECODER = re.compile(
    r"(?ms)^\.method public static \w+\(Ljava/lang/String;\)Ljava/lang/String;\n"
    r"(?:(?!^\.end method).)*?Landroid/util/LruCache;"
    r"(?:(?!^\.end method).)*?const (v\d+), (-?0x[0-9a-f]+)\s+xor-int/2addr v\d+, \1"
)
OBF_STR = re.compile(r'const-string (v\d+), "((?:\\u[0-9a-f]{4})+)"')


def find_xor_key(root):
    for path in root.glob("smali*/**/*.smali"):
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        if "xor-int/2addr" not in text or "LruCache" not in text:
            continue
        m = DECODER.search(text)
        if m:
            return int(m.group(2), 16) & 0xFFFF
    return None


def skip_exif_check(root):
    key = find_xor_key(root)
    if key is None:
        return None
    encoded = "".join(f"\\u{ord(c) ^ key:04x}" for c in SKIP_EXIF_PROP)
    get = re.compile(
        r'(const-string (v\d+|p\d+), "' + re.escape(encoded) + r'"'
        r'(?:(?!\.end method)[\s\S]){0,400}?'
        r'invoke-static \{(?:v|p)\d+, (?:v|p)\d+\}, Lmiuix/core/util/SystemProperties;->getBoolean\(Ljava/lang/String;Z\)Z\s+'
        r'move-result ((?:v|p)\d+))(?!\s+const/4 \3, 0x1)'
    )
    for path in root.glob("smali*/**/*.smali"):
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        if encoded not in text:
            continue
        new, n = get.subn(lambda m: f"{m.group(1)}\n\n    const/4 {m.group(3)}, 0x1", text)
        if n:
            path.write_text(new, encoding="utf-8", errors="surrogateescape")
            return path.name
        return "already"
    return None


def merge_assets(root):
    src = root / "assets" / "watermarks"
    dst = root / "assets" / "cloud_watermark"
    if not src.is_dir() or not dst.is_dir():
        return None

    added = []
    for group in sorted(p for p in src.iterdir() if p.is_dir()):
        target = dst / group.name
        if not target.is_dir():
            # A whole group the editor does not know; its group config and
            # i18n come along.
            shutil.copytree(group, target)
            added.append(f"{group.name}/*")
            continue
        for item in sorted(p for p in group.iterdir() if p.is_dir()):
            if not (target / item.name).exists():
                shutil.copytree(item, target / item.name)
                added.append(f"{group.name}/{item.name}")
    return added


def bump_loaded_key(root):
    for path in root.glob("smali*/**/CloudWatermarkMaker.smali"):
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        if NEW_KEY in text:
            return True
        if LOADED_KEY not in text:
            continue
        path.write_text(text.replace(LOADED_KEY, NEW_KEY), encoding="utf-8", errors="surrogateescape")
        return True
    return False


def main():
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    root = Path(sys.argv[1])

    opened = open_cloud_filter(root)
    if opened:
        print(f"[editor-watermark] cloud list filter opened in {opened}")
    else:
        print("[editor-watermark] cloud list filter not found (or already open)")

    skipped = skip_exif_check(root)
    print(f"[editor-watermark] photo EXIF brand check skipped: {skipped or 'not found'}")

    added = merge_assets(root)
    if added is None:
        print("[editor-watermark] assets/watermarks or assets/cloud_watermark missing; skip")
        return 1
    if not added:
        print("[editor-watermark] cloud_watermark already has every watermark")
        return 0
    print(f"[editor-watermark] added: {', '.join(added)}")

    if not bump_loaded_key(root):
        # The assets still reach a phone whose editor data is fresh.
        print("[editor-watermark] copy-once key not found; existing data keeps the old set")
    return 0


if __name__ == "__main__":
    sys.exit(main())
