package com.android.server.input.shortcut.singlekeyrule;

import android.os.Bundle;

public class PowerKeyRule {
    public boolean postTriggerFunction(String param1, String param2, Bundle param3, boolean param4) {
        return true; // Giá trị trả về chỉ là ví dụ
    }

    public void test() {
        Bundle bundle = null;
        postTriggerFunction("double_click_power_key", "test", bundle, true);
    }


}
