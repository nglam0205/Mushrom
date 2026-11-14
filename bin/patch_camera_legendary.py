#!/usr/bin/env python3
"""Force the Leica Legendary (M3/M9 master-lens) capabilities on in MiuiCamera.

Usage: patch_camera_legendary.py <decoded MiuiCamera dir>

The Legendary master-lens looks (Leica M3 film / M9 CCD) are rendered by the
vendor stack (legendST plugin + legend pipelines + leica_filter_param_m{3,9}
tuning). The camera app gates the feature behind capability getters annotated
  .annotation build Lcom/android/camera/jacoco/JacocoIgnore;
      key = "isSupport..."
On the plain variant those return false (only the Leica edition answers true),
so the M3/M9 entries never show even when every vendor file is present.

This rewrites every capability getter returning Z whose annotation key is in
TARGETS to simply return true. Device-gated by the caller (only run for a
device whose vendor stack actually ships the legend pipelines).
"""
import re
import sys
from pathlib import Path

TARGETS = {
    "isSupportLegendaryMode",   # the Legendary mode entry (M3/M9)
    "isSupportCvLens",          # the master-lens list those entries live in
}

METHOD = re.compile(r"(?ms)^\.method (?P<header>[^\n]*)\n(?P<body>.*?)^\.end method$")
KEY = re.compile(r'key = "([^"]+)"')


def force_true(header):
    return f".method {header}\n    .locals 1\n\n    const/4 v0, 0x1\n\n    return v0\n.end method"


def main():
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2

    root = Path(sys.argv[1])
    patched = 0
    for path in root.glob("smali*/**/*.smali"):
        text = path.read_text(encoding="utf-8", errors="surrogateescape")
        if not any(t in text for t in TARGETS):
            continue

        changed = False

        def repl(m):
            nonlocal changed
            header, body = m.group("header"), m.group("body")
            if not header.rstrip().endswith(")Z"):
                return m.group(0)
            key = KEY.search(body)
            if not key or key.group(1) not in TARGETS:
                return m.group(0)
            # Already a bare "return true"? leave it.
            if re.search(r"const/4 [vp]\d+, 0x1\s+return [vp]\d+\s*$", body):
                return m.group(0)
            changed = True
            name = header.split("(")[0].split()[-1]
            print(f"[legendary] {path.name}: {name}() {key.group(1)} -> true")
            return force_true(header)

        new = METHOD.sub(repl, text)
        if changed:
            path.write_text(new, encoding="utf-8", errors="surrogateescape")
            patched += 1

    if not patched:
        print("[legendary] no capability getter matched; skip")
        return 1
    print(f"[legendary] forced capabilities in {patched} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
