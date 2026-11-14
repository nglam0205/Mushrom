package com.miui.home.launcher;

import com.android.vsteam.Toolbox;

public class LauncherAssistantCompat {

    public static LauncherAssistantCompat newInstance(Launcher launcher) {
        int intGlobal = Toolbox.getIntGlobal("vs_minus_screen_config");

        switch (intGlobal) {
            case 1:
                return new LauncherAssistantCompatMIUI(launcher, "com.mi.globalminusscreen");
            case 2:
                return new LauncherAssistantCompatGoogle(launcher, "com.google.android.googlequicksearchbox");
            default:
                return new LauncherAssistantCompatMIUI(launcher, "com.miui.personalassistant");
        }
    }


}
