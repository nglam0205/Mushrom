#!/usr/bin/env python3
"""Keep FCM alive through ColorOS' intelligent deep sleep (com.oplus.battery).

On CN builds deep sleep switches Wi-Fi off entirely after the screen has been
off for a while, so Google push only arrives at the next unlock. Make it use
its uid firewall path instead (Wi-Fi stays up, every uid except the whitelist
loses network), put GMS in that whitelist, and ship the switch off by default.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


CONTROLLER = Path("com/oplus/deepsleep/ControllerCenter.smali")
CONTROLLER_CLASS = "Lcom/oplus/deepsleep/ControllerCenter;"
GMS_MARKER = "VS_DEEPSLEEP_GMS_WHITELIST"
WHITELIST_METHOD = ".method private isUseNetworkDisableWhiteList()Z"
PKG_WHITE_METHOD = ".method private addPkgWhiteArray(Ljava/util/List;)V"

DEFAULT_STOCK = '"intelligent_deep_sleep_mode,1"'
DEFAULT_PATCHED = '"intelligent_deep_sleep_mode,0"'

RECOVERY_SOURCE = '.source "RecoverySettingsService.java"'
RECOVERY_MARKER = "VS_DEEPSLEEP_RESTORE_OFF"

WHITELIST_BODY = f"""{WHITELIST_METHOD}
    .locals 1

    const/4 v0, 0x1

    return v0
.end method"""

GMS_BLOCK = f"""    # {GMS_MARKER}
    # Firewall whitelist entries are uid strings; add GMS so mtalk (FCM)
    # keeps its connection while every other uid is cut off.
    :try_start_vs_gms
    iget-object v0, p0, {CONTROLLER_CLASS}->mContext:Landroid/content/Context;

    invoke-virtual {{v0}}, Landroid/content/Context;->getPackageManager()Landroid/content/pm/PackageManager;

    move-result-object v0

    const-string v1, "com.google.android.gms"

    const/4 v2, 0x0

    invoke-virtual {{v0, v1, v2}}, Landroid/content/pm/PackageManager;->getPackageUid(Ljava/lang/String;I)I

    move-result v0

    invoke-static {{v0}}, Ljava/lang/String;->valueOf(I)Ljava/lang/String;

    move-result-object v0

    invoke-interface {{p1, v0}}, Ljava/util/List;->contains(Ljava/lang/Object;)Z

    move-result v1

    if-nez v1, :cond_vs_gms_done

    invoke-interface {{p1, v0}}, Ljava/util/List;->add(Ljava/lang/Object;)Z
    :try_end_vs_gms
    .catch Ljava/lang/Exception; {{:try_start_vs_gms .. :try_end_vs_gms}} :cond_vs_gms_done

    :cond_vs_gms_done
    return-void
"""


def method_span(text: str, header: str) -> tuple[int, int]:
    start = text.find(header)
    if start < 0:
        raise RuntimeError(f"{header.split()[-1]} not found")
    end = text.find("\n.end method", start)
    if end < 0:
        raise RuntimeError(f"{header.split()[-1]} is incomplete")
    return start, end + len("\n.end method")


def patch_controller(root: Path) -> str:
    matches = sorted(root.glob(f"smali*/{CONTROLLER}"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one ControllerCenter.smali, found {len(matches)}")
    target = matches[0]
    text = target.read_text(encoding="utf-8")

    start, end = method_span(text, WHITELIST_METHOD)
    text = text[:start] + WHITELIST_BODY + text[end:]

    if GMS_MARKER not in text:
        start, end = method_span(text, PKG_WHITE_METHOD)
        body = text[start:end]
        if body.count("    return-void\n") != 1:
            raise RuntimeError("addPkgWhiteArray: expected a single return-void")
        if ".locals" in body and int(re.search(r"\.locals (\d+)", body).group(1)) < 3:
            raise RuntimeError("addPkgWhiteArray: fewer than 3 locals")
        body = body.replace("    return-void\n", GMS_BLOCK, 1)
        text = text[:start] + body + text[end:]

    target.write_text(text, encoding="utf-8")
    return f"whitelist mode + GMS uid: {target}"


def patch_default(root: Path) -> list[str]:
    done = []
    for path in sorted(root.glob("smali*/**/*.smali")):
        text = path.read_text(encoding="utf-8")
        if DEFAULT_STOCK in text:
            path.write_text(text.replace(DEFAULT_STOCK, DEFAULT_PATCHED), encoding="utf-8")
            done.append(f"default off: {path}")
        elif DEFAULT_PATCHED in text:
            done.append(f"default off: already patched: {path}")
    return done


def find_switch_writer(root: Path) -> str | None:
    """Return the Utils method writing Secure deepsleep_switch_state (obfuscated)."""
    for path in sorted(root.glob("smali*/**/*.smali")):
        text = path.read_text(encoding="utf-8")
        if '"deepsleep_switch_state"' not in text:
            continue
        cls = re.search(r"^\.class [^\n]*?(L[^;\s]+;)", text, re.M)
        for m in re.finditer(r"^\.method public static (\S+)\(Landroid/content/Context;Z\)V\n(.*?)^\.end method", text, re.M | re.S):
            body = m.group(2)
            if '"deepsleep_switch_state"' in body and "Settings$Secure;->putInt" in body:
                return f"{cls.group(1)}->{m.group(1)}(Landroid/content/Context;Z)V"
    return None


def patch_recovery(root: Path) -> str:
    writer = find_switch_writer(root)
    if not writer:
        return "restore-defaults: switch writer not found; skip"
    for path in sorted(root.glob("smali*/**/RecoverySettingsService.smali")):
        text = path.read_text(encoding="utf-8")
        if RECOVERY_SOURCE not in text:
            continue
        if RECOVERY_MARKER in text:
            return f"restore-defaults: already patched: {path}"
        call = re.search(r"^    invoke-static \{([vp]\d+), ([vp]\d+)\}, " + re.escape(writer) + r"\n", text, re.M)
        if not call:
            return "restore-defaults: writer call not found; skip"
        reg = call.group(2)
        # Only flip a register that was loaded with a plain true right before.
        prior = re.findall(r"^    const/4 " + re.escape(reg) + r", (0x[01])\n", text[: call.start()], re.M)
        if not prior or prior[-1] != "0x1":
            return "restore-defaults: unexpected switch value; skip"
        repl = (
            f"    # {RECOVERY_MARKER}\n"
            f"    const/4 {reg}, 0x0\n\n"
            f"{call.group(0)}\n"
            f"    const/4 {reg}, 0x1\n"
        )
        text = text[: call.start()] + repl + text[call.end():]
        path.write_text(text, encoding="utf-8")
        return f"restore-defaults off: {path}"
    return "restore-defaults: RecoverySettingsService not found; skip"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("decoded_root", type=Path)
    args = parser.parse_args()
    root = args.decoded_root.resolve()

    try:
        print(patch_controller(root))
        for line in patch_default(root) or ["default: intelligent_deep_sleep_mode,1 not found; skip"]:
            print(line)
        print(patch_recovery(root))
    except (OSError, RuntimeError, AttributeError) as exc:
        print(f"deepsleep net patch failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
