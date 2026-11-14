#!/usr/bin/env python3
"""
Patch FolderBlurUtilities.smali – thay thế setFolderIconBlur(View, II)
để đọc blur radius từ VS settings thay vì dùng giá trị hệ thống truyền vào.

Gate/logic (chia sẻ với dock blur):
  vs_miuihome_dock_blur   – boolean: gate tổng
  vs_miuihome_system_blur – int: 0 = custom, 1 = system (gốc)

Khi custom (dock_blur=1 AND system_blur=0):
  vs_miuihome_blur_radius – int dp → dùng cho setViewBlur, bỏ qua setBlurColorList
Khi system: giữ nguyên p2 (radius từ caller) và setBlurColorList bình thường.
"""
import re
import sys
from pathlib import Path


DOCK_BLUR_SWITCH = "vs_miuihome_dock_blur"
BLUR_SYSTEM      = "vs_miuihome_system_blur"
BLUR_RADIUS      = "vs_miuihome_blur_radius"

_TOOLBOX  = "Lcom/android/vsteam/Toolbox;"
_DIMEN    = "Lcom/miui/home/common/utils/DimenUtils;"
_BLUR     = "Lcom/miui/home/common/utils/BlurUtilities;"
_FBLUR    = "Lcom/miui/home/common/utils/FolderBlurUtilities;"
_VIEW     = "Landroid/view/View;"

# Signature của method cần patch
_METHOD_SIG = "setFolderIconBlur(Landroid/view/View;II)V"


def build_method() -> str:
    # Register layout (giữ .locals 8 như bản gốc):
    #   p0 = View, p1 = cornerRadius, p2 = blurRadius (system hoặc custom px)
    #   v0 = temp (keys, int-to-float, padding temps)
    #   v1 = move-object p0 (cho range invoke)
    #   v2 = move p1 (cornerRadius, cho range invoke)
    #   v3 = paddingLeft, v4 = paddingTop
    #   v5 = right bound, v6 = bottom bound, v7 = 0 (false)
    return f"""\
.method public static {_METHOD_SIG}
    .locals 8

    if-nez p0, :cond_0

    return-void

    :cond_0
    invoke-virtual {{p0}}, {_VIEW}->getMeasuredWidth()I

    move-result v0

    if-eqz v0, :cond_2

    invoke-virtual {{p0}}, {_VIEW}->getMeasuredHeight()I

    move-result v0

    if-nez v0, :cond_1

    goto :goto_0

    :cond_1
    invoke-static {{p0}}, {_BLUR}->resetBlurColor({_VIEW})V

    const-string v0, "{DOCK_BLUR_SWITCH}"

    invoke-static {{v0}}, {_TOOLBOX}->getBooleanGlobal(Ljava/lang/String;)Z

    move-result v0

    if-eqz v0, :use_system

    const-string v0, "{BLUR_SYSTEM}"

    invoke-static {{v0}}, {_TOOLBOX}->getIntGlobal(Ljava/lang/String;)I

    move-result v0

    if-nez v0, :use_system

    const-string v0, "{BLUR_RADIUS}"

    invoke-static {{v0}}, {_TOOLBOX}->getIntGlobal(Ljava/lang/String;)I

    move-result p2

    if-eqz p2, :use_system

    int-to-float v0, p2

    invoke-static {{v0}}, {_DIMEN}->dp2px(F)I

    move-result p2

    const/4 v0, 0x1

    invoke-static {{p0, v0, v0, p2}}, {_BLUR}->setContainerBlur({_VIEW}IZI)V

    invoke-static {{p0, v0}}, {_BLUR}->setViewBlur({_VIEW}I)V

    goto :apply_rect

    :use_system
    invoke-static {{p0, p2}}, {_BLUR}->setViewBlur({_VIEW}I)V

    invoke-static {{p0}}, {_FBLUR}->setBlurColorList({_VIEW})V

    :apply_rect
    invoke-virtual {{p0}}, {_VIEW}->getPaddingLeft()I

    move-result v3

    invoke-virtual {{p0}}, {_VIEW}->getPaddingTop()I

    move-result v4

    invoke-virtual {{p0}}, {_VIEW}->getMeasuredWidth()I

    move-result p2

    invoke-virtual {{p0}}, {_VIEW}->getPaddingRight()I

    move-result v0

    sub-int v5, p2, v0

    invoke-virtual {{p0}}, {_VIEW}->getMeasuredHeight()I

    move-result p2

    invoke-virtual {{p0}}, {_VIEW}->getPaddingBottom()I

    move-result v0

    sub-int v6, p2, v0

    const/4 v7, 0x0

    move-object v1, p0

    move v2, p1

    invoke-static/range {{v1 .. v7}}, {_BLUR}->setBlurRoundRect({_VIEW}IIIIIZ)V

    :cond_2
    :goto_0
    return-void
.end method
"""


_METHOD_RE = re.compile(
    r"(?m)^\.method\s+public\s+static\s+setFolderIconBlur\(Landroid/view/View;II\)V\b.*?^\.end method[^\n]*\n",
    re.S,
)


def patch_file(path: Path) -> bool:
    text = path.read_text()
    original = text

    m = _METHOD_RE.search(text)
    if not m:
        raise ValueError(f"{_METHOD_SIG} không tìm thấy")

    if BLUR_SYSTEM in m.group(0):
        return False  # đã patch rồi

    text = _METHOD_RE.sub(build_method(), text, count=1)

    if text != original:
        path.write_text(text)
        return True
    return False


def main(argv: list[str]) -> int:
    if not argv:
        print(
            "usage: patch_miuihome_folder_blur.py FolderBlurUtilities.smali [...]",
            file=sys.stderr,
        )
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
