package com.android.vsteam;

import android.app.KeyguardManager;
import android.app.TaskInfo;
import android.content.ComponentName;
import android.content.Context;
import android.database.ContentObserver;
import android.graphics.Point;
import android.hardware.display.DisplayManager;
import android.hardware.input.InputManager;
import android.media.AudioManager;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.PowerManager;
import android.os.SystemClock;
import android.provider.Settings;
import android.view.Display;
import android.view.InputDevice;
import android.view.InputEvent;
import android.view.KeyEvent;
import android.view.MotionEvent;

import java.lang.reflect.Field;
import java.lang.reflect.Method;

/**
 * Rotating camera ring of the Xiaomi 17 Ultra by Leica (nezha), called from
 * InputManagerService by bin/patch_focus_ring.py of the build tool.
 *
 * The ring is the xiaomi-focus-ring input device of focus_ring_bridge.ko (vendor 0x9120,
 * product 0x9126). Its idc makes it a rotaryEncoder, so each detent reaches the focused window
 * as an ACTION_SCROLL MotionEvent of SOURCE_ROTARY_ENCODER with AXIS_SCROLL +1 or -1; the
 * driver also sends +60 (rotation irq) and +6 (motion ping/pang), which carry no direction.
 * Only the camera app uses it (zoom); everywhere else lists and pages scroll a little.
 * Outside the camera the sensor hub sends no detents at all unless MIUI's CameraMrEventHandler
 * asks for them, which {@link #cameraMrState} makes it do while a ring action is set; the same
 * handler tells {@link #setInCamera} when the camera gains or loses focus.
 *
 * VSTeam picks an action per direction in Settings.Global {@code vs_focus_ring_cw} and
 * {@code vs_focus_ring_ccw}; the swipe actions only run in the apps listed in
 * {@code vs_focus_ring_swipe_apps}. While one is set, the input filter of InputManagerService stays
 * enabled, so every event passes {@link #filter} before dispatch; the ring's are consumed and
 * the action runs here, on a thread of its own. Unset or "None" keeps the stock scroll, and the
 * camera always keeps the ring for its zoom.
 */
public final class FocusRing {

    private static final String KEY_CW = "vs_focus_ring_cw";
    private static final String KEY_CCW = "vs_focus_ring_ccw";
    /** Packages, comma separated, where the swipe actions run; nowhere else, not even empty. */
    private static final String KEY_SWIPE_APPS = "vs_focus_ring_swipe_apps";

    /** Ring-only actions; any other value runs through {@link Toolbox#Handle_VS_Action_Boolean}. */
    private static final String SWIPE_UP = "ring_swipe_up";
    private static final String SWIPE_DOWN = "ring_swipe_down";
    private static final String VOLUME_UP = "ring_volume_up";
    private static final String VOLUME_DOWN = "ring_volume_down";
    private static final String BRIGHTNESS_UP = "ring_brightness_up";
    private static final String BRIGHTNESS_DOWN = "ring_brightness_down";
    private static final String MEDIA_NEXT = "ring_media_next";
    private static final String MEDIA_PREVIOUS = "ring_media_previous";

    /**
     * CameraMrEventHandler state: report each detent, as in the camera. Stock never adds its
     * other bits (2 = long rotation, 4 = haptics) to it; with 4 the hub sends detents that swing
     * back and forth (measured: +30/-30 bursts while turning one way).
     */
    private static final int MR_SINGLE_STEP = 1;

    /** Larger AXIS_SCROLL values are the rotation and motion markers, not detents. */
    private static final float MAX_DETENT = 5f;
    /** Detents closer than this belong to one turn of the ring. */
    private static final long TURN_GAP_MS = 500;
    /**
     * The ring reports ~200 events per turn, and a finger merely resting on it makes the hub
     * swing back and forth (runs of 30-50 events each way, ~5 times a second, measured). So one
     * step is this many events in one direction, and nothing counts for a while after the
     * direction flips; a run of events this long or longer counts as a flip.
     */
    private static final int EVENTS_PER_STEP = 8;
    private static final long REVERSAL_QUIET_MS = 600;
    private static final int REVERSAL_MIN_RUN = 2;
    /** A pause this long ends the movement: the next event starts afresh. */
    private static final long EVENT_IDLE_MS = 500;
    /**
     * A nudge of the ring is enough for one swipe: this many events in one direction. The next
     * swipe needs the ring to pause this long first, or, while it keeps turning, this much time
     * since the last swipe, so one nudge shows exactly one more item of the feed.
     */
    private static final int SWIPE_EVENTS = 3;
    private static final long SWIPE_REARM_MS = 250;
    private static final long SWIPE_REPEAT_MS = 700;
    /**
     * A quick flick: path in shares of the screen height, and its moves. Their times are set,
     * not read from the clock, so the app sees the same fast flick however long it takes to
     * handle each event (TikTok took 300-500 ms for one swipe waiting on it, too slow to page).
     */
    private static final float SWIPE_LOW = 0.8f;
    private static final float SWIPE_HIGH = 0.2f;
    private static final int SWIPE_MOVES = 12;
    private static final long SWIPE_MOVE_MS = 8;
    /** Brightness step, in the perceptual (gamma) space of the slider. */
    private static final float BRIGHTNESS_STEP = 1f / 32f;
    private static final float GAMMA = 2.2f;
    /**
     * InputManager.INJECT_INPUT_EVENT_MODE_WAIT_FOR_RESULT: each event is queued before the next
     * is made (with async injection the moves never reached the app, measured), without waiting
     * for the app to handle it.
     */
    private static final int INJECT_WAIT_FOR_RESULT = 1;

    private static Object sIms;
    private static Context sContext;
    private static Handler sHandler;
    private static volatile String sCw;
    private static volatile String sCcw;
    /** The camera has the focus: it zooms with the ring, set by CameraMrEventHandler. */
    private static volatile boolean sInCamera;

    // Worker thread only.
    private static long sLastEvent;
    private static long sLastReversal;
    private static int sRunSign;
    private static int sRunLength;
    private static int sStepCount;
    private static long sLastDetent;
    private static int sLastSign;
    private static boolean sTurnFired;
    private static long sLastSwipe;
    private static boolean sSwipeArmed = true;

    private FocusRing() {
    }

    /** InputManagerService#systemRunning: settings and the focused task are readable now. */
    public static void attach(Object ims, Context context) {
        try {
            sIms = ims;
            sContext = context;
            HandlerThread thread = new HandlerThread("VsFocusRing");
            thread.start();
            sHandler = new Handler(thread.getLooper());
            ContentObserver observer = new ContentObserver(sHandler) {
                @Override
                public void onChange(boolean selfChange) {
                    readSettings();
                }
            };
            for (String key : new String[]{KEY_CW, KEY_CCW}) {
                context.getContentResolver().registerContentObserver(
                        Settings.Global.getUriFor(key), false, observer);
            }
            sHandler.post(FocusRing::readSettings);
        } catch (Throwable t) {
            Toolbox.vslog("FocusRing attach: " + t);
        }
    }

    /**
     * CameraMrEventHandler#determineCameraMrSensorStateLocked (miui-services), outside the
     * camera: the state the ring's sensor hub is set to. Stock only reports detents to the
     * camera and the rotation (quick launch of the camera) elsewhere; while a ring action is set
     * it reports detents everywhere as in the camera, and no rotation, so a turn never opens
     * the camera.
     */
    public static int cameraMrState(Context context, int state) {
        try {
            String cw = Settings.Global.getString(context.getContentResolver(), KEY_CW);
            String ccw = Settings.Global.getString(context.getContentResolver(), KEY_CCW);
            if (isSet(cw) || isSet(ccw)) {
                return MR_SINGLE_STEP;
            }
        } catch (Throwable t) {
            Toolbox.vslog("FocusRing cameraMrState: " + t);
        }
        return state;
    }

    /**
     * CameraMrEventHandler#determineCameraMrSensorStateLocked, which runs whenever the camera
     * gains or loses the focused window.
     */
    public static void setInCamera(boolean inCamera) {
        sInCamera = inCamera;
    }

    /**
     * InputManagerService#setInputFilter: whether the native input filter stays on. It must
     * while a ring action is set, also when accessibility has no filter installed.
     */
    public static boolean filterEnabled(boolean wanted) {
        return wanted || isActive();
    }

    /**
     * InputManagerService#filterInputEvent, before the accessibility filter.
     *
     * @return true when the event was the ring's and is consumed here
     */
    public static boolean filter(InputEvent event, int policyFlags) {
        if (!(event instanceof MotionEvent) || sInCamera || !isActive()) {
            return false;
        }
        MotionEvent motion = (MotionEvent) event;
        if (motion.getSource() != InputDevice.SOURCE_ROTARY_ENCODER
                || motion.getActionMasked() != MotionEvent.ACTION_SCROLL) {
            return false;
        }
        float scroll = motion.getAxisValue(MotionEvent.AXIS_SCROLL);
        // Markers of the driver have no direction, but apps would scroll a page for them.
        boolean marker = Math.abs(scroll) > MAX_DETENT;
        String action = scroll > 0 ? sCw : sCcw;
        if (!marker && !isSet(action)) {
            return false;
        }
        // Not gated on FLAG_PASS_TO_USER: outside the camera MIUI clears it on the ring's events
        // (its own quick launch takes them), so it is unset here even with the screen on. The
        // worker checks the screen instead.
        if (!marker) {
            int sign = scroll > 0 ? 1 : -1;
            long when = motion.getEventTime();
            sHandler.post(() -> onRingEvent(action, sign, when));
        }
        return true;
    }

    /** Turns single events into steps, see {@link #EVENTS_PER_STEP}. */
    private static void onRingEvent(String action, int sign, long when) {
        if (when - sLastEvent > SWIPE_REARM_MS) {
            sSwipeArmed = true;
        }
        if (when - sLastEvent > EVENT_IDLE_MS) {
            sRunSign = 0;
        }
        sLastEvent = when;
        if (sign == sRunSign) {
            sRunLength++;
        } else {
            if (sRunSign != 0 && sRunLength >= REVERSAL_MIN_RUN) {
                sLastReversal = when;
            }
            sRunSign = sign;
            sRunLength = 1;
            sStepCount = 0;
        }
        if (when - sLastReversal < REVERSAL_QUIET_MS) {
            sStepCount = 0;
            return;
        }
        if (SWIPE_UP.equals(action) || SWIPE_DOWN.equals(action)) {
            if (++sStepCount >= SWIPE_EVENTS
                    && (sSwipeArmed || when - sLastSwipe >= SWIPE_REPEAT_MS)) {
                sStepCount = 0;
                sSwipeArmed = false;
                sLastSwipe = when;
                try {
                    if (sContext.getSystemService(PowerManager.class).isInteractive()
                            && !keyguardLocked() && swipeAllowed()) {
                        swipe(SWIPE_UP.equals(action));
                    }
                } catch (Throwable t) {
                    Toolbox.vslog("FocusRing swipe: " + t);
                }
            }
            return;
        }
        if (++sStepCount >= EVENTS_PER_STEP) {
            sStepCount = 0;
            if (sContext.getSystemService(PowerManager.class).isInteractive()) {
                onDetent(action, sign, when);
            }
        }
    }

    private static void onDetent(String action, int sign, long when) {
        boolean newTurn = sign != sLastSign || when - sLastDetent > TURN_GAP_MS;
        sLastSign = sign;
        sLastDetent = when;
        if (newTurn) {
            sTurnFired = false;
        }
        try {
            switch (action) {
                case VOLUME_UP:
                case VOLUME_DOWN:
                    volume(VOLUME_UP.equals(action));
                    break;
                case BRIGHTNESS_UP:
                case BRIGHTNESS_DOWN:
                    brightness(BRIGHTNESS_UP.equals(action));
                    break;
                default:
                    // Once per turn: a track skip or an app launch must not repeat per detent.
                    if (!sTurnFired) {
                        sTurnFired = true;
                        if (MEDIA_NEXT.equals(action) || MEDIA_PREVIOUS.equals(action)) {
                            mediaKey(MEDIA_NEXT.equals(action)
                                    ? KeyEvent.KEYCODE_MEDIA_NEXT : KeyEvent.KEYCODE_MEDIA_PREVIOUS);
                        } else {
                            Toolbox.Handle_VS_Action_Boolean(action);
                        }
                    }
                    break;
            }
        } catch (Throwable t) {
            Toolbox.vslog("FocusRing " + action + ": " + t);
        }
    }

    private static void volume(boolean up) {
        sContext.getSystemService(AudioManager.class).adjustSuggestedStreamVolume(
                up ? AudioManager.ADJUST_RAISE : AudioManager.ADJUST_LOWER,
                AudioManager.USE_DEFAULT_STREAM_TYPE, AudioManager.FLAG_SHOW_UI);
    }

    /** Steps like the brightness slider does: even steps of its gamma curve, not of nits. */
    private static void brightness(boolean up) throws Exception {
        DisplayManager dm = sContext.getSystemService(DisplayManager.class);
        Method get = DisplayManager.class.getMethod("getBrightness", int.class);
        Method set = DisplayManager.class.getMethod("setBrightness", int.class, float.class);
        float linear = (float) get.invoke(dm, Display.DEFAULT_DISPLAY);
        float gamma = (float) Math.pow(Math.max(0f, linear), 1f / GAMMA);
        gamma = Math.max(0f, Math.min(1f, gamma + (up ? BRIGHTNESS_STEP : -BRIGHTNESS_STEP)));
        set.invoke(dm, Display.DEFAULT_DISPLAY, (float) Math.pow(gamma, GAMMA));
    }

    private static void mediaKey(int keyCode) {
        AudioManager audio = sContext.getSystemService(AudioManager.class);
        long now = SystemClock.uptimeMillis();
        audio.dispatchMediaKeyEvent(new KeyEvent(now, now, KeyEvent.ACTION_DOWN, keyCode, 0));
        audio.dispatchMediaKeyEvent(new KeyEvent(now, now, KeyEvent.ACTION_UP, keyCode, 0));
    }

    /**
     * One finger flicked along the middle of the screen: up shows the next item of a feed
     * (Shorts, TikTok, Reels), down the previous one.
     */
    private static void swipe(boolean up) throws Exception {
        Point size = new Point();
        sContext.getSystemService(DisplayManager.class)
                .getDisplay(Display.DEFAULT_DISPLAY).getRealSize(size);
        float x = size.x / 2f;
        float from = size.y * (up ? SWIPE_LOW : SWIPE_HIGH);
        float to = size.y * (up ? SWIPE_HIGH : SWIPE_LOW);

        int device = touchscreen();
        long down = SystemClock.uptimeMillis();
        inject(touch(device, down, down, MotionEvent.ACTION_DOWN, x, from));
        for (int i = 1; i <= SWIPE_MOVES; i++) {
            SystemClock.sleep(SWIPE_MOVE_MS);
            inject(touch(device, down, down + i * SWIPE_MOVE_MS, MotionEvent.ACTION_MOVE,
                    x, from + (to - from) * i / SWIPE_MOVES));
        }
        inject(touch(device, down, down + SWIPE_MOVES * SWIPE_MOVE_MS, MotionEvent.ACTION_UP,
                x, to));
    }

    /** A finger of the real touchscreen, as `input` makes it. */
    private static MotionEvent touch(int device, long down, long time, int action, float x, float y) {
        MotionEvent.PointerProperties[] properties = {new MotionEvent.PointerProperties()};
        properties[0].id = 0;
        properties[0].toolType = MotionEvent.TOOL_TYPE_FINGER;
        MotionEvent.PointerCoords[] coords = {new MotionEvent.PointerCoords()};
        coords[0].x = x;
        coords[0].y = y;
        coords[0].pressure = 1f;
        coords[0].size = 1f;
        return MotionEvent.obtain(down, time, action, 1, properties, coords, 0, 0, 1f, 1f,
                device, 0, InputDevice.SOURCE_TOUCHSCREEN, 0);
    }

    private static int touchscreen() {
        for (int id : InputDevice.getDeviceIds()) {
            InputDevice device = InputDevice.getDevice(id);
            if (device != null && device.supportsSource(InputDevice.SOURCE_TOUCHSCREEN)) {
                return id;
            }
        }
        return 0;
    }

    private static void inject(InputEvent event) throws Exception {
        InputManager im = sContext.getSystemService(InputManager.class);
        Method method = InputManager.class.getMethod("injectInputEvent", InputEvent.class, int.class);
        method.invoke(im, event, INJECT_WAIT_FOR_RESULT);
    }

    /** Whether the focused app is one picked in VSTeam for the swipe actions. */
    private static boolean swipeAllowed() {
        String apps = Settings.Global.getString(sContext.getContentResolver(), KEY_SWIPE_APPS);
        String top = focusedPackage();
        if (apps == null || top == null) {
            return false;
        }
        for (String pkg : apps.split(",")) {
            if (top.equals(pkg.trim())) {
                return true;
            }
        }
        return false;
    }

    private static String focusedPackage() {
        try {
            Object atm = Class.forName("android.app.ActivityTaskManager")
                    .getMethod("getService").invoke(null);
            Object info = atm.getClass().getMethod("getFocusedRootTaskInfo").invoke(atm);
            ComponentName top = info instanceof TaskInfo ? ((TaskInfo) info).topActivity : null;
            return top != null ? top.getPackageName() : null;
        } catch (Throwable t) {
            Toolbox.vslog("FocusRing focusedPackage: " + t);
            return null;
        }
    }

    private static boolean keyguardLocked() {
        return sContext.getSystemService(KeyguardManager.class).isKeyguardLocked();
    }

    private static void readSettings() {
        boolean was = isActive();
        sCw = Settings.Global.getString(sContext.getContentResolver(), KEY_CW);
        sCcw = Settings.Global.getString(sContext.getContentResolver(), KEY_CCW);
        if (was != isActive()) {
            applyFilterState();
        }
    }

    /** Turns the native filter on or off like setInputFilter would, now that ours changed. */
    private static void applyFilterState() {
        try {
            Class<?> cls = sIms.getClass();
            Object lock = field(cls, "mInputFilterLock").get(sIms);
            synchronized (lock) {
                boolean wanted = field(cls, "mInputFilter").get(sIms) != null;
                Object nativeIms = field(cls, "mNative").get(sIms);
                Method enable = nativeIms.getClass().getMethod("setInputFilterEnabled", boolean.class);
                enable.setAccessible(true);
                enable.invoke(nativeIms, filterEnabled(wanted));
            }
        } catch (Throwable t) {
            Toolbox.vslog("FocusRing applyFilterState: " + t);
        }
    }

    private static Field field(Class<?> cls, String name) throws NoSuchFieldException {
        for (Class<?> c = cls; c != null; c = c.getSuperclass()) {
            try {
                Field f = c.getDeclaredField(name);
                f.setAccessible(true);
                return f;
            } catch (NoSuchFieldException ignored) {
            }
        }
        throw new NoSuchFieldException(name);
    }

    private static boolean isActive() {
        return isSet(sCw) || isSet(sCcw);
    }

    private static boolean isSet(String action) {
        return action != null && !action.isEmpty() && !"None".equals(action);
    }
}
