package oplus.systemui.biometrics.finger.udfps.fpquicklaunch.quicklaunch.helper;

import static com.android.vsteam.Toolbox.vslog;

import android.app.contextualsearch.ContextualSearchManager;
import android.content.Context;

public class ActivityStartedHelper {
    private static ContextualSearchManager instance;

    public static void startCtsOnAndroidV(Context context, int entryPoint) {
        try {
            if (instance == null) {
                instance = (ContextualSearchManager) context.getSystemService(ContextualSearchManager.class);
            }

            if (instance != null) {
                instance.startContextualSearch(entryPoint);

                String sb = "startCtsOnAndroidV result true, omniEntryPoint " +
                        entryPoint +
                        " ,instance :" +
                        instance;

                vslog(sb);
            } else {
                vslog("ContextualSearchManager is null");
            }

        } catch (Exception e) {
            vslog("Exception: " + e.toString());
        }
    }
}
