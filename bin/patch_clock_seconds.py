#!/usr/bin/env python3
"""Hooks the status bar "show seconds" feature of ColorOS 17.

Long-pressing the clock (status bar or quick settings) turns the seconds on
for five minutes: ClockSecondsController in SystemUI stores a deadline
(now + 300000 ms) and switches them off once it has passed; the Settings
option does the same through ClockSecondsModePreferenceController.

  * SystemUI, ClockSecondsController#toggleByLongPress(): first ask
    Toolbox.clockLongPress(), which runs the action picked in VSTeam
    (Shortcut > "vs_clock_long_press"); the stock toggle only runs when no
    action is set.
  * SystemUI and Settings, where the deadline is computed: take the duration
    from Toolbox.clockSecondsDuration() instead of the constant. It is five
    minutes unless "vs_clock_seconds_no_limit" is on, then far enough away to
    never be reached. The checks against the deadline are left alone: they
    only switch the seconds off after it.

Usage: patch_clock_seconds.py <decoded-apk-dir>   (SystemUI or Settings)
"""
import re
import sys
from pathlib import Path

MARKER = "# VS_PATCH_CLOCK_SECONDS"
HELPER = "Lcom/android/vsteam/Toolbox;"
FIVE_MINUTES = "0x493e0"

METHOD = re.compile(r"(?ms)^(?P<header>\.method[^\n]*)\n(?P<body>.*?)^\.end method$")
FRAME = re.compile(r"(?m)^(?P<indent>[ \t]*)\.(?P<kind>registers|locals) (?P<count>\d+)\n")
# deadline = System.currentTimeMillis() + 300000
DURATION = re.compile(
    rf"(?m)^(?P<indent>[ \t]*)const-wide/32 (?P<reg>v\d+), {FIVE_MINUTES}\n"
    rf"(?=(?:[ \t]*\n)*[ \t]*add-long(?:/2addr)? )"
)


def patch_duration(source: str) -> tuple[str, int]:
    def hook(match: re.Match) -> str:
        indent, reg = match.group("indent"), match.group("reg")
        return (
            f"{indent}{MARKER}: how long the seconds stay on\n"
            f"{indent}invoke-static {{}}, {HELPER}->clockSecondsDuration()J\n\n"
            f"{indent}move-result-wide {reg}\n"
        )

    return DURATION.subn(hook, source)


def patch_long_press(source: str) -> str:
    for method in METHOD.finditer(source):
        if not re.search(r"\stoggleByLongPress\(Ljava/lang/String;\)V$", method.group("header")):
            continue
        body = method.group("body")
        frame = FRAME.search(body)
        # One local is needed for the result: v0, free at the method entry.
        locals_ = frame and (int(frame.group("count")) - (2 if frame.group("kind") == "registers" else 0))
        if not frame or locals_ < 1:
            raise RuntimeError("toggleByLongPress has no free local")
        indent = frame.group("indent")
        hook = (
            f"\n{indent}{MARKER}: the action picked in VSTeam replaces the toggle\n"
            f"{indent}invoke-static {{}}, {HELPER}->clockLongPress()Z\n\n"
            f"{indent}move-result v0\n\n"
            f"{indent}if-eqz v0, :vs_clock_long_press_stock\n\n"
            f"{indent}return-void\n\n"
            f"{indent}:vs_clock_long_press_stock\n"
        )
        body = body[:frame.end()] + hook + body[frame.end():]
        return source[:method.start("body")] + body + source[method.end("body"):]
    raise RuntimeError("toggleByLongPress() not found")


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <decoded-apk-dir>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    controllers = list(root.glob("smali*/com/oplus/systemui/statusbar/clock/ClockSecondsController.smali"))
    preferences = list(root.glob(
        "smali*/com/oplus/settings/feature/notification/controller/ClockSecondsModePreferenceController.smali"))
    if not controllers and not preferences:
        print("[clock_seconds] this build has no clock seconds feature, nothing to patch")
        return 0
    try:
        for path in controllers + preferences:
            source = path.read_text(encoding="utf-8")
            if MARKER in source:
                print(f"[clock_seconds] already patched: {path.name}")
                continue
            source, count = patch_duration(source)
            if not count:
                raise RuntimeError(f"{path.name}: deadline computation not found")
            if path in controllers:
                source = patch_long_press(source)
            path.write_text(source, encoding="utf-8")
            print(f"[clock_seconds] patched: {path.name} ({count} duration site(s))")
    except RuntimeError as error:
        print(f"[clock_seconds] {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
