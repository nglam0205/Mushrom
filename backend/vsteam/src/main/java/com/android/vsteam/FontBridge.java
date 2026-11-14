package com.android.vsteam;

import android.content.ContentProvider;
import android.content.Context;
import android.net.Uri;
import android.os.Binder;
import android.os.Bundle;
import android.provider.Settings;
import android.text.TextUtils;
import android.util.Log;

import java.io.InputStream;

/**
 * Applies a VSTeam font on ColorOS the way the theme store does, called from Settings by
 * bin/patch_settings_font_bridge.py (SuggestionStateProvider#call, method {@link #METHOD}).
 *
 * Only system_server, Settings (system_app) and the theme store (platform_app) may write
 * /data/system/font, so VSTeam hands the .ttf to Settings, which runs its own
 * com.oplus.flipfont.FontManager#setFontInputStream: the file becomes Customized-Regular.ttf and
 * the FlipFont flag of the configuration changes, which reloads the typeface everywhere without
 * installing a font package or opening the language settings.
 * {@link #METHOD_RESET} goes back to the stock font the way the font page of Settings does.
 */
public final class FontBridge {

    public static final String METHOD = "vs_apply_font";
    public static final String METHOD_RESET = "vs_reset_font";
    private static final String TAG = "VsFontBridge";
    private static final String CALLER = "com.bhlnk.vsteam";
    private static final String FONT_MANAGER = "com.oplus.flipfont.FontManager";
    // Anything but empty or the default package: Settings then treats the font as custom.
    private static final String TYPEFACE = "com.bhlnk.vsteam.font";

    private FontBridge() {
    }

    /** {@code arg} is a content Uri of the .ttf readable by Settings; null for other methods. */
    public static Bundle call(ContentProvider provider, String method, String arg, Bundle extras) {
        boolean applied;
        if (METHOD.equals(method)) {
            applied = apply(provider, arg);
        } else if (METHOD_RESET.equals(method)) {
            applied = reset(provider);
        } else {
            return null;
        }
        Bundle result = new Bundle();
        result.putBoolean("applied", applied);
        return result;
    }

    private static boolean isVsTeam(ContentProvider provider) {
        String caller;
        try {
            caller = provider.getCallingPackage();
        } catch (SecurityException e) {
            caller = null;
        }
        if (!CALLER.equals(caller)) {
            Log.w(TAG, "rejected caller " + caller);
            return false;
        }
        return true;
    }

    /** Same as picking the default font in Settings: drop Customized-*.ttf, then reload. */
    private static boolean reset(ContentProvider provider) {
        if (!isVsTeam(provider)) {
            return false;
        }
        Context context = provider.getContext();
        long identity = Binder.clearCallingIdentity();
        try {
            Class<?> manager = Class.forName(FONT_MANAGER, true,
                    provider.getClass().getClassLoader());
            manager.getMethod("setDefaultSystemFont", Context.class).invoke(null, context);
            manager.getMethod("notifySystem", Context.class).invoke(null, context);
            Log.i(TAG, "setDefaultSystemFont done");
            return true;
        } catch (Throwable e) {
            Log.w(TAG, "reset failed", e);
            return false;
        } finally {
            Binder.restoreCallingIdentity(identity);
        }
    }

    private static boolean apply(ContentProvider provider, String arg) {
        if (!isVsTeam(provider)) {
            return false;
        }
        if (TextUtils.isEmpty(arg)) {
            return false;
        }
        Context context = provider.getContext();
        long identity = Binder.clearCallingIdentity();
        try (InputStream in = context.getContentResolver().openInputStream(Uri.parse(arg))) {
            if (in == null) {
                return false;
            }
            Class<?> manager = Class.forName(FONT_MANAGER, true,
                    provider.getClass().getClassLoader());
            Object instance = manager.getConstructor(Context.class, String.class)
                    .newInstance(context, TYPEFACE);
            boolean applied = (Boolean) manager
                    .getMethod("setFontInputStream", Context.class, InputStream.class)
                    .invoke(instance, context, in);
            Log.i(TAG, "setFontInputStream -> " + applied);
            if (applied) {
                // Same order as FontManager#setNewFormatFont: the file first, then the record.
                Settings.System.putString(context.getContentResolver(), "current_typeface",
                        TYPEFACE);
            }
            return applied;
        } catch (Throwable e) {
            Log.w(TAG, "apply failed", e);
            return false;
        } finally {
            Binder.restoreCallingIdentity(identity);
        }
    }
}
