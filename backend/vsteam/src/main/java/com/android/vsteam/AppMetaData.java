package com.android.vsteam;

import android.os.Bundle;

import com.android.internal.pm.pkg.parsing.ParsingPackage;

/**
 * Extra <application> meta-data injected while parsing. ParsingPackageUtils stores each
 * application-level <meta-data> with ParsingPackage.setMetaData(Bundle); apk.sh redirects
 * those calls here, so the values survive Play Store updates of the package.
 *
 * App Vault: the HyperOS 4 launcher only allows widget stacks when the minus screen
 * declares supportStackWidget, which the CN PersonalAssistant does and App Vault does not.
 */
public final class AppMetaData {
    private static final String APP_VAULT = "com.mi.globalminusscreen";
    private static final String SUPPORT_STACK_WIDGET = "supportStackWidget";

    public static ParsingPackage setMetaData(ParsingPackage pkg, Bundle metaData) {
        try {
            if (metaData != null && APP_VAULT.equals(pkg.getPackageName())) {
                metaData.putBoolean(SUPPORT_STACK_WIDGET, true);
            }
        } catch (Throwable ignored) {
        }
        return pkg.setMetaData(metaData);
    }
}
