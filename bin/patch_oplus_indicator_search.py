#!/usr/bin/env python3
"""Send the launcher's home search entry to Google when vs_search_bar_ggl is on.

Usage: patch_oplus_indicator_search.py <decoded OplusLauncher dir>

Tapping the search entry next to the page indicator builds its intent in
IndicatorEntry$Companion.getIndicatorAppLaunchIntent, which hardcodes
com.oppo.quicksearchbox.action.Dispatch (or DOCK_SEARCH) and the ColorOS search
package. The patched getSearchAppAction/getSearchAppPackageName are never asked,
so with the ColorOS search app debloated the tap resolves to nothing
(START result -91). With vs_search_bar_ggl on, action and package become
android.search.action.GLOBAL_SEARCH / com.google.android.googlequicksearchbox;
off, the stock values stay.

Android 16 builds already take action and package from those getters, so
there is nothing left to hook and the getters' own patch covers it.
"""
import re
import sys
from pathlib import Path


TOOLBOX = "Lcom/android/vsteam/Toolbox;"
TARGET = "com/android/launcher3/search/IndicatorEntry$Companion.smali"
METHOD = re.compile(
    r"(?ms)^\.method [^\n]*getIndicatorAppLaunchIntent\(Landroid/content/Context;\)"
    r"Landroid/content/Intent;\n(?P<body>.*?)^\.end method$"
)
SETTING = "vs_search_bar_ggl"
MARKER = f'"{SETTING}"'
# (Intent setter, getter apk.sh already routes, value used when the setting is on)
SETTERS = (
    ("setAction", "getSearchAppAction", "android.search.action.GLOBAL_SEARCH"),
    ("setPackage", "getSearchAppPackageName", "com.google.android.googlequicksearchbox"),
)
REGISTERS = re.compile(r"^(?P<indent>[ \t]*)\.(?P<kind>registers|locals) (?P<n>\d+)$", re.M)


def param_regs(header):
    """Registers taken by the parameters, including `this`."""
    args = re.search(r"\((.*?)\)", header)[1]
    count = 0 if " static " in header else 1
    for arg in re.findall(r"\[*(?:L[^;]+;|.)", args):
        count += 2 if arg in ("J", "D") else 1
    return count


def add_scratch(body, header):
    """Grow the frame by one local and return (body, that register).

    Named locals alias the parameters (with .registers 3 and one parameter,
    v1 is p0), so a fresh register is the only one sure to be dead."""
    match = REGISTERS.search(body)
    n = int(match["n"])
    local = n if match["kind"] == "locals" else n - param_regs(header)
    line = f"{match['indent']}.{match['kind']} {n + 1}"
    return body[: match.start()] + line + body[match.end():], f"v{local}"


def hook(body, setter, value, label, scratch):
    call = re.compile(
        r"^(?P<indent>[ \t]*)invoke-virtual \{(?P<intent>[vp]\d+), (?P<reg>[vp]\d+)\}, "
        r"Landroid/content/Intent;->" + setter + r"\(Ljava/lang/String;\)Landroid/content/Intent;$",
        re.M,
    )
    # The search intent is built last; an earlier setPackage belongs to the
    # Breeno entry.
    matches = list(call.finditer(body))
    if not matches:
        return None
    match = matches[-1]
    reg = match["reg"]
    invoke = "invoke-static/range" if int(scratch[1:]) > 15 else "invoke-static"
    skip = f":vs_search_{setter.lower()}_stock"
    code = "".join(
        f"    {line}\n\n" for line in (
            f'const-string {scratch}, "{SETTING}"',
            f"{invoke} {{{scratch}}}, {TOOLBOX}->getBooleanGlobal(Ljava/lang/String;)Z",
            f"move-result {scratch}",
            f"if-eqz {scratch}, {skip}",
            f'const-string {reg}, "{value}"',
        )
    ) + f"    {skip}\n"
    print(f"[search] {label}: {setter} follows {SETTING}")
    return body[: match.start()] + code + body[match.start():]


def main():
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2

    targets = list(Path(sys.argv[1]).glob(f"smali*/{TARGET}"))
    if not targets:
        print("[search] IndicatorEntry$Companion not present in this build")
        return 1

    for target in targets:
        text = target.read_text(encoding="utf-8", errors="surrogateescape")
        match = METHOD.search(text)
        if not match:
            print("[search] getIndicatorAppLaunchIntent not present in this build")
            return 1
        body = match["body"]
        if MARKER in body:
            print("[search] already hooked: getIndicatorAppLaunchIntent")
            return 0
        todo = []
        for setter, getter, value in SETTERS:
            if f"->{getter}()Ljava/lang/String;" in body:
                print(f"[search] getIndicatorAppLaunchIntent: {setter} already uses {getter}")
            else:
                todo.append((setter, value))
        if not todo:
            continue
        header = text[text.rfind(".method", 0, match.start("body")):match.start("body")]
        body, scratch = add_scratch(body, header)
        for setter, value in todo:
            patched = hook(body, setter, value, "getIndicatorAppLaunchIntent", scratch)
            if patched is None:
                print(f"[search] could not find Intent.{setter}", file=sys.stderr)
                return 1
            body = patched
        target.write_text(
            text[: match.start("body")] + body + text[match.end("body"):],
            encoding="utf-8",
            errors="surrogateescape",
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
