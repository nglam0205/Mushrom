#!/usr/bin/env python3
import re
import sys
from dataclasses import dataclass
from pathlib import Path


METHOD = re.compile(
    r"(?ms)^(?P<header>\.method[^\n]+)\n(?P<body>.*?)^\.end method$"
)


@dataclass(frozen=True)
class Hook:
    label: str
    markers: tuple[str, ...]
    maximum: int


# Match behavior, not R8-generated class or method names. These strings and API
# descriptors sit at the actual vendor-facing FPS actuator and are substantially
# more stable between Joyose releases than paths such as x0/b.smali or z0/d.smali.
HOOKS = (
    Hook(
        "XRing schedgenius setFps",
        ('const-string', '"setFps"', "Lvendor/xring/hardware/schedgenius/c;"),
        1,
    ),
    Hook(
        "perflock target FPS",
        ('"perflock#02088000_{0}#0"',),
        1,
    ),
    Hook(
        "FPSGO fstb_fps_list writer",
        ('"echo "', '"-"', '" > "', '"sh"', '"-c"'),
        1,
    ),
    Hook(
        "game display refresh-rate override",
        ('"setDisplayRefreshRate, packageName: "',),
        1,
    ),
    Hook(
        "Excellent FPS callback",
        ('" next fps: "', "Lcom/xiaomi/joyose/IMonitorSurface;->getdyFps(I)V"),
        1,
    ),
)


# libmigui.so (MiSurfaceImpl::doDynamicFps) runs inside every game process and
# throttles queueBuffer to whatever "<pkg> <fps>" Joyose writes into
# /data/system/mcd/df ("DynamicFPS DF top: <pkg> : 90"). Dozens of scenarios
# (lobby, hero death, thermal, ...) call the two writers, so neutralise the
# writers themselves: a null package makes Joyose write an empty file, which is
# its own reset path and means "no limit" to libmigui. Do not return-void here:
# the file would keep a stale cap from the previous write.
DF_WRITER_MARKERS = (
    '"/data/system/mcd/df"',
    "Ljava/io/FileOutputStream;-><init>(Ljava/lang/String;Z)V",
    "Landroid/system/Os;->chmod(Ljava/lang/String;I)V",
)
DF_WRITER_TAG = "# VS_PATCH_JOYOSE_FPS: DynamicFPS df always empty"
DF_WRITER_SIGNATURE = re.compile(
    r"^\.method public static \S+\(Landroid/content/Context;Ljava/lang/String;[FI]I\)V$"
)


def patch_df_writers(files: list[Path], root: Path) -> int:
    count = 0
    for path in files:
        source = path.read_text(encoding="utf-8")
        if '"/data/system/mcd/df"' not in source:
            continue
        changed = False

        def inject(match: re.Match[str]) -> str:
            nonlocal changed, count
            header = match.group("header")
            block = match.group(0)
            if not DF_WRITER_SIGNATURE.match(header.strip()):
                return block
            if not all(marker in block for marker in DF_WRITER_MARKERS):
                return block
            if DF_WRITER_TAG in block:
                count += 1
                return block
            directive = re.search(r"(?m)^\s*\.(registers|locals) \d+\n", block)
            if directive is None:
                raise RuntimeError(f"no register directive in {path}: {header}")
            changed = True
            count += 1
            print(f"Hooked Joyose FPS: DynamicFPS df writer [{path.relative_to(root)} :: {header}]")
            pos = directive.end()
            return block[:pos] + f"\n    {DF_WRITER_TAG}\n    const/4 p1, 0x0\n" + block[pos:]

        source = METHOD.sub(inject, source)
        if changed:
            path.write_text(source, encoding="utf-8")
    if count == 0:
        print("Skipped unavailable Joyose FPS path: DynamicFPS df writer")
    elif count > 2:
        raise RuntimeError(f"ambiguous DynamicFPS df writer: found {count} methods")
    return count


def smali_files(root: Path) -> list[Path]:
    return sorted(
        path for directory in root.glob("smali*") if directory.is_dir()
        for path in directory.rglob("*.smali")
    )


def replacement(header: str, label: str) -> str:
    return (
        f"{header}\n"
        "    .locals 0\n\n"
        f"    # VS_PATCH_JOYOSE_FPS: block {label}\n"
        "    return-void\n"
        ".end method"
    )


def patch_root(root: Path) -> int:
    files = smali_files(root)
    if not files:
        raise RuntimeError(f"no smali directories found in {root}")

    methods: list[tuple[Path, re.Match[str], str]] = []
    source_by_path: dict[Path, str] = {}
    for path in files:
        source = path.read_text(encoding="utf-8")
        source_by_path[path] = source
        for match in METHOD.finditer(source):
            header = match.group("header")
            if not header.rstrip().endswith(")V"):
                continue
            methods.append((path, match, match.group(0)))

    selected: dict[Path, list[tuple[int, int, str]]] = {}
    patched_count = 0
    for hook in HOOKS:
        matches = [item for item in methods if all(m in item[2] for m in hook.markers)]
        if len(matches) > hook.maximum:
            locations = ", ".join(str(item[0].relative_to(root)) for item in matches)
            raise RuntimeError(
                f"ambiguous {hook.label}: found {len(matches)} methods in {locations}"
            )
        if not matches:
            print(f"Skipped unavailable Joyose FPS path: {hook.label}")
            continue

        for path, match, _block in matches:
            header = match.group("header")
            selected.setdefault(path, []).append(
                (match.start(), match.end(), replacement(header, hook.label))
            )
            patched_count += 1
            print(
                "Hooked Joyose FPS: "
                f"{hook.label} [{path.relative_to(root)} :: {header}]"
            )

    for path, edits in selected.items():
        source = source_by_path[path]
        for start, end, content in sorted(edits, reverse=True):
            source = source[:start] + content + source[end:]
        path.write_text(source, encoding="utf-8")

    patched_count += patch_df_writers(files, root)
    if patched_count == 0:
        raise RuntimeError("no known FPS control behavior found")
    return patched_count


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: patch_joyose_fps.py <decoded-apk-root>", file=sys.stderr)
        return 2

    try:
        patch_root(Path(sys.argv[1]).resolve())
    except (OSError, RuntimeError) as error:
        print(f"patch_joyose_fps: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
