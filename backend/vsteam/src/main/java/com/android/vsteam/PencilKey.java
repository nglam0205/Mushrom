package com.android.vsteam;

import android.content.Context;
import android.graphics.Point;
import android.hardware.display.DisplayManager;
import android.hardware.input.InputManager;
import android.os.IBinder;
import android.os.ParcelFileDescriptor;
import android.os.SystemClock;
import android.view.Display;
import android.view.InputDevice;
import android.view.InputEvent;
import android.view.KeyCharacterMap;
import android.view.KeyEvent;
import android.view.MotionEvent;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.lang.reflect.Method;

/**
 * Stylus button shortcuts, called from OplusExSystemService by
 * bin/patch_oplus_pencil_button.py of the build tool.
 *
 * ExSystemService turns the button of OPPO/OnePlus pencils into gestures in two places: the
 * KEY_F21 handler of pencils with a physical button (single click, double click, long press)
 * and the BLE GATT notify of the Pro pencils (single and double click). Both ask
 * {@link #onGesture} first; when the Super shortcut screen of VSTeam picked an action for that
 * gesture in Settings.Global {@code vs_pencil_<gesture>_press}, it runs through
 * {@link Toolbox#Handle_VS_Action_Boolean} like every other shortcut and the stock broadcast is
 * skipped. Unset or "None" keeps the ColorOS behaviour.
 *
 * The pencil-only actions (back, zoom in, zoom out) run here instead: an activity would take
 * the focus from the app they act on. ExSystemService runs as system, so it may inject input.
 */
public final class PencilKey {

    public static final int GESTURE_SINGLE = 1;
    public static final int GESTURE_DOUBLE = 2;
    public static final int GESTURE_LONG = 3;

    private static final String[] KEYS = {
            null, "vs_pencil_single_press", "vs_pencil_double_press", "vs_pencil_long_press"};
    // Laser pointer mode of the Pro pencils owns the double click, as stock does.
    private static final String LASER_ONLY = "ipe_setting_only_laser_enable";

    /** Back, or backspace while the keyboard is up. */
    private static final String ACTION_BACK = "pencil_back";
    private static final String ACTION_ZOOM_IN = "pencil_zoom_in";
    private static final String ACTION_ZOOM_OUT = "pencil_zoom_out";

    /** InputManager.INJECT_INPUT_EVENT_MODE_ASYNC */
    private static final int INJECT_ASYNC = 0;
    private static final int PINCH_STEPS = 12;
    private static final long PINCH_STEP_MS = 12;
    /** Finger gap at both ends of the pinch, as a share of the short side of the screen. */
    private static final float PINCH_NEAR = 0.1f;
    private static final float PINCH_FAR = 0.4f;

    private PencilKey() {
    }

    /** Key path of ExSystemService; {@code what} of its handler message is the gesture. */
    public static boolean onKeyMessage(int what) {
        return onGesture(what);
    }

    /** GATT path; laser mode keeps the stock double click. */
    public static boolean onGattClick(int gesture) {
        if (gesture == GESTURE_DOUBLE && Toolbox.getIntGlobal(LASER_ONLY) == 1) {
            return false;
        }
        return onGesture(gesture);
    }

    /**
     * True when a long press action is picked: the button's down/up then stay away from the
     * smart collect of healthservice, the stock long press.
     */
    public static boolean ownsLongPress() {
        return isSet(action(GESTURE_LONG));
    }

    /** @return true when the gesture was handled here and the stock action must be skipped */
    public static boolean onGesture(int gesture) {
        try {
            String action = action(gesture);
            if (!isSet(action)) {
                return false;
            }
            switch (action) {
                case ACTION_BACK:
                case ACTION_ZOOM_IN:
                case ACTION_ZOOM_OUT:
                    // Off the ExSystemService handler: the pinch sleeps between moves.
                    new Thread(() -> runInput(action), "VsPencilKey").start();
                    return true;
                default:
                    return Toolbox.Handle_VS_Action_Boolean(action);
            }
        } catch (Throwable t) {
            Toolbox.vslog("PencilKey: " + t);
            return false;
        }
    }

    private static String action(int gesture) {
        if (gesture < GESTURE_SINGLE || gesture > GESTURE_LONG) {
            return null;
        }
        return Toolbox.getStringGlobal(KEYS[gesture]);
    }

    private static void runInput(String action) {
        try {
            switch (action) {
                case ACTION_BACK:
                    injectKey(imeShown() ? KeyEvent.KEYCODE_DEL : KeyEvent.KEYCODE_BACK);
                    break;
                case ACTION_ZOOM_IN:
                    pinch(PINCH_NEAR, PINCH_FAR);
                    break;
                case ACTION_ZOOM_OUT:
                    pinch(PINCH_FAR, PINCH_NEAR);
                    break;
            }
        } catch (Throwable t) {
            Toolbox.vslog("PencilKey " + action + ": " + t);
        }
    }

    private static void injectKey(int keyCode) throws Exception {
        long now = SystemClock.uptimeMillis();
        int flags = KeyEvent.FLAG_FROM_SYSTEM | KeyEvent.FLAG_VIRTUAL_HARD_KEY;
        for (int action : new int[]{KeyEvent.ACTION_DOWN, KeyEvent.ACTION_UP}) {
            inject(new KeyEvent(now, now, action, keyCode, 0, 0, KeyCharacterMap.VIRTUAL_KEYBOARD,
                    0, flags, InputDevice.SOURCE_KEYBOARD));
        }
    }

    /**
     * Two fingers on the middle of the screen, moving from a gap of {@code from} to {@code to}
     * (shares of the short side): apart zooms in, together zooms out.
     */
    private static void pinch(float from, float to) throws Exception {
        Point size = new Point();
        Toolbox.getCon().getSystemService(DisplayManager.class)
                .getDisplay(Display.DEFAULT_DISPLAY).getRealSize(size);
        float cx = size.x / 2f;
        float cy = size.y / 2f;
        float side = Math.min(size.x, size.y);

        MotionEvent.PointerProperties[] props = new MotionEvent.PointerProperties[2];
        MotionEvent.PointerCoords[] coords = new MotionEvent.PointerCoords[2];
        for (int i = 0; i < 2; i++) {
            props[i] = new MotionEvent.PointerProperties();
            props[i].id = i;
            props[i].toolType = MotionEvent.TOOL_TYPE_FINGER;
            coords[i] = new MotionEvent.PointerCoords();
            coords[i].pressure = 1f;
            coords[i].size = 1f;
        }

        long down = SystemClock.uptimeMillis();
        for (int step = 0; step <= PINCH_STEPS; step++) {
            float half = side * (from + (to - from) * step / PINCH_STEPS) / 2f;
            // Along the diagonal, so both fingers stay on screen in either orientation.
            float d = half * 0.7071f;
            coords[0].x = cx - d;
            coords[0].y = cy - d;
            coords[1].x = cx + d;
            coords[1].y = cy + d;
            if (step == 0) {
                inject(motion(down, MotionEvent.ACTION_DOWN, 1, props, coords));
                inject(motion(down, MotionEvent.ACTION_POINTER_DOWN
                        | (1 << MotionEvent.ACTION_POINTER_INDEX_SHIFT), 2, props, coords));
            } else {
                SystemClock.sleep(PINCH_STEP_MS);
                inject(motion(down, MotionEvent.ACTION_MOVE, 2, props, coords));
            }
        }
        inject(motion(down, MotionEvent.ACTION_POINTER_UP
                | (1 << MotionEvent.ACTION_POINTER_INDEX_SHIFT), 2, props, coords));
        inject(motion(down, MotionEvent.ACTION_UP, 1, props, coords));
    }

    private static MotionEvent motion(long down, int action, int pointers,
            MotionEvent.PointerProperties[] props, MotionEvent.PointerCoords[] coords) {
        return MotionEvent.obtain(down, SystemClock.uptimeMillis(), action, pointers, props,
                coords, 0, 0, 1f, 1f, 0, 0, InputDevice.SOURCE_TOUCHSCREEN, 0);
    }

    private static void inject(InputEvent event) throws Exception {
        InputManager im = Toolbox.getCon().getSystemService(InputManager.class);
        Method method = InputManager.class.getMethod("injectInputEvent", InputEvent.class, int.class);
        method.invoke(im, event, INJECT_ASYNC);
    }

    /**
     * Whether the soft keyboard is up, read from the input_method dump ("mInputShown=true");
     * no API tells another process. Unknown counts as hidden, so the button goes back.
     */
    private static boolean imeShown() {
        try {
            IBinder service = (IBinder) Class.forName("android.os.ServiceManager")
                    .getMethod("getService", String.class)
                    .invoke(null, Context.INPUT_METHOD_SERVICE);
            if (service == null) {
                return false;
            }
            ParcelFileDescriptor[] pipe = ParcelFileDescriptor.createPipe();
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            // Drained on its own thread: a dump longer than the pipe buffer would block.
            Thread reader = new Thread(() -> {
                try (InputStream in = new ParcelFileDescriptor.AutoCloseInputStream(pipe[0])) {
                    byte[] buf = new byte[8192];
                    int n;
                    while ((n = in.read(buf)) > 0) {
                        out.write(buf, 0, n);
                    }
                } catch (Throwable ignored) {
                }
            });
            reader.start();
            try {
                service.dump(pipe[1].getFileDescriptor(), new String[0]);
            } finally {
                pipe[1].close();
            }
            reader.join(1000);
            return out.toString().contains("mInputShown=true");
        } catch (Throwable t) {
            Toolbox.vslog("PencilKey imeShown: " + t);
            return false;
        }
    }

    private static boolean isSet(String action) {
        return action != null && !action.isEmpty() && !"None".equals(action);
    }
}
