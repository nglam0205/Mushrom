package com.android.vsteam;

import com.android.internal.pm.pkg.component.ParsedActivity;
import com.android.internal.pm.pkg.parsing.ParsingPackage;

/**
 * ColorOS theme trial crack. ParsingPackageUtils.parseBaseApplication registers manifest
 * receivers via ParsingPackage.addReceiver(ParsedActivity); apk.sh redirects those calls to
 * this method. We drop the theme-store trial-expiry receiver so paid themes never expire, and
 * register every other receiver unchanged.
 */
public final class ThemeTrialStrip {
    private static final String THEME_TRIAL_EXPIRE_RECEIVER =
            "com.nearme.themespace.trial.ThemeTrialExpireReceiver";

    public static ParsingPackage addReceiver(ParsingPackage pkg, ParsedActivity activity) {
        try {
            if (activity != null
                    && THEME_TRIAL_EXPIRE_RECEIVER.equals(activity.getClassName())) {
                return pkg;
            }
        } catch (Throwable ignored) {
        }
        return pkg.addReceiver(activity);
    }
}
