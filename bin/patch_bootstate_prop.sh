#!/bin/bash
# Cài vs_prop vào system + init.rc + SELinux để ghi đè các ro.boot.* xác thực
# boot mà GIỮ NGUYÊN serial counter của init (= 0), qua mặt nativecheck đọc counter.
#
# vs_prop = build từ resetprop-rs (pure Rust). Dùng cờ -n (quiet/preserve-serial):
# chỉ ghi value + len byte, KHÔNG đụng counter → counter ở đâu init để đó (= 0).
# Chạy trong domain init (exec u:r:init:s0 + execute_no_trans) nên có quyền ghi
# property area.
#
# LƯU Ý: KHÔNG dùng --init hay --stealth. Cả hai đều BUMP counter như 1 update
# (init add sẵn counter 0 -> thành 2) → nativecheck đọc counter thấy 2. Chỉ -n
# (write_value_quiet_preserve_serial) mới giữ counter = 0. Điều kiện: init add
# prop đúng 1 lần (counter gốc = 0) — đúng với ro.boot.* trên máy đích.
#
# Idempotent: chạy lại nhiều lần không nhân bản. fail-closed: thiếu file đích thì
# báo lỗi thay vì tạo bừa.
set -euo pipefail

root="${1:-}"
binary="${2:-}"

if [[ -z "$root" || ! -d "$root" ]]; then
  echo "[FAIL] BootStateProp: img_temp root không hợp lệ: ${root:-empty}" >&2
  exit 1
fi
if [[ -z "$binary" || ! -f "$binary" ]]; then
  echo "[FAIL] BootStateProp: không tìm thấy binary vs_prop: ${binary:-empty}" >&2
  exit 1
fi

BIN_NAME="vs_prop"
SE_TYPE="vs_prop_exec"

# ---- Các prop cần set (init-style serial) ----
# NAME|VALUE
PROPS=(
  "ro.boot.verifiedbootstate|green"
  "ro.boot.flash.locked|1"
  "ro.boot.vbmeta.device_state|locked"
  "sys.oem_unlock_allowed|0"
)

sys_bin="$root/system/system/bin/$BIN_NAME"
rc_file="$root/system/system/etc/init/vs-bootstate.rc"
fs_config="$root/config/system_fs_config"
build_ctx="$root/config/system_file_contexts"
plat_ctx="$root/system/system/etc/selinux/plat_file_contexts"
plat_cil="$root/system/system/etc/selinux/plat_sepolicy.cil"

for f in "$fs_config" "$build_ctx" "$plat_ctx" "$plat_cil"; do
  if [[ ! -f "$f" ]]; then
    echo "[FAIL] BootStateProp: thiếu file đích ${f#"$root"/}" >&2
    exit 1
  fi
done
if [[ ! -d "$root/system/system/etc/init" ]]; then
  echo "[FAIL] BootStateProp: thiếu thư mục system/etc/init" >&2
  exit 1
fi

echo "[START] BootStateProp: cài $BIN_NAME + set ${#PROPS[@]} prop"

# 1) Binary ---------------------------------------------------------------
install -D -m 0755 "$binary" "$sys_bin"
echo "[DONE]  BootStateProp: system/bin/$BIN_NAME ($(du -h "$sys_bin" | cut -f1))"

# 2) init .rc (on post-fs-data, chạy trong domain init) -------------------
{
  echo "# VSTeam: ghi đè value verified-boot props nhưng GIỮ counter của init=0 (vs_prop -n)."
  echo "# Chạy trong domain init để được ghi thẳng property area."
  echo "on post-fs-data"
  for entry in "${PROPS[@]}"; do
    name="${entry%%|*}"
    value="${entry#*|}"
    echo "    exec u:r:init:s0 root root -- /system/bin/$BIN_NAME -n $name $value"
  done
} > "$rc_file"
chmod 0644 "$rc_file"
echo "[DONE]  BootStateProp: system/etc/init/vs-bootstate.rc"

# Helper: thêm 1 dòng vào file nếu chưa có (so khớp cả dòng) ---------------
append_once() {
  local line="$1" file="$2"
  if grep -Fxq "$line" "$file"; then
    return 0
  fi
  printf '%s\n' "$line" >> "$file"
  return 10   # đã thêm
}

# 3) fs_config (perm) ------------------------------------------------------
append_once "system/system/bin/$BIN_NAME 0 2000 0755" "$fs_config" || true

# 4) file_contexts build-time (nhãn khi mkfs.erofs) ------------------------
append_once "/system/system/bin/$BIN_NAME u:object_r:${SE_TYPE}:s0" "$build_ctx" || true

# 5) file_contexts runtime -------------------------------------------------
append_once "/system/bin/$BIN_NAME u:object_r:${SE_TYPE}:s0" "$plat_ctx" || true

# 6) plat_sepolicy.cil: khai báo type + cho init execute_no_trans ----------
#    Không có type thì init không exec được vs_prop trong domain init.
if grep -q "(type ${SE_TYPE})" "$plat_cil"; then
  echo "[SKIP]  BootStateProp: CIL type ${SE_TYPE} đã tồn tại"
else
  {
    echo ""
    echo "; VSTeam: vs_prop exec type"
    echo "(type ${SE_TYPE})"
    echo "(roletype object_r ${SE_TYPE})"
    echo "(typeattributeset file_type (${SE_TYPE}))"
    echo "(typeattributeset exec_type (${SE_TYPE}))"
    echo "(typeattributeset system_file_type (${SE_TYPE}))"
    echo "(allow init ${SE_TYPE} (file (read getattr map execute open execute_no_trans)))"
  } >> "$plat_cil"
  echo "[DONE]  BootStateProp: plat_sepolicy.cil +${SE_TYPE}"
fi

echo "[DONE]  BootStateProp: hoàn tất"
