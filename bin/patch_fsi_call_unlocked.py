#!/usr/bin/env python3
import re
import sys
from pathlib import Path


# AOSP turns a full-screen intent into a heads-up whenever the device is
# awake and unlocked (NO_FSI_EXPECTED_TO_HUN). The MIUI global in-call
# overrides that used to launch call UIs no longer exist on Android 17, so
# incoming calls from apps such as Zalo stay a small heads-up. Launch the
# full-screen intent for CATEGORY_CALL notifications before that branch;
# every other category keeps the AOSP behavior, and DND is still applied
# afterwards by the same method.
TARGET = "FullScreenIntentDecisionProvider.smali"
DECISION = (
    "Lcom/android/systemui/statusbar/notification/interruption/"
    "FullScreenIntentDecisionProvider$DecisionImpl;"
)
METHOD = re.compile(
    r"(?ms)^(?P<header>\.method[^\n]*\bmakeFullScreenIntentDecision\([^\n]*)\n"
    r"(?P<body>.*?)^\.end method$"
)
NOTIFICATION = re.compile(
    r"invoke-virtual \{[vp]\d+\}, Landroid/service/notification/StatusBarNotification;"
    r"->getNotification\(\)Landroid/app/Notification;\s*\n\s*move-result-object (?P<reg>[vp]\d+)"
)
# The branch that returns NO_FSI_EXPECTED_TO_HUN:
#     if-eqz p2, :cond_x
#     sget-object pN, ...->NO_FSI_EXPECTED_TO_HUN:...
#     goto :goto_y
HUN_BRANCH = re.compile(
    r"(?m)^(?P<indent>\s*)if-eqz [vp]\d+, :\w+\s*\n\s*"
    r"sget-object (?P<out>[vp]\d+), " + re.escape(DECISION) + r"->NO_FSI_EXPECTED_TO_HUN:"
    + re.escape(DECISION) + r"\s*\n\s*goto(?:/16|/32)? (?P<done>:\w+)"
)
MARKER = "# VS_PATCH_FSI_CALL_UNLOCKED"


def hook(indent: str, notification: str, out: str, done: str) -> str:
    # The state and scratch registers v1/v2 are dead at this point: the
    # remaining code only reads p0, p2 and the provisioned controller in v0.
    return (
        f"{indent}{MARKER}: launch call full-screen intents while unlocked\n"
        f"{indent}iget-object v1, {notification}, Landroid/app/Notification;->category:Ljava/lang/String;\n"
        f'{indent}const-string v2, "call"\n'
        f"{indent}invoke-virtual {{v2, v1}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z\n"
        f"{indent}move-result v1\n"
        f"{indent}if-eqz v1, :vs_fsi_not_call\n"
        f"{indent}sget-object {out}, {DECISION}->FSI_KEYGUARD_OCCLUDED:{DECISION}\n"
        f"{indent}goto/16 {done}\n"
        f"{indent}:vs_fsi_not_call\n"
    )


def patch_file(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    match = METHOD.search(source)
    if not match:
        return False
    body = match.group("body")
    if MARKER in body:
        print(f"{path.stem}.makeFullScreenIntentDecision already patched")
        return True
    notification = NOTIFICATION.search(body)
    branch = HUN_BRANCH.search(body)
    if not notification or not branch:
        raise RuntimeError(f"{path}: makeFullScreenIntentDecision layout changed")
    reg = notification.group("reg")
    # The Notification register must survive until the HUN branch.
    between = body[notification.end():branch.start()]
    if re.search(
        rf"(?m)^\s*(?:move|const|sget|iget|new-instance|aget)[\w/-]* {reg}(?:,|$)", between
    ):
        raise RuntimeError(f"{path}: {reg} is overwritten before the HUN branch")

    patched = (
        body[:branch.start()]
        + hook(branch.group("indent").lstrip("\n"), reg, branch.group("out"), branch.group("done"))
        + body[branch.start():]
    )
    path.write_text(
        source[:match.start("body")] + patched + source[match.end("body"):],
        encoding="utf-8",
    )
    print(f"{path.stem}.makeFullScreenIntentDecision: launch call FSI while unlocked")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <decoded SystemUI dir>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    targets = [
        path for directory in root.glob("smali*") if directory.is_dir()
        for path in directory.rglob(TARGET)
    ]
    patched = sum(patch_file(path) for path in targets)
    if not patched:
        print("makeFullScreenIntentDecision not found", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
