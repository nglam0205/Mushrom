#!/usr/bin/env python3
"""Transplant the APK Signing Block (v2/v3/v4 signatures) from the original APK
into an apktool-rebuilt APK.

apktool -c only restores META-INF (v1). Most system APKs are v2/v3-only, so the
rebuilt APK ends up unsigned. System-partition apps are scanned without integrity
verification (unsafeGetCertsWithoutVerification), so the original certificate in
the copied block is accepted even though the content digests no longer match.

Must run after zipalign (zipalign rewrites the archive and drops the block).

usage: copy_apk_sigblock.py <original.apk> <rebuilt.apk>
exit: 0 copied, 2 original has no signing block, 1 error
"""
import struct
import sys

EOCD_MAGIC = b"PK\x05\x06"
BLOCK_MAGIC = b"APK Sig Block 42"


def find_eocd(data):
    # EOCD is 22 bytes + up to 65535 bytes of comment
    start = max(0, len(data) - 22 - 0xFFFF)
    pos = data.rfind(EOCD_MAGIC, start)
    while pos != -1:
        comment_len = struct.unpack_from("<H", data, pos + 20)[0]
        if pos + 22 + comment_len == len(data):
            return pos
        pos = data.rfind(EOCD_MAGIC, start, pos)
    raise ValueError("EOCD not found")


def cd_offset(data, eocd):
    offset = struct.unpack_from("<I", data, eocd + 16)[0]
    if offset == 0xFFFFFFFF:
        raise ValueError("zip64 not supported")
    return offset


def signing_block(data, cd):
    if cd < 32 or data[cd - 16:cd] != BLOCK_MAGIC:
        return None
    size = struct.unpack_from("<Q", data, cd - 24)[0]
    start = cd - size - 8
    if start < 0 or struct.unpack_from("<Q", data, start)[0] != size:
        raise ValueError("corrupt signing block")
    return start


def main(orig_path, out_path):
    with open(orig_path, "rb") as f:
        orig = f.read()
    orig_cd = cd_offset(orig, find_eocd(orig))
    orig_start = signing_block(orig, orig_cd)
    if orig_start is None:
        return 2
    block = orig[orig_start:orig_cd]

    with open(out_path, "rb") as f:
        out = f.read()
    eocd = find_eocd(out)
    cd = cd_offset(out, eocd)
    body_end = signing_block(out, cd)
    if body_end is None:
        body_end = cd

    new_cd = body_end + len(block)
    tail = bytearray(out[cd:])
    struct.pack_into("<I", tail, eocd - cd + 16, new_cd)

    with open(out_path, "wb") as f:
        f.write(out[:body_end])
        f.write(block)
        f.write(tail)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__.strip().splitlines()[-3], file=sys.stderr)
        sys.exit(1)
    try:
        sys.exit(main(sys.argv[1], sys.argv[2]))
    except Exception as e:
        print(f"copy_apk_sigblock: {e}", file=sys.stderr)
        sys.exit(1)
