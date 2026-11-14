import re
import sys
import os

def insert_lunar_code(smali_code: str) -> str:
    pattern = (
        r'(invoke-virtual\s+\{\s*(?P<reg_view>\w+),\s*(?P<reg_text>\w+)\s*\},\s*'
        r'Landroid/widget/TextView;->setText\(Ljava/lang/CharSequence;\)V)'
    )


    def replacer(match):
        reg_text = match.group('reg_text')
        inject = (
            f"    invoke-static {{}}, Lcom/android/vsteam/Toolbox;->VSLunarString()Ljava/lang/String;\n"
            f"    move-result-object v0\n"
            f"    invoke-virtual {{{reg_text}, v0}}, Ljava/lang/String;->concat(Ljava/lang/String;)Ljava/lang/String;\n"
            f"    move-result-object {reg_text}\n"
            f"{match.group(0)}"
        )
        return inject


    modified = re.sub(pattern, replacer, smali_code, flags=re.MULTILINE)
    return modified

def main():
    if len(sys.argv) != 2:
        print("Usage: python inject_lunar.py <path_to_smali_file>")
        sys.exit(1)

    smali_path = sys.argv[1]

    if not os.path.isfile(smali_path):
        print(f"❌ File not found: {smali_path}")
        sys.exit(1)

    with open(smali_path, 'r', encoding='utf-8') as f:
        content = f.read()

    modified = insert_lunar_code(content)

    with open(smali_path, 'w', encoding='utf-8') as f:
        f.write(modified)


if __name__ == "__main__":
    main()
