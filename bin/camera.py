#!/usr/bin/env python3
"""Patch camera smali: hook watermark brand/name methods and decrypt map strings."""

from __future__ import annotations

import argparse
import ast
import os
import re
from pathlib import Path


INVOKE_RE = re.compile(
    r"invoke-static\s+\{\s*([vp]\d+)\s*,\s*([vp]\d+)\s*\},\s*"
    r"((?:L[^;]+;)->[^\s(]+\(ILjava/lang/String;\)Ljava/lang/String;)"
)
CLASS_RE = re.compile(r"\.class\b.*\s+(L[^;]+;)\s*$")
METHOD_RE = re.compile(r"\.method\b.*\s+([^\s(]+)\(ILjava/lang/String;\)Ljava/lang/String;\s*$")
DEVICE_MAP_METHOD_RE = re.compile(
    r"\s*\.method\b.*\s[^\s(]+\(\)Landroid/util/SparseArray;\s*$"
)
STRING_METHOD_HEADER_RE = re.compile(r"\s*\.method\b.*\s[^\s(]+\(\)Ljava/lang/String;\s*$")
CONST_RE = re.compile(r"\s*const(?:/\S+)?\s+([vp]\d+),\s+(-?(?:0x[0-9a-fA-F]+|\d+))\s*$")
CONST_STRING_RE = re.compile(r"\s*(const-string(?:/jumbo)?)\s+([vp]\d+),\s+(\".*\")\s*$")
MOVE_RESULT_OBJECT_RE = re.compile(r"\s*move-result-object\s+([vp]\d+)\s*$")
AGET_OBJECT_RE = re.compile(r"\s*aget-object\s+([vp]\d+),\s+([vp]\d+),\s+([vp]\d+)\s*$")
SAME_CLASS_STRING_ARRAY_RE = re.compile(
    r"invoke-virtual\s+\{p0\},\s+L[^;]+;->[^\s(]+\(\)\[Ljava/lang/String;"
)
CONST_ZERO_RE = re.compile(r"\s*const(?:/\S+)?\s+([vp]\d+),\s+(?:0x0|0)\s*$")
CONST_ONE_RE = re.compile(r"\s*const(?:/\S+)?\s+([vp]\d+),\s+(?:0x1|1)\s*$")
FILLED_ARRAY_RANGE_RE = re.compile(
    r"\s*filled-new-array/range\s+\{\s*([vp])(\d+)\s+\.\.\s+([vp])(\d+)\s*\},"
)
REGISTER_RE = re.compile(r"\b[vp]\d+\b")
WRITE_FIRST_OPS = (
    "const",
    "move",
    "move-result",
    "move-exception",
    "new-instance",
    "check-cast",
    "instance-of",
    "sget",
    "iget",
    "aget",
    "array-length",
)
TOOLBOX_BRAND_METHOD = "Lcom/android/vsteam/Toolbox;->wtmBrand()Ljava/lang/String;"
TOOLBOX_NAME_METHOD = "Lcom/android/vsteam/Toolbox;->wtmName()Ljava/lang/String;"


ConstValue = tuple[str, int | str, int, str]


def is_xor_string_decryptor(method_lines: list[str]) -> bool:
    text = "\n".join(method_lines)
    return (
        "Ljava/lang/String;->toCharArray()[C" in text
        and "xor-int" in text
        and "int-to-char" in text
        and "Ljava/lang/String;->valueOf([C)Ljava/lang/String;" in text
        and "return-object" in text
    )


def discover_decrypt_methods(paths: list[Path]) -> set[str]:
    methods: set[str] = set()
    for path in paths:
        class_name: str | None = None
        current_method: tuple[str, list[str]] | None = None

        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue

        for line in lines:
            if class_name is None and (match := CLASS_RE.match(line)):
                class_name = match.group(1)

            if current_method is None:
                match = METHOD_RE.match(line.strip())
                if class_name and match:
                    method_name = match.group(1)
                    current_method = (
                        f"{class_name}->{method_name}(ILjava/lang/String;)Ljava/lang/String;",
                        [line],
                    )
                continue

            method_ref, method_lines = current_method
            method_lines.append(line)
            if line.strip() == ".end method":
                if is_xor_string_decryptor(method_lines):
                    methods.add(method_ref)
                current_method = None

    return methods


def parse_int(value: str) -> int:
    return int(value, 0)


def parse_smali_string(value: str) -> str:
    return ast.literal_eval(value)


def quote_smali_string(value: str) -> str:
    out = ['"']
    for ch in value:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif 0x20 <= code <= 0x7E:
            out.append(ch)
        else:
            out.append(f"\\u{code:04x}")
    out.append('"')
    return "".join(out)


def decode_hf_e0(key: int, text: str) -> str:
    key &= 0xFFFF
    return "".join(chr(ord(ch) ^ key) for ch in text)


def has_device_map_method(lines: list[str]) -> bool:
    current_method: list[str] | None = None

    for line in lines:
        stripped = line.strip()
        if current_method is None:
            if DEVICE_MAP_METHOD_RE.match(line):
                current_method = [line]
            continue

        current_method.append(line)
        if stripped != ".end method":
            continue

        text = "\n".join(current_method)
        if "filled-new-array" in text and "[Ljava/lang/String;" in text:
            return True
        current_method = None

    return False


def next_code_line(lines: list[str], start: int) -> int | None:
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if stripped and not stripped.startswith("#"):
            return index
    return None


def can_collapse(lines: list[str], start: int, end: int, needed: set[int]) -> bool:
    if not needed.issubset(set(range(start, end + 1))):
        return False

    for index in range(start, end + 1):
        if index in needed:
            continue
        stripped = lines[index].strip()
        if stripped and not stripped.startswith("#"):
            return False
    return True


def overwrites_first_operand(line: str, reg: str) -> bool:
    return written_first_operand(line) == reg


def written_first_operand(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or stripped.startswith("."):
        return None

    parts = stripped.split(None, 1)
    if len(parts) != 2 or not parts[0].startswith(WRITE_FIRST_OPS):
        return None

    first_operand = parts[1].split(",", 1)[0].strip()
    if REGISTER_RE.fullmatch(first_operand):
        return first_operand
    return None


def is_label(line: str) -> bool:
    return line.strip().startswith(":")


def register_used_by_range(line: str, reg: str) -> bool:
    match = FILLED_ARRAY_RANGE_RE.match(line)
    reg_match = re.fullmatch(r"([vp])(\d+)", reg)
    if not match or not reg_match:
        return False

    start_prefix, start_num, end_prefix, end_num = match.groups()
    reg_prefix, reg_num = reg_match.groups()
    if start_prefix != end_prefix or reg_prefix != start_prefix:
        return False
    return int(start_num) <= int(reg_num) <= int(end_num)


def reg_used_after(lines: list[str], reg: str, start: int) -> bool:
    for line in lines[start:]:
        stripped = line.strip()
        if stripped == ".end method":
            return False
        if stripped.startswith(":"):
            return True
        if register_used_by_range(line, reg):
            return True
        if not REGISTER_RE.search(line):
            continue
        if reg not in REGISTER_RE.findall(line):
            continue
        if overwrites_first_operand(line, reg):
            return False
        return True
    return False


def replacement_indent(*lines: str) -> str:
    for line in lines:
        match = re.match(r"(\s*)\S", line)
        if match:
            return match.group(1)
    return "    "


def compact_blank_lines(lines: list[str]) -> tuple[list[str], int]:
    compacted: list[str] = []
    blank_count = 0
    removed = 0
    for line in lines:
        if line.strip():
            blank_count = 0
            compacted.append(line)
            continue
        blank_count += 1
        if blank_count <= 1:
            compacted.append(line)
        else:
            removed += 1
    return compacted, removed


def string_method_array_index(method_text: str, class_name: str) -> int | None:
    if (
        f"invoke-virtual {{p0}}, {class_name}->" not in method_text
        or not SAME_CLASS_STRING_ARRAY_RE.search(method_text)
        or "aget-object" not in method_text
        or "return-object" not in method_text
    ):
        return None

    const_values: dict[str, int] = {}
    for line in method_text.splitlines():
        if match := CONST_ZERO_RE.match(line):
            const_values[match.group(1)] = 0
            continue
        if match := CONST_ONE_RE.match(line):
            const_values[match.group(1)] = 1
            continue
        if match := AGET_OBJECT_RE.match(line):
            index = const_values.get(match.group(3))
            if index in (0, 1):
                return index
    return None


def build_toolbox_string_method(method_header: str, toolbox_method: str) -> list[str]:
    return [
        method_header,
        "    .locals 1\n",
        "\n",
        f"    invoke-static {{}}, {toolbox_method}\n",
        "\n",
        "    move-result-object p0\n",
        "\n",
        "    return-object p0\n",
        ".end method\n",
    ]


def patch_watermark_brand_name_methods(path: Path) -> tuple[int, int]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    replacements: list[tuple[int, int, list[str]]] = []
    class_name: str | None = None
    method_start: int | None = None
    patched_brand = 0
    patched_name = 0

    for index, line in enumerate(lines):
        if class_name is None and (match := CLASS_RE.match(line)):
            class_name = match.group(1)

        stripped = line.strip()
        if method_start is None:
            if STRING_METHOD_HEADER_RE.match(line):
                method_start = index
            continue

        if stripped != ".end method":
            continue

        method_end = index
        method_lines = lines[method_start : method_end + 1]
        method_text = "".join(method_lines)
        if class_name:
            array_index = string_method_array_index(method_text, class_name)
        else:
            array_index = None

        if array_index == 0:
            replacement = build_toolbox_string_method(method_lines[0], TOOLBOX_BRAND_METHOD)
            if method_lines != replacement:
                replacements.append((method_start, method_end + 1, replacement))
                patched_brand += 1
        elif array_index == 1:
            replacement = build_toolbox_string_method(method_lines[0], TOOLBOX_NAME_METHOD)
            if method_lines != replacement:
                replacements.append((method_start, method_end + 1, replacement))
                patched_name += 1
        method_start = None

    if not replacements:
        return 0, 0

    new_lines = lines[:]
    for start, end, replacement in reversed(replacements):
        new_lines[start:end] = replacement
    path.write_text("".join(new_lines), encoding="utf-8")
    return patched_brand, patched_name


def decrypt_device_map_strings(path: Path, decrypt_methods: set[str]) -> tuple[int, int]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    if not has_device_map_method(lines):
        return 0, 0

    regs: dict[str, ConstValue] = {}
    replacements: list[tuple[int, int, str]] = []
    replaced_calls = 0
    skipped = 0

    for index, line in enumerate(lines):
        if is_label(line):
            regs.clear()
            continue

        if match := CONST_RE.match(line):
            regs[match.group(1)] = ("int", parse_int(match.group(2)), index, "const")
            continue

        if match := CONST_STRING_RE.match(line):
            regs[match.group(2)] = (
                "str",
                parse_smali_string(match.group(3)),
                index,
                match.group(1),
            )
            continue

        match = INVOKE_RE.search(line)
        if not match:
            if written_reg := written_first_operand(line):
                regs.pop(written_reg, None)
            continue

        key_reg, str_reg, method_ref = match.groups()
        if method_ref not in decrypt_methods:
            continue
        key_value = regs.get(key_reg)
        str_value = regs.get(str_reg)
        move_index = next_code_line(lines, index + 1)
        move_match = MOVE_RESULT_OBJECT_RE.match(lines[move_index]) if move_index is not None else None
        if (
            key_value
            and str_value
            and key_value[0] == "int"
            and str_value[0] == "str"
            and not move_match
        ):
            str_index = int(str_value[2])
            if not reg_used_after(lines, str_reg, index + 1):
                replacements.append((index, index + 1, ""))
                replacements.append((str_index, str_index + 1, ""))
                replaced_calls += 1
            else:
                skipped += 1
            continue

        if not (
            key_value
            and str_value
            and key_value[0] == "int"
            and str_value[0] == "str"
            and move_index is not None
            and move_match
        ):
            skipped += 1
            continue

        key = int(key_value[1])
        encoded = str(str_value[1])
        decoded = decode_hf_e0(key, encoded)
        dest_reg = move_match.group(1)
        opcode = str(str_value[3])
        indent = replacement_indent(lines[index], lines[move_index])
        replacement = f"{indent}{opcode} {dest_reg}, {quote_smali_string(decoded)}\n"

        key_index = int(key_value[2])
        str_index = int(str_value[2])
        collapse_start = min(key_index, str_index)
        needed = {key_index, str_index, index, move_index}
        key_safe = dest_reg == key_reg or not reg_used_after(lines, key_reg, move_index + 1)
        str_safe = dest_reg == str_reg or not reg_used_after(lines, str_reg, move_index + 1)
        if (
            key_index < index
            and str_index < index
            and key_safe
            and str_safe
            and can_collapse(lines, collapse_start, move_index, needed)
        ):
            replacements.append((collapse_start, move_index + 1, replacement))
        else:
            replacements.append((index, move_index + 1, replacement))
        replaced_calls += 1

    new_lines = lines[:]
    for start, end, replacement in reversed(replacements):
        new_lines[start:end] = [replacement]
    removed_blanks = 0
    if replacements:
        new_lines, removed_blanks = compact_blank_lines(new_lines)

    if not replacements and not removed_blanks:
        return 0, skipped

    path.write_text("".join(new_lines), encoding="utf-8")
    return replaced_calls + removed_blanks, skipped


def iter_smali_paths(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(path.rglob("*.smali"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Patch watermark brand/name methods to call Toolbox, then decrypt "
            "selected smali strings."
        )
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Folder containing smali files, for example MiuiCamera_src",
    )
    args = parser.parse_args()

    path = args.path
    if not path.exists():
        parser.error(f"path not found: {path}")
    if not path.is_dir():
        parser.error(f"path must be a folder: {path}")

    paths = iter_smali_paths(path)
    decrypt_methods = discover_decrypt_methods(paths)
    verbose = os.environ.get("VS_VERBOSE_LOG") == "1"

    if decrypt_methods:
        if verbose:
            print("[INFO]  CameraPatch - Decrypt: method(s)")
            for method in sorted(decrypt_methods):
                print(f"               Method: {method}")
        else:
            print(f"[INFO]  CameraPatch - Decrypt: detect {len(decrypt_methods)} method(s)")
    else:
        print("[SKIP]  CameraPatch - Decrypt: no XOR string decrypt method")

    changed_files = 0
    patched_brand_methods = 0
    patched_name_methods = 0
    decrypted_items = 0
    skipped_items = 0
    for smali_path in paths:
        patched_brand, patched_name = patch_watermark_brand_name_methods(smali_path)
        decrypted, skipped = decrypt_device_map_strings(smali_path, decrypt_methods)
        if patched_brand or patched_name or decrypted:
            changed_files += 1
            parts = []
            if patched_brand:
                parts.append(f"patched {patched_brand} watermark brand method(s)")
            if patched_name:
                parts.append(f"patched {patched_name} watermark name method(s)")
            if decrypted:
                parts.append(f"decrypted {decrypted} item(s)")
            if verbose:
                print(f"[UPDATE] CameraPatch - {smali_path.name}: {', '.join(parts)}")
        patched_brand_methods += patched_brand
        patched_name_methods += patched_name
        decrypted_items += decrypted
        skipped_items += skipped

    print(
        f"[DONE]  CameraPatch - Summary: updated {changed_files} file(s), "
        f"brand {patched_brand_methods}, name {patched_name_methods}, "
        f"decrypted {decrypted_items}, skipped {skipped_items}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
