#!/usr/bin/env python3
"""Make OPlus InputManager honor VSTools' touch report-rate setting."""

from __future__ import annotations

import re
import sys
from pathlib import Path


METHOD_RE = re.compile(
    r"(?ms)^(?P<header>\.method[^\n]+)\n(?P<body>.*?)^\.end method$"
)
TARGET_DESCRIPTOR = "(Ljava/lang/String;)V"
PATCH_MARKER = "VS_PATCH_OPLUS_TOUCH_REPORT_RATE"
REQUIRED_MARKERS = (
    "mCurrentReportRate:Ljava/lang/String;",
    "mRateStrategySimple:Z",
    "OplusTouchNodeManager;->writeNodeFileByDevice(IILjava/lang/String;)Z",
    "0xb6",
)
INJECT = """
    # VS_PATCH_OPLUS_TOUCH_REPORT_RATE: honor VSTools user preference on 0xB6
    iget-object v0, p0, Lcom/android/server/input/InputManagerServiceExtImpl;->mContext:Landroid/content/Context;

    iget-boolean v1, p0, Lcom/android/server/input/InputManagerServiceExtImpl;->mRateStrategySimple:Z

    invoke-static {v0, p1, v1}, Lcom/android/vsteam/Toolbox;->resolveOplusTouchReportRate(Landroid/content/Context;Ljava/lang/String;Z)Ljava/lang/String;

    move-result-object p1
"""


class PatchError(RuntimeError):
    pass


def smali_files(root: Path) -> list[Path]:
    return sorted(
        path
        for directory in root.glob("smali*")
        if directory.is_dir()
        for path in directory.rglob("InputManagerServiceExtImpl.smali")
    )


def insertion_offset(body: str) -> int:
    lines = body.splitlines(keepends=True)
    if not lines or not lines[0].lstrip().startswith((".registers ", ".locals ")):
        raise PatchError("target method has no .registers/.locals directive")

    offset = len(lines[0])
    for line in lines[1:]:
        stripped = line.lstrip()
        if not stripped or stripped.startswith((".param ", ".annotation ", ".end annotation")):
            offset += len(line)
            continue
        break
    return offset


def patch_root(root: Path) -> Path:
    candidates: list[tuple[Path, re.Match[str], str]] = []
    for path in smali_files(root):
        source = path.read_text(encoding="utf-8")
        for match in METHOD_RE.finditer(source):
            block = match.group(0)
            if TARGET_DESCRIPTOR not in match.group("header"):
                continue
            if all(marker in block for marker in REQUIRED_MARKERS):
                candidates.append((path, match, source))

    if len(candidates) != 1:
        locations = ", ".join(str(item[0].relative_to(root)) for item in candidates)
        raise PatchError(
            f"expected one dynamic report-rate method, found {len(candidates)}"
            + (f": {locations}" if locations else "")
        )

    path, match, source = candidates[0]
    block = match.group(0)
    if PATCH_MARKER in block:
        print(f"OPlus touch report-rate hook already present: {path.relative_to(root)}")
        return path

    body = match.group("body")
    offset = insertion_offset(body)
    patched_body = body[:offset] + INJECT + body[offset:]
    patched_block = match.group("header") + "\n" + patched_body + ".end method"
    path.write_text(source[:match.start()] + patched_block + source[match.end():], encoding="utf-8")
    print(f"Hooked OPlus touch report rate: {path.relative_to(root)}")
    return path


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: patch_oplus_services_touch_rate.py <decoded-jar-root>", file=sys.stderr)
        return 2
    try:
        patch_root(Path(sys.argv[1]).resolve())
    except (OSError, PatchError) as error:
        print(f"patch_oplus_services_touch_rate: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
