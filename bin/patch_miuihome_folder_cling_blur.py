#!/usr/bin/env python3
"""
Patch FolderClingBlurController.smali – thay thế initFolderClingBlurParams()
bằng phiên bản đọc cấu hình từ VS settings thay vì giá trị cứng.

Gate/logic (chia sẻ với dock blur):
  vs_miuihome_dock_blur   – boolean: gate tổng, tắt = dùng gốc
  vs_miuihome_system_blur – int: 1 = dùng system blur (gốc), 0 = custom

Settings keys khi custom (dùng chung với dock):
  vs_miuihome_blur_radius  – int dp        (mặc định gốc: 60)
  vs_miuihome_blur_alpha   – int 0-255     (mặc định gốc: 100, dùng chung 2 màu)

Màu overlay giữ nguyên gốc: 0xFF828282 (xám) và 0xFF1ACC00 (xanh lá).

Register layout trong method đã patch (.locals 7, v0–v6):
  v0 – BlurStyle new-instance
  v1 – blur radius (px)
  v2 – color2  (0xFF1ACC00, hardcoded)
  v3 – alpha   (từ settings hoặc 100, dùng chung)
  v4 – color1  (0xFF828282, hardcoded)
  v5 – alpha   (copy của v3)
  v6 – temp (const-string key / int-to-float / check flags)
"""
import re
import sys
from pathlib import Path


DOCK_BLUR_SWITCH = "vs_miuihome_dock_blur"
BLUR_SYSTEM      = "vs_miuihome_system_blur"
BLUR_RADIUS      = "vs_miuihome_blur_radius"
BLUR_ALPHA       = "vs_miuihome_blur_alpha"

_CLASS   = "Lcom/miui/home/folder/blur/FolderClingBlurController;"
_TOOLBOX = "Lcom/android/vsteam/Toolbox;"
_DIMEN   = "Lcom/miui/home/common/utils/DimenUtils;"
_STYLE   = "Lcom/miui/home/common/blur/BlurStyle;"
_FOLME   = "Lmiuix/animation/Folme;"
_FSTYLE  = "Lmiuix/animation/FolmeStyle;"
_FLOAT   = "Ljava/lang/Float;"


def build_method() -> str:
    return f"""\
.method private initFolderClingBlurParams()V
    .locals 7

    const-string v0, "folderClingAnim"

    filled-new-array {{v0}}, [Ljava/lang/Object;

    move-result-object v0

    invoke-static {{v0}}, {_FOLME}->useValue([Ljava/lang/Object;)Lmiuix/animation/IStateStyle;

    move-result-object v0

    const/4 v1, 0x0

    invoke-static {{v1}}, {_FLOAT}->valueOf(F)Ljava/lang/Float;

    move-result-object v1

    const-string v2, "folder_cling_blur"

    filled-new-array {{v2, v1}}, [Ljava/lang/Object;

    move-result-object v1

    invoke-interface {{v0, v1}}, {_FSTYLE}->setTo([Ljava/lang/Object;)Lmiuix/animation/IStateStyle;

    new-instance v0, {_STYLE}

    const-string v6, "{DOCK_BLUR_SWITCH}"

    invoke-static {{v6}}, {_TOOLBOX}->getBooleanGlobal(Ljava/lang/String;)Z

    move-result v6

    if-eqz v6, :cond_default

    const-string v6, "{BLUR_SYSTEM}"

    invoke-static {{v6}}, {_TOOLBOX}->getIntGlobal(Ljava/lang/String;)I

    move-result v6

    if-nez v6, :cond_default

    const-string v6, "{BLUR_RADIUS}"

    invoke-static {{v6}}, {_TOOLBOX}->getIntGlobal(Ljava/lang/String;)I

    move-result v1

    int-to-float v6, v1

    invoke-static {{v6}}, {_DIMEN}->dp2px(F)I

    move-result v1

    const-string v6, "{BLUR_ALPHA}"

    invoke-static {{v6}}, {_TOOLBOX}->getIntGlobal(Ljava/lang/String;)I

    move-result v3

    move v5, v3

    const v4, -0x7d7d7e

    const v2, -0xe53400

    goto :goto_build

    :cond_default
    const/high16 v1, 0x42700000

    invoke-static {{v1}}, {_DIMEN}->dp2px(F)I

    move-result v1

    const v2, -0xe53400

    const/16 v3, 0x6a

    const v4, -0x7d7d7e

    const/16 v5, 0x64

    :goto_build
    filled-new-array {{v4, v5, v2, v3}}, [I

    move-result-object v2

    const v3, 0x3d8f5c29

    invoke-direct {{v0, v1, v3, v2}}, {_STYLE}-><init>(IF[I)V

    iput-object v0, p0, {_CLASS}->mFolderClingBlurStyle:{_STYLE}

    return-void
.end method
"""


_METHOD_RE = re.compile(
    r"(?m)^\.method\s+private\s+initFolderClingBlurParams\(\)V\b.*?^\.end method[^\n]*\n",
    re.S,
)


def patch_file(path: Path) -> bool:
    text = path.read_text()
    original = text

    m = _METHOD_RE.search(text)
    if not m:
        raise ValueError("initFolderClingBlurParams() không tìm thấy")

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
            "usage: patch_miuihome_folder_cling_blur.py FolderClingBlurController.smali [...]",
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
