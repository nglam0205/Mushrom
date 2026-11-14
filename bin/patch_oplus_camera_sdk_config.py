#!/usr/bin/env python3
"""Patch decoded smali for the OPlus Camera Unit SDK delta backend.

This tool intentionally does not decode, assemble, open, or replace a JAR. The
shared build pipeline owns those steps; this script only edits smali files.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


TARGET_RELATIVE = Path(
    "com/oplus/ocs/camera/consumer/apsAdapter/update/UpdateHelper.smali"
)
HELPER_NAME = "VSTeamConfigOverride.smali"
HELPER_MARKER = "global-delta-v7-on-open"
PATCH_MARKER = "VS_OPLUS_CAMERA_CONFIG_DELTA_V5"
REALTIME_PATCH_MARKER = "VS_OPLUS_CAMERA_CONFIG_REALTIME_V1"
MERGE_METHOD = (
    "Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;"
    "->merge(Landroid/content/Context;Ljava/lang/String;)Ljava/lang/String;"
)
SAFE_HOOK = f"invoke-static {{v13, v4}}, {MERGE_METHOD}"
OLD_GET_METHOD = (
    "Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;"
    "->get(Landroid/content/Context;)Ljava/lang/String;"
)
TARGET_METHOD = (
    ".method public static getValidConfigData"
    "(Landroid/content/Context;Ljava/lang/String;)Ljava/lang/String;"
)
REALTIME_GETTER_METHODS = (
    (
        Path("com/oplus/ocs/camera/producer/info/CameraConfigHelper.smali"),
        ".method public static getConfigValue(Ljava/lang/String;)Ljava/lang/String;",
    ),
    (
        Path("com/oplus/ocs/camera/consumer/apsAdapter/adapter/ApsUtils.smali"),
        ".method public static getVendorTagConfig(Ljava/lang/String;)Ljava/lang/String;",
    ),
)
REALTIME_METHOD = (
    "Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;"
    "->getRealtimeOverride(Ljava/lang/String;)Ljava/lang/String;"
)


class PatchError(RuntimeError):
    pass


def find_target(path: Path) -> Path:
    if path.is_file():
        if path.name != TARGET_RELATIVE.name:
            raise PatchError(f"expected {TARGET_RELATIVE.name}, got {path.name}")
        return path
    if not path.is_dir():
        raise PatchError(f"decoded smali root not found: {path}")
    matches = [item for item in path.rglob(TARGET_RELATIVE.name)
               if item.as_posix().endswith(TARGET_RELATIVE.as_posix())]
    if len(matches) != 1:
        raise PatchError(
            f"expected exactly one {TARGET_RELATIVE}, found {len(matches)}"
        )
    return matches[0]


def find_relative(decoded_root: Path, relative: Path) -> Path:
    matches = [item for item in decoded_root.rglob(relative.name)
               if item.as_posix().endswith(relative.as_posix())]
    if len(matches) != 1:
        raise PatchError(f"expected exactly one {relative}, found {len(matches)}")
    return matches[0]


def expand_registers(method: str) -> str:
    if "    .registers 16" in method or "    .locals 14" in method:
        return method
    if "    .registers 15" in method:
        return method.replace("    .registers 15", "    .registers 16", 1)
    if "    .locals 13" in method:
        return method.replace("    .locals 13", "    .locals 14", 1)
    directive = re.search(r"^\s*\.(?:registers|locals)\s+\d+", method, re.MULTILINE)
    found = directive.group(0).strip() if directive else "missing"
    raise PatchError(f"unexpected target register layout: {found}")


def remove_legacy_full_override(method: str) -> str:
    pattern = re.compile(
        r"\n\s*invoke-static \{p0\}, " + re.escape(OLD_GET_METHOD)
        + r"\n\s*move-result-object v0"
          r"\n\s*if-eqz v0, :\w+"
          r"\n\s*return-object v0"
          r"\n\s*:\w+\n"
    )
    return pattern.sub("\n", method, count=1)


def patch_method(method: str) -> tuple[str, bool]:
    if SAFE_HOOK in method:
        return method, False

    # Upgrade the broken V4 hook, which used v12 (the high half of a double).
    if MERGE_METHOD in method:
        method = expand_registers(method)
        method = method.replace("    move-object v12, p0", "    move-object v13, p0", 1)
        method = method.replace("invoke-static {v12, v4}",
                                "invoke-static {v13, v4}", 1)
        if SAFE_HOOK not in method or "    move-object v13, p0" not in method:
            raise PatchError("could not upgrade the V4 register hook")
        return method, True

    method = remove_legacy_full_override(method)
    method = expand_registers(method)
    directive = re.search(r"^\s*\.(?:registers|locals)\s+\d+\s*$",
                          method, re.MULTILINE)
    if directive is None:
        raise PatchError("target method has no register directive")
    insertion = directive.end()
    method = method[:insertion] + "\n\n    move-object v13, p0" + method[insertion:]

    return_at = method.rfind("    return-object v4")
    if return_at < 0:
        raise PatchError("target method final return-object v4 not found")
    replacement = f"""    # {PATCH_MARKER}: merge compact delta into decoded stock/RUS data.
    invoke-static {{v13, v4}}, {MERGE_METHOD}

    move-result-object v4

    return-object v4"""
    method = method[:return_at] + replacement + method[return_at + 20:]
    return method, True


def patch_realtime_getter(source: str, signature: str) -> tuple[str, bool]:
    start = source.find(signature)
    if start < 0:
        raise PatchError(f"realtime getter not found: {signature}")
    end = source.find(".end method", start)
    if end < 0:
        raise PatchError(f"realtime getter has no end: {signature}")
    end += len(".end method")
    method = source[start:end]
    if REALTIME_PATCH_MARKER in method:
        return source, False
    directive = re.search(r"^\s*\.(?:registers|locals)\s+(\d+)\s*$",
                          method, re.MULTILINE)
    if directive is None or int(directive.group(1)) < 1:
        raise PatchError(f"realtime getter has no free local v0: {signature}")
    insertion = directive.end()
    hook = f"""

    # {REALTIME_PATCH_MARKER}: resolve the camera-open add/edit snapshot.
    invoke-static {{p0}}, {REALTIME_METHOD}

    move-result-object v0

    if-eqz v0, :vs_camera_config_stock

    return-object v0

    :vs_camera_config_stock"""
    patched = method[:insertion] + hook + method[insertion:]
    return source[:start] + patched + source[end:], True


def patch_smali(decoded_root: Path, helper_template: Path) -> bool:
    decoded_root = decoded_root.resolve()
    target = find_target(decoded_root)
    source = target.read_text(encoding="utf-8")
    start = source.find(TARGET_METHOD)
    if start < 0:
        raise PatchError(f"target method not found: {TARGET_METHOD}")
    end = source.find(".end method", start)
    if end < 0:
        raise PatchError("target method has no end")
    end += len(".end method")
    patched_method, method_changed = patch_method(source[start:end])

    helper = target.with_name(HELPER_NAME)
    template = helper_template.read_text(encoding="utf-8")
    if HELPER_MARKER not in template:
        raise PatchError(f"invalid helper template: {helper_template}")
    helper_changed = not helper.is_file() or helper.read_text(
        encoding="utf-8"
    ) != template

    if method_changed:
        target.write_text(source[:start] + patched_method + source[end:], encoding="utf-8")

    realtime_changed = False
    for relative, signature in REALTIME_GETTER_METHODS:
        getter = find_relative(decoded_root, relative)
        getter_source = getter.read_text(encoding="utf-8")
        getter_patched, changed = patch_realtime_getter(getter_source, signature)
        if changed:
            getter.write_text(getter_patched, encoding="utf-8")
            realtime_changed = True
    if helper_changed:
        helper.write_text(template, encoding="utf-8")

    if method_changed or helper_changed or realtime_changed:
        print(f"Patched Camera SDK smali: {target}")
        return True
    print(f"Camera SDK smali hook already present: {target}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("decoded_root", type=Path,
                        help="apktool-decoded JAR root or UpdateHelper.smali")
    parser.add_argument("--helper", type=Path,
                        help="VSTeamConfigOverride smali template")
    args = parser.parse_args()
    helper = args.helper or Path(__file__).with_name("oplus_camera_delta_override.smali")
    try:
        patch_smali(args.decoded_root, helper.resolve())
    except (OSError, PatchError) as error:
        parser.exit(1, f"patch_oplus_camera_sdk_config: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
