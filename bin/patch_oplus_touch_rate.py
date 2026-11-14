#!/usr/bin/env python3
"""Promote OPlus touch default from the 125 Hz command to the 180 Hz command."""

from __future__ import annotations

import argparse
import os
import re
import stat
import tempfile
from pathlib import Path


MODULE_RE = re.compile(
    r"(?P<open><modules\b(?=[^>]*\bid\s*=\s*['\"]report_rate['\"])[^>]*>)"
    r"(?P<body>.*?)</modules\s*>",
    re.IGNORECASE | re.DOTALL,
)
PARAM_RE_TEMPLATE = (
    r"<param\b(?=[^>]*\bname\s*=\s*['\"]{name}['\"])[^>]*>"
    r"(?P<body>.*?)</param\s*>"
)
VALUE_RE = re.compile(
    r"(?P<open><value\b[^>]*>)(?P<content>.*?)(?P<close></value\s*>)",
    re.IGNORECASE | re.DOTALL,
)
NUMBER_RE = re.compile(r"(?<![\w.])(?:0[xX][0-9a-fA-F]+|\d+)(?![\w.])")

DEFAULT_INDEX = 0
RATE_125_INDEX = 2
RATE_180_INDEX = 3


class PatchError(RuntimeError):
    pass


def find_param_value(module_body: str, name: str) -> tuple[str, int, int] | None:
    param_re = re.compile(
        PARAM_RE_TEMPLATE.format(name=re.escape(name)),
        re.IGNORECASE | re.DOTALL,
    )
    param = param_re.search(module_body)
    if param is None:
        return None
    value = VALUE_RE.search(param.group("body"))
    if value is None:
        return None
    body_offset = param.start("body")
    return (
        value.group("content"),
        body_offset + value.start("content"),
        body_offset + value.end("content"),
    )


def parse_numbers(content: str) -> list[re.Match[str]]:
    return list(NUMBER_RE.finditer(content))


def parse_int(token: str) -> int:
    return int(token, 0)


def patch_report_rate_module(module: str) -> tuple[str, str]:
    open_end = module.find(">") + 1
    close_start = module.lower().rfind("</modules")
    if open_end <= 0 or close_start < open_end:
        raise PatchError("malformed report_rate module")

    body = module[open_end:close_start]
    default_value = find_param_value(body, "default")
    supports_value = find_param_value(body, "supports")
    if default_value is None or supports_value is None:
        raise PatchError("report_rate module is missing default/supports values")

    default_content, _default_start, _default_end = default_value
    default_tokens = parse_numbers(default_content)
    if len(default_tokens) != 1:
        raise PatchError("report_rate default must contain exactly one integer")
    if parse_int(default_tokens[0].group()) != DEFAULT_INDEX:
        return module, f"skip: default index is {default_tokens[0].group()}, not 0"

    supports_content, supports_start, supports_end = supports_value
    commands = parse_numbers(supports_content)
    if len(commands) <= RATE_180_INDEX:
        raise PatchError("report_rate supports must contain at least four commands")

    default_command = parse_int(commands[DEFAULT_INDEX].group())
    rate_125_command = parse_int(commands[RATE_125_INDEX].group())
    rate_180_command = parse_int(commands[RATE_180_INDEX].group())

    if default_command == rate_180_command:
        return module, f"skip: default already uses 180 Hz command {rate_180_command}"
    if default_command != rate_125_command:
        return module, (
            "skip: default command "
            f"{default_command} does not match 125 Hz command {rate_125_command}"
        )

    first = commands[DEFAULT_INDEX]
    patched_content = (
        supports_content[:first.start()]
        + commands[RATE_180_INDEX].group()
        + supports_content[first.end():]
    )
    value_start = open_end + supports_start
    value_end = open_end + supports_end
    patched = module[:value_start] + patched_content + module[value_end:]
    return patched, (
        f"patched: default command {default_command} (125 Hz) -> "
        f"{rate_180_command} (180 Hz)"
    )


def patch_text(source: str) -> tuple[str, list[str], int]:
    matches = list(MODULE_RE.finditer(source))
    if not matches:
        return source, ["skip: no report_rate module"], 0

    output = source
    messages: list[str] = []
    changed = 0
    for match in reversed(matches):
        original = match.group(0)
        patched, message = patch_report_rate_module(original)
        messages.append(message)
        if patched != original:
            output = output[:match.start()] + patched + output[match.end():]
            changed += 1
    messages.reverse()
    return output, messages, changed


def atomic_write(path: Path, content: str, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_name, stat.S_IMODE(mode))
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def patch_file(path: Path) -> tuple[list[str], int]:
    file_stat = path.stat()
    source = path.read_text(encoding="utf-8")
    patched, messages, changed = patch_text(source)
    if changed:
        atomic_write(path, patched, file_stat.st_mode)
    return messages, changed


def find_configs(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(path.glob("firmware/tp/**/vnd_custom_config_main.xml"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "path",
        type=Path,
        help="extracted ODM root or a vnd_custom_config_main.xml file",
    )
    args = parser.parse_args()

    path: Path = args.path
    configs = find_configs(path)
    if not configs:
        print(f"touch-rate patch: no touch configs found under {path}; skip")
        return 0

    total_changed = 0
    try:
        for config in configs:
            messages, changed = patch_file(config)
            total_changed += changed
            for message in messages:
                print(f"{config}: {message}")
    except (OSError, UnicodeError, PatchError, ValueError) as error:
        parser.exit(1, f"patch_oplus_touch_rate: {error}\n")

    print(
        f"touch-rate patch complete: files={len(configs)}, "
        f"modules_updated={total_changed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
