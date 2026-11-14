//
// Decompiled by Jadx - 1090ms
//
package com.android.vsteam;

import static com.android.vsteam.Toolbox.getBooleanGlobal;
import static com.android.vsteam.Toolbox.updateHasselbladFlags;
import static com.android.vsteam.Toolbox.vslog;

import android.app.Application;
import android.content.ContentResolver;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.SystemClock;
import android.os.SystemProperties;
import android.provider.Settings;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileOutputStream;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.IOException;
import java.lang.reflect.Field;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

public class VsProps {

    private static final Map<String, Object> GooglePhoto = new HashMap<String, Object>() {{
        put("BRAND", "google");
        put("MANUFACTURER", "Google");
        put("DEVICE", "marlin");
        put("PRODUCT", "marlin");
        put("MODEL", "Pixel XL");
        put("FINGERPRINT", "google/marlin/marlin:10/QP1A.191005.007.A3/5972272:user/release-keys");
    }};

    private static final Map<String, Object> PixelLatest = new HashMap<String, Object>() {{
        put("BRAND", "google");
        put("BOARD", "mustang");
        put("MANUFACTURER", "Google");
        put("DEVICE", "mustang");
        put("PRODUCT", "mustang");
        put("HARDWARE", "mustang");
        put("MODEL", "Pixel 10 Pro XL");
        put("ID", "BD3A.250721.001.B7");
        put("FINGERPRINT", "google/mustang/mustang:16/BD3A.250721.001.B7/13955164:user/release-keys");
    }};


    private static final Map<String, Object> Xiaomi17U = new HashMap<String, Object>() {{
        put("DEVICE", "nezha");
    }};


    // MediaEditor offers the Leica watermarks and filters only to a Leica
    // model of brand Xiaomi (Redmi/POCO never qualify).
    private static final Map<String, Object> XiaomiLeica = new HashMap<String, Object>() {{
        put("DEVICE", "nezha");
        put("BRAND", "Xiaomi");
    }};


    private static final Map<String, Object> OppoGlobal = new HashMap<String, Object>() {{
        put("MODEL", "CPH2841");
        put("BRAND", "oppo");
        put("MANUFACTURER", "oppo");
    }};

    private static final List<String> AIPackage = Arrays.asList(
            "com.oplus.pantanal.ums",
            "com.oplus.ai.magicstudio",
            "com.coloros.accessibilityassistant",
            "com.oplus.aiunit",
            "com.oplus.aimemory",
            "com.oplus.aiassistant",
            "com.oplus.aiwriter",
            "com.oplus.aiwidget",
            "com.coloros.sceneservice"
    );

    private static final Map<String, Object> Bhlnk = new HashMap<String, Object>() {{
        put("MODEL", "BHLNK");
    }};

    // Cache giúp giảm số lần đọc Settings.Global và giảm cấp phát do reflection.
    // Giới hạn size + TTL để tránh phình RAM khi setProps chạy liên tục.
    private static final long SETTINGS_CACHE_TTL_MS = 1500L;
    private static final int SETTINGS_CACHE_MAX = 128;
    private static final String SPOOF_JSON_PREFIX = "vs_spoof_";

    private static final Map<String, SettingsCacheEntry> sSettingsStringCache =
            Collections.synchronizedMap(new LinkedHashMap<String, SettingsCacheEntry>(SETTINGS_CACHE_MAX, 0.75f, true) {
                @Override
                protected boolean removeEldestEntry(Map.Entry<String, SettingsCacheEntry> eldest) {
                    return size() > SETTINGS_CACHE_MAX;
                }
            });

    private static final class SettingsCacheEntry {
        final String value;
        final long timeMs;

        SettingsCacheEntry(String value, long timeMs) {
            this.value = value;
            this.timeMs = timeMs;
        }
    }

    private static String getGlobalStringCached(ContentResolver contentResolver, String key) {
        if (key == null) return null;
        long now = SystemClock.elapsedRealtime();
        SettingsCacheEntry entry = sSettingsStringCache.get(key);
        if (entry != null && now - entry.timeMs <= SETTINGS_CACHE_TTL_MS) {
            return entry.value;
        }
        String value = Settings.Global.getString(contentResolver, key);
        sSettingsStringCache.put(key, new SettingsCacheEntry(value, now));
        return value;
    }

    private static final ConcurrentHashMap<String, Field> sBuildFieldCache = new ConcurrentHashMap<>();
    private static final ConcurrentHashMap<String, Field> sBuildVersionFieldCache = new ConcurrentHashMap<>();
    private static final ConcurrentHashMap<String, Field> sClassFieldCache = new ConcurrentHashMap<>();

    private static Field getCachedBuildField(String fieldName) throws NoSuchFieldException {
        Field cached = sBuildFieldCache.get(fieldName);
        if (cached != null) return cached;
        Field field = Build.class.getDeclaredField(fieldName);
        field.setAccessible(true);
        sBuildFieldCache.put(fieldName, field);
        return field;
    }

    private static Field getCachedBuildVersionField(String fieldName) throws NoSuchFieldException {
        Field cached = sBuildVersionFieldCache.get(fieldName);
        if (cached != null) return cached;
        Field field = Build.VERSION.class.getDeclaredField(fieldName);
        field.setAccessible(true);
        sBuildVersionFieldCache.put(fieldName, field);
        return field;
    }

    private static Field getCachedClassField(String className, String fieldName) throws ClassNotFoundException, NoSuchFieldException {
        String cacheKey = className + "#" + fieldName;
        Field cached = sClassFieldCache.get(cacheKey);
        if (cached != null) return cached;
        Class<?> clazz = Class.forName(className);
        Field field = clazz.getDeclaredField(fieldName);
        field.setAccessible(true);
        sClassFieldCache.put(cacheKey, field);
        return field;
    }

    private static boolean applyPackageSpoofJson(String packageName, ContentResolver contentResolver) {
        String json = getGlobalStringCached(contentResolver, SPOOF_JSON_PREFIX + packageName);
        if (json == null || json.isEmpty()) return false;
        try {
            JSONObject profile = new JSONObject(json);
            if (!profile.optBoolean("enabled", true)) {
                return false;
            }
            applyAdvancedProfileObject("vs_spoof_", packageName, profile);
            return true;
        } catch (JSONException e) {
            vslog("Spoof JSON " + packageName + " invalid : " + e.getMessage());
            return false;
        }
    }

    private static void handleTftPatch(ContentResolver contentResolver, String packageName) throws IOException {
        String tftPatch;
        List<String> TFTGame = Arrays.asList("com.riotgames.league.teamfighttactics", "com.riotgames.league.teamfighttacticstw", "com.riotgames.league.teamfighttacticsvn");
        if (!TFTGame.contains(packageName) || (tftPatch = Settings.Global.getString(contentResolver, "vs_gconf_tft_120")) == null) {
            return;
        }
        String searchString = "0".equals(tftPatch) ? "WaitForVerticalSync=1" : "WaitForVerticalSync=0";
        String replacementString = "0".equals(tftPatch) ? "WaitForVerticalSync=0" : "WaitForVerticalSync=1";
        String filePath = "/data/data/" + packageName + "/no_backup/Config/Game.cfg";
        File file = new File(filePath);
        if (file.exists()) {
            vslog("Found file: " + filePath);
            StringBuilder content = new StringBuilder();
            boolean modified = false;
            BufferedReader reader = new BufferedReader(new FileReader(file));
            while (true) {
                try {
                    String readLine = reader.readLine();
                    String line = readLine;
                    if (readLine == null) {
                        break;
                    }
                    if (line.contains(searchString)) {
                        line = line.replace(searchString, replacementString);
                        modified = true;
                    }
                    content.append(line).append(System.lineSeparator());
                } catch (Throwable th) {
                    try {
                        reader.close();
                    } catch (Throwable th2) {
                        th.addSuppressed(th2);
                    }
                    throw th;
                }
            }
            reader.close();
            if (modified) {
                BufferedWriter writer = new BufferedWriter(new FileWriter(file));
                try {
                    writer.write(content.toString());
                    vslog("Modified file: " + filePath);
                    writer.close();
                    return;
                } catch (Throwable th3) {
                    try {
                        writer.close();
                    } catch (Throwable th4) {
                        th3.addSuppressed(th4);
                    }
                    throw th3;
                }
            }
            vslog("No changes made to file: " + filePath);
        }
    }



    public static void getSpoofByPackageName(String json, String packageName) {
        String str;
        String str2;
        String str3 = "packageObj";
        if (json == null || json.isEmpty()) {
            return;
        }
        Map<String, Object> result = new HashMap<>();
        try {
            JSONArray jsonArray = new JSONArray(json);
            int i = 0;
            while (i < jsonArray.length()) {
                JSONObject profile = jsonArray.getJSONObject(i);
                if (profile.has(str3) && (profile.get(str3) instanceof JSONArray)) {
                    JSONArray packageObjArray = profile.getJSONArray(str3);
                    int j = 0;
                    while (j < packageObjArray.length()) {
                        String pkgValue = packageObjArray.getString(j);
                        if (!pkgValue.equals(packageName)) {
                            str = str3;
                        } else {
                            JSONObject spoof = profile.getJSONObject("spoof");
                            int i2 = 0;
                            String[] fields = {"brand", "model", "manufacturer"};
                            int length = fields.length;
                            while (i2 < length) {
                                String field = fields[i2];
                                if (!spoof.has(field)) {
                                    str2 = str3;
                                } else {
                                    str2 = str3;
                                    result.put(field.toUpperCase(), spoof.getString(field));
                                }
                                i2++;
                                str3 = str2;
                            }
                            str = str3;
                            spoofFields_local(packageName, result);
                        }
                        j++;
                        str3 = str;
                    }
                }
                i++;
            }
        } catch (JSONException e) {
            e.printStackTrace();
        }
    }

    public static void setProps(String packageName, String processName, String versionName, ContentResolver contentResolver) {
        if (packageName == null || packageName.isEmpty()) {
            return;
        }

        //Advanced
        try {
            if (applyPackageSpoofJson(packageName, contentResolver)) {
                return;
            }
        } catch (Exception e) {
            vslog("Spoof JSON " + packageName + " failed : " + e.getMessage());
        }

        //Hasselblad patch
        try {
            if (packageName.equals("com.coloros.gallery3d") ||  packageName.equals("com.oplus.camera")) {
                updateHasselbladFlags();
            }

        } catch (Exception e3) {
            vslog("Hasselblad patch " + packageName + " failed : " + e3.getMessage());
        }


        //Colour AI Fixes
        try {

            if (AIPackage.contains(packageName)) {
                spoofFields_local(packageName, OppoGlobal);
                return;
            }

            if (packageName.equals("com.coloros.smartsidebar")) {
                spoofFields_local(packageName, Bhlnk);
            }


        } catch (Exception e3) {
            vslog("Colour AI Fixes " + packageName + " failed : " + e3.getMessage());
        }

        final Set<String> GLOBAL_BUILD_PACKAGES = new HashSet<>(
                Arrays.asList(
                        "com.lbe.security.miui",
                        "com.miui.weather2",
                        "com.android.soundrecorder",
                        "com.miui.mediaeditor"
                )
        );

        try {
            if (GLOBAL_BUILD_PACKAGES.contains(packageName)) {

                // Optional extra condition for Weather
                if ("com.miui.weather2".equals(packageName)
                        && (versionName == null || !versionName.startsWith("G-"))) {
                    return;
                }

                if ("com.android.soundrecorder".equals(packageName))
                {
                    spoofFields_local(packageName, Xiaomi17U);
                    return;
                }

                if ("com.miui.mediaeditor".equals(packageName)) {
                    spoofFields_local(packageName, XiaomiLeica);
                }

                setPropValue("IS_GLOBAL_BUILD", true, "miui.os.Build");
                setPropValue("IS_INTERNATIONAL_BUILD", true, "miui.os.Build");
                return;
            }

        } catch (Exception e) {
            vslog("Global build fixes " + packageName + " failed : " + e.getMessage());
        }

        //Fix mishare
        try {
            if ("com.miui.mishare.connectivity".equals(packageName)) {
                Settings.System.putInt(contentResolver, "mishare_cta_agree", 1);
                return;
            }

        } catch (Exception e3) {
            vslog("mishare " + packageName + " failed : " + e3.getMessage());
        }


        //Play Integrity
        try {
            final String PACKAGE_GMS = "com.google.android.gms";
            final String PROCESS_GMS_UNSTABLE = PACKAGE_GMS + ".unstable";

            boolean isGmsUnstable =
                    PACKAGE_GMS.equals(packageName)
                            && PROCESS_GMS_UNSTABLE.equals(processName);

            boolean isGmsPersistent =
                    PACKAGE_GMS.equals(packageName)
                            && PACKAGE_GMS.equals(processName);

            boolean isVending =
                    "com.android.vending".equals(packageName);

            List<String> fieldNames1 = Arrays.asList(
                    "MANUFACTURER",
                    "MODEL",
                    "FINGERPRINT",
                    "BRAND",
                    "PRODUCT",
                    "DEVICE",
                    "ID",
                    "TYPE",
                    "TAGS"
            );

            List<String> fieldNames2 = Arrays.asList(
                    "SECURITY_PATCH",
                    "RELEASE",
                    "INCREMENTAL"
            );

            if (isGmsUnstable || isGmsPersistent) {
                spoofFields("vs_pi_", packageName, contentResolver, fieldNames1);
                spoofFields_version("vs_pi_", packageName, contentResolver, fieldNames2);
                return;
            }


            // Play Store chỉ spoof khi bật setting
            if (isVending
                    && Toolbox.getGlobalSettingAsBoolean(contentResolver, "vs_bypass_newcheck")) {

                spoofFields("vs_pi_", packageName, contentResolver, fieldNames1);
                spoofFields_version("vs_pi_", packageName, contentResolver, fieldNames2);
                return;
            }

        } catch (IllegalArgumentException | SecurityException e) {
            vslog("PI " + packageName + " failed : " + e.getMessage());
        }


        //Google spoof apps
        try {
            if ("com.google.android.apps.photos".equals(packageName)) {
                spoofFields_local(packageName, GooglePhoto);
                return;
            }

            List<String> ggapp = Arrays.asList(
                    "com.google.android.googlequicksearchbox",
                    "com.google.pixel.livewallpaper",
                    "com.google.android.apps.emojiwallpaper",
                    "com.google.android.apps.wallpaper",
                    "com.google.android.apps.aiwallpapers"
            );

            if (ggapp.contains(packageName)) {
                spoofFields_local(packageName, PixelLatest);
                return;
            }

        } catch (Exception e3) {
            vslog("PP " + packageName + " failed : " + e3.getMessage());
        }


        //Game profile
        try {
            handleTftPatch(contentResolver, packageName);
            getSpoofByPackageName(Settings.Global.getString(contentResolver, "vs_game_profile"), packageName);
        } catch (Exception e4) {
            vslog("GP " + packageName + " failed : " + e4.getMessage());
        }


    }




    private static void spoofFields(String prefix, String packageName, ContentResolver contentResolver, List<String> fieldNames) {
        for (int i = 0; i < fieldNames.size(); i++) {
            String fieldName = fieldNames.get(i);
            if (fieldName == null || fieldName.isEmpty()) continue;
            String key = prefix + fieldName;
            String value = getGlobalStringCached(contentResolver, key);
            if (value == null || value.isEmpty()) continue;

            try {
                Field field = getCachedBuildField(fieldName);
                field.set(null, value);
                // Log giá trị hợp lệ (tránh tạo chuỗi log cho null/empty).
                vslog("VP " + packageName + " | " + key + " : " + value);


            } catch (IllegalAccessException | NoSuchFieldException e) {
                vslog(e.getMessage());
            } catch (IllegalArgumentException e) {
                // Trường hợp field không đúng kiểu (dù hiện tại đều là String).
                vslog(e.getMessage());
            }
        }
    }

    private static void spoofFields_version(String prefix, String packageName, ContentResolver contentResolver, List<String> fieldNames) {
        for (int i = 0; i < fieldNames.size(); i++) {
            String fieldName = fieldNames.get(i);
            if (fieldName == null || fieldName.isEmpty()) continue;
            String key = prefix + fieldName;
            String value = getGlobalStringCached(contentResolver, key);
            if (value == null || value.isEmpty()) continue;

            try {
                Field field = getCachedBuildVersionField(fieldName);
                field.set(null, value);
                vslog("VP " + packageName + " | " + key + " : " + value);
            } catch (IllegalAccessException | NoSuchFieldException e) {
                vslog(e.getMessage());
            } catch (IllegalArgumentException e) {
                vslog(e.getMessage());
            }
        }
    }

    private static void spoofFieldsint_version(String prefix, String packageName, ContentResolver contentResolver, List<String> fieldNames) {
        for (int i = 0; i < fieldNames.size(); i++) {
            String fieldName = fieldNames.get(i);
            if (fieldName == null || fieldName.isEmpty()) continue;
            String key = prefix + fieldName;
            String value = getGlobalStringCached(contentResolver, key);
            if (value == null || value.isEmpty()) continue;

            try {
                int intValue = Integer.parseInt(value);
                Field field = getCachedBuildVersionField(fieldName);
                field.setInt(null, intValue);
                vslog("VP " + packageName + " | " + key + " : " + intValue);
            } catch (NumberFormatException e) {
                vslog("Invalid integer value for " + fieldName + ": " + value);
            } catch (IllegalAccessException | NoSuchFieldException e) {
                vslog(e.getMessage());
            } catch (IllegalArgumentException e) {
                vslog(e.getMessage());
            }
        }
    }

    private static void setPropValue(String key, Object value, String className) {
        try {
            Field field = getCachedClassField(className, key);
            field.set(null, value);
        } catch (ClassNotFoundException | IllegalAccessException | NoSuchFieldException e) {
            vslog("VP " + key + " failed : " + e.getMessage());
        }
    }

    private static void applyAdvancedProfileObject(String prefix, String packageName, JSONObject profile) throws JSONException {
        JSONObject buildProps = firstJsonObject(
                profile.optJSONObject("build"),
                profile.optJSONObject("props"),
                profile.optJSONObject("build_props")
        );
        if (buildProps == null) {
            buildProps = profile;
        }

        spoofJsonBuildFields(prefix, packageName, buildProps, Arrays.asList("MANUFACTURER", "MODEL", "FINGERPRINT", "BRAND", "PRODUCT", "DEVICE", "ID", "TYPE", "TAGS"));

        JSONObject versionProps = firstJsonObject(
                profile.optJSONObject("version"),
                profile.optJSONObject("build_version"),
                profile.optJSONObject("version_props")
        );
        if (versionProps != null) {
            spoofJsonBuildVersionFields(prefix, packageName, versionProps, Arrays.asList("SECURITY_PATCH", "RELEASE", "INCREMENTAL"));
            spoofJsonBuildVersionIntFields(prefix, packageName, versionProps, Collections.singletonList("SDK_INT"));
        } else {
            spoofJsonBuildVersionFields(prefix, packageName, profile, Arrays.asList("SECURITY_PATCH", "RELEASE", "INCREMENTAL"));
            spoofJsonBuildVersionIntFields(prefix, packageName, profile, Collections.singletonList("SDK_INT"));
        }

        applyMiuiBuildFlags(prefix, packageName, profile.optJSONObject("miui"));

        Object customProps = firstJsonValue(profile, "custom_props", "CUSTOM_PROPS", "customProps");
        if (customProps instanceof JSONArray) {
            JSONArray props = (JSONArray) customProps;
            for (int i = 0; i < props.length(); i++) {
                spoofCustomFieldObject(prefix, packageName, props.getJSONObject(i));
            }
        } else if (customProps instanceof JSONObject) {
            spoofCustomFieldMap(prefix, packageName, (JSONObject) customProps);
        }
    }

    // "miui": {"IS_INTERNATIONAL_BUILD": bool, "IS_TABLET": bool} — key vắng mặt thì giữ giá trị thật.
    private static void applyMiuiBuildFlags(String prefix, String packageName, JSONObject miui) {
        if (miui == null) return;
        if (miui.has("IS_INTERNATIONAL_BUILD")) {
            boolean international = miui.optBoolean("IS_INTERNATIONAL_BUILD");
            // Giống GLOBAL_BUILD_PACKAGES: nhiều app kiểm tra IS_GLOBAL_BUILD thay vì IS_INTERNATIONAL_BUILD
            setPropValue("IS_INTERNATIONAL_BUILD", international, "miui.os.Build");
            setPropValue("IS_GLOBAL_BUILD", international, "miui.os.Build");
            vslog("VP " + packageName + " | " + prefix + "miui IS_INTERNATIONAL_BUILD : " + international);
        }
        if (miui.has("IS_TABLET")) {
            boolean tablet = miui.optBoolean("IS_TABLET");
            setPropValue("IS_TABLET", tablet, "miui.os.Build");
            vslog("VP " + packageName + " | " + prefix + "miui IS_TABLET : " + tablet);
        }
    }

    private static void spoofJsonBuildFields(String prefix, String packageName, JSONObject props, List<String> fieldNames) {
        for (String fieldName : fieldNames) {
            Object value = optJsonField(props, fieldName);
            if (value == null || value == JSONObject.NULL) continue;
            try {
                Field field = getCachedBuildField(fieldName);
                field.set(null, String.valueOf(value));
                vslog("VP " + packageName + " | " + prefix + fieldName + " : " + value);
            } catch (IllegalAccessException | NoSuchFieldException | IllegalArgumentException e) {
                vslog("VP " + packageName + " " + fieldName + " failed : " + e.getMessage());
            }
        }
    }

    private static void spoofJsonBuildVersionFields(String prefix, String packageName, JSONObject props, List<String> fieldNames) {
        for (String fieldName : fieldNames) {
            Object value = optJsonField(props, fieldName);
            if (value == null || value == JSONObject.NULL) continue;
            try {
                Field field = getCachedBuildVersionField(fieldName);
                field.set(null, String.valueOf(value));
                vslog("VP " + packageName + " | " + prefix + fieldName + " : " + value);
            } catch (IllegalAccessException | NoSuchFieldException | IllegalArgumentException e) {
                vslog("VP " + packageName + " " + fieldName + " failed : " + e.getMessage());
            }
        }
    }

    private static void spoofJsonBuildVersionIntFields(String prefix, String packageName, JSONObject props, List<String> fieldNames) {
        for (String fieldName : fieldNames) {
            Object value = optJsonField(props, fieldName);
            if (value == null || value == JSONObject.NULL) continue;
            try {
                int intValue = value instanceof Number ? ((Number) value).intValue() : Integer.parseInt(String.valueOf(value));
                Field field = getCachedBuildVersionField(fieldName);
                field.setInt(null, intValue);
                vslog("VP " + packageName + " | " + prefix + fieldName + " : " + intValue);
            } catch (NumberFormatException e) {
                vslog("Invalid integer value for " + fieldName + ": " + value);
            } catch (IllegalAccessException | NoSuchFieldException | IllegalArgumentException e) {
                vslog("VP " + packageName + " " + fieldName + " failed : " + e.getMessage());
            }
        }
    }

    private static void spoofCustomFields(String prefix, String packageName, ContentResolver contentResolver) {
        String json = getGlobalStringCached(contentResolver, prefix + "CUSTOM_PROPS");
        if (json == null || json.isEmpty()) {
            json = getGlobalStringCached(contentResolver, prefix + "custom_props");
        }
        if (json == null || json.isEmpty()) return;

        try {
            Object parsed = json.trim().startsWith("[") ? new JSONArray(json) : new JSONObject(json);
            if (parsed instanceof JSONArray) {
                JSONArray props = (JSONArray) parsed;
                for (int i = 0; i < props.length(); i++) {
                    spoofCustomFieldObject(prefix, packageName, props.getJSONObject(i));
                }
            } else {
                spoofCustomFieldMap(prefix, packageName, (JSONObject) parsed);
            }
        } catch (JSONException e) {
            vslog("VP " + packageName + " custom props invalid JSON : " + e.getMessage());
        }
    }

    private static void spoofCustomFieldMap(String prefix, String packageName, JSONObject propsByClass) throws JSONException {
        JSONArray classNames = propsByClass.names();
        if (classNames == null) return;
        for (int i = 0; i < classNames.length(); i++) {
            String className = classNames.getString(i);
            JSONObject fields = propsByClass.optJSONObject(className);
            if (fields == null) continue;
            JSONArray fieldNames = fields.names();
            if (fieldNames == null) continue;
            for (int j = 0; j < fieldNames.length(); j++) {
                String fieldName = fieldNames.getString(j);
                Object value = fields.opt(fieldName);
                String type = null;
                if (value instanceof JSONObject) {
                    JSONObject valueObj = (JSONObject) value;
                    type = firstNonEmpty(valueObj.optString("type", null), valueObj.optString("valueType", null));
                    value = valueObj.opt("value");
                }
                spoofCustomField(prefix, packageName, className, fieldName, type, value);
            }
        }
    }

    private static void spoofCustomFieldObject(String prefix, String packageName, JSONObject prop) {
        String className = firstNonEmpty(
                prop.optString("className", null),
                prop.optString("class", null),
                prop.optString("target", null)
        );
        String fieldName = firstNonEmpty(
                prop.optString("field", null),
                prop.optString("key", null),
                prop.optString("name", null)
        );
        String type = firstNonEmpty(prop.optString("type", null), prop.optString("valueType", null));
        Object value = prop.opt("value");
        spoofCustomField(prefix, packageName, className, fieldName, type, value);
    }

    private static void spoofCustomField(String prefix, String packageName, String className, String fieldName, String type, Object rawValue) {
        if (className == null || className.isEmpty() || fieldName == null || fieldName.isEmpty()
                || rawValue == null || rawValue == JSONObject.NULL) {
            return;
        }

        try {
            Field field = getCachedClassField(className, fieldName);
            Object value = convertCustomValue(rawValue, type, field.getType());
            field.set(null, value);
            vslog("VP " + packageName + " | " + prefix + "custom_props " + className + "." + fieldName + " : " + value);
        } catch (ClassNotFoundException | IllegalAccessException | NoSuchFieldException | IllegalArgumentException e) {
            vslog("VP " + packageName + " custom " + className + "." + fieldName + " failed : " + e.getMessage());
        }
    }

    private static Object convertCustomValue(Object rawValue, String type, Class<?> fieldType) {
        String typeName = type == null ? "" : type.trim().toLowerCase();
        if (typeName.isEmpty()) {
            if (fieldType == Boolean.TYPE || fieldType == Boolean.class) typeName = "boolean";
            else if (fieldType == Integer.TYPE || fieldType == Integer.class) typeName = "int";
            else if (fieldType == Long.TYPE || fieldType == Long.class) typeName = "long";
            else if (fieldType == Float.TYPE || fieldType == Float.class) typeName = "float";
            else if (fieldType == Double.TYPE || fieldType == Double.class) typeName = "double";
            else typeName = "string";
        }

        String value = String.valueOf(rawValue);
        switch (typeName) {
            case "bool":
            case "boolean":
                if (rawValue instanceof Boolean) return rawValue;
                return "1".equals(value) || "true".equalsIgnoreCase(value);
            case "int":
            case "integer":
                if (rawValue instanceof Number) return ((Number) rawValue).intValue();
                return Integer.parseInt(value);
            case "long":
                if (rawValue instanceof Number) return ((Number) rawValue).longValue();
                return Long.parseLong(value);
            case "float":
                if (rawValue instanceof Number) return ((Number) rawValue).floatValue();
                return Float.parseFloat(value);
            case "double":
                if (rawValue instanceof Number) return ((Number) rawValue).doubleValue();
                return Double.parseDouble(value);
            default:
                return value;
        }
    }

    private static String firstNonEmpty(String... values) {
        if (values == null) return null;
        for (String value : values) {
            if (value != null && !value.isEmpty()) return value;
        }
        return null;
    }

    private static JSONArray firstJsonArray(JSONArray... values) {
        if (values == null) return null;
        for (JSONArray value : values) {
            if (value != null) return value;
        }
        return null;
    }

    private static JSONObject firstJsonObject(JSONObject... values) {
        if (values == null) return null;
        for (JSONObject value : values) {
            if (value != null) return value;
        }
        return null;
    }

    private static Object firstJsonValue(JSONObject object, String... keys) {
        if (object == null || keys == null) return null;
        for (String key : keys) {
            if (key != null && object.has(key)) {
                return object.opt(key);
            }
        }
        return null;
    }

    private static Object optJsonField(JSONObject object, String fieldName) {
        if (object == null || fieldName == null) return null;
        if (object.has(fieldName)) return object.opt(fieldName);

        String lower = fieldName.toLowerCase();
        if (object.has(lower)) return object.opt(lower);

        String camel = toLowerCamel(fieldName);
        if (object.has(camel)) return object.opt(camel);

        return null;
    }

    private static String toLowerCamel(String value) {
        String lower = value.toLowerCase();
        StringBuilder builder = new StringBuilder(lower.length());
        boolean upperNext = false;
        for (int i = 0; i < lower.length(); i++) {
            char c = lower.charAt(i);
            if (c == '_') {
                upperNext = true;
                continue;
            }
            builder.append(upperNext ? Character.toUpperCase(c) : c);
            upperNext = false;
        }
        return builder.toString();
    }

    private static void spoofFields_local(String packageName, Map<String, Object> propsToChange) {
        if (propsToChange == null || propsToChange.isEmpty()) return;
        for (Map.Entry<String, Object> entry : propsToChange.entrySet()) {
            String key = entry.getKey();
            Object value = entry.getValue();
            if (key == null) continue;
            try {
                Field field = getCachedBuildField(key);
                field.set(null, value);
                vslog("PP " + packageName + " | " + key + " : " + value);
            } catch (IllegalAccessException | NoSuchFieldException e) {
                vslog("PP " + packageName + " failed : " + e.getMessage());
            } catch (IllegalArgumentException e) {
                vslog("PP " + packageName + " failed : " + e.getMessage());
            }
        }
    }


    private static boolean packageInSettingList(String packageName, ContentResolver contentResolver, String settingKey) {
        if (packageName == null || packageName.isEmpty() || contentResolver == null || settingKey == null || settingKey.isEmpty()) {
            return false;
        }

        String raw = Settings.Global.getString(contentResolver, settingKey);
        if (raw == null || raw.trim().isEmpty()) {
            return false;
        }

        for (String item : raw.split(",")) {
            if (item != null && packageName.equals(item.trim())) {
                return true;
            }
        }
        return false;
    }

    public static boolean shouldSkipSpoofCert(String packageName, ContentResolver contentResolver) {
        return packageInSettingList(packageName, contentResolver, "vs_skip_spoof_cert_apps");
    }

    public static boolean shouldUseKeyboxKeyInfo(String packageName, ContentResolver contentResolver) {
        return packageInSettingList(packageName, contentResolver, "vs_keybox_keyinfo");
    }

    /** Apps in "vs_block_key_attest_apps" get key attestation blocked (KeyStore throws) for that app only. */
    public static boolean shouldBlockKeyAttest(String packageName, ContentResolver contentResolver) {
        return packageInSettingList(packageName, contentResolver, "vs_block_key_attest_apps");
    }

    private static final String CUSTOM_KEYATTEST_PREFIX = "vs_custom_keyattest_";

    /**
     * Per-app custom Key Attestation config, read from the JSON stored at
     * {@code vs_custom_keyattest_<packageName>} (Settings.Global). Example:
     * <pre>{@code
     * {
     *   "enabled": true,
     *   "keyInfo": "keybox",              // "stock" (default) or "keybox"
     *   "tags": [704, 706]                // AuthorizationList tags to override
     *   // "tags" also accepts names: ["rootOfTrust", "osPatchLevel"]
     *   // or an object form: {"rootOfTrust": true, "osVersion": false, ...}
     *   "verifiedBootKey": "<64 hex chars>",   // optional, only used when tag 704 is patched
     *   "verifiedBootHash": "<64 hex chars>",  // optional, same rule
     *   "serial": "<custom serial>"            // optional, only used when tag 713 is patched
     * }
     * }</pre>
     * Recognized tag aliases: rootOfTrust/bootloader=704, osVersion=705,
     * osPatchLevel/securityPatch=706, vendorPatchLevel=718, bootPatchLevel=719,
     * serial/attestationIdSerial=713.
     * verifiedBootKey/verifiedBootHash let a profile pin the exact RootOfTrust bytes of a target
     * device instead of falling back to the system property / the source attestation's own value
     * (see {@code SubAttest.resolveVerifiedBootKey}/{@code resolveVerifiedBootHash}). Likewise,
     * "serial" pins the exact attestationIdSerial value instead of leaving the source
     * attestation's own value untouched.
     */
    public static final class CustomKeyAttestConfig {
        public final boolean useKeyboxKeyInfo;
        /** null == "no tags specified" → caller should fall back to the default full tag set. */
        public final Set<Integer> tags;
        /** 32-byte hex, or null if not overridden by this profile. */
        public final String verifiedBootKeyHex;
        /** 32-byte hex, or null if not overridden by this profile. */
        public final String verifiedBootHashHex;
        /** Custom attestationIdSerial value, or null if not overridden by this profile. */
        public final String serialValue;

        CustomKeyAttestConfig(boolean useKeyboxKeyInfo, Set<Integer> tags,
                               String verifiedBootKeyHex, String verifiedBootHashHex, String serialValue) {
            this.useKeyboxKeyInfo = useKeyboxKeyInfo;
            this.tags = tags;
            this.verifiedBootKeyHex = verifiedBootKeyHex;
            this.verifiedBootHashHex = verifiedBootHashHex;
            this.serialValue = serialValue;
        }
    }

    /** Returns null if there is no enabled custom config for this app. */
    public static CustomKeyAttestConfig getCustomKeyAttestConfig(String packageName, ContentResolver contentResolver) {
        if (packageName == null || packageName.isEmpty()) return null;
        String json = getGlobalStringCached(contentResolver, CUSTOM_KEYATTEST_PREFIX + packageName);
        if (json == null || json.trim().isEmpty()) return null;

        try {
            JSONObject obj = new JSONObject(json.trim());
            if (!obj.optBoolean("enabled", true)) return null;

            String keyInfo = firstNonEmpty(obj.optString("keyInfo", null), obj.optString("key_info", null));
            boolean useKeyboxKeyInfo = "keybox".equalsIgnoreCase(keyInfo);

            Set<Integer> tags = parseKeyAttestTags(obj.opt("tags"));
            String verifiedBootKeyHex = normalizeHex(firstNonEmpty(
                    obj.optString("verifiedBootKey", null), obj.optString("verified_boot_key", null)));
            String verifiedBootHashHex = normalizeHex(firstNonEmpty(
                    obj.optString("verifiedBootHash", null), obj.optString("verified_boot_hash", null)));
            String serialValue = normalizeSerial(firstNonEmpty(
                    obj.optString("serial", null), obj.optString("attestationIdSerial", null),
                    obj.optString("attestation_id_serial", null)));
            return new CustomKeyAttestConfig(useKeyboxKeyInfo, tags, verifiedBootKeyHex, verifiedBootHashHex, serialValue);
        } catch (JSONException e) {
            vslog("Custom keyattest " + packageName + " invalid JSON : " + e.getMessage());
            return null;
        }
    }

    /** Accepts 64 (SHA-256) or 128 (SHA-512) hex chars, same rule SubAttest.hexStringToBytes uses; else null. */
    private static String normalizeHex(String value) {
        if (value == null) return null;
        String trimmed = value.trim();
        if (trimmed.length() != 64 && trimmed.length() != 128) return null;
        for (int i = 0; i < trimmed.length(); i++) {
            if (Character.digit(trimmed.charAt(i), 16) < 0) return null;
        }
        return trimmed;
    }

    /** Trims the configured serial value; null/blank stays null so the original attestation value is left untouched. */
    private static String normalizeSerial(String value) {
        if (value == null) return null;
        String trimmed = value.trim();
        return trimmed.isEmpty() ? null : trimmed;
    }

    private static Set<Integer> parseKeyAttestTags(Object tagsValue) {
        if (tagsValue == null || tagsValue == JSONObject.NULL) return null;

        Set<Integer> result = new HashSet<>();
        if (tagsValue instanceof JSONArray) {
            JSONArray array = (JSONArray) tagsValue;
            for (int i = 0; i < array.length(); i++) {
                Integer tag = resolveKeyAttestTag(array.opt(i));
                if (tag != null) result.add(tag);
            }
        } else if (tagsValue instanceof JSONObject) {
            JSONObject map = (JSONObject) tagsValue;
            JSONArray names = map.names();
            if (names != null) {
                for (int i = 0; i < names.length(); i++) {
                    String name = names.optString(i, null);
                    if (name != null && map.optBoolean(name, false)) {
                        Integer tag = resolveKeyAttestTag(name);
                        if (tag != null) result.add(tag);
                    }
                }
            }
        }
        return result.isEmpty() ? Collections.emptySet() : result;
    }

    private static Integer resolveKeyAttestTag(Object rawTag) {
        if (rawTag == null || rawTag == JSONObject.NULL) return null;
        if (rawTag instanceof Number) return ((Number) rawTag).intValue();

        String name = String.valueOf(rawTag).trim();
        try {
            return Integer.parseInt(name);
        } catch (NumberFormatException ignored) {
            // fall through to alias lookup
        }

        switch (name.toLowerCase(Locale.ROOT)) {
            case "rootoftrust":
            case "root_of_trust":
            case "bootloader":
                return 704;
            case "osversion":
            case "os_version":
                return 705;
            case "ospatchlevel":
            case "os_patch_level":
            case "securitypatch":
            case "security_patch":
                return 706;
            case "vendorpatchlevel":
            case "vendor_patch_level":
                return 718;
            case "bootpatchlevel":
            case "boot_patch_level":
                return 719;
            case "serial":
            case "attestationidserial":
            case "attestation_id_serial":
                return 713;
            default:
                vslog("Custom keyattest: unknown tag alias '" + name + "'");
                return null;
        }
    }

    public static boolean justTrue() {
        return true;
    }

    public static boolean justFalse() {
        return false;
    }


    public void triggerCTSFromOtherApp(Context context) {
        Intent intent = new Intent("com.bhlnk.vsteam.TRIGGER_CTS");
        intent.setPackage("com.bhlnk.vsteam"); // package của ứng dụng chứa TriggerCTS
        intent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        context.startActivity(intent);

    }


}
