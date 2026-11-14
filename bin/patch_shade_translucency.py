#!/usr/bin/env python3
"""Make the control center / shade background blur and tint adjustable.

Usage: patch_shade_translucency.py <decoded MiuiSystemUI dir>

HyperOS draws the panel background from three resources read by
com.miui.systemui.shade.blur: the maximum blur radius, the blend colors laid
over the blurred wallpaper and shade_dim_color. Each value is routed through
Toolbox so VSTeam can scale it (vs_cc_blur_level, vs_cc_dim_level) instead of
shipping one fixed overlay.
"""
import re
import sys
from pathlib import Path


TOOLBOX = "Lcom/android/vsteam/Toolbox;"
BLUR_DIR = "com/miui/systemui/shade/blur"
METHOD = r"(?ms)^(?P<header>\.method [^\n]*{name}\n)(?P<body>.*?)^\.end method$"


def method_pattern(name):
    return re.compile(METHOD.format(name=re.escape(name)))


def prepend(body, hook):
    lines = body.split("\n")
    # Directives (.registers, .param, .line) have to stay ahead of the hook.
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("."):
            lines.insert(index, hook)
            return "\n".join(lines)
    return body


def hook_blur_radius(body):
    hook = "".join(
        f"    invoke-static {{{reg}}}, {TOOLBOX}->tuneShadeBlurRadius(I)I\n\n"
        f"    move-result {reg}\n\n"
        for reg in ("p1", "p2", "p3")
    ).rstrip("\n") + "\n"
    return prepend(body, hook)


def hook_after(call, method, descriptor, move):
    # Re-route the value right after it is loaded from resources.
    pattern = re.compile(
        r"(" + re.escape(call) + r"\s*\n\s*\n?\s*" + move + r" (?P<reg>[vp]\d+)\n)"
    )

    def apply(body):
        def insert(match):
            reg = match.group("reg")
            return (
                f"{match.group(1)}\n"
                f"    invoke-static {{{reg}}}, {TOOLBOX}->{method}{descriptor}\n\n"
                f"    {move} {reg}\n"
            )

        return pattern.sub(insert, body, count=1)

    return apply


BLEND = hook_after(
    "Landroid/content/res/Resources;->getIntArray(I)[I",
    "tuneShadeBlendColors", "([I)[I", "move-result-object",
)
DIM = hook_after(
    "Landroid/content/Context;->getColor(I)I",
    "tuneShadeDimColor", "(I)I", "move-result",
)

# (file, method, marker already present after hooking, transform)
HOOKS = (
    ("ShadeBlendBlurControllerImpl$BlurProvider.smali", "updateMaxRadius(III)V",
     "tuneShadeBlurRadius", hook_blur_radius),
    ("ShadeBlendBlurControllerImpl$BlendBackground.smali", "updateBlendColors()V",
     "tuneShadeBlendColors", BLEND),
    ("ShadeBlendBlurControllerImpl.smali", "updateDimColor()V",
     "tuneShadeDimColor", DIM),
    # The window-level dim splits the same color into alpha and RGB in start().
    ("ShadeWindowBlurControllerImpl.smali", "start()V",
     "tuneShadeDimColor", DIM),
)


def main():
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2

    root = Path(sys.argv[1])
    hooked = 0
    for file_name, method, marker, transform in HOOKS:
        label = f"{file_name.removesuffix('.smali')}.{method}"
        targets = list(root.glob(f"smali*/{BLUR_DIR}/{file_name}"))
        if not targets:
            print(f"[shade] not present in this build: {label}")
            continue

        for target in targets:
            text = target.read_text(encoding="utf-8", errors="surrogateescape")
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
            target.write_text(
                text[: match.start("body")] + patched + text[match.end("body"):],
                encoding="utf-8",
                errors="surrogateescape",
            )
            print(f"[shade] hooked {label}")
            hooked += 1

    return 0 if hooked else 1


if __name__ == "__main__":
    sys.exit(main())
