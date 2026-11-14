#!/usr/bin/env bash
# Run .github/workflows/build.yml locally with nektos/act.
#
#   .github/act/run-local.sh <ROM_URL|codename> [vs|stock|stock-encrypted] [region] [act args...]
#
# Environment overrides:
#   ACT_PUBLISH=true     really upload + send Telegram (default: skipped)
#   ACT_MEMORY=16g       container RAM limit, matches the GitHub runner
#   ACT_SWAP=24g         RAM+swap limit (runner has 8g swap from Maximize disk)
#   ACT_IMAGE=...        runner image (default catthehacker/ubuntu:act-24.04)
#   ACT_STAGE=...        where the clean source copy is staged
#
# The working tree (including uncommitted edits) is copied to a staging dir
# with only the files the build needs, so local-only data such as socrates/
# or built .7z files never gets copied into the container.

set -euo pipefail

usage() {
  sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
}

[[ $# -ge 1 ]] || usage
rom_url="$1"
mode="${2:-vs}"
region="${3:-cn}"

case "$mode" in
  vs|stock|stock-encrypted) ;;
  *) echo "Unknown mode: $mode"; usage ;;
esac

repo="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
stage="${ACT_STAGE:-$HOME/.cache/vs-act/src}"
image="${ACT_IMAGE:-catthehacker/ubuntu:act-24.04}"
memory="${ACT_MEMORY:-16g}"
swap="${ACT_SWAP:-24g}"
publish="${ACT_PUBLISH:-false}"
act_bin="$(command -v act || echo "$HOME/.local/bin/act")"

[[ -x "$act_bin" ]] || { echo "act not found; install from https://github.com/nektos/act"; exit 1; }
docker info >/dev/null 2>&1 || { echo "docker is not running or not accessible"; exit 1; }

# Same top-level entries bhlnk clean() keeps; everything else is local state.
keep_top=(.github .gitignore README.md bhlnk bin backend fastboot module)

echo "[act] Staging source into $stage"
mkdir -p "$stage"
file_list="$(mktemp)"
trap 'rm -f "$file_list"' EXIT
(
  cd "$repo"
  git ls-files -z -c -o --exclude-standard -- "${keep_top[@]}" \
    | while IFS= read -r -d '' f; do if [[ -e "$f" ]]; then printf '%s\0' "$f"; fi; done
) >"$file_list"
rsync -a --from0 --files-from="$file_list" "$repo/" "$stage/"
# rsync --files-from does not prune removed files; drop source files that no
# longer exist. Build caches (.android-sdk, .gradle, ...) are left in place.
(
  cd "$stage"
  comm -23 \
    <(find "${keep_top[@]}" -type f 2>/dev/null | sort) \
    <(tr '\0' '\n' <"$file_list" | sort) \
    | while IFS= read -r f; do rm -f -- "$f"; done
)

# act needs a git repo for github.* context; mirror the current commit subject.
if [[ ! -d "$stage/.git" ]]; then
  git -C "$stage" init -q
  git -C "$stage" remote add origin "$(git -C "$repo" remote get-url origin)"
fi
branch="$(git -C "$repo" rev-parse --abbrev-ref HEAD)"
git -C "$stage" checkout -q -B "$branch"
git -C "$stage" add -A
git -C "$stage" -c user.name=act -c user.email=act@localhost \
  commit -q --allow-empty -m "$(git -C "$repo" log -1 --pretty=format:%s) (local act)"

event="$stage/.git/act-event.json"
cat >"$event" <<EOF
{
  "inputs": {
    "ROM_URL": $(printf '%s' "$rom_url" | python3 -c 'import json,sys;print(json.dumps(sys.stdin.read()))'),
    "mode": "$mode",
    "branch": "$branch",
    "region": "$region",
    "key": "local-act",
    "trigger": "LocalAct"
  }
}
EOF

echo "[act] mode=$mode region=$region branch=$branch publish=$publish memory=$memory"
echo "[act] Free space for Docker ($(docker info --format '{{.DockerRootDir}}')):"
df -h "$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /)" | tail -1

# Only pull the runner image the first time; it is several GB.
pull=true
docker image inspect "$image" >/dev/null 2>&1 && pull=false

cd "$stage"
exec "$act_bin" workflow_dispatch \
  -W .github/workflows/build.yml \
  -e "$event" \
  -P "ubuntu-latest=$image" \
  --pull="$pull" \
  -s AUTH_KEY=local-act \
  --env "VS_NO_PUBLISH=$([[ "$publish" == "true" ]] && echo false || echo true)" \
  --container-options "--privileged --memory=$memory --memory-swap=$swap" \
  --artifact-server-path "$HOME/.cache/vs-act/artifacts" \
  "${@:4}"
