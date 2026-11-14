#!/usr/bin/env python3
"""Rebuild a signed A/B OTA package from a patched ROM.

Subcommands:
  meta      Dump the stock payload.bin layout (partitions, dynamic groups,
            VAB/VABC settings, timestamps, postinstall) to JSON before the
            payload is extracted and deleted.
  otacerts  Build an otacerts.zip holding only our OTA certificate so the
            patched ROM trusts payloads signed with our key (and rejects the
            OEM's official OTAs, which would overwrite the mod).
  build     Generate a full payload.bin signed with our key, wrap it in an
            OTA zip (payload + properties + metadata) and apply a whole-file
            signature so RecoverySystem/recovery also accept the package.

The manifest is decoded with a tiny protobuf wire reader instead of
update_metadata_pb2.py: that generated module predates VABC/cow_version and
per-partition timestamps, which are exactly the fields needed here.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

PAYLOAD_MAGIC = b"CrAU"
MAX_MANIFEST_SIZE = 64 * 1024 * 1024
METADATA_NAME = "META-INF/com/android/metadata"
METADATA_PB_NAME = "META-INF/com/android/metadata.pb"
# Install operations that read the source slot; a payload using any of them
# is incremental and cannot be rebuilt from the extracted images alone.
SOURCE_OPS = {4, 5, 9, 10, 11, 12, 13}
OP_REPLACE_BZ = 1
OP_REPLACE_XZ = 8
PROPERTIES_OWN_KEYS = {"FILE_HASH", "FILE_SIZE", "METADATA_HASH", "METADATA_SIZE"}
# Fixed width of ota-property-files so offsets computed in the first pass stay
# valid after the real values are written (AOSP pads the same way).
PROPERTY_FILES_WIDTH = 512
# How often a payload with bad blobs is repaired and fully re-verified.
MAX_REPAIR_ROUNDS = 2
# Lowest minor version update_engine accepts for a partial payload.
PARTIAL_MINOR_VERSION = 7
# Entries only VSTool reads when the same package is flashed over fastboot.
SUPER_LAYOUT_NAME = "vs/super_layout_{slot}.img"
FASTBOOT_IMAGE_DIR = "vs/images/"
FASTBOOT_IMAGE_SKIP = {"super", "supertest", "real_state_boot"}


# --------------------------------------------------------------------------
# Protobuf wire decoding
# --------------------------------------------------------------------------

def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def _fields(buf: bytes):
    pos = 0
    end = len(buf)
    while pos < end:
        key, pos = _varint(buf, pos)
        num, wire = key >> 3, key & 7
        if wire == 0:
            val, pos = _varint(buf, pos)
        elif wire == 1:
            val = buf[pos:pos + 8]
            pos += 8
        elif wire == 2:
            size, pos = _varint(buf, pos)
            val = buf[pos:pos + size]
            pos += size
        elif wire == 5:
            val = buf[pos:pos + 4]
            pos += 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        yield num, val


def _signed64(v: int) -> int:
    return v - (1 << 64) if v >= 1 << 63 else v


def payload_offset(path: Path) -> int:
    """Byte offset of the payload inside path: 0 for a bare payload.bin, the
    start of the stored payload.bin entry for an OTA zip (read in place, never
    copied out)."""
    with path.open("rb") as f:
        if f.read(4) != b"PK\x03\x04":
            return 0
    with zipfile.ZipFile(path) as zf:
        try:
            info = zf.getinfo("payload.bin")
        except KeyError:
            raise ValueError(f"{path.name}: no payload.bin in the zip") from None
    if info.compress_type != zipfile.ZIP_STORED:
        raise ValueError(f"{path.name}: payload.bin is compressed in the zip")
    return data_offset(path, info)


def read_manifest(payload: Path) -> bytes:
    start = payload_offset(payload)
    with payload.open("rb") as f:
        f.seek(start)
        if f.read(4) != PAYLOAD_MAGIC:
            raise ValueError("invalid payload magic")
        version = struct.unpack(">Q", f.read(8))[0]
        manifest_size = struct.unpack(">Q", f.read(8))[0]
        if version >= 2:
            f.read(4)
        if not 0 < manifest_size <= MAX_MANIFEST_SIZE:
            raise ValueError(f"invalid manifest size: {manifest_size}")
        data = f.read(manifest_size)
    if len(data) != manifest_size:
        raise ValueError("truncated manifest")
    return data


def parse_manifest(data: bytes) -> dict:
    meta = {
        "block_size": 4096,
        "minor_version": 0,
        "max_timestamp": 0,
        "partial_update": False,
        "security_patch_level": "",
        "partitions": [],
        "dynamic": {"groups": []},
    }
    for num, val in _fields(data):
        if num == 3:
            meta["block_size"] = val
        elif num == 12:
            meta["minor_version"] = val
        elif num == 13:
            meta["partitions"].append(_parse_partition(val))
        elif num == 14:
            meta["max_timestamp"] = _signed64(val)
        elif num == 15:
            meta["dynamic"] = _parse_dynamic(val)
        elif num == 16:
            meta["partial_update"] = bool(val)
        elif num == 18:
            meta["security_patch_level"] = val.decode()
    return meta


def _parse_partition(buf: bytes) -> dict:
    part = {
        "name": "",
        "size": 0,
        "run_postinstall": False,
        "postinstall_path": "",
        "filesystem_type": "",
        "postinstall_optional": False,
        "version": "",
        "incremental": False,
    }
    for num, val in _fields(buf):
        if num == 1:
            part["name"] = val.decode()
        elif num == 6:
            part["incremental"] = True
        elif num == 8 and not part["incremental"]:
            for n, v in _fields(val):
                if n == 1 and v in SOURCE_OPS:
                    part["incremental"] = True
        elif num == 2:
            part["run_postinstall"] = bool(val)
        elif num == 3:
            part["postinstall_path"] = val.decode()
        elif num == 4:
            part["filesystem_type"] = val.decode()
        elif num == 7:
            for n, v in _fields(val):
                if n == 1:
                    part["size"] = v
        elif num == 9:
            part["postinstall_optional"] = bool(val)
        elif num == 17:
            part["version"] = val.decode()
    return part


def _parse_dynamic(buf: bytes) -> dict:
    dyn = {
        "groups": [],
        "snapshot_enabled": False,
        "vabc_enabled": False,
        "vabc_compression_param": "",
        "cow_version": 0,
        "vabc_threaded": False,
        "vabc_batch_writes": False,
        "compression_factor": 0,
    }
    for num, val in _fields(buf):
        if num == 1:
            group = {"name": "", "size": 0, "partitions": []}
            for n, v in _fields(val):
                if n == 1:
                    group["name"] = v.decode()
                elif n == 2:
                    group["size"] = v
                elif n == 3:
                    group["partitions"].append(v.decode())
            dyn["groups"].append(group)
        elif num == 2:
            dyn["snapshot_enabled"] = bool(val)
        elif num == 3:
            dyn["vabc_enabled"] = bool(val)
        elif num == 4:
            dyn["vabc_compression_param"] = val.decode()
        elif num == 5:
            dyn["cow_version"] = val
        elif num == 6:
            for n, v in _fields(val):
                if n == 1:
                    dyn["vabc_threaded"] = bool(v)
                elif n == 2:
                    dyn["vabc_batch_writes"] = bool(v)
        elif num == 7:
            dyn["compression_factor"] = val
    return dyn


def _key_values(text: str) -> dict:
    """Return key/value pairs of a metadata / payload_properties.txt file."""
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _encode_varint(v: int) -> bytes:
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _encode_bytes(num: int, data: bytes) -> bytes:
    return _encode_varint(num << 3 | 2) + _encode_varint(len(data)) + data


def _rewrite_fields(buf: bytes, handlers: dict) -> bytes:
    """Re-encode a message, passing chosen fields through handlers.

    handlers maps a field number to fn(wire, raw_value) -> bytes | None
    (encoded field, or None to keep it unchanged).
    """
    out = bytearray()
    pos = 0
    while pos < len(buf):
        start = pos
        key, pos = _varint(buf, pos)
        num, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _varint(buf, pos)
        elif wire == 1:
            value = buf[pos:pos + 8]
            pos += 8
        elif wire == 2:
            size, pos = _varint(buf, pos)
            value = buf[pos:pos + size]
            pos += size
        elif wire == 5:
            value = buf[pos:pos + 4]
            pos += 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        replaced = handlers[num](wire, value) if num in handlers else None
        out += replaced if replaced is not None else buf[start:pos]
    return bytes(out)


def restamp_metadata_pb(stock: bytes, timestamp: int) -> bytes:
    """Set the postcondition timestamps of an OtaMetadata message.

    postcondition (field 6) holds the build timestamp (field 4, int64) and a
    PartitionState per partition (field 7) whose timestamp is a string
    (field 4); only partitions that carry one are updated.
    """
    def partition_state(wire, value):
        return _encode_bytes(7, _rewrite_fields(value, {
            4: lambda w, v: _encode_bytes(4, str(timestamp).encode()) if w == 2 else None,
        }))

    def postcondition(wire, value):
        if wire != 2:
            return None
        return _encode_bytes(6, _rewrite_fields(value, {
            4: lambda w, v: _encode_varint(4 << 3) + _encode_varint(timestamp) if w == 0 else None,
            7: lambda w, v: partition_state(w, v) if w == 2 else None,
        }))

    return _rewrite_fields(stock, {6: postcondition})


def rewrite_metadata_pb(stock: bytes, property_files: dict) -> bytes:
    """Replace the property_files map (field 4) of an OtaMetadata message.

    Everything else (pre/post conditions, OEM fields) is kept byte-for-byte
    so OEM updater apps see the same build info as the stock package.
    """
    out = bytearray()
    pos = 0
    written = False
    while pos < len(stock):
        start = pos
        key, pos = _varint(stock, pos)
        num, wire = key >> 3, key & 7
        if wire == 0:
            _, pos = _varint(stock, pos)
        elif wire == 1:
            pos += 8
        elif wire == 2:
            size, pos = _varint(stock, pos)
            pos += size
        elif wire == 5:
            pos += 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        if num == 4:
            if not written:
                for k, v in property_files.items():
                    entry = _encode_bytes(1, k.encode()) + _encode_bytes(2, v.encode())
                    out += _encode_bytes(4, entry)
                written = True
            continue
        out += stock[start:pos]
    if not written:
        for k, v in property_files.items():
            entry = _encode_bytes(1, k.encode()) + _encode_bytes(2, v.encode())
            out += _encode_bytes(4, entry)
    return bytes(out)


# --------------------------------------------------------------------------
# meta
# --------------------------------------------------------------------------

def add_stock_meta(meta: dict, read) -> None:
    """Copy the stock zip metadata and payload properties into meta; read(name)
    returns the bytes of a package entry, or None when it is missing."""
    def key_values(name):
        data = read(name)
        return _key_values(data.decode()) if data else {}

    meta["zip_metadata"] = key_values(METADATA_NAME)
    meta["payload_properties"] = {
        k: v for k, v in key_values("payload_properties.txt").items()
        if k not in PROPERTIES_OWN_KEYS
    }
    pb = read(METADATA_PB_NAME)
    if pb:
        meta["metadata_pb"] = base64.b64encode(pb).decode()


def cmd_meta(args) -> int:
    meta = parse_manifest(read_manifest(Path(args.payload)))
    incremental = [p["name"] for p in meta["partitions"] if p["incremental"]]
    if incremental:
        print(f"ota_payload: incremental payload ({', '.join(incremental)})", file=sys.stderr)
        return 1
    # OPlus ships full payloads flagged partial_update (minor 9); they only
    # carry REPLACE/ZERO ops, so they are rebuilt with the same flag.
    src = Path(args.payload)
    if payload_offset(src):
        with zipfile.ZipFile(src) as zf:
            names = set(zf.namelist())
            add_stock_meta(meta, lambda name: zf.read(name) if name in names else None)
    elif args.stock_dir:
        stock = Path(args.stock_dir)
        add_stock_meta(meta, lambda name: (stock / name).read_bytes()
                       if (stock / name).is_file() else None)
    Path(args.out).write_text(json.dumps(meta, indent=2) + "\n")
    names = ", ".join(p["name"] for p in meta["partitions"])
    print(f"{len(meta['partitions'])} partition(s): {names}")
    return 0


# --------------------------------------------------------------------------
# otacerts
# --------------------------------------------------------------------------

def cmd_verify(args) -> int:
    path = Path(args.file)
    start = 0
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            info = z.getinfo("payload.bin")
        if info.flag_bits & 1:
            raise ValueError("payload.bin is password-locked; extract it with ota_zipcrypt.py password")
        if info.compress_type != zipfile.ZIP_STORED:
            raise ValueError("payload.bin is compressed inside the zip")
        start = data_offset(path, info)
    total, bad = verify_payload_blobs(path, start)
    for line in bad:
        print(line)
    print(f"{total} blobs checked, {len(bad)} bad")
    return 1 if bad else 0


def cmd_otacerts(args) -> int:
    out = Path(args.out)
    tmp = out.with_name(out.name + ".tmp")
    # Fixed timestamp keeps the image reproducible for identical keys.
    info = zipfile.ZipInfo("ota.x509.pem", date_time=(2009, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = 0o644 << 16
    with zipfile.ZipFile(tmp, "w") as zf:
        zf.writestr(info, Path(args.cert).read_bytes())
    os.replace(tmp, out)
    return 0


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def build_cpus() -> int:
    """CPUs the build may use: VS_OTA_CPUS, else VS_BUILD_CPUS (the limit of
    all compressors), else all but one on GitHub Actions (a fully loaded
    runner stops answering and the job is cancelled)."""
    total = os.cpu_count() or 1
    value = (os.environ.get("VS_OTA_CPUS", "").strip()
             or os.environ.get("VS_BUILD_CPUS", "").strip())
    if value:
        return max(1, min(total, int(value)))
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return max(1, total - 1)
    return total


def limit_cpu(log=None) -> int:
    """Pin this process to build_cpus() CPUs and lower its priority.

    delta_generator has no thread option and spawns one worker per online CPU,
    but affinity and nice are inherited, so its threads (and openssl) share the
    pinned CPUs instead of saturating the machine.
    """
    cpus = build_cpus()
    available = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else []
    if available and cpus < len(available):
        os.sched_setaffinity(0, set(available[:cpus]))
    try:
        os.nice(int(os.environ.get("VS_OTA_NICE", "10")))
    except OSError:
        pass
    if log is not None:
        log.write(f"[cpu] {cpus} of {os.cpu_count()} CPU(s), nice {os.nice(0)}\n".encode())
        log.flush()
    return cpus


def run(cmd: list[str], log) -> None:
    log.write(("$ " + " ".join(cmd) + "\n").encode())
    log.flush()
    res = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
    if res.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} failed with exit code {res.returncode}")


def cow_estimate(size: int) -> int:
    """Generous VABC COW size for an image of the given size.

    The static delta_generator cannot estimate lz4 COW and reports size + 84,
    which runs out of COW space (error 28) on poorly compressible partitions
    such as mi_ext (the AOSP otatools build estimates +2.2% there). Headroom
    only costs /data space during the update.
    """
    return size + size // 16 + 16 * 1024 * 1024


def raise_cow_estimates(manifest: bytes, dynamic: set[str]) -> tuple[bytes, dict]:
    """Rewrite estimate_cow_size (PartitionUpdate field 19) of dynamic partitions."""
    changed = {}

    def partition(wire, value):
        if wire != 2:
            return None
        name, size = "", 0
        for num, val in _fields(value):
            if num == 1:
                name = val.decode()
            elif num == 7:
                for n, v in _fields(val):
                    if n == 1:
                        size = v
        if name not in dynamic or not size:
            return None
        est = cow_estimate(size)
        field = _encode_varint(19 << 3) + _encode_varint(est)
        seen = []

        def replace(w, v):
            seen.append(True)
            return field

        body = _rewrite_fields(value, {19: replace})
        if not seen:
            body += field
        changed[name] = est
        return _encode_bytes(13, body)

    return _rewrite_fields(manifest, {13: partition}), changed


def repair_payload(src: Path, dst: Path, manifest: bytes, bad: set,
                   images: dict, block_size: int) -> int:
    """Copy an unsigned payload, rewriting the bad blobs as plain REPLACE ops.

    bad holds (partition, op index) pairs whose blob does not decode. Their
    data is read back from the new image (full payloads only write dst
    extents), stored uncompressed and re-hashed; the data_offset of every
    later op shifts by the size difference. Cheaper than regenerating the
    whole payload, and update_engine always supports REPLACE.
    """
    plan = []                     # (orig_offset, orig_length, raw bytes | None)
    state = {"orig": 0, "new": 0}

    def partition(wire, value):
        if wire != 2:
            return None
        name = next((v.decode() for n, v in _fields(value) if n == 1), "")
        index = [0]

        def operation(w, op):
            i = index[0]
            index[0] += 1
            kind = offset = length = 0
            extents = []
            for n, v in _fields(op):
                if n == 1:
                    kind = v
                elif n == 2:
                    offset = v
                elif n == 3:
                    length = v
                elif n == 6:
                    ext = dict(_fields(v))
                    extents.append((ext.get(1, 0), ext.get(2, 0)))
            if not length:
                return None
            if offset != state["orig"]:
                raise ValueError(f"{name} op {i}: non-sequential data offset")
            state["orig"] += length
            new_offset = state["new"]
            raw = None
            if (name, i) in bad:
                if kind not in (0, OP_REPLACE_XZ, OP_REPLACE_BZ):
                    raise ValueError(f"{name} op {i}: cannot repair op type {kind}")
                def read_raw():
                    with open(images[name], "rb") as img:
                        parts = []
                        for start, count in extents:
                            img.seek(start * block_size)
                            parts.append(img.read(count * block_size))
                    return b"".join(parts)

                # Two identical reads: a single misread must not be baked in.
                raw = read_raw()
                for _ in range(4):
                    again = read_raw()
                    if again == raw:
                        break
                    raw = again
                else:
                    raise ValueError(f"{name} op {i}: image reads differently every time")
                if len(raw) != sum(c for _, c in extents) * block_size:
                    raise ValueError(f"{name} op {i}: image shorter than its extents")
            plan.append((offset, length, raw))
            state["new"] += len(raw) if raw is not None else length

            def varint_field(num, value):
                return _encode_varint(num << 3) + _encode_varint(value)

            replace = {2: varint_field(2, new_offset)}
            if raw is not None:
                replace[1] = varint_field(1, 0)                 # REPLACE
                replace[3] = varint_field(3, len(raw))
                replace[8] = _encode_bytes(8, hashlib.sha256(raw).digest())
            seen = set()

            def swap(num):
                def handler(w_, v_):
                    seen.add(num)
                    return replace[num]
                return handler

            body = _rewrite_fields(op, {n: swap(n) for n in replace})
            for n in sorted(set(replace) - seen):
                body += replace[n]
            return _encode_bytes(8, body)

        return _encode_bytes(13, _rewrite_fields(value, {8: operation}))

    new_manifest = _rewrite_fields(manifest, {13: partition})
    repaired = sum(1 for _, _, raw in plan if raw is not None)
    if repaired != len(bad):
        raise ValueError(f"expected to repair {len(bad)} blob(s), found {repaired}")

    with src.open("rb") as f, dst.open("wb") as o:
        head = f.read(24)
        if head[:4] != PAYLOAD_MAGIC or struct.unpack(">Q", head[4:12])[0] != 2:
            raise ValueError("expected a major version 2 payload")
        old_size = struct.unpack(">Q", head[12:20])[0]
        if struct.unpack(">I", head[20:24])[0]:
            raise ValueError("payload is already signed")
        f.seek(24 + old_size)
        o.write(head[:12] + struct.pack(">Q", len(new_manifest)) + head[20:24] + new_manifest)
        for _, length, raw in plan:
            if raw is None:
                left = length
                while left:
                    chunk = f.read(min(left, 16 * 1024 * 1024))
                    if not chunk:
                        raise IOError("payload data ends early")
                    o.write(chunk)
                    left -= len(chunk)
            else:
                f.seek(length, os.SEEK_CUR)
                o.write(raw)
    return repaired


def write_dynamic_info(meta: dict, path: Path) -> None:
    dyn = meta["dynamic"]
    groups = [g for g in dyn["groups"] if g["name"] != "default"]
    lines = [f"super_partition_groups={' '.join(g['name'] for g in groups)}"]
    for g in groups:
        lines.append(f"{g['name']}_size={g['size']}")
        lines.append(f"{g['name']}_partition_list={' '.join(g['partitions'])}")
    if dyn["snapshot_enabled"]:
        lines.append("virtual_ab=true")
    if dyn["vabc_enabled"]:
        lines.append("virtual_ab_compression=true")
        if dyn["vabc_compression_param"]:
            lines.append(f"virtual_ab_compression_method={dyn['vabc_compression_param']}")
        if dyn["cow_version"]:
            lines.append(f"virtual_ab_cow_version={dyn['cow_version']}")
        if dyn["compression_factor"]:
            lines.append(f"virtual_ab_compression_factor={dyn['compression_factor']}")
        # Threaded/batched COW writes are chosen on the device from its
        # ro.virtual_ab.* properties; delta_generator has no input for them.
    path.write_text("\n".join(lines) + "\n")


def write_postinstall(meta: dict, path: Path) -> bool:
    lines = []
    for p in meta["partitions"]:
        if not p["run_postinstall"]:
            continue
        n = p["name"]
        lines.append(f"RUN_POSTINSTALL_{n}=true")
        if p["postinstall_path"]:
            lines.append(f"POSTINSTALL_PATH_{n}={p['postinstall_path']}")
        if p["filesystem_type"]:
            lines.append(f"FILESYSTEM_TYPE_{n}={p['filesystem_type']}")
        if p["postinstall_optional"]:
            lines.append(f"POSTINSTALL_OPTIONAL_{n}=true")
    if not lines:
        return False
    path.write_text("\n".join(lines) + "\n")
    return True


def pad_to_block(src: Path, dst: Path, block_size: int) -> bool:
    """Copy src zero-padded to a whole number of blocks.

    Payload partitions must be block aligned, but raw firmware such as MTK
    lk.img is often not; fastboot pads it implicitly, the payload cannot. The
    extra zeros land in the unused tail of the partition.
    """
    size = src.stat().st_size
    if size % block_size == 0:
        return False
    shutil.copyfile(src, dst)
    with dst.open("ab") as f:
        f.write(b"\0" * (block_size - size % block_size))
    return True


def data_offset(path: Path, info: zipfile.ZipInfo) -> int:
    with path.open("rb") as f:
        f.seek(info.header_offset)
        header = f.read(30)
    name_len, extra_len = struct.unpack_from("<HH", header, 26)
    return info.header_offset + 30 + name_len + extra_len


def _payload_ops(manifest: bytes):
    """Yield (partition, index, type, data_offset, data_length, sha256) per operation."""
    for num, part in _fields(manifest):
        if num != 13:
            continue
        name, index = "", 0
        for n, val in _fields(part):
            if n == 1:
                name = val.decode()
            elif n == 8:
                op = {1: 0, 2: 0, 3: 0, 8: b""}
                extents = []
                for k, v in _fields(val):
                    if k in op:
                        op[k] = v
                    elif k == 6:
                        ext = dict(_fields(v))
                        extents.append((ext.get(1, 0), ext.get(2, 0)))
                yield name, index, op[1], op[2], op[3], op[8], extents
                index += 1


def verify_payload_blobs(payload: Path, start: int = 0, bad_ops: set | None = None,
                         decode: bool = True, images: dict | None = None,
                         block_size: int = 4096,
                         transient: list | None = None) -> tuple[int, list[str]]:
    """Check every operation blob against its hash and decode REPLACE_XZ/BZ.

    The static delta_generator occasionally emits an XZ blob that is hashed
    as written but cannot be decoded; update_engine then aborts the install
    with kDownloadOperationExecutionError (28), e.g. odm op 792 on popsicle.

    With images ({partition: image path}) the decoded data of every operation
    is also compared with the image blocks it writes. A blob can be hashed and
    decodable yet hold wrong data (a bit flipped in memory while the payload
    was generated or repaired); the install would then fail the partition
    hash at the very end, on the device.

    transient collects the failures that did not repeat when the operation
    was checked again: misreads of this run, a sign of unstable hardware.
    """
    import bz2
    import lzma
    from concurrent.futures import ThreadPoolExecutor

    fd = os.open(payload, os.O_RDONLY)
    header = os.pread(fd, 24, start)
    if header[:4] != PAYLOAD_MAGIC:
        os.close(fd)
        raise ValueError("invalid payload magic")
    manifest_size = struct.unpack_from(">Q", header, 12)[0]
    signature_size = struct.unpack_from(">I", header, 20)[0]
    base = start + 24 + manifest_size + signature_size
    manifest = os.pread(fd, manifest_size, start + 24)
    ops = [op for op in _payload_ops(manifest) if op[4]]

    def check(op, fd):
        name, index, kind, offset, length, digest, extents = op
        blob = os.pread(fd, length, base + offset)
        if len(blob) != length:
            return f"{name} op {index}: truncated blob"
        if digest and hashlib.sha256(blob).digest() != digest:
            return f"{name} op {index}: hash mismatch"
        if not decode:
            return None
        data = blob
        try:
            if kind == OP_REPLACE_XZ:
                data = lzma.decompress(blob, format=lzma.FORMAT_XZ)
            elif kind == OP_REPLACE_BZ:
                data = bz2.decompress(blob)
        except (lzma.LZMAError, OSError, ValueError, EOFError) as exc:
            return f"{name} op {index}: {'xz' if kind == OP_REPLACE_XZ else 'bz2'} decode failed ({exc})"
        if images is not None and kind in (0, OP_REPLACE_XZ, OP_REPLACE_BZ):
            with open(images[name], "rb") as img:
                parts = []
                for first, count in extents:
                    img.seek(first * block_size)
                    parts.append(img.read(count * block_size))
            if data != b"".join(parts):
                return f"{name} op {index}: data differs from the image"
        return None

    try:
        # lzma/bz2 release the GIL, so threads decode in parallel.
        workers = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 4)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(lambda op: check(op, fd), ops))
        # A failure that does not repeat was a misread during this check, not
        # a bad blob: only what fails twice more, one at a time, is reported.
        for i, op in enumerate(ops):
            if results[i] and check(op, fd) is None and check(op, fd) is None:
                if transient is not None:
                    transient.append(results[i])
                results[i] = None
    finally:
        os.close(fd)
    bad = []
    for op, message in zip(ops, results):
        if message:
            bad.append(message)
            if bad_ops is not None:
                bad_ops.add((op[0], op[1]))
    return len(ops), bad


def check_image_hashes(manifest: bytes, images: dict) -> None:
    """Fail when an image no longer hashes to what the payload recorded for it.

    Together with the per-operation comparison of verify_payload_blobs this
    proves the payload rebuilds exactly the partitions its manifest promises.
    """
    from concurrent.futures import ThreadPoolExecutor

    expected = {}
    for num, part in _fields(manifest):
        if num != 13:
            continue
        name, size, digest = "", 0, b""
        for n, val in _fields(part):
            if n == 1:
                name = val.decode()
            elif n == 7:
                info = dict(_fields(val))
                size, digest = info.get(1, 0), info.get(2, b"")
        if digest:
            expected[name] = (size, digest)

    def image_hash(name):
        size, _ = expected[name]
        digest = hashlib.sha256()
        with open(images[name], "rb") as img:
            left = size
            while left:
                chunk = img.read(min(left, 16 * 1024 * 1024))
                if not chunk:
                    break
                digest.update(chunk)
                left -= len(chunk)
        return digest.digest()

    names = [name for name in expected if name in images]
    with ThreadPoolExecutor(max_workers=build_cpus()) as pool:
        results = list(pool.map(image_hash, names))
    wrong = [name for name, digest in zip(names, results) if digest != expected[name][1]]
    if wrong:
        raise RuntimeError(
            f"image(s) do not match the payload hash: {', '.join(wrong)}; they changed or were "
            "misread while the payload was generated")


# --------------------------------------------------------------------------
# Packaging
#
# The unsigned payload is read once: the signed payload is streamed from it
# straight into the package (locked or not), and the finished package is read
# once to check payload.bin and to sign the whole file. delta_generator's
# hash / insert / verify passes, its properties pass, a plain zip written twice
# and the locked copy of it cost about five more writes and eight more reads of
# a multi-GB payload, and the disk is what limits parallel builds on the farm.
# --------------------------------------------------------------------------

IO_CHUNK = 16 * 1024 * 1024
ZIP_DATE = (2009, 1, 1, 0, 0, 0)


def _rsa_sign(key, digest: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, utils
    return key.sign(digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))


def _rsa_verify(key, signature: bytes, digest: bytes) -> bool:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, utils
    try:
        key.public_key().verify(signature, digest, padding.PKCS1v15(),
                                utils.Prehashed(hashes.SHA256()))
        return True
    except Exception:
        return False


def _signatures_blob(signature: bytes) -> bytes:
    """Signatures message as delta_generator writes it for one RSA signature."""
    entry = _encode_bytes(2, signature) + b"\x1d" + struct.pack("<I", len(signature))
    return _encode_bytes(1, entry)


def _with_signature_fields(manifest: bytes, offset: int, size: int) -> bytes:
    """The manifest with signatures_offset (4) and signatures_size (5) set,
    placed in field order as delta_generator serializes them."""
    fields = (_encode_varint(4 << 3) + _encode_varint(offset)
              + _encode_varint(5 << 3) + _encode_varint(size))
    placed = []

    def field(num):
        def handler(wire, value):
            if num in (4, 5):
                return b""
            if placed:
                return None
            placed.append(True)
            if wire == 0:
                raw = _encode_varint(num << 3) + _encode_varint(value)
            elif wire == 2:
                raw = _encode_bytes(num, value)
            else:
                raw = _encode_varint(num << 3 | wire) + value
            return fields + raw
        return handler

    handlers = {num: field(num) for num in (4, 5)}
    handlers.update({num: field(num) for num in range(6, 64)})
    out = _rewrite_fields(manifest, handlers)
    return out if placed else out + fields


def _signature_data(blob: bytes) -> bytes:
    """The one signature of a Signatures message (signatures[0].data)."""
    found = [v for n, entry in _fields(blob) if n == 1 for k, v in _fields(entry) if k == 2]
    if len(found) != 1:
        raise ValueError(f"expected one signature, found {len(found)}")
    return found[0]


class SignedPayload:
    """payload.bin signed with our key, streamed from an unsigned payload whose
    manifest is replaced (brillo_update_payload's hash / sign / insert flow,
    with the same bytes as delta_generator, in a single read)."""

    def __init__(self, unsigned: Path, manifest: bytes, key):
        self.src, self.key = unsigned, key
        with unsigned.open("rb") as f:
            head = f.read(24)
        if head[:4] != PAYLOAD_MAGIC or struct.unpack(">Q", head[4:12])[0] != 2:
            raise ValueError("expected a major version 2 payload")
        if struct.unpack(">I", head[20:24])[0]:
            raise ValueError("payload is already signed")
        # Data offsets are relative to the end of the metadata, so the blobs
        # stay valid under the new manifest.
        self.data_start = 24 + struct.unpack(">Q", head[12:20])[0]
        self.data_size = unsigned.stat().st_size - self.data_start
        self.sig_size = len(_signatures_blob(bytes(key.key_size // 8)))
        manifest = _with_signature_fields(manifest, self.data_size, self.sig_size)
        self.metadata = PAYLOAD_MAGIC + struct.pack(">QQI", 2, len(manifest), self.sig_size) + manifest
        self.metadata_hash = hashlib.sha256(self.metadata).digest()
        self.metadata_signature = _signatures_blob(_rsa_sign(key, self.metadata_hash))
        self.size = len(self.metadata) + 2 * self.sig_size + self.data_size
        self.ops = sorted((offset, length, digest)
                          for _, _, _, offset, length, digest, _ in _payload_ops(manifest) if length)
        self.file_hash = None
        self.misreads = 0

    def properties(self, file_hash: bytes) -> dict:
        """payload_properties.txt values, as delta_generator --properties_file writes them."""
        return {
            "FILE_HASH": base64.b64encode(file_hash).decode(),
            "FILE_SIZE": self.size,
            "METADATA_HASH": base64.b64encode(self.metadata_hash).decode(),
            "METADATA_SIZE": len(self.metadata),
        }

    def chunks(self):
        """Yield the signed payload. Every blob is checked against its manifest
        hash on the way: this read is the one that ends up in the package."""
        payload_hash = hashlib.sha256(self.metadata)
        file_hash = hashlib.sha256(self.metadata + self.metadata_signature)
        yield self.metadata + self.metadata_signature
        fd = os.open(self.src, os.O_RDONLY)
        try:
            buf = bytearray()
            pos = 0

            def read(offset: int, length: int) -> bytes:
                data = os.pread(fd, length, self.data_start + offset)
                if len(data) != length:
                    raise RuntimeError("unsigned payload is truncated")
                return data

            def emit(data: bytes):
                payload_hash.update(data)
                file_hash.update(data)
                buf.extend(data)

            for offset, length, digest in self.ops:
                if offset < pos:
                    continue    # a blob shared with the previous operation
                while pos < offset:
                    n = min(IO_CHUNK, offset - pos)
                    emit(read(pos, n))
                    pos += n
                blob = read(offset, length)
                if digest and hashlib.sha256(blob).digest() != digest:
                    # Verified on disk before; a mismatch now is a misread.
                    for _ in range(2):
                        self.misreads += 1
                        blob = read(offset, length)
                        if hashlib.sha256(blob).digest() == digest:
                            break
                    else:
                        raise RuntimeError(f"payload blob at {offset} keeps failing its hash "
                                           "while it is packaged; check the RAM and the disk")
                emit(blob)
                pos += length
                if len(buf) >= IO_CHUNK:
                    yield bytes(buf)
                    buf.clear()
            while pos < self.data_size:
                n = min(IO_CHUNK, self.data_size - pos)
                emit(read(pos, n))
                pos += n
            if buf:
                yield bytes(buf)
        finally:
            os.close(fd)
        signature = _signatures_blob(_rsa_sign(self.key, payload_hash.digest()))
        if len(signature) != self.sig_size:
            raise RuntimeError("payload signature does not have the reserved size")
        file_hash.update(signature)
        self.file_hash = file_hash.digest()
        yield signature


class _ChunkReader:
    """File-like read() over an iterator of byte chunks. Like a file it returns
    n bytes until the end: the zip AES keystream advances per 16-byte block,
    so the writer must get whole blocks."""

    def __init__(self, chunks):
        self._chunks, self._buf, self._pos = iter(chunks), b"", 0

    def read(self, n: int) -> bytes:
        parts = []
        while n > 0:
            if self._pos >= len(self._buf):
                self._buf, self._pos = next(self._chunks, b""), 0
                if not self._buf:
                    break
            part = self._buf[self._pos:self._pos + n]
            self._pos += len(part)
            n -= len(part)
            parts.append(part)
        return parts[0] if len(parts) == 1 else b"".join(parts)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _file_chunks(path: Path):
    with path.open("rb") as f:
        while chunk := f.read(IO_CHUNK):
            yield chunk


def _stored_header_size(name: str, size: int) -> int:
    """Local header size zipfile writes for a stored entry of this size."""
    info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
    info.file_size = info.compress_size = size
    info.CRC = 0
    return len(info.FileHeader(size * 1.05 > zipfile.ZIP64_LIMIT))


def write_package(out: Path, signed: SignedPayload, extra_props: dict, metadata: dict,
                  metadata_pb: bytes | None, extras: dict | None = None,
                  lock: dict | None = None) -> str | None:
    """Write the OTA zip in one pass over the payload; returns the package id
    when locked (lock: secret, device, timestamp, products for write_locked).

    ota-property-files holds the offsets of the plain zip, as before; zipfile
    places entries deterministically, so they are computed instead of read
    back from a first copy of the package.
    """
    def props_text(file_hash: bytes) -> bytes:
        values = {**signed.properties(file_hash), **extra_props}
        return "".join(f"{k}={v}\n" for k, v in values.items()).encode()

    def metadata_entries(property_files: str, streaming: str) -> list:
        values = {
            "ota-property-files": property_files.ljust(PROPERTY_FILES_WIDTH),
            "ota-streaming-property-files": streaming.ljust(PROPERTY_FILES_WIDTH),
        }
        md = dict(metadata)
        md.update(values)
        entries = []
        if metadata_pb is not None:
            entries.append((METADATA_PB_NAME, rewrite_metadata_pb(metadata_pb, values)))
        entries.append((METADATA_NAME, "".join(f"{k}={v}\n" for k, v in sorted(md.items())).encode()))
        return entries

    extras = extras or {}
    props_size = len(props_text(bytes(32)))
    # Fastboot-only data (super layout, images the payload lacks) goes last so
    # the OTA entries keep their offsets; update_engine and VSTeam look
    # entries up by name and ignore these.
    layout = ([("payload.bin", signed.size), ("payload_properties.txt", props_size)]
              + [(name, len(data)) for name, data in metadata_entries("", "")]
              + [(name, Path(path).stat().st_size) for name, path in extras.items()])
    offsets, pos = {}, 0
    for name, size in layout:
        offsets[name] = pos + _stored_header_size(name, size)
        pos = offsets[name] + size
    sizes = dict(layout)
    entries = []
    for name in ("payload.bin", "payload_properties.txt", METADATA_NAME, METADATA_PB_NAME):
        if name in offsets:
            key = {METADATA_NAME: "metadata", METADATA_PB_NAME: "metadata.pb"}.get(name, name)
            entries.append(f"{key}:{offsets[name]}:{sizes[name]}")
    streaming = ",".join(entries)
    property_files = (f"payload_metadata.bin:{offsets['payload.bin']}:"
                      f"{len(signed.metadata) + signed.sig_size},{streaming}")
    if len(property_files) > PROPERTY_FILES_WIDTH:
        raise RuntimeError("ota-property-files exceeds reserved width")
    md_entries = metadata_entries(property_files, streaming)
    if [len(d) for _, d in md_entries] != [sizes[n] for n, _ in md_entries]:
        raise RuntimeError("metadata size changed with its offsets")

    def props_chunks():
        if signed.file_hash is None:
            raise RuntimeError("payload_properties.txt written before payload.bin")
        yield props_text(signed.file_hash)

    contents = ([("payload.bin", signed.chunks), ("payload_properties.txt", props_chunks)]
                + [(name, lambda data=data: iter([data])) for name, data in md_entries]
                + [(name, lambda path=path: _file_chunks(Path(path))) for name, path in extras.items()])

    if lock:
        from ota_zipcrypt import write_locked
        return write_locked(out, [(name, sizes[name], lambda gen=gen: _ChunkReader(gen()))
                                  for name, gen in contents], **lock)

    with zipfile.ZipFile(out, "w", allowZip64=True) as zf:
        for name, gen in contents:
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_STORED
            info.file_size = sizes[name]
            with zf.open(info, "w") as dst:
                for chunk in gen():
                    dst.write(chunk)
    with zipfile.ZipFile(out) as zf:
        for name, offset in offsets.items():
            info = zf.getinfo(name)
            if data_offset(out, info) != offset or info.file_size != sizes[name]:
                raise RuntimeError(f"{name}: written at another offset than its metadata says")
    return None


def _whole_file_cms(key_path: Path, cert: Path, key, digest: bytes, log) -> bytes:
    """What openssl cms -sign -noattr outputs for content with this SHA-256.

    Without signed attributes the DER does not depend on the content except
    for the RSA signature at its very end, so openssl signs a stand-in and our
    signature of the real digest takes its place.
    """
    probe_data = b"VS whole-file signature probe\n"
    with tempfile.TemporaryDirectory() as tmp:
        probe, out = Path(tmp) / "probe", Path(tmp) / "probe.p7s"
        probe.write_bytes(probe_data)
        run(["openssl", "cms", "-sign", "-binary", "-noattr", "-nosmimecap",
             "-md", "sha256", "-outform", "DER", "-signer", str(cert), "-inkey", str(key_path),
             "-in", str(probe), "-out", str(out)], log)
        template = out.read_bytes()
    n = key.key_size // 8
    if template[-n:] != _rsa_sign(key, hashlib.sha256(probe_data).digest()):
        raise RuntimeError("openssl cms output does not end with the signature")
    return template[:-n] + _rsa_sign(key, digest)


def _append_whole_file_signature(zip_path: Path, signature: bytes) -> None:
    prefix = b"Signed by VSTeam\x00"
    signature_start = len(signature) + 6
    comment_size = len(prefix) + signature_start
    if comment_size > 0xFFFF:
        raise RuntimeError("signature too large for zip comment")
    footer = struct.pack("<HHH", signature_start, 0xFFFF, comment_size)
    with zip_path.open("ab") as f:
        f.write(struct.pack("<H", comment_size))
        f.write(prefix + signature + footer)


def _check_end_record(zip_path: Path) -> int:
    size = zip_path.stat().st_size
    with zip_path.open("rb") as f:
        f.seek(size - 22)
        eocd = f.read(22)
    if eocd[:4] != b"PK\x05\x06" or eocd[20:22] != b"\x00\x00":
        raise RuntimeError("unexpected zip end record; cannot sign")
    return size


def sign_whole_file(zip_path: Path, key: Path, cert: Path, log) -> None:
    """Append a signapk -w style signature in the zip comment.

    Signed data is the whole archive except the 2-byte EOCD comment length,
    matching what recovery's verifier and RecoverySystem.verifyPackage check.
    """
    size = _check_end_record(zip_path)
    os.truncate(zip_path, size - 2)
    with tempfile.NamedTemporaryFile(suffix=".p7s", delete=False) as tmp:
        sig_path = Path(tmp.name)
    try:
        run([
            "openssl", "cms", "-sign", "-binary", "-noattr", "-nosmimecap",
            "-md", "sha256", "-outform", "DER",
            "-signer", str(cert), "-inkey", str(key),
            "-in", str(zip_path), "-out", str(sig_path),
        ], log)
        signature = sig_path.read_bytes()
    finally:
        sig_path.unlink(missing_ok=True)
    _append_whole_file_signature(zip_path, signature)


def finish_package(zip_path: Path, want: bytes, key_path: Path, cert: Path, key,
                   zip_secret: str | None, log) -> int:
    """Last gate and whole-file signature in one read of the finished package.

    payload.bin as stored (decrypted when locked) must still be the payload
    that was hashed while it was written; the same read hashes the archive for
    the signature in the zip comment. Returns the misreads that were retried.
    """
    from ota_zipcrypt import AUTH_LEN, SALT_LEN, _AesCtr, _entry_keys, package_password, read_id_entry

    size = _check_end_record(zip_path)
    with zipfile.ZipFile(zip_path) as zf:
        info = zf.getinfo("payload.bin")
    start = data_offset(zip_path, info)
    end = start + info.compress_size
    aes_key = None
    if info.flag_bits & 1:
        with zip_path.open("rb") as f:
            f.seek(start)
            salt, verifier = f.read(SALT_LEN), f.read(2)
        aes_key, _, expected = _entry_keys(package_password(read_id_entry(zip_path), zip_secret), salt)
        if verifier != expected:
            raise ValueError("payload.bin: wrong zip secret")
        start += SALT_LEN + 2
        end -= AUTH_LEN

    misreads = 0
    for _ in range(2):
        whole, payload = hashlib.sha256(), hashlib.sha256()
        ctr = _AesCtr(aes_key) if aes_key else None
        carry = b""     # the keystream advances per 16-byte block
        with zip_path.open("rb") as f:
            pos = 0
            while pos < size - 2:
                chunk = f.read(min(IO_CHUNK, size - 2 - pos))
                if not chunk:
                    raise IOError(f"{zip_path.name}: unexpected end of file")
                whole.update(chunk)
                lo, hi = max(start, pos), min(end, pos + len(chunk))
                if lo < hi:
                    piece = chunk[lo - pos:hi - pos]
                    if ctr:
                        piece = carry + piece
                        cut = len(piece) if hi == end else len(piece) - len(piece) % 16
                        carry = piece[cut:]
                        piece = ctr.process(piece[:cut])
                    payload.update(piece)
                pos += len(chunk)
        if payload.digest() == want:
            break
        misreads += 1
    else:
        raise RuntimeError("payload.bin inside the finished package does not match its "
                           "FILE_HASH; the package was corrupted while it was written")

    try:
        signature = _whole_file_cms(key_path, cert, key, whole.digest(), log)
    except RuntimeError as exc:
        log.write(f"whole-file signature: {exc}; signing with openssl over the file\n".encode())
        sign_whole_file(zip_path, key_path, cert, log)
        return misreads
    os.truncate(zip_path, size - 2)
    _append_whole_file_signature(zip_path, signature)
    return misreads


def io_turn(log):
    """Wait for this build's turn at the disk-heavy part of packaging.

    $VS_OTA_IO_LOCK names a lock shared by the farm slots and $VS_OTA_IO_SLOTS
    (default 1) how many packages are written at once; the rest of a build
    (extraction, patching, delta_generator compressing) keeps running in
    parallel. Without $VS_OTA_IO_LOCK there is no waiting.

    $VS_OTA_IO_STATE, when set, names a file that shows the farm whether this
    build is "waiting" for its turn or "packing", and since when.
    """
    import contextlib
    import fcntl
    import time

    state_file = os.environ.get("VS_OTA_IO_STATE", "").strip()

    def show(state):
        if not state_file:
            return
        try:
            with open(state_file + ".part", "w") as handle:
                handle.write(f"{state}\t{int(time.time())}\n")
            os.replace(state_file + ".part", state_file)
        except OSError:
            pass

    @contextlib.contextmanager
    def turn():
        path = os.environ.get("VS_OTA_IO_LOCK", "").strip()
        if not path:
            yield
            return
        slots = max(1, int(os.environ.get("VS_OTA_IO_SLOTS", "1") or 1))
        started = time.monotonic()
        fds = [os.open(f"{path}.{i}", os.O_RDWR | os.O_CREAT, 0o644) for i in range(slots)]
        held = None
        waiting = False
        try:
            while held is None:
                for fd in fds:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        held = fd
                        break
                    except BlockingIOError:
                        pass
                else:
                    if not waiting:
                        show("waiting")
                        waiting = True
                    time.sleep(5)
            show("packing")
            log.write(f"[io] packaging turn after {time.monotonic() - started:.0f}s wait\n".encode())
            log.flush()
            yield
        finally:
            for fd in fds:
                os.close(fd)    # closing releases the lock
            if state_file:
                with contextlib.suppress(OSError):
                    os.remove(state_file)

    return turn()


def package_payload(args, meta: dict, names: list, paths: list, unsigned: Path,
                    out: Path, log) -> None:
    """Verify the generated payload, sign it and write the finished package."""
    from cryptography.hazmat.primitives import serialization

    work = unsigned.parent
    # The payload is signed after the manifest rewrite below, so a bad blob
    # is repaired here (repair_payload) instead of regenerating it all.
    # Every blob must hash, decode and match the image blocks it writes;
    # a repaired payload is checked again in full, since the repair itself
    # reads the image and can go wrong the same way.
    images = dict(zip(names, paths))
    misreads = 0
    check_image_hashes(read_manifest(unsigned), images)
    for attempt in range(1, MAX_REPAIR_ROUNDS + 2):
        bad_ops: set = set()
        transient: list = []
        total, bad = verify_payload_blobs(unsigned, bad_ops=bad_ops, images=images,
                                          block_size=meta["block_size"], transient=transient)
        log.write(f"verify: {total} blobs, {len(bad)} bad, "
                  f"{len(transient)} misread while checking\n".encode())
        for line in bad:
            log.write(f"  {line}\n".encode())
        for line in transient:
            log.write(f"  (not repeated) {line}\n".encode())
        misreads += len(transient)
        log.flush()
        if not bad_ops:
            break
        if attempt > MAX_REPAIR_ROUNDS:
            raise RuntimeError(
                f"payload still has {len(bad)} corrupt blob(s) after {MAX_REPAIR_ROUNDS} "
                f"repair rounds ({'; '.join(bad[:5])}); the build machine is corrupting "
                "data in memory, check its RAM")
        repaired_path = work / "payload_repaired.bin"
        fixed = repair_payload(unsigned, repaired_path, read_manifest(unsigned), bad_ops,
                               images, meta["block_size"])
        log.write(f"repaired {fixed} blob(s) as REPLACE\n".encode())
        os.replace(repaired_path, unsigned)
    dynamic = {p for g in meta["dynamic"]["groups"] for p in g["partitions"]}
    manifest, estimates = raise_cow_estimates(read_manifest(unsigned), dynamic)
    for name, est in estimates.items():
        log.write(f"cow estimate {name}: {est}\n".encode())
    ota_key = serialization.load_pem_private_key(Path(args.key).read_bytes(), password=None)
    signed = SignedPayload(unsigned, manifest, ota_key)
    if not _rsa_verify(ota_key, _signature_data(signed.metadata_signature), signed.metadata_hash):
        raise RuntimeError("metadata signature does not verify with the OTA key")
    log.write(f"signed payload: {signed.size} bytes, metadata {len(signed.metadata)} "
              f"bytes, signature blob {signed.sig_size} bytes\n".encode())

    zip_md = dict(meta.get("zip_metadata") or {})
    for k in ("ota-property-files", "ota-streaming-property-files",
              "ota-required-cache", "pre-build", "pre-build-incremental"):
        zip_md.pop(k, None)
    zip_md.setdefault("ota-type", "AB")
    zip_md["ota-required-cache"] = "0"
    if meta["max_timestamp"]:
        zip_md["post-timestamp"] = str(meta["max_timestamp"])
    if args.device:
        zip_md.setdefault("pre-device", args.device)
    for item in args.set or []:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise ValueError(f"invalid --set value: {item}")
        zip_md[key] = value

    extras = {}
    for item in args.super_layout or []:
        slot, sep, layout_file = item.partition("=")
        if not sep or slot not in ("a", "b"):
            raise ValueError(f"invalid --super-layout value: {item}")
        extras[SUPER_LAYOUT_NAME.format(slot=slot)] = Path(layout_file)
    if args.fastboot_images:
        for img in sorted(Path(args.fastboot_images).glob("*.img")):
            if img.stem not in names and img.stem not in FASTBOOT_IMAGE_SKIP:
                extras[f"{FASTBOOT_IMAGE_DIR}{img.name}"] = img
    log.write(f"fastboot extras: {', '.join(extras) or 'none'}\n".encode())

    tmp_out = out.with_name(out.name + ".tmp")
    stock_pb = meta.get("metadata_pb")
    lock = None
    if args.zip_secret:
        # Password-lock the entries; only the server can derive the password.
        from ota_zipcrypt import load_secret
        lock = {"secret": load_secret(args.zip_secret), "device": zip_md.get("pre-device", ""),
                "timestamp": args.timestamp or 0, "products": zip_md.get("vs-products", "")}
    try:
        package_id = write_package(tmp_out, signed, meta.get("payload_properties") or {}, zip_md,
                                   base64.b64decode(stock_pb) if stock_pb else None, extras, lock)
        misreads += signed.misreads
        if package_id:
            log.write(f"zip locked, id {package_id}\n".encode())
        unsigned.unlink()
        # Last gate: what was written (and encrypted) must still be the payload
        # hashed while it was written; the same read signs the whole file.
        misreads += finish_package(tmp_out, signed.file_hash, Path(args.key), Path(args.cert),
                                   ota_key, args.zip_secret, log)
    except BaseException:
        tmp_out.unlink(missing_ok=True)
        raise
    log.write(f"package payload hash ok; misreads seen during the build: {misreads}\n".encode())
    if misreads:
        print(f"ota_payload: warning: {misreads} misread(s) were caught and retried during "
              "this build; the machine is corrupting data in memory, test its RAM",
              file=sys.stderr)
    os.replace(tmp_out, out)



def cmd_build(args) -> int:
    meta = json.loads(Path(args.meta).read_text())
    if args.drop:
        # Static partitions left out of the OTA (HyperOS MTK: lk), so the
        # update never writes them.
        dynamic = {p for g in meta["dynamic"]["groups"] for p in g["partitions"]}
        bad = sorted(set(args.drop) & dynamic)
        if bad:
            print(f"ota_payload: --drop cannot remove dynamic partition(s): {', '.join(bad)}",
                  file=sys.stderr)
            return 1
        meta["partitions"] = [p for p in meta["partitions"] if p["name"] not in args.drop]
    if args.partial:
        # Partial OTA (e.g. only the Magisk-patched boot): update_engine writes
        # these partitions and copies every other one from the running slot.
        keep = set(args.partial)
        unknown = sorted(keep - {p["name"] for p in meta["partitions"]})
        if unknown:
            print(f"ota_payload: --partial names unknown partition(s): {', '.join(unknown)}",
                  file=sys.stderr)
            return 1
        meta["partitions"] = [p for p in meta["partitions"] if p["name"] in keep]
        for g in meta["dynamic"]["groups"]:
            g["partitions"] = [p for p in g["partitions"] if p in keep]
        meta["partial_update"] = True
        meta["minor_version"] = max(int(meta["minor_version"] or 0), PARTIAL_MINOR_VERSION)
    if args.group_size:
        # Forced super size (VS_SUPER_SIZE): the stock group size no longer
        # fits the device, so the payload carries the one derived from it.
        groups = [g for g in meta["dynamic"]["groups"]
                  if g["name"] != "default" and int(g["size"]) > 0]
        if len(groups) != 1:
            print(f"ota_payload: --group-size needs exactly one dynamic group, "
                  f"found {len(groups)}", file=sys.stderr)
            return 1
        groups[0]["size"] = args.group_size
    if args.timestamp:
        # The patched ROM carries its build time in ro.build.date.utc, which is
        # newer than the stock payload; update_engine refuses payloads older
        # than the running build (error 51) unless downgrades are allowed.
        stamp = max(int(args.timestamp), int(meta["max_timestamp"] or 0))
        meta["max_timestamp"] = stamp
        for part in meta["partitions"]:
            if part["version"]:
                part["version"] = str(max(stamp, int(part["version"]) if part["version"].isdigit() else 0))
        if meta.get("metadata_pb"):
            meta["metadata_pb"] = base64.b64encode(
                restamp_metadata_pb(base64.b64decode(meta["metadata_pb"]), stamp)).decode()
    # Same flow on every brand: stock vbmeta, raised COW estimates, bad blobs
    # repaired as REPLACE, signed after the manifest rewrite.
    # First directory wins: firmware-update holds the shipped images; later
    # directories are fallbacks (mics of workspaces where lk.img was moved).
    image_dirs = [Path(d) for d in args.images]
    work = Path(args.workdir)
    out = Path(args.out)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    log_path = Path(args.log) if args.log else work / "ota_payload.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    names, paths = [], []
    missing = []
    for p in meta["partitions"]:
        img = next((d / f"{p['name']}.img" for d in image_dirs
                    if (d / f"{p['name']}.img").is_file()), None)
        if img is None:
            missing.append(p["name"])
            continue
        padded = work / f"padded_{img.name}"
        if pad_to_block(img, padded, meta["block_size"]):
            img = padded
        names.append(p["name"])
        paths.append(str(img))
    if missing:
        print(f"ota_payload: missing image(s): {', '.join(missing)}", file=sys.stderr)
        return 1

    dyn_info = work / "dynamic_partitions_info.txt"
    write_dynamic_info(meta, dyn_info)
    postinstall = work / "postinstall_config.txt"
    timestamps = ",".join(
        f"{p['name']}:{p['version']}" for p in meta["partitions"] if p["version"]
    )

    unsigned = work / "payload_unsigned.bin"
    cmd = [
        args.delta_generator,
        f"--out_file={unsigned}",
        f"--partition_names={':'.join(names)}",
        f"--new_partitions={':'.join(paths)}",
        f"--dynamic_partition_info_file={dyn_info}",
        f"--max_timestamp={meta['max_timestamp']}",
    ]
    if timestamps:
        cmd.append(f"--partition_timestamps={timestamps}")
    if meta["partial_update"]:
        cmd += ["--is_partial_update", f"--minor_version={meta['minor_version']}"]
    if write_postinstall(meta, postinstall):
        cmd.append(f"--new_postinstall_config_file={postinstall}")

    with log_path.open("ab") as log:
        limit_cpu(log)
        run(cmd, log)
        # From here on the build reads and writes the whole payload; slots
        # take turns (io_turn) so concurrent builds do not saturate the disk.
        with io_turn(log):
            package_payload(args, meta, names, paths, unsigned, out, log)

    shutil.rmtree(work, ignore_errors=True)
    digest = hashlib.sha256(Path(args.cert).read_bytes()).hexdigest()[:12]
    print(f"{out.name}: {len(names)} partition(s), cert {digest}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("meta")
    m.add_argument("payload", help="payload.bin or the OTA zip holding it")
    m.add_argument("out")
    m.add_argument("--stock-dir", help="directory holding the extracted stock OTA zip "
                                       "(not needed when payload is the OTA zip itself)")

    v = sub.add_parser("verify")
    v.add_argument("file", help="payload.bin or OTA zip")

    c = sub.add_parser("otacerts")
    c.add_argument("--cert", required=True)
    c.add_argument("--out", required=True)

    b = sub.add_parser("build")
    b.add_argument("--meta", required=True)
    b.add_argument("--images", required=True, action="append",
                   help="image directory; repeat for fallbacks, first match wins")
    b.add_argument("--key", required=True)
    b.add_argument("--cert", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--workdir", required=True)
    b.add_argument("--delta-generator", required=True)
    b.add_argument("--device", default="")
    b.add_argument("--zip-secret", metavar="FILE",
                   help="password-lock the package (see ota_zipcrypt.py); "
                        "$VS_OTA_ZIP_SECRET overrides the file")
    b.add_argument("--log")
    b.add_argument("--timestamp", type=int,
                   help="build time of the patched ROM (unix seconds); raises the payload timestamps")
    b.add_argument("--group-size", type=int, metavar="BYTES",
                   help="override the size of the dynamic partition group in the payload")
    b.add_argument("--super-layout", action="append", metavar="SLOT=FILE",
                   help="sparse LP layout (metadata only) of slot a or b, for flashing "
                        "super in pieces from the bootloader; give one per slot")
    b.add_argument("--fastboot-images", metavar="DIR",
                   help="ship every DIR/*.img the payload does not contain (dynamic "
                        "partitions outside the OTA, magisk.img) for fastboot installs")
    b.add_argument("--drop", action="append", default=[], metavar="PARTITION",
                   help="leave a static partition of the stock payload out of the OTA")
    b.add_argument("--partial", action="append", default=[], metavar="PARTITION",
                   help="build a partial OTA holding only these partitions; the device "
                        "keeps the rest from its running slot")
    b.add_argument("--set", action="append", metavar="KEY=VALUE",
                   help="extra metadata entry, e.g. vs-version=7.8.ST")

    args = ap.parse_args()
    try:
        return {"meta": cmd_meta, "otacerts": cmd_otacerts, "build": cmd_build,
                "verify": cmd_verify}[args.cmd](args)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ota_payload: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
