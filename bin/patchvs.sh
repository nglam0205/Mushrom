#!/bin/bash

# The rest of bhlnk: globals, helpers and the command dispatcher. Sourced
# by ./bhlnk after it defines patchvs; run it through ./bhlnk, not directly.

if [[ -t 0 ]]; then
  sudo -v
fi

printf '\n  Mushrom Build Tool\n\n'

dir=$(pwd)

# Reads payload.bin in place inside the OTA zip (see bin/payload_extract.py).
payload_extract="python3 $dir/bin/payload_extract.py"

# CPUs the compressors (7za, mkfs.erofs) may use: $VS_BUILD_CPUS, else all.
# The GitHub workflow sets it below the runner's cores: a runner whose CPUs
# are all busy stops answering and the job is cancelled.
build_cpus() {
  local cpus="${VS_BUILD_CPUS:-}"
  [[ "$cpus" =~ ^[1-9][0-9]*$ ]] || cpus="$(nproc)"
  echo "$cpus"
}
ota_payload="python3 $dir/bin/ota_payload.py"
# Static delta_generator: the AOSP otatools build (bin/otatools) segfaults in
# its XZ encoder (LzFind.c) on full payloads. This one cannot estimate lz4 COW
# (estimate_cow_size = image size + 84, error 28 on mi_ext), so ota_payload.py
# rewrites the estimates in the manifest before signing.
delta_generator="$dir/bin/delta_generator"
ota_key_dir="$dir/bin/ota_keys"
ota_certs_zip="$dir/bin/otacerts.zip"
# Derives each OTA zip password; shared with the API only (zip_secret_v2 next to
# it is the one new packages use, see bin/ota_zipcrypt.py).
ota_zip_secret="$dir/bin/ota_keys/zip_secret"
ota_meta="$dir/ota_meta.json"
# Extracted-ROM backups (see backup()). VS_BACKUP_DIR moves the store out of
# the workspace so it outlives it: the local farm wipes a slot after every job.
backup_root="${VS_BACKUP_DIR:-$dir/backup}"
backup_max_gb="${VS_BACKUP_MAX_GB:-300}"
fspatch="python3 $dir/bin/fspatch.py"
repS="python3 $dir/bin/strRep.py"
repM="python3 $dir/bin/repM.py"
repB="python3 $dir/bin/repB.py"
repR="python3 $dir/bin/repR.py"
remM="python3 $dir/bin/remove_method.py"
finM="python3 $dir/bin/find_method.py"
in4render="python3 $dir/bin/apkin4render.py"
avbclear="python3 $dir/bin/avbclear.py"
xmlset="python3 $dir/bin/xmlset.py"
invoke="python3 $dir/bin/invoke.py"
push="python3 $dir/bin/push.py"
pusht="python3 $dir/bin/pusht.py"

ptheme="python3 $dir/bin/patch_theme.py"
pgallery="python3 $dir/bin/patch_gallery.py"
plbe="python3 $dir/bin/patch_lbe.py"
camera="python3 $dir/bin/camera.py"
pdisplay="python3 $dir/bin/patch_display_id.py"
prowqs="python3 $dir/bin/patch_qs_rowlimit_smali.py"
pums="python3 $dir/bin/patch_ums.py"
lunar="python3 $dir/bin/lunar.py"
otastring="python3 $dir/bin/otastring.py"
patch_x9bl="python3 $dir/bin/patch_x9bl.py"
patch_init_ro="python3 $dir/bin/patch_init_ro.py"
patch_oplus_camera_config="python3 $dir/bin/patch_oplus_camera_config.py"
patch_oplus_touch_rate="python3 $dir/bin/patch_oplus_touch_rate.py"
patch_carrierlock="python3 $dir/bin/patch_carrierlock_smali.py"

erofsup="$dir/bin/erofsUpack"
erofsrp="$dir/bin/mkfs.erofs"
zipalign_sa="$dir/bin/zipalign"
rclone="$dir/bin/rclone1"
aapt2_b="$dir/bin/aapt2"
apks_paths="$dir/apks_paths"
sunpack="$dir/bin/lpunpack"

hyperos_extract_parts="mi_ext system_ext system vendor product odm"

coloros_extract_parts="
    system \
    my_product \
    my_stock \
    product \
    system_ext \
    vendor \
    my_engineering \
    my_region "

get_extract_parts() {
  if [[ "$brand" == "ColorOS" ]]; then
    echo "$coloros_extract_parts"
  else
    echo "$hyperos_extract_parts"
  fi
}

is_coloros_device_code() {
  local code="$1"

  code="$(printf "%s" "$code" | tr -d '[:space:]')"
  [[ -z "$code" ]] && return 1

  if [[ -f "$dir/fastboot/colourvs.list" ]] && grep -iqx "$code" "$dir/fastboot/colourvs.list"; then
    return 0
  fi

  [[ "$code" =~ ^[[:alpha:]]{3}[[:digit:]]{3,4}(IN)?$ ]]
}

resolve_lk_image() {
  local firmware_dir="$1"
  echo "$firmware_dir/lk.img"
}

super_part="my_bigball \
    my_carrier \
    my_company \
    my_engineering \
    my_heytap \
    my_manifest \
    my_preload \
    my_product \
    my_region \
    my_stock \
    mi_ext \
    mi_product \
    odm \
    odm_dlkm \
    product \
    product_dlkm \
    system \
    system_dlkm \
    system_ext \
    system_ext_dlkm \
    vendor \
    vendor_dlkm"

branch_name="${TOOL_BRANCH:-${CI_COMMIT_BRANCH:-$(git -C "$dir" branch --show-current 2>/dev/null)}}"
case "$branch_name" in
STABLE)
  isPRE=false
  ;;
PREVIEW)
  isPRE=true
  ;;
*)
  isPRE=true
  ;;
esac
ver="8.2"
api="31"
da=$(date "+%d%m%y")
lang=product

source $dir/bin/logging.sh
source $dir/bin/apk.sh
source $dir/bin/partutils.sh

GREEN="\e[32m"
YELLOW="\e[33m"
RED="\e[31m"
CYAN="\e[36m"
RESET="\e[0m"


get_patch() {
  local temp_ver="$ver"
  if [[ "$brand" == "ColorOS" ]]; then
    temp_ver="${temp_ver}1"
  fi
  # Channel is either PRE or ST (builds before this used PRE.ST for preview).
  if [[ $isPRE == "true" ]]; then
    temp_ver="$temp_ver.PRE"
  else
    temp_ver="$temp_ver.ST"
  fi
  echo "$temp_ver"
}

format_duration() {
  local elapsed="$1"
  local hours=$((elapsed / 3600))
  local minutes=$(((elapsed % 3600) / 60))
  local seconds=$((elapsed % 60))
  local out=""

  ((hours > 0)) && out+="${hours}h "
  ((minutes > 0)) && out+="${minutes}m "
  out+="${seconds}s"
  echo "$out"
}

pack_archive() {
  local scope="$1"
  local output="$2"
  local input="$3"
  local log_file="$dir/logs/${scope,,}_7za.log"
  local archive_size

  mkdir -p "$dir/logs"
  log_start "$scope" "$(basename "$output")" "7za"
  if ! nice -n 10 7za a -mmt"$(build_cpus)" -tzip "$output" "$input" >"$log_file" 2>&1; then
    rm -f "$output"
    log_block "$scope" "$(basename "$output")" "$log_file" 1
    return 1
  fi

  archive_size=$(du -h "$output" | awk '{print $1}')
  log_done "$scope" "$(basename "$output")" "7za archive size: $archive_size"
}

# On constrained CI runners the archive is the only artifact needed for upload,
# so drop the packed device tree (firmware images, encrypted super chunks)
# right away instead of keeping both copies until the job ends.
release_packed_sources() {
  local scope="$1"

  [[ "${CI_LOW_DISK:-false}" == "true" ]] || return 0
  is_local_builder && return 0
  [[ -n "${device:-}" && -d "$dir/$device/firmware-update" ]] || return 0

  cd "$dir" || return 1
  sudo rm -rf -- "$dir/$device/firmware-update"
  log_event info "$scope" firmware-update Clean "released after archive"
}

format_name() {
  zip_file="$1"

  if [[ ! -f "$zip_file" ]]; then
    echo "File not found: $zip_file"
    exit 1
  fi

  # Extract metadata content
  metadata=$(unzip -p "$zip_file" META-INF/com/android/metadata 2>/dev/null)

  # Parse required fields
  product_name=$(echo "$metadata" | grep '^product_name=' | cut -d= -f2)
  raw_version_name=$(echo "$metadata" | grep '^version_name=' | cut -d= -f2)
  build_type=$(echo "$metadata" | grep '^build_type=' | cut -d= -f2)
  display_os_version=$(echo "$metadata" | grep '^display_os_version=' | cut -d= -f2)

  if [[ -z "$product_name" || -z "$raw_version_name" || -z "$build_type" || -z "$display_os_version" ]]; then
    basename "$zip_file"
    return 0
  fi

  # Extract and clean version_name
  version_name=${raw_version_name#${product_name}_}
  version_name=$(echo "$version_name" | sed -E 's/\(([^)]*)\)/.\1/') # đổi (CN01) → .CN01
  version_name=$(echo "$version_name" | sed 's/[[:space:]]//g')      # loại bỏ khoảng trắng

  # Optional: generate a short hash from the original zip for uniqueness
  hash=$(sha1sum "$zip_file" | cut -c1-8)

  # Build new filename
  new_name="${product_name}-ota_full-OS${version_name}-${build_type}-${display_os_version}-${hash}.zip"

  # Rename the file
  [[ "$zip_file" != "$new_name" ]] && mv "$zip_file" "$new_name"

  echo "$new_name"
}

# Function
detect() {
  builder="$1"
  [[ "$builder" == "true" ]] && builder="LocalBuild"

  zipname="$(ls *.zip 2>/dev/null | head -1)"
  [[ -z "$zipname" ]] && zipname="$(ls *.tgz 2>/dev/null | head -1)"
  [[ -z "$zipname" ]] && zipname="$(basename "$2")"

  zipname=$(format_name "$zipname")

  if [[ "$zipname" == *"_OS"* && "$zipname" == *".zip"* ]]; then
    device="$(echo "$zipname" | awk -F"_" '{print $2}')"

    if [[ "$device" == *Global || "$device" == *INGlobal || "$device" == *TW ]]; then
      device="${device//INGlobal/}"
      device="${device//Global/}"
      device="${device//EEA/}"
      device="${device//TW/}"
    fi

    device="${device//PREDPP/}"
    device="${device//DPP/}"
    device="${device,,}"

    base="$(echo "$zipname" | awk -F"_" '{print $3}')"
    os="$(echo "$zipname" | awk -F"_" '{print $5}' | awk -F'.0.zip' '{print $1}')"

  elif [[ "$zipname" == *"ota_full"* ]]; then
    base="$(echo "$zipname" | awk -F"-" '{print $3}')"
    device="$(echo "$zipname" | awk -F"-" '{print $1}' | awk -F"_" '{print $1}')"
    device="${device,,}"
    region="$(echo "$zipname" | awk -F"-" '{print $1}' | awk -F"_" '{print $2}')"

    os="$(echo "$zipname" | awk -F"-" '{print $5}' | awk -F'.0' '{print $1}')"

  elif [[ "$zipname" == *"_"*".zip" ]]; then
    simple_device="$(echo "$zipname" | awk -F"_" '{print $1}')"
    simple_base="$(echo "$zipname" | awk -F"_" '{print $2}' | sed 's/\.zip$//')"

    if is_coloros_device_code "$simple_device"; then
      device="${simple_device,,}"
      base="$simple_base"
      os="$(echo "$simple_base" | awk -F"." '{print $1}')"
    fi

  elif [[ "$zipname" == *".tgz"* ]]; then
    device="$(echo "$zipname" | awk -F"_" '{print $1}')"
    device="${device,,}"

    if [[ "$zipname" == *"_demo_"* ]]; then
      base="$(echo "$zipname" | awk -F"_" '{print $4}')"
      os="$(echo "$zipname" | awk -F"_" '{print $6}' | awk -F'.0' '{print $1}')"
    else
      base="$(echo "$zipname" | awk -F"_" '{print $3}')"
      os="$(echo "$zipname" | awk -F"_" '{print $5}' | awk -F'.0' '{print $1}')"
    fi
  fi

  device_code="${device^^}"
  temp_dev="$device_code"

  if [[ "$base" == *"BETA"* ]]; then
    charcode="$(echo "$base" | rev | cut -d"." -f2 | rev)"
  else
    charcode="$(echo "$base" | rev | cut -d"." -f1 | rev)"
  fi

  iden="${base/$charcode/MUSHROM$da}"

  brand="HyperOS"
  if is_coloros_device_code "$device_code"; then
    brand="ColorOS"
  fi

  patch="$(get_patch)"
  stock_hash="$(basename "$zipname" | sed -nE 's/.*-([0-9a-fA-F]{8})(\.(zip|tgz))$/\1/p')"
  if [[ -z "$stock_hash" && -f "$zipname" ]]; then
    stock_hash="$(sha1sum "$zipname" | cut -c1-8)"
  fi

  final_repack="hypermushrom_${temp_dev}_${iden}_${patch%.OTA}.zip"
  if [[ "$brand" == "ColorOS" ]]; then
    final_repack="colourmushrom_${temp_dev}_${iden}_${patch%.OTA}.zip"
  fi

  final_dir="$dir/$device/*"

  if [[ -f "$dir/fastboot/fastbootd.list" ]] && grep -q "$device_code" "$dir/fastboot/fastbootd.list"; then
    isFastbootD=true
  else
    isFastbootD=false
  fi

  [[ "$device_code" =~ ^RMX ]] && isRealme=true || isRealme=false

  if [[ "$device_code" =~ ^OPD ]]; then
    isType="pad"
  elif [[ -f "$dir/fastboot/pad.list" ]] && grep -q "$device_code" "$dir/fastboot/pad.list"; then
    isType="pad"
  elif [[ -f "$dir/fastboot/fold.list" ]] && grep -q "$device_code" "$dir/fastboot/fold.list"; then
    isType="fold"
  else
    isType="phone"
  fi

  log_start Initialization "$builder"
  log_event info "  Detected" "" "" "$zipname"
  log_event info "  Device" "" "" "$device"
  log_event info "  Brand" "" "" "$brand"
  log_event info "  Base version" "" "" "$base"
  log_event info "  Android version" "" "" "$os"
  log_event info "  Tool version" "" "" "$patch"
  log_event info "  isFastbootD" "" "" "$isFastbootD"
  log_event info "  Type" "" "" "$isType"
  log_done Initialization "$builder"

  # info is sourced later: a URL's & and ? must not reach the shell bare.
  cat >info <<EOF
link=$(printf '%q' "$2")
buildtime=$da
builder=$builder
zipname=$zipname
final_dir=$final_dir
final_repack=$final_repack
iden=$iden
device=$device
brand=$brand
base=$base
os=$os
android=${android:-$os}
patch=$patch
stock_hash=$stock_hash
isFastbootD=$isFastbootD
isRealme=$isRealme
isType=$isType
EOF

  device="${device,,}"

  adjust_template TREE
  sudo chmod +x "$dir"/bin/*
}

adjust_template() {
  cp -rf "$dir/fastboot/$1" "$dir/$device"
  touch "$dir/$device/firmware-update/${device//pre/}.check"

  if [[ "$brand" == "ColorOS" ]]; then
    sudo rm $dir/$device/firmware-update/cust.img
  else
    sudo rm $dir/$device/firmware-update/my_company.img
    sudo rm $dir/$device/firmware-update/my_preload.img
  fi
}

overwirte_bl_state() {
  local firmware_dir="$dir/$device/firmware-update"
  local sboot="$firmware_dir/boot.img"
  local backup_boot="$firmware_dir/real_state_boot.img"
  local magiskboot="$dir/bin/magisk/magiskboot"
  local patched_cmdline="androidboot.verifiedbootstate=green androidboot.vbmeta.device_state=locked"
  local workdir=""
  local error=""

  if [[ ! -f "$sboot" ]]; then
    log_fail Bootloader boot.img "boot.img not found"
    return 1
  else
    cp -rf "$sboot" "$backup_boot"
  fi

  if [[ ! -x "$magiskboot" ]]; then
    log_fail Bootloader boot.img "magiskboot not found or not executable"
    return 1
  fi

  platfrom_chip=$(getprop 'Build.BRAND')

  # MTK hoặc fastbootd thì không cần patch boot state.
  if [[ "${platfrom_chip:-}" == "MTK" ]] || [[ "${isFastbootD:-}" == "true" ]]; then
    log_warn Bootloader boot.img \
      "Skip patch: brand=${brand:-unknown} fastbootd=${isFastbootD:-${isFastbootd:-false}}"
    return 0
  fi

  log_start Bootloader "$(basename "$sboot")"

  workdir=$(mktemp -d) || {
    log_fail Bootloader boot.img "cannot create temporary directory"
    return 1
  }

  while :; do
    cp "$sboot" "$workdir/boot.img" || {
      error="cannot copy boot.img"
      break
    }

    (cd "$workdir" && "$magiskboot" unpack -h boot.img >unpack.log 2>&1) || {
      error="magiskboot unpack failed"
      break
    }

    if [[ ! -f "$workdir/header" ]] || ! grep -q '^cmdline=' "$workdir/header"; then
      error="cmdline not found in boot header"
      break
    fi

    if ! grep -Fq "$patched_cmdline" "$workdir/header"; then
      sed -i "/^cmdline=/s|\$| $patched_cmdline|" "$workdir/header" || {
        error="cannot patch cmdline"
        break
      }
    fi

    (cd "$workdir" && "$magiskboot" repack boot.img green.img >repack.log 2>&1) || {
      error="magiskboot repack failed"
      break
    }

    if [[ ! -s "$workdir/green.img" ]]; then
      error="patched image was not created"
      break
    fi

    mkdir "$workdir/verify" &&
      cp "$workdir/green.img" "$workdir/verify/green.img" || {
      error="cannot prepare cmdline verification"
      break
    }

    (cd "$workdir/verify" && "$magiskboot" unpack -h green.img >/dev/null 2>&1) || {
      error="cannot unpack patched image for verification"
      break
    }

    if [[ ! -f "$workdir/verify/header" ]] ||
      ! grep -Fq "$patched_cmdline" "$workdir/verify/header"; then
      error="cmdline verification failed"
      break
    fi

    cp -rf "$sboot" "$backup_boot" && cp "$workdir/green.img" "$sboot" || {
      error="cannot install patched boot.img"
      break
    }

    break
  done

  rm -rf "$workdir"

  if [[ -n "$error" ]]; then
    log_fail Bootloader boot.img "$error"
    return 1
  fi

  log_done Bootloader "$(basename "$sboot")"
  return 0
}

patch_init_ro_auto() {
  local init_binary="$dir/img_temp/system/system/bin/init"
  local workdir
  local patched_binary
  local log_file="$dir/logs/patch_init_ro.log"
  local status

  if [[ ! -f "$init_binary" ]]; then
    log_warn InitPatch system/bin/init "not found; skip ro.* patch"
    return 0
  fi
  if [[ ! -f "$dir/bin/patch_init_ro.py" ]]; then
    log_fail InitPatch system/bin/init "patch_init_ro.py not found"
    return 1
  fi

  workdir="$(mktemp -d "$dir/.init_patch.XXXXXX")" || {
    log_fail InitPatch system/bin/init "cannot create temporary directory"
    return 1
  }
  patched_binary="$workdir/init"
  mkdir -p "$dir/logs"

  log_start InitPatch "system/bin/init"
  $patch_init_ro "$init_binary" "$patched_binary" >"$log_file" 2>&1
  status=$?

  if ((status != 0)); then
    log_block InitPatch system/bin/init "$log_file" "$status"
    rm -f "$patched_binary"
    rmdir "$workdir" 2>/dev/null || true
    return "$status"
  fi

  if [[ -s "$patched_binary" ]]; then
    if ! mv -f "$patched_binary" "$init_binary"; then
      log_fail InitPatch system/bin/init "cannot install patched binary"
      rm -f "$patched_binary"
      rmdir "$workdir" 2>/dev/null || true
      return 1
    fi
    log_done InitPatch system/bin/init "existing ro.* properties are now updateable"
  else
    log_done InitPatch system/bin/init "already patched"
  fi

  rmdir "$workdir" 2>/dev/null || true
  return 0
}

# Optional per-device overrides use one key=value entry per line in
# fastboot/bootconfig/<device>.conf. Existing keys are replaced and missing
# keys are appended.
patch_vendor_bootconfig_auto() {
  local firmware_dir="$dir/$device/firmware-update"
  local image="$firmware_dir/vendor_boot.img"
  local config="$dir/fastboot/bootconfig/${device}.conf"
  local magiskboot="$dir/bin/magisk/magiskboot"
  local workdir=""
  local unpack_status=0
  local error=""

  if [[ ! -f "$config" ]]; then
    log_warn Bootconfig "$device" "no config at fastboot/bootconfig/${device}.conf; skip"
    return 0
  fi
  if [[ ! -s "$image" ]]; then
    log_fail Bootconfig vendor_boot.img "image not found or empty"
    return 1
  fi
  if [[ ! -x "$magiskboot" ]]; then
    log_fail Bootconfig vendor_boot.img "magiskboot not found or not executable"
    return 1
  fi

  log_start Bootconfig "$(basename "$image")"
  workdir=$(mktemp -d) || {
    log_fail Bootconfig vendor_boot.img "cannot create temporary directory"
    return 1
  }

  while :; do
    cp -- "$image" "$workdir/vendor_boot.img" || {
      error="cannot copy vendor_boot.img"
      break
    }

    (cd "$workdir" && "$magiskboot" unpack -h vendor_boot.img >unpack.log 2>&1)
    unpack_status=$?
    if ((unpack_status != 0 && unpack_status != 3)); then
      error="magiskboot unpack failed (status $unpack_status)"
      break
    fi
    if [[ ! -f "$workdir/bootconfig" ]]; then
      error="vendor_boot has no bootconfig section"
      break
    fi

    if ! awk -v config_file="$config" '
      FILENAME == config_file {
        line = $0
        sub(/^[[:space:]]+/, "", line)
        sub(/[[:space:]]+$/, "", line)
        if (line == "" || line ~ /^#/) next
        key = line
        sub(/[[:space:]]*=.*/, "", key)
        if (key !~ /^[A-Za-z0-9_.-]+$/ || line !~ /^[A-Za-z0-9_.-]+[[:space:]]*=/) {
          printf "invalid bootconfig entry at %s:%d\n", FILENAME, FNR > "/dev/stderr"
          bad = 1
          next
        }
        value = substr(line, index(line, "=") + 1)
        sub(/^[[:space:]]+/, "", value)
        sub(/[[:space:]]+$/, "", value)
        if (value == "") {
          printf "empty bootconfig value at %s:%d\n", FILENAME, FNR > "/dev/stderr"
          bad = 1
          next
        }
        if (!(key in configured)) order[++count] = key
        configured[key] = value
        next
      }
      {
        line = $0
        key = line
        sub(/^[[:space:]]+/, "", key)
        sub(/[[:space:]]*=.*/, "", key)
        if (key in configured) {
          if (!written[key]++) print key "=" configured[key]
          next
        }
        print
      }
      END {
        if (bad) exit 1
        for (i = 1; i <= count; i++) {
          key = order[i]
          if (!written[key]) print key "=" configured[key]
        }
      }
    ' "$config" "$workdir/bootconfig" >"$workdir/bootconfig.new"; then
      error="invalid bootconfig config"
      break
    fi
    mv -- "$workdir/bootconfig.new" "$workdir/bootconfig" || {
      error="cannot write bootconfig"
      break
    }

    (cd "$workdir" && "$magiskboot" repack vendor_boot.img patched_vendor_boot.img >repack.log 2>&1) || {
      error="magiskboot repack failed"
      break
    }
    if [[ ! -s "$workdir/patched_vendor_boot.img" ]]; then
      error="patched vendor_boot.img was not created"
      break
    fi

    mkdir "$workdir/verify" || {
      error="cannot create verification directory"
      break
    }
    cp -- "$workdir/patched_vendor_boot.img" "$workdir/verify/vendor_boot.img" || {
      error="cannot prepare output verification"
      break
    }
    (cd "$workdir/verify" && "$magiskboot" unpack vendor_boot.img >unpack.log 2>&1)
    unpack_status=$?
    if ((unpack_status != 0 && unpack_status != 3)) ||
      ! cmp -s -- "$workdir/bootconfig" "$workdir/verify/bootconfig"; then
      error="bootconfig verification failed"
      break
    fi

    cp -- "$workdir/patched_vendor_boot.img" "$image" || {
      error="cannot install patched vendor_boot.img"
      break
    }
    break
  done

  if [[ -n "$workdir" ]]; then
    rm -rf -- "$workdir"
  fi
  if [[ -n "$error" ]]; then
    log_fail Bootconfig vendor_boot.img "$error"
    return 1
  fi

  log_done Bootconfig "$(basename "$image")"
  return 0
}

# Recovery ADB is expected on every build: patch firmware-update/recovery.img
# in place via patch_recovery_adb() so the shipped recovery always exposes
# adbd (ro.secure=0, ro.adb.secure=0, root adbd on boot), not just when the
# standalone `bhlnk recovery-adb` command is run by hand. It also swaps the
# recovery ramdisk's own otacerts.zip for bin/otacerts.zip so our self-signed
# OTAs install over `adb sideload` (pass --no-ota-cert to keep the OEM key).
#
# GKI 2.0 devices (e.g. PLG110) ship no standalone recovery.img; the recovery
# ramdisk is instead a named "recovery" vendor ramdisk fragment inside
# vendor_boot.img (mkbootimg header v4, VND_RAMDISK type=recovery). boot.img
# on these devices carries the kernel only (RAMDISK_SZ=0), so it is never a
# valid fallback. patch_recovery_adb() unpacks/repacks whichever ramdisk it
# finds (root ramdisk.cpio or vendor_ramdisk/recovery.cpio), so pointing it at
# vendor_boot.img is enough.
patch_recovery_adb_auto() {
  local firmware_dir="$dir/$device/firmware-update"
  local recovery_img="$firmware_dir/recovery.img"
  local vendor_boot_img="$firmware_dir/vendor_boot.img"
  local target_img="$recovery_img"
  local target_name="recovery.img"
  local patched_img

  if [[ ! -f "$recovery_img" ]]; then
    if [[ -f "$vendor_boot_img" ]]; then
      log_warn RecoveryADB recovery.img "not found; falling back to vendor_boot.img recovery ramdisk fragment"
      target_img="$vendor_boot_img"
      target_name="vendor_boot.img"
    else
      log_warn RecoveryADB recovery.img "not found; skip ADB patch"
      return 0
    fi
  fi

  patched_img="$(mktemp "$firmware_dir/.recovery-adb.XXXXXX")" || {
    log_fail RecoveryADB Workspace "cannot create temporary file"
    return 1
  }

  if ! patch_recovery_adb "$target_img" "$patched_img" --force; then
    rm -f -- "$patched_img"
    return 1
  fi

  if ! mv -f -- "$patched_img" "$target_img"; then
    log_fail RecoveryADB Output "cannot install patched $target_name"
    rm -f -- "$patched_img"
    return 1
  fi

  log_done RecoveryADB "$target_name" "recovery now always accepts adb"
}

# Passwordless ADB for the main OS in img_temp, so a build that bootloops can
# still be inspected with logcat: ro.adb.secure=0 / ro.debuggable=1 in every
# build.prop that sets them (later partitions such as vendor override system),
# plus an init trigger next to the adbd service definition so USB debugging is
# forced on at every boot, even when the user's saved value dropped it.
#
# ro.secure and service.adb.root are left alone: user builds carry no
# adbd -> su transition, so a root adbd aborts on setcon() and ADB is lost.
#
# On USB gadget HAL devices (vendor.usb.use_gadget_hal=1) it is usbd, not
# init, that brings the gadget up before system_server, and it only starts
# adbd when persist.sys.usb.config is exactly "adb"; "mtp,adb" gets MTP only.
patch_system_adb_auto() {
  local root="$dir/img_temp"
  local system_prop="$root/system/system/build.prop"
  local rc_path=""
  local usb_config="mtp,adb"
  local prop_file key
  local -a keys=(ro.adb.secure ro.debuggable)
  local -a values=(0 1)
  local i patched=0

  if [[ ! -f "$system_prop" ]]; then
    log_warn SystemADB system/build.prop "not found; skip ADB patch"
    return 0
  fi

  if grep -qsx 'vendor\.usb\.use_gadget_hal=1' "$root/vendor/build.prop"; then
    usb_config="adb"
  fi

  while IFS= read -r -d '' prop_file; do
    for i in "${!keys[@]}"; do
      key="${keys[$i]}"
      grep -q "^${key//./\\.}=" "$prop_file" || continue
      sed -i -E "s|^${key//./\\.}=.*|${key}=${values[$i]}|" "$prop_file" || {
        log_fail SystemADB "${prop_file#$root/}" "cannot update $key"
        return 1
      }
      patched=$((patched + 1))
    done
  done < <(find "$root" -type f -name build.prop -not -path '*/my_product/properties/*' -print0 2>/dev/null)

  for i in "${!keys[@]}"; do
    key="${keys[$i]}"
    grep -q "^${key//./\\.}=" "$system_prop" || printf '%s=%s\n' "$key" "${values[$i]}" >>"$system_prop"
  done
  if grep -q '^persist\.sys\.usb\.config=' "$system_prop"; then
    sed -i -E "s|^persist\.sys\.usb\.config=.*|persist.sys.usb.config=$usb_config|" "$system_prop"
  else
    printf '%s\n' "persist.sys.usb.config=$usb_config" >>"$system_prop"
  fi

  if [[ -f "$root/system/system/etc/init/hw/init.usb.rc" ]] &&
    grep -q '^[[:space:]]*service[[:space:]]\+adbd[[:space:]]' "$root/system/system/etc/init/hw/init.usb.rc"; then
    rc_path="$root/system/system/etc/init/hw/init.usb.rc"
  else
    rc_path="$(grep -rl --include='*.rc' '^[[:space:]]*service[[:space:]]\+adbd[[:space:]]' "$root/system/system/etc/init" 2>/dev/null | sort | head -1)"
  fi
  if [[ -z "$rc_path" ]]; then
    log_fail SystemADB Init "adbd service definition not found"
    return 1
  fi

  # Rewrite the block so a re-run picks up a changed usb config.
  sed -i '/^# VS_SYSTEM_ADB_BEGIN$/,/^# VS_SYSTEM_ADB_END$/d' "$rc_path"
  cat >>"$rc_path" <<EOF
# VS_SYSTEM_ADB_BEGIN
on boot
    setprop persist.sys.usb.config $usb_config
# VS_SYSTEM_ADB_END
EOF

  if ! grep -qx 'ro.adb.secure=0' "$system_prop" ||
    grep -rqx --include=build.prop 'ro.adb.secure=1' "$root" --exclude-dir=properties; then
    log_fail SystemADB Verify "ro.adb.secure is still enforced"
    return 1
  fi

  log_done SystemADB "${rc_path#$root/}" "system now always accepts adb as $usb_config ($patched prop(s) patched)"
}

# Once patch_init_ro_auto() removes the "already set" guard from init,
# vendor/bin/init.class_main.sh:42 ("apq"|"sda"|"qcs" => setprop
# ro.vendor.radio.noril yes) is free to overwrite the my_product/build.prop
# override (ro.radio.noril=no) that keeps the virtual RIL alive on SIM-tray-less
# pads. Neutralize that one line so build.prop's "no" keeps winning even with
# ro.* rewritable.
patch_pad_noril() {
  local build_prop="$dir/img_temp/my_product/build.prop"
  local target="$dir/img_temp/vendor/bin/init.class_main.sh"
  local noril_value

  [[ "$isType" == "pad" ]] || return 0

  if [[ ! -f "$build_prop" ]]; then
    log_warn PatchNoril my_product/build.prop "not found; skip"
    return 0
  fi
  if [[ ! -f "$target" ]]; then
    log_warn PatchNoril vendor/bin/init.class_main.sh "not found; skip"
    return 0
  fi

  noril_value="$(grep -m1 '^ro\.radio\.noril=' "$build_prop" | cut -d= -f2)"
  if [[ "$noril_value" != "no" ]]; then
    log_event info PatchNoril "" Skip "ro.radio.noril=${noril_value:-unset} in build.prop; nothing to fix"
    return 0
  fi

  log_start PatchNoril "vendor/bin/init.class_main.sh"

  if grep -q '^[[:space:]]*#[[:space:]]*setprop ro\.vendor\.radio\.noril yes' "$target"; then
    log_done PatchNoril "vendor/bin/init.class_main.sh" "already patched"
    return 0
  fi

  if ! grep -q '^[[:space:]]*setprop ro\.vendor\.radio\.noril yes[[:space:]]*$' "$target"; then
    log_warn PatchNoril "vendor/bin/init.class_main.sh" "expected setprop line not found; skip"
    return 0
  fi

  sudo sed -i -E \
    's/^([[:space:]]*)setprop ro\.vendor\.radio\.noril yes[[:space:]]*$/\1# setprop ro.vendor.radio.noril yes # disabled by VS_Build_Tool: pad has no SIM tray, keep virtual RIL/' \
    "$target"

  if grep -q '^[[:space:]]*#[[:space:]]*setprop ro\.vendor\.radio\.noril yes' "$target"; then
    log_done PatchNoril "vendor/bin/init.class_main.sh" "ro.vendor.radio.noril yes disabled; build.prop no now wins"
  else
    log_fail PatchNoril "vendor/bin/init.class_main.sh" "patch verification failed"
    return 1
  fi
}

# True when $1 is an OTA zip holding a stored payload.bin.
is_payload_zip() {
  $payload_extract -l "$1" >/dev/null 2>&1
}

# Write the partition images of the payload in OTA zip $1 into the current
# directory, reading payload.bin in place (it is never copied out of the zip).
# $2 is the log tag.
extract_payload() {
  local src="$1" tag="$2" rc=0 log_file="$dir/logs/extract_payload.log"
  mkdir -p "$dir/logs"
  # with_extract_lock already holds the farm's extract lock.
  VS_EXTRACT_LOCK_HELD=1 $payload_extract -o . "$src" >"$log_file" 2>&1 || rc=$?
  if ((rc != 0)); then
    log_fail "$tag" payload.bin "payload extract failed (exit $rc, see logs/extract_payload.log)"
    p_extract_fail_report "$log_file"
    return 1
  fi
  log_event info "$tag" payload.bin "" "$(tail -n 1 "$log_file")"
}

# Run "$@" holding the extract lock shared by the farm slots: $VS_EXTRACT_LOCK,
# or extract.lock beside $VS_OTA_IO_LOCK (a pool from an older farm.sh). The
# payload images and the erofs trees are the disk-heavy part of a build, so
# one slot extracts at a time while the others keep downloading and patching.
# Without either variable (CI, a single build) there is no waiting.
with_extract_lock() {
  local path="${VS_EXTRACT_LOCK:-}" fd rc=0
  if [[ -z "$path" && -n "${VS_OTA_IO_LOCK:-}" ]]; then
    path="$(dirname -- "$VS_OTA_IO_LOCK")/extract.lock"
  fi
  if [[ -z "$path" ]]; then
    "$@"
    return
  fi
  exec {fd}>>"$path"
  if ! flock -n "$fd"; then
    log_event info Extract Lock "" "another build is extracting; waiting for its turn"
    flock "$fd"
    log_event info Extract Lock "" "extraction turn"
  fi
  "$@" || rc=$?
  exec {fd}>&-
  return "$rc"
}

extract() {
  with_extract_lock extract_unlocked
}

extract_stock_images() {
  with_extract_lock extract_stock_images_unlocked
}

extract_unlocked() {

  log_start Extract "$zipname"
  mkdir -p "$dir/zip_temp" "$dir/img_temp" "$dir/$device/firmware-update"
  rm -rf "$dir/zip_temp"/*

  mv "$zipname" "$dir/zip_temp/" || {
    log_fail Extract "$zipname" "move to zip_temp failed"
    return 1
  }
  cd "$dir/zip_temp" || {
    log_fail Extract "$zipname" "cannot enter zip_temp"
    return 1
  }

  if [[ $zipname == *"tgz" ]]; then
    if ! tar -xzf "$zipname" --wildcards --no-anchored 'images/*.img' -C "$(pwd)" --transform='s|.*/||'; then
      log_fail Extract "$zipname" "archive extract failed"
      cd "$dir" || return 1
      return 1
    fi
  elif is_payload_zip "$zipname"; then
    # A/B OTA: the images come straight out of the payload inside the zip;
    # nothing else in the package is used.
    capture_payload_super_size "$zipname"
    capture_ota_meta "$zipname"
    extract_payload "$zipname" Extract || {
      cd "$dir" || return 1
      return 1
    }
  else
    if ! jar xf "$zipname"; then
      log_fail Extract "$zipname" "archive extract failed"
      cd "$dir" || return 1
      return 1
    fi
    # payload.bin compressed inside the zip: it had to be unpacked first.
    if [[ -f payload.bin ]]; then
      capture_payload_super_size payload.bin
      capture_ota_meta payload.bin
      extract_payload payload.bin Extract || {
        cd "$dir" || return 1
        return 1
      }
      rm -f payload.bin
    fi
  fi
  rm -f "$zipname"

  if [[ -f super.img ]]; then
    # Release each intermediate as soon as the next stage has consumed it so
    # sparse super, raw super and unpacked partitions never coexist on disk.
    if ! simg2img super.img raw.img; then
      log_fail Extract super.img "super unpack failed"
      cd "$dir" || return 1
      return 1
    fi
    rm -f super.img
    if ! "$sunpack" raw.img; then
      log_fail Extract super.img "super unpack failed"
      cd "$dir" || return 1
      return 1
    fi
    rm -f raw.img
    rm -f *_b.img

    for file in $(ls *_a.img); do
      mv "$file" "${file%_a.img}.img"
    done
  fi

  if ! mv "$dir"/zip_temp/*.img "$dir/$device/firmware-update/" 2>/dev/null; then
    log_fail Extract "$zipname" "no img files found"
    cd "$dir" || return 1
    return 1
  fi

  local -a erofs_imgs=()
  for i in $(get_extract_parts); do
    if [[ -f "$dir/$device/firmware-update/${i}.img" ]]; then
      w_img="$dir/$device/firmware-update/${i}.img"
      type_img=$(p_detect "$w_img")
      echo "$i=$type_img" >>"$dir/info"
      if [[ $type_img == "erofs" ]]; then
        p_erofs e "$w_img" || {
          cd "$dir" || return 1
          return 1
        }
        # The extracted tree is now the source of truth. Keeping the original
        # partition image doubles peak disk usage during patching, so it is
        # removed after extraction regardless of build type.
        rm -f -- "$w_img" || {
          log_fail Extract "$i.img" "cannot remove extracted source image"
          cd "$dir" || return 1
          return 1
        }
        log_event info Extract "$i.img" Clean "source image removed after extraction"
      else
        log_warn Extract "$i.img" "cannot determine image type"
      fi
    fi
  done

  sudo rm -rf "$dir/zip_temp"
  cd "$dir" || return 1
  log_done Extract "$zipname"
}

extract_stock_images_unlocked() {
  log_start StockExtract "$zipname"

  rm -rf "$dir/zip_temp"
  mkdir -p "$dir/zip_temp"

  mv "$zipname" "$dir/zip_temp/" || {
    log_fail StockExtract "$zipname" "move to zip_temp failed"
    return 1
  }
  cd "$dir/zip_temp" || {
    log_fail StockExtract "$zipname" "cannot enter zip_temp"
    return 1
  }

  if [[ "$zipname" == *"tgz" ]]; then
    if ! tar -xzf "$zipname" --wildcards --no-anchored 'images/*.img' -C "$(pwd)" --transform='s|.*/||'; then
      log_fail StockExtract "$zipname" "archive extract failed"
      cd "$dir" || return 1
      return 1
    fi
  elif is_payload_zip "$zipname"; then
    capture_payload_super_size "$zipname"
    capture_ota_meta "$zipname"
    extract_payload "$zipname" StockExtract || {
      cd "$dir" || return 1
      return 1
    }
  else
    if ! jar xf "$zipname"; then
      log_fail StockExtract "$zipname" "archive extract failed"
      cd "$dir" || return 1
      return 1
    fi
    # payload.bin compressed inside the zip: it had to be unpacked first.
    if [[ -f payload.bin ]]; then
      capture_payload_super_size payload.bin
      capture_ota_meta payload.bin
      extract_payload payload.bin StockExtract || {
        cd "$dir" || return 1
        return 1
      }
      rm -f payload.bin
    fi
  fi
  rm -f "$zipname"

  if [[ -f super.img ]]; then
    # Release each intermediate as soon as the next stage has consumed it so
    # sparse super, raw super and unpacked partitions never coexist on disk.
    if ! simg2img super.img raw.img; then
      log_fail StockExtract super.img "super unpack failed"
      cd "$dir" || return 1
      return 1
    fi
    rm -f super.img
    if ! "$sunpack" raw.img; then
      log_fail StockExtract super.img "super unpack failed"
      cd "$dir" || return 1
      return 1
    fi
    rm -f raw.img
    rm -f *_b.img

    for file in *_a.img; do
      [[ -f "$file" ]] && mv "$file" "${file%_a.img}.img"
    done
  fi

  mv "$dir"/zip_temp/*.img "$dir/$device/firmware-update/" 2>/dev/null || {
    log_fail StockExtract "$zipname" "no img files found"
    cd "$dir" || return 1
    return 1
  }

  sudo rm -rf "$dir/zip_temp"
  cd "$dir" || return 1
  log_done StockExtract "$zipname"
}

vbmetas() {
  local workdir="$dir/bin/avb"
  local img="$dir/$device/firmware-update/vendor_boot.img"
  local mics_dir="$dir/$device/mics"
  local ramdisk one list_avb file_avb i

  log_start AVB vendor_boot.img

  if [[ ! -f "$img" ]]; then
    log_fail AVB vendor_boot.img "missing from firmware-update"
    return 1
  fi

  # Preserve the stock image before unpacking or modifying it. If this patch
  # step is retried after firmware-update was already patched, keep the
  # existing stock backup instead of overwriting it with the patched image.
  if [[ -s "$mics_dir/vendor_boot.img" ]] && ! cmp -s -- "$img" "$mics_dir/vendor_boot.img"; then
    log_event info AVB "vendor_boot Stock" "" "reuse $device/mics/vendor_boot.img"
  else
    if ! mkdir -p "$mics_dir" || ! cp -p -- "$img" "$mics_dir/vendor_boot.img"; then
      log_fail AVB vendor_boot.img "cannot save stock image to $device/mics"
      return 1
    fi
    if ! cmp -s -- "$img" "$mics_dir/vendor_boot.img"; then
      log_fail AVB vendor_boot.img "stock backup verification failed"
      return 1
    fi
    log_event info AVB "vendor_boot Stock" "" "$device/mics/vendor_boot.img"
  fi

  if ! cd "$workdir"; then
    log_fail AVB vendor_boot.img "cannot enter AVB work directory"
    return 1
  fi

  # cleanup cũ
  ./magiskboot cleanup >/dev/null 2>&1

  # copy image vào workdir
  if ! cp -- "$img" ./vendor_boot.img; then
    log_fail AVB vendor_boot.img "cannot prepare image for patching"
    return 1
  fi

  # unpack vendor_boot
  if ! ./magiskboot unpack vendor_boot.img >/dev/null 2>&1; then
    log_fail AVB vendor_boot.img "unpack failed"
    return 1
  fi

  # xác định file ramdisk
  if [ -f ramdisk.cpio ]; then
    ramdisk="ramdisk.cpio"
  elif [ -f vendor_ramdisk.cpio ]; then
    ramdisk="vendor_ramdisk.cpio"
  elif [ -f vendor_ramdisk/ramdisk.cpio ]; then
    ramdisk="vendor_ramdisk/ramdisk.cpio"
  else
    log_fail AVB vendor_boot.img "ramdisk.cpio not found"
    return 1
  fi

  # extract ramdisk
  if ! ./magiskboot cpio "$ramdisk" "extract" >/dev/null 2>&1; then
    log_fail AVB vendor_boot.img "ramdisk extract failed"
    return 1
  fi

  # load keyword
  if [[ ! -f "$workdir/one.txt" ]]; then
    log_fail AVB vendor_boot.img "one.txt not found"
    return 1
  fi
  one=$(<"$workdir/one.txt")

  # tìm fstab
  list_avb=$(find . -type f -name "fstab.*")

  local patched_count=0
  for file_avb in $list_avb; do
    patched_count=$((patched_count + 1))

    for i in $one; do
      sed -i -e "s@$i@@g" "$file_avb"
    done

    # add lại vào ramdisk
    if ! ./magiskboot cpio "$ramdisk" "add 0644 ${file_avb#./} $file_avb" >/dev/null 2>&1; then
      log_fail AVB vendor_boot.img "cannot update ${file_avb#./} in ramdisk"
      return 1
    fi
  done

  if ((patched_count == 0)); then
    log_fail AVB vendor_boot.img "no fstab file was patched"
    return 1
  fi

  # repack
  if ! ./magiskboot repack vendor_boot.img >/dev/null 2>&1; then
    log_fail AVB vendor_boot.img "repack failed"
    return 1
  fi

  # Install only the patched image in firmware-update. The stock image saved
  # above remains in mics.
  if ! mv -f new-boot.img "$img" || [[ ! -s "$img" ]]; then
    log_fail AVB vendor_boot.img "cannot install patched image in firmware-update"
    return 1
  fi
  if cmp -s -- "$img" "$mics_dir/vendor_boot.img"; then
    log_fail AVB vendor_boot.img "patched image is unchanged from stock backup"
    return 1
  fi
  log_event info AVB "vendor_boot Patched" "" "$device/firmware-update/vendor_boot.img"

  # 🔥 cleanup: chỉ giữ magiskboot + one.txt
  find . ! -name "magiskboot" ! -name "one.txt" ! -name "." -exec rm -rf {} + 2>/dev/null

  log_done AVB vendor_boot.img "patched $patched_count fstab file(s)"
}

verify_boot_image_layout() {
  local firmware_dir="$dir/$device/firmware-update"
  local mics_dir="$dir/$device/mics"

  if [[ ! -s "$firmware_dir/vendor_boot.img" ]]; then
    log_fail Pack Images "patched vendor_boot.img missing from firmware-update"
    return 1
  fi
  if [[ ! -s "$mics_dir/vendor_boot.img" ]]; then
    log_fail Pack Images "stock vendor_boot.img missing from mics"
    return 1
  fi
  if cmp -s -- "$firmware_dir/vendor_boot.img" "$mics_dir/vendor_boot.img"; then
    log_fail Pack Images "patched vendor_boot.img matches the stock mics copy"
    return 1
  fi
  if [[ ! -s "$mics_dir/super.img" ]]; then
    log_fail Pack Images "empty layout super.img missing from mics"
    return 1
  fi
  if [[ "$(od -An -tx1 -N4 "$mics_dir/super.img" 2>/dev/null | tr -d '[:space:]')" != "3aff26ed" ]]; then
    log_fail Pack Images "mics/super.img is not an Android sparse image"
    return 1
  fi

  log_done Pack Images "LK, vendor_boot, and empty super layout verified"
}

# Common list first, then the brand list, then the brand list for this
# Android major (e.g. module/debloat_ColorOS_A16.txt) when $2 is set.
debloat_list_files() {
  local prefix="$1"
  local android_major="${2:-0}"
  local brand_name="${brand:-HyperOS}"
  local brand_lower="${brand_name,,}"
  local file
  local -A seen=()
  local candidates=(
    "$dir/module/${prefix}.txt"
    "$dir/module/${prefix}_${brand_name}.txt"
    "$dir/module/${prefix}_${brand_lower}.txt"
  )
  if ((android_major > 0)); then
    candidates+=(
      "$dir/module/${prefix}_${brand_name}_A${android_major}.txt"
      "$dir/module/${prefix}_${brand_lower}_A${android_major}.txt"
    )
  fi

  for file in "${candidates[@]}"; do
    if [[ -f "$file" && -z "${seen[$file]}" ]]; then
      seen[$file]=1
      printf '%s\n' "$file"
    fi
  done
}

debloat() {

  log_start Bloatware Remove

  local android_version="${android:-$os}"
  local android_major=0

  if [[ "$android_version" =~ ^([0-9]+) ]]; then
    android_major=$((10#${BASH_REMATCH[1]}))
  fi

  if [[ -d $dir/jar_temp ]]; then
    rm -rf "$dir"/jar_temp/*
  else
    mkdir -p "$dir/jar_temp"
  fi

  cd $dir/img_temp

  for debloat_file in $(debloat_list_files debloat "$android_major"); do
    log_event info Bloatware "$(basename "$debloat_file")" List "$brand"
    while IFS= read -r i || [[ -n "$i" ]]; do
      [[ -z "$i" || "$i" == \#* ]] && continue

      target="$(sudo find . -name "$i" -print -quit)"
      if [[ -n "$target" ]]; then
        sudo rm -rf "$target"
      fi
    done <"$debloat_file"
  done

  for debloat_file in $(debloat_list_files pkg_debloat "$android_major"); do
    log_event info Bloatware "$(basename "$debloat_file")" PkgList "$brand"
    while IFS= read -r i || [[ -n "$i" ]]; do
      [[ -z "$i" || "$i" == \#* ]] && continue
      # Android 16 trở xuống vẫn giữ PersonalAssistant (MiuiHome cũ cần minus screen)
      [[ "$i" == "com.miui.personalassistant" ]] && ((android_major < 17)) && continue
      remove_pkg "$i"
    done <"$debloat_file"
  done

  sframe=$dir/img_temp/system/system/framework
  if [[ -d $sframe ]]; then
    sudo find $sframe -type f -name "boot.*" -exec rm -f {} \;
    sudo find $sframe -type f -name "*fsv_meta" -exec rm -f {} \;
    sudo find $sframe -type f -name "*.vdex" -exec rm -f {} \;
    sudo rm -rf $dir/img_temp/system/system/framework/arm
    sudo rm -rf $dir/img_temp/system/system/framework/arm64
    sudo rm -rf $dir/img_temp/system/system/framework/oat
  fi

  sframe=$dir/img_temp/system_ext/framework
  if [[ -d $sframe ]]; then
    sudo find $sframe -type f -name "boot.*" -exec rm -f {} \;
    sudo find $sframe -type f -name "*fsv_meta" -exec rm -f {} \;
    sudo find $sframe -type f -name "*.vdex" -exec rm -f {} \;
    sudo rm -rf $dir/img_temp/system_ext/framework/arm
    sudo rm -rf $dir/img_temp/system_ext/framework/arm64
    sudo rm -rf $dir/img_temp/system_ext/framework/oat
  fi

  log_done Bloatware Remove

}

rwprivapp() {
  $dir/bin/patch_privapp_media_rw.sh $dir/img_temp
}

patch_vbmeta_selinux_auto() {
  local script="$dir/bin/patch_vbmeta_selinux.sh"
  local log_file="$dir/logs/patch_vbmeta_selinux.log"
  local status

  if [[ ! -f "$script" ]]; then
    log_fail VbmetaSELinux system "patch_vbmeta_selinux.sh not found"
    return 1
  fi

  mkdir -p "$dir/logs"
  log_start VbmetaSELinux "ro.boot.vbmeta.device_state"
  "$script" "$dir/img_temp" >"$log_file" 2>&1
  status=$?
  if ((status != 0)); then
    log_block VbmetaSELinux system "$log_file" "$status"
    return "$status"
  fi
  log_done VbmetaSELinux "ro.boot.vbmeta.device_state" "untrusted app read blocked"
}

# Cài vs_prop + init.rc + SELinux để set lại verified-boot props với serial
# init-style (counter = 0). Chỉ chạy cho ROM có system EROFS/ext đã giải nén.
patch_bootstate_prop_auto() {
  local script="$dir/bin/patch_bootstate_prop.sh"
  local binary="$dir/bin/vs_prop"
  local log_file="$dir/logs/patch_bootstate_prop.log"

  if [[ ! -d "$dir/img_temp/system/system/bin" ]]; then
    log_warn BootStateProp system "system/bin not found; skip"
    return 0
  fi
  if [[ ! -f "$script" ]]; then
    log_fail BootStateProp system "patch_bootstate_prop.sh not found"
    return 1
  fi
  if [[ ! -f "$binary" ]]; then
    log_fail BootStateProp system "bin/vs_prop not found"
    return 1
  fi

  mkdir -p "$dir/logs"
  log_start BootStateProp "verified-boot props"
  local status
  "$script" "$dir/img_temp" "$binary" >"$log_file" 2>&1
  status=$?
  if ((status != 0)); then
    log_block BootStateProp system "$log_file" "$status"
    return "$status"
  fi
  log_done BootStateProp "verified-boot props" "vs_prop --init set 3 prop(s)"
}

patchhosts() {
  $dir/bin/patch_hosts.sh $dir/img_temp
}

download_latest_vstools_apk() {
  local apk_name="VSTools-release.apk"
  local apk_url="${VSTOOLS_APK_URL:-https://vsteam.vn/api/manage/download?target=apks&filename=$apk_name}"
  local auto_dir="$dir/module/update/madd_vst/product/priv-app/VSTools-release"
  local tmp_apk
  local badging
  local package_name
  local timeout="${VSTOOLS_DOWNLOAD_TIMEOUT:-180}"
  local -a curl_args=(
    -L --fail --retry 3 --retry-delay 2
    --connect-timeout 15 --max-time "$timeout" --retry-max-time "$timeout"
    --speed-limit 1024 --speed-time 30
    --show-error
  )

  log_start Download MushromTools "$apk_name"

  if ! mkdir -p "$auto_dir"; then
    log_fail Download MushromTools "cannot create destination directory"
    return 1
  fi

  # Clean up the fixed-name partial file left by older versions of this function.
  rm -f "$dir/.${apk_name}.download"

  tmp_apk=$(mktemp "$dir/.${apk_name}.download.XXXXXX") || {
    log_fail Download MushromTools "cannot create temporary file"
    return 1
  }

  if [[ -t 2 ]]; then
    curl_args+=(--progress-bar)
  else
    curl_args+=(--silent)
  fi

  if command -v curl >/dev/null 2>&1; then
    if ! curl "${curl_args[@]}" -o "$tmp_apk" "$apk_url"; then
      log_fail Download MushromTools "download failed or timed out"
      rm -f "$tmp_apk"
      return 1
    fi
  elif command -v wget >/dev/null 2>&1; then
    if ! wget --tries=3 --timeout=15 --read-timeout=30 \
      ${CI:+--quiet} -O "$tmp_apk" "$apk_url"; then
      log_fail Download MushromTools "download failed or timed out"
      rm -f "$tmp_apk"
      return 1
    fi
  else
    log_fail Download MushromTools "curl/wget not found"
    rm -f "$tmp_apk"
    return 1
  fi

  if [[ ! -s "$tmp_apk" ]]; then
    log_fail Download MushromTools "download failed"
    rm -f "$tmp_apk"
    return 1
  fi

  if ! badging=$("$aapt2_b" dump badging "$tmp_apk" 2>/dev/null); then
    log_fail Download MushromTools "invalid apk"
    rm -f "$tmp_apk"
    return 1
  fi

  package_name=$(sed -n "s/^package: name='\([^']*\)'.*/\1/p" <<<"$badging" | head -1)
  if [[ "$package_name" != "com.bhlnk.vsteam" ]]; then
    log_fail Download MushromTools "unexpected package: ${package_name:-unknown}"
    rm -f "$tmp_apk"
    return 1
  fi

  if ! chmod 0644 "$tmp_apk"; then
    log_fail Download MushromTools "cannot set apk permissions"
    rm -f "$tmp_apk"
    return 1
  fi

  if ! mv -f "$tmp_apk" "$auto_dir/$apk_name"; then
    log_fail Download MushromTools "cannot install downloaded apk"
    rm -f "$tmp_apk"
    return 1
  fi

  log_done Download MushromTools "$auto_dir/$apk_name"
}

# system_server aborts at boot (bootloop) when a priv-app requests a
# signature|privileged permission missing from its privapp-permissions
# allowlist, so every permission the bundled MushromTools APK requests is added to
# its allowlist before madd_vst is copied into the ROM. Runs even when the
# download failed: the APK already in the module may be newer than the XML.
sync_vstools_privapp_permissions() {
  local package_name="com.bhlnk.vsteam"
  local apk="$dir/module/update/madd_vst/product/priv-app/VSTools-release/VSTools-release.apk"
  local xml="$dir/module/update/madd_vst/product/etc/permissions/$package_name.xml"
  local block_start="<privapp-permissions package=\"$package_name\">"
  local badging requested allowed missing tmp_xml count

  if [[ ! -f "$apk" ]]; then
    log_fail PrivappPermissions MushromTools "apk not found"
    return 1
  fi
  if [[ ! -f "$xml" ]] || ! grep -qF "$block_start" "$xml"; then
    log_fail PrivappPermissions MushromTools "allowlist for $package_name not found"
    return 1
  fi

  if ! badging=$("$aapt2_b" dump badging "$apk" 2>/dev/null); then
    log_fail PrivappPermissions MushromTools "cannot read apk permissions"
    return 1
  fi
  requested=$(sed -n "s/^uses-permission[^:]*: name='\([^']*\)'.*/\1/p" <<<"$badging" | sort -u)
  if [[ -z "$requested" ]]; then
    log_fail PrivappPermissions MushromTools "apk declares no permissions"
    return 1
  fi

  allowed=$(awk -v start="$block_start" '
    index($0, start) { in_block = 1 }
    in_block && match($0, /<permission name="[^"]*"/) { print substr($0, RSTART + 18, RLENGTH - 19) }
    in_block && /<\/privapp-permissions>/ { exit }
  ' "$xml" | sort -u)

  missing=$(comm -23 <(printf '%s\n' "$requested") <(printf '%s\n' "$allowed"))
  if [[ -z "$missing" ]]; then
    log_done PrivappPermissions MushromTools "allowlist already covers the apk"
    return 0
  fi

  tmp_xml=$(mktemp "$xml.XXXXXX") || {
    log_fail PrivappPermissions MushromTools "cannot create temporary file"
    return 1
  }
  if ! awk -v start="$block_start" -v missing="$missing" '
    index($0, start) { in_block = 1 }
    in_block && /<\/privapp-permissions>/ {
      n = split(missing, perms, "\n")
      for (i = 1; i <= n; i++) printf "    <permission name=\"%s\"/>\n", perms[i]
      in_block = 0
    }
    { print }
  ' "$xml" >"$tmp_xml" || ! chmod --reference="$xml" "$tmp_xml" || ! mv -f "$tmp_xml" "$xml"; then
    log_fail PrivappPermissions MushromTools "cannot update allowlist"
    rm -f "$tmp_xml"
    return 1
  fi

  count=$(wc -l <<<"$missing")
  log_done PrivappPermissions MushromTools "added $count permission(s): $(tr '\n' ' ' <<<"$missing")"
}

resolve_downloadcheck_url() {
  local source_url="$1"
  local tmp_info="$2"

  [[ -n "$source_url" && -n "$tmp_info" ]] || return 1

  python3 - "$source_url" "$tmp_info" <<'PY'
import os
import shlex
import sys
import urllib.error
import urllib.parse
import urllib.request

source_url = sys.argv[1]
out_file = sys.argv[2]

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

request = urllib.request.Request(
    source_url,
    headers={
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 13; SM-G998B) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/112.0.0.0 Mobile Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
        "userId": "oplus-ota|00000001",
        "Range": "bytes=0-",
    },
)
opener = urllib.request.build_opener(NoRedirect)
resolved_url = source_url

try:
    response = opener.open(request, timeout=30)
except urllib.error.HTTPError as exc:
    if exc.code not in (301, 302, 303, 307, 308):
        raise
    location = (exc.headers.get("Location") or "").strip()
    if not location:
        raise RuntimeError("downloadCheck response is missing the Location header") from exc
    resolved_url = urllib.parse.urljoin(source_url, location)
else:
    try:
        location = (response.headers.get("Location") or "").strip()
        status = getattr(response, "status", response.getcode())
        final_url = (response.geturl() or "").strip()
    finally:
        response.close()
    if status == 200 and final_url and final_url != source_url:
        resolved_url = final_url
    elif status == 200:
        follow_request = urllib.request.Request(
            source_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Linux; Android 13; SM-G998B) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/112.0.0.0 Mobile Safari/537.36"
                ),
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "identity",
                "Connection": "keep-alive",
                "userId": "oplus-ota|00000001",
                "Range": "bytes=0-",
            },
        )
        with urllib.request.urlopen(follow_request, timeout=30) as follow_response:
            followed_url = (follow_response.geturl() or "").strip()
        if followed_url and followed_url != source_url:
            resolved_url = followed_url
        else:
            raise RuntimeError(f"downloadCheck returned HTTP {status}")
    elif status not in (301, 302, 303, 307, 308):
        raise RuntimeError(f"downloadCheck returned HTTP {status}")
    elif not location:
        raise RuntimeError("downloadCheck response is missing the Location header")
    else:
        resolved_url = urllib.parse.urljoin(source_url, location)

parts = urllib.parse.urlsplit(resolved_url)
filename = urllib.parse.unquote(os.path.basename(parts.path))

with open(out_file, "w", encoding="utf-8") as fh:
    fh.write(f"resolved_url={shlex.quote(resolved_url)}\n")
    fh.write(f"resolved_name={shlex.quote(filename)}\n")
PY
}

download_file() {
  local url="$1"
  local output="$2"
  local expected_output
  local label
  local log_file
  local status=0

  if [[ -z "$url" ]]; then
    log_fail Download URL "missing"
    return 1
  fi

  expected_output="$output"
  if [[ -z "$expected_output" ]]; then
    expected_output="${url%%\?*}"
    expected_output="${expected_output%%#*}"
    expected_output="$(basename "$expected_output")"
  fi
  downloaded_file="$expected_output"

  # resolve_rom_source only asks which archive the server offers.
  if [[ "${rom_resolve_only:-false}" == "true" ]]; then
    rom_source_id="$expected_output"
    rom_source_url="$url"
    rom_source_output="$output"
    return 0
  fi

  if [[ -n "$output" && -s "$output" ]]; then
    log_event warn Download "$output" Skip "already downloaded"
    return 0
  fi

  # Named after the archive, not the URL: a signed link's query string is a
  # secret and makes the log file name longer than the filesystem allows.
  label="$(basename "${output:-$expected_output}")"
  log_file="$dir/logs/download_${label//[^A-Za-z0-9._-]/_}.log"
  mkdir -p "$dir/logs"
  log_start Download "$label"

  # The OPPO/ColorOS OTA CDN (allawnfs.com) throttles many simultaneous TLS
  # handshakes from foreign (Azure/GitHub) IPs, surfacing as "SSL/TLS handshake
  # failure: Error in the pull function" with 0 bytes transferred. Fewer parallel
  # connections, aria2's own per-attempt retries, and an outer retry loop (with
  # resume) ride over these transient/region-throttled failures.
  local attempt status_last=1
  local max_attempts="${DOWNLOAD_MAX_ATTEMPTS:-4}"
  status=1
  for attempt in $(seq 1 "$max_attempts"); do
    if ((attempt > 1)); then
      log_event warn Download "$label" Retry "attempt ${attempt}/${max_attempts} (previous exit=${status_last})"
      sleep $(((attempt - 1) * 8))
    fi

    if command -v aria2c >/dev/null 2>&1; then
      if [[ -n "$output" ]]; then
        aria2c -x8 -s8 -j1 -k1M --file-allocation=none --continue=true \
          --max-tries=5 --retry-wait=5 --connect-timeout=30 --timeout=60 \
          --lowest-speed-limit=30K --max-concurrent-downloads=1 \
          --summary-interval=1 --console-log-level=warn \
          --show-console-readout=true --check-certificate=false \
          -o "$output" "$url" 2>&1 | tee "$log_file"
        status=${PIPESTATUS[0]}
      else
        aria2c -x8 -s8 -j1 -k1M --file-allocation=none --continue=true \
          --max-tries=5 --retry-wait=5 --connect-timeout=30 --timeout=60 \
          --lowest-speed-limit=30K --max-concurrent-downloads=1 \
          --summary-interval=1 --console-log-level=warn \
          --show-console-readout=true --check-certificate=false \
          "$url" 2>&1 | tee "$log_file"
        status=${PIPESTATUS[0]}
      fi
    elif command -v curl >/dev/null 2>&1; then
      if [[ -n "$output" ]]; then
        curl -L --fail --retry 5 --retry-all-errors --retry-delay 5 \
          --connect-timeout 30 -C - \
          --progress-bar --show-error -o "$output" "$url" 2>&1 | tee "$log_file"
        status=${PIPESTATUS[0]}
      else
        curl -L --fail --retry 5 --retry-all-errors --retry-delay 5 \
          --connect-timeout 30 \
          --progress-bar --show-error -O "$url" 2>&1 | tee "$log_file"
        status=${PIPESTATUS[0]}
      fi
    elif command -v wget >/dev/null 2>&1; then
      if [[ -n "$output" ]]; then
        wget --no-verbose --no-check-certificate --tries=5 --waitretry=5 --continue \
          --progress=bar:force:noscroll -O "$output" "$url" 2>&1 | tee "$log_file"
        status=${PIPESTATUS[0]}
      else
        wget --no-verbose --no-check-certificate --tries=5 --waitretry=5 --continue \
          --progress=bar:force:noscroll "$url" 2>&1 | tee "$log_file"
        status=${PIPESTATUS[0]}
      fi
    else
      log_fail Download Tool "aria2c/curl/wget not found"
      return 1
    fi

    status_last="$status"
    ((status == 0)) && break
  done

  if ((status != 0)); then
    log_block Download "$label" "$log_file" "$status"
    return "$status"
  fi

  log_done Download "$label"
}

latest_rom_archive() {
  local candidate
  local latest=""

  for candidate in "$dir"/*.zip "$dir"/*.tgz; do
    [[ -f "$candidate" ]] || continue
    if [[ -z "$latest" || "$candidate" -nt "$latest" ]]; then
      latest="$candidate"
    fi
  done

  printf "%s" "$latest"
}

cmd_download_only() {
  local input="$1"
  local requested_region="${2:-}"
  local archive renamed

  if [[ -z "$input" || $# -gt 2 ]]; then
    echo "Usage: download-only <codename|url> [region]"
    return 1
  fi

  downloaded_file=""
  download "$input" "$requested_region" || return 1

  archive="$downloaded_file"
  if [[ -n "$archive" && "$archive" != /* ]]; then
    archive="$dir/$archive"
  fi
  if [[ -z "$archive" || ! -f "$archive" ]]; then
    archive="$(latest_rom_archive)"
  fi
  if [[ -z "$archive" || ! -f "$archive" ]]; then
    log_fail DownloadOnly Archive "download completed but ROM archive was not found"
    return 1
  fi

  renamed="$(format_name "$archive")" || return 1
  log_done DownloadOnly "$(basename "$renamed")" "downloaded and renamed; extraction skipped"
}

coloros_query_region() {
  local code="$1"
  local requested_region="${2:-}"
  local configured_region

  if [[ -n "$requested_region" ]]; then
    printf "%s" "${requested_region,,}"
  elif [[ -n "${COLOROS_REGION:-}" ]]; then
    printf "%s" "${COLOROS_REGION,,}"
  elif [[ -f "$dir/fastboot/coloros_region.list" ]]; then
    configured_region="$(
      awk -v code="$code" '
        $1 !~ /^#/ && toupper($1) == toupper(code) {
          print tolower($2)
          exit
        }
      ' "$dir/fastboot/coloros_region.list"
    )"
    if [[ -n "$configured_region" ]]; then
      printf "%s" "$configured_region"
    elif [[ "${code^^}" =~ ^CPH ]]; then
      printf "in"
    else
      printf "cn"
    fi
  elif [[ "${code^^}" =~ ^CPH ]]; then
    printf "in"
  else
    printf "cn"
  fi
}

is_coloros_region() {
  case "${1,,}" in
  cn | cn_cmcc | eu | in | sg | gl | id | ru | th | tw | my | vn | sa | mea | ph | roe | la | tr | br | jp | hk | au | lk | eea | kz | mx | eg | 8d | latam | ex | apc | oca)
    return 0
    ;;
  *)
    return 1
    ;;
  esac
}

coloros_query_code() {
  local code="$1"

  if [[ "$code" =~ ^CPH[[:digit:]]{4}IN$ ]]; then
    printf "%s" "${code%IN}"
  else
    printf "%s" "$code"
  fi
}

download_coloros() {
  local codename="$1"
  local requested_region="${2:-}"
  local prefix query_code query_region tmp_info resolver_log

  prefix="$(printf "%s" "$codename" | tr '[:lower:]' '[:upper:]')"
  log_event info Download ColorOS "" "codename detected: $prefix"

  requested_region="${requested_region,,}"
  if [[ -n "$requested_region" ]] && ! is_coloros_region "$requested_region"; then
    log_fail Download Region "unsupported region: $requested_region"
    return 1
  fi

  query_code="$(coloros_query_code "$prefix")"
  query_region="$(coloros_query_region "$prefix" "$requested_region")"
  tmp_info="$dir/.download_coloros_info"
  resolver_log="$dir/logs/download_coloros_resolve.log"
  mkdir -p "$dir/logs"
  rm -f "$tmp_info"

  log_start Download ColorOS "query $query_code/$query_region"
  if ! python3 "$dir/bin/coloros_resolver.py" "$query_code" "$tmp_info" \
    --query --region "$query_region" \
    --anti "${COLOROS_ANTI_QUERY:-1}" >"$resolver_log" 2>&1; then
    log_block Download ColorOS "$resolver_log" 1
    rm -f "$tmp_info"
    return 1
  fi

  # shellcheck disable=SC1090
  source "$tmp_info"
  rm -f "$tmp_info"

  log_done ROM Found
  log_event info "  device" "" "" "$coloros_data_code"
  log_event info "  version" "" "" "$coloros_version"
  log_event info "  ota" "" "" "$coloros_ota_version"
  log_event info "  region" "" "" "$coloros_region"
  log_event info "  published" "" "" "$coloros_published"
  log_event info Download URL "" "latest firmware resolved"
  log_event info "  raw link" "" "" "$coloros_check_url"
  log_event info "  signed link" "" "" "$coloros_resolved_url"
  echo "::add-mask::$coloros_check_url"
  echo "::add-mask::$coloros_resolved_url"

  download_file "$coloros_resolved_url" "$coloros_filename"
}

cmd_check() {
  local codename="$1"
  local builder="${2:-true}"
  local prefix query_code candidates_file selected_file resolver_log
  local choice confirm

  if [[ -z "$codename" ]]; then
    echo "Usage: check <codename> [builder]"
    return 1
  fi

  prefix="$(printf "%s" "$codename" | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')"
  if ! is_coloros_device_code "$prefix"; then
    log_fail Check Device "not a ColorOS/OPlus codename: $prefix"
    return 1
  fi

  query_code="$(coloros_query_code "$prefix")"
  candidates_file="$(mktemp "${TMPDIR:-/tmp}"/vs-coloros-candidates.XXXXXX)"
  selected_file="$(mktemp "${TMPDIR:-/tmp}"/vs-coloros-selected.XXXXXX)"
  resolver_log="$dir/logs/check_coloros_resolve.log"
  mkdir -p "$dir/logs"

  log_start Check Firmware "query $query_code/all regions"
  if ! python3 "$dir/bin/coloros_resolver.py" "$query_code" "$candidates_file" \
    --query --list --all-regions \
    --anti "${COLOROS_ANTI_QUERY:-1}" >"$resolver_log" 2>&1; then
    log_block Check Firmware "$resolver_log" 1
    rm -f "$candidates_file" "$selected_file"
    return 1
  fi

  python3 - "$candidates_file" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as stream:
    data = json.load(stream)

print(f"\nAvailable firmware for {data['device_code']}:")
scanned = data.get("scanned_regions", [])
found_regions = {
    str(item.get("region_code") or "").upper()
    for item in data["items"]
    if item.get("region_code")
}
if scanned:
    print(
        f"Scanned {len(scanned)} regions; "
        f"firmware found in {len(found_regions)} region(s)."
    )
for index, item in enumerate(data["items"], 1):
    version = item.get("version_name") or "Unknown"
    ota = item.get("ota_version") or "Unknown"
    published = item.get("published_time") or "Unknown"
    region = item.get("region_code") or "Unknown"
    print(f"  {index}) {version} [latest for {region}]")
    print(f"     OTA: {ota} | Region: {region} | Published: {published}")
PY

  if [[ ! -t 0 ]]; then
    echo
    echo "Run this command in an interactive terminal to select a firmware to build."
    rm -f "$candidates_file" "$selected_file"
    return 0
  fi

  echo
  read -r -p "Select firmware to build (0 to cancel): " choice
  if [[ -z "$choice" || "$choice" == "0" ]]; then
    rm -f "$candidates_file" "$selected_file"
    log_event info Check Firmware "" Cancelled
    return 0
  fi
  if [[ ! "$choice" =~ ^[1-9][0-9]*$ ]]; then
    rm -f "$candidates_file" "$selected_file"
    log_fail Check Selection "invalid number: $choice"
    return 1
  fi

  if ! python3 "$dir/bin/coloros_resolver.py" "$query_code" "$selected_file" \
    --candidate-file "$candidates_file" --index "$choice"; then
    rm -f "$candidates_file" "$selected_file"
    return 1
  fi

  # shellcheck disable=SC1090
  source "$selected_file"
  rm -f "$candidates_file" "$selected_file"
  echo "::add-mask::$coloros_check_url"
  echo "::add-mask::$coloros_resolved_url"
  echo
  echo "Selected: $coloros_version"
  echo "OTA:      $coloros_ota_version"
  echo "Region:   $coloros_region"
  read -r -p "Start building this firmware? [y/N]: " confirm
  case "${confirm,,}" in
  y | yes)
    cmd_build "$coloros_resolved_url" "$builder"
    ;;
  *)
    log_event info Check Build "" Cancelled
    ;;
  esac
}

download() {
  local input="$1"
  local requested_region="${2:-}"
  local codename tmp_info resolved_url resolved_name

  if [[ -z "$input" ]]; then
    log_fail Download Input "Usage: download <codename|url|file.zip> [region]"
    return 1
  fi

  require_rom_target "$input" Download || return 1
  if [[ -f "$input" ]]; then
    local rom_path
    rom_path="$(local_rom_path "$input")"
    if [[ -z "$rom_path" ]]; then
      log_fail Download Input "not a ROM archive (.zip/.tgz): $input"
      return 1
    fi
    if [[ -z "$(local_rom_keep "$rom_path")" ]]; then
      log_start Download Local "$(basename -- "$rom_path")"
      ln -f -- "$rom_path" "$dir/" 2>/dev/null || cp -f -- "$rom_path" "$dir/" || return 1
    fi
    log_done Download Local "$(basename -- "$rom_path")"
    return 0
  fi

  if [[ "$input" =~ ^https?:// ]]; then
    resolved_url="$input"
    resolved_name=""
    if [[ "$input" == *downloadCheck* || "$input" == *downloadcheck* ]]; then
      tmp_info="$dir/.download_direct_info"
      rm -f "$tmp_info"
      log_start Download Resolve "downloadCheck"
      if ! resolve_downloadcheck_url "$input" "$tmp_info"; then
        rm -f "$tmp_info"
        log_fail Download Resolve "cannot resolve downloadCheck URL"
        return 1
      fi
      # shellcheck disable=SC1090
      source "$tmp_info"
      rm -f "$tmp_info"
      resolved_url="${resolved_url:-$input}"
      log_event info Download URL "" "raw link: $input"
      log_event info Download URL "" "signed link: $resolved_url"
      echo "::add-mask::$input"
      echo "::add-mask::$resolved_url"
      log_done Download Resolve "${resolved_name:-resolved}"
    else
      echo "::add-mask::$input"
    fi
    log_event info Download URL "" "direct: $input"
    if [[ "$resolved_name" == *.zip || "$resolved_name" == *.tgz ]]; then
      download_file "$resolved_url" "$resolved_name"
    else
      download_file "$resolved_url"
    fi
    return $?
  fi

  codename="$(printf "%s" "$input" | tr -d '[:space:]' | tr '[:upper:]' '[:lower:]')"
  if [[ -z "$codename" ]]; then
    log_fail Download Codename "Invalid codename: $input"
    return 1
  fi

  if is_coloros_device_code "$codename"; then
    download_coloros "$codename" "$requested_region"
    return $?
  fi

  tmp_info="$dir/.download_rom_info"
  rm -f "$tmp_info"

  if ! python3 - "$codename" "$tmp_info" <<'PY'; then
import json
import shlex
import sys
import urllib.request

codename = sys.argv[1]
out_file = sys.argv[2]
ota_base_url = "https://bkt-sgp-miui-ota-update-alisgp.oss-ap-southeast-1.aliyuncs.com"
raw_url = f"https://raw.githubusercontent.com/HegeKen/HyperData/main/devices/{codename}.json"
blob_url = f"https://github.com/HegeKen/HyperData/blob/main/devices/{codename}.json"

def branch_name(branch):
    name = branch.get("name", "")
    if isinstance(name, dict):
        return str(name.get("en") or name.get("zh") or "")
    return str(name)

def iter_roms(roms):
    if isinstance(roms, dict):
        return roms.values()
    if isinstance(roms, list):
        return roms
    return []

try:
    req = urllib.request.Request(raw_url, headers={"User-Agent": "bhlnk-rom-downloader"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
except Exception as exc:
    raise SystemExit(f"Cannot fetch HyperData JSON: {exc}")

candidates = []
for branch in data.get("branches", []):
    if not isinstance(branch, dict):
        continue

    name = branch_name(branch)
    name_l = name.lower()
    if "hyperos" not in name_l or "stable" not in name_l:
        continue
    if "beta" in name_l or "demo" in name_l:
        continue

    priority = 0 if name == "Xiaomi HyperOS Stable" else 1
    for rom in iter_roms(branch.get("roms")):
        if not isinstance(rom, dict):
            continue
        recovery = str(rom.get("recovery") or "")
        if not recovery.endswith(".zip"):
            continue
        os_version = str(rom.get("os") or "")
        if not os_version:
            continue
        candidates.append({
            "priority": priority,
            "branch": name,
            "os": os_version,
            "android": str(rom.get("android") or ""),
            "release": str(rom.get("release") or ""),
            "recovery": recovery,
            "download_url": f"{ota_base_url}/{os_version}/{recovery}",
            "codename": codename,
            "hyperdata_url": blob_url,
        })

if not candidates:
    raise SystemExit(f"No HyperOS Stable recovery zip found for codename: {codename}")

candidates.sort(key=lambda item: item["os"], reverse=True)
candidates.sort(key=lambda item: item["release"], reverse=True)
candidates.sort(key=lambda item: item["priority"])
rom = candidates[0]

with open(out_file, "w", encoding="utf-8") as fh:
    for key in ("branch", "os", "android", "release", "recovery", "download_url", "codename", "hyperdata_url"):
        fh.write(f"{key}={shlex.quote(rom[key])}\n")
PY
    rm -f "$tmp_info"
    return 1
  fi

  # shellcheck disable=SC1090
  source "$tmp_info"
  rm -f "$tmp_info"

  log_done ROM Found
  log_event info "  codename" "" "" "$codename"
  log_event info "  branch" "" "" "$branch"
  log_event info "  os" "" "" "$os"
  log_event info "  android" "" "" "$android"
  log_event info "  release" "" "" "$release"
  log_event info "  recovery" "" "" "$recovery"
  log_event info "  hyperdata" "" "" "$hyperdata_url"
  log_event info Download URL "" "$download_url"

  download_file "$download_url" "$recovery"
}

# ---------------------------------------------------------------------------
# Self-signed OTA
#
# The ROM trusts our own OTA key instead of the OEM release key, so later
# builds can be installed as a regular A/B OTA (update_engine / Updater)
# while official OEM OTAs, which would wipe the mod, are rejected.
#
# bin/otacerts.zip holds the public certificate baked into every ROM; the
# matching signing key is committed in bin/ota_keys (private repo). Never
# regenerate them: a ROM only accepts OTAs signed with the key matching the
# otacerts.zip it was built with.
# VS_OTA=false disables both the key patch and the OTA package.
# ---------------------------------------------------------------------------
ota_enabled() {
  [[ "${VS_OTA:-true}" != "false" ]]
}

# Password-lock OTA packages (ota_zipcrypt.py) unless VS_OTA_LOCK=false.
# Fills the named array with the ota_payload build arguments.
ota_lock_args() {
  local -n _lock_args="$1"
  _lock_args=()
  [[ "${VS_OTA_LOCK:-true}" == "false" ]] && return 0
  if [[ -z "${VS_OTA_ZIP_SECRET_V2:-}" && ! -s "${ota_zip_secret}_v2" ]]; then
    log_fail OTA Lock "missing bin/ota_keys/zip_secret_v2 (or VS_OTA_ZIP_SECRET_V2); set VS_OTA_LOCK=false to build unlocked"
    return 1
  fi
  _lock_args=(--zip-secret "$ota_zip_secret")
}

ota_key_prepare() {
  local baked_pubkey

  ota_key="$ota_key_dir/ota.pem"
  ota_cert="$ota_key_dir/ota.x509.pem"

  if [[ ! -f "$ota_key" || ! -f "$ota_cert" || ! -f "$ota_certs_zip" ]]; then
    log_fail OTA Key "missing bin/ota_keys/ota.pem, ota.x509.pem or bin/otacerts.zip"
    return 1
  fi
  if [[ "$(openssl x509 -in "$ota_cert" -noout -pubkey 2>/dev/null)" != "$(openssl pkey -in "$ota_key" -pubout 2>/dev/null)" ]]; then
    log_fail OTA Key "ota.pem does not match ota.x509.pem"
    return 1
  fi
  baked_pubkey="$(unzip -p "$ota_certs_zip" '*.pem' 2>/dev/null | openssl x509 -noout -pubkey 2>/dev/null)"
  if [[ "$baked_pubkey" != "$(openssl x509 -in "$ota_cert" -noout -pubkey 2>/dev/null)" ]]; then
    log_fail OTA Key "bin/ota_keys does not match bin/otacerts.zip; ROMs would reject the package"
    return 1
  fi
}

# Record the stock payload layout (partition list, dynamic groups, VABC,
# timestamps, postinstall) before the payload is extracted and deleted, so
# the patched images can be repacked into an equivalent payload later.
capture_ota_meta() {
  local payload_file="$1"
  local reason

  rm -f "$ota_meta"
  ota_enabled || return 0

  # Given the OTA zip, its metadata and payload_properties.txt are read from
  # it; given a bare payload.bin, from the extracted package in zip_temp.
  if ! reason="$($ota_payload meta "$payload_file" "$ota_meta" --stock-dir . 2>&1 >/dev/null)"; then
    rm -f "$ota_meta"
    log_warn OTA Meta "${reason#ota_payload: }; OTA package disabled"
    return 0
  fi
  log_done OTA Meta "stock payload layout saved"
}

patch_ota_key() {
  local target pubkey count=0

  ota_enabled || return 0
  if [[ ! -f "$ota_certs_zip" ]]; then
    log_warn OTA Key "bin/otacerts.zip missing; ROM keeps the OEM OTA key"
    return 0
  fi
  pubkey="$(unzip -p "$ota_certs_zip" '*.pem' 2>/dev/null | openssl x509 -noout -pubkey 2>/dev/null)"
  if [[ -z "$pubkey" ]]; then
    log_fail OTA Key "bin/otacerts.zip holds no valid certificate"
    return 1
  fi

  while IFS= read -r -d '' target; do
    if ! sudo cp -f -- "$ota_certs_zip" "$target"; then
      log_fail OTA Key "cannot replace ${target#$dir/img_temp/}"
      return 1
    fi
    log_event info OTA "${target#$dir/img_temp/}" Patch "own OTA certificate"
    count=$((count + 1))
  done < <(find "$dir/img_temp" -path '*/etc/security/otacerts.zip' -type f -print0 2>/dev/null)

  # Older update_engine builds verify payloads with this PEM instead of
  # otacerts.zip.
  while IFS= read -r -d '' target; do
    if ! printf '%s\n' "$pubkey" | sudo tee "$target" >/dev/null; then
      log_fail OTA Key "cannot replace ${target#$dir/img_temp/}"
      return 1
    fi
    log_event info OTA "${target#$dir/img_temp/}" Patch "own OTA public key"
    count=$((count + 1))
  done < <(find "$dir/img_temp" -path '*/etc/update_engine/update-payload-key.pub.pem' -type f -print0 2>/dev/null)

  if ((count == 0)); then
    log_fail OTA Key "no otacerts.zip found in img_temp"
    return 1
  fi
  log_done OTA Key "patched $count file(s)"
}

# Full A/B OTA of the patched images, signed with the key patch_ota_key()
# installed. Rebuilds the EROFS images first whenever img_temp holds
# extracted partitions, so running it alone (bhlnk build_ota_package) picks
# up edits made there. Must run before super/encryption steps consume the
# standalone partition images.
# A stock build (is_mkstock=true, see pack_stock) ships the images as they
# are, unlocked: MushromTool flashes it without a server key and without a coin.
# $1 (optional): version of images already built by the caller;
# the package is built from them as-is instead of rebuilding img_temp.
build_ota_package() {
  local prebuilt_version="${1:-}"
  local out_dir="$dir/ota_out"
  local out_name log_file ota_patch
  local fw_dir="$dir/$device/firmware-update"
  local layout_a="$dir/zip_temp/super_layout_a.img"
  local layout_b="$dir/zip_temp/super_layout_b.img"
  local products key
  local stock="${is_mkstock:-false}"
  local -a lock_args=()
  local -a info_args=()
  local -a version_args=()
  local -a group_args=()
  local -a drop_args=()

  if [[ ! -f "$ota_meta" ]]; then
    log_fail OTA Package "missing ota_meta.json; source ROM must be a payload.bin OTA"
    return 1
  fi
  ota_key_prepare || {
    log_fail OTA Package "OTA key unavailable"
    return 1
  }
  if [[ "$stock" != "true" ]]; then
    ota_lock_args lock_args || return 1
  fi

  # Recompute the version instead of trusting info: workspaces detected by
  # older tool versions still carry names like 7.81.PRE.ST.
  if [[ -n "$prebuilt_version" ]]; then
    ota_patch="$prebuilt_version"
  else
    ota_patch="$(get_patch)"
  fi
  # The package serves both OTA and fastboot installs, so neither the version
  # nor the file name carries an .OTA tag (older workspaces may still have one).
  ota_patch="${ota_patch%.OTA}"

  if [[ "$stock" != "true" && -z "$prebuilt_version" && -d "$dir/img_temp" ]]; then
    setprop ro.hypervs.version "$ota_patch"
    # Features the patches applied (add_cap); Mushrom locks the others.
    setprop ro.hypervs.caps "$(paste -sd, "$dir/rom_caps" 2>/dev/null)"
    EROFS_BUILD_TIME="${build_timestamp:-$(date -u +%s)}" build_erofs_images PackOTA || return 1
  fi

  # Fastboot side of the package: a super layout per slot, so MushromTool can
  # flash super in pieces into whichever slot the device is on, and the names
  # fastboot may report for this device (the *.check files device_platform
  # wrote), which MushromTool matches before flashing.
  (cd "$fw_dir" && build_super_layout_image a "$layout_a" &&
    build_super_layout_image b "$layout_b") || return 1
  products="$(find "$fw_dir" -maxdepth 1 -name '*.check' -printf '%f\n' 2>/dev/null |
    sed 's/\.check$//' | tr '[:upper:]' '[:lower:]' | sort -u | paste -sd, -)"
  if [[ -z "$products" ]]; then
    log_fail OTA Package "no *.check in firmware-update; run device_platform first"
    return 1
  fi
  # HyperOS MTK: the ROM ships neither LK nor preloader; the device keeps the
  # bootloader it runs. Both are left out of the payload (so the OTA never
  # writes them) and out of the fastboot images (moved to mics, which is not
  # shipped but where device_platform/is_mtk_rom still find lk.img on a
  # rerun), and vs-isskiplk tells MushromTool not to flash LK.
  if [[ "${brand:-HyperOS}" == "HyperOS" ]] && is_mtk_rom; then
    local bl_img bl_part
    mkdir -p "$dir/$device/mics" || return 1
    shopt -s nullglob
    for bl_img in "$fw_dir"/lk.img "$fw_dir"/preloader*; do
      [[ -f "$bl_img" ]] || continue
      if ! mv -f -- "$bl_img" "$dir/$device/mics/"; then
        shopt -u nullglob
        log_fail OTA Package "cannot move $(basename -- "$bl_img") to $device/mics"
        return 1
      fi
    done
    shopt -u nullglob
    # Payload names: lk plus whatever preloader partitions the stock meta has
    # (preloader_raw on Xiaomi MTK).
    while IFS= read -r bl_part; do
      [[ -n "$bl_part" ]] && drop_args+=(--drop "$bl_part")
    done < <(python3 -c 'import json,re,sys
for p in json.load(open(sys.argv[1]))["partitions"]:
    if re.fullmatch(r"lk|preloader.*", p["name"]): print(p["name"])' "$ota_meta")
    isSkipLK=true
  fi
  for key in brand base os android builder buildtime isSkipLK isSkipABL; do
    [[ -n "${!key:-}" ]] && info_args+=(--set "vs-${key,,}=${!key}")
  done

  mkdir -p "$out_dir" "$dir/logs"
  # <prefix>_<DEVICE>_<iden>_<version>.zip, with the version recomputed above.
  out_name="${final_repack%_*}_${ota_patch}.zip"
  # Kept for build_magisk_ota, whose boot-only package must match this build.
  ota_timestamp="${build_timestamp:-$(date -u +%s)}"
  ota_version="$ota_patch"
  ota_products="$products"
  version_args=(--set "vs-version=$ota_patch" --timestamp "$ota_timestamp")
  if [[ "$stock" == "true" ]]; then
    # Stock keeps its own name and the payload timestamps of the source OTA.
    out_name="$(stock_repack_name)"
    version_args=(--set "vs-stock=true")
  fi
  log_file="$dir/logs/ota_payload.log"
  : >"$log_file"

  # VS_SUPER_SIZE also replaces the stock group size in the payload manifest.
  if [[ -n "${VS_SUPER_SIZE:-}" ]]; then
    load_coloros_super_size >/dev/null || return 1
    group_args=(--group-size "$((device_size - $(super_reserve_mib) * 1024 * 1024))")
  fi

  log_start OTA Package "$out_name"
  [[ ${#group_args[@]} -eq 0 ]] || log_event info "  Group size" "" "" "${group_args[1]} (VS_SUPER_SIZE)"
  log_event info "  Brand" "" "" "${brand:-HyperOS}"
  log_event info "  vbmeta" "" "" "stock"
  log_event info "  COW size" "" "" "raised (+1/16 + 16 MiB)"
  log_event info "  Signing" "" "" "after COW rewrite, streamed into the package in one pass"
  log_event info "  Verify" "" "" "decode every REPLACE_XZ/BZ blob, repair bad ones as REPLACE"
  log_event info "  Lock" "" "" "$([[ ${#lock_args[@]} -gt 0 ]] && echo "AES-256 zip password (Mushrom, MushromTool via API)" || echo "off$([[ "$stock" == "true" ]] && echo " (stock)")")"
  log_event info "  Fastboot" "" "" "$products"
  [[ "${isSkipLK:-}" != "true" ]] || log_event info "  Skip LK" "" "" "lk/preloader removed from payload and fastboot (HyperOS MTK)"
  log_event info "  Log" "" "" "${log_file#"$dir/"}"
  if ! $ota_payload build \
    "${lock_args[@]}" \
    "${group_args[@]}" \
    "${drop_args[@]}" \
    --meta "$ota_meta" \
    --images "$fw_dir" \
    --images "$dir/$device/mics" \
    --super-layout "a=$layout_a" \
    --super-layout "b=$layout_b" \
    --fastboot-images "$fw_dir" \
    --set "vs-products=$products" \
    --set "vs-name=$out_name" \
    "${info_args[@]}" \
    --key "$ota_key" \
    --cert "$ota_cert" \
    --out "$out_dir/$out_name" \
    --workdir "$dir/zip_temp/ota" \
    --delta-generator "$delta_generator" \
    --device "$device" \
    "${version_args[@]}" \
    --set "vs-iden=$iden" \
    --log "$log_file" >>"$log_file" 2>&1; then
    log_block OTA Package "$log_file" 1
    return 1
  fi
  ota_package="$out_dir/$out_name"
  # Keep info in step with the file just written, so a later `bhlnk upload`
  # (which reads final_repack) finds it.
  final_repack="$out_name"
  if grep -q '^final_repack=' "$dir/info"; then
    sed -i "s|^final_repack=.*|final_repack=$out_name|" "$dir/info"
  else
    echo "final_repack=$out_name" >>"$dir/info"
  fi
  log_done OTA Package "ota_out/$out_name" "$(du -h "$ota_package" | awk '{print $1}')"
}

# Boot-only OTA next to the full package (<full name>-magisk.zip): a partial
# payload holding just the Magisk-patched boot/init_boot (magisk.img from
# Root), so a device already on this build gets root through update_engine
# without the whole ROM. Same key, lock, version and timestamp as the full one.
build_magisk_ota() {
  local work="$dir/zip_temp/magisk_ota"
  local log_file="$dir/logs/ota_magisk.log"
  local magisk part out key
  local -a lock_args=() info_args=()

  magisk="$(get_file_dir1 "magisk.img" "$device")"
  if [[ ! -f "$magisk" ]]; then
    log_fail MagiskOTA Package "magisk.img not found; Root did not run"
    return 1
  fi
  # Root patches init_boot when the device has one, boot otherwise.
  if [[ -f "$(get_file_dir1 "init_boot.img" "$device")" ]]; then
    part="init_boot"
  else
    part="boot"
  fi
  if [[ -z "${ota_package:-}" || -z "${ota_timestamp:-}" ]]; then
    log_fail MagiskOTA Package "build the full OTA first (build_ota_package)"
    return 1
  fi
  ota_lock_args lock_args || return 1
  for key in brand base os android builder buildtime; do
    [[ -n "${!key:-}" ]] && info_args+=(--set "vs-${key,,}=${!key}")
  done

  out="${ota_package%.zip}-magisk.zip"
  sudo rm -rf -- "$work"
  mkdir -p "$work/images" "$dir/logs"
  cp -f -- "$magisk" "$work/images/${part}.img" || {
    log_fail MagiskOTA Package "copy failed: ${magisk#"$dir/"}"
    return 1
  }
  : >"$log_file"

  log_start MagiskOTA Package "$(basename -- "$out")"
  log_event info "  Partition" "" "" "$part (${magisk#"$dir/"})"
  log_event info "  Log" "" "" "${log_file#"$dir/"}"
  if ! $ota_payload build \
    "${lock_args[@]}" \
    --partial "$part" \
    --meta "$ota_meta" \
    --images "$work/images" \
    --set "vs-products=$ota_products" \
    --set "vs-name=$(basename -- "$out")" \
    --set "vs-version=$ota_version" \
    --set "vs-iden=$iden" \
    --set "vs-root=magisk" \
    --set "vs-partial=$part" \
    "${info_args[@]}" \
    --key "$ota_key" \
    --cert "$ota_cert" \
    --out "$out" \
    --workdir "$work/payload" \
    --delta-generator "$delta_generator" \
    --device "$device" \
    --timestamp "$ota_timestamp" \
    --log "$log_file" >>"$log_file" 2>&1; then
    log_block MagiskOTA Package "$log_file" 1
    return 1
  fi
  sudo rm -rf -- "$work"
  magisk_ota_package="$out"
  log_done MagiskOTA Package "ota_out/$(basename -- "$out")" "$(du -h "$out" | awk '{print $1}')"
}

# Full A/B OTA straight from a folder of .img files (e.g. an extracted
# fastboot ROM), without a workspace. Partition layout comes from a stock OTA:
# ota_meta.json (default), another meta .json, a stock payload.bin or a stock
# OTA .zip. A super.img in the folder is unpacked for any dynamic partition
# that has no standalone image.
# Usage: imgota <img_dir> [--meta <json|payload.bin|ota.zip>] [--out <zip>]
#               [--device <code>] [--timestamp <unix>] [--set KEY=VALUE]...
cmd_imgota() {
  local img_dir="" meta_src="$ota_meta" out="" img_device="" stamp=""
  local work="$dir/zip_temp/imgota"
  local log_file="$dir/logs/imgota.log"
  local meta_file super raw f base
  local -a sets=() image_dirs=() lock_args=()

  while (($#)); do
    case "$1" in
    --meta)
      meta_src="$2"
      shift 2
      ;;
    --out)
      out="$2"
      shift 2
      ;;
    --device)
      img_device="$2"
      shift 2
      ;;
    --timestamp)
      stamp="$2"
      shift 2
      ;;
    --set)
      sets+=(--set "$2")
      shift 2
      ;;
    -*)
      echo "Unknown imgota option: $1"
      return 1
      ;;
    *)
      if [[ -n "$img_dir" ]]; then
        echo "imgota: only one image directory allowed"
        return 1
      fi
      img_dir="$1"
      shift
      ;;
    esac
  done

  if [[ -z "$img_dir" || ! -d "$img_dir" ]]; then
    log_fail ImgOTA Images "usage: bhlnk imgota <img_dir> [--meta <json|payload.bin|ota.zip>] [--out <zip>]"
    return 1
  fi
  img_dir="$(realpath -- "$img_dir")"
  if [[ ! -f "$meta_src" ]]; then
    log_fail ImgOTA Meta "not found: $meta_src"
    return 1
  fi
  meta_src="$(realpath -- "$meta_src")"

  [[ -f "$dir/info" ]] && source "$dir/info"
  img_device="${img_device:-${device:-}}"
  img_device="${img_device,,}"

  ota_key_prepare || {
    log_fail ImgOTA Key "OTA key unavailable"
    return 1
  }

  sudo rm -rf -- "$work"
  mkdir -p "$work" "$dir/logs" "$dir/ota_out"
  : >"$log_file"
  image_dirs=(--images "$img_dir")

  # Layout: a .json is used as-is; a payload.bin / OTA zip is dumped first
  # (the payload is read in place inside the zip).
  case "${meta_src,,}" in
  *.json) meta_file="$meta_src" ;;
  *.zip | *.bin)
    local stock_args=()
    if [[ "${meta_src,,}" == *.bin ]]; then
      f="$(dirname -- "$meta_src")"
      [[ -f "$f/payload_properties.txt" ]] && stock_args=(--stock-dir "$f")
    fi
    meta_file="$work/ota_meta.json"
    if ! $ota_payload meta "$meta_src" "$meta_file" "${stock_args[@]}" >>"$log_file" 2>&1; then
      log_block ImgOTA Package "$log_file" 1
      return 1
    fi
    ;;
  *)
    log_fail ImgOTA Meta "expected .json, payload.bin or .zip: $meta_src"
    return 1
    ;;
  esac

  # Dynamic partitions missing as standalone images come from super.img.
  local missing
  missing="$(
    python3 - "$meta_file" "$img_dir" <<'PY'
import json, sys, os
meta = json.load(open(sys.argv[1]))
dyn = {p for g in meta["dynamic"]["groups"] for p in g["partitions"]}
print(" ".join(p["name"] for p in meta["partitions"]
               if p["name"] in dyn and not os.path.isfile(os.path.join(sys.argv[2], p["name"] + ".img"))))
PY
  )"
  super="$img_dir/super.img"
  if [[ -n "$missing" && -f "$super" ]]; then
    log_start ImgOTA Super "unpack for: $missing"
    raw="$work/super_raw.img"
    if [[ "$(od -An -tx1 -N4 -- "$super" 2>/dev/null | tr -d '[:space:]')" == "3aff26ed" ]]; then
      simg2img "$super" "$raw" 2>>"$log_file" || {
        log_fail ImgOTA Super "simg2img failed"
        return 1
      }
    else
      raw="$super"
    fi
    mkdir -p "$work/super"
    if ! "$sunpack" "$raw" "$work/super" >>"$log_file" 2>&1; then
      log_block ImgOTA Package "$log_file" 1
      return 1
    fi
    [[ "$raw" != "$super" ]] && rm -f -- "$raw"
    # lpunpack names images <partition>_<slot>; keep the non-empty copy.
    for f in "$work/super"/*_a.img "$work/super"/*_b.img; do
      [[ -f "$f" ]] || continue
      base="$(basename -- "$f" .img)"
      base="${base%_[ab]}"
      if [[ -s "$f" && ! -f "$work/super/$base.img" ]]; then
        mv -- "$f" "$work/super/$base.img"
      else
        rm -f -- "$f"
      fi
    done
    image_dirs+=(--images "$work/super")
  elif [[ -n "$missing" ]]; then
    log_fail ImgOTA Images "missing: $missing (and no super.img)"
    return 1
  fi

  if [[ -z "$out" ]]; then
    out="$dir/ota_out/${img_device:-device}-imgota-$(date +%Y%m%d-%H%M%S).zip"
  fi
  mkdir -p "$(dirname -- "$out")"
  out="$(realpath -m -- "$out")"

  local build_args=(
    --meta "$meta_file"
    "${image_dirs[@]}"
    --key "$ota_key"
    --cert "$ota_cert"
    --out "$out"
    --workdir "$work/payload"
    --delta-generator "$delta_generator"
    --log "$log_file"
    "${sets[@]}"
  )
  [[ -n "$img_device" ]] && build_args+=(--device "$img_device")
  ota_lock_args lock_args || return 1
  build_args+=("${lock_args[@]}")
  [[ -n "$stamp" ]] && build_args+=(--timestamp "$stamp")

  log_start ImgOTA Package "$(basename -- "$out")"
  log_event info "  Images" "" "" "$img_dir"
  log_event info "  Meta" "" "" "$(basename -- "$meta_src")"
  log_event info "  Verify" "" "" "decode every REPLACE_XZ/BZ blob, repair bad ones as REPLACE"
  log_event info "  Log" "" "" "${log_file#"$dir/"}"
  if ! $ota_payload build "${build_args[@]}" >>"$log_file" 2>&1; then
    log_block ImgOTA Package "$log_file" 1
    return 1
  fi
  sudo rm -rf -- "$work"
  log_done ImgOTA Package "$out" "$(du -h "$out" | awk '{print $1}')"
}

# MTK ROMs ship lk/preloader images, and the props name the SoC vendor. The
# chip is only known once the firmware is extracted.
is_mtk_rom() {
  local fw="$dir/$device/firmware-update"
  [[ -f "$fw/lk.img" || -f "$dir/$device/mics/lk.img" ]] && return 0
  compgen -G "$fw/preloader*" >/dev/null && return 0
  [[ "$(getprop 'ro.soc.manufacturer')" == *[Mm]edia[Tt]ek* ]] && return 0
  [[ "$(getprop 'Build.BRAND')" == "MTK" ]]
}

# Device firmware overrides: module/firmware/<device>_<android>/*.img (e.g.
# plg110_16/lk.img), kept in this repo rather than synced, replace the stock
# images in firmware-update. Runs after device_platform so the platform is
# read from the stock LK.
# Only partitions the stock ROM ships are replaced (mics is still checked for
# workspaces from older builds that moved lk.img there), so a stray file can
# never add a partition to the flash list.
firmware_add() {
  local src="$dir/module/firmware/${device,,}_${os}"
  local fw="$dir/$device/firmware-update"
  local img name count=0

  [[ -d "$src" ]] || return 0
  log_start FirmwareAdd "${device,,}_${os}"

  shopt -s nullglob
  for img in "$src"/*.img; do
    name="$(basename -- "$img")"
    if [[ ! -f "$fw/$name" && ! -f "$dir/$device/mics/$name" ]]; then
      log_warn FirmwareAdd "$name" "not a stock image; skipped"
      continue
    fi
    if ! cp -f -- "$img" "$fw/$name"; then
      shopt -u nullglob
      log_fail FirmwareAdd "$name" "copy failed"
      return 1
    fi
    log_event info FirmwareAdd "$name" Copy "-> firmware-update/$name"
    count=$((count + 1))
  done
  shopt -u nullglob

  log_done FirmwareAdd "${device,,}_${os}" "$count image(s)"
}

patch_log_name() {
  local patch_func="$1"
  echo "${patch_func//[^A-Za-z0-9._-]/_}.log"
}

print_patch_log() {
  local patch_func="$1"
  local status="$2"
  local log_file="$3"
  local log_mode="${PATCH_LOG_MODE:-summary}"

  if [[ "$log_mode" == "full" || "$status" -ne 0 || "$patch_func" == "opsystemui" ]]; then
    log_block PatchMushrom "$patch_func" "$log_file" "$status"
  elif [[ "$log_mode" != "quiet" ]]; then
    log_done PatchMushrom "$patch_func"
  fi
}

is_coloros_build() {
  [[ "$brand" == "ColorOS" || "$isColorOS" == "true" ]]
}

# MiB between the stock dynamic groups and the super partition: 10 on Xiaomi
# (pudding: 13411287040 + 10 MiB = 13421772800), 4 on OPlus (PLG110:
# 19323158528 + 4 MiB = 18 GiB).
super_reserve_mib() {
  if is_coloros_build; then
    echo 4
  else
    echo 10
  fi
}

capture_payload_super_size() {
  local payload_file="$1"
  local reader="$dir/bin/payload_super_size.py"
  local detected_size=""

  # The only source of the super size: stock dynamic groups of the input
  # payload + the OEM reserve, saved to info and read back by
  # load_coloros_super_size when the super is packed. It must equal the real
  # super partition, or a later A/B OTA fails (liblp size mismatch,
  # update_engine error 7).
  if [[ -f "$reader" ]]; then
    detected_size="$(python3 "$reader" --reserve-mib "$(super_reserve_mib)" "$payload_file" 2>/dev/null)"
  fi

  sed -i '/^payload_super_size=/d' "$dir/info"
  if [[ "$detected_size" =~ ^[1-9][0-9]*$ ]]; then
    payload_super_size="$detected_size"
    printf 'payload_super_size=%s\n' "$payload_super_size" >>"$dir/info"
    log_done SuperSize "Payload" "$payload_super_size"
  else
    unset payload_super_size
    log_warn SuperSize "Payload" "size unavailable; super will use the calculated size"
  fi
}

# Persist a build size metric (super_size, <partition>_size, ...) to info and
# to every *.check file already generated for this device. device_platform
# snapshots info into *.check before super.img/partition images are built,
# so late results have to be pushed into both places explicitly. Overwrites
# any existing value for the key.
update_check_size() {
  local key="$1"
  local value="$2"
  local target
  local -a targets=("$dir/info")

  [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || return 1
  [[ "$value" =~ ^[0-9]+$ ]] || return 1

  while IFS= read -r -d '' target; do
    targets+=("$target")
  done < <(find "$dir/$device/firmware-update" -maxdepth 1 -name '*.check' -print0 2>/dev/null)

  for target in "${targets[@]}"; do
    [[ -f "$target" ]] || continue
    if grep -q "^${key}=" "$target"; then
      sed -i "s/^${key}=.*/${key}=${value}/" "$target"
    else
      printf '%s=%s\n' "$key" "$value" >>"$target"
    fi
  done
}

load_coloros_super_size() {
  device_size=""

  # VS_SUPER_SIZE forces the super size (bytes) for a device whose super
  # partition differs from the stock payload, e.g. a repartitioned unit.
  if [[ -n "${VS_SUPER_SIZE:-}" ]]; then
    if [[ "$VS_SUPER_SIZE" =~ ^[1-9][0-9]*$ ]] && ((VS_SUPER_SIZE % 4096 == 0)); then
      device_size="$VS_SUPER_SIZE"
      log_done SuperSize "${device^^} (VS_SUPER_SIZE)" "$device_size"
      return 0
    fi
    log_fail SuperSize "${device^^}" "invalid VS_SUPER_SIZE=$VS_SUPER_SIZE (bytes, 4096-aligned)"
    return 1
  fi

  # Per-device hardcoded override from fastboot/supersize.list (device codename,
  # uppercase key: super_size_HOUJI=...). Wins over the payload-derived size so
  # devices whose stock payload declares a larger super than the partition gets.
  local supersize_list="$dir/fastboot/supersize.list"
  if [[ -f "$supersize_list" ]]; then
    local list_key="super_size_${device^^}"
    local list_val
    list_val="$(grep -m1 "^${list_key}=" "$supersize_list" | cut -d= -f2 | tr -d '[:space:]')"
    if [[ "$list_val" =~ ^[1-9][0-9]*$ ]] && ((list_val % 4096 == 0)); then
      device_size="$list_val"
      log_done SuperSize "${device^^} (supersize.list)" "$device_size"
      return 0
    fi
  fi

  # Read back the size capture_payload_super_size saved to info at extract.
  if [[ -f "$dir/info" ]]; then
    device_size="$(sed -n 's/^payload_super_size=//p' "$dir/info" | tail -n 1)"
  fi

  if [[ "$device_size" =~ ^[1-9][0-9]*$ ]] && ((device_size % 4096 == 0)); then
    log_done SuperSize "${device^^} (info)" "$device_size"
    return 0
  fi

  device_size=""
  log_warn SuperSize "${device^^}" \
    "no payload_super_size in info; using calculated size (OTA will not install)"
}

build_empty_super_layout_image() {
  local firmware_dir="$dir/$device/firmware-update"
  local mics_dir="$dir/$device/mics"
  local output_file="$mics_dir/super.img"
  local temp_file="$mics_dir/.super.layout.$$"
  local size_arg group_size part partition_count=0
  local -a lpmake_args

  load_coloros_super_size || return 1
  build_super_partition_lists
  print_super_size_info
  size_arg="${super_lpadd_device_size:-${device_size:-}}"
  group_size="${super_lpadd_group_size:-${device_size:-}}"

  if [[ ! "$size_arg" =~ ^[1-9][0-9]*$ || ! "$group_size" =~ ^[1-9][0-9]*$ ]]; then
    log_fail SuperLayout super.img "invalid device/group size"
    return 1
  fi

  lpmake_args=(
    --metadata-size 65536
    --super-name super
    --metadata-slots 3
    --device-size "$size_arg"
    --group "qti_dynamic_partitions_a:$group_size"
    --group "qti_dynamic_partitions_b:$group_size"
  )

  # Mirror only dynamic partitions present in this ROM. Both slot names use
  # zero extents, so this image rewrites layout metadata without payload data.
  for part in $super_part; do
    if [[ ! -f "$firmware_dir/${part}.img" ]] &&
      ! grep -q "^${part}=" "$dir/info" 2>/dev/null; then
      continue
    fi
    lpmake_args+=(
      --partition "${part}_a:readonly:0:qti_dynamic_partitions_a"
      --partition "${part}_b:readonly:0:qti_dynamic_partitions_b"
    )
    partition_count=$((partition_count + 2))
  done

  if ((partition_count == 0)); then
    log_fail SuperLayout super.img "no dynamic partitions found"
    return 1
  fi

  if ! mkdir -p "$mics_dir"; then
    log_fail SuperLayout super.img "cannot create $device/mics"
    return 1
  fi
  rm -f -- "$temp_file"

  log_start SuperLayout super.img "empty layout"
  if ! "$dir/bin/lpmake" "${lpmake_args[@]}" --sparse --force-full-image \
    --output "$temp_file" >/dev/null 2>&1; then
    rm -f -- "$temp_file"
    log_fail SuperLayout super.img "lpmake failed"
    return 1
  fi

  if [[ ! -s "$temp_file" ]] ||
    [[ "$(od -An -tx1 -N4 "$temp_file" 2>/dev/null | tr -d '[:space:]')" != "3aff26ed" ]]; then
    rm -f -- "$temp_file"
    log_fail SuperLayout super.img "invalid sparse image"
    return 1
  fi
  if ! mv -f -- "$temp_file" "$output_file"; then
    rm -f -- "$temp_file"
    log_fail SuperLayout super.img "cannot save to $device/mics"
    return 1
  fi

  log_done SuperLayout super.img "$partition_count empty partitions; device size $size_arg"
}

build_super_image() {
  local target_slot="${1:-${super_target_slot:-a}}"

  load_coloros_super_size || return 1
  build_super_partition_lists
  log_start Super super.img
  if ! run_lpmake_super super.img "$target_slot"; then
    rm -f super.img
    log_fail Super super.img "build failed"
    return 1
  fi

  if [[ ! -s super.img ]]; then
    rm -f super.img
    log_fail Super super.img "not created"
    return 1
  fi

  local built_super_size
  built_super_size=$(stat -c%s super.img 2>/dev/null)
  if [[ "$built_super_size" =~ ^[0-9]+$ ]]; then
    update_check_size super_size "$built_super_size"
  fi

  if [[ -f super.img ]] && ! is_local_builder; then
    for i in $super_part; do
      [[ -f "$i.img" ]] && rm "$i.img"
    done
  fi

  log_done Super super.img
}

patch_oplus_aizerofs_camera_access() {
  local vendor_init="$dir/img_temp/vendor/etc/init"
  local file
  local relative
  local owner_lines
  local mode_lines
  local patched=0
  local found=0
  local -a rc_files=()

  if [[ ! -d "$vendor_init" ]]; then
    log_warn CameraAIZeroFS "vendor/etc/init" "not found; skip /dev/aizerofs access patch"
    return 0
  fi

  while IFS= read -r -d '' file; do
    if grep -Eq '^[[:space:]]*(chown|chmod)[[:space:]].*[[:space:]]/dev/aizerofs[[:space:]]*$' "$file"; then
      rc_files+=("$file")
    fi
  done < <(find "$vendor_init" -type f -name '*.rc' -print0)

  if ((${#rc_files[@]} == 0)); then
    log_warn CameraAIZeroFS "/dev/aizerofs" "init rule not found; device does not expose this workaround target"
    return 0
  fi

  log_start CameraAIZeroFS "Allow Camera HAL read-only access to /dev/aizerofs"

  for file in "${rc_files[@]}"; do
    relative="${file#"$dir/img_temp/"}"
    owner_lines=$(grep -Ec '^[[:space:]]*chown[[:space:]]+(system[[:space:]]+system|root[[:space:]]+root|camera[[:space:]]+root)[[:space:]]+/dev/aizerofs[[:space:]]*$' "$file")
    mode_lines=$(grep -Ec '^[[:space:]]*chmod[[:space:]]+(0644|0600|0400)[[:space:]]+/dev/aizerofs[[:space:]]*$' "$file")

    if ((owner_lines != 1 || mode_lines != 1)); then
      log_fail CameraAIZeroFS "$relative" "unexpected /dev/aizerofs init rules; refusing a partial patch"
      return 1
    fi

    if grep -Eq '^[[:space:]]*chown[[:space:]]+camera[[:space:]]+root[[:space:]]+/dev/aizerofs[[:space:]]*$' "$file" &&
      grep -Eq '^[[:space:]]*chmod[[:space:]]+0400[[:space:]]+/dev/aizerofs[[:space:]]*$' "$file"; then
      found=$((found + 1))
      continue
    fi

    sed -i -E \
      -e 's#^([[:space:]]*)chown[[:space:]]+(system[[:space:]]+system|root[[:space:]]+root)[[:space:]]+/dev/aizerofs[[:space:]]*$#\1chown camera root /dev/aizerofs#' \
      -e 's#^([[:space:]]*)chmod[[:space:]]+(0644|0600)[[:space:]]+/dev/aizerofs[[:space:]]*$#\1chmod 0400 /dev/aizerofs#' \
      "$file"

    if ! grep -Eq '^[[:space:]]*chown[[:space:]]+camera[[:space:]]+root[[:space:]]+/dev/aizerofs[[:space:]]*$' "$file" ||
      ! grep -Eq '^[[:space:]]*chmod[[:space:]]+0400[[:space:]]+/dev/aizerofs[[:space:]]*$' "$file"; then
      log_fail CameraAIZeroFS "$relative" "failed to install Camera HAL read-only device permissions"
      return 1
    fi

    patched=$((patched + 1))
    found=$((found + 1))
  done

  log_done CameraAIZeroFS "/dev/aizerofs" "$patched patched, $found verified init file(s)"
}

patch_oplus_camera_vendor_tags() {
  local config="$dir/img_temp/odm/etc/camera/config/oplus_camera_config"

  if [[ ! -f "$config" ]]; then
    log_warn CameraConfig "odm/etc/camera/config/oplus_camera_config" "not found; skip vendor-tag patch"
    return 0
  fi
  if [[ ! -f "$dir/bin/patch_oplus_camera_config.py" ]]; then
    log_fail CameraConfig "oplus_camera_config" "patcher not found"
    return 1
  fi

  log_start CameraConfig "Preset + Hasselblad vendor tags"
  if ! $patch_oplus_camera_config "$config"; then
    log_fail CameraConfig "oplus_camera_config" "vendor-tag patch failed"
    return 1
  fi
  log_done CameraConfig "oplus_camera_config" "patched in extracted ODM before image build"
}

patch_oplus_default_touch_rate() {
  local odm_root="$dir/img_temp/odm"

  if [[ ! -d "$odm_root/firmware/tp" ]]; then
    log_warn TouchRate "odm/firmware/tp" "not found; skip default 180 Hz patch"
    return 0
  fi
  if [[ ! -f "$dir/bin/patch_oplus_touch_rate.py" ]]; then
    log_fail TouchRate "patch_oplus_touch_rate.py" "patcher not found"
    return 1
  fi

  log_start TouchRate "Promote 125 Hz default to 180 Hz"
  if ! $patch_oplus_touch_rate "$odm_root"; then
    log_fail TouchRate "ODM touch config" "default-rate patch failed"
    return 1
  fi
  log_done TouchRate "ODM touch config" "conditional default-rate patch complete"
}

build_erofs_images() {
  local warn_scope="${1:-Pack}"
  local part
  local fs_config
  local max_jobs="${VS_EROFS_JOBS:-$(build_cpus)}"
  local failed=""
  local status_dir
  local -a parts=()
  local -a pids=()

  log_start EROFS "Make images"
  for part in $(get_extract_parts); do
    if [[ "$(eval echo "\$$part")" == "erofs" ]]; then
      if [[ -d "$dir/img_temp/$part" ]]; then
        fs_config="$dir/img_temp/config/${part}_fs_config"
        if [[ ! -f "$fs_config" ]]; then
          log_fail "$warn_scope" "${part}.img" "missing fs_config: ${part}_fs_config"
          return 1
        fi
        parts+=("$part")
      fi
    else
      log_warn "$warn_scope" "$part" "cannot determine image type"
    fi
  done

  # mkfs.erofs compresses on a single core, so the partitions are built side
  # by side: at most $VS_EROFS_JOBS (default: one per build_cpus) at a time. Each
  # job leaves its exit status in status_dir.
  [[ "$max_jobs" =~ ^[1-9][0-9]*$ ]] || max_jobs=1
  status_dir="$(mktemp -d)" || return 1
  for part in "${parts[@]}"; do
    log_event info "$warn_scope" "${part}.img" Build "from img_temp/$part"
    (
      (
        # auto_add and later patch steps can add files after manual_add has
        # already refreshed the canned fs_config. Always synchronize it with
        # the final partition tree immediately before building the image.
        if ! $fspatch "$dir/img_temp/$part" "$dir/img_temp/config/${part}_fs_config" >/dev/null 2>&1; then
          log_fail "$warn_scope" "${part}.img" "failed to refresh ${part}_fs_config"
          exit 1
        fi
        p_erofs b "$part"
      )
      echo "$?" >"$status_dir/$part"
    ) &
    pids+=("$!")
    while (($(jobs -rp | wc -l) >= max_jobs)); do
      wait -n 2>/dev/null || true
    done
  done
  ((${#pids[@]} == 0)) || wait "${pids[@]}"

  for part in "${parts[@]}"; do
    [[ "$(cat "$status_dir/$part" 2>/dev/null)" == "0" ]] || failed+=" $part"
  done
  rm -rf "$status_dir"
  if [[ -n "$failed" ]]; then
    log_fail "$warn_scope" "Make images" "failed:$failed"
    return 1
  fi
  log_done EROFS "Make images" "${#parts[@]} partition(s), up to $max_jobs at a time"
}

build_erofs_partition() {
  local part="$1"
  local warn_scope="${2:-Flash}"
  local fs_config

  if [[ -z "$part" || ! "$part" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    log_fail "$warn_scope" Partition "invalid partition name: ${part:-empty}"
    return 1
  fi
  if ! grep -qw -- "$part" <<<"$(get_extract_parts)"; then
    log_fail "$warn_scope" "$part" "partition is not in the extracted partition list"
    return 1
  fi
  if [[ "$(eval echo "\$$part")" != "erofs" ]]; then
    log_fail "$warn_scope" "${part}.img" "partition is not EROFS"
    return 1
  fi
  if [[ ! -d "$dir/img_temp/$part" ]]; then
    log_fail "$warn_scope" "${part}.img" "missing img_temp/$part"
    return 1
  fi

  fs_config="$dir/img_temp/config/${part}_fs_config"
  if [[ ! -f "$fs_config" ]]; then
    log_fail "$warn_scope" "${part}.img" "missing fs_config: ${part}_fs_config"
    return 1
  fi
  if ! $fspatch "$dir/img_temp/$part" "$fs_config" >/dev/null 2>&1; then
    log_fail "$warn_scope" "${part}.img" "failed to refresh ${part}_fs_config"
    return 1
  fi

  log_start EROFS "Make ${part}.img"
  log_event info "$warn_scope" "${part}.img" Build "from img_temp/$part"
  p_erofs b "$part" || return 1
  log_done EROFS "${part}.img"
}

# Pack the patched ROM as one signed, password-locked payload.bin OTA zip
# (ota_out/): Mushrom installs it through update_engine, MushromTool flashes the
# same file over fastboot/fastbootd.
pack() {
  start_time=$(date +%s)

  source $dir/info
  patch=$(get_patch)

  build_ota_package || return 1
  # The full ROM is the product; a failed root-only package only warns.
  build_magisk_ota || log_warn PackOTA MagiskOTA "boot-only Magisk OTA not built"

  # Local builders keep img_temp so edits can be repacked without re-extracting.
  if is_local_builder; then
    log_event info PackOTA img_temp Keep "local builder"
  else
    sudo rm -rf $dir/img_temp
  fi
  sudo rm -rf $dir/zip_temp

  end_time=$(date +%s)
  elapsed=$((end_time - start_time))
  log_done PackOTA Runtime "$(format_duration "$elapsed")"
}

get_file_dir1() {
  if [[ $1 ]]; then
    if [[ $2 == "auto" ]]; then
      sudo find $dir/img_temp/ -name $1
    elif [[ $2 ]]; then
      sudo find $dir/$2/ -name $1 | head -1
    else
      sudo find $dir/img_temp/ -name $1 | head -1
    fi
  else
    return 0
  fi
}

stock_repack_name() {
  local stock_base="${base:-$iden}"
  local stock_android="${android:-$os}"
  local stock_suffix="${stock_hash:-$(date +%s | sha1sum | cut -c1-8)}"

  echo "${device^^}-ota_full-${stock_base}-user-${stock_android}-${stock_suffix}.zip"
}

# Pack the untouched stock images as an unlocked payload.bin OTA zip
# (ota_out/). The super layout inside is always slot a; the slot argument is
# only recorded in info.
pack_stock() {
  local requested_slot="${1:-}"
  local target_slot
  start_time=$(date +%s)

  source "$dir/info"
  target_slot="${requested_slot:-${stock_super_slot:-a}}"
  if [[ "$target_slot" != "a" && "$target_slot" != "b" ]]; then
    log_fail StockPack Slot "invalid value: $target_slot"
    return 1
  fi

  patch=$(get_patch)
  final_repack="$(stock_repack_name)"

  sed -i "s/^patch=.*/patch=$patch/" "$dir/info"
  if grep -q '^final_repack=' "$dir/info"; then
    sed -i "s|^final_repack=.*|final_repack=$final_repack|" "$dir/info"
  else
    echo "final_repack=$final_repack" >>"$dir/info"
  fi

  if grep -q '^is_mkstock=' "$dir/info"; then
    sed -i 's/^is_mkstock=.*/is_mkstock=true/' "$dir/info"
  else
    echo 'is_mkstock=true' >>"$dir/info"
  fi
  if grep -q '^stock_super_slot=' "$dir/info"; then
    sed -i "s/^stock_super_slot=.*/stock_super_slot=$target_slot/" "$dir/info"
  else
    echo "stock_super_slot=$target_slot" >>"$dir/info"
  fi

  # Backups are created before device_platform runs, so their initial
  # <device>.check file is empty. Generate all check files only after the
  # stock metadata and final archive name have been written to info.
  device_platform || return 1
  firmware_add || return 1

  # Same package format as a VS build (payload.bin + fastboot extras), left
  # unlocked: MushromTool flashes it without asking the server for a key.
  is_mkstock=true
  build_ota_package || return 1
  log_done StockPack Flashable "ota_out/$(basename "$ota_package")"
  release_packed_sources StockPack

  end_time=$(date +%s)
  elapsed=$((end_time - start_time))
  log_done StockPack Runtime "$(format_duration "$elapsed")"
}

Root() {
  local boot_dr
  local root_log
  local root_status
  local patched_boot

  log_start Root Magisk
  cd "$dir" || return 1

  boot_dr=$(get_file_dir1 "init_boot.img" "$device")
  if [[ ! -f "$boot_dr" ]]; then
    boot_dr=$(get_file_dir1 "boot.img" "$device")
  fi

  if [[ ! -f "$boot_dr" ]]; then
    log_fail Root Magisk "boot.img/init_boot.img not found"
    return 1
  fi

  mkdir -p "$dir/logs"
  root_log="$dir/logs/root_magisk.log"
  patched_boot="$(dirname "$boot_dr")/magisk.img"

  if ! cp -f "$boot_dr" "$dir/bin/magisk/"; then
    log_fail Root Magisk "copy failed: ${boot_dr#"$dir/"}"
    return 1
  fi

  cd "$dir/bin/magisk" || return 1
  rm -f new-boot.img

  # BOOTMODE=false: util_functions.sh otherwise probes `ps | grep zygote`,
  # which hangs the whole step when any host process wedges /proc.
  BOOTMODE=false ./root.sh "$(basename "$boot_dr")" >"$root_log" 2>&1
  root_status=$?
  if [[ $root_status -ne 0 ]]; then
    log_block Root Magisk "$root_log" "$root_status"
    cd "$dir"
    return "$root_status"
  fi

  if [[ ! -f new-boot.img ]]; then
    printf "%s\n" "new-boot.img was not created" >>"$root_log"
    log_block Root Magisk "$root_log" 1
    cd "$dir"
    return 1
  fi

  if mv -f new-boot.img "$patched_boot"; then
    log_done Root Magisk "${patched_boot#"$dir/"}"
  else
    log_fail Root Magisk "move failed: ${patched_boot#"$dir/"}"
    cd "$dir"
    return 1
  fi

  cd "$dir"
}

notification() {

  cd $dir

  # Local CI runs (act) must not post to the real Telegram channel.
  if [[ "${VS_NO_PUBLISH:-false}" == "true" ]]; then
    log_event warn Telegram "state=$2" Skip "VS_NO_PUBLISH=true"
    return 0
  fi

  [[ -f "$dir/info" ]] && source "$dir/info"
  base="${base:-$miui}"
  action_link=$1
  state=$2
  gitlog=$3
  runid=$4
  builder="${builder:-${BUILDER_NAME:-Local}}"
  link="${link:-${ROM_URL:-$action_link}}"
  base="${base:-${ROM_URL:-Unknown}}"
  patch="${patch:-$(get_patch)}"
  brand="${brand:-HyperOS}"
  device="${device:-${ROM_URL:-unknown}}"
  device="${device##*/}"
  device="${device%%-*}"
  device="${device,,}"
  local push_device="${device^^}"
  local is_stock="${is_mkstock:-false}"
  [[ "${BUILD_MODE:-}" == "stock" ]] && is_stock="true"
  local status=0

  if [[ "$action_link" == "local" ]]; then
    builder="Local"
    action_link="https://vsteam.vn"
  fi

  # ota_published: the package cmd_upload put in PAID_OTA, announced to the
  # devices on its OTA branch once the build is done.
  chatid=$(VS_OTA_PACKAGE="${ota_published:-}" $push "$builder" "$action_link" "$push_device" "$base" "$link" "$patch" "$state" "$gitlog" "$runid" "$is_stock" "$brand" 2>/dev/null)
  status=$?
  if [[ $status -ne 0 ]]; then
    log_fail Telegram "state=$state" "exit=$status"
    return "$status"
  fi

  if [[ -n "$chatid" ]]; then
    echo "chatid=$chatid" >$dir/chatid
  elif [[ $2 == "1" ]]; then
    log_fail Telegram "state=$state" "empty chatid"
    return 1
  fi

  log_event info Telegram "state=$state" "" "sent"

}

notification_t() {

  cd $dir

  source $dir/info
  base="${base:-$miui}"
  device="${device,,}"
  local push_device="${device^^}"
  action_link=$1
  state=$2
  gitlog=$3
  runid=$4
  local status=0

  chatid=$($pusht "$builder" "$action_link" "$push_device" "$base" "$link" "$patch" "${is_mkstock:-false}" "$state" "$gitlog" "$runid" "$dllink" 2>/dev/null)
  status=$?
  if [[ $status -ne 0 ]]; then
    log_fail Telegram "state=$state" "exit=$status"
    return "$status"
  fi

  if [[ -n "$chatid" ]]; then
    echo "chatid=$chatid" >$dir/chatid
  fi

  log_event info Telegram "state=$state" "" "sent"

}

notify_build_state() {
  local state="$1"
  local action_link="${NOTIFY_ACTION_LINK:-}"
  local gitlog="${NOTIFY_GITLOG:-}"
  local runid="${NOTIFY_RUN_ID:-}"

  if [[ -z "$action_link" && -n "$GITHUB_SERVER_URL" && -n "$GITHUB_REPOSITORY" && -n "$GITHUB_RUN_ID" ]]; then
    action_link="$GITHUB_SERVER_URL/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID"
  fi

  [[ -z "$action_link" ]] && action_link="local"
  [[ -z "$gitlog" ]] && gitlog="$(git log -1 --pretty=format:%s 2>/dev/null || echo local)"
  [[ -z "$runid" ]] && runid="${GITHUB_RUN_ID:-local-$(date +%s)}"

  notification "$action_link" "$state" "$gitlog" "$runid" || true
}

iroot() {
  local img part slot

  cd "$dir" || return 1

  Root || { log_fail Root Install "Root (magisk.img) failed"; return 1; }

  img="$(get_file_dir1 "magisk.img" "$device")"
  if [[ ! -f "$img" ]]; then
    log_fail Root Install "magisk.img not found"
    return 1
  fi

  # init_boot on newer devices, boot.img on older ones.
  if [[ -f "$(get_file_dir1 "init_boot.img" "$device")" ]]; then
    part="init_boot"
  elif [[ -f "$(get_file_dir1 "boot.img" "$device")" ]]; then
    part="boot"
  else
    log_fail Root Install "boot.img/init_boot.img not found"
    return 1
  fi

  # magisk.img is ready. With no device in fastboot, stop here (success) and
  # leave it for manual flashing instead of letting `fastboot flash` hang on
  # `< waiting for any device >`.
  if ! wait_for_fastboot_device 3; then
    log_done Root Install "${img#"$dir/"}"
    echo "====> Khong thay thiet bi fastboot - bo qua buoc flash."
    echo "      magisk.img da tao: ${img#"$dir/"}"
    echo "      Flash tay (chon dung slot _a/_b dang active):"
    echo "        sudo fastboot flash ${part} \"$img\""
    return 0
  fi

  # Flash the slot the device actually boots from; flashing _a while the
  # device runs on b leaves the booted slot stock (no root). Also make that
  # slot active so the patched image is what comes up after reboot.
  slot="$(detect_active_slot)"
  if [[ -n "$slot" ]]; then
    part="${part}_${slot}"
  fi

  echo "====> Install Root ($part)"

  if ! sudo fastboot flash "$part" "$img"; then
    log_fail Root Install "fastboot flash $part failed"
    return 1
  fi

  if [[ -n "$slot" ]] && ! sudo fastboot --set-active="$slot" 2>/dev/null; then
    sudo fastboot set_active "$slot" 2>/dev/null || true
  fi

  sudo fastboot reboot

  echo "Success"
}

update_var() {
  local var_name="$1"
  local new_value="$2"
  local file="$3"

  sed -i "s/^$var_name=.*/$var_name=$new_value/" "$file"
}

# Asks the server which ROM archive `download "$@"` would fetch, without
# fetching it: download() runs as usual up to download_file, which only records
# the archive. rom_source_id is the archive file name, unique per ROM version.
resolve_rom_source() {
  local rc=0

  rom_source_id="" rom_source_url="" rom_source_output=""
  rom_resolve_only=true
  download "$@" || rc=$?
  rom_resolve_only=false
  ((rc == 0)) && [[ -n "$rom_source_id" ]]
}

# Downloads the archive resolve_rom_source found, so a signed link is not
# requested from the server a second time.
download_resolved_rom() {
  download_file "$rom_source_url" "$rom_source_output"
}

# Value of a key in a backup's `source` file (source_id, target).
backup_source_field() {
  [[ -f "$1/source" ]] || return 0
  sed -n "s/^$2=//p" "$1/source" | head -n 1
}

# Backup that holds ROM archive $1 extracted, if there is one.
find_backup_by_source() {
  local id="$1" entry

  [[ -n "$id" ]] || return 0
  for entry in "$backup_root"/*/; do
    entry="${entry%/}"
    if [[ "$(backup_source_field "$entry" source_id)" == "$id" ]]; then
      printf '%s' "$entry"
      return 0
    fi
  done
  # Backups made before the source file existed (or by buildtest --backup on a
  # local archive) only name the archive in info; adopt the first match.
  for entry in "$backup_root"/*/; do
    entry="${entry%/}"
    [[ -f "$entry/source" || ! -f "$entry/info" ]] && continue
    if [[ "$(sed -n 's/^zipname=//p' "$entry/info" | head -n 1)" == "$id" ]]; then
      [[ -n "${rom_source_target:-}" ]] &&
        printf 'source_id=%s\ntarget=%s\n' "$id" "$rom_source_target" >"$entry/source" 2>/dev/null
      printf '%s' "$entry"
      return 0
    fi
  done
}

# Builds of the local farm share one backup store. A reader holds the lock
# shared while it copies a backup out; a writer holds it exclusive while it
# publishes a new backup and prunes old ones.
backup_lock() {
  mkdir -p "$backup_root" || return 1
  exec {backup_lock_fd}>>"$backup_root/.lock" || return 1
  flock "$1" "$backup_lock_fd"
}

backup_unlock() {
  exec {backup_lock_fd}>&-
}

remove_backup() {
  [[ "$1" == "$backup_root/"* && -d "$1" ]] || return 0
  log_event warn Backup "$(basename -- "$1")" Remove "$2"
  rm -rf -- "$1" 2>/dev/null || sudo rm -rf -- "$1"
}

# Keeps the store within $backup_max_gb. A backup made for the same target as
# $1 is an older ROM version and goes first; after that the least recently
# used backups go until the rest fits. $1 itself is never removed.
prune_backups() {
  local keep="$1" key entry size total limit_kb
  local -a others=()
  local -A sizes=()

  find "$backup_root" -mindepth 1 -maxdepth 1 -type d -name '.tmp-*' -mmin +1440 \
    -exec rm -rf -- {} + 2>/dev/null

  key="$(backup_source_field "$keep" target)"
  while IFS= read -r entry; do
    [[ -n "$entry" && "$entry" != "$keep" ]] || continue
    if [[ -n "$key" && "$(backup_source_field "$entry" target)" == "$key" ]]; then
      remove_backup "$entry" "replaced by $(basename -- "$keep")"
    else
      others+=("$entry")
    fi
  done < <(find "$backup_root" -mindepth 1 -maxdepth 1 -type d ! -name '.*' -printf '%T@ %p\n' |
    sort -n | cut -d' ' -f2-)

  limit_kb=$((backup_max_gb * 1024 * 1024))
  total="$(du -sk -- "$keep" 2>/dev/null | cut -f1)"
  total="${total:-0}"
  for entry in "${others[@]}"; do
    size="$(du -sk -- "$entry" 2>/dev/null | cut -f1)"
    sizes["$entry"]="${size:-0}"
    total=$((total + ${size:-0}))
  done
  # others is sorted oldest first; restoring a backup touches it.
  for entry in "${others[@]}"; do
    ((total > limit_kb)) || break
    remove_backup "$entry" "store over ${backup_max_gb}G"
    total=$((total - ${sizes["$entry"]}))
  done
  if ((total > limit_kb)); then
    log_warn Backup Store "$((total / 1024 / 1024))G used, over the ${backup_max_gb}G limit with one backup left"
  else
    log_event info Backup Store "" "$((total / 1024 / 1024))G of ${backup_max_gb}G used"
  fi
}

# Saves the freshly extracted ROM (img_temp, firmware images, info) so a later
# build of the same ROM version starts from it. The copy is built under a
# temporary name and renamed, so other builds never see a half-written backup.
backup() {
  cd $dir
  log_done Backup Extracted "$zipname"

  local name="${device}_${buildtime}" staging
  mkdir -p "$backup_root" || {
    log_fail Backup Create "cannot create $backup_root"
    return 1
  }
  [[ -e "$backup_root/$name" ]] && name="${name}_$(date +%H%M%S)"
  bkname="$backup_root/$name"
  staging="$backup_root/.tmp-$name-$$"
  log_start Backup Create "$bkname"
  mkdir -p "$staging"

  if [[ -f info ]]; then
    cp -rf info "$staging"
  else
    log_fail Backup Create "missing info file"
    rm -rf -- "$staging"
    return 1
  fi

  [[ -f "$ota_meta" ]] && cp -f "$ota_meta" "$staging/"

  # On btrfs/xfs the copies are reflinks: instant, and no extra disk until
  # the build changes a file.
  if [[ -d img_temp ]]; then
    log_start Backup img_temp "copy"
    cp -rf --reflink=auto img_temp "$staging"
    log_done Backup img_temp
  else
    log_warn Backup img_temp "not found"
  fi

  log_start Backup "$device" "copy"
  cp -rf --reflink=auto "$device" "$staging"
  log_done Backup "$device"

  # Only a ROM that was looked up on the server is tied to its version.
  if [[ -n "${rom_source_id:-}" ]]; then
    printf 'source_id=%s\ntarget=%s\n' "$rom_source_id" "${rom_source_target:-$device}" >"$staging/source"
  fi

  backup_lock -x || {
    log_fail Backup Create "cannot lock $backup_root"
    rm -rf -- "$staging"
    return 1
  }
  if ! mv -- "$staging" "$bkname"; then
    backup_unlock
    log_fail Backup Create "cannot publish $bkname"
    rm -rf -- "$staging"
    return 1
  fi
  log_done Backup Create "$bkname"
  prune_backups "$bkname"
  backup_unlock
}

restore() {
  cd "$dir" || exit

  if [[ ! -d "$backup_root" ]]; then
    log_fail Restore Backup "folder not found"
    return
  fi

  backups=($(ls -d "$backup_root"/*/ 2>/dev/null))
  if [[ ${#backups[@]} -eq 0 ]]; then
    log_fail Restore Backup "no backups available"
    return
  fi

  log_event info Restore Backup Select "use the up/down arrows"

  bkname=$(printf "%s\n" "${backups[@]}" | fzf --height=10 --reverse --border --prompt="Backup: ")

  if [[ -z "$bkname" ]]; then
    log_fail Restore Backup "no backup selected"
    return
  fi

  log_start Restore Backup "$bkname"

  # Source the info file if it exists
  if [[ -f "$bkname/info" ]]; then
    log_done Restore Info "$bkname/info"
    source "$bkname/info"
    device="${device,,}"
    cp -rf "$bkname/info" .
  fi

  rm -f "$ota_meta"
  [[ -f "$bkname/ota_meta.json" ]] && cp -f "$bkname/ota_meta.json" "$ota_meta"

  if [[ -d "$bkname/img_temp" ]]; then
    log_start Restore img_temp
    cp -rf "$bkname/img_temp" .
    log_done Restore img_temp
  fi

  if [[ -d "$bkname/$device" ]]; then
    log_start Restore "$device"
    cp -rf "$bkname/$device" .
    log_done Restore "$device"
  fi

  log_done Restore Backup "$bkname"
}

latest_backup_dir() {
  local target="${1,,}"
  local find_args=(-mindepth 1 -maxdepth 1 -type d)

  if [[ -n "$target" ]]; then
    find_args+=(-iname "${target}_*")
  fi

  find "$backup_root" "${find_args[@]}" -printf '%T@ %p\n' 2>/dev/null |
    sort -nr |
    awk 'NR==1 {sub(/^[^ ]+ /, ""); print}'
}

restore_latest_backup() {
  local target="${1,,}"
  local missing_level="${2:-fail}"
  local bkname

  cd "$dir" || return 1
  bkname="$(latest_backup_dir "$target")"
  if [[ -z "$bkname" ]]; then
    if [[ "$missing_level" == "warn" ]]; then
      log_warn Backup Restore "latest backup not found"
    else
      log_fail Backup Restore "latest backup not found"
    fi
    return 1
  fi

  restore_backup_dir "$bkname"
}

# Copies backup $1 into the workspace. Another build may prune the store at
# any time, so the backup is checked again once the lock is held.
restore_backup_dir() {
  local bkname="$1"
  local rc=0

  cd "$dir" || return 1
  backup_lock -s || {
    log_fail Backup Restore "cannot lock $backup_root"
    return 1
  }
  if [[ ! -f "$bkname/info" ]]; then
    backup_unlock
    log_fail Backup Restore "missing info"
    return 1
  fi

  log_done Restore Backup "$bkname"
  rm -rf "$dir/img_temp"
  cp -rf "$bkname/info" "$dir/info"
  source "$dir/info"
  device="${device,,}"

  rm -f "$ota_meta"
  [[ -f "$bkname/ota_meta.json" ]] && cp -f "$bkname/ota_meta.json" "$ota_meta"
  if [[ -d "$bkname/img_temp" ]]; then
    cp -rf --reflink=auto "$bkname/img_temp" "$dir/img_temp" || rc=1
  fi
  if [[ -d "$bkname/$device" ]]; then
    cp -rf --reflink=auto "$bkname/$device" "$dir/$device" || rc=1
  fi
  # Marks the backup as recently used for prune_backups.
  touch -- "$bkname" 2>/dev/null
  backup_unlock

  if ((rc != 0)); then
    log_fail Backup Restore "copy from $bkname failed"
    return 1
  fi
  if [[ ! -d "$dir/img_temp" && ! -d "$dir/$device/firmware-update" ]]; then
    log_fail Backup Restore "missing img_temp and firmware-update"
    return 1
  fi
}

# A .zip/.tgz target that is not an existing file is a typo or an already
# deleted archive: fail before clean() wipes the workspace or download()
# looks the file name up as a codename.
require_rom_target() {
  case "${1,,}" in
  http://* | https://*) return 0 ;;
  *.zip | *.tgz)
    [[ -f "$1" ]] && return 0
    log_fail "${2:-ROM}" Source "archive not found: $1"
    return 1
    ;;
  esac
}

# Absolute path of a local ROM archive target (.zip/.tgz), empty otherwise.
local_rom_path() {
  [[ -f "$1" ]] || return 0
  case "${1,,}" in
  *.zip | *.tgz) realpath -- "$1" ;;
  esac
}

# Workspace entry `clean` must keep so a local ROM target survives it.
local_rom_keep() {
  [[ -n "$1" && "$(dirname -- "$1")" == "$(realpath -- "$dir")" ]] && basename -- "$1"
  return 0
}

has_rom_archive() {
  compgen -G "$dir/*.zip" >/dev/null || compgen -G "$dir/*.tgz" >/dev/null
}

prepare_test_source() {
  local target="$1"
  local builder="$2"
  local mode="$3"

  cd "$dir" || return 1

  if has_rom_archive; then
    detect "$builder" "$target" || return 1
    if [[ "$mode" == "stock" ]]; then
      extract_stock_images || return 1
      device_platform || return 1
    else
      extract || return 1
    fi
  else
    if [[ -n "$target" && "$target" != http://* && "$target" != https://* && ! -f "$target" ]]; then
      if restore_latest_backup "$target" warn; then
        return 0
      fi
      log_warn Test Source "No backup for $target"
    fi

    if [[ -n "$target" ]]; then
      log_warn Test Source "No ROM archive, downloading $target"
      clean || return 1
      download "$target" || return 1
      detect "$builder" "$target" || return 1
      if [[ "$mode" == "stock" ]]; then
        extract_stock_images || return 1
        device_platform || return 1
      else
        extract || return 1
      fi
    else
      log_warn Test Source "No ROM archive, using latest backup"
      restore_latest_backup || return 1
    fi
  fi
}

clean() {
  cd "$dir" || exit

  local mode="$1"
  local dry_run=false
  local remove_backup=false
  local item
  local keep_items=(
    ".git"
    ".github"
    ".gitignore"
    ".android-sdk"
    # CI points TMPDIR/GRADLE_USER_HOME/XDG_CACHE_HOME here (off the root disk).
    ".tmp"
    ".gradle"
    ".cache"
    "README.md"
    "bhlnk"
    "bin"
    "backend"
    "fastboot"
    "module"
    "backup"
  )

  while [[ $# -gt 0 ]]; do
    case "$1" in
    -n | --dry-run)
      dry_run=true
      ;;
    --all)
      remove_backup=true
      ;;
    --keep)
      shift
      [[ -n "${1:-}" ]] && keep_items+=("$1")
      ;;
    "") ;;
    *)
      echo "Unknown clean option: $1"
      echo "Usage: clean [--dry-run] [--all] [--keep <name>]"
      return 1
      ;;
    esac
    shift
  done

  if [[ "$remove_backup" == "true" ]]; then
    keep_items=("${keep_items[@]/backup/}")
  fi

  should_keep_clean_item() {
    local target="$1"
    local keep
    for keep in "${keep_items[@]}"; do
      [[ "$target" == "$keep" ]] && return 0
    done
    return 1
  }

  clean_remove() {
    local target="$1"
    [[ -e "$target" || -L "$target" ]] || return 0

    if should_keep_clean_item "$target"; then
      return 0
    fi

    echo -e "${RED}Removing:${RESET} $target"
    if [[ "$dry_run" == "false" ]]; then
      rm -rf -- "$target" 2>/dev/null || sudo rm -rf -- "$target"
    fi
  }

  log_start Clean Workspace "next build session"
  [[ "$dry_run" == "true" ]] && log_warn Clean Workspace "Dry run only; no files will be removed"

  shopt -s nullglob dotglob
  for item in *; do
    [[ "$item" == "." || "$item" == ".." ]] && continue
    clean_remove "$item"
  done
  shopt -u nullglob dotglob

  log_done Clean Workspace
}

build_super_partition_lists() {
  sum=0
  results_1=""
  results_2=""

  for i in $super_part; do
    if [[ -f "$i.img" ]]; then
      size=$(stat -c%s "$i.img")

      # KHÔNG cộng buffer từng partition
      sum=$((sum + size))

      temp_a="--partition ${i}_a:readonly:$size:qti_dynamic_partitions_a --image ${i}_a=$i.img"
      temp_b="--partition ${i}_b:readonly:0:qti_dynamic_partitions_b"
    else
      temp_a="--partition ${i}_a:readonly:0:qti_dynamic_partitions_a"
      temp_b="--partition ${i}_b:readonly:0:qti_dynamic_partitions_b"
    fi

    results_1+="$temp_a "
    results_2+="$temp_b "
  done

  if [[ -n "$device_size" ]]; then
    part_a="$device_size $results_1"
    part_b="$device_size $results_2"
    super_lpadd_device_size="$device_size"
    super_lpadd_group_size="$device_size"
    if [[ -n "${VS_SUPER_SIZE:-}" ]]; then
      # Forced size: the group is the super minus the OEM reserve, the same
      # relation capture_payload_super_size reads off a stock payload.
      super_lpadd_group_size=$((device_size - $(super_reserve_mib) * 1024 * 1024))
      part_a="$super_lpadd_group_size $results_1"
      part_b="$super_lpadd_group_size $results_2"
    fi
  else
    # chỉ cộng buffer 1 lần cho group
    buffer=$((5 * 1024 * 1024))
    group_size=$((sum + buffer))

    part_a="$group_size $results_1"
    part_b="$group_size $results_2"
    super_lpadd_device_size=$((group_size + 16 * 1024 * 1024))
    super_lpadd_group_size="$group_size"
  fi
}

# Print the super layout about to be passed to lpmake: where the size came
# from, device/group size, every partition image and the space left.
print_super_size_info() {
  local size="${super_lpadd_device_size:-${device_size:-}}"
  local group="${super_lpadd_group_size:-${device_size:-}}"
  local source="info (payload_super_size)"
  # lpmake reserves 4 KiB + 2 geometry copies + primary/backup metadata
  # (--metadata-size 65536 x --metadata-slots 3) at the start of super.
  local metadata=$((4096 + 2 * 4096 + 2 * 3 * 65536))
  local total=0 part part_size free

  size_mib() { awk -v b="$1" 'BEGIN { printf "%s (%.2f MiB)", b, b / 1048576 }'; }

  [[ -n "$device_size" ]] || source="calculated (no payload_super_size in info)"
  [[ -z "${VS_SUPER_SIZE:-}" ]] || source="VS_SUPER_SIZE"

  log_start SuperSize Info "${device^^}"
  log_event info "  Source" "" "" "$source"
  log_event info "  Super size" "" "" "$(size_mib "$size")"
  log_event info "  Group size" "" "" "$(size_mib "$group")"
  for part in $super_part; do
    [[ -f "${part}.img" ]] || continue
    part_size=$(stat -c%s "${part}.img")
    total=$((total + part_size))
    log_event info "  ${part}.img" "" "" "$(size_mib "$part_size")"
  done
  log_event info "  Partitions total" "" "" "$(size_mib "$total")"
  if [[ "$size" =~ ^[0-9]+$ ]]; then
    free=$((size - metadata - total))
    log_event info "  Free" "" "" "$(size_mib "$free")"
    if ((free < 0)); then
      log_warn SuperSize "${device^^}" "partitions exceed the super size by $(size_mib $((-free)))"
    fi
  fi
  log_done SuperSize Info
}

run_lpmake_super() {
  local output_file="$1"
  local target_slot="${2:-${super_target_slot:-a}}"
  local raw_file="${output_file}.raw"
  local seed_img="${output_file}.seed"
  local log_file="${output_file}.log"
  local target_group="qti_dynamic_partitions_${target_slot}"
  local other_slot other_group
  local size_arg="${super_lpadd_device_size:-${device_size:-auto}}"
  local group_size="${super_lpadd_group_size:-${device_size:-0}}"
  local img2simg_bin=""
  local part
  local part_size
  local -a layout_args=()

  if [[ "$target_slot" != "a" && "$target_slot" != "b" ]]; then
    log_fail Super Slot "invalid target slot: $target_slot"
    return 1
  fi

  img2simg_bin="$(command -v img2simg 2>/dev/null || true)"
  if [[ -z "$img2simg_bin" ]]; then
    log_fail Super img2simg "not found"
    return 1
  fi

  rm -f "$output_file" "$raw_file" "$seed_img" "$log_file"

  # Mirror the stock layout: every dynamic partition exists in both slots.
  # The other slot and target-slot partitions without an image are declared
  # zero-sized here; lpadd appends the real images below. No placeholder
  # partition is left in the metadata, and --force-full-image writes a holey
  # raw file so the unused space stays DONT_CARE in the sparse output instead
  # of gigabytes of zero FILL chunks the bootloader has to write.
  [[ "$target_slot" == "a" ]] && other_slot="b" || other_slot="a"
  other_group="qti_dynamic_partitions_${other_slot}"
  for part in $super_part; do
    layout_args+=(--partition "${part}_${other_slot}:readonly:0:$other_group")
    [[ -f "${part}.img" ]] ||
      layout_args+=(--partition "${part}_${target_slot}:readonly:0:$target_group")
  done

  log_start Super Create "$size_arg"
  if ! $dir/bin/lpmake --metadata-size 65536 --super-name super --metadata-slots 3 \
    --device-size "$size_arg" \
    --group qti_dynamic_partitions_a:$group_size \
    --group qti_dynamic_partitions_b:$group_size \
    "${layout_args[@]}" \
    --force-full-image \
    --output "$raw_file" >"$log_file" 2>&1; then
    cat "$log_file"
    rm -f "$raw_file" "$seed_img" "$log_file"
    return 1
  fi

  for part in $super_part; do
    [[ -f "${part}.img" ]] || continue

    part_size=$(stat -c%s "${part}.img")
    log_event info Super "${part}_${target_slot}" Add "$part_size"
    if ! $dir/bin/lpadd --readonly "$raw_file" "${part}_${target_slot}" "$target_group" "${part}.img" >"$log_file" 2>&1; then
      cat "$log_file"
      rm -f "$raw_file" "$seed_img" "$log_file"
      return 1
    fi

    # lpadd has copied this partition into the raw super image. On constrained
    # CI runners, release the standalone copy immediately instead of keeping
    # raw super + all partition images until img2simg finishes.
    if [[ "${CI_LOW_DISK:-false}" == "true" ]]; then
      rm -f -- "${part}.img" || {
        log_fail Super "${part}.img" "cannot release image after lpadd"
        rm -f "$raw_file" "$seed_img" "$log_file"
        return 1
      }
      log_event info Super "${part}.img" Clean "released after lpadd"
    fi
  done

  log_start Super Sparse "$output_file"
  if ! "$img2simg_bin" -s "$raw_file" "$output_file" >"$log_file" 2>&1; then
    cat "$log_file"
    rm -f "$output_file" "$raw_file" "$seed_img" "$log_file"
    return 1
  fi

  # Android sparse images start with the little-endian magic 0xed26ff3a.
  # Do not let buildtest/supertest continue with a raw super image.
  if [[ "$(od -An -tx1 -N4 "$output_file" 2>/dev/null | tr -d '[:space:]')" != "3aff26ed" ]]; then
    log_fail Super "$output_file" "output is not a sparse image"
    rm -f "$output_file" "$raw_file" "$seed_img" "$log_file"
    return 1
  fi

  rm -f "$raw_file" "$seed_img" "$log_file"
  log_done Super "$output_file"
}

# Repack an already-built super.img so its real partition data lives in the
# opposite (or requested) slot, without needing the original per-partition
# source images. Unpacks the given super.img with lpunpack, keeps whichever
# per-partition file actually has real data, then rebuilds with lpmake/lpadd
# targeting the requested slot via run_lpmake_super.
reslot() (
  local input=""
  local slot=""
  local output=""
  local input_abs=""
  local output_abs=""
  local workdir=""
  local raw_img=""
  local extract_dir=""
  local log_file=""
  local dump=""
  local device_size=""
  local group_size=""
  local base=""
  local f=""
  local sz=""
  local force=false
  local arg
  local -a positional=()
  local -A src_file=()
  local -A src_size=()

  while (($# > 0)); do
    arg="$1"
    case "$arg" in
    --force | -f)
      force=true
      ;;
    --*)
      echo "Unknown reslot option: $arg"
      return 1
      ;;
    *)
      positional+=("$arg")
      ;;
    esac
    shift
  done

  if ((${#positional[@]} < 2 || ${#positional[@]} > 3)); then
    echo "Usage: bhlnk reslot <super.img> <a|b> [output.img] [--force]"
    return 1
  fi

  input="${positional[0]}"
  slot="${positional[1],,}"
  if [[ "$slot" != "a" && "$slot" != "b" ]]; then
    log_fail Reslot Slot "invalid slot: $slot (expected a or b)"
    return 1
  fi
  if [[ ! -f "$input" ]]; then
    log_fail Reslot Input "not found: $input"
    return 1
  fi
  for f in "$dir/bin/lpunpack" "$dir/bin/lpmake" "$dir/bin/lpadd" "$dir/bin/lpdump"; do
    if [[ ! -x "$f" ]]; then
      log_fail Reslot Tool "missing or not executable: $f"
      return 1
    fi
  done
  if ! command -v img2simg >/dev/null 2>&1; then
    log_fail Reslot img2simg "not found"
    return 1
  fi

  input_abs="$(readlink -f -- "$input")" || return 1
  if ((${#positional[@]} == 3)); then
    output="${positional[2]}"
  else
    output="$(dirname -- "$input_abs")/$(basename -- "${input_abs%.img}")_${slot}.img"
  fi
  output_abs="$(readlink -m -- "$output")" || return 1
  # Same path is allowed (in-place patch) but, like overwriting any existing
  # output, requires --force since it mutates the original irreversibly.
  if [[ -e "$output_abs" && "$force" != "true" ]]; then
    log_fail Reslot "$output_abs" "output exists; pass --force to replace it"
    return 1
  fi
  if [[ ! -d "$(dirname -- "$output_abs")" ]]; then
    log_fail Reslot Output "directory does not exist: $(dirname -- "$output_abs")"
    return 1
  fi

  # Fast path: patch only the liblp metadata copies in place (a few KB,
  # regardless of image size) instead of unpacking and rebuilding every
  # partition. Falls through to the slow path below when it doesn't apply
  # (unsupported metadata layout, non-RAW-backed sparse chunks, etc).
  local reslot_helper="$dir/bin/lp_reslot.py"
  if [[ -f "$reslot_helper" ]] && command -v python3 >/dev/null 2>&1; then
    local fast_log fast_rc
    fast_log="$(mktemp "${TMPDIR:-/tmp}"/vs-reslot-fast.XXXXXX)" || return 1
    if [[ "$input_abs" != "$output_abs" ]]; then
      log_start Reslot Copy "$(basename -- "$output_abs")"
      if ! cp --reflink=auto -- "$input_abs" "$output_abs" 2>"$fast_log"; then
        cat "$fast_log"
        rm -f -- "$fast_log"
        log_fail Reslot Copy "cannot create $output_abs"
        return 1
      fi
      log_done Reslot Copy "$output"
    fi

    log_start Reslot Patch "metadata-only, slot $slot"
    python3 "$reslot_helper" "$output_abs" "$slot" >"$fast_log" 2>&1
    fast_rc=$?
    cat "$fast_log"
    rm -f -- "$fast_log"

    if ((fast_rc == 0)); then
      log_done Reslot "$output" "fast in-place metadata patch (no partition data touched)"
      return 0
    fi
    if ((fast_rc != 2)); then
      log_fail Reslot Patch "fast path error"
      return 1
    fi
    log_warn Reslot Patch "fast path not applicable; falling back to unpack+rebuild"
    [[ "$input_abs" != "$output_abs" ]] && rm -f -- "$output_abs"
  fi

  workdir="$(mktemp -d "$dir/.vs-reslot.XXXXXX")" || {
    log_fail Reslot Workspace "cannot create temporary directory"
    return 1
  }
  trap 'rm -rf -- "$workdir"' EXIT
  raw_img="$workdir/raw.img"
  extract_dir="$workdir/extracted"
  log_file="$workdir/reslot.log"
  mkdir -p "$extract_dir"

  log_start Reslot Unsparse "$(basename -- "$input_abs")"
  if [[ "$(od -An -tx1 -N4 -- "$input_abs" 2>/dev/null | tr -d '[:space:]')" == "3aff26ed" ]]; then
    if ! simg2img "$input_abs" "$raw_img" 2>"$log_file"; then
      cat "$log_file"
      log_fail Reslot Unsparse "simg2img failed"
      return 1
    fi
  else
    cp -- "$input_abs" "$raw_img" || {
      log_fail Reslot Unsparse "cannot copy $input_abs"
      return 1
    }
  fi

  dump="$("$dir/bin/lpdump" "$raw_img" 2>/dev/null)"
  if [[ -z "$dump" ]]; then
    log_fail Reslot Metadata "cannot read metadata from $input"
    return 1
  fi
  device_size="$(awk '/^Block device table:/{f=1;next} f&&/Size:/{print $2;exit}' <<<"$dump")"
  group_size="$(awk '/Name: qti_dynamic_partitions_a/{getline; print $3; exit}' <<<"$dump")"
  if [[ -z "$group_size" || "$group_size" == "0" ]]; then
    group_size="$(awk '/Name: qti_dynamic_partitions_b/{getline; print $3; exit}' <<<"$dump")"
  fi
  if [[ ! "$device_size" =~ ^[1-9][0-9]*$ || ! "$group_size" =~ ^[1-9][0-9]*$ ]]; then
    log_fail Reslot Metadata "cannot determine device/group size"
    return 1
  fi

  log_start Reslot Unpack "$(basename -- "$input_abs")"
  if ! "$dir/bin/lpunpack" "$raw_img" "$extract_dir" >"$log_file" 2>&1; then
    cat "$log_file"
    log_fail Reslot Unpack "lpunpack failed"
    return 1
  fi

  # Keep only the larger (real) copy per partition when both slots were
  # present in the source image; drop the zero-sized/placeholder duplicate.
  for f in "$extract_dir"/*.img; do
    [[ -f "$f" ]] || continue
    case "$(basename -- "$f" .img)" in
    *_a)
      base="$(basename -- "$f" .img)"
      base="${base%_a}"
      ;;
    *_b)
      base="$(basename -- "$f" .img)"
      base="${base%_b}"
      ;;
    *) continue ;;
    esac
    [[ "$base" == "vst_placeholder" ]] && continue
    sz="$(stat -c%s -- "$f")"
    if [[ -z "${src_size[$base]:-}" ]] || ((sz > src_size[$base])); then
      [[ -n "${src_file[$base]:-}" ]] && rm -f -- "${src_file[$base]}"
      src_size["$base"]="$sz"
      src_file["$base"]="$f"
    else
      rm -f -- "$f"
    fi
  done

  if ((${#src_file[@]} == 0)); then
    log_fail Reslot Partitions "no real partition data found in $input"
    return 1
  fi

  for base in "${!src_file[@]}"; do
    mv -- "${src_file[$base]}" "$extract_dir/$base.img"
  done

  cd "$extract_dir" || return 1
  super_part="${!src_file[*]}"
  super_lpadd_device_size="$device_size"
  super_lpadd_group_size="$group_size"
  if ! run_lpmake_super "$output_abs" "$slot"; then
    log_fail Reslot "$output" "build failed"
    return 1
  fi

  log_done Reslot "$output" "slot $slot; ${#src_file[@]} partition(s)"
)

cmd_reslot() {
  if [[ $# -eq 0 ]]; then
    echo "Usage: bhlnk reslot <super.img> <a|b> [output.img] [--force]"
    return 1
  fi

  reslot "$@"
}


if [[ -f $dir/info ]]; then
  source $dir/info
  base="${base:-$miui}"
  device="${device,,}"
fi

# Root of an upload destination: a configured rclone remote ("GoogleDrive" ->
# "GoogleDrive:") or a directory on this machine ("/data" -> "/data"), such as
# the OTA store of the build server (VS_OTA_REMOTE, see cmd_upload).
rclone_root() {
  if [[ "$1" == /* ]]; then
    printf '%s' "${1%/}"
  else
    printf '%s:' "$1"
  fi
}

prepare_rclone_upload() {
  local upload_type="$1"
  local upload_log="$2"
  local remote_name="${3:-OneDrive}"
  local remotes

  if [[ ! -x "$rclone" ]]; then
    log_fail Upload "$upload_type" "missing rclone binary: $rclone"
    return 1
  fi

  export RCLONE_CONFIG="${RCLONE_CONFIG:-$dir/bin/rclone.conf}"
  export RCLONE_CONFIG_PASS="${RCLONE_CONFIG_PASS:-SHARE_REDACTED_VALUE}"

  # A local directory needs no remote in the rclone config. Only its presence is
  # checked: the build user may own PAID_OTA/ inside it without owning the
  # root, and rclone reports a folder it cannot write.
  if [[ "$remote_name" == /* ]]; then
    if [[ ! -d "$remote_name" ]]; then
      log_fail Upload "$upload_type" "local store is missing: $remote_name"
      return 1
    fi
    return 0
  fi

  if [[ ! -f "$RCLONE_CONFIG" ]]; then
    log_fail Upload "$upload_type" "missing rclone config: $RCLONE_CONFIG"
    return 1
  fi

  if ! remotes="$("$rclone" listremotes 2>>"$upload_log")"; then
    log_fail Upload "$upload_type" "cannot read rclone config"
    return 1
  fi
  if ! grep -qx "${remote_name}:" <<<"$remotes"; then
    log_fail Upload "$upload_type" "$remote_name remote not found in rclone config"
    return 1
  fi
}

upload_via_rclone() {
  local upload_file="$1"
  local remote_file="$2"
  local upload_type="$3"
  local upload_log="$4"
  local remote_name="${5:-OneDrive}"
  local status
  local metadata
  local remote_size
  local local_size

  mkdir -p "$(dirname "$upload_log")"
  : >"$upload_log"
  prepare_rclone_upload "$upload_type" "$upload_log" "$remote_name" || return 1

  log_start Upload "$upload_type" "$remote_file"
  # A local store on the same btrfs as the workspace (the farm's /data) takes
  # a reflink: no data is copied, so the upload costs the disk nothing. Any
  # failure (another filesystem, no reflink support) falls back to rclone.
  if [[ "$remote_file" == /* ]] && mkdir -p "$(dirname "$remote_file")" 2>>"$upload_log" &&
    cp --reflink=always --preserve=timestamps -- "$upload_file" "$remote_file.part" 2>>"$upload_log" &&
    mv -f -- "$remote_file.part" "$remote_file" 2>>"$upload_log"; then
    echo "reflinked $upload_file -> $remote_file" >>"$upload_log"
    status=0
  else
    [[ "$remote_file" == /* ]] && rm -f -- "$remote_file.part"
    # Drive keeps the last chunk open while it finalizes a multi-GB file; with
    # the default 5m IO timeout rclone gave up there and re-sent the whole file.
    "$rclone" copyto "$upload_file" "$remote_file" \
      --no-traverse \
      --timeout 30m \
      --drive-chunk-size 128M \
      --retries 5 \
      --low-level-retries 20 \
      --retries-sleep 10s \
      --stats 30s \
      --stats-one-line-date \
      --log-level DEBUG \
      --stats-log-level NOTICE >>"$upload_log" 2>&1
    status=$?
  fi
  if [[ $status -ne 0 ]]; then
    log_block Upload "$upload_type" "$upload_log" "$status"
    return "$status"
  fi

  if ! metadata="$("$rclone" lsf "$remote_file" --files-only --format sp --separator '|' 2>>"$upload_log")"; then
    log_fail Upload "$upload_type" "uploaded, but remote verification failed"
    return 1
  fi

  local_size="$(stat -c %s "$upload_file")"
  remote_size="${metadata%%|*}"
  if [[ ! "$remote_size" =~ ^[0-9]+$ || "$remote_size" != "$local_size" ]]; then
    log_fail Upload "$upload_type" "size mismatch: local=$local_size remote=${remote_size:-unknown}"
    return 1
  fi

  log_done Upload "$upload_type" "$(basename "$upload_file") verified"
  return 0
}

cmd_upload_test() {
  [[ -f "$dir/info" ]] && source "$dir/info"
  local upload_file="ota_out/$final_repack"
  local filename
  local remote_file
  local upload_log="$dir/logs/upload_test.log"
  local link
  # Every upload goes to GoogleDrive; --onedrive still opts into OneDrive.
  local remote_name="GoogleDrive" fallback_name=""
  local arg

  for arg in "$@"; do
    case "$arg" in
    --gdrive | --drive | --googledrive)
      remote_name="GoogleDrive"
      fallback_name=""
      ;;
    --onedrive)
      remote_name="OneDrive"
      fallback_name="GoogleDrive"
      ;;
    esac
  done

  [[ "$upload_file" != /* ]] && upload_file="$dir/$upload_file"
  filename=$(basename "$upload_file")

  if [[ ! -f "$upload_file" ]]; then
    log_fail Upload "$filename" "file not found"
    return 1
  fi

  local type="PAID"

  [[ ${is_custom:-false} == true ]] && type="CUSTOM"
  [[ ${is_mkstock:-false} == true ]] && type="STOCK"
  remote_file="${remote_name}:/TESTBUILD/$filename"

  if ! upload_via_rclone "$upload_file" "$remote_file" "$type" "$upload_log" "$remote_name"; then
    if [[ -z "$fallback_name" ]]; then
      return 1
    fi
    log_warn Upload "$type" "$remote_name failed, falling back to $fallback_name"
    remote_name="$fallback_name"
    remote_file="${remote_name}:/TESTBUILD/$filename"
    upload_via_rclone "$upload_file" "$remote_file" "$type" "$upload_log" "$remote_name" || return 1
  fi

  if ! link="$("$rclone" link "$remote_file" 2>>"$upload_log")" || [[ -z "$link" ]]; then
    log_fail Upload "$type" "uploaded, but download link creation failed"
    return 1
  fi

  if [[ -f "$dir/info" ]]; then
    sed -i '/^dllink=/d' "$dir/info"
    printf 'dllink=%q\n' "$link" >>"$dir/info"
  fi
  dllink="$link"
  log_done UploadLink "$type" "$filename"
}

# The branch of a ROM package, from its name: "..._8.01.PRE.zip" is PRE,
# everything else ST.
ota_package_branch() {
  [[ "$1" == *.PRE.* ]] && echo PRE || echo ST
}

# The folder of a ROM package below PAID_OTA: ST packages are kept in STABLE,
# PRE packages in PREVIEW.
ota_package_folder() {
  [[ "$(ota_package_branch "$1")" == PRE ]] && echo PREVIEW || echo STABLE
}

# A device keeps one ROM package per branch on the remote, the newest ST (in
# STABLE) and the newest PRE (in PREVIEW): once the new upload is verified,
# every other *.zip of its branch in the device folder is removed. Only called after upload_via_rclone
# succeeded, so a failed upload never touches the previous package. Cleanup
# errors are warnings; the new build is already up. On the build server old
# PRE packages are also removed by age (farm.sh, VS_OTA_PRE_HOURS).
prune_remote_ota() {
  local remote_dir="$1" keep="$2" upload_log="$3"
  local listing name removed=0 failed=0
  local branch
  branch="$(ota_package_branch "$keep")"

  if ! listing="$("$rclone" lsf "$remote_dir" --files-only 2>>"$upload_log")"; then
    log_warn Upload "PAID_OTA" "cannot list $remote_dir; old OTA packages kept"
    return 0
  fi
  # The new package must be visible before anything else is deleted.
  if ! grep -qxF -- "$keep" <<<"$listing"; then
    log_warn Upload "PAID_OTA" "$keep not listed in $remote_dir; old OTA packages kept"
    return 0
  fi

  while IFS= read -r name; do
    [[ -n "$name" && "$name" != "$keep" && "$name" == *.zip ]] || continue
    [[ "$(ota_package_branch "$name")" == "$branch" ]] || continue
    if "$rclone" deletefile "$remote_dir$name" >>"$upload_log" 2>&1; then
      removed=$((removed + 1))
      log_event info Upload "PAID_OTA" Clean "removed old $name"
    else
      failed=$((failed + 1))
      log_warn Upload "PAID_OTA" "cannot remove old $name"
    fi
  done <<<"$listing"

  ((removed + failed == 0)) || log_done Upload "PAID_OTA" "old packages removed: $removed, failed: $failed"
  return 0
}

# Google Drive remotes a package goes to, all at the same time. A drive that
# refuses it (full, over its daily upload, not in the rclone config) is
# skipped: the upload succeeds when at least one drive holds the verified
# package. The API serves a package from whichever drive has it.
VS_DRIVE_REMOTES="${VS_DRIVE_REMOTES:-GoogleDrive gdrive3}"

upload_to_drives() {
  local upload_file="$1" rel_dir="$2" type="$3" upload_log="$4"
  local filename remote i ok=0
  local pids=() remotes=()
  filename="$(basename "$upload_file")"

  for remote in $VS_DRIVE_REMOTES; do
    upload_via_rclone "$upload_file" "$remote:/$rel_dir$filename" "$type" "${upload_log%.log}_$remote.log" "$remote" &
    pids+=("$!")
    remotes+=("$remote")
  done

  for i in "${!pids[@]}"; do
    remote="${remotes[$i]}"
    if wait "${pids[$i]}"; then
      ok=$((ok + 1))
      [[ $type == "PAID_OTA" ]] && prune_remote_ota "$remote:/$rel_dir" "$filename" "${upload_log%.log}_$remote.log"
    else
      log_warn Upload "$type" "$remote failed, skipped"
    fi
  done

  if ((ok == 0)); then
    log_fail Upload "$type" "no drive took $filename ($VS_DRIVE_REMOTES)"
    return 1
  fi
  log_done Upload "$type" "$filename on $ok of ${#remotes[@]} drives"
}

cmd_upload() {
  [[ -f "$dir/info" ]] && source "$dir/info"

  # UPLOAD_FILE lets build/stock publish the package from ota_out/.
  # Packages are written to ota_out/ under the name recorded in info.
  local upload_file="${UPLOAD_FILE:-ota_out/$final_repack}"
  local filename
  local rel_dir
  local remote_dir
  local remote_file
  local upload_log
  local type="PAID"
  local branch_dir="STABLE"
  # Every upload goes to GoogleDrive; --onedrive still opts into OneDrive.
  local remote_name="GoogleDrive" fallback_name=""
  local arg

  [[ "$upload_file" != /* ]] && upload_file="$dir/$upload_file"

  if [[ ! -f "$upload_file" ]]; then
    log_fail Upload "$(basename "$upload_file")" "file not found"
    return 1
  fi

  if [[ "${VS_NO_PUBLISH:-false}" == "true" ]]; then
    log_event warn Upload "$(basename "$upload_file")" Skip "VS_NO_PUBLISH=true ($(du -h "$upload_file" | cut -f1))"
    return 0
  fi

  for arg in "$@"; do
    case "$arg" in
    --gdrive | --drive | --googledrive)
      remote_name="GoogleDrive"
      fallback_name=""
      ;;
    --onedrive)
      remote_name="OneDrive"
      fallback_name="GoogleDrive"
      ;;
    *) [[ -n "$arg" ]] && type="CUSTOM" ;;
    esac
  done

  [[ ${is_custom:-false} == true ]] && type="CUSTOM"
  [[ ${is_mkstock:-false} == true ]] && type="STOCK"
  # VS ROM packages go to their own folder next to PAID.
  [[ ${isOTA:-false} == true && $type == "PAID" ]] && type="PAID_OTA"
  # The build server keeps OTA and stock packages on its own disk and the API
  # serves them from there: VS_OTA_REMOTE=/data. GoogleDrive stays the fallback.
  # Stock packages land in COLOROS/<device>/ like on the drives and are not
  # pruned: several versions and regions of one device are kept side by side.
  if [[ ($type == "PAID_OTA" || $type == "STOCK") && -n "${VS_OTA_REMOTE:-}" ]]; then
    remote_name="${VS_OTA_REMOTE%:}"
    fallback_name="GoogleDrive"
    [[ "$remote_name" == "$fallback_name" ]] && fallback_name=""
  fi

  if [[ -z "${device:-}" ]]; then
    log_fail Upload "$type" "missing device in info"
    return 1
  fi

  filename=$(basename "$upload_file")
  upload_log="$dir/logs/upload_${device}_${type,,}.log"
  [[ $type == "PAID_OTA" ]] && branch_dir="$(ota_package_folder "$filename")"

  if [[ ${is_mkstock:-false} == true ]]; then
    rel_dir="COLOROS/$device/"
  else
    rel_dir="$type/$branch_dir/$device/"
  fi

  if [[ "$remote_name" == "GoogleDrive" ]]; then
    upload_to_drives "$upload_file" "$rel_dir" "$type" "$upload_log"
    return
  fi

  remote_dir="$(rclone_root "$remote_name")/$rel_dir"
  remote_file="$remote_dir$filename"

  if upload_via_rclone "$upload_file" "$remote_file" "$type" "$upload_log" "$remote_name"; then
    [[ $type == "PAID_OTA" ]] && prune_remote_ota "$remote_dir" "$filename" "$upload_log"
    return 0
  fi

  if [[ -z "$fallback_name" ]]; then
    return 1
  fi

  log_warn Upload "$type" "$remote_name failed, falling back to Google Drive"
  upload_to_drives "$upload_file" "$rel_dir" "$type" "$upload_log"
}

cmd_sync() {
  cd "$dir" || return 1

  [[ -f "$dir/info" ]] && source "$dir/info"

  mkdir -p "$dir/module/update"

  if [[ ! -f "$dir/bin/rclone1" ]]; then
    wget https://github.com/buihien224/toolbox_notification/releases/download/rclone/rclone1 -O $dir/bin/rclone1
    chmod +x $dir/bin/rclone1
  fi

  export RCLONE_CONFIG="${RCLONE_CONFIG:-$dir/bin/rclone.conf}"
  export RCLONE_CONFIG_PASS="${RCLONE_CONFIG_PASS:-SHARE_REDACTED_VALUE}"

  if [[ ! -f "$RCLONE_CONFIG" && ! -d "${VS_MODULE_DIR:-}" ]]; then
    echo "Missing rclone config: $RCLONE_CONFIG"
    return 1
  fi

  if [[ -z "$brand" || -z "$os" || -z "$device" || -z "$isType" ]]; then
    echo "Missing brand/os/device/isType. Run detect first or keep info file."
    exit 1
  fi

  # With a local module store (VS_MODULE_DIR) the store alone says which
  # modules exist and Google Drive is never asked. Without one the remote
  # folder is listed, once.
  local remote_dirs="" remote_listed=false

  remote_has() {
    if [[ -d "${VS_MODULE_DIR:-}" ]]; then
      module_store_has "$1"
      return
    fi
    if [[ $remote_listed == false ]]; then
      remote_listed=true
      remote_dirs="$($rclone lsd teamdrive:VS_BackEnd 2>/dev/null | awk '{print $NF}')"
      [[ -z "$remote_dirs" ]] && echo "Cannot list remote folder: teamdrive:VS_BackEnd"
    fi
    grep -qx "$1" <<<"$remote_dirs"
  }

  sync_if_exists() {
    if remote_has "$1"; then
      sync "$1"
    elif [[ -d "${VS_MODULE_DIR:-}" ]]; then
      log_event warn Sync "$1" Skip "not in the module store: $VS_MODULE_DIR/$1"
    else
      log_event warn Sync "$1" Skip "missing remote folder: VS_BackEnd/$1"
    fi
  }

  sync_if_exists "${brand}_auto_base_${os}"
  sync_if_exists "${brand}_auto_${device}_${os}"
  sync_if_exists "${brand}_auto_${isType}_${os}"
  sync_if_exists "${brand}_madd_base_${os}"
  sync_if_exists "${brand}_madd_theme_${os}"
  sync_if_exists "${brand}_madd_ggassistant_${os}"
  sync_if_exists "${brand}_overlay_${os}"

  if remote_has "universal"; then
    sync "universal" || return 1
  else
    log_fail Sync universal "missing required module folder: universal"
    notify_build_state -2
    return 1
  fi

  log_done Sync All
}

cmd_flash() {

  [[ -f $dir/info ]] && source $dir/info
  local requested_partition="$1"
  local image

  # `flash super` rebuilds the images and the whole super.img, then flashes it.
  if [[ "${requested_partition%.img}" == "super" ]]; then
    shift
    cmd_flash_super "$@"
    return
  fi

  # `flash <partition>...` is intentionally a direct, per-partition flow.
  # It must never create/flash super.img or touch the active slot.
  if [[ -n "$requested_partition" && "$requested_partition" != "f" ]]; then
    local -a requested_partitions=()
    local arg
    for arg in "$@"; do
      arg="${arg%.img}"
      [[ " ${requested_partitions[*]} " == *" $arg "* ]] && continue
      requested_partitions+=("$arg")
    done

    # Build every image before touching the device, so a build failure
    # never leaves it with only some of the partitions flashed.
    for requested_partition in "${requested_partitions[@]}"; do
      build_erofs_partition "$requested_partition" Flash || return 1
      image="$dir/$device/firmware-update/${requested_partition}.img"
      if [[ ! -f "$image" ]]; then
        log_fail Flash "$requested_partition" "built image not found: $image"
        return 1
      fi
    done
    ensure_fastbootd_device || return 1

    for requested_partition in "${requested_partitions[@]}"; do
      image="$dir/$device/firmware-update/${requested_partition}.img"
      log_start Flash "$requested_partition"
      fastboot flash "$requested_partition" "$image" || {
        log_fail Flash "$requested_partition" "fastboot flash failed"
        return 1
      }
      log_done Flash "$requested_partition" "$(basename "$image")"
    done
    fastboot reboot || return 1
    return 0
  fi

  build_erofs_images Flash || return 1

  cd $dir/$device/firmware-update

  if ! $isFastbootD; then
    build_super_image || return 1
  fi

  ensure_fastboot_device || return 1

  if [[ $1 == "f" || $2 == "f" ]]; then
    if $isFastbootD; then
      skip_img=(real_state_boot magisk)
      all_img=()
      for f in *.img; do
        [[ -f "$f" ]] && all_img+=("${f%.img}")
      done

      for i in "${all_img[@]}"; do
        if [[ ! " $super_part " =~ " $i " && ! " ${skip_img[*]} " =~ " $i " ]]; then
          fastboot flash "${i}_a" "${i}.img"
        fi
      done
    else
      python3 "$dir/bin/flashing.py" "$dir/$device/firmware-update"
    fi
  fi

  if $isFastbootD; then
    for i in $super_part; do
      img="${i}.img"
      if [[ -f "$img" ]]; then
        echo "Flashing $i from $img"
        fastboot flash "${i}_a" "$img"
      fi
    done
  else
    fastboot -aa
    fastboot flash super super.img
    ! is_local_builder && rm -f super.img
  fi
  fastboot reboot || return 1
}

cmd_patch_call() {
  [[ -f $dir/info ]] && source $dir/info
  local fn="$1"
  local option
  shift || true

  if [[ -z "$fn" ]]; then
    echo "Missing function name"
    return 1
  fi

  if ! declare -F "$fn" >/dev/null; then
    echo "Unknown function: $fn"
    return 1
  fi

  apk_patch_mode="$1"
  apktool_show_logs=false
  apk_sign_output=false
  apk_install_output=false

  for option in "$@"; do
    case "$option" in
    l | --log)
      apktool_show_logs=true
      ;;
    s | --sign)
      apk_sign_output=true
      ;;
    i | --install)
      apk_sign_output=true
      apk_install_output=true
      ;;
    esac
  done
  "$fn" "$@"
}

cmd_patch_x9bl() {
  if [[ $# -eq 0 ]]; then
    echo "Usage: bhlnk x9bl <lk.img> [output.img] [--dry-run]"
    return 1
  fi

  $patch_x9bl "$@"
}

cmd_patch_init_ro() {
  if [[ $# -eq 0 ]]; then
    echo "Usage: bhlnk initro <init> [output] [--dry-run]"
    return 1
  fi

  $patch_init_ro "$@"
}

patch_recovery_adb() (
  local input=""
  local output=""
  local input_abs=""
  local output_abs=""
  local workdir=""
  local magiskboot="$dir/bin/magisk/magiskboot"
  local image_name="recovery.img"
  local patched_name="recovery-adb.img"
  local log_file=""
  local rc_path=""
  local prop_file=""
  local mode=""
  local ramdisk_cpio=""
  local unpack_status=0
  local dry_run=false
  local force=false
  local ota_cert_patch=true
  local cert_count cert_target
  local arg
  local -a positional=()
  local -a patched_props=()

  # GKI 2.0 vendor_boot.img has no root ramdisk.cpio; the recovery ramdisk is
  # the "recovery" vendor ramdisk fragment at vendor_ramdisk/recovery.cpio.
  # Prefer the root ramdisk so classic recovery.img/boot.img are untouched.
  resolve_ramdisk_cpio() {
    if [[ -f ramdisk.cpio ]]; then
      echo ramdisk.cpio
    elif [[ -f vendor_ramdisk/recovery.cpio ]]; then
      echo vendor_ramdisk/recovery.cpio
    fi
  }

  while (($# > 0)); do
    arg="$1"
    case "$arg" in
    --dry-run)
      dry_run=true
      ;;
    --force | -f)
      force=true
      ;;
    --no-ota-cert)
      ota_cert_patch=false
      ;;
    --*)
      echo "Unknown recovery-adb option: $arg"
      return 1
      ;;
    *)
      positional+=("$arg")
      ;;
    esac
    shift
  done

  if ((${#positional[@]} < 1 || ${#positional[@]} > 2)); then
    echo "Usage: bhlnk recovery-adb <recovery.img> [output.img] [--dry-run] [--force] [--no-ota-cert]"
    return 1
  fi

  input="${positional[0]}"
  if [[ ! -f "$input" ]]; then
    log_fail RecoveryADB "$input" "input image not found"
    return 1
  fi
  if [[ ! -x "$magiskboot" ]]; then
    log_fail RecoveryADB magiskboot "not found or not executable: $magiskboot"
    return 1
  fi

  input_abs="$(readlink -f -- "$input")" || return 1
  if ((${#positional[@]} == 2)); then
    output="${positional[1]}"
  else
    output="$(dirname -- "$input_abs")/$(basename -- "${input_abs%.img}")-adb.img"
  fi
  output_abs="$(readlink -m -- "$output")" || return 1

  if [[ "$input_abs" == "$output_abs" ]]; then
    log_fail RecoveryADB Output "refusing to overwrite the stock recovery image"
    return 1
  fi
  if [[ -e "$output_abs" && "$force" != "true" && "$dry_run" != "true" ]]; then
    log_fail RecoveryADB "$output_abs" "output exists; pass --force to replace it"
    return 1
  fi
  if [[ ! -d "$(dirname -- "$output_abs")" ]]; then
    log_fail RecoveryADB Output "directory does not exist: $(dirname -- "$output_abs")"
    return 1
  fi

  workdir="$(mktemp -d "$dir/.vs-recovery-adb.XXXXXX")" || {
    log_fail RecoveryADB Workspace "cannot create temporary directory"
    return 1
  }
  trap 'rm -rf -- "$workdir"' EXIT
  log_file="$workdir/recovery-adb.log"

  cp -- "$input_abs" "$workdir/$image_name" || return 1
  cd "$workdir" || return 1

  log_start RecoveryADB Unpack "$(basename -- "$input_abs")"
  # magiskboot returns a non-zero bitmask of format flags on success (e.g. 3
  # for a vendor_boot carrying a VBMETA/DTB section, as on PKT110); only a
  # genuine parse failure is fatal, so accept 0 and 3 like the bootconfig path.
  "$magiskboot" unpack "$image_name" >"$log_file" 2>&1
  unpack_status=$?
  if ((unpack_status != 0 && unpack_status != 3)); then
    cat "$log_file"
    log_fail RecoveryADB Unpack "unsupported recovery image (status $unpack_status)"
    return 1
  fi
  ramdisk_cpio="$(resolve_ramdisk_cpio)"
  if [[ -z "$ramdisk_cpio" ]]; then
    log_fail RecoveryADB Ramdisk "no recovery ramdisk found (checked ramdisk.cpio, vendor_ramdisk/recovery.cpio)"
    return 1
  fi
  if ! "$magiskboot" cpio "$ramdisk_cpio" extract >>"$log_file" 2>&1; then
    cat "$log_file"
    log_fail RecoveryADB Ramdisk "cannot extract ramdisk"
    return 1
  fi
  if [[ ! -x system/bin/adbd ]]; then
    # On GKI vendor_boot recovery fragments, adbd is provided by the
    # com.android.adbd APEX at runtime rather than baked into the ramdisk.
    # Trust it only when a service definition actually references it.
    if [[ "$ramdisk_cpio" == vendor_ramdisk/recovery.cpio ]] &&
      grep -rq '^[[:space:]]*service[[:space:]]\+adbd[[:space:]]' . 2>/dev/null; then
      log_warn RecoveryADB adbd "binary not in ramdisk; relying on APEX-provided adbd (vendor_boot recovery fragment)"
    else
      log_fail RecoveryADB adbd "system/bin/adbd not found in recovery ramdisk"
      return 1
    fi
  fi

  set_recovery_property() {
    local file="$1"
    local key="$2"
    local value="$3"

    if grep -q "^${key}=" "$file"; then
      sed -i -E "s|^${key}=.*|${key}=${value}|g" "$file"
    else
      printf '%s=%s\n' "$key" "$value" >>"$file"
    fi
  }

  # default.prop is commonly a symlink to prop.default. Patch only real files
  # so the original ramdisk link layout is preserved.
  for prop_file in prop.default default.prop; do
    [[ -f "$prop_file" && ! -L "$prop_file" ]] || continue
    set_recovery_property "$prop_file" ro.secure 0
    set_recovery_property "$prop_file" ro.adb.secure 0
    set_recovery_property "$prop_file" ro.debuggable 1
    patched_props+=("$prop_file")
  done
  if ((${#patched_props[@]} == 0)); then
    prop_file="prop.default"
    printf '%s\n' 'ro.secure=0' 'ro.adb.secure=0' 'ro.debuggable=1' >"$prop_file"
    patched_props+=("$prop_file")
  fi

  if [[ -f system/etc/init/hw/init.rc ]] && grep -q '^[[:space:]]*service[[:space:]]\+adbd[[:space:]]' system/etc/init/hw/init.rc; then
    rc_path="system/etc/init/hw/init.rc"
  else
    while IFS= read -r prop_file; do
      if grep -q '^[[:space:]]*service[[:space:]]\+adbd[[:space:]]' "$prop_file"; then
        rc_path="${prop_file#./}"
        break
      fi
    done < <(find . -type f -name '*.rc' -print | sort)
  fi
  if [[ -z "$rc_path" ]]; then
    log_fail RecoveryADB Init "adbd service definition not found"
    return 1
  fi

  if ! grep -q '^# VS_RECOVERY_ADB_BEGIN$' "$rc_path"; then
    cat >>"$rc_path" <<'EOF'

# VS_RECOVERY_ADB_BEGIN
on fs
    setprop sys.usb.config adb
    setprop service.adb.root 1
    start adbd
# VS_RECOVERY_ADB_END
EOF
  fi

  for prop_file in "${patched_props[@]}"; do
    mode="$(stat -c '%a' "$prop_file")" || return 1
    "$magiskboot" cpio "$ramdisk_cpio" "add 0${mode} ${prop_file} ${prop_file}" >>"$log_file" 2>&1 || {
      cat "$log_file"
      log_fail RecoveryADB Ramdisk "cannot update $prop_file"
      return 1
    }
  done
  mode="$(stat -c '%a' "$rc_path")" || return 1
  "$magiskboot" cpio "$ramdisk_cpio" "add 0${mode} ${rc_path} ${rc_path}" >>"$log_file" 2>&1 || {
    cat "$log_file"
    log_fail RecoveryADB Ramdisk "cannot update $rc_path"
    return 1
  }

  # --- Self-signed OTA certificate for `adb sideload` -----------------------
  # Recovery verifies a sideloaded package against the otacerts.zip embedded in
  # its OWN ramdisk, independent of the system partition copy that
  # patch_ota_key() rewrites. Swap it for bin/otacerts.zip so our self-signed
  # OTAs can be installed over `adb sideload`; --no-ota-cert leaves the OEM
  # sideload key in place. Missing cert is a warning (not a failure) so the
  # standalone `bhlnk recovery-adb` still works on arbitrary images.
  if [[ "$ota_cert_patch" == "true" ]]; then
    if [[ ! -f "$ota_certs_zip" ]]; then
      log_warn RecoveryADB OTACert "bin/otacerts.zip missing; recovery keeps the OEM sideload key"
    elif ! unzip -p "$ota_certs_zip" '*.pem' 2>/dev/null | openssl x509 -noout -pubkey >/dev/null 2>&1; then
      log_fail RecoveryADB OTACert "bin/otacerts.zip holds no valid certificate"
      return 1
    else
      cert_count=0
      while IFS= read -r cert_target; do
        cert_target="${cert_target#./}"
        cp -f -- "$ota_certs_zip" "$cert_target" || {
          log_fail RecoveryADB OTACert "cannot stage $cert_target"
          return 1
        }
        mode="$(stat -c '%a' "$cert_target")" || return 1
        "$magiskboot" cpio "$ramdisk_cpio" "add 0${mode} ${cert_target} ${cert_target}" >>"$log_file" 2>&1 || {
          cat "$log_file"
          log_fail RecoveryADB OTACert "cannot update $cert_target"
          return 1
        }
        log_event info RecoveryADB "$cert_target" Patch "own OTA certificate"
        cert_count=$((cert_count + 1))
      done < <(find . -path '*/etc/security/otacerts.zip' -type f -print | sort)

      if ((cert_count == 0)); then
        # No otacerts.zip in the ramdisk yet: install one where recovery looks
        # for it by default so sideload verification trusts our key.
        cert_target="system/etc/security/otacerts.zip"
        mkdir -p "$(dirname -- "$cert_target")" || return 1
        cp -f -- "$ota_certs_zip" "$cert_target" || return 1
        "$magiskboot" cpio "$ramdisk_cpio" "add 0644 ${cert_target} ${cert_target}" >>"$log_file" 2>&1 || {
          cat "$log_file"
          log_fail RecoveryADB OTACert "cannot add $cert_target"
          return 1
        }
        log_event info RecoveryADB "$cert_target" Patch "own OTA certificate (new)"
        cert_count=1
      fi
      log_done RecoveryADB OTACert "patched $cert_count file(s)"
    fi
  fi

  log_start RecoveryADB Repack "$patched_name"
  if ! "$magiskboot" repack "$image_name" "$patched_name" >>"$log_file" 2>&1; then
    cat "$log_file"
    log_fail RecoveryADB Repack "failed"
    return 1
  fi
  if [[ ! -s "$patched_name" ]]; then
    log_fail RecoveryADB Repack "output image is empty"
    return 1
  fi
  if (($(stat -c '%s' "$patched_name") > $(stat -c '%s' "$image_name"))); then
    log_fail RecoveryADB Size "patched image exceeds the source partition size"
    return 1
  fi

  mkdir verify || return 1
  cd verify || return 1
  "$magiskboot" unpack "../$patched_name" >"../verify.log" 2>&1
  unpack_status=$?
  if ((unpack_status != 0 && unpack_status != 3)); then
    cat ../verify.log
    log_fail RecoveryADB Verify "cannot unpack patched image (status $unpack_status)"
    return 1
  fi
  ramdisk_cpio="$(resolve_ramdisk_cpio)"
  if [[ -z "$ramdisk_cpio" ]]; then
    cat ../verify.log
    log_fail RecoveryADB Verify "no recovery ramdisk found in patched image"
    return 1
  fi
  if ! "$magiskboot" cpio "$ramdisk_cpio" extract >>"../verify.log" 2>&1; then
    cat ../verify.log
    log_fail RecoveryADB Verify "cannot extract patched ramdisk"
    return 1
  fi
  if ! grep -q '^# VS_RECOVERY_ADB_BEGIN$' "$rc_path"; then
    log_fail RecoveryADB Verify "ADB init trigger is missing"
    return 1
  fi
  for prop_file in "${patched_props[@]}"; do
    if ! grep -qx 'ro.secure=0' "$prop_file" ||
      ! grep -qx 'ro.adb.secure=0' "$prop_file" ||
      ! grep -qx 'ro.debuggable=1' "$prop_file"; then
      log_fail RecoveryADB Verify "ADB properties are missing from $prop_file"
      return 1
    fi
  done
  if [[ "$ota_cert_patch" == "true" && -f "$ota_certs_zip" ]]; then
    cert_count=0
    while IFS= read -r cert_target; do
      if ! cmp -s -- "$ota_certs_zip" "$cert_target"; then
        log_fail RecoveryADB Verify "otacerts.zip was not applied to ${cert_target#./}"
        return 1
      fi
      cert_count=$((cert_count + 1))
    done < <(find . -path '*/etc/security/otacerts.zip' -type f -print)
    if ((cert_count == 0)); then
      log_fail RecoveryADB Verify "no otacerts.zip present in patched ramdisk"
      return 1
    fi
  fi
  cd "$workdir" || return 1

  if [[ "$dry_run" == "true" ]]; then
    log_done RecoveryADB DryRun "patch and repack verified; no output written"
    return 0
  fi

  cp -f -- "$patched_name" "$output_abs" || {
    log_fail RecoveryADB Output "cannot write $output_abs"
    return 1
  }
  log_done RecoveryADB "$output_abs" "$(sha256sum "$output_abs" | awk '{print $1}')"
)

cmd_patch_recovery_adb() {
  if [[ $# -eq 0 ]]; then
    echo "Usage: bhlnk recovery-adb <recovery.img> [output.img] [--dry-run] [--force] [--no-ota-cert]"
    return 1
  fi

  patch_recovery_adb "$@"
}

# Reuse the latest backup/<codename>_* snapshot (taken right after extract)
# instead of downloading, refreshing the per-build fields detect() would set.
restore_build_source() {
  local target="$1"
  local builder="$2"

  [[ "$builder" == "true" ]] && builder="LocalBuild"
  if [[ -n "${3:-}" ]]; then
    restore_backup_dir "$3" || return 1
  else
    restore_latest_backup "$target" || return 1
  fi

  patch="$(get_patch)"
  if [[ "$base" == *"BETA"* ]]; then
    charcode="$(echo "$base" | rev | cut -d"." -f2 | rev)"
  else
    charcode="$(echo "$base" | rev | cut -d"." -f1 | rev)"
  fi
  iden="${base/$charcode/MUSHROM$da}"
  final_repack="${final_repack%%_*}_${device^^}_${iden}_${patch%.OTA}.zip"
  buildtime="$da"

  update_var buildtime "$buildtime" "$dir/info"
  update_var builder "$builder" "$dir/info"
  update_var iden "$iden" "$dir/info"
  update_var patch "$patch" "$dir/info"
  update_var final_repack "$final_repack" "$dir/info"
  log_event info "  Base version" "" "" "$base"
  log_event info "  Repack" "" "" "$final_repack"
}

# Full build. The patched ROM is packed as one signed, password-locked
# payload.bin OTA zip (pack): Mushrom installs it as an OTA and MushromTool flashes
# the same file over fastboot/fastbootd.
cmd_build() {
  local target=""
  local builder="true"
  local from_backup=false
  local force_download=false
  local do_backup=true
  local do_upload=true
  local backup_dir=""
  local arg
  local -a positional=()

  for arg in "$@"; do
    case "$arg" in
    --restore) from_backup=true ;;
    --download) force_download=true ;;
    --backup) do_backup=true ;;
    --no-backup) do_backup=false ;;
    --no-upload) do_upload=false ;;
    --*)
      echo "Unknown build option: $arg"
      return 1
      ;;
    *) positional+=("$arg") ;;
    esac
  done
  target="${positional[0]:-}"
  builder="${positional[1]:-$builder}"

  if [[ -z "$target" ]]; then
    echo "Usage: build <codename|url|rom.zip> [builder] [--restore|--download] [--backup|--no-backup] [--no-upload]"
    return 1
  fi
  if [[ "$from_backup" == "true" && "$force_download" == "true" ]]; then
    echo "build: --restore and --download cannot be used together"
    return 1
  fi

  # Fail before the download when the ROM could not be signed at the end.
  if [[ "${VS_OTA:-true}" == "false" ]]; then
    log_fail Build Config "VS_OTA=false disables OTA key patching"
    return 1
  fi
  ota_key_prepare || {
    log_fail Build Key "OTA key unavailable (bin/ota_keys)"
    return 1
  }

  # isOTA routes the upload to PAID_OTA; persist it for later steps that
  # re-source info.
  isOTA=true
  rom_source_id="" rom_source_target=""
  if [[ "$from_backup" == "true" ]]; then
    clean || return 1
    restore_build_source "$target" "$builder" || return 1
  elif [[ ! -f "$target" ]]; then
    # A codename or URL is looked up on the server first. If the backup store
    # already holds that ROM archive extracted, the build starts from the
    # backup; a different (newer) ROM is downloaded, extracted and backed up
    # in its place. --download skips the backup, --restore skips the server.
    require_rom_target "$target" || return 1
    clean || return 1
    [[ "$target" =~ ^https?:// ]] || rom_source_target="${target,,}"
    if resolve_rom_source "$target"; then
      [[ "$force_download" == "true" ]] || backup_dir="$(find_backup_by_source "$rom_source_id")"
      [[ -z "$backup_dir" ]] ||
        log_event info Build Source "" "backup $(basename -- "$backup_dir") holds this ROM; download and extract skipped"
    elif [[ "$force_download" != "true" && -n "$rom_source_target" ]]; then
      backup_dir="$(latest_backup_dir "$target")"
      [[ -n "$backup_dir" ]] || return 1
      log_warn Build Source "server lookup failed; using the latest backup of $target"
    else
      return 1
    fi
    if [[ -n "$backup_dir" ]]; then
      if restore_build_source "$target" "$builder" "$backup_dir"; then
        from_backup=true
      else
        [[ -n "$rom_source_url" ]] || return 1
        log_warn Build Source "backup could not be restored; downloading the ROM"
        clean || return 1
      fi
    fi
    if [[ "$from_backup" != "true" ]]; then
      download_resolved_rom || return 1
    fi
  else
    prepare_rom_source Build target || return 1
  fi
  if [[ "$from_backup" != "true" ]]; then
    detect "$builder" "$target" || return 1
  fi
  if grep -q '^is_mkstock=' "$dir/info"; then
    sed -i "s/^is_mkstock=.*/is_mkstock=false/" "$dir/info"
  else
    echo "is_mkstock=false" >>"$dir/info"
  fi
  echo "isOTA=true" >>"$dir/info"
  notify_build_state 1
  notify_build_state 2
  cmd_sync || return 1
  notify_build_state 3
  if [[ "$from_backup" == "true" ]]; then
    log_event info Extract Skip "" "restored from backup"
  else
    extract || return 1
  fi
  if [[ ! -f "$ota_meta" ]]; then
    log_fail Build Source "ROM archive has no payload.bin; cannot rebuild as OTA"
    return 1
  fi
  if [[ "$from_backup" != "true" ]]; then
    # CI runners are thrown away, so a backup only pays off when VS_BACKUP_DIR
    # names a store that outlives the job (the local farm sets it).
    if [[ "${GITHUB_ACTIONS:-false}" == "true" && -z "${VS_BACKUP_DIR:-}" ]]; then
      log_event warn Backup "GitHub Actions" Skip "disabled in CI"
    elif [[ "$do_backup" == "true" ]]; then
      backup || return 1
    fi
  fi
  notify_build_state 4
  patchvs "$builder" || return 1
  notify_build_state 5
  pack || return 1
  notify_build_state 6
  if [[ "$do_upload" == "true" ]]; then
    UPLOAD_FILE="$ota_package" cmd_upload || return 1
    if [[ "${isOTA:-false}" == "true" ]]; then
      ota_published="$(basename "$ota_package")"
    fi
  else
    log_event warn Upload "${ota_package#$dir/}" Skip "--no-upload"
  fi
  notify_build_state 0
}

# Used by build: a local rom.zip is moved into the workspace
# root where detect() looks (clean() would delete it, nothing to download);
# anything else is cleaned and downloaded. $2 names the caller's target
# variable, rewritten to the archive basename for a local file.
prepare_rom_source() {
  local scope="$1"
  local -n rom_target="$2"

  require_rom_target "$rom_target" "$scope" || return 1
  if [[ -f "$rom_target" ]]; then
    if [[ "$(realpath -- "$(dirname -- "$rom_target")")" != "$(realpath -- "$dir")" ]]; then
      mv -- "$rom_target" "$dir/" || return 1
    fi
    rom_target="$(basename -- "$rom_target")"
    if compgen -G "$dir/*.zip" | grep -vxF "$dir/$rom_target" | grep -q . ||
      compgen -G "$dir/*.tgz" >/dev/null; then
      log_fail "$scope" Source "other ROM archives in workspace; detect() would pick the wrong one"
      return 1
    fi
  else
    clean || return 1
    download "$rom_target" || return 1
  fi
}

stock_build() {
  local target=""
  local builder="StockBuild"
  local rom_path
  local do_backup=false
  local target_slot="a"
  local arg
  local -a positional=()

  for arg in "$@"; do
    case "$arg" in
    --backup)
      do_backup=true
      ;;
    --a)
      target_slot="a"
      ;;
    --b)
      target_slot="b"
      ;;
    --*)
      echo "Unknown stock option: $arg"
      return 1
      ;;
    *)
      positional+=("$arg")
      ;;
    esac
  done

  if ((${#positional[@]} > 2)); then
    echo "Usage: stock <codename|url> [builder] [--backup] [--a|--b]"
    return 1
  fi

  target="${positional[0]:-}"
  builder="${positional[1]:-$builder}"

  if [[ -z "$target" ]]; then
    echo "Usage: stock <codename|url> [builder] [--backup]"
    return 1
  fi

  require_rom_target "$target" || return 1
  rom_path="$(local_rom_path "$target")"
  [[ -n "$rom_path" ]] && target="$rom_path"
  clean --keep "$(local_rom_keep "$rom_path")" || return 1
  download "$target" || return 1
  detect "$builder" "$target" || return 1
  if grep -q '^is_mkstock=' "$dir/info"; then
    sed -i "s/^is_mkstock=.*/is_mkstock=true/" "$dir/info"
  else
    echo "is_mkstock=true" >>"$dir/info"
  fi
  notify_build_state 1
  notify_build_state 3
  extract_stock_images || return 1
  if [[ "$do_backup" == "true" ]]; then
    backup || return 1
  fi
  notify_build_state 5
  pack_stock "$target_slot" || return 1
  notify_build_state 6
  UPLOAD_FILE="$ota_package" cmd_upload || return 1
  notify_build_state 0
}

# Stock ROM: the untouched images packed as an unlocked payload.bin OTA zip
# (pack_stock). A ColorOS/OPlus codename builds the newest firmware of a
# region: positional or --region, else $COLOROS_REGION or
# fastboot/coloros_region.list.
cmd_stock() {
  local usage="Usage: stock <codename|url|rom.zip> [region] [builder] [--region REGION] [--backup] [--a|--b]"
  local target=""
  local region=""
  local region_option_set=false
  local builder="StockBuild"
  local do_backup=false
  local target_slot="a"
  local arg
  local -a positional=()
  local -a stock_args=()

  while (($# > 0)); do
    arg="$1"
    case "$arg" in
    --region)
      if (($# < 2)) || [[ -z "$2" || "$2" == --* ]]; then
        echo "stock: --region requires a value"
        return 1
      fi
      region="$2"
      region_option_set=true
      shift 2
      continue
      ;;
    --region=*)
      region="${arg#*=}"
      if [[ -z "$region" ]]; then
        echo "stock: --region requires a value"
        return 1
      fi
      region_option_set=true
      ;;
    --backup)
      do_backup=true
      ;;
    --a)
      target_slot="a"
      ;;
    --b)
      target_slot="b"
      ;;
    --*)
      echo "Unknown stock option: $arg"
      return 1
      ;;
    *)
      positional+=("$arg")
      ;;
    esac
    shift
  done

  target="${positional[0]:-}"
  if [[ -z "$target" ]] || ((${#positional[@]} > 3)); then
    echo "$usage"
    return 1
  fi

  if [[ ! "$target" =~ ^https?:// && ! -f "$target" ]] && is_coloros_device_code "$target"; then
    if [[ "$region_option_set" == "true" ]]; then
      positional=("$target" "" "${positional[@]:1}")
    elif ! is_coloros_region "${positional[1]:-}"; then
      # No region given: the second argument is the builder.
      positional=("$target" "" "${positional[@]:1}")
    else
      region="${positional[1]}"
    fi
    if ((${#positional[@]} > 3)); then
      echo "$usage"
      return 1
    fi
    builder="${positional[2]:-$builder}"
    [[ -n "$region" ]] || region="$(coloros_query_region "$target")"
    region="${region,,}"
    if ! is_coloros_region "$region"; then
      log_fail Stock Region "unsupported region: $region"
      return 1
    fi
    log_event info Stock Region "" "${region^^}"
  else
    # Direct link, local archive or a non-ColorOS codename: no region.
    if [[ "$region_option_set" == "true" ]]; then
      log_fail Stock Region "--region is only valid with a ColorOS codename"
      return 1
    fi
    if ((${#positional[@]} > 2)); then
      echo "$usage"
      return 1
    fi
    builder="${positional[1]:-$builder}"
  fi

  stock_args=("$target" "$builder" "--$target_slot")
  [[ "$do_backup" == "true" ]] && stock_args+=(--backup)
  if [[ -n "$region" ]]; then
    COLOROS_REGION="$region" stock_build "${stock_args[@]}"
  else
    stock_build "${stock_args[@]}"
  fi
}

fastboot_device_connected() {
  command -v fastboot >/dev/null 2>&1 || return 1
  fastboot devices 2>/dev/null | awk 'NF { found=1 } END { exit !found }'
}

first_adb_device() {
  command -v adb >/dev/null 2>&1 || return 1
  adb devices 2>/dev/null | awk 'NR > 1 && $2 == "device" { print $1; exit }'
}

# Print the active slot (a|b) of the connected device: ADB first
# (ro.boot.slot_suffix), then bootloader/fastbootd (getvar current-slot).
# Prints nothing when no device answers or the device is not A/B.
detect_active_slot() {
  local serial
  local slot=""

  serial="$(first_adb_device)"
  if [[ -n "$serial" ]]; then
    slot="$(adb -s "$serial" shell getprop ro.boot.slot_suffix 2>/dev/null)"
  elif fastboot_device_connected; then
    slot="$(fastboot getvar current-slot 2>&1 | awk -F': *' '/^current-slot:/{print $2; exit}')"
  fi

  slot="${slot//[[:space:]]/}"
  slot="${slot#_}"
  if [[ "$slot" == "a" || "$slot" == "b" ]]; then
    printf '%s\n' "$slot"
  fi
}

wait_for_fastboot_device() {
  local timeout="${1:-60}"
  local waited=0

  while ((waited < timeout)); do
    fastboot_device_connected && return 0
    sleep 1
    waited=$((waited + 1))
  done

  return 1
}

fastboot_userspace_device() {
  local vars

  fastboot_device_connected || return 1
  vars="$(fastboot getvar is-userspace 2>&1 || true)"
  grep -Eiq 'is-userspace:[[:space:]]*yes|is-userspace:[[:space:]]*true' <<<"$vars"
}

wait_for_fastbootd_device() {
  local timeout="${1:-60}"
  local waited=0

  while ((waited < timeout)); do
    fastboot_userspace_device && return 0
    sleep 1
    waited=$((waited + 1))
  done

  return 1
}

ensure_fastbootd_device() {
  local adb_serial=""

  if fastboot_userspace_device; then
    log_done Fastbootd Device "already in userspace"
    return 0
  fi

  if fastboot_device_connected; then
    log_start Fastboot Device "reboot fastbootd"
    fastboot reboot fastboot || {
      log_fail Fastboot Device "cannot reboot to fastbootd"
      return 1
    }
  else
    command -v adb >/dev/null 2>&1 || {
      log_fail Fastbootd Device "fastboot unavailable and adb is not installed"
      return 1
    }

    adb_serial="$(first_adb_device)"
    if [[ -z "$adb_serial" ]]; then
      log_fail Fastbootd Device "no fastboot or authorized ADB device connected"
      return 1
    fi

    log_start ADB "$adb_serial" "reboot fastboot"
    adb -s "$adb_serial" reboot fastboot || {
      log_fail ADB "$adb_serial" "cannot reboot to fastboot"
      return 1
    }
  fi

  log_start Fastbootd Device "waiting up to 60 seconds"
  if ! wait_for_fastbootd_device 60; then
    log_fail Fastbootd Device "device did not enter fastbootd within 60 seconds"
    return 1
  fi
  log_done Fastbootd Device "connected in userspace"
}

ensure_fastboot_device() {
  local adb_serial=""
  local reboot_target="fastboot"

  fastboot_device_connected && return 0

  command -v adb >/dev/null 2>&1 || {
    log_fail Fastboot Device "fastboot unavailable and adb is not installed"
    return 1
  }

  adb_serial="$(first_adb_device)"
  if [[ -z "$adb_serial" ]]; then
    log_fail Fastboot Device "no fastboot or authorized ADB device connected"
    return 1
  fi

  log_start ADB "$adb_serial" "reboot $reboot_target"
  if ! adb -s "$adb_serial" reboot "$reboot_target"; then
    log_fail ADB "$adb_serial" "cannot reboot to $reboot_target"
    return 1
  fi
  log_done ADB "$adb_serial" "reboot $reboot_target"

  log_start Fastboot Device "waiting up to 60 seconds"
  if ! wait_for_fastboot_device 60; then
    log_fail Fastboot Device "device did not enter fastboot within 60 seconds"
    return 1
  fi
  log_done Fastboot Device "connected"
}

supertest() {
  local adb_serial=""
  local waited=0
  local slot=""
  local arg

  # Usage: supertest [--a|--b]
  # Without a slot, build for the slot that is active on the connected device
  # (ADB or fastboot/fastbootd); fall back to slot a when no device answers.
  for arg in "$@"; do
    case "$arg" in
    --a | a) slot="a" ;;
    --b | b) slot="b" ;;
    *)
      echo "Unknown supertest option: $arg"
      return 1
      ;;
    esac
  done

  [[ -f "$dir/info" ]] && source "$dir/info"
  device="${device,,}"

  if [[ -z "$device" ]]; then
    log_fail SuperTest Device "missing device, run detect/restore first"
    return 1
  fi

  if [[ -z "$slot" ]]; then
    slot="$(detect_active_slot)"
    if [[ -n "$slot" ]]; then
      log_event info SuperTest Slot "" "active slot on device: $slot"
    else
      slot="a"
      log_warn SuperTest Slot "no device answered, default slot a"
    fi
  fi

  if [[ -d "$dir/img_temp" ]]; then
    log_start SuperTest "Build images" "from img_temp"
    builder="LocalBuild"
    build_erofs_images SuperTest || return 1
  else
    log_warn SuperTest img_temp "not found, build from existing img files"
  fi

  cd "$dir/$device/firmware-update" || {
    log_fail SuperTest firmware-update "missing"
    return 1
  }

  # Always a whole super image, FastbootD devices included; flashing the
  # partitions one by one from fastbootd is fastbootdtest.
  load_coloros_super_size || return 1
  build_super_partition_lists
  print_super_size_info
  if ! run_lpmake_super supertest.img "$slot"; then
    rm -f supertest.img
    log_fail SuperTest supertest.img "build failed"
    return 1
  fi
  log_done SuperTest supertest.img "slot $slot"

  # super is written from the bootloader: leave fastbootd, or reboot there
  # from Android.
  if fastboot_userspace_device; then
    log_start Fastboot Device "reboot bootloader (leaving fastbootd)"
    fastboot reboot bootloader || {
      log_fail Fastboot Device "cannot reboot to bootloader"
      return 1
    }
    while ((waited < 60)); do
      fastboot_device_connected && ! fastboot_userspace_device && break
      sleep 1
      waited=$((waited + 1))
    done
  elif ! fastboot_device_connected; then
    adb_serial="$(first_adb_device)"
    if [[ -z "$adb_serial" ]]; then
      log_event warn Fastboot "" Skip "no fastboot or authorized ADB device connected"
      return 0
    fi
    log_start ADB "$adb_serial" "reboot bootloader"
    if ! adb -s "$adb_serial" reboot bootloader; then
      log_fail ADB "$adb_serial" "cannot reboot to bootloader"
      return 1
    fi
    wait_for_fastboot_device 60
  fi

  if ! fastboot_device_connected || fastboot_userspace_device; then
    log_fail Fastboot Device "bootloader not detected within 60 seconds"
    return 1
  fi
  log_done Fastboot Device "bootloader connected"

  log_start Fastboot Flash "supertest.img" "slot $slot"
  # Partition trong super chỉ có hậu tố _$slot, nên phải boot đúng slot đó.
  if ! fastboot --set-active="$slot"; then
    log_fail Fastboot Slot "cannot set active slot $slot"
    return 1
  fi
  if ! fastboot flash super supertest.img; then
    log_fail Fastboot Flash "super rejected by the bootloader; try: bhlnk fastbootdtest"
    return 1
  fi

  fastboot reboot || return 1
}

# Sparse super image holding only the LP metadata of one slot: every dynamic
# partition with an image in the current directory is declared with its
# (block-padded) size, so its extents are allocated, but no data is attached.
# bin/super_chunks.py and MushromTool place the partition data at those offsets.
# Usage: build_super_layout_image <a|b> <output.img>   (run in firmware-update)
build_super_layout_image() {
  local slot="$1"
  local output="$2"
  local log_file="${output}.log"
  local other_slot part size size_arg group_size
  local -a layout_args=()

  [[ "$slot" == "a" ]] && other_slot="b" || other_slot="a"
  load_coloros_super_size || return 1
  build_super_partition_lists
  size_arg="${super_lpadd_device_size:-${device_size:-auto}}"
  group_size="${super_lpadd_group_size:-${device_size:-0}}"

  # Same layout as run_lpmake_super, with the sizes declared up front.
  for part in $super_part; do
    size=0
    [[ -f "${part}.img" ]] && size=$((($(stat -c%s "${part}.img") + 4095) / 4096 * 4096))
    layout_args+=(--partition "${part}_${other_slot}:readonly:0:qti_dynamic_partitions_${other_slot}")
    layout_args+=(--partition "${part}_${slot}:readonly:$size:qti_dynamic_partitions_${slot}")
  done

  log_start SuperLayout "$(basename "$output")" "slot $slot"
  mkdir -p "$(dirname "$output")" || return 1
  if ! "$dir/bin/lpmake" --metadata-size 65536 --super-name super --metadata-slots 3 \
    --device-size "$size_arg" \
    --group "qti_dynamic_partitions_a:$group_size" \
    --group "qti_dynamic_partitions_b:$group_size" \
    "${layout_args[@]}" \
    --sparse --force-full-image --output "$output" >"$log_file" 2>&1; then
    log_block SuperLayout "$(basename "$output")" "$log_file" 1
    rm -f "$output" "$log_file"
    return 1
  fi
  rm -f "$log_file"
  log_done SuperLayout "$(basename "$output")" "$(stat -c%s "$output") bytes"
}

# Test of the single-file flashing route: instead of a full super.img, build
# only the LP metadata (real extents, no data) and let bin/super_chunks.py
# place each partition image at its offset, emitting sparse pieces no larger
# than max-download-size that are flashed to super one after another.
# Usage: flash_newtest [--a|--b] [--dry-run]
#   --dry-run  no device: keep the pieces in zip_temp/newtest, rebuild a raw
#              super from them and compare every partition with its image
flash_newtest() {
  local slot=""
  local dry_run=false
  local arg part size unpacked
  local waited=0
  local max_size=$((256 * 1024 * 1024))
  local work layout log_file

  for arg in "$@"; do
    case "$arg" in
    --a | a) slot="a" ;;
    --b | b) slot="b" ;;
    --dry-run) dry_run=true ;;
    *)
      echo "Unknown flash_newtest option: $arg"
      return 1
      ;;
    esac
  done

  [[ -f "$dir/info" ]] && source "$dir/info"
  device="${device,,}"
  if [[ -z "$device" ]]; then
    log_fail NewTest Device "missing device, run detect/restore first"
    return 1
  fi

  if [[ -z "$slot" ]]; then
    slot="$(detect_active_slot)"
    if [[ -n "$slot" ]]; then
      log_event info NewTest Slot "" "active slot on device: $slot"
    else
      slot="a"
      log_warn NewTest Slot "no device answered, default slot a"
    fi
  fi

  cd "$dir/$device/firmware-update" || {
    log_fail NewTest firmware-update "missing"
    return 1
  }

  work="$dir/zip_temp/newtest"
  layout="$work/layout.img"
  log_file="$work/newtest.log"
  rm -rf "$work"
  mkdir -p "$work" || return 1
  build_super_layout_image "$slot" "$layout" || return 1
  print_super_size_info

  if [[ "$dry_run" == "true" ]]; then
    log_start NewTest Pieces "dry run"
    if ! python3 "$dir/bin/super_chunks.py" "$layout" . "$slot" "$work" --max-size "$max_size"; then
      log_fail NewTest Pieces "compose failed"
      return 1
    fi
    log_start NewTest Verify "simg2img + lpunpack"
    if ! simg2img "$work"/super_*.img "$work/super.raw" >"$log_file" 2>&1 ||
      ! mkdir -p "$work/unpack" ||
      ! "$dir/bin/lpunpack" "$work/super.raw" "$work/unpack" >>"$log_file" 2>&1; then
      log_block NewTest Verify "$log_file" 1
      return 1
    fi
    for part in $super_part; do
      [[ -f "${part}.img" ]] || continue
      unpacked="$work/unpack/${part}_${slot}.img"
      if ! cmp -s -- "${part}.img" "$unpacked"; then
        log_fail NewTest Verify "${part}_${slot} differs from ${part}.img"
        return 1
      fi
      log_event info NewTest "${part}_${slot}" OK "$(stat -c%s "${part}.img")"
    done
    rm -rf "$work/super.raw" "$work/unpack"
    log_done NewTest Verify "pieces kept in zip_temp/newtest"
    return 0
  fi

  ensure_fastboot_device || return 1
  # super is written from the bootloader, not fastbootd.
  if fastboot_userspace_device; then
    log_start Fastboot Device "reboot bootloader (leaving fastbootd)"
    fastboot reboot bootloader || {
      log_fail Fastboot Device "cannot reboot to bootloader"
      return 1
    }
    while ((waited < 60)); do
      fastboot_device_connected && ! fastboot_userspace_device && break
      sleep 1
      waited=$((waited + 1))
    done
    if ! fastboot_device_connected || fastboot_userspace_device; then
      log_fail Fastboot Device "bootloader not detected within 60 seconds"
      return 1
    fi
  fi

  # Pieces must fit one download; cap at 1 GiB to bound the temp file.
  size="$(fastboot getvar max-download-size 2>&1 | awk -F': *' '/^max-download-size:/{print $2; exit}')"
  if [[ "$size" =~ ^(0x[0-9a-fA-F]+|[0-9]+)$ ]] && ((size >= 16 * 1024 * 1024)); then
    max_size=$((size > 1024 * 1024 * 1024 ? 1024 * 1024 * 1024 : size))
  else
    log_warn NewTest max-download-size "not reported, using $max_size"
  fi

  log_start NewTest Flash "slot $slot, pieces up to $max_size bytes"
  # Partition trong super chỉ có hậu tố _$slot, nên phải boot đúng slot đó.
  if ! fastboot --set-active="$slot"; then
    log_fail NewTest Slot "cannot set active slot $slot"
    return 1
  fi
  if ! python3 "$dir/bin/super_chunks.py" "$layout" . "$slot" "$work" \
    --max-size "$max_size" --flash; then
    log_fail NewTest Flash "super piece rejected; the device may not boot until super is reflashed"
    return 1
  fi
  rm -rf "$work"
  log_done NewTest Flash "slot $slot"

  fastboot reboot || return 1
}

# Rebuild the EROFS images from img_temp, pack them into super.img for the
# requested slot, then flash it from the bootloader via cmd_flashsuper.
# Usage: flash super [--a|--b]   (default slot: a)
cmd_flash_super() {
  local slot="a"
  local arg

  for arg in "$@"; do
    case "$arg" in
    --a | a) slot="a" ;;
    --b | b) slot="b" ;;
    *)
      echo "Unknown flash super option: $arg"
      return 1
      ;;
    esac
  done

  [[ -f "$dir/info" ]] && source "$dir/info"
  device="${device,,}"
  if [[ -z "$device" ]]; then
    log_fail FlashSuper Device "missing device, run detect/restore first"
    return 1
  fi

  # Keep the partition images after lpadd (build_super_image removes them
  # for non-local builders).
  builder="LocalBuild"
  if [[ -d "$dir/img_temp" ]]; then
    build_erofs_images FlashSuper || return 1
  else
    log_warn FlashSuper img_temp "not found, build from existing img files"
  fi

  cd "$dir/$device/firmware-update" || {
    log_fail FlashSuper firmware-update "missing"
    return 1
  }

  load_coloros_super_size || return 1
  build_super_partition_lists
  print_super_size_info
  build_super_image "$slot" || return 1

  cmd_flashsuper "$dir/$device/firmware-update/super.img" "--$slot"
}

# Flash an already-built super image as-is: no erofs/lpmake rebuild.
# Default image: <device>/firmware-update/super.img, then supertest.img.
cmd_flashsuper() {
  local image=""
  local slot="a"
  local arg

  for arg in "$@"; do
    case "$arg" in
    --a | a) slot="a" ;;
    --b | b) slot="b" ;;
    --*)
      echo "Unknown flashsuper option: $arg"
      return 1
      ;;
    *) image="$arg" ;;
    esac
  done

  if [[ -z "$image" ]]; then
    [[ -f "$dir/info" ]] && source "$dir/info"
    device="${device,,}"
    local candidate
    for candidate in super.img supertest.img; do
      if [[ -n "$device" && -f "$dir/$device/firmware-update/$candidate" ]]; then
        image="$dir/$device/firmware-update/$candidate"
        break
      fi
    done
  fi

  if [[ -z "$image" || ! -s "$image" ]]; then
    log_fail FlashSuper Image "super image not found: ${image:-<none>}"
    return 1
  fi
  image="$(realpath "$image")"

  ensure_fastboot_device || return 1
  # super is written from the bootloader, not fastbootd.
  if fastboot_userspace_device; then
    log_start Fastboot Device "reboot bootloader (leaving fastbootd)"
    fastboot reboot bootloader || {
      log_fail Fastboot Device "cannot reboot to bootloader"
      return 1
    }
    local waited=0
    while ((waited < 60)); do
      fastboot_device_connected && ! fastboot_userspace_device && break
      sleep 1
      waited=$((waited + 1))
    done
    if ! fastboot_device_connected || fastboot_userspace_device; then
      log_fail Fastboot Device "bootloader not detected within 60 seconds"
      return 1
    fi
  fi

  log_start FlashSuper "$(basename "$image")" "slot $slot"
  fastboot --set-active="$slot" || {
    log_fail FlashSuper Slot "cannot set active slot $slot"
    return 1
  }
  fastboot flash super "$image" || {
    log_fail FlashSuper "$(basename "$image")" "fastboot flash failed"
    return 1
  }
  log_done FlashSuper "$(basename "$image")" "slot $slot"
  fastboot reboot || return 1
}

# Flash the dynamic (super) partitions one by one from fastbootd instead of
# writing a whole super image: rebuilds the EROFS images from img_temp when it
# exists, reboots the device (adb/bootloader) into fastbootd, cancels a pending
# Virtual A/B merge (fastbootd refuses to flash logical partitions during one)
# and flashes every super partition that has an image in firmware-update and
# exists as a logical partition on the device, into the current slot.
# Usage: fastbootdtest [partition ...]   (default: all super partitions)
fastbootdtest() {
  local -a parts=()
  local part img target slot flashed=0

  [[ -f "$dir/info" ]] && source "$dir/info"
  device="${device,,}"
  if [[ -z "$device" ]]; then
    log_fail FastbootdTest Device "missing device, run detect/restore first"
    return 1
  fi

  for part in "$@"; do
    part="${part%.img}"
    if ! grep -qw -- "$part" <<<"$super_part"; then
      log_fail FastbootdTest "$part" "not a super partition"
      return 1
    fi
    parts+=("$part")
  done
  ((${#parts[@]})) || read -r -a parts <<<"$(echo $super_part)"

  if [[ -d "$dir/img_temp" ]]; then
    log_start FastbootdTest "Build images" "from img_temp"
    builder="LocalBuild"
    build_erofs_images FastbootdTest || return 1
  else
    log_warn FastbootdTest img_temp "not found, flash existing img files"
  fi

  cd "$dir/$device/firmware-update" || {
    log_fail FastbootdTest firmware-update "missing"
    return 1
  }

  ensure_fastbootd_device || return 1
  # fastbootd only knows slotted names (system_a), not the bare name.
  slot="$(fastboot getvar current-slot 2>&1 | awk -F': *' '/^current-slot:/{print $2; exit}')"
  slot="${slot#_}"
  log_event info FastbootdTest Slot "" "current slot: ${slot:-none}"
  fastboot snapshot-update cancel >/dev/null 2>&1 || true

  for part in "${parts[@]}"; do
    img="${part}.img"
    [[ -f "$img" ]] || continue
    target="$part${slot:+_$slot}"
    if ! fastboot getvar "is-logical:${target}" 2>&1 | grep -qiE "^is-logical:${target}:[[:space:]]*yes"; then
      log_event warn FastbootdTest "$target" Skip "not a logical partition on this device"
      continue
    fi
    log_start FastbootdTest Flash "$img -> $target"
    # The fastboot client appends the current slot itself (system ->
    # system_a); passing system_a would write system_a_a.
    if ! fastboot flash "$part" "$img"; then
      log_fail FastbootdTest "$part" "fastboot flash failed"
      return 1
    fi
    flashed=$((flashed + 1))
  done

  if ((flashed == 0)); then
    log_fail FastbootdTest Flash "no super partition image flashed"
    return 1
  fi
  log_done FastbootdTest Flash "$flashed partition(s)"
  fastboot reboot || return 1
}

# buildtest source for a codename or URL. Sets buildtest_new_rom=true when a
# ROM was downloaded and extracted (it should be backed up), false when the
# workspace came from a backup.
prepare_buildtest_source() {
  local target="$1"
  local builder="$2"
  local backup_dir=""

  buildtest_new_rom=false
  [[ "$target" =~ ^https?:// ]] || rom_source_target="${target,,}"
  if resolve_rom_source "$target"; then
    backup_dir="$(find_backup_by_source "$rom_source_id")"
    if [[ -n "$backup_dir" ]]; then
      log_event info BuildTest Source "" "backup $(basename -- "$backup_dir") holds the newest ROM; download skipped"
      restore_backup_dir "$backup_dir" && return 0
      log_warn BuildTest Source "backup could not be restored; downloading the ROM"
      clean || return 1
    else
      log_event info BuildTest Source "" "newer ROM $rom_source_id; downloading"
    fi
    download_resolved_rom || return 1
  elif [[ -n "$rom_source_target" ]]; then
    log_warn BuildTest Source "server lookup failed; using the latest backup of $target"
    restore_latest_backup "$target"
    return
  else
    return 1
  fi

  detect "$builder" "$target" || return 1
  extract || return 1
  buildtest_new_rom=true
}

cmd_buildtest() {
  local target=""
  local builder="LocalBuild"
  local rom_path
  local do_sync=false
  local do_backup=false
  local no_backup=false
  local arg

  for arg in "$@"; do
    case "$arg" in
    --sync) do_sync=true ;;
    --backup) do_backup=true ;;
    --no-backup) no_backup=true ;;
    --*)
      echo "Unknown buildtest option: $arg"
      return 1
      ;;
    *)
      if [[ -z "$target" ]]; then
        target="$arg"
      else
        builder="$arg"
      fi
      ;;
    esac
  done

  require_rom_target "$target" || return 1
  rom_path="$(local_rom_path "$target")"
  [[ -n "$rom_path" ]] && target="$rom_path"
  clean --keep "$(local_rom_keep "$rom_path")" || return 1
  rom_source_id="" rom_source_target=""
  if [[ -n "$target" && -z "$rom_path" ]]; then
    # Same as build: the newest ROM is looked up on the server; a backup that
    # holds it is restored, a newer one is downloaded and backed up in place of
    # the older backup of this device.
    prepare_buildtest_source "$target" "$builder" || return 1
    [[ "$no_backup" == "true" ]] && buildtest_new_rom=false
    [[ "$buildtest_new_rom" == "true" ]] && do_backup=true
  else
    prepare_test_source "$target" "$builder" "build" || return 1
  fi
  [[ -f "$dir/info" ]] && source "$dir/info"
  if [[ "$do_sync" == "true" ]]; then
    cmd_sync || return 1
  fi
  if [[ "$do_backup" == "true" ]]; then
    if [[ "${GITHUB_ACTIONS:-false}" == "true" && -z "${VS_BACKUP_DIR:-}" ]]; then
      log_event warn Backup "GitHub Actions" Skip "disabled in CI"
    else
      backup || return 1
    fi
  fi
  builder="LocalBuild"
  patchvs "$builder" || return 1
  supertest
}

cmd_stocktest() {
  local target=""
  local builder="StockBuild"
  local do_sync=false
  local do_backup=false
  local arg

  for arg in "$@"; do
    case "$arg" in
    --sync) do_sync=true ;;
    --backup) do_backup=true ;;
    --*)
      echo "Unknown stocktest option: $arg"
      return 1
      ;;
    *)
      if [[ -z "$target" ]]; then
        target="$arg"
      else
        builder="$arg"
      fi
      ;;
    esac
  done

  prepare_test_source "$target" "$builder" "stock" || return 1
  [[ -f "$dir/info" ]] && source "$dir/info"
  if [[ "$do_sync" == "true" ]]; then
    cmd_sync || return 1
  fi
  if [[ "$do_backup" == "true" ]]; then
    backup || return 1
  fi
  supertest
}

show_usage() {
  cat <<EOF
Usage:
  bhlnk build <code|url|rom.zip> [builder] [--restore|--download] [--backup|--no-backup] [--no-upload]
                        VS ROM: clean -> download -> detect -> extract -> backup -> patch
                        -> pack -> upload. One signed, locked payload.bin OTA zip (ota_out/)
                        for both Mushrom OTA and MushromTool fastboot installs; a local rom.zip
                        skips clean/download. A codename or URL is looked up on the
                        server first: if a backup holds that same ROM extracted, the
                        build starts from it; a newer ROM is downloaded and backed up
                        in its place (--download always downloads; --restore uses the
                        latest backup without asking the server). Backups live in
                        VS_BACKUP_DIR (default backup/), at most VS_BACKUP_MAX_GB
                        (default 300), least recently used removed first
  bhlnk stock <code|url|rom.zip> [region] [builder] [--region REGION] [--backup] [--a|--b]
                        stock ROM: the untouched images as an unlocked payload.bin OTA zip
                        (ota_out/) that MushromTool flashes without a key or coin; a ColorOS
                        codename builds the newest firmware of the region
  bhlnk check <code> [builder]
                        list ColorOS firmware and select one to build
  bhlnk buildtest [code|url|rom.zip] [--sync] [--backup|--no-backup]
                        clean, restore/download, patch and build supertest.img. Like
                        build, a codename or URL is looked up on the server: a backup
                        holding that ROM is restored, a newer ROM is downloaded and
                        backed up in place of the older backup (--no-backup skips it;
                        the latest backup is used if the server lookup fails)
  bhlnk stocktest [code] [--sync] [--backup]
                        stock build supertest.img, prefer latest backup if no ROM archive
  bhlnk supertest [--a|--b]
                        build supertest.img for the slot (default: active slot read via
                        ADB/fastboot, else a); reboot to bootloader (from ADB or fastbootd),
                        set the active slot, then flash super
  bhlnk flash_newtest [--a|--b] [--dry-run]
                        flash super as sparse pieces composed from LP metadata plus the
                        partition images (no super.img); --dry-run verifies without a device
  bhlnk fastbootdtest [partition ...]
                        rebuild images, boot fastbootd and flash the super partitions
                        one by one (default: all with an image in firmware-update)
  bhlnk flashsuper [super.img] [--a|--b]
                        flash an existing super image without rebuilding (default:
                        <device>/firmware-update/super.img, then supertest.img; slot a)
  bhlnk i [args]        detect only
  bhlnk ota [args]      detect OTA
  bhlnk download <code> [region]
                        download latest firmware; explicit region overrides config
  bhlnk download-only <code|url> [region]
                        download and rename the ROM archive without extracting it
  bhlnk flash <partition> [partition...]
                        build and flash only those partitions (no super rebuild)
  bhlnk flash super [--a|--b]
                        rebuild images from img_temp, build super.img for the slot
                        (default: a), then flash it from the bootloader
  bhlnk t [f]           legacy full-image flash flow
  bhlnk upload [custom] [--gdrive|--onedrive]
                        upload final package to GoogleDrive (build packages go to PAID_OTA)
  bhlnk ut [--gdrive|--onedrive]
                        upload final package to GoogleDrive TESTBUILD
  bhlnk sync            sync backend modules
  bhlnk clean           remove build/download temp files for next session
  bhlnk x9bl <lk.img> [output.img] [--dry-run]
                        patch LK fastboot gates for Find X9/X9 Pro
  bhlnk initro <init> [output] [--dry-run]
                        patch init to update existing ro.* properties
  bhlnk recovery-adb <recovery.img> [output.img] [--dry-run] [--force]
                        enable unauthenticated root ADB in a recovery image
  bhlnk reslot <super.img> <a|b> [output.img] [--force]
                        relabel an already-built super.img to the given slot; patches only the
                        liblp metadata in place (no partition data touched) when possible,
                        falling back to unpack+rebuild otherwise. Pass the same path as
                        output.img (with --force) to edit the file in place.
  bhlnk patch <fn> ...  call a shell function
  bhlnk <fn> [mode] [l|--log] [s|--sign] [i|--install]
                        call a shell function; optionally log, sign, and adb-install APK
  bhlnk pack            pack patched ROM as a payload.bin OTA zip signed with bin/ota_keys,
                        plus <name>-magisk.zip, a partial OTA holding only the Magisk-patched boot,
                        e.g. bhlnk extract patchvs pack
  bhlnk imgota <img_dir> [--meta <json|payload.bin|ota.zip>] [--out <zip>]
               [--device <code>] [--timestamp <unix>] [--set K=V]
                        build a signed full OTA zip from a folder of .img files
                        (layout from ota_meta.json by default; super.img is unpacked
                        for dynamic partitions without their own image)
  bhlnk extract patchvs pack
                        run shell functions in order

Short flags still work: -i, -ota, -t, -u, -ut, -s, -p.
You can also chain simple commands, for example: bhlnk i t
EOF
}

normalize_cmd() {
  case "$1" in
  -h | --help | help) echo "help" ;;
  -ut | ut | upload-test | test-upload) echo "upload_test" ;;
  -u | u | up | upload) echo "upload" ;;
  -s | s | sync) echo "sync" ;;
  c | clean) echo "clean" ;;
  -t | t | flash) echo "flash" ;;
  dl | download) echo "download" ;;
  download-only | download_only | downloadonly) echo "download_only" ;;
  ck | check) echo "check" ;;
  -i | i | init | detect) echo "detect" ;;
  -ota | ota | detect-ota) echo "detect_ota" ;;
  -p | p | patch | call | run) echo "patch_call" ;;
  x9bl | patch-x9bl | patch_x9bl) echo "patch_x9bl" ;;
  initro | patch-init-ro | patch_init_ro) echo "patch_init_ro" ;;
  recovery-adb | recovery_adb | patch-recovery-adb | patch_recovery_adb) echo "patch_recovery_adb" ;;
  b | build) echo "build" ;;
  stock | st) echo "stock" ;;
  buildtest | bt) echo "buildtest" ;;
  stocktest | stt) echo "stocktest" ;;
  supertest | spt) echo "supertest" ;;
  flash_newtest | flash-newtest | fnt) echo "flash_newtest" ;;
  fastbootdtest | fbdt | fastbootd-test) echo "fastbootdtest" ;;
  flashsuper | flash-super | flash_super | fs) echo "flashsuper" ;;
  reslot) echo "reslot" ;;
  imgota | img-ota | img_ota | io) echo "imgota" ;;
  *) echo "" ;;
  esac
}

run_cmd() {
  local raw="$1"
  local cmd
  shift || true
  cmd="$(normalize_cmd "$raw")"

  case "$cmd" in
  help) show_usage ;;
  upload_test) cmd_upload_test "$@" ;;
  upload) cmd_upload "$@" ;;
  sync) cmd_sync "$@" ;;
  clean) clean "$@" ;;
  flash) cmd_flash "$@" ;;
  download) download "$@" ;;
  download_only) cmd_download_only "$@" ;;
  check) cmd_check "$@" ;;
  detect) detect "$@" ;;
  detect_ota) detect_ota "$@" ;;
  patch_call) cmd_patch_call "$@" ;;
  patch_x9bl) cmd_patch_x9bl "$@" ;;
  patch_init_ro) cmd_patch_init_ro "$@" ;;
  patch_recovery_adb) cmd_patch_recovery_adb "$@" ;;
  build) cmd_build "$@" ;;
  stock) cmd_stock "$@" ;;
  buildtest) cmd_buildtest "$@" ;;
  stocktest) cmd_stocktest "$@" ;;
  supertest) supertest "$@" ;;
  flash_newtest) flash_newtest "$@" ;;
  fastbootdtest) fastbootdtest "$@" ;;
  flashsuper) cmd_flashsuper "$@" ;;
  reslot) cmd_reslot "$@" ;;
  imgota) cmd_imgota "$@" ;;
  "")
    if declare -F -- "$raw" >/dev/null; then
      cmd_patch_call "$raw" "$@"
    else
      echo "Unknown command: $raw"
      show_usage
      return 1
    fi
    ;;
  esac
}

is_known_cmd() {
  [[ -n "$(normalize_cmd "$1")" ]]
}

is_runnable_cmd() {
  is_known_cmd "$1" || declare -F -- "$1" >/dev/null
}

if [[ $# -eq 0 ]]; then
  show_usage
elif [[ $# -gt 1 ]]; then
  all_commands=true
  for arg in "$@"; do
    if ! is_runnable_cmd "$arg"; then
      all_commands=false
      break
    fi
  done

  if [[ "$all_commands" == "true" ]]; then
    for arg in "$@"; do
      run_cmd "$arg"
    done
  else
    run_cmd "$@"
  fi
else
  run_cmd "$@"
fi
