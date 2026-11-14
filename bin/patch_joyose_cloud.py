#!/usr/bin/env python3
"""Stop Joyose from applying any cloud config; keep only the configs baked in the ROM.

Joyose (com.xiaomi.joyose) builds booster_config / common_config /
scene_recognize_config (game fps caps by scene, hero-die fps, dynamic fps,
QFPS, GPU tuner FPSCap profiles, perf commands...) in JoyoseCloudControlManager3
by taking the newest version out of four sources:

  1. MIUI cloud SDK com.xiaomi.teg.config.CloudConfig (mcc.inf.miui.com),
  2. the system Settings cloud (MiuiSettings$SettingsCloudData.getCloudDataList),
  3. Joyose's own smartP database table cloud_config (filled by CloudStrategy
     downloads from the jupiter server),
  4. the local profile: assets/default_cloud.json + odm/etc/default_cloud.json.

This patch removes 1-3 so only 4 (what Xiaomi shipped in this ROM) is used:
  - every public entry of the teg CloudConfig SDK becomes a no-op; the getters
    return their default argument and getDataLists returns null (callers
    already handle null), so the SDK never syncs or registers observers;
  - the Settings cloud reader returns an empty list;
  - the smartP cloud_config reads return a null cursor and its insert returns
    -1, so configs downloaded or cached by older builds are ignored.

Markers are API descriptors and log strings, not R8 class names, so the patch
follows Joyose updates. Idempotent: re-running is a no-op.
"""
import re
import sys
from dataclasses import dataclass
from pathlib import Path

MARKER = "VS_PATCH_JOYOSE_CLOUD"
METHOD = re.compile(r"(?ms)^(?P<header>\.method[^\n]+)\n(?P<body>.*?)^\.end method$")

TEG_SDK = "com/xiaomi/teg/config/CloudConfig.smali"

# name -> (descriptor suffix, body). Static methods: p0, p1, p2 = default value.
TEG_STUBS = {
    "init(Landroid/content/Context;)V": "return-void",
    "init(Landroid/content/Context;Ljava/lang/String;)V": "return-void",
    "registerObserver(": "return-void",
    "unregisterObserver(": "return-void",
    "setIsInternationalVersion(Z)V": "return-void",
    "setNetworkAccessEnabled(Z)V": "return-void",
    "setStagingModeEnabled(Z)V": "return-void",
    "setUpdateInterval(I)V": "return-void",
    "updateData()V": "return-void",
    "getDataLists(Ljava/lang/String;)Ljava/util/List;": "const/4 v0, 0x0\n\n    return-object v0",
    "getBoolean(Ljava/lang/String;Ljava/lang/String;Z)Z": "return p2",
    "getInt(Ljava/lang/String;Ljava/lang/String;I)I": "return p2",
    "getLong(Ljava/lang/String;Ljava/lang/String;J)J": "return-wide p2",
    "getDouble(Ljava/lang/String;Ljava/lang/String;D)D": "return-wide p2",
    "getString(Ljava/lang/String;Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;": "return-object p2",
}


@dataclass(frozen=True)
class Hook:
    label: str
    markers: tuple[str, ...]
    returns: str  # descriptor suffix the method header must end with
    body: str
    maximum: int


HOOKS = (
    Hook(
        "Settings cloud reader",
        ('"getSettingConfigs, moduleName: "', '"getCloudDataList"'),
        ")Ljava/util/List;",
        "new-instance v0, Ljava/util/ArrayList;\n\n"
        "    invoke-direct {v0}, Ljava/util/ArrayList;-><init>()V\n\n"
        "    return-object v0",
        1,
    ),
    Hook(
        "smartP cloud_config read",
        ('"SELECT * FROM cloud_config',),
        ")Landroid/database/Cursor;",
        "const/4 v0, 0x0\n\n    return-object v0",
        2,
    ),
    Hook(
        "smartP cloud_config insert",
        ('"insert open db failed: "', '"cloud_config"'),
        ")J",
        "const-wide/16 v0, -0x1\n\n    return-wide v0",
        1,
    ),
)


def stub(header: str, label: str, body: str) -> str:
    return (
        f"{header}\n"
        "    .locals 2\n\n"
        f"    # {MARKER}: {label}\n"
        f"    {body}\n"
        ".end method"
    )


def smali_files(root: Path) -> list[Path]:
    return sorted(
        path
        for directory in root.glob("smali*") if directory.is_dir()
        for path in directory.rglob("*.smali")
    )


def patch_teg(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    edits = []
    for match in METHOD.finditer(source):
        header = match.group("header")
        if " static " not in header or " public " not in f" {header} ":
            continue
        for signature, body in TEG_STUBS.items():
            name_desc = header.rsplit(" ", 1)[-1]
            if not name_desc.startswith(signature):
                continue
            if MARKER not in match.group("body"):
                edits.append((match.start(), match.end(), stub(header, "teg " + signature.split("(")[0], body)))
            else:
                edits.append(None)
            break
    count = len(edits)
    for start, end, content in sorted((e for e in edits if e), reverse=True):
        source = source[:start] + content + source[end:]
    if any(edits):
        path.write_text(source, encoding="utf-8")
    return count


def patch_root(root: Path) -> None:
    files = smali_files(root)
    if not files:
        raise RuntimeError(f"no smali directories found in {root}")

    teg = [p for p in files if p.as_posix().endswith(TEG_SDK)]
    if not teg:
        raise RuntimeError("teg CloudConfig SDK not found")
    for path in teg:
        count = patch_teg(path)
        print(f"Blocked Joyose cloud: teg CloudConfig SDK, {count} methods [{path.relative_to(root)}]")
        if count < 8:
            raise RuntimeError(f"teg CloudConfig: only {count} known methods found")

    sources = {path: path.read_text(encoding="utf-8") for path in files}
    selected: dict[Path, list[tuple[int, int, str]]] = {}
    for hook in HOOKS:
        matches = []
        for path, source in sources.items():
            for match in METHOD.finditer(source):
                block = match.group(0)
                header = match.group("header")
                if not header.rstrip().endswith(hook.returns):
                    continue
                if MARKER in block and f"{MARKER}: {hook.label}" in block:
                    matches.append((path, match, True))
                elif all(m in block for m in hook.markers):
                    matches.append((path, match, False))
        if not matches:
            raise RuntimeError(f"{hook.label} not found")
        if len(matches) > hook.maximum:
            where = ", ".join(str(p.relative_to(root)) for p, _m, _d in matches)
            raise RuntimeError(f"ambiguous {hook.label}: {len(matches)} methods in {where}")
        for path, match, done in matches:
            header = match.group("header")
            print(f"Blocked Joyose cloud: {hook.label} [{path.relative_to(root)} :: {header}]")
            if not done:
                selected.setdefault(path, []).append(
                    (match.start(), match.end(), stub(header, hook.label, hook.body))
                )

    for path, edits in selected.items():
        source = sources[path]
        for start, end, content in sorted(edits, reverse=True):
            source = source[:start] + content + source[end:]
        path.write_text(source, encoding="utf-8")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: patch_joyose_cloud.py <decoded-apk-root>", file=sys.stderr)
        return 2
    try:
        patch_root(Path(sys.argv[1]).resolve())
    except (OSError, RuntimeError) as error:
        print(f"patch_joyose_cloud: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
