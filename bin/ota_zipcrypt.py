#!/usr/bin/env python3
"""Password-lock a VS OTA zip with WinZip AES-256 (AE-2).

payload.bin, payload_properties.txt, the metadata and the fastboot-only vs/
entries are rewritten as AES-encrypted STORED entries, so 7-Zip/WinRAR still open the package with the
password. The encryption is AES-CTR, which lets VSTeam decrypt payload.bin at
any offset while staging it, without extracting a second copy.

The password is never stored: it is HMAC-SHA256(secret, "VSOTA2|id|device"),
where id/device come from the plaintext entry META-INF/com/bhlnk/vsota.prop.
The secret is shared with the API only, which hands the password to VSTeam and
VSTool over its secure channel for licensed devices; no client can derive it.
It comes from $VS_OTA_ZIP_SECRET_V2 or bin/ota_keys/zip_secret_v2.

Version 1 packages (context VSOTA1, $VS_OTA_ZIP_SECRET or bin/ota_keys/zip_secret)
used a secret that was also compiled into VSTeam; they can still be read here.
The id of a version 2 package ends with a MAC of its random part, which is how
the API tells the two apart.

Usage:
  ota_zipcrypt.py lock <plain.zip> <locked.zip> --secret-file F --device D
  ota_zipcrypt.py password <locked.zip> --secret-file F
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import os
import secrets
import struct
import sys
import zipfile
from array import array
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

ID_ENTRY = "META-INF/com/bhlnk/vsota.prop"
VERSION = 2
LOCKED_ENTRIES = ("payload.bin", "payload_properties.txt",
                  "META-INF/com/android/metadata", "META-INF/com/android/metadata.pb")
LOCKED_PREFIX = "vs/"    # fastboot-only entries read by VSTool

SALT_LEN = 16            # AES-256
PBKDF2_ROUNDS = 1000     # fixed by the WinZip AES spec
AUTH_LEN = 10
AES_METHOD = 99
AES_EXTRA = struct.pack("<HHH2sBH", 0x9901, 7, 2, b"AE", 3, 0)  # AE-2, AES-256, stored
CHUNK = 4 * 1024 * 1024
DOS_DATE = (2009 - 1980) << 9 | 1 << 5 | 1  # 2009-01-01, like the plain OTA entries


def load_secret(secret_file: str | None, version: int = VERSION) -> bytes:
    """The secret of a package version; `secret_file` is the version 1 file, the
    version 2 one sits next to it as <name>_v2."""
    suffix = "" if version == 1 else f"_V{version}"
    value = os.environ.get(f"VS_OTA_ZIP_SECRET{suffix}", "").strip()
    if not value and secret_file:
        path = Path(secret_file + suffix.lower())
        value = path.read_text().strip() if path.is_file() else ""
    if len(value) < 32:
        raise ValueError(f"zip secret (version {version}) missing or shorter than 32 characters")
    return value.encode()


def new_package_id(secret: bytes) -> str:
    rand = secrets.token_hex(12)
    return rand + hmac.new(secret, f"VSOTA2-ID|{rand}".encode(), hashlib.sha256).hexdigest()[:8]


def derive_password(secret: bytes, package_id: str, device: str, version: int = VERSION) -> str:
    msg = f"VSOTA{version}|{package_id}|{device}".encode()
    return hmac.new(secret, msg, hashlib.sha256).hexdigest()[:40]


def package_password(prop: dict, secret_file: str | None) -> str:
    """Password of the package this vsota.prop belongs to."""
    version = int(prop.get("version", 1))
    return derive_password(load_secret(secret_file, version), prop["id"], prop["device"], version)


class _AesCtr:
    """WinZip AES keystream: AES-ECB of a little-endian counter starting at 1."""

    def __init__(self, key: bytes):
        if sys.byteorder != "little":
            raise RuntimeError("counter packing assumes a little-endian host")
        self._ecb = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        self._counter = 1

    def process(self, data: bytes) -> bytes:
        blocks = (len(data) + 15) // 16
        counters = array("Q", bytes(16 * blocks))
        counters[0::2] = array("Q", range(self._counter, self._counter + blocks))
        self._counter += blocks
        stream = self._ecb.update(counters.tobytes())[:len(data)]
        n = len(data)
        return (int.from_bytes(data, "little") ^ int.from_bytes(stream, "little")).to_bytes(n, "little")


def _entry_keys(password: str, salt: bytes) -> tuple[bytes, bytes, bytes]:
    dk = hashlib.pbkdf2_hmac("sha1", password.encode(), salt, PBKDF2_ROUNDS, 66)
    return dk[:32], dk[32:64], dk[64:66]


class _Writer:
    """Minimal zip64-aware writer for stored (plain or AES) entries."""

    def __init__(self, out):
        self.out = out
        self.central: list[bytes] = []

    def add(self, name: str, size: int, reader, password: str | None) -> None:
        name_b = name.encode()
        offset = self.out.tell()
        encrypted = password is not None
        csize = size + (SALT_LEN + 2 + AUTH_LEN if encrypted else 0)
        zip64 = csize >= 0xFFFFFFFF or offset >= 0xFFFFFFFF
        crc = 0
        if not encrypted:
            data = reader.read(size)
            crc = zipfile.crc32(data)

        flags = 0x0800 | (1 if encrypted else 0)
        method = AES_METHOD if encrypted else 0
        version = 51 if encrypted else (45 if zip64 else 20)
        extra = (AES_EXTRA if encrypted else b"")
        local_extra = extra + (struct.pack("<HHQQ", 1, 16, size, csize) if zip64 else b"")
        sizes = (0xFFFFFFFF, 0xFFFFFFFF) if zip64 else (csize, size)
        self.out.write(struct.pack("<IHHHHHIIIHH", 0x04034b50, version, flags,
                                   method, 0, DOS_DATE, crc, *sizes, len(name_b), len(local_extra)))
        self.out.write(name_b + local_extra)

        if encrypted:
            salt = secrets.token_bytes(SALT_LEN)
            aes_key, mac_key, verifier = _entry_keys(password, salt)
            ctr, mac = _AesCtr(aes_key), hmac.new(mac_key, digestmod=hashlib.sha1)
            self.out.write(salt + verifier)
            left = size
            while left:
                block = reader.read(min(CHUNK, left))
                if not block:
                    raise IOError(f"{name}: unexpected end of data")
                left -= len(block)
                enc = ctr.process(block)
                mac.update(enc)
                self.out.write(enc)
            self.out.write(mac.digest()[:AUTH_LEN])
        else:
            self.out.write(data)

        z64 = b""
        if zip64:
            z64 = struct.pack("<HHQQQ", 1, 24, size, csize, offset)
        cd_sizes = (0xFFFFFFFF, 0xFFFFFFFF) if zip64 else (csize, size)
        cd_offset = 0xFFFFFFFF if zip64 else offset
        cd_extra = extra + z64
        self.central.append(struct.pack(
            "<IHHHHHHIIIHHHHHII", 0x02014b50, version | 0x0300, version, flags, method, 0,
            DOS_DATE, crc, *cd_sizes, len(name_b), len(cd_extra), 0, 0, 0, 0o100644 << 16,
            cd_offset) + name_b + cd_extra)

    def finish(self) -> None:
        cd_start = self.out.tell()
        for record in self.central:
            self.out.write(record)
        cd_size = self.out.tell() - cd_start
        count = len(self.central)
        if cd_start >= 0xFFFFFFFF or count >= 0xFFFF:
            z64_pos = self.out.tell()
            self.out.write(struct.pack("<IQHHIIQQQQ", 0x06064b50, 44, 45, 45, 0, 0,
                                       count, count, cd_size, cd_start))
            self.out.write(struct.pack("<IIQI", 0x07064b50, 0, z64_pos, 1))
            count16, start32 = 0xFFFF, 0xFFFFFFFF
        else:
            count16, start32 = count, cd_start
        self.out.write(struct.pack("<IHHHHIIH", 0x06054b50, 0, 0, count16, count16,
                                   cd_size, start32, 0))


def lock_zip(src: Path, dst: Path, secret: bytes, device: str, timestamp: int = 0,
             products: str = "") -> str:
    """Rewrite src into dst with the OTA entries password-locked; returns the id.

    `timestamp` is the build time of the ROM (ro.hypervs.build.timestamp). It
    is written in the clear so the server can tell a rebuild from the package
    of the same name it replaced. `products` are the names the phone goes by
    (vs-products: codename, platform, ossi), also in the clear: the server
    learns from them that a licence used as "canoe" and as "PLK110" is one phone.
    """
    with zipfile.ZipFile(src) as zin:
        for info in zin.infolist():
            if info.compress_type != zipfile.ZIP_STORED:
                raise ValueError(f"{info.filename}: only stored entries are supported")
        entries = [(info.filename, info.file_size, lambda info=info: zin.open(info))
                   for info in zin.infolist()]
        return write_locked(dst, entries, secret, device, timestamp, products)


def write_locked(dst: Path, entries, secret: bytes, device: str, timestamp: int = 0,
                 products: str = "") -> str:
    """Write a locked package straight from its entries; returns the id.

    entries are (name, size, open) with open() returning a reader of exactly
    size bytes, so a multi-GB payload is streamed into the package without
    first being written to a plain zip (see lock_zip for the other arguments).
    """
    if not device:
        raise ValueError("device is required to lock an OTA package")
    package_id = new_package_id(secret)
    password = derive_password(secret, package_id, device)
    prop = f"version={VERSION}\nid={package_id}\ndevice={device}\n"
    if timestamp:
        prop += f"timestamp={int(timestamp)}\n"
    if products:
        prop += f"products={products}\n"
    prop = prop.encode()

    with dst.open("wb") as out:
        writer = _Writer(out)
        # Plaintext first: clients send it to the server to get the password.
        writer.add(ID_ENTRY, len(prop), _Bytes(prop), None)
        for name, size, opener in entries:
            with opener() as reader:
                writer.add(name, size, reader,
                           password if name in LOCKED_ENTRIES
                           or name.startswith(LOCKED_PREFIX) else None)
        writer.finish()
    return package_id


def payload_sha256(path: Path, secret_file: str | None) -> bytes:
    """SHA-256 of payload.bin as stored in an OTA zip, decrypted when locked."""
    with zipfile.ZipFile(path) as zf:
        info = zf.getinfo("payload.bin")
    locked = bool(info.flag_bits & 1)
    digest = hashlib.sha256()
    with path.open("rb") as f:
        f.seek(info.header_offset)
        header = f.read(30)
        if header[:4] != b"PK\x03\x04":
            raise ValueError("payload.bin: broken zip entry")
        name_len, extra_len = struct.unpack("<HH", header[26:30])
        f.seek(info.header_offset + 30 + name_len + extra_len)
        left = info.compress_size
        ctr = None
        if locked:
            salt = f.read(SALT_LEN)
            aes_key, _, verifier = _entry_keys(
                package_password(read_id_entry(path), secret_file), salt)
            if f.read(2) != verifier:
                raise ValueError("payload.bin: wrong zip secret")
            ctr = _AesCtr(aes_key)
            left -= SALT_LEN + 2 + AUTH_LEN
        while left:
            block = f.read(min(CHUNK, left))
            if not block:
                raise IOError("payload.bin: unexpected end of data")
            left -= len(block)
            digest.update(ctr.process(block) if ctr else block)
    return digest.digest()


class _Bytes:
    def __init__(self, data: bytes):
        self.data, self.pos = data, 0

    def read(self, n: int) -> bytes:
        chunk = self.data[self.pos:self.pos + n]
        self.pos += len(chunk)
        return chunk


def read_id_entry(path: Path) -> dict:
    with zipfile.ZipFile(path) as zf:
        text = zf.read(ID_ENTRY).decode()
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    lock = sub.add_parser("lock")
    lock.add_argument("src")
    lock.add_argument("dst")
    lock.add_argument("--secret-file")
    lock.add_argument("--device", required=True)
    pw = sub.add_parser("password", help="print the password of a locked package")
    pw.add_argument("zip")
    pw.add_argument("--secret-file")
    args = parser.parse_args()

    if args.cmd == "lock":
        package_id = lock_zip(Path(args.src), Path(args.dst), load_secret(args.secret_file), args.device)
        print(f"locked {Path(args.dst).name}: id {package_id}")
    else:
        print(package_password(read_id_entry(Path(args.zip)), args.secret_file))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as exc:
        print(f"ota_zipcrypt: {exc}", file=sys.stderr)
        raise SystemExit(1)
