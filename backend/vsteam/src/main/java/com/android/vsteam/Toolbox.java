package com.android.vsteam;

import static com.android.systemui.shared.recents.system.cts.OmniHelper.notifyToStartCtS;
import static com.android.vsteam.LunarCalendar.convertSolar2Lunar;

import android.annotation.SuppressLint;
import android.app.Application;
import android.content.ActivityNotFoundException;
import android.content.ComponentName;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.database.ContentObserver;
import android.graphics.Bitmap;
import android.graphics.drawable.BitmapDrawable;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.Icon;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.SystemClock;
import android.os.SystemProperties;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.net.Uri;
import android.provider.Settings;
import android.service.notification.StatusBarNotification;
import android.util.Log;
import android.util.Pair;
import android.view.KeyEvent;
import android.view.View;
import android.widget.Toast;

import com.miui.home.launcher.Launcher;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.Node;
import org.w3c.dom.NodeList;

import java.io.BufferedReader;
import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.FileReader;
import java.io.IOException;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.security.MessageDigest;
import java.security.PublicKey;
import java.security.cert.Certificate;
import java.security.cert.CertificateFactory;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

import javax.xml.parsers.DocumentBuilder;
import javax.xml.parsers.DocumentBuilderFactory;
import javax.xml.transform.OutputKeys;
import javax.xml.transform.Transformer;
import javax.xml.transform.TransformerFactory;
import javax.xml.transform.dom.DOMSource;
import javax.xml.transform.stream.StreamResult;

import miui.process.ProcessConfig;

public final class Toolbox {

    private static final String OPLUS_TOUCH_REPORT_RATE_KEY = "vs_touch_report_rate";

    public static final int ENABLE_SCREEN_BATTERY = 32;
    public static final float RATE_VAL_120 = 120.0f;
    public static final float RATE_VAL_144 = 144.0f;
    public static final float RATE_VAL_165 = 165.0f;
    public static final float RATE_VAL_30 = 30.0f;
    public static final float RATE_VAL_60 = 60.0f;
    public static final float RATE_VAL_72 = 72.0f;
    public static final float RATE_VAL_90 = 90.0f;
    public static final float RATE_VAL_ZERO = 0.0f;
    private static final String SPOOF_JSON_PREFIX = "vs_spoof_";
    private static ContentResolver sCR;
    private static Context sCon;
    private static volatile boolean skipspoof = false;
    private static volatile boolean skipspoofbyapp = false;

    // Package whose signature must ALWAYS be verified for real, even when the
    // vs_corepatch_damage switch is on (which otherwise lets unverified /
    // mismatched APKs install). This protects our own app from being
    // overwritten by a differently-signed or tampered build.
    private static final String PROTECTED_VERIFY_PKG = "com.bhlnk.vsteam";

    // Per-thread flag: is the APK currently being verified on this thread the
    // protected package? Stamped at the parse/verify entry points where the
    // APK path is known (markApkForVerify); read by the CorePatch* helpers,
    // which run at low-level sites that have no package context of their own.
    private static final ThreadLocal<Boolean> sVerifyProtected =
            new ThreadLocal<Boolean>() {
                @Override
                protected Boolean initialValue() {
                    return Boolean.FALSE;
                }
            };

    private static int getBatteryInfo(String type) {
        String filePath = "/sys/class/power_supply/battery/" + type;
        File file = new File(filePath);

        if (!file.exists()) {
            filePath = "/sys/class/qcom-battery/battery/" + type;
            file = new File(filePath);
        }

        try (FileReader fileReader = new FileReader(file);
             BufferedReader bufferedReader = new BufferedReader(fileReader)) {

            String readLine = bufferedReader.readLine();
            return Integer.parseInt(readLine.trim());

        } catch (FileNotFoundException e) {
            System.err.println("File not found: " + e.getMessage());
        } catch (IOException e) {
            System.err.println("IOException while reading file: " + e.getMessage());
        } catch (NumberFormatException e) {
            System.err.println("Error parsing number from file: " + e.getMessage());
        }

        return 0; // Return a default value in case of an error
    }

    public static boolean EnableSplashScreen() {
        return getBooleanGlobal("vs_splash_screen");
    }

    /**
     * Resolves the user-selected OPlus touch report-rate enum for the new 0xB6
     * protocol. A missing/invalid setting keeps the framework decision
     * unchanged. Game Space can use the non-simple strategy, but it still
     * writes the same report-rate node; do not discard the user's selection
     * solely because that strategy is active.
     */
    public static String resolveOplusTouchReportRate(
            Context context, String frameworkRate, boolean simpleRateProtocol) {
        if (context == null || frameworkRate == null) {
            return frameworkRate;
        }

        final int selectedRate;
        try {
            selectedRate = Settings.Global.getInt(
                    context.getContentResolver(), OPLUS_TOUCH_REPORT_RATE_KEY, -1);
        } catch (RuntimeException e) {
            Log.w("VSToolbox", "Unable to read OPlus touch report-rate setting", e);
            return frameworkRate;
        }

        // Normal ReportFps modes supported by the dynamic VSTools UI. Instant
        // touch-down modes 10, 11 and 16 are intentionally not persisted.
        if ((selectedRate >= 1 && selectedRate <= 9)
                || (selectedRate >= 12 && selectedRate <= 15)) {
            return Integer.toString(selectedRate);
        }
        return frameworkRate;
    }

    public static boolean GoogleAppDetail() {
        return getBooleanGlobal("vs_google_app_detail");
    }

    public static boolean DisableWallZoom() {
        return getBooleanGlobal("vs_dis_wall_zoom");
    }

    public static boolean GayStbA13() {
        return getBooleanGlobalZ("vs_grayscale");
    }

    public static float parseFloatFromString(String str) {
        return Float.parseFloat(str.replace(",", "."));
    }

    public static float get_custom_ani() {
        if (getBooleanGlobal("vs_use_custom_animation")) {
            String vl = getStrGlobal("vs_use_custom_animation_value");
            if (vl != null && !vl.isEmpty()) {
                try {
                    return parseFloatFromString(vl);
                } catch (Exception e) {
                    vslog("get_custom_ani Error");
                    return 1.0f;
                }
            }
        }
        return 1.0f;
    }

    // Layout of the float[] every app hands to View.setMiGlass (HyperOS
    // "bionic material" / liquid glass), as filled by BionicsToken.
    private static final int MIGLASS_PARAM_COUNT = 37;
    private static final int MIGLASS_SHAPE_THICKNESS_PX = 21;
    private static final int MIGLASS_SHAPE_REFLECT_OFFSET_PX = 22;
    private static final int MIGLASS_REFRACT_IOR = 32;

    // Percentage settings written by VSTeam Hyper Config. Unset means 100
    // (stock), so 0 stays available as a real value.
    private static int getPercentGlobal(String name, int max) {
        try {
            int value = Settings.Global.getInt(getCR(), name, 100);
            return Math.max(0, Math.min(max, value));
        } catch (Exception e) {
            return 100;
        }
    }

    // Called from View.setMiGlass. vs_liquid_glass_level is a percentage of the
    // stock refraction: lower is flatter glass, higher bends the background more.
    public static float[] tuneMiGlass(float[] params) {
        if (params == null || params.length < MIGLASS_PARAM_COUNT) {
            return params;
        }
        int level = getPercentGlobal("vs_liquid_glass_level", 400);
        if (level == 100) {
            return params;
        }
        float scale = level / 100.0f;
        float[] tuned = params.clone();
        // The shader offsets the background by refract(normal, 1/IOR) * thickness,
        // so both the lens depth and the index have to move together.
        tuned[MIGLASS_SHAPE_THICKNESS_PX] *= scale;
        tuned[MIGLASS_SHAPE_REFLECT_OFFSET_PX] *= scale;
        tuned[MIGLASS_REFRACT_IOR] = 1.0f + (tuned[MIGLASS_REFRACT_IOR] - 1.0f) * scale;
        return tuned;
    }

    // Called from MiuiSystemUI ShadeBlendBlurControllerImpl.BlurProvider:
    // maximum blur radius behind the control center / notification shade.
    public static int tuneShadeBlurRadius(int radius) {
        int level = getPercentGlobal("vs_cc_blur_level", 300);
        return level == 100 ? radius : Math.round(radius * level / 100.0f);
    }

    private static int scaleAlpha(int color, int level) {
        int alpha = Math.min(255, Math.round((color >>> 24) * level / 100.0f));
        return (alpha << 24) | (color & 0x00ffffff);
    }

    // Shade blend colors come as {color, blendMode, color, blendMode, ...};
    // only the colors' alpha is scaled.
    public static int[] tuneShadeBlendColors(int[] colors) {
        int level = getPercentGlobal("vs_cc_dim_level", 300);
        if (colors == null || level == 100) {
            return colors;
        }
        int[] tuned = colors.clone();
        for (int i = 0; i < tuned.length; i += 2) {
            tuned[i] = scaleAlpha(tuned[i], level);
        }
        return tuned;
    }

    // shade_dim_color, the darkening layer over the wallpaper behind the shade.
    public static int tuneShadeDimColor(int color) {
        int level = getPercentGlobal("vs_cc_dim_level", 300);
        return level == 100 ? color : scaleAlpha(color, level);
    }

    // Called from QSTileItemIconView.applyTileBackgroundStyle: progress blends
    // the tile background from the inactive (0) to the active (1) look. With
    // vs_cc_flat_active_tile on, active tiles keep the inactive glass.
    public static float tuneTileActiveProgress(float progress) {
        return getBooleanGlobal("vs_cc_flat_active_tile") ? 0.0f : progress;
    }

    // With vs_cc_flat_active_tile on, an active tile loses its white fill, so
    // icon and card text carry the on/off difference on the glass instead:
    // on is solid bright white, off is the stock color dimmed well below it.
    private static final int CC_ACTIVE_WHITE = 0xFFFFFFFF;
    private static final int CC_INACTIVE_ALPHA = 45; // percent of the stock off color
    private static final int QS_TILE_ACTIVE = 2; // Tile.STATE_ACTIVE

    // QSTileItemIconView.drawableTint / QSCardItemIconView.setIcon, on colors.
    public static int tuneActiveIconColor(int color) {
        return getBooleanGlobal("vs_cc_flat_active_tile") ? CC_ACTIVE_WHITE : color;
    }

    // Same methods, off color: dimmed so the white active icons stand out.
    public static int tuneInactiveIconColor(int color) {
        return getBooleanGlobal("vs_cc_flat_active_tile") ? scaleAlpha(color, CC_INACTIVE_ALPHA) : color;
    }

    // QSCardItemView.updateState: the on title is black, made for the white fill.
    public static int tuneCardTitleColor(int color, int state) {
        if (state != QS_TILE_ACTIVE || !getBooleanGlobal("vs_cc_flat_active_tile")) {
            return color;
        }
        return 0xFFFFFFFF;
    }

    // Same method, status line ("Connected"): bright white like the on icon.
    public static int tuneCardStatusColor(int color, int state) {
        if (state != QS_TILE_ACTIVE || !getBooleanGlobal("vs_cc_flat_active_tile")) {
            return color;
        }
        return CC_ACTIVE_WHITE;
    }

    public static float getFloatGlobalOrZero(String name) {
        String vl = getStrGlobal(name);
        if (vl != null && !vl.isEmpty()) {
            try {
                float v = parseFloatFromString(vl);
                if (v == RATE_VAL_30
                        || v == RATE_VAL_60
                        || v == RATE_VAL_72
                        || v == RATE_VAL_90
                        || v == RATE_VAL_120
                        || v == RATE_VAL_144
                        || v == RATE_VAL_165) {
                    return v;
                }
            } catch (Exception e) {
                vslog("getFloatGlobalOrZero Error: " + name);
            }
        }
        return RATE_VAL_120;
    }

    public static int get_custom_dock_cellwith(int dvalue) {
        if (getBooleanGlobal("vs_fold_dock_enable")) {
            String vl = getStrGlobal("vs_use_dock_cellwith_value");
            if (vl != null && !vl.isEmpty()) {
                try {
                    return Integer.parseInt(vl);
                } catch (Exception e) {
                    vslog("get_custom_ani Error");
                    return dvalue;
                }
            }
        }
        vslog("fold dock cellwith " + String.valueOf(dvalue));
        return dvalue;
    }

    public static String get_custom_screenshot() {
        boolean useAospScreenshot = getBooleanGlobal("vs_use_aosp_screenshot");
        if (useAospScreenshot) {
            return "com.android.systemui/com.android.systemui.screenshot.TakeScreenshotService";
        }
        return "com.miui.screenshot/com.miui.screenshot.TakeScreenshotService";
    }

    public static boolean getBooleanGlobal_custom(String name, boolean check) {
        return getIntGlobal(name) != 0;
    }

    public static boolean getGlobalSettingAsBoolean(ContentResolver contentResolver, String settingName) {
        int defaultValue = 0;
        int intValue = Settings.Global.getInt(contentResolver, settingName, defaultValue);

        return intValue == 1;

    }
    public static Certificate[] engineGetCertificateChain(Certificate[] caList) {
        String currentApp = getCurrentAppPackage();

        // Nếu skipspoof bật thì trả lại y nguyên, ngược lại thì tạo cert mới
        if (getBooleanGlobal("vs_skip_spoof_cert")) {
            vslog("Skip spoof cert chain for app: " + currentApp);
            return caList;
        }

        skipspoofbyapp = VsProps.shouldSkipSpoofCert(currentApp, sCR);
        if (skipspoofbyapp) {
            vslog("Skip spoof cert: " + currentApp);
            return caList;
        }

        if (getBooleanGlobal("vs_block_key_attest")) {
            throw new UnsupportedOperationException();
        }

        if (VsProps.shouldBlockKeyAttest(currentApp, sCR)) {
            vslog("Blocking key attestation for app: " + currentApp);
            throw new UnsupportedOperationException();
        }

        // Nếu danh sách chứng chỉ rỗng hoặc chỉ có 1 phần tử thì giữ nguyên
        if (caList == null || caList.length < 2) {
            return caList;
        }


        VsProps.CustomKeyAttestConfig customConfig = VsProps.getCustomKeyAttestConfig(currentApp, sCR);
        if (customConfig != null) {
            vslog("Creating spoofed certificate chain for app: " + currentApp
                    + " | attestation=CUSTOM(tags=" + customConfig.tags + ")"
                    + " | keyInfo=" + (customConfig.useKeyboxKeyInfo ? "keybox" : "stock")
                    + " | rootOfTrustOverride=" + (customConfig.verifiedBootKeyHex != null
                            || customConfig.verifiedBootHashHex != null)
                    + " | serialOverride=" + (customConfig.serialValue != null));
            try {
                return SubAttest.CreateCert(caList, customConfig.useKeyboxKeyInfo, customConfig.tags,
                        customConfig.verifiedBootKeyHex, customConfig.verifiedBootHashHex, customConfig.serialValue);
            } catch (LinkageError error) {
                vslog("SubAttest CreateCert ABI mismatch: " + error);
                return caList;
            }
        }

        boolean useKeyboxKeyInfo = VsProps.shouldUseKeyboxKeyInfo(currentApp, sCR);
        vslog("Creating spoofed certificate chain for app: " + currentApp
                + " | attestation=STOCK"
                + " | keyInfo=" + (useKeyboxKeyInfo ? "keybox" : "stock"));
        try {
            return SubAttest.CreateCert(caList, useKeyboxKeyInfo);
        } catch (LinkageError error) {
            // framework.jar and the injected SubAttest backend must expose the
            // same ABI. Keep KeyStore usable if a stale backend is installed.
            vslog("SubAttest CreateCert ABI mismatch: " + error);
            return caList;
        }
    }


    public static String getCurrentAppPackage() {
        try {
            String processName = Application.getProcessName();
            if (processName != null && !processName.isEmpty()) {
                int colonIndex = processName.lastIndexOf(':');
                if (colonIndex > 0) {
                    return processName.substring(0, colonIndex);
                }
                return processName;
            }
        } catch (Exception e) {
            vslog("Error getting app package from process name: " + e.getMessage());
        }

        try {
            Context context = getCon();
            if (context != null) {
                return context.getPackageName();
            }
        } catch (Exception e) {
            vslog("Error getting app package from context: " + e.getMessage());
        }

        return "unknown_app";
    }

    public static byte[] modifyLeaf(byte[] bytes) throws Throwable {
        String currentApp = getCurrentAppPackage();
        if (getBooleanGlobal("vs_skip_spoof_cert")) {
            vslog("Skip spoof leaf");
            return bytes;
        }

        skipspoofbyapp = VsProps.shouldSkipSpoofCert(currentApp, sCR);
        if (skipspoofbyapp) {
            vslog("Skip spoof cert: " + currentApp);
            return bytes;
        }

        if (VsProps.shouldBlockKeyAttest(currentApp, sCR)) {
            vslog("Blocking key attestation for app: " + currentApp);
            throw new UnsupportedOperationException();
        }

        VsProps.CustomKeyAttestConfig customConfig = VsProps.getCustomKeyAttestConfig(currentApp, sCR);
        if (customConfig != null) {
            vslog("Modifying leaf for app: " + currentApp
                    + " | attestation=CUSTOM(tags=" + customConfig.tags + ")"
                    + " | keyInfo=" + (customConfig.useKeyboxKeyInfo ? "keybox" : "stock")
                    + " | rootOfTrustOverride=" + (customConfig.verifiedBootKeyHex != null
                            || customConfig.verifiedBootHashHex != null)
                    + " | serialOverride=" + (customConfig.serialValue != null));
            try {
                return SubAttest.modifyLeaf(bytes, customConfig.useKeyboxKeyInfo, customConfig.tags,
                        customConfig.verifiedBootKeyHex, customConfig.verifiedBootHashHex, customConfig.serialValue);
            } catch (LinkageError error) {
                vslog("SubAttest modifyLeaf ABI mismatch: " + error);
                return bytes;
            }
        }

        boolean useKeyboxKeyInfo = VsProps.shouldUseKeyboxKeyInfo(currentApp, sCR);
        vslog("Modifying leaf for app: " + currentApp
                + " | attestation=STOCK"
                + " | keyInfo=" + (useKeyboxKeyInfo ? "keybox" : "stock"));
        try {
            return SubAttest.modifyLeaf(bytes, useKeyboxKeyInfo);
        } catch (LinkageError error) {
            vslog("SubAttest modifyLeaf ABI mismatch: " + error);
            return bytes;
        }
    }

    // ---------------------------------------------------------------------
    // Route B: synthesize attestation for keys the TEE could not attest.
    //
    // On RKP-only devices whose attestation key pool cannot be provisioned,
    // KeyStore.generateKeyPair() with an attestation challenge fails in KeyMint
    // with -74 (ATTESTATION_KEYS_NOT_PROVISIONED) before any leaf exists. The
    // framework generate hook (AndroidKeyStoreKeyPairGeneratorSpi) then:
    //   1) remembers the requested attestation parameters here, keyed by alias,
    //   2) retries generateKey WITHOUT the challenge so the TEE produces a plain
    //      (un-attested) key that is actually stored.
    // When the app later reads the key, KeyStore2.getKeyEntry -> modifyOrSynthLeaf
    // sees a leaf with no attestation extension, looks up the remembered params
    // and builds the whole attestation chain from the keybox via
    // SubAttest.generateChain. Everything runs in the app's own process, so the
    // map is naturally scoped to the requesting app.
    // ---------------------------------------------------------------------

    private static final class PendingAttest {
        final byte[] challenge;
        final int keySize;
        final int[] purposes;
        final int[] digests;
        final int ecCurve;
        final byte[] rsaExponent;
        final String packageName;
        final long versionCode;
        final byte[] signatureDigest;

        PendingAttest(byte[] challenge, int keySize, int[] purposes, int[] digests,
                      int ecCurve, byte[] rsaExponent, String packageName, long versionCode,
                      byte[] signatureDigest) {
            this.challenge = challenge;
            this.keySize = keySize;
            this.purposes = purposes;
            this.digests = digests;
            this.ecCurve = ecCurve;
            this.rsaExponent = rsaExponent;
            this.packageName = packageName;
            this.versionCode = versionCode;
            this.signatureDigest = signatureDigest;
        }
    }

    private static final int MAX_PENDING_ATTEST = 64;
    private static final Map<String, PendingAttest> PENDING_ATTEST = new ConcurrentHashMap<>();
    // keystore2's getKeyEntry response carries a KEY_ID KeyDescriptor whose alias is null, so the
    // read-side onGetKeyEntry hook usually cannot recover the alias the key was stored under. Track
    // the most recently remembered alias as a fallback: an app reads back the key it just
    // generated, and this map is per-process, so the newest pending entry is almost always the
    // right one when the alias is unavailable.
    private static volatile String LAST_PENDING_ALIAS;

    /** Default RSA public exponent (F4 = 65537) when the spec does not override it. */
    private static final byte[] DEFAULT_RSA_EXPONENT = {0x01, 0x00, 0x01};

    // Route B is fully automatic: real TEE attestation is attempted first, and only when it fails
    // with "attestation keys not provisioned" (KeyMint -74, typical of RKP-only devices that cannot
    // provision) does the generate hook retry without a challenge and synthesize from the keybox.
    // These two thread-locals coordinate that single retry across the recursive generateKeyPair()
    // call the :catch_95 hook makes. STRIP is a one-shot consumed by interceptChallenge on the
    // retry; TRIED guards against a second retry (no infinite recursion).
    private static final ThreadLocal<Boolean> ATTEST_STRIP = new ThreadLocal<>();
    private static final ThreadLocal<Boolean> ATTEST_TRIED = new ThreadLocal<>();

    /** KeyMint ATTESTATION_KEYS_NOT_PROVISIONED (and the related IDs code). */
    private static final int KM_ERROR_ATTESTATION_KEYS_NOT_PROVISIONED = -74;
    private static final int KM_ERROR_ATTESTATION_IDS_NOT_PROVISIONED = -75;

    /**
     * Injected at the challenge-read site of AndroidKeyStoreKeyPairGeneratorSpi.
     * addAttestationParameters (the smali passes the Spi {@code p0} and the challenge, needing no
     * scratch register). Returns the challenge unchanged on the first, real attestation attempt;
     * returns {@code null} only on the bypass retry armed by {@link #beginAttestBypass} after a
     * provisioning failure, so NO challenge reaches KeyMint and the TEE generates a plain key.
     */
    public static byte[] interceptChallenge(Object spiOrSpec, byte[] originalChallenge) {
        if (originalChallenge == null) {
            return null;
        }
        try {
            if (Boolean.TRUE.equals(ATTEST_STRIP.get())) {
                ATTEST_STRIP.set(Boolean.FALSE); // one-shot
                vslog("interceptChallenge: stripping challenge for keybox synthesis");
                return null;
            }
        } catch (Throwable t) {
            vslog("interceptChallenge failed: " + t);
        }
        return originalChallenge;
    }

    /**
     * True when the KeyStoreException that aborted key generation means the TEE has no attestation
     * key to sign with (RKP-only device whose key pool could not be provisioned). Injected at
     * generateKeyPair's KeyStoreException catch to decide whether to attempt the keybox fallback.
     */
    public static boolean isAttestNotProvisioned(Object keyStoreException) {
        if (keyStoreException == null) return false;
        // android.security.KeyStoreException exposes the raw KeyMint code through
        // getNumericErrorCode(); getErrorCode() only returns the coarse public/systemic category
        // (e.g. KEY_NOT_TEMPORALLY_VALID / SYSTEMIC), which never equals -74. Check the numeric
        // code first, then fall back to the public one, then to the message/cause chain.
        for (String method : new String[] {"getNumericErrorCode", "getErrorCode"}) {
            try {
                Object code = keyStoreException.getClass().getMethod(method)
                        .invoke(keyStoreException);
                if (code instanceof Integer) {
                    int c = (Integer) code;
                    if (c == KM_ERROR_ATTESTATION_KEYS_NOT_PROVISIONED
                            || c == KM_ERROR_ATTESTATION_IDS_NOT_PROVISIONED) {
                        return true;
                    }
                }
            } catch (Throwable ignored) {
            }
        }
        // Fall back to the message, which carries "code: -74" / the KeyMint constant name. Walk the
        // whole cause chain: the -74 is often nested inside a ProviderException wrapper.
        try {
            StringBuilder msg = new StringBuilder();
            Throwable t = (Throwable) keyStoreException;
            for (int depth = 0; t != null && depth < 8; depth++) {
                msg.append(t).append(' ');
                t = t.getCause();
            }
            String s = msg.toString();
            return s.contains("-74") || s.contains("-75")
                    || s.contains("ATTESTATION_KEYS_NOT_PROVISIONED")
                    || s.contains("ATTESTATION_IDS_NOT_PROVISIONED");
        } catch (Throwable ignored) {
        }
        return false;
    }

    /**
     * Arms the keybox-synthesis fallback: records the attestation parameters and tells the next
     * interceptChallenge (on the retry) to strip the challenge. Returns false if a retry was
     * already attempted on this thread, so the generate hook retries at most once.
     */
    public static boolean beginAttestBypass(Object spiOrSpec) {
        try {
            if (Boolean.TRUE.equals(ATTEST_TRIED.get())) {
                return false;
            }
            Object spec = resolveKeyGenSpec(spiOrSpec);
            if (spec == null) return false;
            // Reconstruct the challenge from the spec (it was the real one on the failed attempt).
            byte[] challenge = (byte[]) spec.getClass().getMethod("getAttestationChallenge")
                    .invoke(spec);
            if (challenge == null) return false;
            if (!rememberAttestation(spec, challenge)) return false;
            ATTEST_TRIED.set(Boolean.TRUE);
            ATTEST_STRIP.set(Boolean.TRUE);
            vslog("beginAttestBypass: retrying generation without challenge for keybox synthesis");
            return true;
        } catch (Throwable t) {
            vslog("beginAttestBypass failed: " + t);
            return false;
        }
    }

    /** Clears the per-thread bypass state after the generate hook finishes handling the failure. */
    public static void endAttestBypass() {
        try {
            ATTEST_STRIP.remove();
            ATTEST_TRIED.remove();
        } catch (Throwable ignored) {
        }
    }

    /**
     * Returns the KeyGenParameterSpec from either the spec itself or the Spi instance that holds it
     * in a {@code mSpec} field. Lets the generate hook pass {@code p0} (the Spi) directly.
     */
    private static Object resolveKeyGenSpec(Object spiOrSpec) {
        if (spiOrSpec == null) return null;
        try {
            // A spec exposes getKeystoreAlias(); the Spi does not.
            spiOrSpec.getClass().getMethod("getKeystoreAlias");
            return spiOrSpec;
        } catch (NoSuchMethodException notTheSpec) {
            // Fall through: treat it as the Spi and read its mSpec field.
        } catch (Throwable t) {
            return null;
        }
        try {
            java.lang.reflect.Field field = spiOrSpec.getClass().getDeclaredField("mSpec");
            field.setAccessible(true);
            return field.get(spiOrSpec);
        } catch (Throwable t) {
            vslog("resolveKeyGenSpec failed: " + t);
            return null;
        }
    }

    /**
     * Records the attestation parameters of a key whose TEE attestation is being bypassed, so the
     * leaf can be synthesized from the keybox when the app reads the key back. The KeyMint
     * algorithm is not needed here; it is derived from the generated public key at synthesis time.
     *
     * @return true if parameters were recorded under the key's alias.
     */
    public static boolean rememberAttestation(Object spec, byte[] challenge) {
        if (spec == null || challenge == null) return false;
        try {
            Class<?> cls = spec.getClass();
            String alias = (String) cls.getMethod("getKeystoreAlias").invoke(spec);
            if (alias == null || alias.isEmpty()) return false;

            int keySize = -1;
            try {
                keySize = (int) cls.getMethod("getKeySize").invoke(spec);
            } catch (Throwable ignored) {
            }

            int purposeMask = 0;
            try {
                purposeMask = (int) cls.getMethod("getPurposes").invoke(spec);
            } catch (Throwable ignored) {
            }
            int[] purposes = mapPurposes(purposeMask);

            int[] digests;
            try {
                String[] digestNames = (String[]) cls.getMethod("getDigests").invoke(spec);
                digests = mapDigests(digestNames);
            } catch (Throwable noDigest) {
                digests = new int[]{4}; // SHA-256
            }

            // Valid only for EC keys; ignored at synthesis time for RSA. keySize may be <=0 here
            // (EC specs often omit it); the synthesizer fixes it up once the algorithm is known.
            int ecCurve = ecCurveForKeySize(keySize <= 0 ? 256 : keySize);

            String packageName = getCurrentAppPackage();
            long versionCode = 0L;
            byte[] signatureDigest = null;
            try {
                Context context = getCon();
                if (context != null) {
                    PackageManager pm = context.getPackageManager();
                    PackageInfo pi = pm.getPackageInfo(packageName,
                            PackageManager.GET_SIGNING_CERTIFICATES);
                    versionCode = pi.getLongVersionCode();
                    signatureDigest = firstSignatureDigest(pi);
                }
            } catch (Throwable t) {
                vslog("rememberAttestation: package info lookup failed: " + t);
            }

            if (PENDING_ATTEST.size() >= MAX_PENDING_ATTEST) {
                PENDING_ATTEST.clear();
            }
            PENDING_ATTEST.put(alias, new PendingAttest(challenge, keySize, purposes, digests,
                    ecCurve, DEFAULT_RSA_EXPONENT, packageName, versionCode, signatureDigest));
            LAST_PENDING_ALIAS = alias;
            vslog("rememberAttestation: stored params for alias=" + alias + " pkg=" + packageName);
            return true;
        } catch (Throwable t) {
            vslog("rememberAttestation failed: " + t);
            return false;
        }
    }

    private static int[] mapPurposes(int mask) {
        // KeyProperties.PURPOSE_* bit -> KeyMint purpose enum.
        List<Integer> out = new ArrayList<>();
        if ((mask & 1) != 0) out.add(0);   // ENCRYPT
        if ((mask & 2) != 0) out.add(1);   // DECRYPT
        if ((mask & 4) != 0) out.add(2);   // SIGN
        if ((mask & 8) != 0) out.add(3);   // VERIFY
        if ((mask & 32) != 0) out.add(5);  // WRAP_KEY
        if ((mask & 64) != 0) out.add(6);  // AGREE_KEY
        if ((mask & 128) != 0) out.add(7); // ATTEST_KEY
        if (out.isEmpty()) {
            out.add(2);
            out.add(3);
        }
        int[] res = new int[out.size()];
        for (int i = 0; i < res.length; i++) res[i] = out.get(i);
        return res;
    }

    private static int[] mapDigests(String[] names) {
        if (names == null || names.length == 0) return new int[]{4};
        List<Integer> out = new ArrayList<>();
        for (String name : names) {
            if (name == null) continue;
            switch (name.toUpperCase(Locale.ROOT)) {
                case "NONE": out.add(0); break;
                case "MD5": out.add(1); break;
                case "SHA-1": out.add(2); break;
                case "SHA-224": out.add(3); break;
                case "SHA-256": out.add(4); break;
                case "SHA-384": out.add(5); break;
                case "SHA-512": out.add(6); break;
                default: break;
            }
        }
        if (out.isEmpty()) out.add(4);
        int[] res = new int[out.size()];
        for (int i = 0; i < res.length; i++) res[i] = out.get(i);
        return res;
    }

    private static int ecCurveForKeySize(int keySize) {
        // KeyMint EcCurve enum: P-224=0, P-256=1, P-384=2, P-521=3.
        if (keySize <= 224) return 0;
        if (keySize <= 256) return 1;
        if (keySize <= 384) return 2;
        return 3;
    }

    private static byte[] firstSignatureDigest(PackageInfo pi) {
        try {
            if (pi.signingInfo == null) return null;
            android.content.pm.Signature[] sigs = pi.signingInfo.getApkContentsSigners();
            if (sigs == null || sigs.length == 0) return null;
            return MessageDigest.getInstance("SHA-256").digest(sigs[0].toByteArray());
        } catch (Throwable t) {
            vslog("firstSignatureDigest failed: " + t);
            return null;
        }
    }

    /**
     * Called from the KeyStore2.getKeyEntry hook with the stored leaf and its alias. Re-signs a
     * real TEE attestation leaf when present; otherwise, if the key's attestation was remembered
     * by {@link #rememberAttestation}, builds the whole attestation leaf from the keybox. Returns
     * null when nothing should change (the caller then leaves the response untouched). Propagates
     * the key-attestation block as UnsupportedOperationException exactly like {@link #modifyLeaf}.
     */
    public static byte[] modifyOrSynthLeaf(byte[] leafBytes, String alias) {
        try {
            return modifyLeaf(leafBytes);
        } catch (UnsupportedOperationException block) {
            throw block;
        } catch (Throwable noAttestationExtension) {
            // Leaf has no attestation extension (TEE could not attest): fall through to synthesis.
        }

        try {
            PendingAttest pending = (alias == null) ? null : PENDING_ATTEST.remove(alias);
            if (pending == null) {
                // keystore2 hands getKeyEntry a KEY_ID KeyDescriptor with alias==null, so the hook
                // rarely knows the alias. Fall back to the most recently remembered pending key:
                // the app reads back the key it just generated, and the map is per-process.
                String last = LAST_PENDING_ALIAS;
                if (last != null) {
                    pending = PENDING_ATTEST.remove(last);
                    if (pending != null) {
                        vslog("modifyOrSynthLeaf: alias unavailable, synthesizing from last pending "
                                + last);
                    }
                }
            }
            if (pending == null) {
                return null;
            }
            PublicKey publicKey = extractPublicKey(leafBytes);
            if (publicKey == null) {
                return null;
            }
            boolean isEc = "EC".equalsIgnoreCase(publicKey.getAlgorithm());
            int kmAlgo = isEc ? SubAttest.KM_ALGORITHM_EC : SubAttest.KM_ALGORITHM_RSA;
            int keySize = pending.keySize;
            if (keySize <= 0) {
                keySize = isEc ? 256 : 2048;
            }
            Certificate[] chain = SubAttest.generateChain(publicKey, pending.challenge,
                    kmAlgo, keySize, pending.purposes, pending.digests,
                    pending.ecCurve, pending.rsaExponent, pending.packageName, pending.versionCode,
                    pending.signatureDigest);
            if (chain == null || chain.length == 0) {
                return null;
            }
            // SubAttest.generateChain set SubAttest.algo, so the existing hook's
            // getCertificateChain(algo) call returns the matching keybox chain.
            return chain[0].getEncoded();
        } catch (Throwable t) {
            vslog("modifyOrSynthLeaf synthesis failed: " + t);
            return null;
        }
    }

    private static PublicKey extractPublicKey(byte[] leafBytes) {
        try {
            CertificateFactory cf = CertificateFactory.getInstance("X.509");
            X509Certificate cert = (X509Certificate) cf.generateCertificate(
                    new ByteArrayInputStream(leafBytes));
            return cert.getPublicKey();
        } catch (Throwable t) {
            vslog("extractPublicKey failed: " + t);
            return null;
        }
    }

    @SuppressLint("SimpleDateFormat")
    public static void updateOplusMediaControllerConfig(ContentResolver cr) {
        final String filePath = "/data/user_de/0/com.android.systemui/shared_prefs/oplus_media_controller_config_sp.xml";
        final File xmlFile = new File(filePath);

        try {
            // 1) Lấy chuỗi packages từ Settings.Global
            String raw = Settings.Global.getString(cr, "vs_sys_media_dynamic");

            // 2) Chuẩn hoá danh sách: tách theo ',', trim, loại rỗng, loại trùng (giữ thứ tự)
            java.util.Set<String> whiteList = new java.util.LinkedHashSet<>();
            if (raw != null && !raw.trim().isEmpty()) {
                for (String s : raw.split(",")) {
                    if (s != null) {
                        String t = s.trim();
                        if (!t.isEmpty()) whiteList.add(t);
                    }
                }
            }

            // 3) Lấy ngày hiện tại dạng yyyyMMdd
            String today = new java.text.SimpleDateFormat("yyyyMMdd").format(new java.util.Date());

            // 4) Build phần <set name="rus_content_key"> ... </set> hoặc self-closing nếu rỗng
            final String setRusContent;
            if (whiteList.isEmpty()) {
                setRusContent = "    <set name=\"rus_content_key\" />\n";
            } else {
                StringBuilder sb = new StringBuilder();
                sb.append("    <set name=\"rus_content_key\">\n");
                for (String pkg : whiteList) {
                    sb.append("        <string>").append(pkg).append("</string>\n");
                }
                sb.append("    </set>\n");
                setRusContent = sb.toString();
            }

            // 5) Build toàn bộ XML
            String newContent =
                    "<?xml version='1.0' encoding='utf-8' standalone='yes' ?>\n" +
                            "<map>\n" +
                            "    <set name=\"rus_blacklist_key\" />\n" +
                            "    <int name=\"rus_location_version_key\" value=\"" + today + "\" />\n" +
                            setRusContent +
                            "</map>\n";

            // 6) Đảm bảo thư mục tồn tại rồi ghi đè file
            File parent = xmlFile.getParentFile();
            if (parent != null && !parent.exists()) parent.mkdirs();

            try (FileOutputStream fos = new FileOutputStream(xmlFile, false)) {
                fos.write(newContent.getBytes(java.nio.charset.StandardCharsets.UTF_8));
                fos.getFD().sync();
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }


    public static void SuperSetprop(Application application, Context context) throws NoSuchFieldException, ClassNotFoundException, IllegalAccessException {

        try {
            ContentResolver contentResolver = context.getContentResolver();
            String Pkg_name = context.getPackageName();
            String processName = Application.getProcessName();

            if (android.os.Process.isIsolated()) {
                return;
            }

            PackageManager packageManager = context.getPackageManager();
            String versionName = "Unknown";
            try {
                PackageInfo packageInfo = packageManager.getPackageInfo(Pkg_name, 0);
                versionName = packageInfo.versionName;
            } catch (PackageManager.NameNotFoundException e) {
                vslog("Error getting version name: " + e.getMessage());
            }


            if (Pkg_name.equals("com.android.systemui")) {
                try {

                    Settings.Global.putString(contentResolver, "hidden_api_policy", null);

                } catch (Exception e) {
                    vslog(e.getMessage());
                }

            }


            if (Pkg_name.equals("com.coloros.bootreg")) {

                if (SystemProperties.getBoolean("persist.sys.device_first_boot", false)) {
                    Settings.Secure.putInt(contentResolver, "vs_minus_screen_ggl", 1);
                    Settings.Secure.putInt(contentResolver, "vs_aosp_perm", 1);
                }
            }


            if (SystemProperties.getBoolean("sys.boot_completed", false)) {

                VsProps.setProps(Pkg_name, processName, versionName, contentResolver);

            }



        } catch (Exception e) {
            vslog("SuperSetprop Error");
            vslog(e.getMessage());
        }

    }

    public static boolean getBooleanGlobal(String name) {
        try {
            return 0 != getIntGlobal(name);
        } catch (Exception e) {
            return false;
        }
    }

    public static boolean getBooleanSecure(String name, boolean check) {
        try {
            return 0 != getIntSecure(name);
        } catch (Exception e) {
            return false;
        }
    }

    public static boolean getBooleanGlobalZ(String name) {
        try {
            return 0 == getIntGlobal(name);
        } catch (Exception e) {
            return true;
        }
    }

    public static String getRegion() {
        return "VN";
    }

    public static boolean kill(ProcessConfig config) {
        return false;
    }

    public static double parseDoubleFromString(String str) {
        return Double.parseDouble(str.replace(",", "."));
    }

    public static int getIntGlobal(String name) {
        try {
            return Settings.Global.getInt(getCR(), name, 0);
        } catch (Exception e) {
            Toolbox.vslog("Error in getIntGlobal: " + e.toString());
            return 0;
        }
    }

    public static String getStringGlobal(String name) {
        try {
            return Settings.Global.getString(getCR(), name);
        } catch (Exception e) {
            Toolbox.vslog("Error in getStringGlobal: " + e.toString());
            return null;
        }
    }

    private static volatile int cachedVsteamSystemUid = Integer.MIN_VALUE;

    // On this ROM, PackageManagerInternal is packaged inside services.jar,
    // not framework.jar. Toolbox is loaded by the boot classloader
    // (framework.jar only, confirmed via decompile), and even a Binder pool
    // thread's context classloader in system_server does not see
    // services.jar classes — both Class.forName(name) and
    // Class.forName(name, false, Thread.currentThread().getContextClassLoader())
    // threw ClassNotFoundException in practice (checked via logcat on device).
    // com.android.server.LocalServices, however, IS in framework.jar and
    // loads fine. Its registry is a private static
    // ArrayMap<Class<?>, Object> sLocalServiceObjects (field name confirmed
    // by decompiling LocalServices.smali) that PackageManagerService already
    // populated using ITS OWN services.jar classloader at boot. Reading that
    // field directly hands back the already-loaded PackageManagerInternal
    // instance without Toolbox ever having to load the class itself; from
    // there, instance.getClass() is the correct, already-loaded class.
    @SuppressLint("BlockedPrivateApi")
    static Object findLocalService(String className) {
        try {
            Class<?> localServicesClass = Class.forName("com.android.server.LocalServices");
            java.lang.reflect.Field field = localServicesClass.getDeclaredField("sLocalServiceObjects");
            field.setAccessible(true);
            Object mapObj = field.get(null);
            if (!(mapObj instanceof Map)) {
                return null;
            }
            Map<?, ?> map = (Map<?, ?>) mapObj;
            synchronized (map) {
                for (Map.Entry<?, ?> entry : map.entrySet()) {
                    Object key = entry.getKey();
                    if (key instanceof Class && className.equals(((Class<?>) key).getName())) {
                        return entry.getValue();
                    }
                }
            }
        } catch (Exception e) {
            vslog("findLocalService Error: " + e);
        }
        return null;
    }

    // PackageManagerInternal.getPackageUid() always resolves using a fixed
    // SYSTEM_UID (1000) as its internal filtering context, not the real
    // caller — verified by decompiling ComputerEngine.getPackageUidInternal.
    // So even though this re-enters shouldFilterApplication one extra level
    // deep, that nested call always exits at the callingUid < 10000 check in
    // isPackageAlwaysHidden below, before it would ever call this again.
    // Bounded to exactly one extra frame; never recurses further.
    private static int getVsteamUidForFilter(int userId) {
        if (cachedVsteamSystemUid != Integer.MIN_VALUE) {
            return cachedVsteamSystemUid;
        }
        try {
            Object pmi = findLocalService("android.content.pm.PackageManagerInternal");
            if (pmi == null) {
                return -1;
            }
            int uid = (int) pmi.getClass().getMethod("getPackageUid", String.class, long.class, int.class)
                    .invoke(pmi, "com.bhlnk.vsteam", 0L, userId);
            cachedVsteamSystemUid = uid;
            return uid;
        } catch (Exception e) {
            vslog("getVsteamUidForFilter Error: " + e);
            return -1;
        }
    }

    // PackageManagerInternal.isSystemPackage()/getNameForUid() are safe for
    // the same reason as getVsteamUidForFilter(): isSystemPackage() calls
    // Binder.clearCallingIdentity() before its internal lookup (verified by
    // decompiling PackageManagerService.ensureSystemPackageName), so any
    // nested call back into shouldFilterApplication sees callingUid=1000
    // (system), which always exits at the UID check below instead of
    // recursing further. getNameForUid() never reaches the overload this
    // patches at all (it goes through shouldFilterApplicationIncludingUninstalled).
    private static boolean isSystemCaller(int callingUid) {
        if (callingUid < android.os.Process.FIRST_APPLICATION_UID) {
            return true;
        }
        try {
            Object pmi = findLocalService("android.content.pm.PackageManagerInternal");
            if (pmi == null) {
                return false;
            }
            String callerPkg = (String) pmi.getClass().getMethod("getNameForUid", int.class)
                    .invoke(pmi, callingUid);
            if (callerPkg == null) {
                return false;
            }
            return (boolean) pmi.getClass().getMethod("isSystemPackage", String.class)
                    .invoke(pmi, callerPkg);
        } catch (Exception e) {
            vslog("isSystemCaller Error: " + e);
            return false;
        }
    }

    private static final String ALWAYS_HIDDEN_SETTING = "vs_always_hidden_packages";
    private static volatile Set<String> sAlwaysHiddenPackages = java.util.Collections.emptySet();
    private static final java.util.concurrent.atomic.AtomicBoolean sAlwaysHiddenWatchStarted =
            new java.util.concurrent.atomic.AtomicBoolean(false);
    private static Handler sAlwaysHiddenHandler;

    // shouldFilterApplication runs with AppOpsService/AMS locks held by the
    // caller, while SettingsProvider (in-process in system_server) holds its
    // own lock while calling back into AppOps/AMS. Reading the setting
    // synchronously here deadlocked system_server (watchdog: android.io ->
    // binder -> ActivityManager -> binder). So only a cached copy is read on
    // this path; it is loaded and kept fresh from a dedicated thread.
    private static Set<String> getAlwaysHiddenPackages() {
        if (sAlwaysHiddenWatchStarted.compareAndSet(false, true)) {
            HandlerThread thread = new HandlerThread("vs-always-hidden");
            thread.start();
            sAlwaysHiddenHandler = new Handler(thread.getLooper());
            sAlwaysHiddenHandler.post(Toolbox::watchAlwaysHiddenPackages);
        }
        return sAlwaysHiddenPackages;
    }

    private static void watchAlwaysHiddenPackages() {
        try {
            ContentResolver cr = getCR();
            if (cr == null) {
                throw new IllegalStateException("no ContentResolver yet");
            }
            cr.registerContentObserver(Settings.Global.getUriFor(ALWAYS_HIDDEN_SETTING), false,
                    new ContentObserver(sAlwaysHiddenHandler) {
                        @Override
                        public void onChange(boolean selfChange) {
                            reloadAlwaysHiddenPackages();
                        }
                    });
            reloadAlwaysHiddenPackages();
        } catch (Throwable e) {
            vslog("watchAlwaysHiddenPackages Error: " + e);
            // Early boot: settings/content service may not be up yet.
            sAlwaysHiddenHandler.postDelayed(Toolbox::watchAlwaysHiddenPackages, 5000);
        }
    }

    private static void reloadAlwaysHiddenPackages() {
        String raw = getStringGlobal(ALWAYS_HIDDEN_SETTING);
        Set<String> parsed = new HashSet<>();
        if (raw != null) {
            for (String pkg : raw.split(",")) {
                String trimmed = pkg.trim();
                if (!trimmed.isEmpty()) {
                    parsed.add(trimmed);
                }
            }
        }
        sAlwaysHiddenPackages = parsed.isEmpty()
                ? java.util.Collections.emptySet()
                : java.util.Collections.unmodifiableSet(parsed);
    }

    // Deliberately avoids most PackageManager/PackageManagerInternal calls
    // here: this is called from inside ComputerEngine.shouldFilterApplication,
    // and several PackageManager queries (e.g. getPackagesForUid) route back
    // through that exact method with the REAL caller uid, which previously
    // caused infinite recursion and crashed system_server. The "caller is
    // viewing itself" check is done with raw UID math in the smali patch
    // before this is even called. isSystemCaller()/getVsteamUidForFilter()
    // above are the exceptions, and are safe for the reasons documented there.
    public static boolean isPackageAlwaysHidden(String packageName, int callingUid, int userId) {
        if (packageName == null || packageName.isEmpty()) {
            return false;
        }

        Set<String> hidden = getAlwaysHiddenPackages();
        if (hidden.isEmpty() || !hidden.contains(packageName)) {
            return false;
        }

        if (isSystemCaller(callingUid)) {
            return false;
        }

        return callingUid != getVsteamUidForFilter(userId);
    }

    public static String getSsaidSettingName(String packageName) {
        if (packageName == null) {
            return null;
        }
        String normalized = packageName.trim();
        if (normalized.isEmpty()) {
            return null;
        }
        return "vs_ssaid_" + normalized;
    }

    private static JSONObject getSpoofProfileForPackage(String packageName) {
        if (packageName == null || packageName.trim().isEmpty()) {
            return null;
        }

        String raw = getStringGlobal(SPOOF_JSON_PREFIX + packageName.trim());
        if (raw == null || raw.trim().isEmpty()) {
            return null;
        }

        try {
            JSONObject profile = new JSONObject(raw.trim());
            if (!profile.optBoolean("enabled", true)) return null;
            return profile;
        } catch (Exception e) {
            vslog("getSpoofProfileForPackage Error: " + e);
        }
        return null;
    }

    private static String getSsaidFromSpoofProfile(String packageName) {
        JSONObject profile = getSpoofProfileForPackage(packageName);
        if (profile == null) {
            return null;
        }
        String value = profile.optString("android_id",
                profile.optString("ANDROID_ID", ""));
        if (value == null) {
            return null;
        }
        value = value.trim();
        return value.isEmpty() ? null : value;
    }

    public static String getSsaidForPackage(String packageName) {
        String spoofProfileValue = getSsaidFromSpoofProfile(packageName);
        if (spoofProfileValue != null) {
            return spoofProfileValue;
        }
        String settingName = getSsaidSettingName(packageName);
        if (settingName == null) {
            return null;
        }
        String value = getStringGlobal(settingName);
        if (value == null) {
            return null;
        }
        value = value.trim();
        return value.isEmpty() ? null : value;
    }

    public static String getSsaidForCurrentPackage() {
        return getSsaidForPackage(getCurrentAppPackage());
    }

    public static int getIntSecure(String name) {
        try {
            return Settings.Secure.getInt(getCR(), name, 0);
        } catch (Exception e) {
            Toolbox.vslog("Error in getIntSecure: " + e.toString());
            return 0;
        }
    }

    public static String getStrGlobal(String name) {
        try {
            return Settings.Global.getString(getCR(), name);
        } catch (Exception e) {
            Toolbox.vslog("Error in getStrGlobal: " + e.toString());
            return null;
        }
    }


    public static void vslog(String str) {
        Log.d("VSLog", str);
    }

    public static void PatchSuperIcon(String str) throws IOException {
        if (str.contains("largeicons") || str.contains("rearscreen")) {
            File file = new File("/storage/emulated/0/Android/data/com.android.thememanager/files/MIUI/theme/.data/rights/theme/" + str);
            File parentFile = file.getParentFile();
            if (parentFile != null && !parentFile.exists()) {
                parentFile.mkdirs();
            }
            file.createNewFile();
        }
    }

    public static Context getCon() {
        try {
            if (sCon == null) {
                sCon = createContext();
            }
            return sCon;
        } catch (Exception e) {
            Toolbox.vslog("Error in getCon: " + e.toString());
            return null;
        }
    }

    private static ContentResolver getCR() {
        try {
            if (sCR == null) {
                sCR = getCon().getContentResolver();
            }
            return sCR;
        } catch (Exception e) {
            Toolbox.vslog("Error in getCR: " + e.toString());
            return null;
        }
    }


    private static Context createContext() {
        try {
            Class cl = Class.forName("android.app.AppGlobals");
            Method method = cl.getMethod("getInitialApplication");
            Object o = method.invoke(new Object());
            if (!(o instanceof Application)) {
                return null;
            }
            Context context = ((Application) o).createPackageContext("android", Context.CONTEXT_IGNORE_SECURITY);
            return context;
        } catch (PackageManager.NameNotFoundException e) {
            Log.d("myapp", "CreateContext error");
            return null;
        } catch (ClassNotFoundException e2) {
            Log.d("myapp", "No find AppGlobals.class");
            return null;
        } catch (IllegalAccessException | InvocationTargetException e3) {
            Log.d("myapp", "No invoke metod ");
            return null;
        } catch (NoSuchMethodException e4) {
            Log.d("myapp", "No find getInitialApplication method ");
            return null;
        }
    }


    public static boolean OverlayClipboard() {
        return getBooleanGlobal("vs_overlay_clipboard");
    }

    public static boolean UsbDialog() {
        return getBooleanGlobal("vs_usb_dialog");
    }


    private final ApplicationInfo getAppInfo(Context context) {
        PackageManager pm = context.getPackageManager();

        if (!getBooleanGlobal("vs_minus_screen_ggl")) {
            try {
                return pm.getApplicationInfo("com.coloros.assistantscreen", 128);
            } catch (PackageManager.NameNotFoundException ignored) {
                // fallback
            }
        }

        try {
            return pm.getApplicationInfo("com.google.android.googlequicksearchbox", 128);
        } catch (PackageManager.NameNotFoundException e) {
            return null; // trường hợp cực hiếm
        }
    }

    public static String getOverlayAction(Context context) {
        if (getBooleanGlobal("vs_minus_screen_ggl")) {
            return "com.android.launcher3.WINDOW_OVERLAY";
        } else {
            return "heytap.intent.action.assistantscreen.window.server";
        }
    }

    public static String getOverlayPackageName() {
        if (getBooleanGlobal("vs_minus_screen_ggl")) {
            return "com.google.android.googlequicksearchbox";
        } else {
            return "com.coloros.assistantscreen";
        }
    }


    public static ComponentName getGlobalSearchActivity() {
        if (getBooleanGlobal("vs_global_search_provider")) {
            return new ComponentName("com.google.android.googlequicksearchbox", "com.google.android.googlequicksearchbox.GoogleAppGlobalSearchImplicitGatewayInternal");
        } else {
            return new ComponentName("com.oppo.quicksearchbox", "com.oppo.quicksearchbox.SearchActivity");
        }
    }



    public final String getSearchAppPackageName() {
        if (getBooleanGlobal("vs_search_bar_ggl")) {
            return "com.google.android.googlequicksearchbox";
        } else {
            return "com.oppo.quicksearchbox";
        }
    }

    public final String getSearchAppAction() {
        if (getBooleanGlobal("vs_search_bar_ggl")) {
            return "android.search.action.GLOBAL_SEARCH";
        } else {
            return "com.oppo.quicksearchbox.action.Dispatch";
        }
    }


    public static void openGlobalSearch(Launcher launcher, View view) {
        try {

            if (getBooleanGlobal("vs_global_search_provider")) {
                Intent intent = new Intent("com.android.miui.search");
                intent.setPackage("com.mi.globalminusscreen");
                intent.putExtra("from", "launcher");
                launcher.startActivity(intent);
            } else {
                Intent intent = new Intent("android.search.action.GLOBAL_SEARCH");
                intent.setPackage("com.google.android.googlequicksearchbox");
                intent.putExtra("from", "launcher");
                launcher.startActivity(intent);
            }

        } catch (Exception unused) {
            Log.e("InternationalGlobalSearchUtil", "Global search activity not found");
        }
    }

    public static void vsSetprop(String prop, String value) {
        SystemProperties.set(prop, value);
    }

    // When the APK currently being verified is the protected package, these
    // helpers behave as if CorePatch were not present: they return the real
    // verification result / original value, so a mismatched or unsigned build
    // can never install over com.bhlnk.vsteam even while vs_corepatch_damage
    // is enabled. The *Int variants return int (not boolean) so the original
    // minSignatureSchemeVersion is preserved exactly on the protected path.

    public static boolean CorePatchTrue(boolean value) {
        if (isVerifyProtected()) {
            return value;
        }
        if (!value) {
            vslog("CorePatchTrue value : " + getBooleanGlobal("vs_corepatch_damage"));
            return getBooleanGlobal("vs_corepatch_damage");

        }
        return true;
    }

    public static int CorePatchTrueInt(int value) {
        if (isVerifyProtected()) {
            return value;
        }
        if (value != 1) {
            vslog("CorePatchTrueInt value : " + getBooleanGlobal("vs_corepatch_damage"));
            return getBooleanGlobal("vs_corepatch_damage") ? 1 : 0;

        }
        return 1;
    }

    public static boolean CorePatchFalse(boolean value) {
        if (isVerifyProtected()) {
            return value;
        }
        if (value) {
            vslog("CorePatchFalse value : " + getBooleanGlobalZ("vs_corepatch_damage"));
            return getBooleanGlobalZ("vs_corepatch_damage");
        }
        return false;
    }

    public static int CorePatchFalseInt(int value) {
        if (isVerifyProtected()) {
            return value;
        }
        if (value == 1) {
            vslog("CorePatchFalseInt value : " + getBooleanGlobalZ("vs_corepatch_damage"));
            return getBooleanGlobalZ("vs_corepatch_damage") ? 1 : 0;
        }
        return 0;
    }

    public static boolean isVerifyProtected() {
        Boolean v = sVerifyProtected.get();
        return v != null && v;
    }

    // Bypass gate for the whole-method signature-capability / V1-verify sites
    // (SigningDetails.checkCapability, StrictJarVerifier.verify, ...). The
    // CorePatch bypass applies only when the switch is on AND the APK being
    // verified is not the protected package; for the protected package these
    // methods fall through to the real framework verification logic.
    public static boolean corepatchBypass(String name) {
        return getBooleanGlobal(name) && !isVerifyProtected();
    }

    // Called at each signature parse/verify entry point (where the APK file
    // path is in scope) to record whether the package about to be checked is
    // the protected one. parsePackageLite only reads AndroidManifest; it runs
    // no signature check, so it is safe here and cannot be fooled by a
    // tampered signing block. Any lookup failure falls back to "not protected"
    // so a broken resolution never blocks ordinary installs -- it only means
    // the exception is skipped (no regression vs. current behaviour).
    public static void markApkForVerify(String apkPath) {
        boolean isProtected = false;
        try {
            String pkg = resolvePackageName(apkPath);
            isProtected = PROTECTED_VERIFY_PKG.equals(pkg);
            if (isProtected) {
                vslog("markApkForVerify: forcing real signature check for " + pkg);
            }
        } catch (Throwable t) {
            vslog("markApkForVerify Error: " + t);
        }
        sVerifyProtected.set(isProtected);
    }

    // Best-effort package name from an APK path via PackageParser's manifest-
    // only parser. The method shape changed across releases, so the known
    // variants are tried by reflection; the first that yields a name wins.
    private static String resolvePackageName(String apkPath) {
        if (apkPath == null || apkPath.isEmpty()) {
            return null;
        }
        File apkFile = new File(apkPath);
        if (!apkFile.exists()) {
            return null;
        }
        try {
            Class<?> parser = Class.forName("android.content.pm.PackageParser");
            // Legacy (<= Android 10): PackageLite parsePackageLite(File, int)
            try {
                Method m = parser.getMethod("parsePackageLite", File.class, int.class);
                String pkg = readPackageNameField(m.invoke(null, apkFile, 0));
                if (pkg != null) {
                    return pkg;
                }
            } catch (NoSuchMethodException ignored) {
            }
            // Modern (Android 11+): parsePackageLite(ParseInput, File, int)
            try {
                Class<?> parseInputCls =
                        Class.forName("android.content.pm.parsing.result.ParseInput");
                Class<?> parseTypeImpl =
                        Class.forName("android.content.pm.parsing.result.ParseTypeImpl");
                Object input = parseTypeImpl.getMethod("forDefaultParsing").invoke(null);
                Method m = parser.getMethod(
                        "parsePackageLite", parseInputCls, File.class, int.class);
                Object result = m.invoke(null, input, apkFile, 0);
                Object lite = result.getClass().getMethod("getResult").invoke(result);
                String pkg = readPackageNameField(lite);
                if (pkg != null) {
                    return pkg;
                }
            } catch (ClassNotFoundException | NoSuchMethodException ignored) {
            }
        } catch (Throwable t) {
            vslog("resolvePackageName Error: " + t);
        }
        return null;
    }

    private static String readPackageNameField(Object lite) {
        if (lite == null) {
            return null;
        }
        try {
            return (String) lite.getClass().getField("packageName").get(lite);
        } catch (NoSuchFieldException e) {
            try {
                return (String) lite.getClass().getMethod("getPackageName").invoke(lite);
            } catch (Throwable ignored) {
            }
        } catch (Throwable ignored) {
        }
        return null;
    }

    public static Object os2_custom_layout() {
        try {
            int xValueStr = getIntGlobal("vs_layout_grid_x");
            int yValueStr = getIntGlobal("vs_layout_grid_y");

            if (xValueStr < 4 || xValueStr > 7) {
                xValueStr = 5;
            }

            if (yValueStr < 6 || yValueStr > 12) {
                yValueStr = 7;
            }

            return new Pair<>(xValueStr, yValueStr);
        } catch (NumberFormatException e) {
            return 5;
        }
    }

    public static int workspaceTopPadding() {
        int value = getIntGlobal("vs_layout_workspaceTopPadding");

        return Math.max(value, 0);

    }

    public static String ForceRegion() {
        return "CN";
    }

    public static void startCts() {
        try {
            Bundle bundle = new Bundle();
            bundle.putLong("invocation_time_ms", SystemClock.elapsedRealtime());
            bundle.putInt("omni.entry_point", 1);
            bundle.putBoolean("vsteam", true);

            @SuppressLint("PrivateApi") Class<?> iVimsClass = Class.forName("com.android.internal.app.IVoiceInteractionManagerService");
            @SuppressLint("PrivateApi") Object vis = Class.forName("android.os.ServiceManager")
                    .getMethod("getService", String.class)
                    .invoke(null, "voiceinteraction");

            @SuppressLint("PrivateApi") Object vims = Class.forName("com.android.internal.app.IVoiceInteractionManagerService$Stub")
                    .getMethod("asInterface", IBinder.class)
                    .invoke(null, vis);

            iVimsClass.getMethod("showSessionFromSession", IBinder.class, Bundle.class, int.class, String.class)
                    .invoke(vims, null, bundle, 7, "hyperOS_home");

        } catch (Exception e) {
            vslog(e.getMessage());
        }
    }
    public static void vibrate(Context context) {
        Vibrator vibrator = (Vibrator) context.getSystemService(Context.VIBRATOR_SERVICE);
        if (vibrator != null) {
            vibrator.vibrate(VibrationEffect.createOneShot(100, VibrationEffect.DEFAULT_AMPLITUDE));
        }
    }


    public static boolean Handle_VS_Action_ColorOS(KeyEvent event) {
        final int keyCode = event.getKeyCode();
        final int action = event.getAction();
        boolean handled = false;

        vslog("Keycode : " + keyCode + ", Action : " + action);

        if (action == KeyEvent.ACTION_DOWN) {
            switch (keyCode) {
                case 769: { // Double press
                    String value = getStringGlobal("spruce_double_press");
                    if (value != null && !value.isEmpty() && !value.equals("None")) {
                        Handle_VS_Action(value, "Spruce Press");
                        handled = true;
                    }
                    break;
                }

                case 767: { // Single press
                    String value = getStringGlobal("spruce_single_press");
                    if (value != null && !value.isEmpty() && !value.equals("None")) {
                        Handle_VS_Action(value, "Spruce Press");
                        handled = true;
                    }
                    break;
                }

                case 781: { // Action press
                    String value = getStringGlobal("action_single_press");
                    if (value != null && !value.isEmpty() && !value.equals("None")) {
                        Handle_VS_Action(value, "Action Press");
                        handled = true;
                    }
                    break;
                }

                case 782: { // Action press
                    String value = getStringGlobal("action_long_press");
                    if (value != null && !value.isEmpty() && !value.equals("None")) {
                        Handle_VS_Action(value, "Long Press");
                        handled = true;
                    }
                    break;
                }

                default:
                    // Không xử lý các key khác
                    break;
            }
        }

        vslog("Handle_VS_Action_ColorOS return: " + handled);
        return handled;
    }

    public static boolean Handle_VS_Action_Boolean(String value) {
        boolean handled = false;
        if (value != null && !value.isEmpty() && !value.equals("None")) {
            Handle_VS_Action(value, "Shortcut");
            handled = true;
        }

        vslog("Handle_VS_Action_Boolean return: " + handled);
        return handled;
    }


    public static void Handle_VS_Action(String function, String action) {
        try {
            if (function != null) {

                openApp(function);

            } else {
                vslog("Handle_VS_Action Error: function is null");
            }
        } catch (Exception e) {
            vslog("Handle_VS_Action Exception: " + e.getClass().getName() + ": " + e.getMessage());
            e.printStackTrace();
        }
    }



    public static void openApp(String action) {
        Context context = getCon(); // getCon() cần trả về context hợp lệ
        if (context == null || action == null || action.isEmpty()) {
            vslog("Invalid context or package name.");
            return;
        }

        try {
            Intent intent = new Intent();
            intent.setComponent(new ComponentName("com.bhlnk.vsteam", "com.bhlnk.vsteam.LaunchTargetActivity"));
            intent.putExtra("action", action);
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            context.startActivity(intent);
            vslog("Requested to LaunchTargetActivity: " + action);
        } catch (Exception e) {
            vslog("Failed to launch app via LaunchTargetActivity: " + e.getMessage());
            e.printStackTrace();
        }
    }



    public static void gemini() {
        try {
            Intent intent = new Intent("android.intent.action.VOICE_COMMAND");
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            Objects.requireNonNull(getCon()).startActivity(intent);
        } catch (ActivityNotFoundException e) {
            try {
                Intent fallbackIntent = Objects.requireNonNull(getCon()).getPackageManager()
                        .getLaunchIntentForPackage("com.google.android.googlequicksearchbox");
                if (fallbackIntent != null) {
                    fallbackIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    getCon().startActivity(fallbackIntent);
                } else {
                    Toast.makeText(getCon(), "Google Search not found", Toast.LENGTH_SHORT).show();
                }
            } catch (Exception ex) {
                Toast.makeText(getCon(), "Unable to launch voice command", Toast.LENGTH_SHORT).show();
            }
        }
    }



    public static void wallet() {
        try {
            Intent intent = new Intent();
            ComponentName componentName = new ComponentName(
                    "com.google.android.apps.walletnfcrel",
                    "com.google.commerce.tapandpay.android.wallet.WalletActivity"
            );
            intent.setComponent(componentName);
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            Objects.requireNonNull(getCon()).startActivity(intent);
        } catch (ActivityNotFoundException e) {
            Toast.makeText(getCon(), "Google Pay is not installed", Toast.LENGTH_SHORT).show();
        } catch (Exception e) {
            Toast.makeText(getCon(), "Unable to open Google Pay Wallet", Toast.LENGTH_SHORT).show();
        }
    }


    private final void handleLongPressEvent() {
        String function = getStrGlobal("long_press_fullscreen_gesture_line");
        if (function != null) {
            if (function.equals("androidv_cts")) {
                boolean result = notifyToStartCtS(1);
                if (result) {
                    vslog("handleLongPressEvent: notifyToStartCtS succeeded.");
                } else {
                    vslog("handleLongPressEvent: notifyToStartCtS failed.");
                }
            } else {
                Handle_VS_Action(function, "long_press_fullscreen_gesture_line");
            }
        }
    }

    private final void handleDoubleClickEvent() {
        String function = getStrGlobal("double_click_fullscreen_gesture_line");
        if (function != null) {
            if (function.equals("androidv_cts")) {
                boolean result = notifyToStartCtS(1);
                if (result) {
                    vslog("handleDoubleClickEvent: notifyToStartCtS succeeded.");
                } else {
                    vslog("handleDoubleClickEvent: notifyToStartCtS failed.");
                }
            } else {
                Handle_VS_Action(function, "long_press_fullscreen_gesture_line");
            }
        }
    }


    /**
     * ColorOS 17 SystemUI, long press on the status bar / quick settings clock
     * (bin/patch_clock_seconds.py). True when the action picked in VSTeam ran; the stock
     * "show seconds" toggle is skipped then.
     */
    public static boolean clockLongPress() {
        try {
            return Handle_VS_Action_Boolean(getStringGlobal("vs_clock_long_press"));
        } catch (Throwable t) {
            return false;
        }
    }

    /**
     * How long the seconds shown by the status bar clock stay on, in milliseconds: the stock
     * five minutes, or with "vs_clock_seconds_no_limit" on a time that is never reached.
     * SystemUI and Settings add it to the current time to get their deadline.
     */
    public static long clockSecondsDuration() {
        try {
            if (getIntGlobal("vs_clock_seconds_no_limit") == 1) {
                return CLOCK_SECONDS_NO_LIMIT_MS;
            }
        } catch (Throwable ignored) {
        }
        return CLOCK_SECONDS_STOCK_MS;
    }

    private static final long CLOCK_SECONDS_STOCK_MS = 5 * 60 * 1000L;
    /** Ten years. */
    private static final long CLOCK_SECONDS_NO_LIMIT_MS = 3650L * 24 * 60 * 60 * 1000;

    public static void longpresstemplate(){
        String function = getStrGlobal("long_press_fullscreen_gesture_line");
        String action = "Long Press Handler";
        Handle_VS_Action(function, action);
    }

    public final void startXiaoAiService(int i, String str) {
        String function = getStrGlobal(str);

        if (function != null) {
            if (function.equals("androidv_cts")) {
                boolean result = notifyToStartCtS(1);
                if (result) {
                    vslog("startXiaoAiService: notifyToStartCtS succeeded for " + str);
                } else {
                    vslog("startXiaoAiService: notifyToStartCtS failed for " + str);
                }
            } else {
                Handle_VS_Action(function, str);
            }
        }
    }

    private static final Map<String, Boolean> featureMap = new HashMap<>();
    static {
        featureMap.put("oplus.software.activity_start_manager", false);
        featureMap.put("oplus.software.art_domestic_isolate", false);
        featureMap.put("oplus.software.clipboard_ai_protect", false);
        featureMap.put("oplus.software.read_app_list_restriction", false);
        featureMap.put("oplus.software.clipboard_notify_enabled", false);
        featureMap.put("oplus.software.pms_app_frozen", false);
        featureMap.put("oplus.software.notification.envelope_assistant_enable", false);
        featureMap.put("oplus.software.get_installed_apps_intercept", false);
        featureMap.put("oplus.software.intercept_lockscreen_service", false);
        featureMap.put("oplus.software.inputmethod.cn", false);
        featureMap.put("oplus.software.background_start_activity_permission", false);
        featureMap.put("oplus.software.power_button_redefine", true);
        featureMap.put("oplus.software.aon_gestureui_side_enable", true);
        featureMap.put("oplus.software.audio.super_volume", true);
        featureMap.put("oplus.software.audio.super_volume_3x", true);
        featureMap.put("oplus.software.audio.super_volume_4x", true);
        featureMap.put("oplus.software.display.pixelworks_enable", true);
        featureMap.put("oplus.software.powerkey_disbale_turnoff_torch", false);
        featureMap.put("oplus.software.hans_restriction_exp", true);
        // AccessCard checks this through OplusFeatureConfigManager before
        // routing an NFC tag to CardDetectedActivity.
        featureMap.put("SYSTEM_HCE_ACCESSCARD", true);
        featureMap.put("com.oplus.nfc.HceAccesscardRegionControl", true);


    }


    public static boolean featureHooking(String feature, boolean sdefault) {
        return Boolean.TRUE.equals(featureMap.getOrDefault(feature, sdefault));
    }


    private static final Map<String, String> HASSELBLAD_TAGS = new HashMap<>();
    static {
        HASSELBLAD_TAGS.put("com.oplus.camera.support.custom.hasselblad.watermark", "1");
        HASSELBLAD_TAGS.put("com.oplus.camera.support.custom.hasselblad.watermark.sellmode.default.open", "1");
        HASSELBLAD_TAGS.put("com.oplus.hasselblad.watermark.guide.support", "0");
        HASSELBLAD_TAGS.put("com.oplus.use.hasselblad.style.support", "1");
        HASSELBLAD_TAGS.put("com.oplus.professional.use.hasselblad.style.support", "0");
    }



    private static final List<String> GALLERY_TAGS = List.of(
            "feature_is_support_lumo_watermark"
    );

    public static boolean galleryhooking(String feature) {
        vslog(feature);
        return GALLERY_TAGS.contains(feature);
    }

    public static String hasselbladHooking(String inputJson) {
        try {
            JSONObject root = new JSONObject(inputJson);

            Object fileDataObj = root.opt("file_data");
            if (!(fileDataObj instanceof JSONArray)) {
                vslog("file_data is not a JSONArray.");
                return inputJson;
            }
            JSONArray fileData = (JSONArray) fileDataObj;

            for (Map.Entry<String, String> entry : HASSELBLAD_TAGS.entrySet()) {
                String vendorTag = entry.getKey();
                String value = entry.getValue();
                boolean found = false;

                for (int i = 0; i < fileData.length(); i++) {
                    JSONObject obj = fileData.optJSONObject(i);
                    if (obj != null && vendorTag.equals(obj.optString("VendorTag"))) {
                        obj.put("Type", "Byte");
                        obj.put("Count", "1");
                        obj.put("Value", value);
                        found = true;
                        break;
                    }
                }

                if (!found) {
                    JSONObject newObj = new JSONObject();
                    newObj.put("VendorTag", vendorTag);
                    newObj.put("Type", "Byte");
                    newObj.put("Count", "1");
                    newObj.put("Value", value);
                    fileData.put(newObj);
                }
            }

            root.put("file_data", fileData);
            return root.toString(2);

        } catch (JSONException e) {
            vslog("JSONException: " + e.toString());
            return inputJson;
        }
    }


    private static Map  <String, String> new_hasselbladHooking(Map<String, String> original) {
        // Nếu original null => tạo map mới chứa toàn bộ hasselblad tags
        if (original == null) {
            return new LinkedHashMap<>(HASSELBLAD_TAGS);
        }

        // Giữ nguyên thứ tự, sao chép những giá trị hiện có
        LinkedHashMap<String, String> result = new LinkedHashMap<>(original);

        // Thêm các tag của Hasselblad nếu chưa tồn tại (không ghi đè)
        for (Map.Entry<String, String> e : HASSELBLAD_TAGS.entrySet()) {
            result.putIfAbsent(e.getKey(), e.getValue());
        }

        return result;
    }

    public static String VSLunarString() {
        if (getBooleanGlobal("vs_qsheader_lunar")) {
            String custom = getStrGlobal("vs_qsheader_lunar_custom");
            if (custom != null && !custom.isEmpty()) {
                return convertSolar2Lunar(custom);
            }

            return convertSolar2Lunar(" (DD/MM)");
        }
        return "";
    }

    private static volatile int cachedVsteamUid = Integer.MIN_VALUE;
    private static final Object uidLock = new Object();

    public static int getVsteamUid() {
        if (cachedVsteamUid != Integer.MIN_VALUE) {
            return cachedVsteamUid;
        }

        synchronized (uidLock) {
            if (cachedVsteamUid != Integer.MIN_VALUE) {
                return cachedVsteamUid;
            }

            // Thực hiện get UID
            cachedVsteamUid = getUidInternal();
            return cachedVsteamUid;
        }
    }

    private static int getUidInternal() {
        String packageName = "com.bhlnk.vsteam";

        try {
            // Method 1: Try với getCon() nhưng có timeout protection
            Context context = getContextWithTimeout();
            if (context != null) {
                PackageManager pm = context.getPackageManager();
                ApplicationInfo info = pm.getApplicationInfo(packageName, 0);
                return info.uid;
            }

            // Method 2: Fallback với reflection
            return getVsteamUidSafeReflection();

        } catch (Exception e) {
            return -1;
        }
    }

    private static Context getContextWithTimeout() {
        try {
            // Set timeout để tránh infinite wait
            return getCon(); // Gọi getCon() nhưng có protection
        } catch (StackOverflowError e) {
            return null; // Nếu bị StackOverflow thì return null
        } catch (Exception e) {
            return null;
        }
    }


    public static int getVsteamUidSafeReflection() {
        String packageName = "com.bhlnk.vsteam";

        try {
            // Sử dụng reflection để get ActivityThread context
            Class<?> activityThread = Class.forName("android.app.ActivityThread");
            Method currentApplication = activityThread.getDeclaredMethod("currentApplication");
            Context context = (Context) currentApplication.invoke(null);

            if (context == null) {
                return -1;
            }

            PackageManager pm = context.getPackageManager();
            ApplicationInfo info = pm.getApplicationInfo(packageName, 0);
            return info.uid;

        } catch (Exception e) {
            return -1;
        }
    }

    public static Icon getSmallIconColor(Context context, StatusBarNotification sbn) {
        // 1. Thử lấy largeIcon
        if (sbn.getNotification().getLargeIcon() != null) {
            Drawable largeDrawable = sbn.getNotification().getLargeIcon().loadDrawable(context);
            if (largeDrawable instanceof BitmapDrawable) {
                Bitmap bmp = ((BitmapDrawable) largeDrawable).getBitmap();
                if (bmp != null && !bmp.isRecycled()) {
                    return Icon.createWithBitmap(bmp);
                }
            }
        }

        // 2. Thử lấy icon ứng dụng (launcher icon)
        try {
            Drawable appIcon = context.getPackageManager().getApplicationIcon(sbn.getPackageName());
            if (appIcon instanceof BitmapDrawable) {
                Bitmap bmp = ((BitmapDrawable) appIcon).getBitmap();
                if (bmp != null && !bmp.isRecycled()) {
                    return Icon.createWithBitmap(bmp);
                }
            }
        } catch (PackageManager.NameNotFoundException e) {
            e.printStackTrace();
        }

        // 3. Nếu tất cả trên lỗi, trả về smallIcon từ notification
        return sbn.getNotification().getSmallIcon();
    }

    public static String HookProp(String prop, String value) {
        try {
            String check = getStrGlobal(prop);
            if (check != null) {
                vslog("HookProp : "+prop + " : " + check);
                return check;
            }

            String result = SystemProperties.get(prop, value);
            if (result == null || result.isEmpty()) {
                return value;
            }

            return result;
        } catch (Exception e) {
            e.printStackTrace();
            return value; // trả về giá trị mặc định nếu có lỗi
        }
    }




    public static String getUpdateString() {
        String quote = getStrGlobal("vs_setting_update_string");
        if (quote != null && !quote.isEmpty()) {
            return quote;
        }

        String Ver = SystemProperties.get("ro.hypervs.version", "6.01.ST");
        String lang = Locale.getDefault().getLanguage(); // ví dụ: "vi" hoặc "en"

        boolean isVietnamese = lang.equalsIgnoreCase("vi");

        if (Ver.contains("PRE")) {
            return isVietnamese ? "Phiên bản xem trước" : "Preview version";
        } else {
            return isVietnamese ? "Phiên bản ổn định" : "Stable version";
        }
    }


    public static String getUpdateStringforXiaomi(Context context) {
        String quote = getStrGlobal("vs_setting_update_string");
        if (quote != null && !quote.isEmpty()) {
            return quote;
        }

        String Ver = SystemProperties.get("ro.hypervs.version", "6.80.ST");
        String DefaultVer = "HyperMushrom " + Ver + " by Mushrom";
        return DefaultVer;
    }

    /**
     * Settings > My device (MiuiMyDeviceSettings.startRuntimeShader): true draws the static
     * background (drawable device_lite_bg) instead of the moving shader. Lite devices always
     * do; VS Tools turns it on when it installs a picked image as that drawable
     * (vsteam.com.info.bgimg overlay).
     */
    public static boolean useInfoImageBg(boolean lite) {
        return lite || getBooleanGlobal("vs_info_bg_image");
    }



    // One device name set in VSTeam Hyper Config, used for both the camera
    // watermark and the EXIF Model. Null while the user has not set one.
    private static String customDeviceName() {
        String name = getStrGlobal("vs_hypervs_device_name");
        if (name == null || name.trim().isEmpty()) {
            return null;
        }
        return name.trim();
    }

    // The watermark has a brand slot and a name slot: the first word fills
    // the brand, the rest the name, as stock does with the market name.
    private static String firstWord(String text) {
        String[] parts = text.trim().split("\\s+");
        return parts.length > 0 ? parts[0] : text;
    }

    private static String afterFirstWord(String text) {
        String[] parts = text.trim().split("\\s+", 2);
        return parts.length > 1 ? parts[1] : "";
    }

    public static String wtmBrand() {
        String custom = customDeviceName();
        if (custom != null) {
            return firstWord(custom);
        }

        // Settings saved by VSTeam builds that still had a brand picker.
        String custom_brand = getStrGlobal("vs_hypervs_watermark_brand");
        if (custom_brand != null) {
            if (custom_brand.equals("Empty")) {
                return "";
            }
            if (!custom_brand.isEmpty()) {
                return custom_brand;
            }
        }

        return firstWord(SystemProperties.get("ro.product.odm.marketname", "VSTeam"));
    }

    public static String wtmName() {
        String custom = customDeviceName();
        if (custom != null) {
            return afterFirstWord(custom);
        }

        String custom_name = getStrGlobal("vs_hypervs_watermark_name");
        if (custom_name != null && !custom_name.isEmpty()) {
            return custom_name;
        }

        return afterFirstWord(SystemProperties.get("ro.product.odm.marketname", "VSTeam"));
    }

    // EXIF Model/Make written by MiuiCamera; untouched while no name is set.
    public static String exifModel(String stock) {
        String custom = customDeviceName();
        return custom != null ? custom : stock;
    }

    public static String exifMake(String stock) {
        String custom = customDeviceName();
        return custom != null ? firstWord(custom) : stock;
    }

    // Called from MiuiCamera's Storage just before an image is written. Most
    // shots are encoded by the camera HAL, which fills Make/Model from system
    // properties, so the tags are rewritten in the finished JPEG instead.
    // Anything that is not a JPEG with those tags is returned untouched.
    public static java.nio.ByteBuffer exifPatch(java.nio.ByteBuffer data) {
        try {
            String model = customDeviceName();
            if (model == null || data == null) {
                return data;
            }
            // Storage writes array()[0 .. limit()), whatever the position is.
            java.nio.ByteBuffer copy = data.duplicate();
            copy.rewind();
            byte[] jpeg = new byte[copy.remaining()];
            copy.get(jpeg);
            byte[] patched = rewriteExifMakeModel(jpeg, firstWord(model), model);
            if (patched == null) {
                return data;
            }
            return java.nio.ByteBuffer.wrap(patched).order(data.order());
        } catch (Throwable t) {
            return data;
        }
    }

    private static final int EXIF_TAG_MAKE = 0x010F;
    private static final int EXIF_TAG_MODEL = 0x0110;
    private static final int EXIF_TYPE_ASCII = 2;

    private static int exifInt(byte[] b, int at, int size, boolean little) {
        int value = 0;
        for (int i = 0; i < size; i++) {
            int shift = little ? i : size - 1 - i;
            value |= (b[at + i] & 0xff) << (8 * shift);
        }
        return value;
    }

    private static void exifPut(byte[] b, int at, int size, boolean little, int value) {
        for (int i = 0; i < size; i++) {
            int shift = little ? i : size - 1 - i;
            b[at + i] = (byte) (value >>> (8 * shift));
        }
    }

    // Returns the JPEG with new Make/Model strings, or null when nothing has to
    // change. The strings are appended to the end of the EXIF block and the two
    // IFD entries repointed, so no other offset inside the block moves.
    private static byte[] rewriteExifMakeModel(byte[] jpeg, String make, String model) {
        if (jpeg.length < 4 || (jpeg[0] & 0xff) != 0xFF || (jpeg[1] & 0xff) != 0xD8) {
            return null;
        }
        int pos = 2;
        while (pos + 4 <= jpeg.length && (jpeg[pos] & 0xff) == 0xFF) {
            int marker = jpeg[pos + 1] & 0xff;
            if (marker == 0xDA || marker == 0xD9) {
                return null;
            }
            int segLen = exifInt(jpeg, pos + 2, 2, false);
            int segEnd = pos + 2 + segLen;
            if (segLen < 2 || segEnd > jpeg.length) {
                return null;
            }
            boolean exif = marker == 0xE1 && segLen >= 16
                    && jpeg[pos + 4] == 'E' && jpeg[pos + 5] == 'x' && jpeg[pos + 6] == 'i'
                    && jpeg[pos + 7] == 'f' && jpeg[pos + 8] == 0 && jpeg[pos + 9] == 0;
            if (!exif) {
                pos = segEnd;
                continue;
            }

            int tiff = pos + 10;
            boolean little = jpeg[tiff] == 'I';
            int ifd = tiff + exifInt(jpeg, tiff + 4, 4, little);
            if (ifd + 2 > segEnd) {
                return null;
            }
            int count = exifInt(jpeg, ifd, 2, little);
            if (ifd + 2 + count * 12 > segEnd) {
                return null;
            }

            byte[] head = java.util.Arrays.copyOfRange(jpeg, 0, segEnd);
            java.io.ByteArrayOutputStream extra = new java.io.ByteArrayOutputStream();
            int tiffLen = segEnd - tiff;
            boolean changed = false;
            for (int i = 0; i < count; i++) {
                int entry = ifd + 2 + i * 12;
                int tag = exifInt(jpeg, entry, 2, little);
                if (tag != EXIF_TAG_MAKE && tag != EXIF_TAG_MODEL) {
                    continue;
                }
                byte[] text = ((tag == EXIF_TAG_MAKE ? make : model) + "\0")
                        .getBytes(java.nio.charset.StandardCharsets.UTF_8);
                int oldCount = exifInt(jpeg, entry + 4, 4, little);
                int oldAt = oldCount <= 4 ? entry + 8 : tiff + exifInt(jpeg, entry + 8, 4, little);
                if (oldCount == text.length && oldAt + oldCount <= segEnd
                        && java.util.Arrays.equals(
                                java.util.Arrays.copyOfRange(jpeg, oldAt, oldAt + oldCount), text)) {
                    continue;
                }
                exifPut(head, entry + 2, 2, little, EXIF_TYPE_ASCII);
                exifPut(head, entry + 4, 4, little, text.length);
                if (text.length <= 4) {
                    exifPut(head, entry + 8, 4, little, 0);
                    System.arraycopy(text, 0, head, entry + 8, text.length);
                } else {
                    exifPut(head, entry + 8, 4, little, tiffLen + extra.size());
                    extra.write(text, 0, text.length);
                    if ((extra.size() & 1) != 0) {
                        extra.write(0);
                    }
                }
                changed = true;
            }
            if (!changed || segLen + extra.size() > 0xFFFF) {
                return null;
            }

            exifPut(head, pos + 2, 2, false, segLen + extra.size());
            byte[] added = extra.toByteArray();
            byte[] out = new byte[jpeg.length + added.length];
            System.arraycopy(head, 0, out, 0, segEnd);
            System.arraycopy(added, 0, out, segEnd, added.length);
            System.arraycopy(jpeg, segEnd, out, segEnd + added.length, jpeg.length - segEnd);
            return out;
        }
        return null;
    }


    public static void updateHasselbladFlags() {

        boolean isRealme = SystemProperties.getBoolean("ro.colourvs.isRealme", false);
        boolean isOppo = SystemProperties.getBoolean("ro.colourvs.isOppo", false);


        //Skip hasselblad on realme
        if (!isRealme && !isOppo) {

            @SuppressLint("SdCardPath") File xmlFile = new File("/data/user/0/com.coloros.gallery3d/shared_prefs/business_featureSwitch_config.xml");

            // Only the gallery process owns this file; from com.oplus.camera
            // (or before gallery first writes it) open() fails with EACCES/ENOENT.
            if (!xmlFile.canRead() || !xmlFile.canWrite()) {
                return;
            }

            try {
                DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
                factory.setIgnoringComments(true);
                factory.setIgnoringElementContentWhitespace(true);
                DocumentBuilder builder = factory.newDocumentBuilder();
                Document doc = builder.parse(xmlFile);

                Element root = doc.getDocumentElement();

                String[] targetNames = {
                        "business_featureSwitch_is_hassel_watermark_supported",
                        "business_featureSwitch_is_support_hassel_device",
                        "business_featureSwitch_is_support_lumo_watermark",
                        "featureSwitch_is_support_lumo_watermark",
                        "feature_is_support_lumo_watermark",
                        "business_featureSwitch_feature_is_support_lumo_watermark",
                        "business_featureSwitch_is_use_hassel_watermark_bh"
                };

                for (String name : targetNames) {
                    // Tìm phần tử boolean có name tương ứng
                    Node targetNode = null;
                    NodeList list = root.getElementsByTagName("boolean");
                    for (int i = 0; i < list.getLength(); i++) {
                        Element e = (Element) list.item(i);
                        if (name.equals(e.getAttribute("name"))) {
                            targetNode = e;
                            break;
                        }
                    }

                    if (targetNode != null) {
                        // Nếu tồn tại -> cập nhật value="true"
                        ((Element) targetNode).setAttribute("value", "true");
                    } else {
                        // Nếu chưa có -> thêm mới phần tử boolean
                        Element newBool = doc.createElement("boolean");
                        newBool.setAttribute("name", name);
                        newBool.setAttribute("value", "true");
                        root.appendChild(newBool);
                    }
                }

                // 4. Ghi lại file XML
                TransformerFactory transformerFactory = TransformerFactory.newInstance();
                Transformer transformer = transformerFactory.newTransformer();
                transformer.setOutputProperty(OutputKeys.INDENT, "yes");
                transformer.setOutputProperty(OutputKeys.ENCODING, "utf-8");
                transformer.setOutputProperty("{http://xml.apache.org/xslt}indent-amount", "4");

                transformer.transform(new DOMSource(doc), new StreamResult(xmlFile));

                vslog("Hasselblad file : " + xmlFile.getAbsolutePath());

            } catch (Exception e) {
                e.printStackTrace();
            }
        }
    }


    public static List<Pair<Integer, Pair<Integer, Integer>>> getLayoutNormal() {
        List<Pair<Integer, Pair<Integer, Integer>>> layouts = new ArrayList<>();

        // Tạo tất cả layout từ 3x3 đến 9x9
        for (int cols = 3; cols <= 9; cols++) {
            for (int rows = 3; rows <= 9; rows++) {
                // Pair<Row, RowForWordless> - thường thì Row và RowForWordless giống nhau
                Pair<Integer, Integer> rowPair = Pair.create(rows, rows);
                // Pair<Column, Pair<Row, RowForWordless>>
                Pair<Integer, Pair<Integer, Integer>> layout = Pair.create(cols, rowPair);
                layouts.add(layout);
            }
        }

        return layouts;
    }

    /** Cùng giá trị LocalProductInfo.PURCHASE_STATUS_TRIAL */
    private static final int THEME_PURCHASE_STATUS_TRIAL = 1;
    /** Cùng giá trị LocalProductInfo.PURCHASE_STATUS_BUY */
    private static final int THEME_PURCHASE_STATUS_BUY = 2;
    private static final Uri T_LOCAL_THEME_URI =
            Uri.parse("content://com.nearme.themespace/t_local_theme");

    /**
     * Một lệnh update: mọi dòng đang trial → BUY.
     *
     * @return số dòng cập nhật do ContentProvider trả về
     */
    public static int markAllTrialsAsPurchasedOneShot(Application application, Context context) {
        Context base = null;
        if (context != null) {
            Context appCtx = context.getApplicationContext();
            base = appCtx != null ? appCtx : context;
        }
        if (base == null && application != null) {
            base = application;
        }
        if (base == null) {
            return 0;
        }

        ContentValues values = new ContentValues();
        values.put("purchase_status", THEME_PURCHASE_STATUS_BUY);

        String where = "purchase_status = ?";
        String[] whereArgs = new String[]{
                String.valueOf(THEME_PURCHASE_STATUS_TRIAL)
        };

        ContentResolver cr = base.getContentResolver();
        return cr.update(T_LOCAL_THEME_URI, values, where, whereArgs);
    }

}
