#!/usr/bin/env python3
"""
music_session_patch.py
======================
Tự động tìm và patch smali trong MIUI subscreencenter để cho phép
MỌI music app hiển thị trên sub-screen (không cần whitelist).

Hoạt động với mọi phiên bản nhờ regex - không hardcode tên file/class.

Cơ chế (5 patch):
  1 & 2  ActiveAudioSessionManager + OnActiveSessionListener
         → bypass filter getMusicPackageList → isEmpty → if-nez → goto
  3      Initializer gọi addMusicPackageList
         → skip → mMusicPackages luôn rỗng → filter tự skip
  4      handleTopMediaDataChanged: "Unsupported music app" check
         → isMusicApp if-nez → goto
  5      p2/c.k(): music switch gate
         → isMusicApp if-eqz → removed (all apps use music switch path)

Usage:
  python3 music_session_patch.py <decompiled_dir>
  python3 music_session_patch.py <decompiled_dir> --dry-run
"""

import os
import re
import sys
import argparse
from pathlib import Path

REG = r'[vp]\d+'


class Patch:
    def __init__(self, name, find_patterns, replacements, exclude_patterns=None):
        self.name             = name
        self.find_patterns    = find_patterns
        self.replacements     = replacements
        self.exclude_patterns = exclude_patterns or []

    def matches_file(self, content: str) -> bool:
        if any(re.search(p, content) for p in self.exclude_patterns):
            return False
        return all(re.search(p, content, re.DOTALL) for p in self.find_patterns)

    def already_applied(self, content: str) -> bool:
        for pat, _, flags, _ in self.replacements:
            if re.search(pat, content, flags):
                return False
        return True


def build_patches():
    # ── Patch 1 & 2: bypass filter dalam ActiveAudioSessionManager ─────────
    filter_pat_nez = (
        r'(invoke-static\s+\{\},\s+'
        r'Lcom/miui/maml/elements/MusicController;->getMusicPackageList\(\)Ljava/util/List;\s+'
        r'move-result-object\s+(' + REG + r')\s+'
        r'invoke-interface\s+\{\2\},\s+Ljava/util/List;->isEmpty\(\)Z\s+'
        # Android 17 keeps the list in its register and puts isEmpty() in another.
        r'move-result\s+(' + REG + r')'
        r')(\s+)'
        r'if-nez\s+\3,\s+(:\w+)'
    )
    filter_pat_eqz = filter_pat_nez.replace(r'if-nez', r'if-eqz')
    filter_repl = r'\1\4goto \5'

    patch_filter = Patch(
        name="Session Filter Bypass",
        # Android 17 walks the sessions by index and removes through
        # AbstractCollection, so Iterator.remove() is no longer a marker.
        find_patterns=[
            r'getMusicPackageList\(\)Ljava/util/List;',
            r'MediaController;->getPackageName\(\)',
        ],
        replacements=[
            (filter_pat_nez, filter_repl, re.DOTALL, "getMusicPackageList isEmpty if-nez → goto"),
            (filter_pat_eqz, filter_repl, re.DOTALL, "getMusicPackageList isEmpty if-eqz → goto"),
        ],
    )

    # ── Patch 3: block addMusicPackageList ──────────────────────────────────
    init_pat_nez = (
        r'(invoke-virtual\s+\{' + REG + r'\},\s+'
        r'Ljava/util/ArrayList;->isEmpty\(\)Z\s+'
        r'move-result\s+(' + REG + r')'
        r')(\s+)'
        r'if-nez\s+\2,\s+(:\w+)'
        r'(?=[\s\S]{0,800}?addMusicPackageList\(Ljava/util/List;\)V)'
    )
    init_pat_eqz = init_pat_nez.replace(r'if-nez', r'if-eqz')
    init_repl = r'\1\3goto \4'

    patch_init = Patch(
        name="Block addMusicPackageList",
        find_patterns=[
            r'addMusicPackageList\(Ljava/util/List;\)V',
            r'Ljava/util/ArrayList;->isEmpty\(\)Z',
        ],
        exclude_patterns=[r'\.method public static addMusicPackageList'],
        replacements=[
            (init_pat_nez, init_repl, re.DOTALL, "addMusicPackageList isEmpty if-nez → goto"),
            (init_pat_eqz, init_repl, re.DOTALL, "addMusicPackageList isEmpty if-eqz → goto"),
        ],
    )

    # ── Patch 4: "Unsupported music app" widget check ───────────────────────
    unsupported_pat_nez = (
        r'(invoke-static\s+\{(' + REG + r')\},\s+'
        r'L[\w/]+;->c\(Ljava/lang/String;\)Z\s+'
        r'move-result\s+(' + REG + r')'
        r')(\s+)'
        r'if-nez\s+\3,\s+(:\w+)'
        r'(?=[\s\S]{0,500}?"Unsupported music app)'
    )
    unsupported_pat_eqz = (
        r'(invoke-static\s+\{(' + REG + r')\},\s+'
        r'L[\w/]+;->c\(Ljava/lang/String;\)Z\s+'
        r'move-result\s+(' + REG + r')'
        r')(\s+)'
        r'if-eqz\s+\3,\s+(:\w+)'
        r'(?=[\s\S]{0,500}?"Unsupported music app)'
    )

    patch_unsupported = Patch(
        name="Unsupported Music App Check Bypass",
        find_patterns=[
            r'"Unsupported music app',
            r'L[\w/]+;->c\(Ljava/lang/String;\)Z',
        ],
        replacements=[
            (unsupported_pat_nez, r'\1\4goto \5', re.DOTALL, "isMusicApp if-nez → goto"),
            (unsupported_pat_eqz, r'\1\4goto \5', re.DOTALL, "isMusicApp if-eqz → goto"),
        ],
    )

    # ── Patch 5: music switch gate in p2/c.k() ──────────────────────────────
    # Older builds load 0x0, "SmartAssistantUtils" and 0x1 between the
    # isMusicApp result and the branch; OS4.0 (m2/d.m) keeps only 0x1 there.
    music_switch_pat = (
        r'(invoke-static\s+\{p0\},\s+L[\w/]+;->c\(Ljava/lang/String;\)Z\s+'
        r'move-result\s+(' + REG + r')'
        r'(?:\s+const(?:/4|/16|-string)?\s+' + REG + r',\s+[^\n]+)*'
        r')(\s+)'
        r'if-eqz\s+\2,\s+(:\w+)'
        r'(?=[\s\S]{0,200}?"com\.music\.service")'
    )
    music_switch_repl = (
        r'\1\3'
        r'# PATCH: bypass whitelist check → all apps use music switch path (like Spotify)'
    )

    patch_music_switch = Patch(
        name="Music Switch Gate Bypass (p2/c.k)",
        find_patterns=[
            r'"Music app %s allowed: %s \(music switch: %s\)"',
            r'"com\.music\.service"',
            r'L[\w/]+;->c\(Ljava/lang/String;\)Z',
        ],
        replacements=[
            (music_switch_pat, music_switch_repl, re.DOTALL, "k() isMusicApp if-eqz → remove"),
        ],
    )

    return [patch_filter, patch_init, patch_unsupported, patch_music_switch]


def iter_smali(root: str):
    for path in Path(root).rglob("*.smali"):
        yield path


def apply_patch(path, patch: Patch, dry_run: bool) -> bool:
    content = Path(path).read_text(encoding="utf-8", errors="replace")
    original = content
    for pat, repl, flags, _ in patch.replacements:
        content, _ = re.subn(pat, repl, content, flags=flags)
    changed = content != original
    if changed and not dry_run:
        Path(path).write_text(content, encoding="utf-8")
    return changed


def run(root: str, dry_run: bool):
    patches = build_patches()
    all_files = list(iter_smali(root))
    total = 0
    verbose = os.environ.get("VS_VERBOSE_LOG") == "1"

    for patch in patches:
        for f in all_files:
            try:
                content = Path(f).read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if not patch.matches_file(content):
                continue
            if patch.already_applied(content):
                continue
            changed = apply_patch(f, patch, dry_run)
            if changed:
                total += 1
                rel = os.path.relpath(str(f), root)
                if verbose:
                    prefix = "[DRY] " if dry_run else ""
                    print(f"[PATCH] SubscreenPatch - {rel}: {prefix}{patch.name}")

    if total == 0:
        print("[SKIP]  SubscreenPatch - MusicSession: already done or not found")
    else:
        suffix = " (dry run)" if dry_run else ""
        print(f"[DONE]  SubscreenPatch - MusicSession: {total} file(s) patched{suffix}")


def main():
    ap = argparse.ArgumentParser(
        description="Patch MIUI subscreencenter smali - allow all music apps on sub-screen"
    )
    ap.add_argument("apk_dir", help="apktool output directory (contains smali/)")
    ap.add_argument("--dry-run", action="store_true", help="preview only, no writes")
    args = ap.parse_args()

    root = os.path.abspath(args.apk_dir)
    if not os.path.isdir(root):
        print(f"error: directory not found: {root}", file=sys.stderr)
        sys.exit(1)

    run(root, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
