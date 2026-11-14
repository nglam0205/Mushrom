#!/usr/bin/env python3
"""Let screen-off "Hey Google" run on the DSP: read the hotword module version in PAL.

Usage: patch_hotword_module_version.py <img_temp dir>

GSA only loads its DSP model when the Sound Trigger HAL reports supportedModelArch
("<module version>, <platform uuid>"); otherwise it falls back to software detection, which
stops when the screen turns off (dumpsys voiceinteraction: DetectorType=trusted_hotword_software).
PAL fills that from the ADSP only when the HOTWORD_VOICE_UI stream of resourcemanager.xml has
get_module_version="true". Xiaomi ships per-device copies in odm/etc/audio/sku_*/: true on
myron, athens and songyuan (DSP hotword works there), false on nezha, pudding, pandora and
popsicle although their hotword block is otherwise the same.

Only the HOTWORD_VOICE_UI stream is touched. Run for every HyperOS build (checked on nezha: DSP
detection with the screen off, no ADSP restart). Not for ColorOS: on the OPPO Pad (opd2515) the
same switch made the ADSP crash-loop once the screen went off, a firmware limit.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

STREAM = re.compile(r"<stream_config>(?:(?!</stream_config>).)*?</stream_config>", re.S)
HOTWORD_NAME = "<name>HOTWORD_VOICE_UI</name>"
FLAG = re.compile(r'(<param\s+get_module_version=")false(")')


def patch_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    changed = 0
    found = 0

    def fix(match: re.Match) -> str:
        nonlocal changed, found
        block = match.group(0)
        if HOTWORD_NAME not in block:
            return block
        found += 1
        new_block, count = FLAG.subn(r"\1true\2", block)
        changed += count
        return new_block

    patched = STREAM.sub(fix, text)
    if not found:
        return "no HOTWORD_VOICE_UI"
    if not changed:
        return "already true"
    path.write_text(patched, encoding="utf-8")
    return "patched"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image_root", type=Path)
    args = parser.parse_args()

    files = sorted((args.image_root / "odm").glob("etc/audio/sku_*/resourcemanager_*.xml"))
    if not files:
        print("hotword module version: no odm resourcemanager xml, skip")
        return 0
    try:
        for path in files:
            print(f"hotword module version: {patch_file(path)}: {path.relative_to(args.image_root)}")
    except OSError as exc:
        print(f"hotword module version patch failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
