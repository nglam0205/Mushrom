#!/usr/bin/env python3
"""Optional transparent look for active control center surfaces.

Usage: patch_cc_flat_tile.py <decoded MIUISystemUIPlugin dir>

Tiles, big cards and the brightness/volume sliders take their "on" look from
shared tokens: blend colors in MiuiColorBlendToken and the glass token in
MiBackgroundStyle. Those getters are redirected to the inactive tokens when the
matching switch is on (vs_cc_flat_active_tile for tiles and cards,
vs_cc_flat_slider for slider fills).

QSTileItemIconView additionally interpolates by an inactive->active progress,
or without blur picks a plain drawable per state; both are hooked as well.

Without the white "on" fill, an active tile or card no longer stands out and
card text meant for that fill (black) sits on glass. Icon tints and card text
colors therefore go through Toolbox, which picks glass-friendly colors while
vs_cc_flat_active_tile is on.

A slider fill blended with the inactive token comes out darker than its own
track, so with vs_cc_flat_slider on the main panel sliders get a plain
faint (20%) white fill instead (the way the ROM paints a muted slider).
The slider icons (AnimateColorView, blended from color tokens) are drawn in
solid white instead, like the active tile icons.
"""
import re
import sys
from pathlib import Path


CLASS = "miui/systemui/controlcenter/qs/tileview/QSTileItemIconView"
TOOLBOX = "Lcom/android/vsteam/Toolbox;"
STATE = "Lcom/android/systemui/plugins/qs/QSTile$State;"
DRAWABLE = "Landroid/graphics/drawable/Drawable;"
METHOD = r"(?ms)^(?P<header>\.method [^\n]*{name}\n)(?P<body>.*?)^\.end method$"

PROGRESS_HOOK = (
    f"    invoke-static {{p1}}, {TOOLBOX}->tuneTileActiveProgress(F)F\n\n"
    "    move-result p1\n"
)
DRAWABLE_HOOK = (
    '    const-string v0, "vs_cc_flat_active_tile"\n\n'
    f"    invoke-static {{v0}}, {TOOLBOX}->getBooleanGlobal(Ljava/lang/String;)Z\n\n"
    "    move-result v0\n\n"
    "    if-eqz v0, :vs_stock_active_bg\n\n"
    f"    invoke-direct {{p0, p1}}, L{CLASS};->getDisabledBackgroundDrawable({STATE}){DRAWABLE}\n\n"
    "    move-result-object v0\n\n"
    "    return-object v0\n\n"
    "    :vs_stock_active_bg\n"
)
HOOKS = (
    ("applyTileBackgroundStyle(FF)V", "tuneTileActiveProgress", PROGRESS_HOOK),
    (f"getActiveBackgroundDrawable({STATE}){DRAWABLE}", "vs_cc_flat_active_tile", DRAWABLE_HOOK),
)

BLEND = "Lmiuix/theme/token/ColorBlendToken;"
GLASS = "Lmiui/systemui/util/BionicsToken;"
TILE_OFF = f"getCC_TILE_DEFAULT_BLEND_COLORS(){BLEND}"
# (class, active getter, inactive getter to return instead, switch)
REDIRECTS = (
    ("miui/systemui/util/MiuiColorBlendToken", f"getCC_TILE_ON_BLEND_COLORS(){BLEND}",
     TILE_OFF, "vs_cc_flat_active_tile"),
    ("miui/systemui/util/MiBackgroundStyle", f"getACTIVATED_GLASS_TOKEN(){GLASS}",
     f"getDEFAULT_GLASS_TOKEN(){GLASS}", "vs_cc_flat_active_tile"),
    ("miui/systemui/util/MiuiColorBlendToken", f"getCC_SLIDER_BLEND_COLORS(){BLEND}",
     TILE_OFF, "vs_cc_flat_slider"),
    ("miui/systemui/util/MiuiColorBlendToken", f"getCC_SLIDER_GLASS_BLEND_COLORS(){BLEND}",
     TILE_OFF, "vs_cc_flat_slider"),
)


def redirect_hook(owner, fallback, switch, label):
    return (
        f'    const-string v0, "{switch}"\n\n'
        f"    invoke-static {{v0}}, {TOOLBOX}->getBooleanGlobal(Ljava/lang/String;)Z\n\n"
        "    move-result v0\n\n"
        f"    if-eqz v0, :{label}\n\n"
        f"    invoke-virtual {{p0}}, L{owner};->{fallback}\n\n"
        "    move-result-object v0\n\n"
        "    return-object v0\n\n"
        f"    :{label}\n"
    )


TILEVIEW = "miui/systemui/controlcenter/qs/tileview"
# Icon colors of an active tile; any other read is the off color.
ACTIVE_ICON_FIELDS = {
    "defaultIconColor", "quietModeTileIconColor", "paperModeTileIconColor",
    "muteTileIconColor", "miuiCellularTileIconColor", "autoBrightnessTileIconColor",
    "miuiFlashlightTileIconColor", "powerSaferTileIconColor",
}
ICON_TINTERS = (
    ("QSTileItemIconView", f"drawableTint({STATE}{DRAWABLE})V"),
    ("QSCardItemIconView", f"setIcon({STATE}Z)V"),
)
ICON_READ = re.compile(r"(?m)^(?P<indent>\s*)iget (?P<reg>[vp]\d+), p0, L[^;]+;->(?P<field>\w+):I\n")
# title / status color: getColor(res) -> setTextColor; the res register is free
# afterwards and carries the tile state into Toolbox.
CARD_TEXT = re.compile(
    r"(?P<get>invoke-virtual \{(?P<ctx>[vp]\d+), (?P<res>[vp]\d+)\}, "
    r"Landroid/content/Context;->getColor\(I\)I\n\s*move-result (?P<color>[vp]\d+)\n)"
    r"(?P<set>\s*invoke-virtual \{[vp]\d+, (?P=color)\}, Landroid/widget/TextView;->setTextColor\(I\)V)"
)


def hook_icon_tints(root):
    hooked = 0
    for cls, name in ICON_TINTERS:
        for target in root.glob(f"smali*/{TILEVIEW}/{cls}.smali"):
            text = target.read_text(encoding="utf-8", errors="surrogateescape")
            match = re.search(METHOD.format(name=re.escape(name)), text)
            if not match:
                continue
            body = match.group("body")
            if "tuneActiveIconColor" in body:
                print(f"[cc-tile] already hooked: {cls} icon tint")
                hooked += 1
                break

            tuned = []

            def hook(read):
                field = read.group("field")
                if field in ACTIVE_ICON_FIELDS:
                    tune = "tuneActiveIconColor"
                elif field == "defaultIconColorOff":
                    tune = "tuneInactiveIconColor"
                else:
                    return read.group(0)
                tuned.append(field)
                reg, indent = read.group("reg"), read.group("indent")
                return (f"{read.group(0)}\n{indent}invoke-static {{{reg}}}, {TOOLBOX}->{tune}(I)I\n\n"
                        f"{indent}move-result {reg}\n")

            body = ICON_READ.sub(hook, body)
            text = text[: match.start("body")] + body + text[match.end("body"):]
            target.write_text(text, encoding="utf-8", errors="surrogateescape")
            print(f"[cc-tile] hooked {cls} icon tint ({len(tuned)} colors)")
            hooked += 1
            break
        else:
            print(f"[cc-tile] not present in this build: {cls} icon tint")
    return hooked


def hook_card_text(root):
    name = f"updateState({STATE}ZZ)V"
    for target in root.glob(f"smali*/{TILEVIEW}/QSCardItemView.smali"):
        text = target.read_text(encoding="utf-8", errors="surrogateescape")
        match = re.search(METHOD.format(name=re.escape(name)), text)
        if not match:
            continue
        body = match.group("body")
        if "tuneCardTitleColor" in body:
            print("[cc-tile] already hooked: card text")
            return 1
        reads = list(CARD_TEXT.finditer(body))
        if len(reads) != 2:
            print(f"[cc-tile] card text: expected title + status colors, found {len(reads)}")
            return 0
        for read, tune in reversed(list(zip(reads, ("tuneCardTitleColor", "tuneCardStatusColor")))):
            res, color = read.group("res"), read.group("color")
            hook = (f"    iget {res}, p1, {STATE[:-1]};->state:I\n\n"
                    f"    invoke-static {{{color}, {res}}}, {TOOLBOX}->{tune}(II)I\n\n"
                    f"    move-result {color}\n\n")
            body = body[: read.start("set")] + hook + body[read.start("set"):]
        text = text[: match.start("body")] + body + text[match.end("body"):]
        target.write_text(text, encoding="utf-8", errors="surrogateescape")
        print("[cc-tile] hooked card text")
        return 1
    print("[cc-tile] not present in this build: card text")
    return 0


SLIDER = "miui/systemui/controlcenter/panel/main/recyclerview/ToggleSliderViewHolder"
SLIDER_BINDING = "Lmiui/systemui/controlcenter/databinding/ToggleSliderItemViewBinding;"
SLIDER_FILL_HOOK = (
    '    const-string v0, "vs_cc_flat_slider"\n\n'
    f"    invoke-static {{v0}}, {TOOLBOX}->getBooleanGlobal(Ljava/lang/String;)Z\n\n"
    "    move-result v0\n\n"
    "    if-eqz v0, :vs_stock_fill\n\n"
    f"    iget-boolean v0, p0, L{SLIDER};->disableState:Z\n\n"
    "    if-nez v0, :vs_stock_fill\n\n"
    f"    iget-object v0, p0, L{SLIDER};->binding:{SLIDER_BINDING}\n\n"
    f"    iget-object v0, v0, {SLIDER_BINDING}->progress:Lmiui/systemui/widget/View;\n\n"
    # Faint white (20%): just above the glass track so the level reads,
    # without the bright fill standing out next to the white active icons.
    "    const v1, 0x33ffffff\n\n"
    "    invoke-static {v0, v1}, Lcom/android/systemui/miui/volume/Util;->applyFlatColor(Landroid/view/View;I)V\n\n"
    "    return-void\n\n"
    "    :vs_stock_fill\n"
)


def hook_slider_fill(root):
    for target in root.glob(f"smali*/{SLIDER}.smali"):
        text = target.read_text(encoding="utf-8", errors="surrogateescape")
        match = re.search(METHOD.format(name=re.escape("updateProgressBlendColors()V")), text)
        if not match or "applyFlatColor(Landroid/view/View;I)V" not in text:
            continue
        body = match.group("body")
        if ":vs_stock_fill" in body:
            print("[cc-tile] already hooked: slider fill")
            return 1
        if not re.search(r"(?m)^\s*\.locals [2-9]", body):
            print("[cc-tile] slider fill: needs two locals; skip")
            return 0
        body = prepend(body, SLIDER_FILL_HOOK)
        text = text[: match.start("body")] + body + text[match.end("body"):]
        target.write_text(text, encoding="utf-8", errors="surrogateescape")
        print("[cc-tile] hooked slider fill")
        return 1
    print("[cc-tile] not present in this build: slider fill")
    return 0


ICON_VIEW = "miui/systemui/controlcenter/widget/AnimateColorView"
ICON_METHODS = (
    "updateIconColor(Lmiuix/theme/token/ColorBlendToken;Lmiuix/theme/token/ColorBlendToken;IIZ)V",
    "updateIconColorWithGlass(Lmiuix/theme/token/ColorBlendToken;Lmiuix/theme/token/ColorBlendToken;IZ)V",
)
SLIDER_ICON_HOOK = (
    '    const-string v0, "vs_cc_flat_slider"\n\n'
    f"    invoke-static {{v0}}, {TOOLBOX}->getBooleanGlobal(Ljava/lang/String;)Z\n\n"
    "    move-result v0\n\n"
    "    if-eqz v0, :vs_stock_slider_icon\n\n"
    # Drop the blend tokens so a later blend progress cannot paint over the tint.
    "    const/4 v0, 0x0\n\n"
    f"    iput-object v0, p0, L{ICON_VIEW};->fromToken:Lmiuix/theme/token/ColorBlendToken;\n\n"
    f"    iput-object v0, p0, L{ICON_VIEW};->toToken:Lmiuix/theme/token/ColorBlendToken;\n\n"
    f"    invoke-direct {{p0}}, L{ICON_VIEW};->cancelBlendAnimation()V\n\n"
    f"    invoke-direct {{p0}}, L{ICON_VIEW};->cancelNormalAnimation()V\n\n"
    "    sget-object v0, Lmiui/systemui/util/CommonUtils;->INSTANCE:Lmiui/systemui/util/CommonUtils;\n\n"
    "    invoke-virtual {v0, p0}, Lmiui/systemui/util/CommonUtils;->clearMiBlurBlendEffect(Landroid/view/View;)V\n\n"
    "    invoke-virtual {p0}, Landroid/widget/ImageView;->getDrawable()Landroid/graphics/drawable/Drawable;\n\n"
    "    move-result-object v0\n\n"
    "    if-eqz v0, :vs_slider_icon_done\n\n"
    "    const/4 v1, -0x1\n\n"
    "    invoke-static {v0, v1}, Landroidx/core/graphics/drawable/DrawableCompat;->setTint(Landroid/graphics/drawable/Drawable;I)V\n\n"
    "    :vs_slider_icon_done\n"
    "    return-void\n\n"
    "    :vs_stock_slider_icon\n"
)


def hook_slider_icon(root):
    targets = list(root.glob(f"smali*/{ICON_VIEW}.smali"))
    if not targets:
        print("[cc-tile] not present in this build: slider icon")
        return 0
    text = targets[0].read_text(encoding="utf-8", errors="surrogateescape")
    hooked = 0
    for name in ICON_METHODS:
        label = f"slider icon {name.split('(')[0]}"
        match = re.search(METHOD.format(name=re.escape(name)), text)
        if not match:
            print(f"[cc-tile] not present in this build: {label}")
            continue
        body = match.group("body")
        if "vs_cc_flat_slider" in body:
            print(f"[cc-tile] already hooked: {label}")
            hooked += 1
            continue
        # The hook needs v0 and v1.
        body = re.sub(r"(?m)^(\s*)\.locals [01]$", r"\1.locals 2", body, count=1)
        body = prepend(body, SLIDER_ICON_HOOK)
        text = text[: match.start("body")] + body + text[match.end("body"):]
        print(f"[cc-tile] hooked {label}")
        hooked += 1
    targets[0].write_text(text, encoding="utf-8", errors="surrogateescape")
    return 1 if hooked else 0


def ensure_local(body):
    # The trivial getters are compiled with no locals; the hook needs v0.
    return re.sub(r"(?m)^(\s*)\.locals 0\s*$", r"\1.locals 1", body, count=1)


def apply_redirects(root):
    hooked = 0
    for owner, getter, fallback, switch in REDIRECTS:
        label = f"{owner.rsplit('/', 1)[1]}.{getter.split('(')[0]}"
        for target in root.glob(f"smali*/{owner}.smali"):
            text = target.read_text(encoding="utf-8", errors="surrogateescape")
            match = re.search(METHOD.format(name=re.escape(getter)), text)
            if not match or fallback.split("(")[0] + "(" not in text:
                continue
            body = match.group("body")
            if f'"{switch}"' in body:
                print(f"[cc-tile] already hooked: {label}")
            else:
                hook = redirect_hook(owner, fallback, switch, "vs_stock_token")
                body = prepend(ensure_local(body), hook)
                text = text[: match.start("body")] + body + text[match.end("body"):]
                target.write_text(text, encoding="utf-8", errors="surrogateescape")
                print(f"[cc-tile] hooked {label}")
            hooked += 1
            break
        else:
            print(f"[cc-tile] not present in this build: {label}")
    return hooked


def prepend(body, hook):
    lines = body.split("\n")
    # Directives (.registers, .param, .line) have to stay ahead of the hook.
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("."):
            lines.insert(index, hook)
            return "\n".join(lines)
    return body


def main():
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2

    root = Path(sys.argv[1])
    hooked = apply_redirects(root)
    hooked += hook_icon_tints(root)
    hooked += hook_card_text(root)
    hooked += hook_slider_fill(root)
    hooked += hook_slider_icon(root)
    targets = list(root.glob(f"smali*/{CLASS}.smali"))
    if not targets:
        print("[cc-tile] QSTileItemIconView not present in this build")
    for target in targets:
        text = target.read_text(encoding="utf-8", errors="surrogateescape")
        for name, marker, hook in HOOKS:
            label = name.split("(")[0]
            match = re.search(METHOD.format(name=re.escape(name)), text)
            if not match:
                print(f"[cc-tile] not present in this build: {label}")
                continue
            body = match.group("body")
            if marker in body:
                print(f"[cc-tile] already hooked: {label}")
                hooked += 1
                continue
            text = text[: match.start("body")] + prepend(body, hook) + text[match.end("body"):]
            print(f"[cc-tile] hooked {label}")
            hooked += 1
        target.write_text(text, encoding="utf-8", errors="surrogateescape")

    return 0 if hooked else 1


if __name__ == "__main__":
    sys.exit(main())
