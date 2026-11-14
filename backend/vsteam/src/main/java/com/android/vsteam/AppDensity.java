package com.android.vsteam;

import android.content.Context;
import android.database.ContentObserver;
import android.database.Cursor;
import android.net.Uri;
import android.os.Handler;
import android.os.HandlerThread;
import android.provider.Settings;
import android.util.DisplayMetrics;

import org.json.JSONObject;

import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * DPI per app, called from InputManagerService#systemRunning by bin/patch_app_density.py of the
 * build tool.
 *
 * VSTeam stores it as {@code "density"} in the app's spoof profile (Settings.Global
 * {@code vs_spoof_<package>}, the JSON VsProps reads for the spoofed build). It reaches the app
 * the way GameManager's downscale does: a CompatScaleProvider of the window manager returns a
 * density scale for the package, and the app process starts with that compat density (stock
 * density / scale), resources and window alike. The process has to start again to pick up a
 * change; VSTeam stops the app when it saves the profile.
 *
 * The provider interface lives in services.jar, which this boot class path code cannot load by
 * name, so it is implemented with a Proxy on the class loader of ActivityTaskManagerInternal.
 * It runs under the window manager lock: it only reads a map kept up to date from the settings.
 */
public final class AppDensity {

    private static final String KEY_PREFIX = "vs_spoof_";
    /** Same prefix, other settings: profiles switched off in VSTeam, the device info switch. */
    private static final String[] OTHER_KEYS = {"vs_spoof_disabled_", "vs_spoof_device_info"};
    /** CompatScaleProvider.COMPAT_SCALE_MODE_PRODUCT: after GameManager's downscale. */
    private static final int PROVIDER_ID = 2;
    private static final int DENSITY_MIN = 120;
    private static final int DENSITY_MAX = 1000;

    private static final Map<String, Float> sScales = new ConcurrentHashMap<>();
    private static Context sContext;
    private static Constructor<?> sCompatScale;

    private AppDensity() {
    }

    public static void attach(Context context) {
        try {
            sContext = context;
            sCompatScale = Class.forName("android.content.res.CompatibilityInfo$CompatScale")
                    .getConstructor(float.class, float.class);
            HandlerThread thread = new HandlerThread("VsAppDensity");
            thread.start();
            Handler handler = new Handler(thread.getLooper());
            context.getContentResolver().registerContentObserver(Settings.Global.CONTENT_URI, true,
                    new ContentObserver(handler) {
                        @Override
                        public void onChange(boolean selfChange, Uri uri) {
                            String key = uri != null ? uri.getLastPathSegment() : null;
                            if (isProfileKey(key)) {
                                load(key);
                            }
                        }
                    });
            handler.post(AppDensity::loadAll);
            register();
        } catch (Throwable t) {
            Toolbox.vslog("AppDensity attach: " + t);
        }
    }

    private static void register() throws Exception {
        Object atm = Toolbox.findLocalService("com.android.server.wm.ActivityTaskManagerInternal");
        if (atm == null) {
            throw new IllegalStateException("no ActivityTaskManagerInternal");
        }
        ClassLoader loader = atm.getClass().getClassLoader();
        Class<?> providerClass = loader.loadClass("com.android.server.wm.CompatScaleProvider");
        Object provider = Proxy.newProxyInstance(loader, new Class<?>[]{providerClass},
                (proxy, method, args) -> {
                    switch (method.getName()) {
                        case "getCompatScale":
                            return compatScale((String) args[0]);
                        case "hashCode":
                            return System.identityHashCode(proxy);
                        case "equals":
                            return proxy == args[0];
                        case "toString":
                            return "VsAppDensity";
                        default:
                            return null;
                    }
                });
        Method method = null;
        for (Class<?> c = atm.getClass(); c != null && method == null; c = c.getSuperclass()) {
            for (Method m : c.getDeclaredMethods()) {
                if ("registerCompatScaleProvider".equals(m.getName())
                        && m.getParameterCount() == 2) {
                    method = m;
                }
            }
        }
        if (method == null) {
            throw new NoSuchMethodException("registerCompatScaleProvider");
        }
        method.setAccessible(true);
        method.invoke(atm, PROVIDER_ID, provider);
    }

    /** CompatScaleProvider#getCompatScale: null keeps the app as it is. */
    private static Object compatScale(String packageName) {
        Float scale = packageName != null ? sScales.get(packageName) : null;
        if (scale == null) {
            return null;
        }
        try {
            return sCompatScale.newInstance(1f, scale);
        } catch (Throwable t) {
            return null;
        }
    }

    private static boolean isProfileKey(String key) {
        if (key == null || !key.startsWith(KEY_PREFIX) || key.length() == KEY_PREFIX.length()) {
            return false;
        }
        for (String other : OTHER_KEYS) {
            if (key.startsWith(other)) {
                return false;
            }
        }
        return true;
    }

    private static void loadAll() {
        try (Cursor cursor = sContext.getContentResolver().query(Settings.Global.CONTENT_URI,
                new String[]{Settings.NameValueTable.NAME}, null, null, null)) {
            while (cursor != null && cursor.moveToNext()) {
                String key = cursor.getString(0);
                if (isProfileKey(key)) {
                    load(key);
                }
            }
        } catch (Throwable t) {
            Toolbox.vslog("AppDensity loadAll: " + t);
        }
    }

    private static void load(String key) {
        String packageName = key.substring(KEY_PREFIX.length());
        int density = 0;
        try {
            String json = Settings.Global.getString(sContext.getContentResolver(), key);
            if (json != null && !json.isEmpty()) {
                JSONObject profile = new JSONObject(json);
                if (profile.optBoolean("enabled", true)) {
                    density = profile.optInt("density", 0);
                }
            }
        } catch (Throwable ignored) {
            // Not a profile: no density.
        }
        int stock = DisplayMetrics.DENSITY_DEVICE_STABLE;
        if (density >= DENSITY_MIN && density <= DENSITY_MAX && density != stock) {
            sScales.put(packageName, (float) stock / density);
        } else {
            sScales.remove(packageName);
        }
    }
}
