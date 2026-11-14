#!/usr/bin/env python3
"""HTTP front for farm.sh so the API project can queue local builds over the LAN.

Start with `.github/local/farm.sh serve [--host 0.0.0.0] [--port 8770]`.
It only edits the queue; the worker pool is a separate
`.github/local/farm.sh run --watch` (it needs sudo, so it stays in a terminal).

Every request except GET /health needs the shared key, as
`Authorization: Bearer <key>` or `X-Farm-Key: <key>`. The key is DEFAULT_KEY
below (API/backend.py carries the same value) unless VS_FARM_KEY overrides it.

  GET  /health                 {"ok": true}
  GET  /status?recent=20       pool state, branch, free disk, running/queued/recent
  POST /jobs                   {"ROM_URL", "mode", "region", "trigger", "branch", "super_size"}
                               same names as the build.yml inputs -> {"id": ...}
  GET  /jobs/<id>              one job
  GET  /jobs/<id>/log?tail=200 last lines of its log
  POST /jobs/<id>/cancel       drop it from the queue / stop the build
  POST /jobs/<id>/rebuild      queue a finished job again, same ROM/mode/region/branch
                               ({"trigger"} optional); the pool pulls the branch first,
                               so it builds with the newest pushed commit

A running or finished job carries "rom_info" ({"device", "brand", "base",
"android", "package"}) once bhlnk has identified the ROM. A running job also
carries "packing": {"state": "waiting"|"packing", "since": epoch} while it waits
for or holds the OTA packaging turn (bin/ota_payload.py io_turn), else null.
  POST /update                 pull the checked-out branch, then restart the farm service
                               (pool and this API) as soon as no job is running; the API
                               project calls it after each of its deploys
  GET  /config                 pool settings, machine totals and a forecast
  POST /config                 {"slots", "cpus", "ram_gb"} (per slot, 0 = no limit),
                               {"total_cpus", "total_ram_gb"} (shared by the whole pool,
                               0 = off; wins over the per-slot limit of that resource)
  GET  /backups                extracted-ROM backups (bhlnk backup) and the store limit
  GET  /backups/<name>/list?path=dir   one folder of a backup
  GET  /backups/<name>/file?path=file  start of a text file (binary files: size only)
  GET  /backups/<name>/download?path=file  the whole file (honours a Range header)
  GET  /backups/<name>/search?kind=name|text&q=...  the whole backup: files and folders
                               whose name contains q, or text-file lines that contain q

`branch` is a local branch of this machine's VS_Build_Tool checkout (default:
the checked-out one, built with its uncommitted edits).
"""

import argparse
import fcntl
import hmac
import json
import os
import re
import shutil
import stat
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
FARM_SH = HERE / "farm.sh"
FARM = Path(os.environ.get("VS_FARM_HOME") or Path.home() / ".cache/vs-farm")
REPO = Path(os.environ.get("VS_FARM_REPO") or HERE.parent.parent)
# Same default as farm.sh gives bhlnk (VS_BACKUP_DIR).
BACKUPS = Path(os.environ.get("VS_BACKUP_DIR") or FARM / "rom-backup")

DEFAULT_KEY = "SHARE_REDACTED_VALUE"

MODES = {"vs", "stock"}
# The ROM goes to bhlnk as an argument: only a URL or a device codename, never
# a path or an option.
ROM_URL_RE = re.compile(r"^https?://[^\s]+$")
CODENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./-]{0,63}$")
REGION_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")
# Forced super partition size in bytes (bhlnk VS_SUPER_SIZE); empty = stock.
SUPER_SIZE_RE = re.compile(r"^[1-9][0-9]{0,14}$")
JOB_ID_RE = re.compile(r"^[0-9]{6}-[0-9]{6}-[0-9a-z]{1,8}$")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
MAX_BODY = 16 * 1024
# No leading dot: bhlnk's unfinished copies (.tmp-*) and .lock stay hidden.
BACKUP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
MAX_LISTING = 5000
MAX_PREVIEW = 256 * 1024
DOWNLOAD_CHUNK = 1024 * 1024
# A search walks a whole backup, tens of GB of ROM files: it stops after this
# many results or seconds and says so.
MAX_SEARCH_RESULTS = 300
MAX_SEARCH_QUERY = 200
MAX_SEARCH_LINE = 400
SEARCH_SECONDS = 25
SEARCH_HITS_PER_FILE = 20


def load_key():
    return os.environ.get("VS_FARM_KEY", "").strip() or DEFAULT_KEY


def meminfo_gb(field):
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith(field + ":"):
            return round(int(line.split()[1]) / 2**20, 1)
    return 0.0


def read_config():
    """Mirrors load_config in farm.sh: $VS_FARM_HOME/config, key=value lines."""
    config = {"slots": 4, "cpus": 0, "ram_gb": 0, "total_cpus": 0, "total_ram_gb": 0}
    try:
        lines = (FARM / "config").read_text().splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        key, _, value = line.partition("=")
        if key in config and value.isdigit():
            config[key] = int(value)
    config["slots"] = max(1, config["slots"])
    return config


def config_report(config=None):
    """Settings plus what they leave free on this machine."""
    config = config or read_config()
    total_cpus = os.cpu_count() or 1
    total_ram = meminfo_gb("MemTotal")
    # A pool budget is the most the slots can take together.
    cpus_used = config["total_cpus"] or min(total_cpus, config["slots"] * (config["cpus"] or total_cpus))
    ram_used = config["total_ram_gb"] or config["slots"] * config["ram_gb"]
    return {
        **config,
        "machine": {
            "cpus": total_cpus,
            "ram_gb": total_ram,
            "ram_available_gb": meminfo_gb("MemAvailable"),
            "swap_gb": meminfo_gb("SwapTotal"),
            "load_1m": round(os.getloadavg()[0], 1),
        },
        "forecast": {
            "cpus_used": cpus_used,
            "cpus_left": total_cpus - cpus_used,
            # Without a RAM limit a slot can take anything, so there is nothing to forecast.
            "ram_used_gb": ram_used or None,
            "ram_left_gb": round(total_ram - ram_used, 1) if ram_used else None,
        },
    }


def read_rows(path):
    try:
        lines = path.read_text(errors="replace").splitlines()
    except FileNotFoundError:
        return []
    return [line.split("\t") for line in lines if line and not line.startswith("#")]


def pool_running():
    """farm.sh run holds an exclusive lock on run.lock for its lifetime."""
    try:
        with open(FARM / "run.lock", "a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(handle, fcntl.LOCK_UN)
        return False
    except OSError:
        return True


def repo_branch():
    try:
        return subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def repo_head():
    try:
        return subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


# Previous CPU sample per job id, to turn the cgroup's cumulative CPU time
# into a percentage between two polls.
_cpu_samples = {}


CLOCK_TICKS = os.sysconf("SC_CLK_TCK")
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")


def tree_usage(job_id, pid, started):
    """CPU and memory of a build that has no cgroup of its own: summed over the
    job's process tree. CPU of children that already exited is not counted."""
    parents = {}
    stats = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            # comm may contain spaces; the fixed fields start after the last ')'.
            fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            rss_pages = int((entry / "statm").read_text().split()[1])
        except (OSError, IndexError, ValueError):
            continue
        parents[int(entry.name)] = int(fields[1])
        stats[int(entry.name)] = (int(fields[11]) + int(fields[12]), rss_pages)
    if pid not in stats:
        return None
    children = {}
    for child, parent in parents.items():
        children.setdefault(parent, []).append(child)
    tree, todo = [], [pid]
    while todo:
        current = todo.pop()
        tree.append(current)
        todo.extend(children.get(current, []))

    now = time.time()
    used = sum(stats[p][0] for p in tree) / CLOCK_TICKS
    last = _cpu_samples.get(job_id)
    _cpu_samples[job_id] = (now, used)
    percent = (used - last[1]) / (now - last[0]) * 100 if last and now - last[0] >= 1 else 0
    try:
        cpus = len(os.sched_getaffinity(tree[-1]))
    except OSError:
        cpus = os.cpu_count() or 1
    return {
        "cpu_percent": round(max(0.0, percent)),
        "cpus": cpus,
        "ram_gb": round(sum(stats[p][1] for p in tree) * PAGE_SIZE / 2**30, 1),
        "ram_limit_gb": 0,
        "procs": len(tree),
    }


def job_usage(job_id, pid, started):
    """CPU and memory the build is using now: from its vs-farm-<id> scope cgroup
    when it has one (a RAM limit is set), else from its process tree."""
    return cgroup_usage(job_id, started) or (tree_usage(job_id, int(pid), started) if pid.isdigit() else None)


def cgroup_usage(job_id, started):
    """The build's scope is in farm.sh's vsfarm.slice of the user manager
    (app.slice for a build started by an older pool)."""
    uid = os.getuid()
    manager = Path(f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service")
    base = manager / "vsfarm.slice" / f"vs-farm-{job_id}.scope"
    if not base.exists():
        base = manager / "app.slice" / base.name
    try:
        procs = (base / "cgroup.procs").read_text().split()
        stat = dict(line.split() for line in (base / "memory.stat").read_text().splitlines())
        cpu = dict(line.split() for line in (base / "cpu.stat").read_text().splitlines())
        current = int((base / "memory.current").read_text())
        peak = int((base / "memory.peak").read_text()) if (base / "memory.peak").exists() else 0
        high = (base / "memory.high").read_text().strip()
        if not high.isdigit():
            # No limit of its own: the pool budget on the slice applies.
            high = (base.parent / "memory.high").read_text().strip()
        cpus = len(os.sched_getaffinity(int(procs[0])))
    except (OSError, ValueError, IndexError):
        return None

    now = time.time()
    used = int(cpu.get("usage_usec", 0)) / 1e6
    last = _cpu_samples.get(job_id)
    _cpu_samples[job_id] = (now, used)
    if last and now - last[0] >= 1:
        percent = (used - last[1]) / (now - last[0]) * 100
    else:
        # First poll: average since the build started.
        percent = used / max(1, now - started) * 100
    gb = 2**30
    return {
        "cpu_percent": round(max(0.0, percent)),
        "cpus": cpus,
        "cpu_seconds": round(used),
        # Page cache the kernel can drop at once is not counted as used.
        "ram_gb": round(max(0, current - int(stat.get("inactive_file", 0))) / gb, 1),
        "ram_cache_gb": round(int(stat.get("file", 0)) / gb, 1),
        "ram_peak_gb": round(peak / gb, 1),
        "ram_limit_gb": round(int(high) / gb, 1) if high.isdigit() else 0,
        "procs": len(procs),
    }


INFO_KEYS = {"device": "device", "brand": "brand", "base": "base", "android": "android", "final_repack": "package"}
LOG_INFO_KEYS = {"Device": "device", "Brand": "brand", "Base version": "base", "Android version": "android"}
LOG_INFO_RE = re.compile(r"^\s+(Device|Brand|Base version|Android version): (.+)$")
# Finished jobs do not change: looked up once.
_rom_info_cache = {}


def rom_info_from_file(path):
    keys = read_keys(path)
    return {name: keys[key] for key, name in INFO_KEYS.items() if keys.get(key)}


def rom_info_from_log(job_id):
    """bhlnk prints these lines right after it has identified the ROM; they are
    all that is left of a job that ran before the pool kept its info file."""
    found = {}
    try:
        with open(FARM / "logs" / f"{job_id}.log", "rb") as handle:
            text = handle.read(4 * 2**20).decode(errors="replace")
    except OSError:
        return found
    for line in text.splitlines():
        match = LOG_INFO_RE.match(ANSI_RE.sub("", line))
        if match:
            found.setdefault(LOG_INFO_KEYS[match.group(1)], match.group(2).strip())
            if len(found) == len(LOG_INFO_KEYS):
                break
    return found


def rom_info(job_id, slot=None):
    """What bhlnk knows about the job's ROM: from the slot's info file while the
    job runs (`slot`), from the copy farm.sh keeps afterwards. Empty until
    bhlnk has downloaded and identified the ROM."""
    if slot is None and job_id in _rom_info_cache:
        return _rom_info_cache[job_id]
    info = rom_info_from_file(FARM / "slots" / slot / "info" if slot else FARM / "logs" / job_id / "info")
    if not info.get("device"):
        info = {**rom_info_from_log(job_id), **info}
    if slot is None:
        _rom_info_cache[job_id] = info
    return info


def job_super_size(job_id):
    """Forced super size of a started job (farm.sh records it in logs/<id>/job)."""
    return read_keys(FARM / "logs" / job_id / "job").get("super_size", "")


def packing_state(slot, started):
    """Where the slot's build is at the OTA packaging turn (bin/ota_payload.py
    io_turn): {"state": "waiting"|"packing", "since": epoch}, or None."""
    rows = read_rows(FARM / "slots" / f"{slot}.io")
    if not rows or len(rows[0]) < 2 or rows[0][0] not in ("waiting", "packing"):
        return None
    since = int(rows[0][1]) if rows[0][1].isdigit() else 0
    # Left behind by an earlier job in this slot.
    if since < started:
        return None
    return {"state": rows[0][0], "since": since}


def running_jobs():
    jobs = []
    for state in sorted((FARM / "slots").glob("*.state")):
        rows = read_rows(state)
        if not rows or len(rows[0]) < 4:
            continue
        row = rows[0] + [""] * 3
        job_id, mode, started, rom, branch, trigger, pid = row[:7]
        jobs.append({
            "id": job_id, "status": "running", "slot": state.stem, "mode": mode,
            "rom": rom, "started": int(started or 0), "branch": branch, "trigger": trigger,
            "super_size": job_super_size(job_id),
            "usage": job_usage(job_id, pid, int(started or 0)),
            "rom_info": rom_info(job_id, state.stem),
            "packing": packing_state(state.stem, int(started or 0)),
        })
    for job_id in set(_cpu_samples) - {job["id"] for job in jobs}:
        del _cpu_samples[job_id]
    return jobs


def queued_jobs():
    return [
        {"id": row[4] if len(row) > 4 else "", "status": "queued", "mode": row[1],
         "rom": row[0], "trigger": row[3] if len(row) > 3 else "",
         "branch": row[5] if len(row) > 5 else "",
         "super_size": row[6] if len(row) > 6 else ""}
        for row in read_rows(FARM / "queue") if len(row) >= 2
    ]


def finished_jobs(limit=None):
    rows = [row for row in read_rows(FARM / "history.tsv") if len(row) >= 5]
    if limit:
        rows = rows[-limit:]
    jobs = []
    for row in rows:
        row = row + [""] * 4
        finished = int(row[8]) if row[8].isdigit() else 0
        jobs.append({
            "id": row[0], "status": "ok" if row[1] == "OK" else row[1].split("(")[0].lower(),
            "result": row[1], "seconds": int(row[2] or 0), "mode": row[3], "rom": row[4],
            "branch": row[6], "trigger": row[7], "finished": finished,
            "super_size": job_super_size(row[0]),
            "started": finished - int(row[2] or 0) if finished else 0,
            "rom_info": rom_info(row[0]),
        })
    return jobs


def find_job(job_id):
    for job in running_jobs() + queued_jobs() + finished_jobs()[::-1]:
        if job["id"] == job_id:
            return job
    return None


def tail_log(job_id, count):
    log = FARM / "logs" / f"{job_id}.log"
    if not log.exists():
        return None
    with open(log, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        handle.seek(max(0, handle.tell() - 512 * 1024))
        text = handle.read().decode(errors="replace")
    lines = [ANSI_RE.sub("", line) for line in re.split(r"[\r\n]+", text) if line.strip()]
    return lines[-count:]


def has_local_branch(branch):
    return subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        capture_output=True,
    ).returncode == 0


def ensure_local_branch(branch):
    """True when `branch` can be built. A branch that so far only exists on
    origin is fetched into a local one, the same way farm.sh updates it before
    a job; the checked-out branch is not touched."""
    if has_local_branch(branch):
        return True
    with open(FARM / "git.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            subprocess.run(
                ["git", "-C", str(REPO), "fetch", "--quiet", "origin", f"{branch}:{branch}"],
                capture_output=True, timeout=60, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    return has_local_branch(branch)


def read_keys(path):
    """key=value lines of a backup's `info` or `source` file."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return {}
    keys = {}
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            keys[key] = value.strip().strip("'\"")
    return keys


# `du` over an extracted ROM takes seconds, longer than the API waits for an
# answer: sizes are measured in the background and a listing reports null
# until they are known. Keyed by mtime, which bhlnk bumps when it restores.
backup_sizes = {}
backup_sizes_lock = threading.Lock()
backup_sizes_pending = set()


def measure_backup(path, mtime):
    try:
        result = subprocess.run(["du", "-sk", "--", str(path)], capture_output=True, text=True)
        size = int(result.stdout.split()[0])
    except (OSError, ValueError, IndexError):
        size = None
    with backup_sizes_lock:
        backup_sizes_pending.discard(path.name)
        if size is not None:
            backup_sizes[path.name] = (mtime, size)


def backup_size_kb(path, mtime):
    with backup_sizes_lock:
        known = backup_sizes.get(path.name)
        if known and known[0] == mtime:
            return known[1]
        if path.name not in backup_sizes_pending:
            backup_sizes_pending.add(path.name)
            threading.Thread(target=measure_backup, args=(path, mtime), daemon=True).start()
    return None


def backups_report():
    backups = []
    try:
        entries = sorted(BACKUPS.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        if not BACKUP_NAME_RE.match(entry.name) or entry.is_symlink() or not entry.is_dir():
            continue
        info = read_keys(entry / "info")
        source = read_keys(entry / "source")
        mtime = int(entry.stat().st_mtime)
        backups.append({
            "name": entry.name,
            "device": info.get("device", ""),
            "base": info.get("base", ""),
            # The ROM archive this backup was extracted from; bhlnk compares it
            # with what the server offers. Empty for a manual backup.
            "source_id": source.get("source_id", ""),
            "used": mtime,
            "size_kb": backup_size_kb(entry, mtime),
        })
    backups.sort(key=lambda backup: backup["used"], reverse=True)
    limit = os.environ.get("VS_BACKUP_MAX_GB", "")
    return {"backups": backups, "limit_gb": int(limit) if limit.isdigit() else 300}


def backup_path(name, rel):
    """Path of `rel` inside backup `name`, or None when it is not a valid one.

    Symlinks are never followed: a ROM tree is full of absolute links
    (/system/..., /data/...) that would lead out of the store. The returned path
    itself may be a link; callers lstat it.
    """
    root = BACKUPS / name
    if not BACKUP_NAME_RE.match(name) or root.is_symlink() or not root.is_dir():
        return None
    path = root
    for part in rel.split("/"):
        if not part:
            continue
        if part in (".", "..") or "\x00" in part or path.is_symlink():
            return None
        path = path / part
    return path


def list_backup_dir(path):
    entries = []
    with os.scandir(path) as items:
        for item in items:
            info = item.stat(follow_symlinks=False)
            if item.is_symlink():
                kind = "link"
            elif item.is_dir(follow_symlinks=False):
                kind = "dir"
            else:
                kind = "file"
            entry = {
                "name": item.name,
                "type": kind,
                "size": info.st_size if kind == "file" else None,
                "modified": int(info.st_mtime),
                "mode": stat.filemode(info.st_mode),
            }
            if kind == "link":
                entry["target"] = os.readlink(item.path)
            entries.append(entry)
    entries.sort(key=lambda entry: (entry["type"] != "dir", entry["name"].lower()))
    return {"entries": entries[:MAX_LISTING], "truncated": len(entries) > MAX_LISTING}


def preview_backup_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        size = os.fstat(handle.fileno()).st_size
        data = handle.read(MAX_PREVIEW + 1)
    binary = b"\0" in data
    return {
        "size": size,
        "binary": binary,
        "truncated": len(data) > MAX_PREVIEW,
        "text": None if binary else data[:MAX_PREVIEW].decode("utf-8", errors="replace"),
    }


def search_backup_names(root, query):
    """Files, folders and links of a backup whose name contains `query`, any case."""
    needle = query.lower()
    deadline = time.monotonic() + SEARCH_SECONDS
    results = []
    stopped = ""
    # os.walk does not follow links, so the walk stays inside the backup.
    for folder, dirs, files in os.walk(root):
        if time.monotonic() > deadline:
            stopped = "time"
            break
        dirs.sort()
        for name in sorted(dirs) + sorted(files):
            if needle not in name.lower():
                continue
            path = os.path.join(folder, name)
            try:
                info = os.lstat(path)
            except OSError:
                continue
            kind = "link" if stat.S_ISLNK(info.st_mode) else "dir" if stat.S_ISDIR(info.st_mode) else "file"
            results.append({
                "path": os.path.relpath(path, root), "type": kind,
                "size": info.st_size if kind == "file" else None,
            })
            if len(results) >= MAX_SEARCH_RESULTS:
                stopped = "results"
                break
        if stopped:
            break
    return {"results": results, "stopped": stopped}


def search_backup_text(root, query):
    """Lines of the backup's text files that contain `query`, any case.

    grep does the reading: -r stays off links, -I skips binary files (images,
    apks, libraries), -F takes the query as plain text."""
    process = subprocess.Popen(
        ["grep", "-rIinsF", "-D", "skip", "--null", "-m", str(SEARCH_HITS_PER_FILE), "-e", query, "--", "."],
        cwd=root, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    timed_out = threading.Event()

    def stop():
        timed_out.set()
        process.kill()

    timer = threading.Timer(SEARCH_SECONDS, stop)
    timer.start()
    results = []
    stopped = ""
    try:
        for raw in process.stdout:
            path, _, rest = raw.partition(b"\0")
            number, _, text = rest.partition(b":")
            if not number.isdigit():
                continue
            results.append({
                "path": os.path.normpath(path.decode("utf-8", errors="replace")),
                "line": int(number),
                "text": text.decode("utf-8", errors="replace").strip()[:MAX_SEARCH_LINE],
            })
            if len(results) >= MAX_SEARCH_RESULTS:
                stopped = "results"
                break
    finally:
        timer.cancel()
        process.kill()
        process.wait()
    if timed_out.is_set():
        stopped = "time"
    return {"results": results, "stopped": stopped}


class Handler(BaseHTTPRequestHandler):
    server_version = "vs-farm"
    key = ""

    def log_message(self, fmt, *args):
        print(f"[{time.strftime('%H:%M:%S')}] {self.client_address[0]} {fmt % args}", flush=True)

    def reply(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        given = self.headers.get("X-Farm-Key", "")
        bearer = self.headers.get("Authorization", "")
        if bearer.startswith("Bearer "):
            given = bearer[7:]
        return hmac.compare_digest(given.strip().encode(), self.key.encode())

    def do_GET(self):
        url = urlparse(self.path)
        parts = [part for part in url.path.split("/") if part]
        if parts == ["health"]:
            return self.reply(200, {"ok": True})
        if not self.authorized():
            return self.reply(401, {"error": "invalid key"})
        if parts == ["status"]:
            try:
                recent = max(1, min(int(parse_qs(url.query).get("recent", ["20"])[0]), 500))
            except ValueError:
                recent = 20
            return self.reply(200, {
                "pool_running": pool_running(),
                "branch": repo_branch(),
                "free_gb": shutil.disk_usage(FARM).free // 2**30,
                "running": running_jobs(),
                "queued": queued_jobs(),
                "recent": finished_jobs(recent)[::-1],
            })
        if parts == ["config"]:
            return self.reply(200, config_report())
        if parts == ["backups"]:
            return self.reply(200, backups_report())
        if len(parts) == 3 and parts[0] == "backups" and parts[2] in ("list", "file"):
            return self.browse_backup(parts[1], parts[2], parse_qs(url.query).get("path", [""])[0])
        if len(parts) == 3 and parts[0] == "backups" and parts[2] == "search":
            query = parse_qs(url.query)
            return self.search_backup(parts[1], query.get("kind", ["name"])[0], query.get("q", [""])[0])
        if len(parts) == 3 and parts[0] == "backups" and parts[2] == "download":
            return self.send_backup_file(parts[1], parse_qs(url.query).get("path", [""])[0])
        if len(parts) in (2, 3) and parts[0] == "jobs" and JOB_ID_RE.match(parts[1]):
            if len(parts) == 2:
                job = find_job(parts[1])
                return self.reply(200, job) if job else self.reply(404, {"error": "job not found"})
            if parts[2] == "log":
                try:
                    count = int(parse_qs(url.query).get("tail", ["200"])[0])
                except ValueError:
                    count = 200
                lines = tail_log(parts[1], max(1, min(count, 2000)))
                if lines is None:
                    return self.reply(404, {"error": "no log yet"})
                job = find_job(parts[1]) or {}
                return self.reply(200, {"id": parts[1], "status": job.get("status", ""), "lines": lines})
        return self.reply(404, {"error": "not found"})

    def browse_backup(self, name, action, rel):
        path = backup_path(name, rel)
        try:
            mode = path.lstat().st_mode if path else None
            if mode is not None and action == "list" and stat.S_ISDIR(mode):
                return self.reply(200, {"name": name, "path": rel.strip("/"), **list_backup_dir(path)})
            if mode is not None and action == "file" and stat.S_ISREG(mode):
                return self.reply(200, {"name": name, "path": rel.strip("/"), **preview_backup_file(path)})
        except PermissionError:
            return self.reply(403, {"error": "the farm cannot read this path"})
        except OSError:
            pass
        return self.reply(404, {"error": "no such folder" if action == "list" else "no such file"})

    def search_backup(self, name, kind, query):
        root = backup_path(name, "")
        query = query.strip()
        if root is None:
            return self.reply(404, {"error": "no such backup"})
        if kind not in ("name", "text") or not query or len(query) > MAX_SEARCH_QUERY or "\n" in query or "\0" in query:
            return self.reply(400, {"error": f"q (1-{MAX_SEARCH_QUERY} characters) and kind (name or text) are required"})
        search = search_backup_names if kind == "name" else search_backup_text
        try:
            return self.reply(200, {"name": name, "kind": kind, "query": query, **search(root, query)})
        except OSError as exc:
            return self.reply(500, {"error": f"search failed: {exc}"})

    def send_backup_file(self, name, rel):
        """Streams one regular file of a backup; a single `Range: bytes=a-b`
        is honoured so a browser can resume a large image."""
        path = backup_path(name, rel)
        try:
            if path is None or not stat.S_ISREG(path.lstat().st_mode):
                return self.reply(404, {"error": "no such file"})
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except PermissionError:
            return self.reply(403, {"error": "the farm cannot read this path"})
        except OSError:
            return self.reply(404, {"error": "no such file"})
        with os.fdopen(fd, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            start, end, partial = 0, size - 1, False
            wanted = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", "").strip())
            if wanted and (wanted.group(1) or wanted.group(2)):
                if wanted.group(1):
                    start = int(wanted.group(1))
                    if wanted.group(2):
                        end = min(int(wanted.group(2)), size - 1)
                else:
                    start = max(0, size - int(wanted.group(2)))
                if start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                partial = True
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            handle.seek(start)
            left = end - start + 1
            try:
                while left > 0:
                    chunk = handle.read(min(DOWNLOAD_CHUNK, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_POST(self):
        parts = [part for part in urlparse(self.path).path.split("/") if part]
        if not self.authorized():
            return self.reply(401, {"error": "invalid key"})
        if parts == ["jobs"]:
            return self.add_job()
        if parts == ["config"]:
            return self.set_config()
        if parts == ["update"]:
            return self.update_farm()
        if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "rebuild" and JOB_ID_RE.match(parts[1]):
            return self.rebuild_job(parts[1])
        if len(parts) == 3 and parts[0] == "jobs" and parts[2] == "cancel" and JOB_ID_RE.match(parts[1]):
            result = subprocess.run([str(FARM_SH), "cancel", parts[1]], capture_output=True, text=True)
            if result.returncode != 0:
                return self.reply(404, {"error": "job not queued or running"})
            return self.reply(200, {"id": parts[1], "status": "cancelling"})
        return self.reply(404, {"error": "not found"})

    def read_json(self):
        """The request body as a dict, or None after replying with the error."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY:
                self.reply(413, {"error": "body too large"})
                return None
            data = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(data, dict):
                raise ValueError
        except ValueError:
            self.reply(400, {"error": "body must be a JSON object"})
            return None
        return data

    def update_farm(self):
        before = repo_head()
        try:
            # farm.sh gives a pull 300 s, twice when bin/rclone.conf was in its way.
            result = subprocess.run([str(FARM_SH), "update"], capture_output=True, text=True, timeout=660)
            pulled, log = result.returncode == 0, result.stderr
        except subprocess.TimeoutExpired:
            pulled, log = False, "farm.sh update timed out"
        after = repo_head()
        return self.reply(200, {
            "branch": repo_branch(),
            "before": before,
            "after": after,
            "pulled": pulled,
            "updated": before != after,
            # The pool is what restarts the service; without one nothing does.
            "restart": "pending" if pool_running() else "no pool",
            "running": len(running_jobs()),
            "log": ANSI_RE.sub("", log).strip()[-1000:],
        })

    def set_config(self):
        data = self.read_json()
        if data is None:
            return
        config = read_config()
        max_cpus, max_ram = os.cpu_count() or 1, int(meminfo_gb("MemTotal"))
        limits = {"slots": (1, 16), "cpus": (0, max_cpus), "ram_gb": (0, max_ram),
                  "total_cpus": (0, max_cpus), "total_ram_gb": (0, max_ram)}
        for key, (low, high) in limits.items():
            if key not in data:
                continue
            value = data[key]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                return self.reply(400, {"error": f"{key} must be an integer from {low} to {high}"})
            config[key] = value
        temp = FARM / "config.tmp"
        temp.write_text("".join(f"{key}={value}\n" for key, value in config.items()))
        temp.replace(FARM / "config")
        return self.reply(200, config_report(config))

    def add_job(self):
        data = self.read_json()
        if data is None:
            return

        return self.queue_job(
            rom=str(data.get("ROM_URL") or data.get("rom") or "").strip(),
            mode=str(data.get("mode") or "vs").strip(),
            region=str(data.get("region") or "cn").strip(),
            trigger=data.get("trigger"),
            branch=str(data.get("branch") or "").strip(),
            super_size=str(data.get("super_size") or "").strip(),
        )

    def rebuild_job(self, job_id):
        data = self.read_json()
        if data is None:
            return
        job = find_job(job_id)
        if not job:
            return self.reply(404, {"error": "job not found"})
        if job["status"] in ("running", "queued"):
            return self.reply(409, {"error": f"job is still {job['status']}"})
        # farm.sh writes logs/<id>/job when a job starts; history.tsv has no region.
        meta = read_keys(FARM / "logs" / job_id / "job")
        rom = meta.get("rom") or job.get("rom", "")
        mode = meta.get("mode") or job.get("mode", "")
        region = meta.get("region", "")
        if not region:
            # Only a stock build of a codename passes the region on to bhlnk.
            if mode == "stock" and CODENAME_RE.match(rom):
                return self.reply(409, {"error": "the region of this older job was not recorded; start it from the build form"})
            region = "cn"
        return self.queue_job(
            rom=rom, mode=mode, region=region,
            trigger=data.get("trigger") or meta.get("builder") or job.get("trigger"),
            branch=meta.get("branch") or job.get("branch", ""),
            super_size=meta.get("super_size", ""),
        )

    def queue_job(self, rom, mode, region, trigger, branch, super_size=""):
        builder = re.sub(r"[^A-Za-z0-9_.@-]", "_", str(trigger or "LocalFarm"))[:48]

        if not (ROM_URL_RE.match(rom) or CODENAME_RE.match(rom)):
            return self.reply(400, {"error": "ROM_URL must be an http(s) URL or a device codename"})
        if mode not in MODES:
            return self.reply(400, {"error": f"mode must be one of {sorted(MODES)}"})
        if not REGION_RE.match(region):
            return self.reply(400, {"error": "invalid region"})
        if super_size and not (SUPER_SIZE_RE.match(super_size) and int(super_size) % 4096 == 0):
            return self.reply(400, {"error": "super_size must be a size in bytes, a multiple of 4096"})
        branch = branch or repo_branch()
        if not BRANCH_RE.match(branch) or not ensure_local_branch(branch):
            return self.reply(400, {"error": f"no branch '{branch}' on the farm or on origin"})
        if not pool_running():
            return self.reply(503, {"error": "worker pool is not running (farm.sh run --watch)"})

        result = subprocess.run(
            [str(FARM_SH), "add", "-m", mode, "-r", region, "-b", builder, "--branch", branch,
             *(["--super-size", super_size] if super_size else []), rom],
            capture_output=True, text=True,
        )
        job_id = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
        if result.returncode != 0 or not JOB_ID_RE.match(job_id):
            return self.reply(500, {"error": result.stderr.strip() or "could not queue job"})
        return self.reply(202, {"id": job_id, "status": "queued", "branch": branch,
                                "super_size": super_size})


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()

    Handler.key = load_key()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    key_source = "VS_FARM_KEY" if os.environ.get("VS_FARM_KEY") else "built-in"
    print(f"vs-farm API on http://{args.host}:{args.port} (key: {key_source}, farm: {FARM})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
