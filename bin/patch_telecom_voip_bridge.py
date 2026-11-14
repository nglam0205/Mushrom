#!/usr/bin/env python3
import re
import sys
from pathlib import Path


# Watch companion apps (Huawei Health, Mi Fitness, Zepp, Galaxy Wearable, the
# apps of no-name bands...) forward incoming calls to the watch from the
# PHONE_STATE broadcast and hang up through TelecomManager.endCall(). Telecom
# treats self-managed (VoIP) calls as second-class on both paths:
#
#   * PhoneStateBroadcaster reports RINGING for them but strips the caller, so
#     the watch rings late and with no name (Huawei Health: "doPhoneRing
#     incomingNumber is null").
#   * TelecomServiceImpl#endCallInternal() ignores the request unless the
#     caller is root/system/shell ("self-mgd disconnect ignored from
#     non-privileged app ..."), so "reject" on the watch does nothing.
#
# Report the caller of self-managed calls (the name in the app's call
# notification, else its Telecom display name, else the handle) and let any
# app that may end calls (ANSWER_PHONE_CALLS, checked before this point) end
# them too, as it can for a cellular call. No watch app is singled out.
#
# ColorOS ships Telecom through R8: TelecomServiceImpl/PhoneStateBroadcaster
# and the Call accessors are renamed or inlined to field reads. Nothing here
# relies on those names; the targets are found from a log string, framework
# calls and the ConnectionService AIDL adapter, which cannot be renamed.
MARKER = "# VS_PATCH_VOIP_WATCH_BRIDGE"

PACKAGE_DIR = "com/android/server/telecom"
CALL = "Lcom/android/server/telecom/Call;"
END_CALL_ANCHOR = '"self-mgd disconnect ignored'
SSP = "Landroid/net/Uri;->getSchemeSpecificPart()Ljava/lang/String;"
NOTIFY = ("Landroid/telephony/TelephonyRegistryManager;->"
          "notifyCallStateChangedForAllSubscriptions(ILjava/lang/String;)V")
# backend/vsteam, merged into framework.jar. tag() marks the caller of a
# self-managed call where Telecom computes it, under its lock. notifyState()
# sends the phone state in Telecom's place: for a marked RINGING it first
# waits, off Telecom's lock, for the app's incoming-call notification and
# takes the name from there, which some apps (Messenger) hide from Telecom.
HELPER = "Lcom/android/vsteam/VoipCaller;"
TAG = f"{HELPER}->tag(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;"
NOTIFY_STATE = f"{HELPER}->notifyState(Ljava/lang/Object;ILjava/lang/String;)V"

METHOD = re.compile(r"(?ms)^(?P<header>\.method[^\n]*)\n(?P<body>.*?)^\.end method$")
REGISTERS = re.compile(r"^(\s*)\.(registers|locals) (\d+)$", re.M)
GAP = r"\n(?:[ \t]*\n)*"
REG = r"[vp]\d+"
CALL_RE = re.escape(CALL)
# A boolean read from a Call: a getter call, or the field read R8 inlines it to.
CALL_FLAG = re.compile(
    rf"(?m)^(?P<indent>[ \t]*)(?:"
    rf"invoke-virtual(?:/range)? \{{(?P<c1>{REG})(?: \.\. {REG})?\}}, {CALL_RE}->(?P<method>\w+)\(\)Z"
    rf"{GAP}[ \t]*move-result (?P<r1>{REG})"
    rf"|iget-boolean (?P<r2>{REG}), (?P<c2>{REG}), {CALL_RE}->(?P<field>\w+):Z"
    rf")\n"
)
NEXT_BRANCH = re.compile(rf"(?:[ \t]*\n)*[ \t]*(?P<op>if-eqz|if-nez) (?P<reg>{REG}), (?P<label>:\w+)\n")


class Flag:
    """One CALL_FLAG match."""

    def __init__(self, match: re.Match):
        self.end = match.end()
        self.indent = match.group("indent")
        self.method = match.group("method")
        self.field = match.group("field")
        self.reg = match.group("r1") or match.group("r2")
        self.call = match.group("c1") or match.group("c2")


def methods(source: str):
    return METHOD.finditer(source)


def call_method(call_source: str, name: str, signature: str) -> str | None:
    for method in methods(call_source):
        if re.search(rf"\s{re.escape(name)}{re.escape(signature)}$", method.group("header")):
            return method.group("body")
    return None


def instructions(body: str) -> list[str]:
    return [line.strip() for line in body.splitlines()
            if line.strip() and not line.strip().startswith((".", "#", ":"))]


def parameter_words(header: str) -> int:
    args = re.search(r"\(([^)]*)\)", header).group(1)
    words = 0 if " static " in f" {header} " else 1
    for token in re.finditer(r"\[*(?:L[^;]+;|[ZBCSIFJD])", args):
        words += 2 if token.group(0) in ("J", "D") else 1
    return words


def add_local(header: str, body: str) -> tuple[str, str]:
    """Grow the frame by one local; returns (body, new register name)."""
    match = REGISTERS.search(body)
    if not match:
        raise RuntimeError("method has no .registers/.locals line")
    indent, kind, count = match.group(1), match.group(2), int(match.group(3))
    words = parameter_words(header)
    total = count if kind == "registers" else count + words
    # Keep every register, parameters included, addressable in 4 bits.
    if total + 1 > 16:
        raise RuntimeError("method frame too large to add a local")
    body = body[:match.start()] + f"{indent}.{kind} {count + 1}" + body[match.end():]
    return body, f"v{total - words}"


def first_instruction(body: str) -> int:
    offset = 0
    for line in body.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and not stripped.startswith((".", "#")):
            return offset
        offset += len(line)
    raise RuntimeError("method has no instructions")


def invoke(kind: str, reg: str, target: str) -> str:
    """A one-argument invoke; parameter registers may not fit the 4-bit form."""
    if reg[0] == "v" and int(reg[1:]) < 16:
        return f"invoke-{kind} {{{reg}}}, {target}"
    return f"invoke-{kind}/range {{{reg} .. {reg}}}, {target}"


def replace_method(source: str, method: re.Match, body: str) -> str:
    return source[:method.start("body")] + body + source[method.end("body"):]


def patch_end_call(source: str) -> tuple[str, Flag]:
    """Drops the isSelfManaged() branch that ignores the request."""
    method = next((m for m in methods(source) if END_CALL_ANCHOR in m.group("body")), None)
    if not method:
        raise RuntimeError("endCall method not found")
    body = method.group("body")
    anchor = body.index(END_CALL_ANCHOR)
    # The request is only ignored for self-managed calls: the last Call flag
    # that has to be true before the log line is isSelfManaged().
    flag = None
    for match in CALL_FLAG.finditer(body, 0, anchor):
        branch = NEXT_BRANCH.match(body, match.end())
        if branch and branch.group("op") == "if-eqz" and branch.group("reg") in (
                match.group("r1"), match.group("r2")):
            flag = Flag(match)
    if not flag:
        raise RuntimeError("endCall has no isSelfManaged() check")
    hook = (
        f"\n{flag.indent}{MARKER}: self-managed calls can be ended like any other\n"
        f"{flag.indent}const/4 {flag.reg}, 0x0\n"
    )
    body = body[:flag.end] + hook + body[flag.end:]
    return replace_method(source, method, body), flag


def self_managed_field(call_source: str, flag: Flag) -> str | None:
    """The field behind isSelfManaged(), for builds that inline the getter."""
    if flag.field:
        return flag.field
    body = call_method(call_source, flag.method, "()Z")
    if body:
        code = instructions(body)
        getter = re.fullmatch(rf"iget-boolean ({REG}), p0, {CALL_RE}->(\w+):Z", code[0]) \
            if len(code) == 2 else None
        if getter and code[1] == f"return {getter.group(1)}":
            return getter.group(2)
    return None


def display_name_read(root: Path, call_source: str):
    """Returns f(dest, call) -> smali that loads Call#getCallerDisplayName()."""
    getter = "getCallerDisplayName"
    if call_method(call_source, getter, "()Ljava/lang/String;") is None:
        # Renamed: the AIDL adapter still hands the name to Call's setter.
        adapters = list(root.glob(f"smali*/{PACKAGE_DIR}/ConnectionServiceWrapper$Adapter.smali"))
        if len(adapters) != 1:
            raise RuntimeError("ConnectionServiceWrapper$Adapter not found")
        entry = next((m for m in methods(adapters[0].read_text(encoding="utf-8"))
                      if " setCallerDisplayName(" in m.group("header")), None)
        setter = entry and re.search(
            rf"{CALL_RE}->(\w+)\((Ljava/lang/String;I|ILjava/lang/String;)\)V", entry.group("body"))
        if not setter:
            raise RuntimeError("Call#setCallerDisplayName() not found")
        body = call_method(call_source, setter.group(1), f"({setter.group(2)})V")
        fields = set(re.findall(
            rf"iput-object {REG}, p0, {CALL_RE}->(\w+):Ljava/lang/String;", body or ""))
        if len(fields) != 1:
            raise RuntimeError("caller display name field not found")
        field = fields.pop()
        getter = None
        for method in methods(call_source):
            name = re.search(r"\spublic (?:final )?(\w+)\(\)Ljava/lang/String;$",
                             method.group("header"))
            code = instructions(method.group("body"))
            if name and code == [
                    f"iget-object p0, p0, {CALL}->{field}:Ljava/lang/String;", "return-object p0"]:
                getter = name.group(1)
                break
        if not getter:
            raise RuntimeError("Call#getCallerDisplayName() not found")
    target = f"{CALL}->{getter}()Ljava/lang/String;"
    return lambda dest, call: (invoke("virtual", call, target), f"move-result-object {dest}")


def guarded(indent: str, label: str, *lines: str) -> str:
    """The lines, skipped as a whole if they throw: a framework.jar without the
    backend class must not take Telecom down."""
    code = [f":vs_voip_{label}_start", *lines, f":vs_voip_{label}_end",
            f".catch Ljava/lang/Throwable; {{:vs_voip_{label}_start .. :vs_voip_{label}_end}} "
            f":vs_voip_{label}_done",
            f":vs_voip_{label}_done"]
    return "".join(f"{indent}{line}\n\n" for line in code)


def patch_caller(source: str, flag: Flag, field: str | None, read_name) -> str:
    """handle = isSelfManaged() ? VoipCaller.tag(name, handle) : handle"""
    for method in methods(source):
        header, body = method.group("header"), method.group("body")
        for match in CALL_FLAG.finditer(body):
            check = Flag(match)
            if not ((check.method and check.method == flag.method)
                    or (check.field and check.field == field)):
                continue
            branch = NEXT_BRANCH.match(body, check.end)
            if not branch or branch.group("op") != "if-nez" or branch.group("reg") != check.reg:
                continue
            # Self-managed calls branch over the code that reads the handle.
            handle = re.compile(
                rf"{re.escape(SSP)}{GAP}[ \t]*move-result-object (?P<reg>{REG})\n"
            ).search(body, branch.end())
            if not handle:
                continue
            # Where the handle is read the compiler may already have reused the
            # Call register, so fetch the name at the check into our own local
            # and let the stock code run as for a managed call.
            grown, name = add_local(header, body)
            grown, self_managed = add_local(header, grown)
            shift = len(grown) - len(body)
            reg, indent, dest = check.reg, check.indent, handle.group("reg")
            tag = (
                f"\n{indent}{MARKER}: mark the caller of a self-managed call\n"
                f"{indent}if-eqz {self_managed}, :vs_voip_tag_done\n\n"
                + guarded(indent, "tag",
                          f"invoke-static {{{name}, {dest}}}, {TAG}",
                          f"move-result-object {dest}").rstrip("\n") + "\n"
            )
            fetch = (
                f"\n{indent}{MARKER}: report the caller of self-managed calls\n"
                f"{indent}if-eqz {reg}, :vs_voip_managed\n\n"
                + "".join(f"{indent}{line}\n\n" for line in read_name(name, check.call))
                + f"{indent}const/4 {self_managed}, 0x1\n\n"
                f"{indent}const/16 {reg}, 0x0\n\n"
                f"{indent}:vs_voip_managed\n"
            )
            for at, text in ((handle.end(), tag), (check.end, fetch)):
                grown = grown[:at + shift] + text + grown[at + shift:]
            entry = first_instruction(grown)
            init = f"{indent}const/4 {name}, 0x0\n\n{indent}const/4 {self_managed}, 0x0\n\n"
            grown = grown[:entry] + init + grown[entry:]
            return replace_method(source, method, grown)
    raise RuntimeError("phone state broadcast has no isSelfManaged() check")


def patch_notify(source: str) -> str:
    """VoipCaller.notifyState(registry, state, handle), the stock call if it throws"""
    site = re.compile(
        rf"(?m)^(?P<indent>[ \t]*)invoke-virtual \{{(?P<registry>{REG}), (?P<state>{REG}), "
        rf"(?P<handle>{REG})\}}, " + re.escape(NOTIFY) + r"\n"
    )

    def hook(match: re.Match) -> str:
        indent, label = match.group("indent"), f"vs_voip_notify_{match.start()}"
        registers = [match.group(name) for name in ("registry", "state", "handle")]
        # Parameter registers of a large frame do not fit the 4-bit form.
        if any(reg[0] == "v" and int(reg[1:]) > 15 for reg in registers):
            raise RuntimeError("phone state notify call uses registers above v15")
        lines = [
            f"{MARKER}: name the caller from its call notification",
            f":{label}_start",
            f"invoke-static {{{', '.join(registers)}}}, {NOTIFY_STATE}",
            f":{label}_end",
            # A framework.jar without the backend class must not take Telecom
            # down, nor leave the phone state unsent.
            f".catch Ljava/lang/Throwable; {{:{label}_start .. :{label}_end}} :{label}_stock",
            f"goto :{label}_done",
            f":{label}_stock",
            match.group(0).strip(),
            f":{label}_done",
        ]
        return "".join(f"{indent}{line}\n\n" for line in lines)

    patched, count = site.subn(hook, source)
    if not count:
        raise RuntimeError("phone state notify call not found")
    return patched


def only(paths: list[Path], what: str) -> Path:
    if len(paths) != 1:
        raise RuntimeError(f"expected one {what}, found {len(paths)}")
    return paths[0]


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <decoded-apk-dir>", file=sys.stderr)
        return 2

    root = Path(sys.argv[1])
    sources = {path: path.read_text(encoding="utf-8")
               for path in root.glob(f"smali*/{PACKAGE_DIR}/*.smali")}
    if any(MARKER in source for source in sources.values()):
        print("[telecom_voip_bridge] already patched")
        return 0
    try:
        call = only([p for p in sources if p.name == "Call.smali"], "Call class")
        end_call = only([p for p, s in sources.items() if END_CALL_ANCHOR in s],
                        "endCall class")
        broadcaster = only([p for p, s in sources.items() if NOTIFY in s and SSP in s],
                           "phone state broadcaster class")
        patched_end_call, flag = patch_end_call(sources[end_call])
        patched_broadcaster = patch_notify(patch_caller(
            sources[broadcaster], flag, self_managed_field(sources[call], flag),
            display_name_read(root, sources[call])))
    except RuntimeError as error:
        print(f"[telecom_voip_bridge] {error}", file=sys.stderr)
        return 1

    # Both or neither: a half-patched Telecom rings the watch but cannot reject.
    end_call.write_text(patched_end_call, encoding="utf-8")
    broadcaster.write_text(patched_broadcaster, encoding="utf-8")
    print(f"[telecom_voip_bridge] patched: {end_call}")
    print(f"[telecom_voip_bridge] patched: {broadcaster}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
