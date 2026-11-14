#!/usr/bin/env python3
"""Lets VSTeam apply a font on ColorOS through Settings, like ThemeProvider#applyFont on HyperOS.

/data/system/font (oem_font_data_file) is writable only by system_server, Settings
(system_app) and the theme store (platform_app), and neither of the last two takes a
font from another app without a UI. Settings ships the theme store's engine,
com.oplus.flipfont.FontManager, whose setFontInputStream() writes Customized-Regular.ttf
and flips the FlipFont flag of the configuration, so every app reloads the typeface.

SuggestionStateProvider is exported without a permission and only answers
"getSuggestionState". Its call() first asks FontBridge (backend/vsteam, merged into
framework.jar), which handles "vs_apply_font" and "vs_reset_font" for VSTeam only and returns
null for anything else, so the stock path stays as it was.

Usage: patch_settings_font_bridge.py <decoded-apk-dir>
"""
import re
import sys
from pathlib import Path

MARKER = "# VS_PATCH_FONT_BRIDGE"
PROVIDER = "com/android/settings/dashboard/suggestions/SuggestionStateProvider.smali"
FONT_MANAGER = "com/oplus/flipfont/FontManager.smali"
BRIDGE = ("Lcom/android/vsteam/FontBridge;->call(Landroid/content/ContentProvider;"
          "Ljava/lang/String;Ljava/lang/String;Landroid/os/Bundle;)Landroid/os/Bundle;")

CALL = re.compile(
    r"(?ms)^\.method public call\(Ljava/lang/String;Ljava/lang/String;Landroid/os/Bundle;\)"
    r"Landroid/os/Bundle;\n(?P<body>.*?)^\.end method$")
FRAME = re.compile(r"(?m)^(?P<indent>[ \t]*)\.(?P<kind>registers|locals) (?P<count>\d+)\n")


def patch(source: str) -> str:
    method = CALL.search(source)
    if not method:
        raise RuntimeError("SuggestionStateProvider#call() not found")
    body = method.group("body")
    frame = FRAME.search(body)
    # One local is needed for the result, plus p0..p3 for the call: v0 is free at the entry.
    locals_ = frame and (int(frame.group("count")) - (4 if frame.group("kind") == "registers" else 0))
    if not frame or locals_ < 1:
        raise RuntimeError("SuggestionStateProvider#call() has no free local")
    indent = frame.group("indent")
    hook = (
        f"\n{indent}{MARKER}: VSTeam font apply, null for the stock methods\n"
        f"{indent}invoke-static {{p0, p1, p2, p3}}, {BRIDGE}\n\n"
        f"{indent}move-result-object v0\n\n"
        f"{indent}if-eqz v0, :vs_font_bridge_stock\n\n"
        f"{indent}return-object v0\n\n"
        f"{indent}:vs_font_bridge_stock\n"
    )
    body = body[:frame.end()] + hook + body[frame.end():]
    return source[:method.start("body")] + body + source[method.end("body"):]


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <decoded-apk-dir>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    providers = list(root.glob(f"smali*/{PROVIDER}"))
    if not providers:
        print("[font_bridge] SuggestionStateProvider not found, nothing to patch")
        return 0
    if not list(root.glob(f"smali*/{FONT_MANAGER}")):
        print("[font_bridge] this Settings has no flipfont FontManager, nothing to patch")
        return 0
    path = providers[0]
    source = path.read_text(encoding="utf-8")
    if MARKER in source:
        print(f"[font_bridge] already patched: {path.name}")
        return 0
    try:
        path.write_text(patch(source), encoding="utf-8")
    except RuntimeError as error:
        print(f"[font_bridge] {error}", file=sys.stderr)
        return 1
    print(f"[font_bridge] patched: {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
