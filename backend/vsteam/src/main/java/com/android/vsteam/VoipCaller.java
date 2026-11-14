package com.android.vsteam;

import android.annotation.SuppressLint;
import android.app.Notification;
import android.app.Person;
import android.os.Binder;
import android.os.Bundle;
import android.os.IBinder;
import android.os.SystemClock;
import android.service.notification.StatusBarNotification;
import android.telephony.TelephonyManager;
import android.text.TextUtils;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Caller name for the phone state broadcast of a self-managed (VoIP) call, called from Telecom
 * by bin/patch_telecom_voip_bridge.py. Watch apps of every brand ring from that broadcast.
 *
 * Some apps hide the caller from Telecom (Messenger reports every call as "Messenger user"), but
 * all of them show the real name in their incoming-call notification, so prefer that.
 */
public final class VoipCaller {

    /** Prefix of a caller produced by {@link #tag}; never leaves {@link #notifyState}. */
    private static final String TAG = "\u0001vs\u0001";
    /**
     * How long the RINGING of a VoIP call is held back for the app's call notification. Watches
     * take the caller from the first RINGING only, so a name that arrives later is lost.
     */
    private static final long RING_WAIT_MS = 1200L;
    private static final long POLL_MS = 40L;
    private static final int MAX_LENGTH = 40;
    // Notification.CallStyle.CALL_TYPE_INCOMING
    private static final int CALL_TYPE_INCOMING = 1;

    // Reads the notifications and sends the held-back states, outside Telecom's lock.
    private static final ExecutorService LOOKUP = Executors.newSingleThreadExecutor(runnable -> {
        Thread thread = new Thread(runnable, "VoipCaller");
        thread.setDaemon(true);
        return thread;
    });
    private static final Object QUEUE_LOCK = new Object();
    /** States handed to {@link #LOOKUP} and not sent yet. */
    private static int sQueued;
    private static volatile Method sNotifyState;
    private static Object sNotificationManager;
    private static Method sGetActiveNotifications;

    private VoipCaller() {
    }

    /** Marks the caller of a self-managed call: its Telecom display name, or else its handle. */
    public static String tag(String displayName, String handle) {
        String caller = TextUtils.isEmpty(displayName) ? handle : displayName;
        return TAG + (caller != null ? caller : "");
    }

    /**
     * Sends a phone state for Telecom, in place of
     * {@code TelephonyRegistryManager#notifyCallStateChangedForAllSubscriptions}.
     *
     * The RINGING of a call that {@link #tag} marked is sent from the worker thread, once the
     * app's ringing-call notification shows who is calling (or after {@link #RING_WAIT_MS}).
     * Telecom reports RINGING while it holds its lock, and NotificationManagerService needs that
     * lock to post a call notification, so the name can never be read from inside this call.
     * States that follow while a RINGING is still waiting go through the same thread, in order.
     * Everything else is sent at once on the calling thread, as Telecom does itself.
     *
     * @param registry the TelephonyRegistryManager Telecom uses
     * @param state    the TelephonyManager call state
     * @throws Exception when nothing was sent; Telecom then makes the stock call
     */
    public static void notifyState(Object registry, int state, String caller) throws Exception {
        Method notify = notifyMethod(registry);
        boolean tagged = caller != null && caller.startsWith(TAG);
        String fallback = !tagged ? caller
                : caller.length() > TAG.length() ? caller.substring(TAG.length()) : null;
        boolean ringing = tagged && state == TelephonyManager.CALL_STATE_RINGING;
        synchronized (QUEUE_LOCK) {
            if (ringing || sQueued > 0) {
                sQueued++;
                LOOKUP.execute(() -> sendQueued(notify, registry, state, fallback, ringing));
                return;
            }
        }
        send(notify, registry, state, fallback);
    }

    private static void sendQueued(Method notify, Object registry, int state, String fallback,
                                   boolean ringing) {
        try {
            String caller = fallback;
            if (ringing) {
                try {
                    String name = waitForRingingCaller(RING_WAIT_MS);
                    if (name != null) {
                        caller = name;
                    }
                } catch (Throwable ignored) {
                    // Keep the name Telecom has.
                }
            }
            long identity = Binder.clearCallingIdentity();
            try {
                send(notify, registry, state, caller);
            } finally {
                Binder.restoreCallingIdentity(identity);
            }
        } catch (Throwable ignored) {
            // Nothing to hand the failure to on this thread.
        } finally {
            synchronized (QUEUE_LOCK) {
                sQueued--;
            }
        }
    }

    private static void send(Method notify, Object registry, int state, String caller)
            throws Exception {
        try {
            notify.invoke(registry, state, caller);
        } catch (InvocationTargetException e) {
            // Telecom catches the SecurityException of the stock call.
            if (e.getCause() instanceof RuntimeException) {
                throw (RuntimeException) e.getCause();
            }
            throw e;
        }
    }

    private static Method notifyMethod(Object registry) throws Exception {
        Method notify = sNotifyState;
        if (notify == null) {
            notify = registry.getClass().getMethod(
                    "notifyCallStateChangedForAllSubscriptions", int.class, String.class);
            sNotifyState = notify;
        }
        return notify;
    }

    /** Null when no ringing-call notification showed up; gives up early once another state waits. */
    private static String waitForRingingCaller(long wait) throws Exception {
        long deadline = SystemClock.uptimeMillis() + wait;
        long identity = Binder.clearCallingIdentity();
        try {
            while (true) {
                String name = ringingCaller();
                if (name != null || SystemClock.uptimeMillis() >= deadline) {
                    return name;
                }
                synchronized (QUEUE_LOCK) {
                    if (sQueued > 1) {
                        return null; // The call already moved on: answered, rejected or missed.
                    }
                }
                Thread.sleep(POLL_MS);
            }
        } finally {
            Binder.restoreCallingIdentity(identity);
        }
    }

    /** The caller shown by a ringing-call notification, or null when none is posted. */
    private static String ringingCaller() throws Exception {
        for (StatusBarNotification sbn : activeNotifications()) {
            Notification notification = sbn.getNotification();
            Bundle extras = notification != null ? notification.extras : null;
            if (extras == null) {
                continue;
            }
            CharSequence name = null;
            int callType = extras.getInt(Notification.EXTRA_CALL_TYPE, 0);
            if (callType == CALL_TYPE_INCOMING) {
                Person person = extras.getParcelable(Notification.EXTRA_CALL_PERSON, Person.class);
                name = person != null ? person.getName() : null;
            } else if (callType != 0 || !Notification.CATEGORY_CALL.equals(notification.category)
                    || notification.fullScreenIntent == null) {
                // Not ringing: an ongoing call, or no call at all.
                continue;
            }
            if (TextUtils.isEmpty(name)) {
                name = extras.getCharSequence(Notification.EXTRA_TITLE);
            }
            if (!TextUtils.isEmpty(name)) {
                String caller = name.toString().trim();
                return caller.length() > MAX_LENGTH ? caller.substring(0, MAX_LENGTH) : caller;
            }
        }
        return null;
    }

    @SuppressLint("PrivateApi")
    private static StatusBarNotification[] activeNotifications() throws Exception {
        if (sGetActiveNotifications == null) {
            IBinder binder = (IBinder) Class.forName("android.os.ServiceManager")
                    .getMethod("getService", String.class).invoke(null, "notification");
            sNotificationManager = Class.forName("android.app.INotificationManager$Stub")
                    .getMethod("asInterface", IBinder.class).invoke(null, binder);
            // Telecom runs in system_server, where this is the service itself rather than a proxy;
            // go through the interface, the implementing class is not public.
            sGetActiveNotifications = Class.forName("android.app.INotificationManager").getMethod(
                    "getActiveNotificationsWithAttribution", String.class, String.class);
        }
        StatusBarNotification[] active = (StatusBarNotification[])
                sGetActiveNotifications.invoke(sNotificationManager, "android", null);
        return active != null ? active : new StatusBarNotification[0];
    }
}
