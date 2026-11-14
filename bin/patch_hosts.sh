#!/bin/bash
set -euo pipefail

# Patch system/etc/hosts trong cây ROM để chặn quảng cáo lúc build.
# Domain cần chặn được cấu hình trong file domain-list (mặc định:
# bin/adblock_domains.txt), mỗi dòng một domain, dòng bắt đầu bằng '#'
# hoặc dòng trống sẽ bị bỏ qua.
#
# Usage: patch_hosts.sh <rom_root> [domains_file]

dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! declare -F log_event >/dev/null 2>&1 && [[ -f "$dir/bin/logging.sh" ]]; then
	source "$dir/bin/logging.sh"
fi

root="${1:-.}"
domains_file="${2:-$dir/bin/adblock_domains.txt}"
redirect_ip="${VS_ADBLOCK_REDIRECT_IP:-0.0.0.0}"
block_tag="# vs-adblock"
verbose="${VS_VERBOSE_LOG:-0}"

if [ ! -d "$root" ]; then
	echo "ROM root not found: $root" >&2
	exit 1
fi

if [ ! -f "$domains_file" ]; then
	log_fail HostsPatch "$domains_file" "domain list not found"
	exit 1
fi

target="$(find "$root" -type f -path '*/etc/hosts' | sort | head -n 1)"

if [ -z "$target" ] || [ ! -f "$target" ]; then
	log_fail HostsPatch "" "không tìm thấy system/etc/hosts trong: $root"
	exit 1
fi

log_start HostsPatch "${target#"$root"/}"

mapfile -t domains < <(sed -e 's/#.*$//' -e 's/[[:space:]]*$//' -e '/^[[:space:]]*$/d' "$domains_file")

if [ "${#domains[@]}" -eq 0 ]; then
	log_event info HostsPatch "" Skip "domain list rỗng, không có gì để patch"
	exit 0
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

# Bỏ các dòng đã gắn block_tag để lần patch này idempotent, rồi thêm lại
# đúng danh sách hiện tại của domains_file.
grep -v -F "$block_tag" "$target" > "$tmp" || true

added=0
skipped=0
for domain in "${domains[@]}"; do
	line="$redirect_ip $domain $block_tag"
	if grep -Fxq "$line" "$tmp"; then
		skipped=$((skipped + 1))
		continue
	fi
	echo "$line" >> "$tmp"
	added=$((added + 1))
done

cp "$tmp" "$target"

if [ "$verbose" = "1" ]; then
	log_event info HostsPatch "" Info "added=$added skipped=$skipped total=${#domains[@]}"
fi

log_done HostsPatch "${target#"$root"/}" "$added domain(s) added"
