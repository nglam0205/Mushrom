#!/usr/bin/env python3
import re
import sys
from pathlib import Path


# Oplus's channel-lock plugin (subsys-channel-lock-plugin.jar) drives the
# carrier/device-lock restriction that the phone process enforces through
# OplusTelephonyManager.setCarrierLockRestricted(feature, value, ...). When the
# modem reports a device-lock state (or IMSI lock is in use),
# CarrierLockManager.isDeviceLockEffect() returns true and features 3 (SMS in)
# and 4 (SMS out) get restricted globally, so SMSDispatcher fails every send
# with "Sending sms is disabled." and calls are limited to a whitelist.
#
# On a ported ROM the modem/my_carrier image can raise this lock spuriously
# while the LockAssistant UI that would clear it is disabled, leaving SMS and
# calls silently blocked. Force isDeviceLockEffect() to report "not locked" so
# no restriction is ever applied. This is the single root gate: it clears both
# the SMS block and the call whitelist.
TARGET = "CarrierLockManager.smali"
METHOD = re.compile(
    r"(?ms)^(?P<header>\.method[^\n]*\bisDeviceLockEffect\(I\)Z)\n"
    r"(?P<body>.*?)^\.end method$"
)
MARKER = "# VS_PATCH_CARRIERLOCK"
REGISTERS = re.compile(r"^(\s*)\.(registers|locals) (\d+)$", re.M)


def stub() -> str:
    return (
        f"    {MARKER}: force device-lock ineffective (unblock SMS + calls)\n"
        "    const/4 v0, 0x0\n"
        "    return v0\n\n"
    )


def ensure_register(body: str) -> str:
    # We use v0; the method is private isDeviceLockEffect(I) so p0/p1 exist and
    # at least one local (v0) is already declared, but guard the minimum anyway.
    match = REGISTERS.search(body)
    if not match:
        raise RuntimeError("isDeviceLockEffect has no .registers/.locals line")
    indent, kind, count = match.group(1), match.group(2), int(match.group(3))
    if kind == "registers":
        # p0 (this) + p1 (int) => need >= 3 to have v0 free.
        if count < 3:
            return body[:match.start()] + f"{indent}.registers 3" + body[match.end():]
    else:  # locals
        if count < 1:
            return body[:match.start()] + f"{indent}.locals 1" + body[match.end():]
    return body


def first_instruction(body: str) -> int:
    offset = 0
    for line in body.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and not stripped.startswith((".", "#")):
            return offset
        offset += len(line)
    raise RuntimeError("isDeviceLockEffect has no instructions")


def patch_file(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    match = METHOD.search(source)
    if not match:
        return False
    body = match.group("body")
    if MARKER in body:
        print(f"{path.stem}.isDeviceLockEffect already patched")
        return True

    body = ensure_register(body)
    at = first_instruction(body)
    body = body[:at] + stub() + body[at:]
    path.write_text(
        source[:match.start("body")] + body + source[match.end("body"):],
        encoding="utf-8",
    )
    print(f"{path.stem}.isDeviceLockEffect: force return false (device-lock disabled)")
    return True


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} <decoded subsys-channel-lock-plugin dir>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1])
    targets = [
        path for directory in root.glob("smali*") if directory.is_dir()
        for path in directory.rglob(TARGET)
    ]
    if not targets:
        # Also allow pointing directly at a classout*/ dir (no smali* prefix).
        targets = list(root.rglob(TARGET))
    patched = sum(patch_file(path) for path in targets)
    if not patched:
        print("isDeviceLockEffect not found in CarrierLockManager", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
