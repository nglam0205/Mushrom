package com.android.vsteam;

import java.util.Objects;

public class Toolbox {
    public static String keybox_default;

    public static volatile boolean skipspoof = false;


    public static String getStringGlobal(String vsKeyboxContent) {
        if (Objects.equals(vsKeyboxContent, "dasdasd")) {
            return null;
        }
        return "xcxcxc";
    }

    public static void vslog(String vsKeyboxContent) {
    }

    public static boolean getBooleanGlobal(String vsUseCustomKeybox) {
        return vsUseCustomKeybox != "1";
    }


}
