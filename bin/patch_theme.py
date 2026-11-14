import re
import sys

def PatchSuperIcon(smali_file_path):
    with open(smali_file_path, 'r') as file:
        smali_lines = file.readlines()

    patched_lines = []
    v1 = None
    v3 = None
    t0 = True
    t1 = True
    for i, line in enumerate(smali_lines):

        line = line.replace("DRM_ERROR_UNKNOWN", "DRM_SUCCESS")

        if t1:
            if t0:
                if "invoke-virtual" in line and "Ljava/io/File;->getName()Ljava/lang/String;" in line:
                    v1 = line.split("{")[1].split("}")[0]
                    
                if v1:
                    if "move-result-object" in line:
                        v3 = line.split("move-result-object ")[1].strip()
                        if v3:
                            t0 = False

            else:
                patched_lines.append(f"    invoke-static {{v3}}, Lcom/android/vsteam/Toolbox;->PatchSuperIcon(Ljava/lang/String;)V\n")
                t1 = False     

        patched_lines.append(line)

    with open(smali_file_path, 'w') as file:
        file.writelines(patched_lines)


def LocalImportDrm(smali_file_path):
    # DrmService.q(Resource): resource import local (vd font VSTeam tự đóng gói .mtz) không có
    # onlineId/productId nên không bao giờ có file rights .mra -> cho DRM_SUCCESS.
    # Resource từ store luôn có productId nên vẫn kiểm tra như cũ.
    res = "Lcom/android/thememanager/basemodule/resource/model/Resource;"
    sig = f".method public q({res})Lmiui/drm/DrmManager$DrmResult;"
    label = ":vs_local_import_drm"
    inject = (
        f"    invoke-virtual {{p1}}, {res}->getOnlineId()Ljava/lang/String;\n"
        "    move-result-object v0\n"
        "    invoke-static {v0}, Landroid/text/TextUtils;->isEmpty(Ljava/lang/CharSequence;)Z\n"
        "    move-result v0\n"
        f"    if-eqz v0, {label}\n"
        f"    invoke-virtual {{p1}}, {res}->getProductId()Ljava/lang/String;\n"
        "    move-result-object v0\n"
        "    invoke-static {v0}, Landroid/text/TextUtils;->isEmpty(Ljava/lang/CharSequence;)Z\n"
        "    move-result v0\n"
        f"    if-eqz v0, {label}\n"
        "    sget-object v0, Lmiui/drm/DrmManager$DrmResult;->DRM_SUCCESS:Lmiui/drm/DrmManager$DrmResult;\n"
        "    return-object v0\n"
        f"    {label}\n"
    )

    with open(smali_file_path, 'r') as file:
        smali_lines = file.readlines()
    if any(label in line for line in smali_lines):
        return

    patched_lines = []
    in_method = False
    done = False
    for line in smali_lines:
        patched_lines.append(line)
        if done:
            continue
        if line.strip() == sig:
            in_method = True
        elif in_method and line.strip() == ".end annotation":
            patched_lines.append(inject)
            done = True

    with open(smali_file_path, 'w') as file:
        file.writelines(patched_lines)


def Bought(smali_file_path):
    with open(smali_file_path, 'r') as file:
        smali_lines = file.readlines()

    patched_lines = []
    v3 = None
    t0 = True
    t1 = True
    v1 = None
    for i, line in enumerate(smali_lines):

        if t1:
            if t0:
                if "iget-boolean" in line and "Lcom/android/thememanager/detail/theme/model/OnlineResourceDetail;->bought:Z" in line:
                    t0 = False
            else:
                patched_lines.append(f"    const v3, 0x1\n    iput-boolean v3, p0, Lcom/android/thememanager/detail/theme/model/OnlineResourceDetail;->bought:Z\n")
                t1 = False   

        patched_lines.append(line)

    with open(smali_file_path, 'w') as file:
        file.writelines(patched_lines)


def PatchRearScreen(smali_file_path):
    needle = (
        "Lcom/rearScreen/bean/RearScreenListItemBean;->rightPath:Ljava/lang/String;"
    )
    invoke = (
        "    invoke-static {p0}, "
        "Lcom/android/vsteam/Toolbox;->PatchSuperIcon(Ljava/lang/String;)V\n"
    )

    with open(smali_file_path, "r") as f:
        lines = f.readlines()

    patched = []
    for i, line in enumerate(lines):
        patched.append(line)
        next_line = lines[i + 1] if i + 1 < len(lines) else ""
        if (
            "iget-object" in line
            and "p0, p0" in line
            and needle in line
            and "PatchSuperIcon(Ljava/lang/String;)V" not in next_line
        ):
            patched.append(invoke)

    with open(smali_file_path, "w") as f:
        f.writelines(patched)


def Price(smali_file_path):
    with open(smali_file_path, 'r') as file:
        smali_lines = file.readlines()

    patched_lines = []
    t0 = True
    t2 = 0
    for i, line in enumerate(smali_lines):

        if t0:
            if ".method" in line and "(II)V" in line:
                t0 = False 
        else :
            patched_lines.append("    const p2, 0x0\n")
            t0 = True

        patched_lines.append(line)

    with open(smali_file_path, 'w') as file:
        file.writelines(patched_lines)


if __name__ == "__main__":
    argc = len(sys.argv)
    if argc not in (4, 5):
        sys.exit(1)

    icon = sys.argv[1]
    buy = sys.argv[2]
    pr = sys.argv[3]

    PatchSuperIcon(icon)
    LocalImportDrm(icon)
    Bought(buy)
    Price(pr)
    if argc == 5:
        PatchRearScreen(sys.argv[4])