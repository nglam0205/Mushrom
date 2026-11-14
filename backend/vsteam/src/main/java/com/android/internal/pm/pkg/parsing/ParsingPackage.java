package com.android.internal.pm.pkg.parsing;

import android.os.Bundle;

import com.android.internal.pm.pkg.component.ParsedActivity;

/**
 * Compile-time stub for the framework-internal interface. Provided by framework.jar at
 * runtime; not shipped (preparefiles only copies com/android/vsteam/*).
 */
public interface ParsingPackage {
    ParsingPackage addReceiver(ParsedActivity activity);

    String getPackageName();

    ParsingPackage setMetaData(Bundle metaData);
}
