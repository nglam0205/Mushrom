#!/usr/bin/env python3
"""
Patch QS customize smali to extend personal area row handling.

Design goals:
- Works across APK variants where local register names differ.
- Uses method signatures + opcode patterns, not fixed variable names.
- Safe by default: creates .bak backups (disable with --no-backup).

Usage:
  python3 patch_qs_rowlimit_smali.py --root /path/to/decompiled_apk/SystemUI
  python3 patch_qs_rowlimit_smali.py --root . --dry-run
  python3 patch_qs_rowlimit_smali.py --root . --summary-only
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# Global configurable target row count.
TARGET_ROW = 6
TARGET_ROW_HEX = format(TARGET_ROW, "x")
TARGET_ROW_TAG = f"row_min{TARGET_ROW}"


class PatchError(Exception):
    pass


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def split_method_block(text: str, method_sig_regex: str) -> Optional[Tuple[int, int, str]]:
    """
    Return (start_idx, end_idx, block_text) for first method matching signature regex.
    end_idx is exclusive and includes '.end method'.
    """
    m = re.search(method_sig_regex, text, flags=re.MULTILINE)
    if not m:
        return None
    start = m.start()
    end_m = re.search(r"^\.end method\s*$", text[m.end() :], flags=re.MULTILINE)
    if not end_m:
        raise PatchError(f"Found method start but no .end method for regex: {method_sig_regex}")
    end = m.end() + end_m.end()
    return start, end, text[start:end]


def replace_method_block(
    text: str, method_sig_regex: str, transform: Callable[[str], Tuple[str, bool]]
) -> Tuple[str, bool]:
    found = split_method_block(text, method_sig_regex)
    if not found:
        return text, False
    start, end, block = found
    new_block, changed = transform(block)
    if not changed:
        return text, False
    return text[:start] + new_block + text[end:], True


def patch_on_enter_edit_mode(block: str) -> Tuple[str, bool]:
    """
    In OplusQSPersonalComponent.onEnterEditMode(Z)V:
    change updateAreaRow(4/6) -> updateAreaRow(TARGET_ROW), regardless of register names.
    """
    pattern = re.compile(
        r"(const/(?:4|16)\s+(v\d+),\s+0x)(?:4|6)"
        r"(\s*\n\s*invoke-virtual\s+\{[^}]*\b\2\b\},\s+"
        r"Lcom/oplus/systemui/plugins/qs/customize/component/proxy/CellSizeChangeProxy;->updateAreaRow\(I\)V)",
        flags=re.MULTILINE,
    )
    new_block, n = pattern.subn(rf"\g<1>{TARGET_ROW_HEX}\3", block, count=1)
    return new_block, n > 0


def patch_collectflow_row_full_check(content: str) -> Tuple[str, bool]:
    """
    In collectFlow synthetic class, update full-row constant 4 -> TARGET_ROW
    around TreeSet size check leading to saveLatestNoEditModeLocList call.
    """
    pattern = re.compile(
        r"(TreeSet;->size\(\)I[\s\S]{0,120}?const/(?:4|16)\s+v\d+,\s+0x)(?:4|6)"
        r"([\s\S]{0,120}?saveLatestNoEditModeLocList)",
        flags=re.MULTILINE,
    )
    new_content, n = pattern.subn(rf"\g<1>{TARGET_ROW_HEX}\2", content, count=1)
    if n > 0:
        return new_content, True

    # Fallback for variants: patch the first TreeSet-size comparison constant 4/6 -> TARGET_ROW
    # even when method/call names are changed.
    fallback = re.compile(
        r"(TreeSet;->size\(\)I[\s\S]{0,120}?const/(?:4|16)\s+v\d+,\s+0x)(?:4|6)"
        r"([\s\S]{0,80}?if-(?:ne|eq)\s+v\d+,\s+v\d+,\s*:[\w$]+)",
        flags=re.MULTILINE,
    )
    newer_content, n2 = fallback.subn(rf"\g<1>{TARGET_ROW_HEX}\2", content, count=1)
    return newer_content, n2 > 0


def patch_add_one_tile_alg_process(block: str) -> Tuple[str, bool]:
    """
    In AddOneTileAlg.process(...):
    force local gridRow to be at least TARGET_ROW before scanning slots.
    """
    # If already patched, skip.
    if re.search(
        rf"getGridRow\(\)I[\s\S]{{0,140}}?const/4\s+v\d+,\s+0x{TARGET_ROW_HEX}[\s\S]{{0,120}}?if-ge\s+v\d+,\s+v\d+,\s*:(?:{TARGET_ROW_TAG}_ok|cond_row_ok)",
        block,
    ):
        return block, False

    p = re.compile(
        r"(invoke-virtual\s+\{p1\},\s+"
        r"Lcom/oplus/systemui/plugins/qs/customize/data/model/GridInfoEntity;->getGridRow\(\)I\s*\n\s*"
        r"move-result\s+(v\d+)\s*\n)",
        flags=re.MULTILINE,
    )
    m = p.search(block)
    if not m:
        return block, False
    row_reg = m.group(2)

    # Try to reuse v7 as temp (common in this method).
    inject = (
        f"{m.group(1)}\n"
        f"    const/4 v7, 0x{TARGET_ROW_HEX}\n\n"
        f"    if-ge {row_reg}, v7, :{TARGET_ROW_TAG}_ok\n\n"
        f"    move {row_reg}, v7\n\n"
        f"    :{TARGET_ROW_TAG}_ok\n"
    )
    new_block = block[: m.start()] + inject + block[m.end() :]
    return new_block, True


def patch_mapper_default_grid(block: str) -> Tuple[str, bool]:
    """
    In SepTileConfigMapperRepository.<clinit>:
    make DEFAULT_GRID_INFO = GridInfoEntity(user=0, col=4, row=TARGET_ROW).
    """
    changed = False
    # Ensure enough locals for fallback temp register v3.
    locals_pat = re.compile(r"(\.method\s+static constructor <clinit>\(\)V\s*\n\s*\.locals\s+)(\d+)")
    lm = locals_pat.search(block)
    if lm and int(lm.group(2)) < 4:
        block = block[: lm.start(2)] + "4" + block[lm.end(2) :]
        changed = True

    # Fast path for common exact form (including builds without `.line` entries):
    #   const/4 v1, 0x0
    #   const/4 v2, 0x4
    #   invoke-direct {v0, v1, v2, v2}, GridInfoEntity.<init>(III)V
    exact_shared = re.compile(
        r"(?ms)"
        r"(\bconst/4\s+v1,\s+0x0\s*\n\s*"
        r"const/4\s+v2,\s+0x4\s*\n)\s*"
        r"invoke-direct\s+\{v0,\s+v1,\s+v2,\s+v2\},\s*"
        r"Lcom/oplus/systemui/plugins/qs/customize/data/model/GridInfoEntity;-><init>\(III\)V"
    )
    m_exact = exact_shared.search(block)
    if m_exact:
        replacement = (
            m_exact.group(1)
            + f"    const/4 v3, 0x{TARGET_ROW_HEX}\n"
            + "    invoke-direct {v0, v1, v2, v3}, "
            + "Lcom/oplus/systemui/plugins/qs/customize/data/model/GridInfoEntity;-><init>(III)V"
        )
        block = block[: m_exact.start()] + replacement + block[m_exact.end() :]
        return block, True

    # Find first GridInfoEntity ctor call in <clinit>.
    ctor_line = re.search(
        r"^\s*invoke-direct\s+\{(v\d+),\s*(v\d+),\s*(v\d+)\},\s*"
        r"Lcom/oplus/systemui/plugins/qs/customize/data/model/GridInfoEntity;-><init>\(III\)V\s*$",
        block,
        flags=re.MULTILINE,
    )
    if not ctor_line:
        return block, changed

    user_reg, col_reg, row_reg = ctor_line.group(1), ctor_line.group(2), ctor_line.group(3)
    invoke_line = ctor_line.group(0)
    indent = re.match(r"\s*", invoke_line).group(0)
    pre = block[: ctor_line.start()]

    # Already patched case.
    if row_reg == "v3":
        if re.search(rf"const(?:/4|/16)?\s+v3,\s+0x{TARGET_ROW_HEX}\b", pre):
            return block, changed

    # Case 1: col and row share same register (e.g. v2,v2) -> must split row into v3.
    if row_reg == col_reg:
        injected = (
            f"{indent}const/4 v3, 0x{TARGET_ROW_HEX}\n"
            f"{indent}invoke-direct {{{user_reg}, {col_reg}, v3}}, "
            "Lcom/oplus/systemui/plugins/qs/customize/data/model/GridInfoEntity;-><init>(III)V"
        )
        block = block[: ctor_line.start()] + injected + block[ctor_line.end() :]
        return block, True

    # Case 2: separate row register exists -> try rewriting immediate const for row.
    const_row_iter = list(
        re.finditer(
            rf"^\s*(const(?:/4|/16|/high16)?|const)\s+{row_reg},\s+0x[0-9a-fA-F]+\s*$",
            pre,
            flags=re.MULTILINE,
        )
    )
    if const_row_iter:
        const_row = const_row_iter[-1]  # nearest assignment before ctor
        line = const_row.group(0)
        if not re.search(rf",\s+0x{TARGET_ROW_HEX}\b", line):
            new_line = re.sub(r",\s+0x[0-9a-fA-F]+\s*$", f", 0x{TARGET_ROW_HEX}", line)
            block = block[: const_row.start()] + new_line + block[const_row.end() :]
            return block, True
        return block, changed

    # Case 3: fallback, inject explicit row=TARGET_ROW into v3 and route ctor row arg to v3.
    injected = (
        f"{indent}const/4 v3, 0x{TARGET_ROW_HEX}\n"
        f"{indent}invoke-direct {{{user_reg}, {col_reg}, v3}}, "
        "Lcom/oplus/systemui/plugins/qs/customize/data/model/GridInfoEntity;-><init>(III)V"
    )
    block = block[: ctor_line.start()] + injected + block[ctor_line.end() :]
    changed = True
    return block, changed


def patch_user_repo_load_gridinfo(block: str) -> Tuple[str, bool]:
    """
    In SepUserTileConfigRepository.loadGridInfoFromDBOrDefault:
    clamp returned GridInfoEntity.gridRow >= TARGET_ROW.
    """
    if f":{TARGET_ROW_TAG}_return_ok" in block:
        return block, False

    # Replace first "return-object p2" after a label with clamp logic.
    ret_pat = re.compile(r"(\n\s*:[\w$]+\s*\n)\s*return-object p2\b", flags=re.MULTILINE)
    rm = ret_pat.search(block)
    if not rm:
        return block, False

    injected = (
        f"{rm.group(1)}"
        "    invoke-virtual {p2}, Lcom/oplus/systemui/plugins/qs/customize/data/model/GridInfoEntity;->getGridRow()I\n\n"
        "    move-result p0\n\n"
        f"    const/4 v0, 0x{TARGET_ROW_HEX}\n\n"
        f"    if-lt p0, v0, :{TARGET_ROW_TAG}_build\n\n"
        f"    :{TARGET_ROW_TAG}_return_ok\n"
        "    return-object p2\n\n"
        f"    :{TARGET_ROW_TAG}_build\n"
        "    new-instance p0, Lcom/oplus/systemui/plugins/qs/customize/data/model/GridInfoEntity;\n\n"
        "    invoke-virtual {p2}, Lcom/oplus/systemui/plugins/qs/customize/data/model/GridInfoEntity;->getUserId()I\n\n"
        "    move-result p1\n\n"
        "    invoke-virtual {p2}, Lcom/oplus/systemui/plugins/qs/customize/data/model/GridInfoEntity;->getGridCol()I\n\n"
        "    move-result p2\n\n"
        "    invoke-direct {p0, p1, p2, v0}, Lcom/oplus/systemui/plugins/qs/customize/data/model/GridInfoEntity;-><init>(III)V\n\n"
        "    return-object p0"
    )
    new_block = block[: rm.start()] + injected + block[rm.end() :]
    return new_block, True


def patch_user_repo_fallback_any_method(text: str) -> Tuple[str, bool]:
    """
    Fallback for ROM variants where loadGridInfoFromDBOrDefault method name/signature changed.
    Strategy:
    - scan all methods in SepUserTileConfigRepository
    - pick a method that handles GridInfoEntity + default grid loading
    - inject same clamp logic at first `return-object p2`.
    """
    method_re = re.compile(
        r"(?ms)^\.method[^\n]*\n.*?^\.end method\s*$"
    )
    for m in method_re.finditer(text):
        block = m.group(0)
        if "Lcom/oplus/systemui/plugins/qs/customize/data/model/GridInfoEntity;" not in block:
            continue
        if "getDefaultGridInfo()Ljava/util/List;" not in block:
            continue
        if "return-object p2" not in block:
            continue
        new_block, changed = patch_user_repo_load_gridinfo(block)
        if not changed:
            continue
        return text[: m.start()] + new_block + text[m.end() :], True
    return text, False


def apply_file_patch(
    path: Path,
    patch_fn: Callable[[str], Tuple[str, bool]],
    backup: bool,
    dry_run: bool,
) -> bool:
    if not path.exists():
        return False
    old = read_text(path)
    new, changed = patch_fn(old)
    if not changed:
        return False
    if not dry_run:
        if backup:
            backup_path = path.with_suffix(path.suffix + ".bak")
            if not backup_path.exists():
                write_text(backup_path, old)
        write_text(path, new)
    return True


def main() -> int:
    global TARGET_ROW, TARGET_ROW_HEX, TARGET_ROW_TAG
    parser = argparse.ArgumentParser(description="Patch QS row limit related smali files.")
    parser.add_argument("--root", required=True, help="Root folder containing SystemUI smali files.")
    parser.add_argument("--dry-run", action="store_true", help="Only show what would change.")
    parser.add_argument("--no-backup", action="store_true", help="Do not create .bak files.")
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print patch result summary without listing touched files.",
    )
    parser.add_argument(
        "--target-row",
        type=int,
        default=TARGET_ROW,
        help=f"Target personal area row count (default: {TARGET_ROW}).",
    )
    args = parser.parse_args()
    if args.target_row <= 0 or args.target_row > 15:
        raise SystemExit("--target-row must be between 1 and 15")

    TARGET_ROW = args.target_row
    TARGET_ROW_HEX = format(TARGET_ROW, "x")
    TARGET_ROW_TAG = f"row_min{TARGET_ROW}"

    root = Path(args.root).expanduser().resolve()
    backup = not args.no_backup

    files_touched: List[Path] = []
    results: Dict[str, bool] = {}

    # 1) OplusQSPersonalComponent.onEnterEditMode
    comp = next(
        iter(root.glob("**/com/oplus/systemui/plugins/qs/customize/component/OplusQSPersonalComponent.smali")),
        None,
    )
    if comp:
        def _patch_comp(text: str) -> Tuple[str, bool]:
            return replace_method_block(
                text,
                r"^\.method\s+public\s+final\s+onEnterEditMode\(Z\)V\s*$",
                patch_on_enter_edit_mode,
            )

        changed = apply_file_patch(comp, _patch_comp, backup, args.dry_run)
        results["onEnterEditMode updateAreaRow"] = changed
        if changed:
            files_touched.append(comp)
    else:
        results["onEnterEditMode updateAreaRow"] = False

    # 2) collectFlow synthetic class row full check
    collect_candidates = list(
        root.glob(
            "**/com/oplus/systemui/plugins/qs/customize/component/OplusQSPersonalComponent$collectFlow*.smali"
        )
    )
    collect_changed = False
    for cf in collect_candidates:
        changed = apply_file_patch(cf, patch_collectflow_row_full_check, backup, args.dry_run)
        if changed:
            collect_changed = True
            files_touched.append(cf)
            break
    results["collectFlow full-row check"] = collect_changed

    # 3) AddOneTileAlg.process min row clamp
    add_alg = next(
        iter(root.glob("**/com/oplus/systemui/plugins/qs/customize/data/algorithm/AddOneTileAlg.smali")),
        None,
    )
    if add_alg:
        def _patch_add_alg(text: str) -> Tuple[str, bool]:
            return replace_method_block(
                text,
                r"^\.method\s+public\s+process\(Lcom/oplus/systemui/plugins/qs/customize/data/algorithm/AddInputData;\)Ljava/util/List;\s*$",
                patch_add_one_tile_alg_process,
            )

        changed = apply_file_patch(add_alg, _patch_add_alg, backup, args.dry_run)
        results["AddOneTileAlg min row clamp"] = changed
        if changed:
            files_touched.append(add_alg)
    else:
        results["AddOneTileAlg min row clamp"] = False

    # 4) SepTileConfigMapperRepository DEFAULT_GRID_INFO
    mapper = next(
        iter(root.glob("**/com/oplus/systemui/plugins/qs/customize/data/repo/SepTileConfigMapperRepository.smali")),
        None,
    )
    if mapper:
        def _patch_mapper(text: str) -> Tuple[str, bool]:
            updated, changed = replace_method_block(
                text,
                r"^\.method\s+static constructor <clinit>\(\)V\s*$",
                patch_mapper_default_grid,
            )
            if changed:
                return updated, True

            # Fallback for stubborn variants where method-level parser misses but
            # DEFAULT_GRID_INFO is still initialized as {..., colReg, colReg}.
            fallback = re.compile(
                r"^(\s*)invoke-direct\s+\{(v\d+),\s*(v\d+),\s*(v\d+),\s*\4\},\s+"
                r"Lcom/oplus/systemui/plugins/qs/customize/data/model/GridInfoEntity;-><init>\(III\)V\s*$",
                flags=re.MULTILINE,
            )
            m = fallback.search(text)
            if not m:
                return text, False
            indent, obj_reg, user_reg, col_reg = m.group(1), m.group(2), m.group(3), m.group(4)
            replacement = (
                f"{indent}const/4 v3, 0x{TARGET_ROW_HEX}\n"
                f"{indent}invoke-direct {{{obj_reg}, {user_reg}, {col_reg}, v3}}, "
                "Lcom/oplus/systemui/plugins/qs/customize/data/model/GridInfoEntity;-><init>(III)V"
            )
            newer = text[: m.start()] + replacement + text[m.end() :]
            n = 1
            return newer, n > 0

        changed = apply_file_patch(mapper, _patch_mapper, backup, args.dry_run)
        results["Mapper DEFAULT_GRID_INFO"] = changed
        if changed:
            files_touched.append(mapper)
    else:
        results["Mapper DEFAULT_GRID_INFO"] = False

    # 5) SepUserTileConfigRepository.loadGridInfoFromDBOrDefault
    user_repo = next(
        iter(root.glob("**/com/oplus/systemui/plugins/qs/customize/data/repo/SepUserTileConfigRepository.smali")),
        None,
    )
    if user_repo:
        def _patch_user_repo(text: str) -> Tuple[str, bool]:
            updated, changed = replace_method_block(
                text,
                r"^\.method\s+private\s+final\s+loadGridInfoFromDBOrDefault\(ILkotlin/coroutines/Continuation;\)Ljava/lang/Object;\s*$",
                patch_user_repo_load_gridinfo,
            )
            if changed:
                return updated, True
            # Fallback for method name/signature changes across builds.
            return patch_user_repo_fallback_any_method(text)

        changed = apply_file_patch(user_repo, _patch_user_repo, backup, args.dry_run)
        results["UserRepo loadGridInfo clamp"] = changed
        if changed:
            files_touched.append(user_repo)
    else:
        results["UserRepo loadGridInfo clamp"] = False

    print("=== Patch summary ===")
    for k, v in results.items():
        print(f"[{'OK' if v else 'SKIP'}] {k}")
    if not args.summary_only:
        print("")
        if files_touched:
            mode = "would modify" if args.dry_run else "modified"
            print(f"{mode} {len(files_touched)} file(s):")
            for p in files_touched:
                print(f"- {p}")
        else:
            print("No changes applied.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
