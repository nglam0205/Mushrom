import os
import re
import argparse

def modify_package_parser(file_path):
    with open(file_path, 'r') as file:
        lines = file.readlines()

    modified_lines = []
    pattern = re.compile(
        r'invoke-static \{v2, v0, v1\}, Landroid/util/apk/ApkSignatureVerifier;->unsafeGetCertsWithoutVerification\(Landroid/content/pm/parsing/result/ParseInput;Ljava/lang/String;I\)Landroid/content/pm/parsing/result/ParseResult;')

    for line in lines:
        if pattern.search(line):
            # v0 is the apk path argument of unsafeGetCertsWithoutVerification;
            # stamp it so the protected package keeps real verification.
            modified_lines.append('    invoke-static {v0}, Lcom/android/vsteam/Toolbox;->markApkForVerify(Ljava/lang/String;)V\n')
            modified_lines.append('    invoke-static {v1}, Lcom/android/vsteam/Toolbox;->CorePatchTrueInt(I)I\n')
            modified_lines.append('    move-result v1\n')
        modified_lines.append(line)

    with open(file_path, 'w') as file:
        file.writelines(modified_lines)


def modify_apk_signature_verifier(file_path):
    with open(file_path, 'r') as file:
        lines = file.readlines()

    modified_lines = []
    pattern = re.compile(
        r'invoke-static \{p0, p1, p3\}, Landroid/util/apk/ApkSignatureVerifier;->verifyV1Signature\(Landroid/content/pm/parsing/result/ParseInput;Ljava/lang/String;Z\)Landroid/content/pm/parsing/result/ParseResult;')

    for line in lines:
        if pattern.search(line):
            # p1 is the apk path argument of verifyV1Signature.
            modified_lines.append('    invoke-static {p1}, Lcom/android/vsteam/Toolbox;->markApkForVerify(Ljava/lang/String;)V\n')
            modified_lines.append('    invoke-static {p3}, Lcom/android/vsteam/Toolbox;->CorePatchFalseInt(I)I\n')
            modified_lines.append('    move-result p3\n')
        modified_lines.append(line)

    with open(file_path, 'w') as file:
        file.writelines(modified_lines)


def modify_exception_file(file_path):
    with open(file_path, 'r') as file:
        lines = file.readlines()

    modified_lines = []
    for line in lines:
        if re.search(r'iput p1, p0, Landroid/content/pm/PackageParser\$PackageParserException;->error:I', line):
            modified_lines.append('    invoke-static {p1}, Lcom/android/vsteam/Toolbox;->CorePatchTrueInt(I)I\n')
            modified_lines.append('    move-result p1\n')
        modified_lines.append(line)

    with open(file_path, 'w') as file:
        file.writelines(modified_lines)


def modify_apk_signature_scheme_v2_verifier(file_path):
    modify_invoke_static(file_path)


def modify_apk_signature_scheme_v3_verifier(file_path):
    modify_invoke_static(file_path)


def modify_apk_signing_block_utils(file_path):
    modify_invoke_static(file_path)


def modify_invoke_static(file_path):
    with open(file_path, 'r') as file:
        lines = file.readlines()

    modified_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        modified_lines.append(line)
        if 'Ljava/security/MessageDigest;->isEqual([B[B)Z' in line:
            for j in range(i + 1, min(i + 4, len(lines))):
                if re.match(r'\s*move-result\s+(v\d+)', lines[j]):
                    variable = re.search(r'\s*move-result\s+(v\d+)', lines[j]).group(1)
                    
                    # Insert new instructions
                    modified_lines.append(f'    move-result {variable}\n')  
                    modified_lines.append(f'    invoke-static {{{variable}}}, Lcom/android/vsteam/Toolbox;->CorePatchTrue(Z)Z\n')
                    modified_lines.append(f'    move-result {variable}\n')
                    
                    i = j  # Skip the original move-result line
                    break
        i += 1

    with open(file_path, 'w') as file:
        file.writelines(modified_lines)


def _string_param_reg(descriptor, is_static):
    """Return the p-register index of the first String parameter, or None.

    descriptor is the text between '(' and ')' of a smali method. For a static
    method params start at p0; for an instance method p0 is 'this' so they
    start at p1. Wide types (J/D) take two registers; arrays take one.
    """
    idx = 0 if is_static else 1
    i = 0
    n = len(descriptor)
    while i < n:
        c = descriptor[i]
        if c == '[':
            while i < n and descriptor[i] == '[':
                i += 1
            if i < n and descriptor[i] == 'L':
                i = descriptor.index(';', i) + 1
            else:
                i += 1
            idx += 1
            continue
        if c == 'L':
            j = descriptor.index(';', i)
            if descriptor[i:j + 1] == 'Ljava/lang/String;':
                return idx
            idx += 1
            i = j + 1
            continue
        if c in 'JD':
            idx += 2
            i += 1
            continue
        idx += 1
        i += 1
    return None


def modify_apk_signature_verifier(file_path):
    """Stamp markApkForVerify() at the entry of every verify() overload that
    takes an apk-path String. verify() is the single funnel for the V2/V3/V1
    schemes, so stamping here sets the per-thread protected flag before the
    low-level MessageDigest.isEqual sites run -- those sites have no package
    context of their own."""
    with open(file_path, 'r') as file:
        lines = file.readlines()

    method_re = re.compile(r'^\s*\.method\b(.*?)\sverify\(([^)]*)\)')
    mark = '    invoke-static {{p{0}}}, Lcom/android/vsteam/Toolbox;->markApkForVerify(Ljava/lang/String;)V\n'
    out = []
    i = 0
    n = len(lines)
    while i < n:
        m = method_re.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue

        is_static = 'static' in m.group(1)
        reg = _string_param_reg(m.group(2), is_static)
        out.append(lines[i])
        i += 1
        if reg is None:
            continue

        ann_depth = 0
        inserted = False
        while i < n:
            stripped = lines[i].strip()
            if stripped == '.end method':
                if not inserted:
                    out.append(mark.format(reg))
                out.append(lines[i])
                i += 1
                break
            if stripped.startswith('.annotation'):
                ann_depth += 1
            elif stripped.startswith('.end annotation'):
                ann_depth = max(0, ann_depth - 1)
            else:
                is_instr = (ann_depth == 0 and stripped
                            and not stripped.startswith('.')
                            and not stripped.startswith('#')
                            and not stripped.startswith(':'))
                if is_instr and not inserted:
                    out.append(mark.format(reg))
                    inserted = True
            out.append(lines[i])
            i += 1

    with open(file_path, 'w') as file:
        file.writelines(out)


def modify_smali_files(directories):
    for directory in directories:
        apk_signature_verifier = os.path.join(directory, 'android/util/apk/ApkSignatureVerifier.smali')
        apk_signature_scheme_v2_verifier = os.path.join(directory, 'android/util/apk/ApkSignatureSchemeV2Verifier.smali')
        apk_signature_scheme_v3_verifier = os.path.join(directory, 'android/util/apk/ApkSignatureSchemeV3Verifier.smali')
        apk_signing_block_utils = os.path.join(directory, 'android/util/apk/ApkSigningBlockUtils.smali')
        package_parser = os.path.join(directory, 'android/content/pm/PackageParser.smali')
        package_parser_exception = os.path.join(directory,
                                                'android/content/pm/PackageParser$PackageParserException.smali')
        
        if os.path.exists(apk_signature_verifier):
            modify_apk_signature_verifier(apk_signature_verifier)
        if os.path.exists(apk_signature_scheme_v2_verifier):
            modify_apk_signature_scheme_v2_verifier(apk_signature_scheme_v2_verifier)
        if os.path.exists(apk_signature_scheme_v3_verifier):
            modify_apk_signature_scheme_v3_verifier(apk_signature_scheme_v3_verifier)
        if os.path.exists(apk_signing_block_utils):
            modify_apk_signing_block_utils(apk_signing_block_utils)
        if os.path.exists(package_parser):
            modify_package_parser(package_parser)
        if os.path.exists(package_parser_exception):
            modify_exception_file(package_parser_exception)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process some directories.")
    parser.add_argument(
        'current_directory',
        type=str,
        help='The current directory to be appended to the base directories'
    )
    
    args = parser.parse_args()
    current_directory = args.current_directory

    if not os.path.isdir(current_directory):
        sys.exit(1)

    base_directories = [name for name in os.listdir(current_directory)
                        if name == "smali" or re.fullmatch(r"smali_classes\d+", name)]
    directories = [os.path.join(current_directory, name)
                   for name in sorted(base_directories)]

    modify_smali_files(directories)
