#!/bin/bash
set -euo pipefail

root="${1:-.}"

if [ ! -d "$root" ]; then
  echo "ROM root not found: $root" >&2
  exit 1
fi

target="$(find "$root" -type f -name 'system_ext_sepolicy.cil' | sort | head -n 1)"

if [ -z "$target" ] || [ ! -f "$target" ]; then
  echo "Không tìm thấy system_ext_sepolicy.cil trong: $root" >&2
  exit 1
fi

verbose="${VS_VERBOSE_LOG:-0}"
echo "[START] SELinuxPatch - system_ext_sepolicy.cil: ${target#"$root"/}"

policy_has_symbol() {
  local symbol="$1"
  grep -R -q -E "(^|[[:space:]()])${symbol}([[:space:]()]|$)" "$root" \
    --include="*.cil" \
    --include="*.te" \
    --include="*.conf"
}

# Android 17 tách privileged app target SDK mới sang priv_app_36. Chỉ thêm
# domain này khi policy của ROM có khai báo để script vẫn dùng được cho ROM cũ.
DOMAINS=(priv_app)
if policy_has_symbol priv_app_36; then
  DOMAINS+=(priv_app_36)
fi

RULES=()

for domain in "${DOMAINS[@]}"; do
  RULES+=(
    "(allow $domain media_rw_data_file (dir (getattr search open read write add_name remove_name create rmdir setattr ioctl lock watch watch_reads)))"
    "(allow $domain media_rw_data_file (file (getattr open read write append create unlink rename setattr ioctl lock map watch watch_reads)))"
  )

  # Cho phép VSTool đọc thông tin pin từ vendor sysfs nếu ROM có type này.
  if policy_has_symbol vendor_sysfs_battery_supply; then
    RULES+=("(allow $domain vendor_sysfs_battery_supply (file (getattr open read)))")
  fi

  # Các ROM có thể gán node pin cho type platform sysfs_battery_supply
  # thay vì vendor_sysfs_battery_supply. Chỉ thêm rule khi type tồn tại.
  if policy_has_symbol sysfs_battery_supply; then
    RULES+=(
      "(allow $domain sysfs_battery_supply (dir (getattr search open read)))"
      "(allow $domain sysfs_battery_supply (file (getattr open read)))"
    )
  fi

  # Cho phép đọc các thermal zone chuẩn khi ROM khai báo type tương ứng.
  if policy_has_symbol sysfs_thermal; then
    RULES+=(
      "(allow $domain sysfs_thermal (dir (getattr search open read)))"
      "(allow $domain sysfs_thermal (file (getattr open read)))"
    )
  fi

  # Nhiều ROM MTK/Oplus dùng type sysfs_therm (không phải sysfs_thermal chuẩn
  # AOSP) cho /sys/devices/virtual/thermal. Thêm rule khi type này tồn tại,
  # vì sysfs_thermal có thể được khai báo nhưng không thực sự gán cho path đó.
  if policy_has_symbol sysfs_therm; then
    RULES+=(
      "(allow $domain sysfs_therm (dir (getattr search open read)))"
      "(allow $domain sysfs_therm (file (getattr open read)))"
    )
  fi

  # /data/vendor/battery (CSV log pin) trên ROM Oplus/OnePlus MTK được gán
  # label oplus_battery_log_file. Cho phép đọc khi type tồn tại.
  if policy_has_symbol oplus_battery_log_file; then
    RULES+=(
      "(allow $domain oplus_battery_log_file (dir (getattr search open read)))"
      "(allow $domain oplus_battery_log_file (file (getattr open read)))"
    )
  fi

  # VSTool > Cập nhật ROM: cài gói OTA qua update_engine không cần root.
  # App tìm service, gọi binder và nhận callback từ update_engine, đồng thời
  # chép payload vào /data/ota_package (ota_package_file) để update_engine
  # đọc được. AOSP chỉ cấp các quyền này cho gmscore_app/system_app.
  if policy_has_symbol update_engine_service && policy_has_symbol ota_package_file; then
    RULES+=(
      "(allow $domain update_engine_service (service_manager (find)))"
      "(allow $domain update_engine (binder (call transfer)))"
      "(allow update_engine $domain (binder (call transfer)))"
      "(allow $domain ota_package_file (dir (getattr search open read write add_name remove_name)))"
      "(allow $domain ota_package_file (file (getattr open read write create setattr unlink)))"
    )
  fi

  # VSTool đọc serial trực tiếp từ kernel (androidboot.serialno trong
  # /proc/bootconfig, hoặc /proc/cmdline trên kernel cũ) và từ ro.serialno
  # bằng native: giá trị trong bootconfig/cmdline không phải property nên
  # resetprop không sửa được. AOSP giấu cả ba với app.
  for type in proc_bootconfig proc_cmdline; do
    if policy_has_symbol "$type"; then
      RULES+=("(allow $domain $type (file (getattr open read)))")
    fi
  done
  if policy_has_symbol serialno_prop; then
    RULES+=("(allow $domain serialno_prop (file (getattr open read map)))")
  fi

  # Kết nối tới property_service. Quyền set type cụ thể được cấp bên dưới.
  RULES+=(
    "(allow $domain property_socket (sock_file (write)))"
    "(allow $domain init (unix_stream_socket (connectto)))"
  )

  # VSTool set prop (tên máy, persist.sys.*...). Android 17 đưa priv app
  # targetSdk < 37 (gồm VSTool) sang priv_app_36 nên phải cấp cho cả domain này.
  RULES+=("(allow $domain property_type (property_service (set)))")

done

rules=""

for rule in "${RULES[@]}"; do
  rules="${rules}${rule}"$'\n'
done

if [ "$verbose" = "1" ]; then
  echo "[CHECK] SELinuxPatch - neverallow: scanning"
fi

while IFS= read -r rule; do
  [ -n "$rule" ] || continue

  src="$(echo "$rule" | awk '{print $2}')"
  tgt="$(echo "$rule" | awk '{print $3}')"
  cls="$(echo "$rule" | sed -n 's/.*(\([a-zA-Z0-9_]*\) (.*/\1/p')"

  if grep -R "neverallow.*$src.*$tgt.*$cls" "$root" \
    --include="*.cil" \
    --include="*.te" \
    --include="*.conf" >/dev/null 2>&1; then

    echo "[FAIL]  SELinuxPatch - neverallow: rule may conflict"
    echo "  - Rule: $rule"
    echo "  - Action: skip to avoid compile/bootloop"
    exit 1
  fi
done <<< "$rules"

if [ "$verbose" = "1" ]; then
  echo "[DONE]  SELinuxPatch - neverallow: no obvious conflict"
fi

missing_rules=""

while IFS= read -r rule; do
  [ -n "$rule" ] || continue

  if grep -Fxq "$rule" "$target"; then
    if [ "$verbose" = "1" ]; then
      echo "[SKIP]  SELinuxPatch - rule exists: $rule"
    fi
  else
    missing_rules="${missing_rules}${rule}"$'\n'
  fi
done <<< "$rules"

if [ -z "$missing_rules" ]; then
  echo "[SKIP]  SELinuxPatch - system_ext_sepolicy.cil: rules already exist"
  exit 0
fi

hash_file="${target%.cil}_and_mapping.sha256"
mapping_file=""
hash_mode="none"

if [ -f "$hash_file" ]; then
  old_hash="$(tr -d '[:space:]' < "$hash_file")"
  map_dir="$(dirname "$target")/mapping"

  if [ -d "$map_dir" ]; then
    while IFS= read -r map; do
      current_hash="$(cat "$target" "$map" | sha256sum | awk '{print $1}')"
      if [ "$current_hash" = "$old_hash" ]; then
        mapping_file="$map"
        hash_mode="mapping"
        break
      fi
    done < <(find "$map_dir" -maxdepth 1 -type f -name '*.cil' | sort -V)
  fi

  if [ "$hash_mode" = "none" ]; then
    current_hash="$(sha256sum "$target" | awk '{print $1}')"
    if [ "$current_hash" = "$old_hash" ]; then
      hash_mode="cil-only"
    fi
  fi

  if [ "$hash_mode" = "none" ]; then
    echo "Warning: không xác định được cách hash, sẽ bỏ qua update hash."
  fi
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

awk -v add="$missing_rules" '
  BEGIN {
    add_count = split(add, add_lines, "\n")
  }
  {
    print $0
  }
  END {
    print ""
    print "; Added by patch script"
    for (j = 1; j <= add_count; j++) {
      if (add_lines[j] != "") print add_lines[j]
    }
  }
' "$target" > "$tmp"

missing_count="$(printf '%s' "$missing_rules" | sed '/^$/d' | wc -l | tr -d ' ')"
if [ "$verbose" = "1" ]; then
  echo "[ADD]   SELinuxPatch - rule: $missing_count rule(s)"
  printf '%s' "$missing_rules"
fi

cp "$tmp" "$target"
echo "[DONE]  SELinuxPatch - system_ext_sepolicy.cil: patched $missing_count rule(s)"

if [ -f "$hash_file" ] && [ "$hash_mode" != "none" ]; then
  if [ "$hash_mode" = "mapping" ]; then
    new_hash="$(cat "$target" "$mapping_file" | sha256sum | awk '{print $1}')"
  else
    new_hash="$(sha256sum "$target" | awk '{print $1}')"
  fi

  printf '%s\n' "$new_hash" > "$hash_file"
  echo "[DONE]  SELinuxPatch - hash: updated ${hash_file#"$root"/}"
fi

# /proc/bootconfig is left to root and group system (init.rc, "on boot":
# chown root system, mode 0440), so the SELinux rule above alone does not let
# VSTools read androidboot.serialno. Open the mode after init.rc set the owner
# (actions of one trigger run in the order the files are parsed, init.rc
# first); SELinux still keeps out every domain that has no rule.
init_dir="$root/system/system/etc/init"
if [ -d "$init_dir" ]; then
  {
    echo "# VSTeam: lets VSTools (priv_app, see system_ext_sepolicy.cil) read the serial"
    echo "# the bootloader handed to the kernel."
    echo "on boot"
    echo "    chmod 0444 /proc/bootconfig"
  } > "$init_dir/vs-bootconfig.rc"
  chmod 0644 "$init_dir/vs-bootconfig.rc"
  echo "[DONE]  SELinuxPatch - init: system/etc/init/vs-bootconfig.rc"
else
  echo "[SKIP]  SELinuxPatch - init: no system/etc/init, /proc/bootconfig stays closed to VSTools"
fi
