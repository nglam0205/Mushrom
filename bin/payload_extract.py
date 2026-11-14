#!/usr/bin/env python3
"""Extract the partition images of a full A/B OTA payload (replaces payload_ext).

The payload is read in place: given the OTA zip, payload.bin is read straight
from its stored entry, so the 5-10 GB payload is never copied to disk before
the images are written (jar xf + payload_ext wrote everything twice). Images
are created sparse: ZERO/DISCARD operations leave holes instead of writing
zeros.

Writing the images is the disk-heavy part of a build. $VS_EXTRACT_LOCK names
a lock shared by the farm slots (default: extract.lock beside $VS_OTA_IO_LOCK)
so one build extracts at a time while the others wait; without either there
is no waiting. $VS_EXTRACT_LOCK_HELD means the caller already holds it.

Usage:
  payload_extract.py [-o DIR] [-p a,b] [-j N] <ota.zip | payload.bin>
  payload_extract.py -l <ota.zip | payload.bin>
"""

from __future__ import annotations

import argparse
import bz2
import contextlib
import hashlib
import lzma
import os
import shutil
import struct
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ota_payload import PAYLOAD_MAGIC, _fields, payload_offset

OP_REPLACE = 0
OP_REPLACE_BZ = 1
OP_ZERO = 6
OP_DISCARD = 7
OP_REPLACE_XZ = 8
OP_REPLACE_ZSTD = 14
OP_NAMES = {2: "MOVE", 3: "BSDIFF", 4: "SOURCE_COPY", 5: "SOURCE_BSDIFF", 9: "PUFFDIFF",
            10: "BROTLI_BSDIFF", 11: "ZUCCHINI", 12: "LZ4DIFF_BSDIFF", 13: "LZ4DIFF_PUFFDIFF"}


def read_payload(path: Path) -> tuple[int, int, list]:
    """Return (data base offset, block size, partitions) of the payload in path.

    Each partition is {"name", "size", "ops": [(type, offset, length, sha256,
    [(first block, block count)])]}.
    """
    start = payload_offset(path)
    with path.open("rb") as f:
        f.seek(start)
        header = f.read(24)
        if header[:4] != PAYLOAD_MAGIC:
            raise ValueError("invalid payload magic")
        version, manifest_size = struct.unpack_from(">QQ", header, 4)
        if version != 2:
            raise ValueError(f"unsupported payload version {version}")
        signature_size = struct.unpack_from(">I", header, 20)[0]
        manifest = f.read(manifest_size)
    if len(manifest) != manifest_size:
        raise ValueError("truncated manifest")

    block_size = 4096
    partitions = []
    for num, val in _fields(manifest):
        if num == 3:
            block_size = val
        if num != 13:
            continue
        part = {"name": "", "size": 0, "ops": []}
        for n, v in _fields(val):
            if n == 1:
                part["name"] = v.decode()
            elif n == 7:
                part["size"] = dict(_fields(v)).get(1, 0)
            elif n == 8:
                op = {1: 0, 2: 0, 3: 0, 8: b""}
                extents = []
                for k, x in _fields(v):
                    if k in op:
                        op[k] = x
                    elif k == 6:
                        ext = dict(_fields(x))
                        extents.append((ext.get(1, 0), ext.get(2, 0)))
                part["ops"].append((op[1], op[2], op[3], op[8], extents))
        partitions.append(part)
    return start + 24 + manifest_size + signature_size, block_size, partitions


def zstd_decompress(blob: bytes) -> bytes:
    try:
        import zstandard
    except ImportError:
        return subprocess.run(["zstd", "-dcq"], input=blob, capture_output=True, check=True).stdout
    return zstandard.ZstdDecompressor().decompressobj().decompress(blob)


@contextlib.contextmanager
def extract_turn():
    if os.environ.get("VS_EXTRACT_LOCK_HELD"):
        # patchvs.sh holds the lock around the whole Extract step.
        yield
        return
    path = os.environ.get("VS_EXTRACT_LOCK", "").strip()
    # A pool started from an older farm.sh only exports the packaging lock;
    # the extraction lock lives next to it.
    io_lock = os.environ.get("VS_OTA_IO_LOCK", "").strip()
    if not path and io_lock:
        path = os.path.join(os.path.dirname(io_lock), "extract.lock")
    if not path:
        yield
        return
    import fcntl
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("[lock] another build is extracting; waiting for its turn", flush=True)
            started = time.monotonic()
            fcntl.flock(fd, fcntl.LOCK_EX)
            print(f"[lock] extraction turn after {time.monotonic() - started:.0f}s wait", flush=True)
        yield
    finally:
        os.close(fd)    # closing releases the lock


def extract(src: Path, out: Path, partitions: list, base: int, block_size: int, jobs: int) -> int:
    for part in partitions:
        for kind, *_ in part["ops"]:
            if kind in OP_NAMES:
                raise ValueError(f"{part['name']}: incremental payload ({OP_NAMES[kind]} operation)")
            if kind not in (OP_REPLACE, OP_REPLACE_BZ, OP_REPLACE_XZ, OP_REPLACE_ZSTD,
                            OP_ZERO, OP_DISCARD):
                raise ValueError(f"{part['name']}: unknown operation type {kind}")

    out.mkdir(parents=True, exist_ok=True)
    src_fd = os.open(src, os.O_RDONLY)
    fds = {}
    pending = {}
    lock = threading.Lock()
    written = 0

    def run(name, fd, op):
        kind, offset, length, digest, extents = op
        if kind in (OP_ZERO, OP_DISCARD) or not extents:
            return 0
        blob = os.pread(src_fd, length, base + offset)
        if len(blob) != length:
            raise ValueError(f"{name}: payload truncated")
        if digest and hashlib.sha256(blob).digest() != digest:
            raise ValueError(f"{name}: operation data hash mismatch (corrupt download?)")
        if kind == OP_REPLACE_XZ:
            data = lzma.decompress(blob, format=lzma.FORMAT_XZ)
        elif kind == OP_REPLACE_BZ:
            data = bz2.decompress(blob)
        elif kind == OP_REPLACE_ZSTD:
            data = zstd_decompress(blob)
        else:
            data = blob
        view = memoryview(data)
        pos = 0
        for first, count in extents:
            chunk = view[pos:pos + count * block_size]
            os.pwrite(fd, chunk, first * block_size)
            pos += len(chunk)
        return pos

    def done(name):
        with lock:
            pending[name] -= 1
            if pending[name]:
                return
        os.close(fds[name])
        print(f"  {name}.img", flush=True)

    def task(name, fd, op):
        nonlocal written
        size = run(name, fd, op)
        with lock:
            written += size
        done(name)

    try:
        for part in partitions:
            fd = os.open(out / f"{part['name']}.img", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            fds[part["name"]] = fd
            os.ftruncate(fd, part["size"])
            pending[part["name"]] = len(part["ops"])
            if not part["ops"]:
                os.close(fd)
        # Operations are queued partition by partition, so the images are
        # written mostly in order; lzma/bz2/pread/pwrite release the GIL.
        pool = ThreadPoolExecutor(max_workers=jobs)
        try:
            futures = [pool.submit(task, part["name"], fds[part["name"]], op)
                       for part in partitions for op in part["ops"]]
            for future in futures:
                future.result()
        finally:
            # On a failure the queued operations are dropped, not written.
            pool.shutdown(wait=True, cancel_futures=True)
    except BaseException:
        for name, left in pending.items():
            if left:
                with contextlib.suppress(OSError):
                    os.close(fds[name])
        raise
    finally:
        os.close(src_fd)
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("payload", help="OTA zip or payload.bin")
    ap.add_argument("-o", "--output", default=".", help="output directory (default: .)")
    ap.add_argument("-p", "--partitions", help="extract only these partitions (comma-separated)")
    ap.add_argument("-l", "--list", action="store_true", help="list the partitions and exit")
    ap.add_argument("-j", "--jobs", type=int,
                    default=int(os.environ.get("VS_EXTRACT_JOBS", "")
                                or os.environ.get("VS_BUILD_CPUS", "") or 0),
                    help="decode threads (default: CPUs, at most 16; "
                         "$VS_EXTRACT_JOBS, else $VS_BUILD_CPUS)")
    args = ap.parse_args()

    src = Path(args.payload)
    try:
        base, block_size, partitions = read_payload(src)
        if args.list:
            for part in partitions:
                print(f"{part['name']}\t{part['size']}")
            return 0
        if args.partitions:
            want = {p.strip() for p in args.partitions.split(",") if p.strip()}
            missing = want - {p["name"] for p in partitions}
            if missing:
                raise ValueError(f"not in payload: {', '.join(sorted(missing))}")
            partitions = [p for p in partitions if p["name"] in want]

        cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 4)
        jobs = args.jobs if args.jobs > 0 else min(cpus, 16)
        out = Path(args.output)
        need = sum(p["size"] for p in partitions)
        free = shutil.disk_usage(out if out.exists() else out.parent).free
        if need > free:
            raise ValueError(f"not enough disk: images need {need >> 20} MiB, {free >> 20} MiB free")

        with extract_turn():
            print(f"extracting {len(partitions)} partition(s) from {src.name} with {jobs} thread(s)",
                  flush=True)
            started = time.monotonic()
            written = extract(src, out, partitions, base, block_size, jobs)
            took = max(time.monotonic() - started, 0.001)
        print(f"done: {written >> 20} MiB written in {took:.0f}s ({written / took / 1e6:.0f} MB/s)")
        return 0
    except (OSError, ValueError, lzma.LZMAError, subprocess.CalledProcessError) as exc:
        print(f"payload_extract: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
