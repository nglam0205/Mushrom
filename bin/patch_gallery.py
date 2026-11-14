import sys
import os
import re

def insert_instruction(smali_file):
    try:
        # Kiểm tra file tồn tại
        if not os.path.isfile(smali_file):
            print(f"File not found: {smali_file}")
            return

        with open(smali_file, 'r', encoding='utf-8') as file:
            lines = file.readlines()

        updated_lines = []
        instruction_found = False

        for line in lines:
            # Tìm dòng chứa instruction với (v0, p0, a0, ...) là regex
            match = re.search(r'sput-boolean ((?:v|p|a)\d+), Lmiuix/os/Build;->IS_INTERNATIONAL_BUILD:Z', line)
            if match:
                v0 = match.group(1)
                # Thêm đoạn mã trước dòng đã tìm thấy
                updated_lines.append(f'    const-string {v0}, "vs_google_sync"\n')
                updated_lines.append(f'    invoke-static {{v0}}, Lcom/android/vsteam/Toolbox;->getBooleanGlobal(Ljava/lang/String;)Z\n')
                updated_lines.append(f'    move-result {v0}\n')
                instruction_found = True

            updated_lines.append(line)

        if not instruction_found:
            print("Instruction not found in the file.")
            return

        # Ghi lại file
        with open(smali_file, 'w', encoding='utf-8') as file:
            file.writelines(updated_lines)


    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python insert_smali_code.py <smali_file>")
    else:
        smali_file = sys.argv[1]
        insert_instruction(smali_file)
