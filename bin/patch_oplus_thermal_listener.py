#!/usr/bin/env python3
"""Make Oplus' thermal listener accept the extended Horae tsensor payload."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


CLASS_RELATIVE_PATH = Path(
    "com/oplus/thermalcontrol/IThermalStatusListener$Stub.smali"
)
PATCH_MARKER = "VS_THERMAL_LISTENER_EXTENDED_TSENSOR_PAYLOAD"

STOCK_BLOCK = """    .local v2, \"_arg0\":I
    invoke-virtual {p2}, Landroid/os/Parcel;->enforceNoDataAvail()V
"""

PATCHED_BLOCK = f"""    .local v2, \"_arg0\":I

    # {PATCH_MARKER}
    # Newer Horae sends notifyTsensorTemp(temp, back, frame, front), while
    # this framework API exposes only notifyTsensorTemp(temp). Consume the
    # three extension integers only for the observed 12-byte payload.
    invoke-virtual {{p2}}, Landroid/os/Parcel;->dataAvail()I

    move-result v3

    if-eqz v3, :cond_vs_tsensor_payload_done

    const/16 v4, 0xc

    if-ne v3, v4, :cond_vs_tsensor_payload_done

    invoke-virtual {{p2}}, Landroid/os/Parcel;->readInt()I

    invoke-virtual {{p2}}, Landroid/os/Parcel;->readInt()I

    invoke-virtual {{p2}}, Landroid/os/Parcel;->readInt()I

    :cond_vs_tsensor_payload_done
    invoke-virtual {{p2}}, Landroid/os/Parcel;->enforceNoDataAvail()V
"""


def find_target(decoded_root: Path) -> Path:
    matches = sorted(decoded_root.glob(f"smali*/{CLASS_RELATIVE_PATH}"))
    if len(matches) != 1:
        raise RuntimeError(
            "expected exactly one IThermalStatusListener$Stub.smali, "
            f"found {len(matches)}"
        )
    return matches[0]


def validate_context(text: str) -> None:
    if ".method public whitelist onTransact(" not in text:
        raise RuntimeError("onTransact method not found")
    if "TRANSACTION_notifyTsensorTemp:I = 0x6" not in text:
        raise RuntimeError("notifyTsensorTemp transaction 6 not found")
    if (
        "Lcom/oplus/thermalcontrol/IThermalStatusListener$Stub;"
        "->notifyTsensorTemp(I)V"
    ) not in text:
        raise RuntimeError("notifyTsensorTemp(int) dispatch not found")


def on_transact_body(text: str) -> str:
    start = text.find(".method public whitelist onTransact(")
    if start < 0:
        raise RuntimeError("onTransact method not found")
    end = text.find("\n.end method", start)
    if end < 0:
        raise RuntimeError("onTransact method is incomplete")
    return text[start:end]


def is_structurally_patched(text: str) -> bool:
    body = on_transact_body(text)
    notify = body.find(
        "Lcom/oplus/thermalcontrol/IThermalStatusListener$Stub;"
        "->notifyTsensorTemp(I)V"
    )
    if notify < 0:
        return False

    # Limit detection to transaction 6 immediately before its dispatch. Labels
    # are deliberately ignored because baksmali renames them after each build.
    prefix = body[max(0, notify - 1600) : notify]
    arg_start = prefix.rfind('.local v2, "_arg0":I')
    if arg_start < 0:
        return False
    transaction = prefix[arg_start:]
    return (
        transaction.count("Landroid/os/Parcel;->dataAvail()I") == 1
        and "const/16 v4, 0xc" in transaction
        and transaction.count("Landroid/os/Parcel;->readInt()I") == 3
        and transaction.count("Landroid/os/Parcel;->enforceNoDataAvail()V") == 1
    )


def patch(decoded_root: Path) -> tuple[Path, str]:
    target = find_target(decoded_root)
    text = target.read_text(encoding="utf-8")
    validate_context(text)

    if PATCH_MARKER in text or is_structurally_patched(text):
        if PATCH_MARKER in text and (
            text.count(PATCH_MARKER) != 1 or PATCHED_BLOCK not in text
        ):
            raise RuntimeError("thermal listener patch marker is inconsistent")
        return target, "already-patched"

    if text.count(STOCK_BLOCK) != 1:
        raise RuntimeError(
            "expected one stock notifyTsensorTemp Parcel check; refusing partial patch"
        )

    patched = text.replace(STOCK_BLOCK, PATCHED_BLOCK, 1)
    if patched.count(PATCH_MARKER) != 1:
        raise RuntimeError("thermal listener patch verification failed")

    target.write_text(patched, encoding="utf-8")
    return target, "patched"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("decoded_root", type=Path)
    args = parser.parse_args()

    try:
        target, status = patch(args.decoded_root.resolve())
    except (OSError, RuntimeError) as exc:
        print(f"thermal listener patch failed: {exc}", file=sys.stderr)
        return 1

    print(f"thermal listener {status}: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
