-keep class miui.** {
    public *;
}
-keep class android.** {
    public *;
}
-keep class com.** {
    public *;
}
-repackageclasses 'com.android.vsteam'
-keep class com.android.vsteam.SubAttest { public *; }
-keep class com.android.vsteam.DefaultKeybox { public *; }
