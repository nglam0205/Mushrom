#!/usr/bin/env python3
"""Optional stacked dual-SIM signal icon in the status bar.

Usage: patch_stacked_mobile.py <decoded MiuiSystemUI dir>

HyperOS 4 ships the AOSP stacked mobile icon (both SIMs' bars in one slot,
"stacked_mobile") and already binds it into every status icon container, but
MobileIconsInteractorImpl.isStackable was folded to flowOf(false) because the
NewStatusBarIcons flag is off, so the slot never shows.

With vs_stacked_mobile on:
- MobileIconsViewModel.isStackable becomes "all mobile icons visible && switch"
  instead of "... && interactor.isStackable". The stacked view model and the
  MobileUiAdapter both read it.
- MobileUiAdapter only hands the icon controller an empty sub list (dropping the
  two separate icons) when there are exactly two subs, so a single SIM keeps
  its normal icon.
- The slot list gets "stacked_mobile" right before "mobile"; MIUI's own
  config_statusBarIcons lacks it, so it would otherwise land at the far left of
  the icon row. Harmless with the switch off: the slot stays hidden.
- The stacked icon never draws its "!" cutout. AOSP sets it whenever the
  primary SIM is not the validated default network (e.g. on Wi-Fi), where
  MIUI's own icons show nothing; the cutout also hides the top bars.
- The bar count is numberOfLevels, not numberOfLevels - 1. MIUI reports
  numLevels=4 with levels 0..4 (4 = all four bars), so AOSP's "- 1" drew three
  bars where MIUI's icon shows four.
- No 4G/5G on the stacked icon: its network type source always gives null, so
  only the data activity arrows sit next to the bars. (AOSP's mapping also
  showed 5G NSA as 4G where MIUI shows 5G.)
- The data arrows follow the default network instead of data activity: AOSP
  only composed them while data was flowing, so the icon width (and the icons
  beside it) jumped. Now they stay (dimmed when idle) while mobile is the
  default network and go away on Wi-Fi. StackedMobileActivity combines the
  primary SIM's mobileIsDefault into activityContainerVisible.

The view model only re-reads the switch when its inputs change, so VSTeam
restarts SystemUI after toggling it.
"""
import re
import sys
from pathlib import Path


TOOLBOX = "Lcom/android/vsteam/Toolbox;"
SWITCH = "vs_stacked_mobile"
MOBILE_UI = "com/android/systemui/statusbar/pipeline/mobile/ui"
SLOTS_FACTORY = (
    "com/android/systemui/statusbar/dagger/"
    "CentralSurfacesDependenciesModule_ProvideStatusBarIconListFactory.smali"
)
HELPER_CLASS = "com/android/systemui/vsteam/StackedMobileSlots"
HELPER = "Lcom/android/systemui/vsteam/StackedMobileSlots;"
METHOD = r"(?ms)^(?P<header>\.method [^\n]*{name}\n)(?P<body>.*?)^\.end method$"

HELPER_SMALI = f"""\
.class public final L{HELPER_CLASS};
.super Ljava/lang/Object;


# Puts "stacked_mobile" in front of "mobile" in the status bar slot order.
.method public static insert([Ljava/lang/String;)[Ljava/lang/String;
    .registers 6

    if-nez p0, :cond_3

    return-object p0

    :cond_3
    const/4 v0, 0x0

    const/4 v1, -0x1

    move v2, v0

    :goto_7
    array-length v3, p0

    const-string v4, "stacked_mobile"

    if-ge v2, v3, :cond_25

    aget-object v3, p0, v2

    invoke-virtual {{v4, v3}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v3

    if-eqz v3, :cond_15

    return-object p0

    :cond_15
    if-gez v1, :cond_22

    const-string v3, "mobile"

    aget-object v4, p0, v2

    invoke-virtual {{v3, v4}}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v3

    if-eqz v3, :cond_22

    move v1, v2

    :cond_22
    add-int/lit8 v2, v2, 0x1

    goto :goto_7

    :cond_25
    if-gez v1, :cond_28

    return-object p0

    :cond_28
    array-length v2, p0

    add-int/lit8 v2, v2, 0x1

    new-array v2, v2, [Ljava/lang/String;

    invoke-static {{p0, v0, v2, v0, v1}}, Ljava/lang/System;->arraycopy(Ljava/lang/Object;ILjava/lang/Object;II)V

    const-string v4, "stacked_mobile"

    aput-object v4, v2, v1

    add-int/lit8 v0, v1, 0x1

    array-length v3, p0

    sub-int/2addr v3, v1

    invoke-static {{p0, v1, v2, v0, v3}}, Ljava/lang/System;->arraycopy(Ljava/lang/Object;ILjava/lang/Object;II)V

    return-object v2
.end method
"""


def read(path):
    return path.read_text(encoding="utf-8", errors="surrogateescape")


def write(path, text):
    path.write_text(text, encoding="utf-8", errors="surrogateescape")


def find_one(root, relative):
    targets = list(root.glob(f"smali*/{relative}"))
    return targets[0] if len(targets) == 1 else None


def patch_method(path, name, marker, transform, label, done=None):
    text = read(path)
    match = re.search(METHOD.format(name=re.escape(name)), text)
    if not match:
        print(f"[stacked] method not found: {label}", file=sys.stderr)
        return False
    body = match.group("body")
    # A rebuilt APK loses smali comments, so markers that are only comments
    # need a `done` pattern matching the hook itself.
    if marker in body or (done is not None and done.search(body)):
        print(f"[stacked] already hooked: {label}")
        return True
    patched = transform(body)
    if patched is None or patched == body:
        print(f"[stacked] could not hook {label}", file=sys.stderr)
        return False
    write(path, text[: match.start("body")] + patched + text[match.end("body"):])
    print(f"[stacked] hooked {label}")
    return True


def switch_check(reg):
    return (
        f'    const-string {reg}, "{SWITCH}"\n\n'
        f"    invoke-static {{{reg}}}, {TOOLBOX}->getBooleanGlobal(Ljava/lang/String;)Z\n\n"
        f"    move-result {reg}\n\n"
    )


def hook_view_model(body):
    # Z$0 = iconsAreAllVisible, Z$1 = interactor.isStackable (always false):
    #   if-eqz v0, :cond_0 / if-eqz v1, :cond_0
    pattern = re.compile(
        r"(if-eqz (?P<a>[vp]\d+), (?P<label>:cond_\d+)\n)"
        r"\s*if-eqz (?P<b>[vp]\d+), (?P=label)\n"
    )
    match = pattern.search(body)
    if not match:
        return None
    reg = match.group("b")
    replacement = (
        match.group(1)
        + "\n"
        + switch_check(reg)
        + f"    if-eqz {reg}, {match.group('label')}\n"
    )
    return body[: match.start()] + replacement + body[match.end():]


ADAPTER_DONE = re.compile(r"Ljava/util/List;->size\(\)I\n\s*\n?\s*move-result ([vp]\d+)\n\s*\n?\s*xor-int/lit8 \1, \1, 0x2\n")


def hook_adapter(body):
    # The stacked branch: Pair(subIds: List, isStackable: Boolean). Keep it to
    # exactly two subs; otherwise fall through to the separate icons.
    pattern = re.compile(
        r"(?P<check>if-eqz (?P<flag>[vp]\d+), (?P<label>:cond_\d+)\n)"
        r"(?P<rest>\s*\n?\s*iget-object [vp]\d+, [vp]\d+, L[^;]+/MobileUiAdapter;->iconController:)"
    )
    match = pattern.search(body)
    if not match:
        return None
    lists = re.findall(r"check-cast ([vp]\d+), Ljava/util/List;", body[: match.start()])
    if not lists:
        return None
    sub_ids, flag, label = lists[0], match.group("flag"), match.group("label")
    hook = (
        f"{match.group('check')}\n"
        f"    # {SWITCH}: stack only two SIMs\n"
        f"    invoke-interface {{{sub_ids}}}, Ljava/util/List;->size()I\n\n"
        f"    move-result {flag}\n\n"
        f"    xor-int/lit8 {flag}, {flag}, 0x2\n\n"
        f"    if-nez {flag}, {label}\n"
    )
    return body[: match.start()] + hook + match.group("rest") + body[match.end():]


def hook_slots(body):
    pattern = re.compile(
        r"(Landroid/content/res/Resources;->getStringArray\(I\)\[Ljava/lang/String;\n"
        r"\s*\n?\s*move-result-object (?P<reg>[vp]\d+)\n)"
    )
    match = pattern.search(body)
    if not match:
        return None
    reg = match.group("reg")
    hook = (
        f"\n    invoke-static {{{reg}}}, {HELPER}->insert([Ljava/lang/String;)[Ljava/lang/String;\n\n"
        f"    move-result-object {reg}\n"
    )
    return body[: match.end()] + hook + body[match.end():]


EXCLAMATION = re.compile(
    r"iget-boolean (?P<reg>[vp]\d+), [vp]\d+, "
    r"Lcom/android/systemui/statusbar/pipeline/mobile/domain/model/"
    r"SignalIconModel\$CellularTypeIconModel\$Cellular;->showExclamationMark:Z\n"
)


def hide_exclamation(root):
    hooked = 0
    for path in root.glob("smali*/com/android/systemui/statusbar/pipeline/shared/ui/composable/StackedMobileIconKt*.smali"):
        text = read(path)
        patched = EXCLAMATION.sub(
            lambda m: f"const/4 {m.group('reg')}, 0x0  # {SWITCH}: no \"!\" cutout\n", text)
        if patched != text:
            write(path, patched)
            hooked += 1
        elif f"{SWITCH}: no" in text or "showExclamationMark" not in text:
            hooked += 1
    print(f"[stacked] {'hid' if hooked else 'could not find'} the stacked \"!\" cutout")
    return hooked > 0


BAR_COUNT = re.compile(
    r"(?P<keep>invoke-static \{(?P<reg>[vp]\d+), [vp]\d+\}, Ljava/lang/Math;->max\(II\)I\n"
    r"\s*\n?\s*move-result (?P=reg)\n)"
    r"\s*\n?\s*sub-int/2addr (?P=reg), [vp]\d+\n"
)


BAR_COUNT_DONE = re.compile(
    r"Cellular;->numberOfLevels:I\n\s*\n?\s*invoke-static \{([vp]\d+), [vp]\d+\}, Ljava/lang/Math;->max\(II\)I\n"
    r"\s*\n?\s*move-result \1\n(?!\s*\n?\s*sub-int/2addr \1)"
)


def fix_bar_count(root):
    for path in root.glob("smali*/com/android/systemui/statusbar/pipeline/shared/ui/composable/StackedMobileIconKt.smali"):
        text = read(path)
        if f"{SWITCH}: MIUI bar count" in text or BAR_COUNT_DONE.search(text):
            print("[stacked] already hooked: stacked bar count")
            return True
        start = text.find(".method public static final StackedMobileIcon-")
        end = text.find(".end method", start)
        if start < 0 or end < 0:
            break
        body = text[start:end]
        if "DualSim;->primary" not in body or "->numberOfLevels:I" not in body:
            break
        patched, count = BAR_COUNT.subn(
            lambda m: m.group("keep") + f"\n    # {SWITCH}: MIUI bar count = numberOfLevels\n", body, count=1)
        if count:
            write(path, text[:start] + patched + text[end:])
            print("[stacked] hooked stacked bar count")
            return True
    print("[stacked] could not fix the stacked bar count", file=sys.stderr)
    return False


def hide_type(root):
    # firstOrNull(viewModels)?.networkTypeIcon ?: flowOf(null): always take the
    # flowOf(null) branch.
    pattern = re.compile(
        r"(check-cast (?P<vm>[vp]\d+), L[^;]+/MobileIconViewModelCommon;\n\s*\n?\s*)"
        r"if-eqz (?P=vm), (?P<label>:cond_\d+)\n"
    )
    for path in root.glob(f"smali*/{MOBILE_UI}/viewmodel/StackedMobileIconViewModelImpl$*.smali"):
        text = read(path)
        if "MobileIconViewModelCommon;->getNetworkTypeIcon()" not in text or "firstOrNull" not in text:
            continue
        if f"{SWITCH}: no 4G/5G" in text or re.search(
                r"MobileIconViewModelCommon;\n\s*\n?\s*goto :cond_\d+\n", text):
            print("[stacked] already hooked: stacked 4G/5G hidden")
            return True
        patched, count = pattern.subn(
            lambda m: m.group(1) + f"# {SWITCH}: no 4G/5G on the stacked icon\n    goto {m.group('label')}\n",
            text, count=1)
        if count:
            write(path, patched)
            print("[stacked] hid the stacked 4G/5G")
            return True
    print("[stacked] could not hide the stacked 4G/5G", file=sys.stderr)
    return False


ACTIVITY_CLASS = "com/android/systemui/vsteam/StackedMobileActivity"
ACTIVITY_HELPER = "Lcom/android/systemui/vsteam/StackedMobileActivity;"
# Built from Java (javac + d8 + baksmali) against stubs of the SystemUI classes.
ACTIVITY_SMALI = """\
.class public final Lcom/android/systemui/vsteam/StackedMobileActivity;
.super Ljava/lang/Object;
# interfaces
.implements Lkotlin/jvm/functions/Function3;
# static fields
.field private static volatile model:Lcom/android/systemui/statusbar/pipeline/mobile/ui/viewmodel/MobileIconsViewModel;
# direct methods
.method public constructor <init>()V
    .registers 1
    invoke-direct {p0}, Ljava/lang/Object;-><init>()V
    return-void
.end method
.method public static containerVisible(Lcom/android/systemui/statusbar/pipeline/mobile/ui/viewmodel/MobileIconViewModelCommon;Lkotlinx/coroutines/flow/Flow;)Lkotlinx/coroutines/flow/Flow;
    .registers 4
    sget-object v0, Lcom/android/systemui/vsteam/StackedMobileActivity;->model:Lcom/android/systemui/statusbar/pipeline/mobile/ui/viewmodel/MobileIconsViewModel;
    if-eqz v0, :cond_2c
    if-eqz p0, :cond_2c
    if-eqz p1, :cond_2c
    iget-object v1, v0, Lcom/android/systemui/statusbar/pipeline/mobile/ui/viewmodel/MobileIconsViewModel;->interactor:Lcom/android/systemui/statusbar/pipeline/mobile/domain/interactor/MobileIconsInteractor;
    if-nez v1, :cond_d
    goto :goto_2c
    :cond_d
    iget-object v0, v0, Lcom/android/systemui/statusbar/pipeline/mobile/ui/viewmodel/MobileIconsViewModel;->interactor:Lcom/android/systemui/statusbar/pipeline/mobile/domain/interactor/MobileIconsInteractor;
    invoke-interface {p0}, Lcom/android/systemui/statusbar/pipeline/mobile/ui/viewmodel/MobileIconViewModelCommon;->getSubscriptionId()I
    move-result p0
    invoke-interface {v0, p0}, Lcom/android/systemui/statusbar/pipeline/mobile/domain/interactor/MobileIconsInteractor;->getMobileConnectionInteractorForSubId(I)Lcom/android/systemui/statusbar/pipeline/mobile/domain/interactor/MobileIconInteractor;
    move-result-object p0
    if-nez p0, :cond_1b
    const/4 p0, 0x0
    goto :goto_1f
    :cond_1b
    invoke-interface {p0}, Lcom/android/systemui/statusbar/pipeline/mobile/domain/interactor/MobileIconInteractor;->getMobileIsDefault()Lkotlinx/coroutines/flow/Flow;
    move-result-object p0
    :goto_1f
    if-nez p0, :cond_22
    return-object p1
    :cond_22
    new-instance v0, Lcom/android/systemui/vsteam/StackedMobileActivity;
    invoke-direct {v0}, Lcom/android/systemui/vsteam/StackedMobileActivity;-><init>()V
    invoke-static {p1, p0, v0}, Lkotlinx/coroutines/flow/FlowKt;->flowCombine(Lkotlinx/coroutines/flow/Flow;Lkotlinx/coroutines/flow/Flow;Lkotlin/jvm/functions/Function3;)Lkotlinx/coroutines/flow/FlowKt__ZipKt$combine$$inlined$unsafeFlow$1;
    move-result-object p0
    return-object p0
    :cond_2c
    :goto_2c
    return-object p1
.end method
.method public static register(Lcom/android/systemui/statusbar/pipeline/mobile/ui/viewmodel/MobileIconsViewModel;)V
    .registers 1
    sput-object p0, Lcom/android/systemui/vsteam/StackedMobileActivity;->model:Lcom/android/systemui/statusbar/pipeline/mobile/ui/viewmodel/MobileIconsViewModel;
    return-void
.end method
# virtual methods
.method public invoke(Ljava/lang/Object;Ljava/lang/Object;Ljava/lang/Object;)Ljava/lang/Object;
    .registers 4
    sget-object p1, Ljava/lang/Boolean;->TRUE:Ljava/lang/Boolean;
    invoke-virtual {p1, p2}, Ljava/lang/Boolean;->equals(Ljava/lang/Object;)Z
    move-result p1
    invoke-static {p1}, Ljava/lang/Boolean;->valueOf(Z)Ljava/lang/Boolean;
    move-result-object p1
    return-object p1
.end method
"""


def hook_activity_source(body):
    pattern = re.compile(
        r"(?P<call>invoke-interface \{(?P<vm>[vp]\d+)\}, L[^;]+/MobileIconViewModelCommon;->getActivityContainerVisible\(\)Lkotlinx/coroutines/flow/Flow;\n)"
        r"(?P<gap>\s*\n?\s*)move-result-object (?P=vm)\n"
    )
    match = pattern.search(body)
    lists = re.findall(r"check-cast ([vp]\d+), Ljava/util/List;", body)
    if not match or not lists:
        return None
    vm, keep = match.group("vm"), lists[0]
    return body[: match.start()] + (
        f"move-object {keep}, {vm}\n\n"
        f"    {match.group('call')}"
        f"{match.group('gap')}move-result-object {vm}\n\n"
        f"    # {SWITCH}: arrows follow mobileIsDefault\n"
        f"    invoke-static {{{keep}, {vm}}}, {ACTIVITY_HELPER}->containerVisible(L{MOBILE_UI}/viewmodel/MobileIconViewModelCommon;Lkotlinx/coroutines/flow/Flow;)Lkotlinx/coroutines/flow/Flow;\n\n"
        f"    move-result-object {vm}\n"
    ) + body[match.end():]


def activity_follows_default(root):
    model = find_one(root, f"{MOBILE_UI}/viewmodel/MobileIconsViewModel.smali")
    source = None
    for path in root.glob(f"smali*/{MOBILE_UI}/viewmodel/StackedMobileIconViewModelImpl$*.smali"):
        text = read(path)
        if "MobileIconViewModelCommon;->getActivityContainerVisible()" in text and "firstOrNull" in text:
            source = path
            break
    if not model or not source:
        print("[stacked] stacked activity source not found; arrows stay AOSP", file=sys.stderr)
        return False

    text = read(model)
    match = re.search(r"(?ms)^\.method public constructor <init>\([^\n]*\n(?P<body>.*?)^\.end method$", text)
    if not match:
        print("[stacked] MobileIconsViewModel constructor not found", file=sys.stderr)
        return False
    body = match.group("body")
    if ACTIVITY_HELPER not in body:
        index = body.rfind("    return-void")
        if index < 0:
            print("[stacked] could not hook MobileIconsViewModel registry", file=sys.stderr)
            return False
        body = (body[:index]
                + f"    invoke-static {{p0}}, {ACTIVITY_HELPER}->register(L{MOBILE_UI}/viewmodel/MobileIconsViewModel;)V\n\n"
                + body[index:])
        write(model, text[: match.start("body")] + body + text[match.end("body"):])
        print("[stacked] hooked MobileIconsViewModel registry")

    if not patch_method(source, "invokeSuspend(Ljava/lang/Object;)Ljava/lang/Object;",
                        ACTIVITY_HELPER, hook_activity_source, "stacked activity source"):
        return False
    helper = model.parents[len(Path(f"{MOBILE_UI}/viewmodel/x").parts) - 1] / f"{ACTIVITY_CLASS}.smali"
    helper.parent.mkdir(parents=True, exist_ok=True)
    write(helper, ACTIVITY_SMALI)
    return True


def main():
    if len(sys.argv) != 2 or not Path(sys.argv[1]).is_dir():
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    root = Path(sys.argv[1])

    view_model = find_one(root, f"{MOBILE_UI}/viewmodel/MobileIconsViewModel$isStackable$1.smali")
    adapter = find_one(root, f"{MOBILE_UI}/MobileUiAdapter$start$1$4.smali")
    factory = find_one(root, SLOTS_FACTORY)
    stacked = list(root.glob(f"smali*/{MOBILE_UI}/StackedMobileBindableIcon.smali"))
    if not (view_model and adapter and factory and stacked):
        print("[stacked] stacked mobile icon not present in this build; skip")
        return 1

    ok = patch_method(view_model, "invokeSuspend(Ljava/lang/Object;)Ljava/lang/Object;",
                      SWITCH, hook_view_model, "MobileIconsViewModel.isStackable")
    ok = ok and patch_method(adapter, "invokeSuspend(Ljava/lang/Object;)Ljava/lang/Object;",
                             SWITCH, hook_adapter, "MobileUiAdapter sub ids", ADAPTER_DONE)
    ok = ok and patch_method(factory, "provideStatusBarIconList(Landroid/content/Context;)"
                             "Lcom/android/systemui/statusbar/phone/ui/StatusBarIconList;",
                             HELPER, hook_slots, "StatusBarIconList slot order")
    if not ok:
        return 1
    hide_exclamation(root)
    fix_bar_count(root)
    hide_type(root)
    activity_follows_default(root)

    helper = factory.parents[len(Path(SLOTS_FACTORY).parts) - 1] / f"{HELPER_CLASS}.smali"
    helper.parent.mkdir(parents=True, exist_ok=True)
    write(helper, HELPER_SMALI)
    return 0


if __name__ == "__main__":
    sys.exit(main())
