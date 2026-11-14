package com.miui.home.launcher.common;

public class ChangeableValue<T> {
    public ChangeableValue(T i) {
    }

    public boolean setValue(T i) {
        return true;
    }

    public T getValue() {
        return (T) "0";
    }
}
