#!/usr/bin/env python3
import re
import sys
from pathlib import Path

def usage():
    print("Usage: {} path/to/target.smali path/to/R$string.smali [path/to/R$color.smali]".format(sys.argv[0]))
    sys.exit(1)

if len(sys.argv) not in (3, 4):
    usage()

target_path = Path(sys.argv[1])
id_path = Path(sys.argv[2])
color_path = Path(sys.argv[3]) if len(sys.argv) == 4 else id_path.with_name("R$color.smali")

if not target_path.is_file():
    print("Target file not found:", target_path)
    sys.exit(2)
if not id_path.is_file():
    print("ID file not found:", id_path)
    sys.exit(3)
if len(sys.argv) == 4 and not color_path.is_file():
    print("Color ID file not found:", color_path)
    sys.exit(4)

target_text = target_path.read_text(encoding="utf-8")
id_text = id_path.read_text(encoding="utf-8")
color_text = color_path.read_text(encoding="utf-8") if color_path.is_file() else ""


def replace_update_string(text):
    # Pattern A: original style using sget to R$string;->about_phone_already_last_version:I
    pattern_name = re.compile(
        r"""(
            sget\s+([vp]\d+)\s*,\s*Lcom/android/settings/R\$string;->about_phone_already_last_version:I\s*
            \n\s*
            invoke-virtual\s*\{\s*([vp]\d+)\s*,\s*\2\s*\}\s*,\s*Landroid/content/Context;->getString\(I\)Ljava/lang/String;\s*
            (?:\n\s*move-result-object\s*([vp]\d+)\s*)?
        )""",
        re.VERBOSE | re.MULTILINE,
    )

    def repl_name(m):
        # Remove whole matched block, replace with static call.
        return "invoke-static {}, Lcom/android/vsteam/Toolbox;->getUpdateString()Ljava/lang/String;\n"

    new_text, n_name = pattern_name.subn(repl_name, text, count=0)

    if n_name > 0:
        return new_text, n_name

    # Pattern A not found -> try to extract id from the provided id file
    id_decl = re.search(
        r"about_phone_already_last_version:I\s*=\s*(0x[0-9a-fA-F]+)",
        id_text
    )

    if not id_decl:
        return text, 0

    id_hex = id_decl.group(1).lower()  # e.g. 0x7f14003b

    # Pattern B: find const <constReg>, <id_hex> followed by invoke-virtual {regA, regB}, Context->getString(I)...
    # Capture groups:
    #  1: whole block
    #  2: constReg
    #  3: invoke reg A
    #  4: invoke reg B
    #  5: optional move-result-object dest
    pattern_const = re.compile(
        rf"""(
            const\s+([vp]\d+)\s*,\s*{re.escape(id_hex)}\s*
            \n\s*
            invoke-virtual\s*\{{\s*([vp]\d+)\s*,\s*([vp]\d+)\s*\}}\s*,\s*Landroid/content/Context;->getString\(I\)Ljava/lang/String;\s*
            (?:\n\s*move-result-object\s*([vp]\d+)\s*)?
        )""",
        re.VERBOSE | re.MULTILINE,
    )

    def repl_const(m):
        whole = m.group(1)
        const_reg = m.group(2)
        reg_a = m.group(3)
        reg_b = m.group(4)
        # Optional move-result dest = m.group(5) (may be None)
        # Ensure const_reg actually appears in the invoke-virtual regs (either reg_a or reg_b)
        if const_reg != reg_a and const_reg != reg_b:
            # Not the pattern we want (const used elsewhere) -> leave unchanged
            return whole
        # Otherwise replace whole block with static call.
        return "invoke-static {}, Lcom/android/vsteam/Toolbox;->getUpdateString()Ljava/lang/String;\n"

    new_text, n_const = pattern_const.subn(repl_const, text, count=0)
    return new_text, n_const


def replace_name_version_color(text):
    if not color_text:
        return text, 0

    old_decl = re.search(
        r"about_phone_name_version_color:I\s*=\s*(0x[0-9a-fA-F]+)",
        color_text
    )
    new_decl = re.search(
        r"about_phone_brand_color:I\s*=\s*(0x[0-9a-fA-F]+)",
        color_text
    )

    if not old_decl or not new_decl:
        return text, 0

    old_id = old_decl.group(1)
    new_id = new_decl.group(1).lower()
    pattern_id = re.compile(rf"(?<![0-9a-fA-F]){re.escape(old_id)}(?![0-9a-fA-F])", re.IGNORECASE)
    text, n_id = pattern_id.subn(new_id, text)
    return text, n_id


target_text, n_update = replace_update_string(target_text)
target_text, n_color = replace_name_version_color(target_text)

if n_update > 0 or n_color > 0:
    target_path.write_text(target_text, encoding="utf-8")

if n_update > 0:
    print(f"Replaced {n_update} update string occurrence(s).")
if n_color > 0:
    print(f"Replaced {n_color} color occurrence(s).")

sys.exit(0)
