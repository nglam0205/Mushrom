package com.android.vsteam;

import android.media.MediaDrm;

import java.util.Collections;
import java.util.Map;
import java.util.UUID;
import java.util.WeakHashMap;

/**
 * Widevine L3 for models whose L1 cannot provision, called from MediaDrm by
 * bin/patch_widevine_l3.py of the build tool (models in fastboot/widevine_l3.list).
 *
 * On those models OEMCrypto L1 initializes, so the Widevine HAL picks L1, but the TA fails to
 * sign the provisioning request (derivekey_from_context). Apps such as Netflix then stop with
 * error -93. securityLevel is a property of the DRM plugin, not a system property: it is set on
 * every Widevine MediaDrm before a session opens, and sessions asked at a hardware level are
 * opened at SW_SECURE_DECODE, since the plugin refuses a hardware session once L3 is set.
 */
public final class WidevineL3 {

    private static final UUID WIDEVINE = new UUID(0xedef8ba979d64aceL, 0xa3c827dcd51d21edL);

    private static final Map<MediaDrm, Boolean> sForced =
            Collections.synchronizedMap(new WeakHashMap<>());

    private WidevineL3() {
    }

    /** End of MediaDrm(UUID). */
    public static void onCreate(MediaDrm drm, UUID uuid) {
        if (!WIDEVINE.equals(uuid)) {
            return;
        }
        try {
            drm.setPropertyString("securityLevel", "L3");
            sForced.put(drm, Boolean.TRUE);
        } catch (Throwable t) {
            Toolbox.vslog("WidevineL3: set securityLevel failed: " + t);
        }
    }

    /** Start of MediaDrm#openSession(int): the level to open the session at. */
    public static int sessionLevel(MediaDrm drm, int level) {
        if (level > MediaDrm.SECURITY_LEVEL_SW_SECURE_DECODE && sForced.containsKey(drm)) {
            return MediaDrm.SECURITY_LEVEL_SW_SECURE_DECODE;
        }
        return level;
    }
}
