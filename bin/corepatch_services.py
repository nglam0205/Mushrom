import os
import re
import argparse

def modify_invoke_interface(file_path):
    with open(file_path, 'r') as file:
        lines = file.readlines()

    modified_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        modified_lines.append(line)
        if 'Lcom/android/server/pm/pkg/AndroidPackage;->isPersistent()Z' in line:
            for j in range(i + 1, min(i + 4, len(lines))):
                if re.match(r'\s*move-result\s+(v\d+)', lines[j]):
                    variable = re.search(r'\s*move-result\s+(v\d+)', lines[j]).group(1)
                    modified_lines.append(f'    move-result {variable}\n')
                    modified_lines.append(f'    invoke-static {{{variable}}}, Lcom/android/vsteam/Toolbox;->CorePatchFalseInt(I)I\n')
                    modified_lines.append(f'    move-result {variable}\n')
                    i = j  # Skip to the line after the move-result
                    break
        i += 1

    with open(file_path, 'w') as file:
        file.writelines(modified_lines)

def modify_parsing_package_utils(file_path):
    with open(file_path, 'r') as file:
        lines = file.readlines()

    modified_lines = []
    for line in lines:
        if "invoke-static {p0, p1, v0}, Landroid/util/apk/ApkSignatureVerifier;->unsafeGetCertsWithoutVerification(Landroid/content/pm/parsing/result/ParseInput;Ljava/lang/String;I)Landroid/content/pm/parsing/result/ParseResult;" in line:
            # p1 is the apk path argument; stamp it so the protected package
            # keeps real signature verification even with CorePatch enabled.
            modified_lines.append('    invoke-static {p1}, Lcom/android/vsteam/Toolbox;->markApkForVerify(Ljava/lang/String;)V\n')
            modified_lines.append('    invoke-static {v0}, Lcom/android/vsteam/Toolbox;->CorePatchTrueInt(I)I\n')
            modified_lines.append('    move-result v0\n')
        modified_lines.append(line)

    with open(file_path, 'w') as file:
        file.writelines(modified_lines)

def modify_smali_files(directories):
    for directory in directories:
        parsing_package_utils = os.path.join(directory, 'com/android/server/pm/pkg/parsing/ParsingPackageUtils.smali')
        package_info_utils = os.path.join(directory, 'com/android/server/pm/parsing/PackageInfoUtils.smali')

        if os.path.exists(parsing_package_utils):
            modify_parsing_package_utils(parsing_package_utils)
        if os.path.exists(package_info_utils):
            modify_invoke_interface(package_info_utils)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process some directories.")
    parser.add_argument('current_directory', type=str, help='The current directory to be appended to the base directories')
    
    args = parser.parse_args()
    current_directory = args.current_directory

    if not os.path.isdir(current_directory):
        sys.exit(1)

    base_directories = [name for name in os.listdir(current_directory)
                        if name == "smali" or re.fullmatch(r"smali_classes\d+", name)]
    directories = [os.path.join(current_directory, name)
                   for name in sorted(base_directories)]

    modify_smali_files(directories)
