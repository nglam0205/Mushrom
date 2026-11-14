#!/usr/bin/env python3
"""
Remove the `final` modifier from android.os.Build / Build$VERSION fields so
VsProps can spoof them via reflection (Android 17+ ART refuses Field.set() on
static final fields even after setAccessible(true)).

Usage:
  patch_build_nonfinal.py <decoded_framework_dir>     # apktool dir (smali, smali_classesN)
  patch_build_nonfinal.py <framework.jar> [-o out.jar] # patch jar directly (baksmali/smali)
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

BIN_DIR = os.path.dirname(os.path.abspath(__file__))
# Same pairs apk.sh uses: smali 3.x writes dex 040, smali_14 (2.2.5) keeps dex 039.
TOOLS_040 = (os.path.join(BIN_DIR, 'baksmali.jar'), os.path.join(BIN_DIR, 'smali.jar'))
TOOLS_039 = (os.path.join(BIN_DIR, 'baksmali_14.jar'), os.path.join(BIN_DIR, 'smali_14.jar'))

TARGETS = {
    'android/os/Build.smali': [
        'BOARD', 'BRAND', 'DEVICE', 'FINGERPRINT', 'HARDWARE', 'ID',
        'MANUFACTURER', 'MODEL', 'PRODUCT', 'TAGS', 'TYPE',
    ],
    'android/os/Build$VERSION.smali': [
        'SECURITY_PATCH', 'RELEASE', 'INCREMENTAL', 'SDK_INT',
    ],
}


def patch_smali_file(file_path, fields):
    with open(file_path, 'r') as file:
        lines = file.readlines()

    patched = []
    field_re = re.compile(r'^(\.field\s+)(.*?)\s(' + '|'.join(fields) + r'):')
    for i, line in enumerate(lines):
        m = field_re.match(line)
        if not m:
            continue
        mods = m.group(2).split()
        if 'final' not in mods:
            continue
        mods.remove('final')
        lines[i] = m.group(1) + ' '.join(mods) + ' ' + line[m.start(3):]
        patched.append(m.group(3))

    with open(file_path, 'w') as file:
        file.writelines(lines)
    return patched


def patch_smali_dirs(directories):
    found = set()
    for directory in directories:
        for rel, fields in TARGETS.items():
            path = os.path.join(directory, rel)
            if not os.path.isfile(path):
                continue
            found.add(rel)
            patched = patch_smali_file(path, fields)
            print(f"[build_nonfinal] {rel}: {', '.join(patched) if patched else 'already non-final'}")
    missing = set(TARGETS) - found
    for rel in sorted(missing):
        print(f"[build_nonfinal] {rel} not found", file=sys.stderr)
    return not missing


def patch_decoded_dir(current_directory):
    # apktool layout (smali, smali_classesN) or apk.sh jar_temp layout (classesN.dex.out)
    names = [n for n in os.listdir(current_directory)
             if n == 'smali' or re.fullmatch(r'smali_classes\d+', n) or re.fullmatch(r'classes\d*\.dex\.out', n)]
    return patch_smali_dirs([os.path.join(current_directory, n) for n in sorted(names)])


def patch_jar(jar_path, out_path, api):
    work = tempfile.mkdtemp(prefix='build_nonfinal_')
    try:
        with zipfile.ZipFile(jar_path) as zf:
            dex_names = [n for n in zf.namelist() if re.fullmatch(r'classes\d*\.dex', n)]
            zf.extractall(work, dex_names)

        classes = ','.join('L' + rel[:-len('.smali')] + ';' for rel in TARGETS)
        rebuilt = {}
        for dex in dex_names:
            dex_path = os.path.join(work, dex)
            out_dir = os.path.join(work, 'out_' + dex)
            with open(dex_path, 'rb') as f:
                dex_version = f.read(8)[4:7].decode()
            baksmali, smali = TOOLS_040 if dex_version >= '040' else TOOLS_039
            subprocess.run(['java', '-jar', baksmali, 'd', dex_path, '-o', out_dir, '-a', str(api),
                            '--classes', classes], check=True, stdout=subprocess.DEVNULL)
            if not any(os.path.isfile(os.path.join(out_dir, rel)) for rel in TARGETS):
                continue
            # Only this dex needs rebuilding: disassemble it fully, patch, reassemble.
            shutil.rmtree(out_dir)
            subprocess.run(['java', '-jar', baksmali, 'd', dex_path, '-o', out_dir, '-a', str(api)],
                           check=True)
            patch_smali_dirs([out_dir])
            new_dex = dex_path + '.new'
            subprocess.run(['java', '-jar', smali, 'a', out_dir, '-o', new_dex, '-a', str(api)],
                           check=True)
            rebuilt[dex] = new_dex
            print(f"[build_nonfinal] {dex}: dex {dex_version} rebuilt with {os.path.basename(smali)}")

        if not rebuilt:
            print('[build_nonfinal] Build classes not found in jar', file=sys.stderr)
            return False

        tmp_out = out_path + '.tmp'
        with zipfile.ZipFile(jar_path) as src, zipfile.ZipFile(tmp_out, 'w') as dst:
            for info in src.infolist():
                data = open(rebuilt[info.filename], 'rb').read() if info.filename in rebuilt \
                    else src.read(info.filename)
                dst.writestr(info, data)
        os.replace(tmp_out, out_path)
        print(f"[build_nonfinal] wrote {out_path} (rebuilt {', '.join(sorted(rebuilt))})")
        return True
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Drop final from android.os.Build spoof fields')
    parser.add_argument('target', help='Decoded framework dir or framework.jar')
    parser.add_argument('-o', '--output', help='Output jar (jar mode, default: overwrite input)')
    parser.add_argument('-a', '--api', type=int, default=34, help='API level for baksmali/smali (jar mode)')
    args = parser.parse_args()

    if os.path.isdir(args.target):
        ok = patch_decoded_dir(args.target)
    elif os.path.isfile(args.target) and args.target.endswith('.jar'):
        ok = patch_jar(args.target, args.output or args.target, args.api)
    else:
        print(f'[build_nonfinal] invalid target: {args.target}', file=sys.stderr)
        ok = False
    sys.exit(0 if ok else 1)
