#!/usr/bin/env python3
"""Enable selected OPlus Camera vendor tags in an extracted ROM tree."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import tempfile
from pathlib import Path


# libOplusSecurity.so version 1 de-obfuscates a 32-byte constant, then uses
# every second byte as an AES-128 key. Camera's UpdateHelper also accepts the
# resulting configuration as plain UTF-8 JSON, so re-encryption is unnecessary.
OPLUS_V1_AES_KEY = bytes.fromhex("6f2170406c247525535e4326412a4d28")

REQUIRED_TAGS = {
    "com.oplus.ai.scene.preset.support": ("Byte", "1"),
    "com.oplus.ai.scene.preset.party.support": ("Byte", "1"),
    "com.oplus.ai.scene.preset.video.support": ("Byte", "1"),
    "com.oplus.camera.support.auto.preset": ("Byte", "1"),
    "com.oplus.camera.preset.scene.detect.support": ("Byte", "1"),
    "com.oplus.camera.master.preset.function.flags": ("Int32", "3"),
    "com.oplus.use.hasselblad.style.support": ("Byte", "1"),
    "com.oplus.camera.support.custom.hasselblad.watermark": ("Byte", "1"),
    "com.oplus.camera.support.custom.hasselblad.watermark.sellmode.default.open": ("Byte", "0"),
    "com.oplus.hasselblad.watermark.guide.support": ("Byte", "0"),
}

# Absence is meaningful for CameraUnitUtils: do not replace a missing tag with
# an explicit zero when matching the stock device configuration.
REMOVED_TAGS = {
    "com.oplus.professional.use.hasselblad.style.support",
}


class ConfigError(RuntimeError):
    pass


def decrypt_aes_ecb(ciphertext: bytes) -> bytes:
    if not ciphertext or len(ciphertext) % 16:
        raise ConfigError("invalid OPlus encrypted payload length")

    try:
        from Crypto.Cipher import AES  # type: ignore

        return AES.new(OPLUS_V1_AES_KEY, AES.MODE_ECB).decrypt(ciphertext)
    except ImportError:
        command = [
            "openssl", "enc", "-d", "-aes-128-ecb", "-nopad",
            "-K", OPLUS_V1_AES_KEY.hex(),
        ]
        try:
            result = subprocess.run(
                command, input=ciphertext, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, check=False,
            )
        except FileNotFoundError as exc:
            raise ConfigError("PyCryptodome or openssl is required to decode OPlus config") from exc
        if result.returncode:
            detail = result.stderr.decode("utf-8", "replace").strip()
            raise ConfigError(f"openssl AES decode failed: {detail}")
        return result.stdout


def remove_pkcs7(data: bytes) -> bytes:
    if not data:
        raise ConfigError("decoded OPlus config is empty")
    padding = data[-1]
    if not 1 <= padding <= 16 or data[-padding:] != bytes([padding]) * padding:
        raise ConfigError("decoded OPlus config has invalid PKCS#7 padding")
    return data[:-padding]


def decode_config(raw: bytes) -> tuple[list[dict[str, object]], str]:
    # Prefer plain JSON. This also makes the patch idempotent on subsequent builds.
    try:
        parsed = json.loads(raw.decode("utf-8-sig"))
        source_format = "plain JSON"
    except (UnicodeDecodeError, json.JSONDecodeError):
        if len(raw) < 24 or raw[0] != 1 or raw[1] != 1:
            raise ConfigError("unsupported config format (expected JSON or OPlus v1 envelope)")
        plaintext = remove_pkcs7(decrypt_aes_ecb(raw[4:-4]))
        try:
            parsed = json.loads(plaintext.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigError("AES output is not a valid camera vendor-tag JSON") from exc
        source_format = "OPlus encrypted v1"

    if not isinstance(parsed, list) or not all(isinstance(item, dict) for item in parsed):
        raise ConfigError("camera vendor-tag config must be a JSON array of objects")
    return parsed, source_format


def patch_entries(entries: list[dict[str, object]]) -> tuple[int, int, int]:
    before = len(entries)
    entries[:] = [
        item for item in entries
        if not isinstance(item.get("VendorTag"), str) or item.get("VendorTag") not in REMOVED_TAGS
    ]
    removed = before - len(entries)
    matched: dict[str, int] = {tag: 0 for tag in REQUIRED_TAGS}
    changed = 0

    for item in entries:
        tag = item.get("VendorTag")
        if not isinstance(tag, str) or tag not in matched:
            continue
        matched[tag] += 1
        value_type, value = REQUIRED_TAGS[tag]
        desired = {"Type": value_type, "Count": "1", "Value": value}
        for key, value in desired.items():
            if item.get(key) != value:
                item[key] = value
                changed += 1

    added = 0
    for tag, count in matched.items():
        if count:
            continue
        value_type, value = REQUIRED_TAGS[tag]
        entries.append({"VendorTag": tag, "Type": value_type, "Count": "1", "Value": value})
        added += 1
    return added, changed, removed


def atomic_write(path: Path, data: bytes, original_mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_name, stat.S_IMODE(original_mode))
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path, help="extracted odm oplus_camera_config")
    args = parser.parse_args()

    path: Path = args.config
    if not path.is_file():
        parser.error(f"config not found: {path}")

    original_stat = path.stat()
    try:
        entries, source_format = decode_config(path.read_bytes())
        added, changed, removed = patch_entries(entries)
        output = (json.dumps(entries, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        atomic_write(path, output, original_stat.st_mode)
    except ConfigError as exc:
        parser.exit(1, f"error: {exc}\n")

    print(
        f"patched {path}: source={source_format}, entries={len(entries)}, "
        f"added={added}, updated_fields={changed}, removed={removed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
