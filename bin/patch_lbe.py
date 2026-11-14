import re
import sys


PERMISSION_NEW_INSTANCE = re.compile(
    # Older builds use com.lbe.security.bean, HyperOS builds use com.hyperos.security.bean.
    r"^\s*new-instance\s+[^,]+,\s*Lcom/(?:lbe|hyperos)/security/bean/Permission;"
)
PERMISSION_ADD = re.compile(
    r"^(\s*)invoke-virtual\s+\{[^}]+\},\s*"
    r"Ljava/util/ArrayList;->add\(Ljava/lang/Object;\)Z\s*$"
)


def find_permission_block(lines: list[str], target: str) -> tuple[int, int] | None:
    """Return the Permission-construction block containing target."""

    starts = [i for i, line in enumerate(lines) if PERMISSION_NEW_INSTANCE.match(line)]
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        if any(target in line for line in lines[start:end]):
            return start, end
    return None


def patch_permission(target: str, input_file: str) -> bool:
    with open(input_file, "r", encoding="utf-8") as file:
        lines = file.readlines()

    block = find_permission_block(lines, target)
    if block is None:
        print(f"patch_lbe: permission block not found for {target}", file=sys.stderr)
        return False

    start, end = block
    add_index = None
    for i in range(start, end):
        if PERMISSION_ADD.match(lines[i]):
            add_index = i

    if add_index is None:
        print(f"patch_lbe: ArrayList.add not found for {target}", file=sys.stderr)
        return False

    match = PERMISSION_ADD.match(lines[add_index])
    lines[add_index] = (
        f"{match.group(1)}# VS_PATCH_LBE: omit {target} from sPermissions\n"
    )

    with open(input_file, "w", encoding="utf-8") as file:
        file.writelines(lines)
    return True


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: patch_lbe.py <target> <input.smali>", file=sys.stderr)
        return 2

    return 0 if patch_permission(sys.argv[1], sys.argv[2]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
