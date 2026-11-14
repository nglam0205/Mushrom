#!/usr/bin/env python3
"""Copy watermarks added to the MiuiCamera APK into an existing data copy.

Usage: patch_camera_watermark_merge.py <decoded MiuiCamera dir>

MiuiCamera renders watermarks from files/watermarks (and video_watermarks) in
its data, copied once from assets/. tryCopyAssets copies again only when the
data "version" is older than the APK's, so after a ROM update without a data
wipe the phone keeps the old set and never sees watermarks the new APK adds
(the Leitzphone set leica 88-92, 111 on myron).

This makes tryCopyAssets also run the recursive copy in no-overwrite mode when
the version check passes: only files missing from data are copied, so the
watermarks already there, cloud downloads included, stay as they are. A copy
failure is caught so it cannot break watermark loading.

Method names are R8 output, so both methods are found by their log strings.
"""
import re
import sys
from pathlib import Path

METHOD = re.compile(r"(?ms)^\.method (?P<header>[^\n]*)\n(?P<body>.*?)^\.end method$")
PASS_LOG = '"assets_copied exists and version check pass"'
COPY_LOG = '"copyAssetsRecursively: "'
COPY_SIGNATURE = "(Landroid/content/Context;Ljava/io/File;Z)V"
TRY_COPY_SIGNATURE = "(Landroid/content/Context;Ljava/io/File;)V"
MARKER = ":try_start_vs_merge"


def main():
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2

    for path in Path(sys.argv[1]).glob("smali*/**/*.smali"):
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        if PASS_LOG not in text or COPY_LOG not in text:
            continue
        if MARKER in text:
            print("[watermark-merge] already patched")
            return 0

        cls = re.search(r"(?m)^\.class [^\n]*?(L[^;\s]+;)", text).group(1)
        copy = next(
            (m for m in METHOD.finditer(text)
             if m.group("header").endswith(COPY_SIGNATURE) and COPY_LOG in m.group("body")),
            None,
        )
        try_copy = next(
            (m for m in METHOD.finditer(text)
             if m.group("header").endswith(TRY_COPY_SIGNATURE) and PASS_LOG in m.group("body")),
            None,
        )
        if not copy or not try_copy or " static " not in f" {copy.group('header')} ":
            continue

        body = try_copy.group("body")
        # The pass branch starts by loading the log tag; p0 (context) and
        # p1 (target dir) are still intact there and v0 is dead.
        anchor = re.search(
            r'(?m)^(?P<indent>\s*)const-string [vp]\d+, "AssetsUtils"\n\s*\n?\s*const-string [vp]\d+, '
            + re.escape(PASS_LOG),
            body,
        )
        if not anchor:
            continue
        name = copy.group("header").split()[-1]
        merge = (
            "    const/4 v0, 0x0\n\n"
            f"    {MARKER}\n"
            f"    invoke-static {{p0, p1, v0}}, {cls}->{name}\n\n"
            "    :try_end_vs_merge\n"
            "    .catch Ljava/io/IOException; {:try_start_vs_merge .. :try_end_vs_merge} :catch_vs_merge\n\n"
            "    :catch_vs_merge\n"
        )
        start = try_copy.start("body") + anchor.start()
        text = text[:start] + merge + text[start:]
        path.write_text(text, encoding="utf-8", errors="surrogateescape")
        print(f"[watermark-merge] {path.name}: tryCopyAssets now adds missing assets via {name}")
        return 0

    print("[watermark-merge] tryCopyAssets not found; skip")
    return 1


if __name__ == "__main__":
    sys.exit(main())
