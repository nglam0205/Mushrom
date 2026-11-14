#!/usr/bin/env bash
# Local build farm: the "Run build" part of .github/workflows/build.yml on this
# machine, several ROMs at once. Tools and module files are already installed
# here, so a job is only: download ROM -> ./bhlnk <mode> -> upload.
#
#   .github/local/farm.sh run [opts] [rom...]   start the worker pool
#   .github/local/farm.sh add [opts] <rom...>   queue jobs (pool may be running)
#   .github/local/farm.sh add -f jobs.txt       one job per line: rom [mode] [region] [builder]
#   .github/local/farm.sh status                slots, queue, recent results
#   .github/local/farm.sh logs <slot>           follow the running job of a slot
#   .github/local/farm.sh cancel <job id>       drop a queued job / stop a running one
#   .github/local/farm.sh stop                  finish running jobs, then exit
#   .github/local/farm.sh update                pull the checked-out branch, then restart the
#                                               service once no job is running
#   .github/local/farm.sh serve [--host H] [--port 8770]   HTTP API (farm_api.py)
#
# Options (run/add):
#   -m, --mode <vs|stock>                            default vs
#   -r, --region <cn|...>                            stock of a ColorOS codename, default cn
#       --branch <PREVIEW|STABLE>                    tool branch, default the checked-out one
#   -b, --builder <name>                             default LocalFarm
#       --super-size <bytes>                         VS_SUPER_SIZE: force the super (and group)
#                                                    size, for a device with a resized super
# Options (run):
#   -j, --jobs <n>      parallel slots (default 4; slots= in $VS_FARM_HOME/config wins)
#   -w, --watch         keep waiting for new `add`ed jobs instead of exiting
#       --no-publish    VS_NO_PUBLISH=true: skip upload + Telegram
#       --keep-failed   leave a failed job's workspace in place (uses disk)
#
# Environment:
#   VS_FARM_HOME=~/.cache/vs-farm   queue, slot workspaces, logs
#   VS_FARM_MIN_FREE_GB=30          a slot waits for this much free disk before starting
#   VS_FARM_PULL=false              build the checkout as it is, without pulling first
#   VS_FARM_UNIT=vs-farm.service    the systemd user unit that runs the pool and the API
#                                   (`update` restarts it)
#   VS_OTA_REMOTE=/data             OTA and stock packages are copied to this directory
#                                   instead of GoogleDrive (bhlnk upload); the API serves
#                                   them from it
#   VS_OTA_PRE_HOURS=24             a PRE package in $VS_OTA_REMOTE/PAID_OTA is removed this
#                                   long after it was built (0 = never); ST packages stay
#   VS_BACKUP_DIR=$VS_FARM_HOME/rom-backup
#                                   extracted ROMs shared by all slots: a job whose ROM is
#                                   already there skips download + extract (bhlnk build)
#   VS_BACKUP_MAX_GB=300            size limit of that store
#   VS_OTA_IO_SLOTS=1               how many slots write their OTA package at once; the
#                                   others wait at that step (bin/ota_payload.py io_turn)
#                                   while extraction, patching and compression run in
#                                   parallel, so packaging does not saturate the disk
#
# bhlnk treats $(pwd) as its workspace, so every slot gets its own copy of
# bhlnk/bin/backend/fastboot/module (reflinked: free on btrfs) and builds there.
# The working tree is copied as-is, uncommitted edits included.
#
# Before a job is staged its branch is fast-forwarded to origin, so every build
# uses the newest pushed tool. A pull that cannot fast-forward (offline, local
# commits, an uncommitted edit to a file the pull changes) is a warning and the
# job builds the checkout as it is. The exception is bin/rclone.conf, which
# rclone rewrites by itself: there the pushed copy wins and the local one is
# saved in $VS_FARM_HOME/backup. A pool that is already running keeps its
# own farm.sh until it is restarted; bhlnk and the other tool files are new at once.
#
# `update` is that restart, asked for by the API project after each of its
# deploys (farm_api.py POST /update): the checked-out branch is pulled, the
# slots take no new job, and when the running ones are done the pool restarts
# $VS_FARM_UNIT, which starts the new farm.sh and farm_api.py. Queued jobs stay
# queued for the new pool.
#
# bhlnk calls sudo. Started from a terminal the farm asks once and keeps the
# ticket alive; for cron/systemd/nohup add a NOPASSWD sudoers rule instead.

set -uo pipefail

repo="${VS_FARM_REPO:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel)}"
farm="${VS_FARM_HOME:-$HOME/.cache/vs-farm}"
min_free_gb="${VS_FARM_MIN_FREE_GB:-30}"
queue="$farm/queue"
history="$farm/history.tsv"
stop_flag="$farm/stop"
restart_flag="$farm/restart"
farm_unit="${VS_FARM_UNIT:-vs-farm.service}"
tool_items=(bhlnk bin backend fastboot module)
# Tracked files a build machine rewrites by itself: rclone re-encrypts its
# config whenever it saves a refreshed token, so every machine's copy differs.
volatile_items=(bin/rclone.conf)

mode="vs"
region="cn"
branch=""
builder="LocalFarm"
super_size=""
jobs=4
watch=false
no_publish=false
keep_failed=false
jobs_file=""
roms=()

usage() {
  sed -n '2,37p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
}

die() {
  echo "farm: $*" >&2
  exit 1
}

say() {
  echo "[$(date +%H:%M:%S)] $*" >&2
}

valid_mode() {
  case "$1" in
    vs|stock) return 0 ;;
  esac
  return 1
}

# Empty means the size of the stock payload.
valid_super_size() {
  [[ -z "$1" ]] && return 0
  [[ "$1" =~ ^[1-9][0-9]{0,14}$ ]] && (( $1 % 4096 == 0 ))
}

parse_opts() {
  while (( $# > 0 )); do
    case "$1" in
      -m|--mode) mode="${2:-}"; shift ;;
      -r|--region) region="${2:-}"; shift ;;
      --branch) branch="${2:-}"; shift ;;
      -b|--builder) builder="${2:-}"; shift ;;
      --super-size) super_size="${2:-}"; shift ;;
      -j|--jobs) jobs="${2:-}"; shift ;;
      -f|--file) jobs_file="${2:-}"; shift ;;
      -w|--watch) watch=true ;;
      --no-publish) no_publish=true ;;
      --keep-failed) keep_failed=true ;;
      -h|--help) usage ;;
      -*) die "unknown option: $1" ;;
      *) roms+=("$1") ;;
    esac
    shift
  done
  valid_mode "$mode" || die "unknown mode: $mode"
  [[ "$jobs" =~ ^[1-9][0-9]*$ ]] || die "invalid --jobs: $jobs"
  valid_super_size "$super_size" || die "invalid --super-size: $super_size (bytes, 4096-aligned)"
}

current_branch() {
  git -C "$repo" branch --show-current 2>/dev/null
}

# The build journal, one line per finished job (TAB separated):
# id, result, seconds, mode, rom, log, branch, builder, finished (epoch).
log_history() {
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$1" "$2" "$3" "$4" "$5" "$farm/logs/$1.log" "${6:--}" "${7:--}" "$(date +%s)" >>"$history"
}

new_job_id() {
  printf '%s-%04x' "$(date +%y%m%d-%H%M%S)" "$RANDOM"
}

# Queue lines are TAB separated: rom, mode, region, builder, id, branch,
# super size (empty: stock).
# Prints the job id on stdout (farm_api.py reads it).
enqueue() {
  local rom="$1" job_mode="$2" job_region="$3" job_builder="$4" job_branch="${5:-}" job_super="${6:-}" id
  valid_mode "$job_mode" || die "unknown mode for $rom: $job_mode"
  valid_super_size "$job_super" || die "invalid super size for $rom: $job_super"
  [[ -n "$job_branch" ]] || job_branch="$(current_branch)"
  git -C "$repo" rev-parse --verify --quiet "refs/heads/$job_branch" >/dev/null \
    || die "no local branch '$job_branch' in $repo"
  id="$(new_job_id)"
  # A local archive is resolved now; the worker runs from another directory.
  [[ -f "$rom" ]] && rom="$(realpath -- "$rom")"
  (
    flock 9
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$rom" "$job_mode" "$job_region" "$job_builder" "$id" "$job_branch" "$job_super" >>"$queue"
  ) 9>>"$queue.lock"
  say "queued $id [$job_mode $job_branch] $rom${job_super:+ super=$job_super}"
  echo "$id"
}

enqueue_requested() {
  local rom line
  local -a fields
  for rom in "${roms[@]}"; do
    enqueue "$rom" "$mode" "$region" "$builder" "$branch" "$super_size"
  done
  [[ -n "$jobs_file" ]] || return 0
  [[ -f "$jobs_file" ]] || die "jobs file not found: $jobs_file"
  while IFS= read -r line || [[ -n "$line" ]]; do
    read -r -a fields <<<"$line"
    [[ ${#fields[@]} -eq 0 || "${fields[0]}" == \#* ]] && continue
    enqueue "${fields[0]}" "${fields[1]:-$mode}" "${fields[2]:-$region}" "${fields[3]:-$builder}" "${fields[4]:-$branch}" "$super_size"
  done <"$jobs_file"
}

pop_job() {
  (
    flock 9
    local line=""
    while [[ -s "$queue" ]]; do
      line="$(head -n 1 "$queue")"
      sed -i 1d "$queue"
      [[ -n "$line" && "$line" != \#* ]] && break
      line=""
    done
    printf '%s' "$line"
  ) 9>>"$queue.lock"
}

wipe_slot() {
  local slot_dir="$1"
  [[ "$slot_dir" == "$farm/slots/"* ]] || return 1
  [[ -e "$slot_dir" ]] || return 0
  rm -rf -- "$slot_dir" 2>/dev/null || sudo rm -rf -- "$slot_dir"
}

pull_branch() {
  GIT_TERMINAL_PROMPT=0 timeout 300 git -C "$repo" pull --ff-only --quiet origin "$1" 2>&1
}

# Fast-forwards the job's branch to origin: `pull` for the checked-out branch,
# a ref update for any other (run_job lays that one over the slot with git
# archive). Never merges, rebases or stashes, so local work is left alone.
# Fails when the pull did; a job builds the checkout as it is then.
update_repo() {
  local who="$1" job_branch="$2" before after out rc item
  [[ "${VS_FARM_PULL:-true}" == "false" ]] && return 0
  (
    flock 9
    before="$(git -C "$repo" rev-parse --quiet --verify "refs/heads/$job_branch")"
    if [[ "$job_branch" == "$(current_branch)" ]]; then
      out="$(pull_branch "$job_branch")"
      rc=$?
      # A local edit to a volatile file blocks the pull whenever the push
      # changes that file too. The pushed copy wins.
      if (( rc != 0 )) && ! git -C "$repo" diff --quiet -- "${volatile_items[@]}"; then
        mkdir -p "$farm/backup"
        for item in "${volatile_items[@]}"; do
          cp -p -- "$repo/$item" "$farm/backup/$(basename "$item").$(date +%y%m%d-%H%M%S)" 2>/dev/null
        done
        git -C "$repo" checkout -- "${volatile_items[@]}"
        say "$who: local ${volatile_items[*]} replaced by the pushed one (old copy in $farm/backup)"
        out="$(pull_branch "$job_branch")"
        rc=$?
      fi
    else
      out="$(GIT_TERMINAL_PROMPT=0 timeout 300 git -C "$repo" fetch --quiet origin "$job_branch:$job_branch" 2>&1)"
      rc=$?
    fi
    if (( rc != 0 )); then
      say "$who: git pull of $job_branch failed, the checkout stays as it is: $(tr '\n' ' ' <<<"$out" | cut -c1-240)"
      exit 1
    fi
    after="$(git -C "$repo" rev-parse --quiet --verify "refs/heads/$job_branch")"
    [[ "$before" == "$after" ]] || say "$who: $job_branch updated ${before:0:7} -> ${after:0:7}"
  ) 9>>"$farm/git.lock"
}

# Slots start one at a time so the free-space check sees the previous slot's
# ROM download rather than four empty workspaces.
stage_slot() {
  local slot="$1" slot_dir="$2" free_gb waited=0 item
  (
    flock 9
    while :; do
      free_gb="$(df -BG --output=avail "$farm" | tail -1 | tr -dc '0-9')"
      (( free_gb >= min_free_gb )) && break
      [[ -e "$stop_flag" ]] && exit 1
      (( waited % 30 == 0 )) && say "slot $slot: waiting for disk (${free_gb}G free, need ${min_free_gb}G)"
      waited=$((waited + 1))
      sleep 10
    done
    wipe_slot "$slot_dir" || exit 1
    mkdir -p "$slot_dir" || exit 1
    for item in "${tool_items[@]}"; do
      cp -a --reflink=auto -- "$repo/$item" "$slot_dir/" || exit 1
    done
    sleep 5
  ) 9>>"$farm/stage.lock"
}

# Same environment the workflow gives the build. A backup kept in the slot
# would be wiped with it, so VS_BACKUP_DIR points bhlnk at a store all slots
# share; with it set, GITHUB_ACTIONS no longer turns the backup off.
# Reads the job variables of the calling run_job.
slot_bhlnk() {
  (
    cd "$slot_dir" || exit 1
    mkdir -p .tmp && chmod 700 .tmp
    export ROM_URL="$rom" BUILD_MODE="$job_mode" ROM_REGION="$job_region" BUILDER_NAME="$job_builder"
    export NOTIFY_ACTION_LINK="https://vsteam.vn" NOTIFY_RUN_ID="local-$id" NOTIFY_GITLOG="$gitlog"
    export TOOL_BRANCH="$job_branch"
    # A forced super size replaces the one read from the stock payload.
    [[ -z "$job_super" ]] || export VS_SUPER_SIZE="$job_super"
    export GITHUB_ACTIONS=true CI_LOW_DISK=true VS_NO_PUBLISH="$no_publish"
    export VS_BACKUP_DIR="${VS_BACKUP_DIR:-$farm/rom-backup}"
    # Backend modules are copied from this folder instead of Google Drive
    # (bin/partutils.sh sync, bhlnk sync).
    export VS_MODULE_DIR="${VS_MODULE_DIR:-/data/MODULE}"
    export TMPDIR="$slot_dir/.tmp" _JAVA_OPTIONS="-Djava.io.tmpdir=$slot_dir/.tmp"
    # Slots take turns at the disk-heavy end of OTA packaging; the slot's .io
    # file says whether it is waiting for its turn or packing (farm_api status).
    export VS_OTA_IO_LOCK="$farm/ota_io.lock" VS_OTA_IO_SLOTS="${VS_OTA_IO_SLOTS:-1}"
    export VS_OTA_IO_STATE="$farm/slots/$slot.io"
    # One slot at a time writes the images out of its stock payload
    # (bin/payload_extract.py); the others wait at that step.
    export VS_EXTRACT_LOCK="$farm/extract.lock"
    exec "${limit_cmd[@]}" ./bhlnk "$@"
  )
}

# Pool settings, editable while the pool runs (dashboard > Build > Local PC):
# $farm/config holds slots=, then either a budget for the whole pool
# (total_cpus=, total_ram_gb=) or fixed limits per slot (cpus=, ram_gb=).
# 0 = no limit; for one resource the pool budget wins over the per-slot limit.
# Without a slots= line the pool uses -j.
load_config() {
  local key value
  cfg_slots="$jobs" cfg_cpus=0 cfg_ram_gb=0 cfg_total_cpus=0 cfg_total_ram_gb=0
  [[ -f "$farm/config" ]] || return 0
  while IFS='=' read -r key value; do
    [[ "$value" =~ ^[0-9]+$ ]] || continue
    case "$key" in
      slots) (( value >= 1 )) && cfg_slots="$value" ;;
      cpus) cfg_cpus="$value" ;;
      ram_gb) cfg_ram_gb="$value" ;;
      total_cpus) cfg_total_cpus="$value" ;;
      total_ram_gb) cfg_total_ram_gb="$value" ;;
    esac
  done <"$farm/config"
}

# Builds run in scopes of this slice of the systemd user manager. The name has
# no dash on purpose: "vs-farm.slice" would be nested under "vs.slice".
farm_slice="vsfarm.slice"

have_scopes() {
  command -v systemd-run >/dev/null && [[ -n "${XDG_RUNTIME_DIR:-}" ]]
}

# Puts the pool budget on the slice, where the kernel shares it between the
# builds that are running: one build alone may use all of it, and whatever a
# build leaves idle goes to the others. Takes effect on running builds too.
apply_budget() {
  local cpu_quota="" ram_high="infinity"
  have_scopes || return 0
  load_config
  (( cfg_total_cpus > 0 )) && cpu_quota="$(( cfg_total_cpus * 100 ))%"
  (( cfg_total_ram_gb > 0 )) && ram_high="${cfg_total_ram_gb}G"
  systemctl --user set-property --runtime "$farm_slice" \
    "CPUQuota=$cpu_quota" "MemoryHigh=$ram_high" 2>/dev/null
}

limits_summary() {
  local cpus ram
  if (( cfg_total_cpus > 0 )); then cpus="shared/$cfg_total_cpus"; else cpus="${cfg_cpus/#0/all}"; fi
  if (( cfg_total_ram_gb > 0 )); then ram="shared/${cfg_total_ram_gb}G"; else ram="${cfg_ram_gb/#0/unlimited}"; fi
  echo "cpus=$cpus ram=$ram"
}

# Fills limit_cmd with the wrapper that applies the limits to a build.
# Pool budget (total_cpus/total_ram_gb): the slice caps the sum, and every
# build is guaranteed its 1/slots part of it when all slots are busy - CPU by
# an equal CPUWeight, RAM by a MemoryLow the other builds cannot reclaim.
# Per-slot CPUs: each slot is pinned to its own block of cores, so nproc inside
# the build (7za, payload tools, delta_generator) sees only that many.
# Per-slot RAM: a cgroup MemoryHigh, past which the build is pushed to swap,
# not killed.
# (Kernel 7.0.0-34 oopses in lruvec_stat_mod_folio during btrfs writeback under
# heavy build I/O with or without these scopes; that needs another kernel.)
slot_limits() {
  local slot="$1" total first i list=""
  local -a props=()
  limit_cmd=()
  load_config
  if (( cfg_total_ram_gb > 0 )); then
    props+=(-p "MemoryLow=$(( cfg_total_ram_gb * 1024 / cfg_slots ))M")
  elif (( cfg_ram_gb > 0 )); then
    props+=(-p "MemoryHigh=${cfg_ram_gb}G")
  fi
  (( cfg_total_cpus > 0 )) && props+=(-p CPUWeight=100)
  if (( ${#props[@]} > 0 )); then
    if have_scopes; then
      apply_budget
      limit_cmd+=(systemd-run --user --scope --quiet --slice "$farm_slice" --unit "vs-farm-$id" "${props[@]}" --)
    else
      say "slot $slot: pool budget and RAM limit skipped (systemd-run --user --scope unavailable)"
    fi
  fi
  total="$(nproc --all)"
  if (( cfg_total_cpus == 0 && cfg_cpus > 0 && cfg_cpus < total )); then
    first=$(( (slot - 1) * cfg_cpus ))
    for (( i = 0; i < cfg_cpus; i++ )); do
      list+="${list:+,}$(( (first + i) % total ))"
    done
    limit_cmd+=(taskset -c "$list")
  fi
}

kill_tree() {
  local child
  for child in $(pgrep -P "$1"); do
    kill_tree "$child"
  done
  kill -TERM "$1" 2>/dev/null
}

run_job() {
  local slot="$1" job="$2"
  local rom job_mode job_region job_builder id job_branch job_super gitlog
  IFS=$'\t' read -r rom job_mode job_region job_builder id job_branch job_super <<<"$job"
  # Hand-written queue lines have no id or branch.
  [[ -n "$id" ]] || id="$(new_job_id)"
  [[ -n "$job_branch" ]] || job_branch="$(current_branch)"
  update_repo "slot $slot" "$job_branch"
  gitlog="$(git -C "$repo" log -1 --pretty=format:%s "$job_branch" 2>/dev/null || echo local)"

  local slot_dir="$farm/slots/$slot"
  local log="$farm/logs/$id.log"
  local state="$farm/slots/$slot.state"
  local cancel_flag="$farm/cancel/$id"
  local start=$SECONDS rc=130 result job_pid job_cancelled=0
  local target="$rom"
  local -a cmd

  say "slot $slot: START $id [$job_mode $job_branch] $rom${job_super:+ super=$job_super}"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$id" "$job_mode" "$(date +%s)" "$rom" "$job_branch" "$job_builder" >"$state"
  # Everything needed to queue this job again (farm_api.py rebuild); the
  # region is in no other record.
  mkdir -p "$farm/logs/$id" && printf '%s=%s\n' rom "$rom" mode "$job_mode" region "$job_region" \
    builder "$job_builder" branch "$job_branch" super_size "$job_super" >"$farm/logs/$id/job"

  if ! stage_slot "$slot" "$slot_dir" || [[ "$cancelled" == "1" && ! -e "$cancel_flag" ]]; then
    say "slot $slot: workspace not prepared, job returned to queue"
    ( flock 9; printf '%s\n' "$job" >>"$queue" ) 9>>"$queue.lock"
    rm -f "$state"
    return 1
  fi

  # The slot is a copy of the working tree. For another branch its committed
  # files are laid over that copy; untracked data (module/update, rclone) stays.
  if [[ "$job_branch" != "$(current_branch)" ]]; then
    if ! git -C "$repo" archive "$job_branch" -- "${tool_items[@]}" | tar -x -C "$slot_dir"; then
      say "slot $slot: FAILED $id - cannot check out branch $job_branch"
      log_history "$id" "FAILED(branch)" 0 "$job_mode" "$rom" "$job_branch" "$job_builder"
      wipe_slot "$slot_dir"
      rm -f "$state"
      return 1
    fi
  fi

  # build moves a local archive into its workspace; give it a private copy.
  if [[ -f "$rom" ]]; then
    cp --reflink=auto -- "$rom" "$slot_dir/" || { rm -f "$state"; return 1; }
    target="$(basename -- "$rom")"
  fi

  case "$job_mode" in
    vs) cmd=(build "$target" "$job_builder") ;;
    stock)
      cmd=(stock "$target" "$job_builder")
      # The region only applies to a ColorOS codename, not to a link or file.
      [[ "$target" =~ ^[A-Za-z]{3}[0-9]{3,4}(IN)?$ ]] && cmd+=(--region "$job_region")
      ;;
  esac

  local -a limit_cmd
  slot_limits "$slot"
  say "slot $slot: limits $(limits_summary)${limit_cmd:+ (${limit_cmd[*]})}"

  # `farm.sh cancel <id>` may have arrived while the slot was being staged.
  if [[ ! -e "$cancel_flag" ]]; then
    slot_bhlnk "${cmd[@]}" >"$log" 2>&1 </dev/null &
    job_pid=$!
    printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' "$id" "$job_mode" "$(date +%s)" "$rom" "$job_branch" "$job_builder" "$job_pid" >"$state"
    wait "$job_pid"
    rc=$?
    # A signal interrupts `wait`. Background jobs ignore Ctrl-C, so stop the
    # build explicitly.
    if [[ "$cancelled" == "1" ]]; then
      kill_tree "$job_pid"
      wait "$job_pid"
    fi
  fi
  if [[ "$cancelled" == "1" || -e "$cancel_flag" ]]; then
    job_cancelled=1
    rc=130
  fi
  if (( rc != 0 )); then
    # Mirror "Push stop notification": -1 cancelled, -2 failed.
    limit_cmd=()
    slot_bhlnk notification "https://vsteam.vn" "$( ((job_cancelled)) && echo -1 || echo -2)" \
      "$gitlog" "local-$id" >>"$log" 2>&1 </dev/null || true
  fi

  if [[ -d "$slot_dir/logs" ]]; then
    mkdir -p "$farm/logs/$id" && cp -a "$slot_dir/logs/." "$farm/logs/$id/" 2>/dev/null
  fi
  # bhlnk's record of the ROM (device, version, package name) outlives the
  # slot, so the job list can still show it.
  if [[ -f "$slot_dir/info" ]]; then
    mkdir -p "$farm/logs/$id" && cp -f "$slot_dir/info" "$farm/logs/$id/info" 2>/dev/null
  fi

  if (( rc == 0 )); then
    result="OK"
  elif (( job_cancelled )); then
    result="CANCELLED"
  else
    result="FAILED($rc)"
  fi
  log_history "$id" "$result" "$((SECONDS - start))" "$job_mode" "$rom" "$job_branch" "$job_builder"
  say "slot $slot: $result $id in $(( (SECONDS - start) / 60 ))m - $log"

  if (( rc != 0 )) && [[ "$keep_failed" == "true" ]]; then
    say "slot $slot: workspace kept at $slot_dir (removed when the slot takes its next job)"
  else
    wipe_slot "$slot_dir"
  fi
  rm -f "$state" "$cancel_flag" "$farm/slots/$slot.io"
  return "$rc"
}

worker() {
  local slot="$1" job
  cancelled=0
  trap 'cancelled=1' TERM
  while [[ "$cancelled" == "0" && ! -e "$stop_flag" ]]; do
    # The slot count was lowered: this slot retires after its current job.
    load_config
    (( slot <= cfg_slots )) || break
    # A pending restart (cmd_update) waits for idle slots: no new job.
    job=""
    [[ "$watch" == "true" && -e "$restart_flag" ]] || job="$(pop_job)"
    if [[ -z "$job" ]]; then
      [[ "$watch" == "true" ]] || break
      sleep 10 &
      wait $!
      continue
    fi
    run_job "$slot" "$job"
  done
}

# The OTA store keeps the newest ST (PAID_OTA/STABLE) and the newest PRE
# (PAID_OTA/PREVIEW) package of a device (bhlnk prune_remote_ota). A PRE build is short-lived: it is removed by age
# as well, so the disk is not filled with previews nobody installs any more.
expire_pre_packages() {
  local store="${VS_OTA_REMOTE:-}" hours="${VS_OTA_PRE_HOURS:-24}" file
  [[ "$store" == /* && -d "$store/PAID_OTA" && "$hours" =~ ^[1-9][0-9]*$ ]] || return 0
  while IFS= read -r file; do
    rm -f -- "$file" && say "expired PRE package: ${file#"$store"/}"
  done < <(find "$store/PAID_OTA" -type f -name '*.PRE.*' -name '*.zip' -mmin "+$((hours * 60))")
}

cmd_run() {
  local slot before failed keepalive_pid stopping=0 ticks=0
  local -A pids=()

  for slot in "${tool_items[@]}"; do
    [[ -e "$repo/$slot" ]] || die "missing $repo/$slot"
  done
  bash -n "$repo/bhlnk" || die "bhlnk has syntax errors"

  exec 8>>"$farm/run.lock"
  flock -n 8 || die "a pool is already running; use '$0 add' to queue more jobs"

  if ! sudo -n true 2>/dev/null; then
    [[ -t 0 ]] || die "sudo needs a password: start from a terminal or add a NOPASSWD rule"
    sudo -v || die "sudo authentication failed"
    # Keep the password ticket alive for the whole run. Not needed (and
    # refused, logging an error every time) under a NOPASSWD rule.
    ( while sleep 50; do sudo -n -v 2>/dev/null; done ) 8>&- &
    keepalive_pid=$!
  fi

  # This pool holds the lock, so any slot state left behind belongs to a pool
  # that died with its builds (crash, power loss): journal them and free the disk.
  local stale id job_mode started rom branch builder
  for stale in "$farm"/slots/*.state; do
    [[ -e "$stale" ]] || continue
    IFS=$'\t' read -r id job_mode started rom branch builder _ <"$stale"
    log_history "$id" "FAILED(crash)" 0 "$job_mode" "$rom" "$branch" "$builder"
    say "recovered: job $id was interrupted by a crash"
    rm -f "$stale" "${stale%.state}.io"
  done
  for stale in "$farm"/slots/*/; do
    [[ -d "$stale" ]] && wipe_slot "${stale%/}"
  done
  rm -f "$farm"/cancel/*

  rm -f "$stop_flag" "$restart_flag"
  enqueue_requested
  before="$(wc -l <"$history")"

  # Starts a worker for every configured slot that has none.
  start_workers() {
    load_config
    for slot in $(seq 1 "$cfg_slots"); do
      [[ -n "${pids[$slot]:-}" ]] && kill -0 "${pids[$slot]}" 2>/dev/null && continue
      worker "$slot" 8>&- &
      pids[$slot]=$!
    done
  }

  load_config
  apply_budget
  say "pool: $cfg_slots slots, $(limits_summary), queue=$(grep -c . "$queue"), watch=$watch, no_publish=$no_publish, home=$farm"
  trap 'say "stopping: cancelling running jobs"; stopping=1; : >"$stop_flag"; kill -TERM "${pids[@]}" 2>/dev/null' INT TERM
  start_workers
  expire_pre_packages
  # A watching pool follows slot-count and budget changes in the config.
  local budget applied_budget="$cfg_total_cpus/$cfg_total_ram_gb"
  while [[ "$watch" == "true" && "$stopping" == "0" && ! -e "$stop_flag" ]]; do
    sleep 10 8>&- &
    wait $!
    [[ "$stopping" == "0" && ! -e "$stop_flag" ]] || continue
    start_workers
    if [[ -e "$restart_flag" ]] && ! compgen -G "$farm/slots/*.state" >/dev/null; then
      restart_pool
    fi
    # Every ten minutes.
    ticks=$((ticks + 1))
    (( ticks % 60 == 0 )) && expire_pre_packages
    # A changed budget reaches the builds that are already running.
    budget="$cfg_total_cpus/$cfg_total_ram_gb"
    [[ "$budget" == "$applied_budget" ]] || { apply_budget; applied_budget="$budget"; }
  done
  # `wait` returns early when a signal arrives; keep waiting for the workers.
  while kill -0 "${pids[@]}" 2>/dev/null; do
    wait "${pids[@]}" 2>/dev/null
  done
  [[ -n "${keepalive_pid:-}" ]] && kill "$keepalive_pid" 2>/dev/null

  echo
  tail -n +"$((before + 1))" "$history" | awk -F'\t' '{printf "%-22s %-12s %4dm  %-6s %s\n", $1, $2, $3/60, $4, $5}'
  failed="$(tail -n +"$((before + 1))" "$history" | awk -F'\t' '$2 != "OK"' | wc -l)"
  (( failed == 0 ))
}

# Restarts the whole unit, so farm_api.py is new as well. A job a slot took
# in this very moment goes back to the queue (run_job, stage_slot).
restart_pool() {
  rm -f "$restart_flag"
  if systemctl --user is-active --quiet "$farm_unit" 2>/dev/null; then
    say "restart: no job running, restarting $farm_unit"
    systemctl --user restart --no-block "$farm_unit"
  else
    say "restart: this pool is not $farm_unit, restart it by hand to load the new farm.sh"
  fi
}

# Pulls the checked-out branch and asks the watching pool to restart.
cmd_update() {
  local rc=0
  update_repo update "$(current_branch)" || rc=1
  : >"$restart_flag"
  say "update: restart requested, the pool restarts when no job is running"
  return "$rc"
}

cmd_status() {
  local state id job_mode started rom slot log last
  echo "home: $farm  free: $(df -h --output=avail "$farm" | tail -1 | tr -d ' ')"
  echo "running:"
  for state in "$farm"/slots/*.state; do
    [[ -e "$state" ]] || continue
    slot="$(basename "$state" .state)"
    IFS=$'\t' read -r id job_mode started rom _ <"$state"
    log="$farm/logs/$id.log"
    last="$(tail -c 4000 "$log" 2>/dev/null | tr '\r' '\n' | sed 's/\x1b\[[0-9;]*[A-Za-z]//g' | grep . | tail -n 1 | cut -c1-90)"
    printf '  slot %s  %4dm  %-6s %s  (%s)\n          %s\n' "$slot" "$(( ($(date +%s) - started) / 60 ))" "$job_mode" "$rom" "$id" "$last"
  done
  echo "queued: $(grep -c . "$queue")"
  awk -F'\t' '{printf "  %-6s %s  (%s)\n", $2, $1, $5}' "$queue"
  echo "recent:"
  tail -n 8 "$history" | awk -F'\t' '{printf "  %-22s %-12s %4dm  %-6s %s\n", $1, $2, $3/60, $4, $5}'
}

# Cancels one job: dropped from the queue, or its build stopped if running.
cmd_cancel() {
  local want="${1:-}" state id job_mode started rom pid removed builder branch
  [[ -n "$want" ]] || die "usage: $0 cancel <job id>"
  removed="$(
    (
      flock 9
      awk -F'\t' -v id="$want" '$5 == id' "$queue"
      awk -F'\t' -v id="$want" '$5 != id' "$queue" >"$queue.tmp" && cat "$queue.tmp" >"$queue"
      rm -f "$queue.tmp"
    ) 9>>"$queue.lock"
  )"
  if [[ -n "$removed" ]]; then
    IFS=$'\t' read -r rom job_mode _ builder _ branch _ <<<"$removed"
    log_history "$want" "CANCELLED" 0 "$job_mode" "$rom" "$branch" "$builder"
    say "cancelled queued job $want"
    return 0
  fi
  for state in "$farm"/slots/*.state; do
    [[ -e "$state" ]] || continue
    IFS=$'\t' read -r id job_mode started rom _ _ pid <"$state"
    [[ "$id" == "$want" ]] || continue
    : >"$farm/cancel/$want"
    [[ -n "$pid" ]] && kill_tree "$pid"
    say "cancelling running job $want"
    return 0
  done
  die "job not found (not queued or running): $want"
}

cmd_logs() {
  local slot="${1:-}" id
  [[ -f "$farm/slots/$slot.state" ]] || die "slot '$slot' is not running a job"
  IFS=$'\t' read -r id _ <"$farm/slots/$slot.state"
  exec tail -n 50 -f "$farm/logs/$id.log"
}

[[ $# -ge 1 ]] || usage
action="$1"
shift
mkdir -p "$farm/slots" "$farm/logs" "$farm/cancel" || die "cannot create $farm"
touch "$queue" "$history"

case "$action" in
  run)
    parse_opts "$@"
    cmd_run
    ;;
  add)
    parse_opts "$@"
    [[ ${#roms[@]} -gt 0 || -n "$jobs_file" ]] || die "add: nothing to queue"
    enqueue_requested
    ;;
  status) cmd_status ;;
  logs) cmd_logs "$@" ;;
  cancel) cmd_cancel "$@" ;;
  update) cmd_update ;;
  serve)
    VS_FARM_HOME="$farm" VS_FARM_REPO="$repo" exec python3 "$(dirname "$0")/farm_api.py" "$@"
    ;;
  stop)
    : >"$stop_flag"
    say "running jobs will finish, queued jobs stay queued (Ctrl-C in the pool cancels now)"
    ;;
  *) usage ;;
esac
