#!/usr/bin/env python3
"""Make the ColorOS control center / shade background blur and tint adjustable.

Usage: patch_oplus_shade_translucency.py <decoded SystemUI dir>

ColorOS builds the behind scrim (ScrimControllerExImp.refreshBehindDrawable and
updateBehindMixConfig) from two helpers in NotifiAndQsPlatformBlurExKt:
panelBlurRadius(), the blur radius read from an integer resource, and
panelPlatformMixConfig(), which wraps the PANEL_LIGHT/NIGHT_MIX_COLOR tint
layers laid over the blurred content. Both are routed through Toolbox so the
same VSTeam settings as HyperOS apply (vs_cc_blur_level, vs_cc_dim_level). The
values are read when the drawable is built, so a change needs a SystemUI
restart, as on HyperOS.
"""
import re
import sys
from pathlib import Path


TOOLBOX = "Lcom/android/vsteam/Toolbox;"
TARGET = "com/oplusos/systemui/common/util/NotifiAndQsPlatformBlurExKt.smali"
MIX = "Lcom/oplusos/systemui/common/blurability/MixColorWithShader;"
SINGLE = "Lcom/oplusos/systemui/common/blurability/BlurMixConfig$BlurMixSingleWithShader;"
METHOD = r"(?ms)^(?P<header>\.method [^\n]*{name}\n)(?P<body>.*?)^\.end method$"


def method_pattern(name):
    return re.compile(METHOD.format(name=re.escape(name)))


def hook_blur_radius(body):
    # Scale the radius right before every return.
    return re.sub(
        r"^(?P<indent>[ \t]*)return (?P<reg>[vp]\d+)$",
        lambda m: (
            f"{m['indent']}invoke-static {{{m['reg']}}}, {TOOLBOX}->tuneShadeBlurRadius(I)I\n\n"
            f"{m['indent']}move-result {m['reg']}\n\n"
            f"{m['indent']}return {m['reg']}"
        ),
        body,
        flags=re.M,
    )


def hook_mix_colors(body):
    # The chosen MixColorWithShader is handed to BlurMixSingleWithShader; swap
    # it for a copy whose two layer colors have their alpha scaled.
    init = re.search(
        r"^(?P<indent>[ \t]*)invoke-direct \{[vp]\d+, (?P<reg>[vp]\d+)\}, "
        + re.escape(SINGLE + "-><init>(" + MIX + ")V") + "$",
        body,
        flags=re.M,
    )
    # apktool writes .locals, plain baksmali .registers (locals + the 3
    # parameter registers of this static (Context, Z, Z) method).
    locals_match = re.search(r"^([ \t]*)\.(locals|registers) (\d+)$", body, flags=re.M)
    if not init or not locals_match:
        return body

    # The scratch registers are appended after the method's own locals: the
    # existing ones stay live past the hook (v1 is the null message handed to
    # LogUtil.i$default), and clobbering one with an int crashes the ART JIT.
    reg = init["reg"]
    indent, kind, count = locals_match.groups()
    first = int(count) - (0 if kind == "locals" else 3)
    if first + 3 > 15:
        return body
    t_mode, t_color, b_mode, b_color = (f"v{first + i}" for i in range(4))
    hook = "".join(
        f"    {line}\n\n" for line in (
            f"iget {t_mode}, {reg}, {MIX}->topMode:I",
            f"iget {t_color}, {reg}, {MIX}->topLayerColor:I",
            f"invoke-static {{{t_color}}}, {TOOLBOX}->tuneShadeDimColor(I)I",
            f"move-result {t_color}",
            f"iget {b_mode}, {reg}, {MIX}->bottomMode:I",
            f"iget {b_color}, {reg}, {MIX}->bottomLayerColor:I",
            f"invoke-static {{{b_color}}}, {TOOLBOX}->tuneShadeDimColor(I)I",
            f"move-result {b_color}",
            f"new-instance {reg}, {MIX}",
            f"invoke-direct {{{reg}, {t_mode}, {t_color}, {b_mode}, {b_color}}}, {MIX}-><init>(IIII)V",
        )
    )
    body = body[: init.start()] + hook + body[init.start():]
    return body.replace(locals_match.group(0), f"{indent}.{kind} {int(count) + 4}", 1)


# (method, marker already present after hooking, transform)
HOOKS = (
    ("panelBlurRadius(Landroid/content/Context;)I", "tuneShadeBlurRadius", hook_blur_radius),
    ("panelPlatformMixConfig(Landroid/content/Context;ZZ)"
     "Lcom/oplusos/systemui/common/blurability/BlurMixConfig;",
     "tuneShadeDimColor", hook_mix_colors),
)


def main():
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2

    targets = list(Path(sys.argv[1]).glob(f"smali*/{TARGET}"))
    if not targets:
        print("[shade] NotifiAndQsPlatformBlurExKt not present in this build")
        return 1

    hooked = 0
    for target in targets:
        text = target.read_text(encoding="utf-8", errors="surrogateescape")
        for method, marker, transform in HOOKS:
            label = method.split("(")[0]
            match = method_pattern(method).search(text)
            if not match:
                print(f"[shade] not present in this build: {label}")
                continue
            body = match.group("body")
            if marker in body:
                print(f"[shade] already hooked: {label}")
                hooked += 1
                continue
            patched = transform(body)
            if patched == body:
                print(f"[shade] could not hook {label}", file=sys.stderr)
                continue
            text = text[: match.start("body")] + patched + text[match.end("body"):]
            print(f"[shade] hooked {label}")
            hooked += 1
        target.write_text(text, encoding="utf-8", errors="surrogateescape")

    return 0 if hooked else 1


if __name__ == "__main__":
    sys.exit(main())
