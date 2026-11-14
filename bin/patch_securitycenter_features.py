#!/usr/bin/env python3
"""Force the Security Center feature gates on.

Usage: patch_securitycenter_features.py <decoded apk dir> [--dry-run]

Security Center decides what Game Turbo, the video toolbox and the power pages
show by reading device_features keys (miui.util.FeatureParser), vendor system
properties and a few codename whitelists. The class and method names around
those reads are R8-generated and change every release, so match the key string
itself: the result of the read that consumes the key is replaced by a constant
and the rest of the method (region checks, negative gates, logging) is left
alone.

Only positive "is this supported" gates are listed. Keys that are negative
(ro.radio.noril, is_blackshark, *.nosupport_*), that hold user/vendor state
(*.enable, persist.vendor.smartchg bitmask, privacy_camera*) or that pick
between two variants of the same feature are deliberately not here.
"""
import re
import sys
from pathlib import Path


# key -> forced value. Boolean reads become 0x1; a key whose value is not 1 is
# read as an int by the app and compared against that value.
GATES = {
    # --- Game Turbo (GameBoosterFeatureUtils)
    "support_game_mi_time": 1,
    "support_game_dim": 1,
    "support_video_dfps": 1,
    "support_mi_game_macro": 1,
    "support_game_gunsight": 1,
    "support_displayfeature_gamemode": 1,
    "support_displayfeature_gamemode_HDR": 1,
    "support_touchfeature_gamemode": 1,
    "support_wifi_low_latency_mode": 1,
    "support_network_rps_mode": 1,
    "support_paper_eyecare": 1,
    "support_autobrightness_optimize": 1,
    "support_disable_ndds_sim": 1,
    "ro.vendor.audio.game.effect": 1,
    "ro.vendor.audio.game.mode": 1,
    "ro.vendor.audio.game.vibrate": 1,
    "ro.vendor.audio.game.spatialmode": 1,
    "ro.vendor.audio.voice.change.support": 1,
    "ro.vendor.audio.voice.change.youme.support": 1,
    "ro.vendor.pq.disp_game_HDR_support": 1,
    "debug.game.video.support": 1,
    # --- Video toolbox
    "is_support_video_tool_box": 1,
    "support_videobox_display_effect": 1,
    "support_videobox_cinema_adapt_ce": 1,
    "support_conversation_toolbox_voiprecord": 1,
    "support_voip_record": 1,
    "ro.vendor.video_box.version": 2,
    "ro.vendor.media.video.style.support": 1,
    "ro.vendor.video.style.by.layer.support": 1,
    "ro.vendor.media.video.frc.support": 1,
    "ro.vendor.media.video.vpp.support": 1,
    "ro.vendor.media.video.meeting.support": 1,
    "ro.vendor.gpp.frc.support": 1,
    "ro.vendor.xiaomi.sr.support": 1,
    "ro.vendor.audio.surround.support": 1,
    "ro.vendor.audio.vocal.support": 1,
    "ro.vendor.audio.sd.support": 1,
    "ro.vendor.audio.dolby.dax.support": 1,
    "ro.vendor.audio.privacy.call.support": 1,
    "ro.vendor.audio.vpnr.support": 1,
    # --- Battery / power modes
    "support_power_save_new": 1,
    "support_wild_boost": 1,
    "support_wild_boost_bat_perf": 1,
    "support_smart_fps": 1,
    "support_hangup_while_screen_off": 1,
    "support_new_silentmode": 1,
    "is_support_superpower": 1,
    "persist.sys.power_mode_support": 1,
    "persist.vendor.night.charge": 1,
    "ro.vendor.persist.hardware.perfflinger.support_overclock": 1,
}

# Codename whitelists: whole method forced to true, located by a log string
# that only the gate itself contains.
WHITELISTS = (
    ("agent privacy control", '"isAgentCtlVisible: device="'),
)

METHOD = re.compile(r"(?ms)^(?P<header>\.method[^\n]+)\n(?P<body>.*?)^\.end method$")
CONST_STRING = re.compile(r'^\s*const-string(?:/jumbo)? (?P<reg>[vp]\d+), "(?P<key>[^"]+)"\s*$')
INVOKE = re.compile(
    r"^\s*invoke-[\w/]+ \{(?P<regs>[^}]*)\}, (?P<owner>L[^;]+;)->\S+\)(?P<ret>\S+)\s*$"
)
# The key is being compared (e.g. a provider dispatching on the method name),
# not looked up; forcing that result would hijack every other branch.
COMPARISONS = ("Ljava/lang/String;", "Landroid/text/TextUtils;", "Ljava/util/Objects;")
MOVE_RESULT = re.compile(r"^(?P<indent>\s*)move-result(?P<object>-object)? (?P<reg>[vp]\d+)\s*$")
# Instructions that end the straight-line window in which the key register
# still holds the key.
STOP = re.compile(r"^\s*(?:return|goto|if-|throw|packed-switch|sparse-switch|\.end method)")
WINDOW = 12


def invoke_registers(spec):
    spec = spec.strip()
    if ".." in spec:
        first, last = (part.strip() for part in spec.split(".."))
        prefix = first[0]
        return {f"{prefix}{n}" for n in range(int(first[1:]), int(last[1:]) + 1)}
    return {part.strip() for part in spec.split(",") if part.strip()}


def overwrites(line, reg):
    # Any instruction whose destination (first operand) is the key register.
    match = re.match(r"^\s*(?!invoke-|if-|[ais]put|return|throw|monitor-)[\w/-]+ ([vp]\d+)\b", line)
    return bool(match) and match.group(1) == reg


def patch_gates(lines, stats, unhandled, label):
    changed = False
    for index, line in enumerate(lines):
        match = CONST_STRING.match(line)
        if not match or match.group("key") not in GATES:
            continue
        key, reg = match.group("key"), match.group("reg")
        value = GATES[key]

        seen = 0
        for cursor in range(index + 1, len(lines)):
            current = lines[cursor]
            if not current.strip() or current.lstrip().startswith((".", ":")):
                if current.lstrip().startswith(".end method"):
                    break
                continue
            seen += 1
            if seen > WINDOW or STOP.match(current) or overwrites(current, reg):
                break
            invoke = INVOKE.match(current)
            if not invoke or reg not in invoke_registers(invoke.group("regs")):
                continue

            if invoke.group("owner") in COMPARISONS:
                break

            result = next(
                (n for n in range(cursor + 1, len(lines)) if lines[n].strip()), None
            )
            moved = MOVE_RESULT.match(lines[result]) if result is not None else None
            returns = invoke.group("ret")
            # A boolean read can only be forced on when the wanted value is 1;
            # keys compared against another number must be read as an int.
            if moved and returns == "Ljava/lang/String;" and value == 1:
                # Properties read as text and compared with "true".
                lines[result] = f"{moved.group('indent')}const-string {moved.group('reg')}, \"true\""
                stats[key] = stats.get(key, 0) + 1
                changed = True
            elif moved and not moved.group("object") and (
                returns == "I" or (returns == "Z" and value == 1)
            ):
                lines[result] = f"{moved.group('indent')}const/16 {moved.group('reg')}, {hex(value)}"
                stats[key] = stats.get(key, 0) + 1
                changed = True
            elif returns != "V":
                unhandled.append(f"{key} read as {returns} in {label}")
            break
    return changed


def patch_whitelists(text, stats):
    def force_true(match):
        header, body = match.group("header"), match.group("body")
        if not header.rstrip().endswith(")Z"):
            return match.group(0)
        for name, marker in WHITELISTS:
            if marker in body:
                stats[name] = stats.get(name, 0) + 1
                return f"{header}\n    .locals 1\n\n    const/4 v0, 0x1\n\n    return v0\n.end method"
        return match.group(0)

    return METHOD.sub(force_true, text)


def main():
    args = [arg for arg in sys.argv[1:] if arg != "--dry-run"]
    dry_run = "--dry-run" in sys.argv[1:]
    if len(args) != 1 or not Path(args[0]).is_dir():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2

    root = Path(args[0])
    needles = tuple(f'"{key}"' for key in GATES) + tuple(marker for _, marker in WHITELISTS)
    stats, unhandled, touched = {}, [], 0

    for smali in sorted(root.glob("smali*/**/*.smali")):
        text = smali.read_text(encoding="utf-8", errors="surrogateescape")
        if not any(needle in text for needle in needles):
            continue

        lines = patch_whitelists(text, stats).split("\n")
        patch_gates(lines, stats, unhandled, str(smali.relative_to(root)))
        patched = "\n".join(lines)
        if patched != text:
            touched += 1
            if not dry_run:
                smali.write_text(patched, encoding="utf-8", errors="surrogateescape")

    for name in sorted(stats):
        print(f"[securitycenter] forced {name} x{stats[name]}")
    for name in sorted(set(GATES) | {name for name, _ in WHITELISTS}):
        if name not in stats:
            print(f"[securitycenter] not present in this build: {name}")
    for entry in sorted(set(unhandled)):
        print(f"[securitycenter] left untouched: {entry}")
    print(f"[securitycenter] {sum(stats.values())} gates forced in {touched} files")

    return 0 if stats else 1


if __name__ == "__main__":
    sys.exit(main())
