package com.miui.home.launcher;

import android.content.ComponentName;

import com.android.vsteam.Toolbox;

import java.util.HashSet;

public class AppFilter {

    public boolean shouldShowApp(ComponentName componentName) {

        HashSet<String> hideApps = new HashSet<>();
        hideApps.add("com.mi.globalminusscreen");

        String packageName = componentName.getPackageName();
        String strGlobal = Toolbox.getStrGlobal("vs_hide_app");

        if (strGlobal != null && strGlobal.contains(packageName)) {
            return false;
        }

        if (hideApps.contains(packageName)) {
            return false;
        }

        return true;
    }
}
