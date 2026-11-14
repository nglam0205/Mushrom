#!/usr/bin/env python3
import re
import sys
from pathlib import Path


# MIUI gates ActivityRecord#canShowWhenLocked() behind its private app op
# 10020 (0x2724). CN builds only pre-grant it to a vendor whitelist, so call
# apps such as Zalo fire their full-screen intent, start the in-call activity
# and then stay hidden behind the keyguard. Let apps the user already allowed
# to use full-screen intents skip that op; everyone else keeps MIUI's check.
TARGET = "ActivityRecordImpl.smali"
METHOD = re.compile(
    r"(?ms)^(?P<header>\.method[^\n]*\bcanShowWhenLocked\(\)Z)\n"
    r"(?P<body>.*?)^\.end method$"
)
MARKER = "# VS_PATCH_SHOW_WHEN_LOCKED"
AR_FIELD = re.compile(
    r"iget-object [vp]\d+, p0, (L[^;]+/ActivityRecordImpl;->\w+:"
    r"Lcom/android/server/wm/ActivityRecord;)"
)
REGISTERS = re.compile(r"^(\s*)\.(registers|locals) (\d+)$", re.M)
NEEDED_LOCALS = 5

AR = "Lcom/android/server/wm/ActivityRecord;"
ATMS = "Lcom/android/server/wm/ActivityTaskManagerService;"


def hook(ar_field: str) -> str:
    return (
        f"    {MARKER}: allow apps granted USE_FULL_SCREEN_INTENT\n"
        f"    iget-object v0, p0, {ar_field}\n"
        f"    iget-object v1, v0, {AR}->mAtmService:{ATMS}\n"
        f"    iget-object v1, v1, {ATMS}->mContext:Landroid/content/Context;\n"
        f"    invoke-virtual {{v0}}, {AR}->getUid()I\n"
        "    move-result v3\n"
        f"    iget-object v4, v0, {AR}->packageName:Ljava/lang/String;\n"
        "    move-object v0, v1\n"
        '    const-string v1, "android.permission.USE_FULL_SCREEN_INTENT"\n'
        "    const/4 v2, -0x1\n"
        "    invoke-static {v0, v1, v2, v3, v4}, "
        "Landroid/content/PermissionChecker;->checkPermissionForPreflight("
        "Landroid/content/Context;Ljava/lang/String;IILjava/lang/String;)I\n"
        "    move-result v0\n"
        "    if-nez v0, :vs_fsi_not_granted\n"
        "    const/4 v0, 0x1\n"
        "    return v0\n"
        "    :vs_fsi_not_granted\n\n"
    )


def ensure_locals(body: str) -> str:
    match = REGISTERS.search(body)
    if not match:
        raise RuntimeError("canShowWhenLocked has no .registers/.locals line")
    indent, kind, count = match.group(1), match.group(2), int(match.group(3))
    # Instance method with no arguments: only p0 is a parameter register.
    locals_count = count - 1 if kind == "registers" else count
    if locals_count >= NEEDED_LOCALS:
        return body
    count = NEEDED_LOCALS + 1 if kind == "registers" else NEEDED_LOCALS
    return body[:match.start()] + f"{indent}.{kind} {count}" + body[match.end():]


def first_instruction(body: str) -> int:
    offset = 0
    for line in body.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and not stripped.startswith((".", "#")):
            return offset
        offset += len(line)
    raise RuntimeError("canShowWhenLocked has no instructions")


def patch_file(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    match = METHOD.search(source)
    if not match:
        return False
    body = match.group("body")
    if MARKER in body:
        print(f"{path.stem}.canShowWhenLocked already patched")
        return True
    if "0x2724" not in body:
        raise RuntimeError(f"{path}: canShowWhenLocked no longer checks op 10020")
    field = AR_FIELD.search(body)
    if not field:
        raise RuntimeError(f"{path}: ActivityRecord field not found")

    body = ensure_locals(body)
    at = first_instruction(body)
    body = body[:at] + hook(field.group(1)) + body[at:]
    path.write_text(
        source[:match.start("body")] + body + source[match.end("body"):],
        encoding="utf-8",
    )
    print(f"{path.stem}.canShowWhenLocked: skip op 10020 for USE_FULL_SCREEN_INTENT apps")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <decoded miui-services dir>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    targets = [
        path for directory in root.glob("smali*") if directory.is_dir()
        for path in directory.rglob(TARGET)
    ]
    patched = sum(patch_file(path) for path in targets)
    if not patched:
        print("canShowWhenLocked not found in ActivityRecordImpl", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
