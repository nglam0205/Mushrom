#!/usr/bin/env python3
import re
import sys
from pathlib import Path


# NotificationManagerService strips the full-screen intent from a notification
# (FSI_REQUESTED_BUT_DENIED) when the app's USE_FULL_SCREEN_INTENT app op is
# denied. Play Store installs Zalo, Messenger, ... with that op denied, so
# their incoming calls stay a heads-up and never reach the lock screen. Skip
# the permission check for CATEGORY_CALL notifications; every other category
# still honours the "Full screen notifications" setting.
TARGET = "NotificationManagerService.smali"
NMS = "Lcom/android/server/notification/NotificationManagerService;"
MARKER = "# VS_PATCH_FSI_CALL_PERMISSION"
# if (notification.fullScreenIntent != null) {
FSI_GUARD = re.compile(
    r"(?m)^(?P<indent>[ \t]*)iget-object (?P<tmp>[vp]\d+), (?P<notification>[vp]\d+), "
    r"Landroid/app/Notification;->fullScreenIntent:Landroid/app/PendingIntent;\s*\n"
    r"\s*if-eqz (?P=tmp), (?P<skip>:\w+)[ \t]*\n"
)
#     canUseFullScreenIntent = checkUseFullScreenIntentPermission(source, ai, true);
#     if (!canUseFullScreenIntent) makeStickyHun(...);
CHECK = re.compile(
    r"const/4 (?P<flag>[vp]\d+), 0x1\s*\n"
    r"\s*invoke-direct \{[^}]*, (?P=flag)\}, " + re.escape(NMS)
    + r"->checkUseFullScreenIntentPermission\([^)]*\)Z\s*\n"
    r"\s*move-result (?P<result>[vp]\d+)\s*\n"
    r"(?:\s*\.[^\n]*\n|\s*\n)*"
    r"\s*if-nez (?P=result), (?P<skip>:\w+)"
)
# The check must directly follow the guard, inside the same if block.
MAX_GAP = 1500


def hook(indent: str, tmp: str, notification: str, flag: str, skip: str) -> str:
    # tmp only held the tested PendingIntent and flag is assigned right before
    # the permission check, so both are free here. They are zeroed before the
    # jump to keep their type compatible with the original null-check path.
    lines = [
        f"{MARKER}: call notifications keep their full-screen intent",
        f"iget-object {tmp}, {notification}, Landroid/app/Notification;->category:Ljava/lang/String;",
        f'const-string {flag}, "call"',
        f"invoke-virtual {{{flag}, {tmp}}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z",
        f"move-result {tmp}",
        f"const/4 {flag}, 0x0",
        f"if-eqz {tmp}, :vs_fsi_not_call",
        f"const/4 {tmp}, 0x0",
        f"goto/16 {skip}",
        ":vs_fsi_not_call",
    ]
    return "".join(f"{indent}{line}\n\n" for line in lines)


def patch_file(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    if "checkUseFullScreenIntentPermission" not in source:
        return False
    if MARKER in source:
        print(f"{path.stem}: call full-screen intent permission already patched")
        return True

    sites = []
    for guard in FSI_GUARD.finditer(source):
        check = CHECK.search(source, guard.end(), guard.end() + MAX_GAP)
        if check and check.group("skip") == guard.group("skip"):
            sites.append((guard, check))
    if len(sites) != 1:
        raise RuntimeError(f"{path}: expected 1 full-screen intent check, found {len(sites)}")

    guard, check = sites[0]
    tmp, notification, flag = guard.group("tmp"), guard.group("notification"), check.group("flag")
    if len({tmp, notification, flag}) != 3:
        raise RuntimeError(f"{path}: scratch registers overlap ({tmp}, {notification}, {flag})")

    patched = (
        source[:guard.end()]
        + "\n"
        + hook(guard.group("indent"), tmp, notification, flag, guard.group("skip")).rstrip("\n")
        + "\n"
        + source[guard.end():]
    )
    path.write_text(patched, encoding="utf-8")
    print(f"{path.stem}: call notifications skip the USE_FULL_SCREEN_INTENT check")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <decoded services.jar dir>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    targets = [
        path for directory in root.glob("smali*") if directory.is_dir()
        for path in directory.rglob(TARGET)
    ]
    patched = sum(patch_file(path) for path in targets)
    if not patched:
        print("full-screen intent permission check not found", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
