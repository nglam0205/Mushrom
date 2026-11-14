import com.android.build.gradle.internal.api.BaseVariantOutputImpl

plugins {
    id("com.android.application")
    id("org.lsposed.lsparanoid")
}

android {
    namespace = "com.vsteam.toolbox"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.vsteam.toolbox"
        minSdk = 34
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
        multiDexEnabled = false
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    applicationVariants.all {
        outputs.all {
            (this as? BaseVariantOutputImpl)?.outputFileName = "vsteam.apk"
        }
    }

}

dependencies {
    implementation("com.madgag.spongycastle:bcpkix-jdk15on:1.58.0.0")
}
