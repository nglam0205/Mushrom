#!/usr/bin/env python3
"""Route B generate-side hook.

Injects a call to com.android.vsteam.Toolbox.interceptChallenge into
AndroidKeyStoreKeyPairGeneratorSpi.addAttestationParameters, right where the Spi
reads mSpec.getAttestationChallenge(). When the vs_synth_attestation switch is on,
interceptChallenge records the key's attestation parameters and returns null, so the
challenge never reaches KeyMint: the TEE then generates a plain key that always
succeeds even on RKP-only devices whose attestation key pool cannot be provisioned
(KeyMint -74). The attestation leaf is synthesized from the keybox later, when the
app reads the key back (Toolbox.modifyOrSynthLeaf via the onGetKeyEntry hook).

The Spi instance (p0) is passed to interceptChallenge, which recovers the spec from
its mSpec field, so the injection needs no scratch register: it only reuses p0 and
the challenge register.

Usage: patch_keystore_synth.py <framework.jar.out root>
Exit 0 on success (or already patched); non-zero if the target could not be patched.
"""

import os
import re
import sys

CLASS_BASENAME = "AndroidKeyStoreKeyPairGeneratorSpi.smali"
# Match on the method SIGNATURE, not the exact modifiers: apktool-vs injects hidden-API
# keywords (blacklist / whitelist / greylist / test-api) between the access flags and the
# name, e.g. ".method private blacklist addAttestationParameters(Ljava/util/List;)V".
METHOD_SIG = "addAttestationParameters(Ljava/util/List;)V"
CHALLENGE_CALL = "->getAttestationChallenge()[B"
INTERCEPT = "Lcom/android/vsteam/Toolbox;->interceptChallenge(Ljava/lang/Object;[B)[B"
MOVE_RE = re.compile(r"^\s*move-result-object\s+(v\d+)\s*$")

GENKP_SIG = "generateKeyPair()Ljava/security/KeyPair;"
GENERATE_CALL = "->generateKey("
BYPASS_MARKER = "Lcom/android/vsteam/Toolbox;->isAttestNotProvisioned("
CATCH_RE = re.compile(
    r"^\s*\.catch\s+Landroid/security/KeyStoreException;\s*"
    r"\{:(\w+)\s*\.\.\s*:(\w+)\}\s*:(\w+)\s*$")
MOVE_EXC_RE = re.compile(r"^\s*move-exception\s+(v\d+)\s*$")
LOCALS_RE = re.compile(r"^(\s*)\.locals\s+(\d+)\s*$")
SPI_TYPE = "Landroid/security/keystore2/AndroidKeyStoreKeyPairGeneratorSpi;"


def find_smali(root):
    for base, _dirs, files in os.walk(root):
        if CLASS_BASENAME in files:
            return os.path.join(base, CLASS_BASENAME)
    return None


def method_def_index(lines, sig):
    """Index of the `.method ... <sig>` definition line, ignoring hidden-API keywords."""
    for i, ln in enumerate(lines):
        stripped = ln.lstrip()
        if stripped.startswith(".method ") and sig in ln:
            return i
    return None


def patch(path):
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    if any(INTERCEPT in ln for ln in lines):
        print("patch_keystore_synth: already patched")
        return True

    # Locate addAttestationParameters.
    start = method_def_index(lines, METHOD_SIG)
    if start is None:
        print("patch_keystore_synth: addAttestationParameters not found", file=sys.stderr)
        return False
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].strip() == ".end method"), len(lines))

    # First getAttestationChallenge() inside the method, then its move-result-object.
    chal = next((i for i in range(start, end) if CHALLENGE_CALL in lines[i]), None)
    if chal is None:
        print("patch_keystore_synth: getAttestationChallenge not found in method", file=sys.stderr)
        return False

    move_idx = None
    reg = None
    for i in range(chal + 1, end):
        m = MOVE_RE.match(lines[i])
        if m:
            move_idx, reg = i, m.group(1)
            break
    if move_idx is None:
        print("patch_keystore_synth: move-result-object after challenge not found", file=sys.stderr)
        return False

    indent = lines[move_idx][:len(lines[move_idx]) - len(lines[move_idx].lstrip())]
    injection = [
        "\n",
        "%sinvoke-static {p0, %s}, %s\n" % (indent, reg, INTERCEPT),
        "\n",
        "%smove-result-object %s\n" % (indent, reg),
    ]
    lines[move_idx + 1:move_idx + 1] = injection

    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    print("patch_keystore_synth: injected interceptChallenge on %s (%s)" % (reg, os.path.basename(path)))
    return True


def method_bounds(lines, sig):
    start = method_def_index(lines, sig)
    if start is None:
        return None, None
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].strip() == ".end method"), len(lines))
    return start, end


def patch_generate_retry(path):
    """Inject the auto fallback into generateKeyPair's KeyStoreException catch.

    On a provisioning failure (KeyMint -74) it records the key's parameters, re-runs
    generateKeyPair with the challenge stripped (so the TEE produces a plain key that
    succeeds), and returns that KeyPair; the attestation leaf is synthesized at read time.
    """
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    if any(BYPASS_MARKER in ln for ln in lines):
        print("patch_keystore_synth: generate retry already patched")
        return True

    start, end = method_bounds(lines, GENKP_SIG)
    if start is None:
        print("patch_keystore_synth: generateKeyPair not found", file=sys.stderr)
        return False

    # Pick the KeyStoreException catch whose try body contains generateKey().
    catch_label = None
    for i in range(start, end):
        m = CATCH_RE.match(lines[i])
        if not m:
            continue
        try_start_lbl, try_end_lbl, lbl = m.group(1), m.group(2), m.group(3)
        ts = next((j for j in range(start, end)
                   if lines[j].strip() == ":" + try_start_lbl), None)
        te = next((j for j in range(start, end)
                   if lines[j].strip() == ":" + try_end_lbl), None)
        if ts is not None and te is not None and any(
                GENERATE_CALL in lines[j] for j in range(ts, te)):
            catch_label = lbl
            break
    if catch_label is None:
        print("patch_keystore_synth: generateKey KeyStoreException catch not found", file=sys.stderr)
        return False

    lbl_idx = next((i for i in range(start, end)
                    if lines[i].strip() == ":" + catch_label), None)
    if lbl_idx is None:
        print("patch_keystore_synth: catch label :%s not found" % catch_label, file=sys.stderr)
        return False
    move_idx = None
    exc = None
    for i in range(lbl_idx + 1, end):
        m = MOVE_EXC_RE.match(lines[i])
        if m:
            move_idx, exc = i, m.group(1)
            break
        if lines[i].strip():
            break
    if move_idx is None:
        print("patch_keystore_synth: move-exception after :%s not found" % catch_label, file=sys.stderr)
        return False

    # Allocate a fresh scratch register by growing .locals, instead of clobbering v1/v2/v9:
    # those carry live values of other types elsewhere in generateKeyPair (e.g. a String in
    # v2), so reusing them made the verifier reject the class and killed zygote (bootloop).
    # The new register (vS = old locals count) is referenced nowhere else, so it cannot
    # collide, and the exception register is left intact for the original handler's
    # fall-through. The method must declare .locals (baksmali's norm); .registers would
    # renumber params, so we bail there rather than risk another brick.
    loc_idx = next((i for i in range(start, end) if LOCALS_RE.match(lines[i])), None)
    if loc_idx is None:
        print("patch_keystore_synth: .locals not found in generateKeyPair "
              "(.registers unsupported); skipping retry hook", file=sys.stderr)
        return False
    loc_m = LOCALS_RE.match(lines[loc_idx])
    locals_n = int(loc_m.group(2))
    scratch = locals_n  # first register index freed up by the bump below
    lines[loc_idx] = "%s.locals %d\n" % (loc_m.group(1), locals_n + 1)
    # The injection point shifted by zero lines (we only rewrote one line in place), but it
    # precedes move_idx, so move_idx is still valid.

    ind = "    "
    sr = "v%d" % scratch
    orig = ":vs_rb_orig"
    # /range forms are used for every invoke that carries a register so the hook is correct
    # regardless of how high p0 / exc / the scratch register land.
    block = [
        "\n",
        "%sinvoke-static/range {%s .. %s}, Lcom/android/vsteam/Toolbox;->isAttestNotProvisioned(Ljava/lang/Object;)Z\n" % (ind, exc, exc),
        "%smove-result %s\n" % (ind, sr),
        "%sif-eqz %s, %s\n" % (ind, sr, orig),
        "%siget-object %s, p0, %s->mSpec:Landroid/security/keystore/KeyGenParameterSpec;\n" % (ind, sr, SPI_TYPE),
        "%sinvoke-static/range {%s .. %s}, Lcom/android/vsteam/Toolbox;->beginAttestBypass(Ljava/lang/Object;)Z\n" % (ind, sr, sr),
        "%smove-result %s\n" % (ind, sr),
        "%sif-eqz %s, %s\n" % (ind, sr, orig),
        "%sinvoke-virtual/range {p0 .. p0}, %s->generateKeyPair()Ljava/security/KeyPair;\n" % (ind, SPI_TYPE),
        "%smove-result-object %s\n" % (ind, sr),
        "%sinvoke-static {}, Lcom/android/vsteam/Toolbox;->endAttestBypass()V\n" % ind,
        "%sreturn-object %s\n" % (ind, sr),
        "%s%s\n" % (ind, orig),
        "%sinvoke-static {}, Lcom/android/vsteam/Toolbox;->endAttestBypass()V\n" % ind,
    ]
    lines[move_idx + 1:move_idx + 1] = block

    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    print("patch_keystore_synth: injected generate retry at :%s (exc=%s, scratch=%s, locals %d->%d)"
          % (catch_label, exc, sr, locals_n, locals_n + 1))
    return True


def main():
    if len(sys.argv) != 2:
        print("usage: patch_keystore_synth.py <framework.jar.out root>", file=sys.stderr)
        return 2
    root = sys.argv[1]
    path = find_smali(root)
    if path is None:
        print("patch_keystore_synth: %s not found under %s" % (CLASS_BASENAME, root), file=sys.stderr)
        return 1
    ok_challenge = patch(path)
    ok_retry = patch_generate_retry(path)
    return 0 if (ok_challenge and ok_retry) else 1


if __name__ == "__main__":
    sys.exit(main())
