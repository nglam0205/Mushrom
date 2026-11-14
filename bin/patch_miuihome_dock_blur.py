#!/usr/bin/env python3
import re
import struct
import sys
from pathlib import Path


# Edit these values to tune the blur background around dock icons.
DOCK_PADDING_LEFT_DP = 10.0
DOCK_PADDING_RIGHT_DP = 10.0
DOCK_PADDING_TOP_DP = 6.0
DOCK_PADDING_BOTTOM_DP = 6.0
DOCK_CORNER_RADIUS_DP = 27.0

DOCK_BLUR_SWITCH = "vs_miuihome_dock_blur"
BLUR_SYSTEM = "vs_miuihome_system_blur"
BLUR_RADIUS = "vs_miuihome_blur_radius"
DOCK_CORNER_RADIUS_OFFSET = "vs_miuihome_dock_blur_corner_offset"
DOCK_PADDING_OFFSET = "vs_miuihome_dock_blur_padding_offset"
DOCK_BLUR_RADIUS_DIMEN = "shortcut_menu_blur_radius"

CALL = (
    "    invoke-direct {p0}, "
    "Lcom/miui/home/launcher/hotseats/HotSeatsScreenViewContent;"
    "->applyHotseatBackgroundBlur()V\n"
)


def smali_float_const(register: str, value: float) -> str:
    bits = struct.unpack(">I", struct.pack(">f", float(value)))[0]
    if bits & 0x0000FFFF == 0:
        return f"    const/high16 {register}, 0x{bits:08x}    # {value:.2f}f\n"
    return f"    const {register}, 0x{bits:08x}    # {value:.2f}f\n"


def dp_to_px(register_float: str, register_result: str, value: float) -> str:
    return (
        smali_float_const(register_float, value)
        + f"\n    invoke-static {{{register_float}}}, "
        "Lcom/miui/home/common/utils/DimenUtils;->dp2px(F)I\n\n"
        + f"    move-result {register_result}\n"
    )


def dp_offset_to_px(register_key: str, register_float: str, register_result: str, setting_key: str) -> str:
    return (
        f'    const-string {register_key}, "{setting_key}"\n\n'
        f"    invoke-static {{{register_key}}}, "
        "Lcom/android/vsteam/Toolbox;->getIntGlobal(Ljava/lang/String;)I\n\n"
        f"    move-result {register_key}\n\n"
        f"    int-to-float {register_float}, {register_key}\n\n"
        f"    invoke-static {{{register_float}}}, "
        "Lcom/miui/home/common/utils/DimenUtils;->dp2px(F)I\n\n"
        f"    move-result {register_result}\n"
    )


def negative_dp_offset_to_px(register_key: str, register_float: str, register_result: str, setting_key: str) -> str:
    return (
        f'    const-string {register_key}, "{setting_key}"\n\n'
        f"    invoke-static {{{register_key}}}, "
        "Lcom/android/vsteam/Toolbox;->getIntGlobal(Ljava/lang/String;)I\n\n"
        f"    move-result {register_key}\n\n"
        f"    neg-int {register_key}, {register_key}\n\n"
        f"    int-to-float {register_float}, {register_key}\n\n"
        f"    invoke-static {{{register_float}}}, "
        "Lcom/miui/home/common/utils/DimenUtils;->dp2px(F)I\n\n"
        f"    move-result {register_result}\n"
    )


def build_helper() -> str:
    return f"""
.method private applyHotseatBlurRoundRect()V
    .locals 7

{dp_to_px("v0", "v1", DOCK_CORNER_RADIUS_DP)}

{dp_offset_to_px("v0", "v0", "v0", DOCK_CORNER_RADIUS_OFFSET)}

    add-int/2addr v1, v0

    const/4 v0, 0x0

    invoke-static {{v1, v0}}, Ljava/lang/Math;->max(II)I

    move-result v1

{dp_to_px("v0", "v2", DOCK_PADDING_LEFT_DP)}

{dp_to_px("v0", "v3", DOCK_PADDING_TOP_DP)}

{dp_to_px("v0", "v6", DOCK_PADDING_RIGHT_DP)}

{dp_to_px("v0", "v0", DOCK_PADDING_BOTTOM_DP)}

{negative_dp_offset_to_px("v4", "v4", "v4", DOCK_PADDING_OFFSET)}

    add-int/2addr v2, v4

    add-int/2addr v3, v4

    add-int/2addr v6, v4

    add-int/2addr v0, v4

    const/4 v5, 0x0

    invoke-static {{v2, v5}}, Ljava/lang/Math;->max(II)I

    move-result v2

    invoke-static {{v3, v5}}, Ljava/lang/Math;->max(II)I

    move-result v3

    invoke-static {{v6, v5}}, Ljava/lang/Math;->max(II)I

    move-result v6

    invoke-static {{v0, v5}}, Ljava/lang/Math;->max(II)I

    move-result v0

    invoke-virtual {{p0}}, Landroid/view/View;->getMeasuredWidth()I

    move-result v4

    if-lez v4, :cond_0

    sub-int/2addr v4, v6

    invoke-virtual {{p0}}, Landroid/view/View;->getMeasuredHeight()I

    move-result v5

    if-lez v5, :cond_0

    sub-int/2addr v5, v0

    const/4 v6, 0x0

    move-object v0, p0

    invoke-static/range {{v0 .. v6}}, Lcom/miui/home/common/utils/BlurUtilities;->setBlurRoundRect(Landroid/view/View;IIIIIZ)V

    :cond_0
    return-void
.end method

.method private applyHotseatBackgroundBlur()V
    .locals 4

    const-string v0, "{DOCK_BLUR_SWITCH}"

    invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getBooleanGlobal(Ljava/lang/String;)Z

    move-result v0

    if-eqz v0, :cond_2

    invoke-static {{}}, Lcom/miui/home/common/utils/BlurUtilities;->isThreeLayerBlurSupported()Z

    move-result v0

    if-eqz v0, :cond_2

    const-string v0, "{BLUR_SYSTEM}"

    invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getIntGlobal(Ljava/lang/String;)I

    move-result v0

    if-eqz v0, :cond_1

    invoke-virtual {{p0}}, Landroid/view/View;->getContext()Landroid/content/Context;

    move-result-object v0

    invoke-virtual {{v0}}, Landroid/content/Context;->getResources()Landroid/content/res/Resources;

    move-result-object v0

    sget v1, Lcom/miui/home/common/R$dimen;->{DOCK_BLUR_RADIUS_DIMEN}:I

    invoke-virtual {{v0, v1}}, Landroid/content/res/Resources;->getDimensionPixelSize(I)I

    move-result v0

    invoke-static {{p0}}, Lcom/miui/home/common/utils/FolderBlurUtilities;->setDragFolderIconBlurColors(Landroid/view/View;)V

    const/4 v1, 0x1

    invoke-static {{p0, v1, v1, v0}}, Lcom/miui/home/common/utils/BlurUtilities;->setContainerBlur(Landroid/view/View;IZI)V

    invoke-static {{p0, v1}}, Lcom/miui/home/common/utils/BlurUtilities;->setViewBlur(Landroid/view/View;I)V

    const/4 v0, 0x0

    invoke-virtual {{p0, v0}}, Landroid/view/View;->setBackgroundColor(I)V

    goto :apply_blur

    :cond_1
    const-string v0, "{BLUR_RADIUS}"

    invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getIntGlobal(Ljava/lang/String;)I

    move-result v0

    int-to-float v0, v0

    invoke-static {{v0}}, Lcom/miui/home/common/utils/DimenUtils;->dp2px(F)I

    move-result v0

    const/4 v1, 0x1

    invoke-static {{p0, v1, v1, v0}}, Lcom/miui/home/common/utils/BlurUtilities;->setContainerBlur(Landroid/view/View;IZI)V

    invoke-static {{p0, v1}}, Lcom/miui/home/common/utils/BlurUtilities;->setViewBlur(Landroid/view/View;I)V

    :apply_blur
    invoke-direct {{p0}}, Lcom/miui/home/launcher/hotseats/HotSeatsScreenViewContent;->applyHotseatBlurRoundRect()V

    const/4 v0, 0x0

    invoke-static {{p0, v0}}, Lcom/miui/home/common/utils/BlurUtilities;->setMiBackgroundBlurEnhanceFlag(Landroid/view/View;Z)V

    :cond_2
    return-void
.end method

"""


def insert_before_return(src: str, method_regex: str, required: bool = True) -> str:
    method = re.search(method_regex, src, flags=re.S | re.M)
    if not method:
        if required:
            raise ValueError(f"method anchor not found: {method_regex}")
        return src

    block = method.group(0)
    if "->applyHotseatBackgroundBlur()V" in block:
        return src

    returns = list(re.finditer(r"(?m)^    return-void\s*$", block))
    if not returns:
        if required:
            raise ValueError(f"return-void anchor not found: {method_regex}")
        return src

    pos = returns[-1].start()
    block = block[:pos] + CALL + "\n" + block[pos:]
    return src[: method.start()] + block + src[method.end() :]


def patch_file(path: Path) -> bool:
    text = path.read_text()
    original = text

    helper_pattern = (
        r"(?m)^\.method\s+private\s+applyHotseat"
        r"(?:BlurRoundRect|BackgroundBlur)\(\)V\b.*?^\.end method\n+"
    )
    text = re.sub(helper_pattern, "", text, flags=re.S)

    helper = build_helper()
    constructor = re.search(r"(?m)^\.method[^\n]*\sconstructor\s+<init>\(", text)
    if constructor:
        text = text[: constructor.start()] + helper + text[constructor.start() :]
    else:
        first_method = re.search(r"(?m)^\.method\s+", text)
        if not first_method:
            raise ValueError("no method anchor found")
        text = text[: first_method.start()] + helper + text[first_method.start() :]

    text = insert_before_return(
        text,
        r"(?m)^\.method[^\n]*\sonFinishInflate\(\)V\b.*?^\.end method$",
    )
    text = insert_before_return(
        text,
        r"(?m)^\.method[^\n]*\sonLayout\(ZIIII\)V\b.*?^\.end method$",
    )
    text = insert_before_return(
        text,
        r"(?m)^\.method[^\n]*\sonDarkModeChanged\(\)V\b.*?^\.end method$",
        required=False,
    )

    if text != original:
        path.write_text(text)
        return True
    return False


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: patch_miuihome_dock_blur.py HotSeatsScreenViewContent.smali [...]", file=sys.stderr)
        return 2

    changed = 0
    for arg in argv:
        path = Path(arg)
        try:
            if patch_file(path):
                changed += 1
        except Exception as exc:
            print(f"{path}: {exc}", file=sys.stderr)
            return 1

    print(changed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
