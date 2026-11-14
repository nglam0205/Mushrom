#!/bin/bash
# Ẩn ro.boot.vbmeta.device_state khỏi app bên thứ 3 / app người dùng cài.
# Prop stock map vào bootloader_prop, mà base policy có
#   (allow domain bootloader_prop (file (read getattr map open)))
# nên mọi app đọc được. Ta chuyển prop sang type riêng rồi chỉ cho mọi domain
# TRỪ untrusted_app_all đọc -> system/priv/platform app (gồm com.bhlnk.vsteam
# chạy system_app) vẫn đọc được, chỉ app untrusted bị chặn.
set -euo pipefail

root="${1:-}"
prop="ro.boot.vbmeta.device_state"
prop_type="vbmeta_device_state_prop"
reader_attr="vbmeta_state_reader"
blocked_attr="untrusted_app_all"
context_line="$prop u:object_r:${prop_type}:s0 exact string"

if [[ -z "$root" || ! -d "$root" ]]; then
  echo "[FAIL] VbmetaSELinux: img_temp root khong hop le: ${root:-empty}" >&2
  exit 1
fi

plat_contexts="$root/system/system/etc/selinux/plat_property_contexts"
plat_cil="$root/system/system/etc/selinux/plat_sepolicy.cil"
for f in "$plat_contexts" "$plat_cil"; do
  if [[ ! -f "$f" ]]; then
    echo "[FAIL] VbmetaSELinux: thieu ${f#"$root"/}" >&2
    exit 1
  fi
done

echo "[START] VbmetaSELinux: chan third-party/user app doc $prop"

# Entry exact được ưu tiên hơn prefix ro.boot.*. Xoá entry exact của prop ở mọi
# partition (tránh nơi khác đè), rồi giữ một entry chuẩn trong plat_property_contexts.
context_patched=0
while IFS= read -r -d '' file; do
  tmp="$(mktemp)"
  if [[ "$file" == "$plat_contexts" ]]; then
    awk -v repl="$context_line" '
      /^[[:space:]]*ro\.boot\.vbmeta\.device_state([[:space:]]|$)/ { if (!f++) print repl; next }
      { print }
      END { if (!f) print repl }
    ' "$file" > "$tmp"
  else
    awk '!/^[[:space:]]*ro\.boot\.vbmeta\.device_state([[:space:]]|$)/' "$file" > "$tmp"
  fi

  if cmp -s "$file" "$tmp"; then
    rm -f "$tmp"
  else
    mv "$tmp" "$file"
    context_patched=$((context_patched + 1))
  fi
done < <(find "$root" -type f -name '*property_contexts' -print0 | sort -z)

# Chuyển prop sang type riêng (thoát base rule bootloader_prop) rồi chỉ cho
# domain (not untrusted_app_all) đọc.
policy_lines=(
  "; VSTeam: hide ro.boot.vbmeta.device_state from third-party/user apps"
  "(type $prop_type)"
  "(roletype object_r $prop_type)"
  "(typeattributeset property_type ($prop_type))"
  "(typeattribute $reader_attr)"
  "(typeattributeset $reader_attr (and domain (not $blocked_attr)))"
  "(allow $reader_attr $prop_type (file (read getattr map open)))"
)
policy_patched=0
for line in "${policy_lines[@]}"; do
  grep -Fqx "$line" "$plat_cil" || {
    printf '%s\n' "$line" >> "$plat_cil"
    policy_patched=$((policy_patched + 1))
  }
done

echo "[DONE]  VbmetaSELinux: context files changed=$context_patched, policy lines added=$policy_patched"
