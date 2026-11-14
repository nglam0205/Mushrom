.class public final Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;
.super Ljava/lang/Object;
.source "VSTeamConfigOverride.java"


# static fields
.field private static final KEY:Ljava/lang/String; = "vs_oplus_camera_config_delta"

.field private static final VERSION:Ljava/lang/String; = "global-delta-v7-on-open"

.field private static volatile sLoaded:Z

.field private static volatile sLastDelta:Ljava/lang/String;

.field private static sLoggedTags:Ljava/util/HashSet;

.field private static volatile sRealtimeAdd:Lorg/json/JSONArray;

.field private static volatile sRealtimeSet:Lorg/json/JSONObject;


# direct methods
.method private constructor <init>()V
    .registers 1

    .line 15
    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method

.method private static logApplied(Ljava/lang/String;I)V
    .registers 4

    new-instance v0, Ljava/lang/StringBuilder;

    const-string v1, "Applied "

    invoke-direct {v0, v1}, Ljava/lang/StringBuilder;-><init>(Ljava/lang/String;)V

    invoke-virtual {v0, p0}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    const-string p0, " index="

    invoke-virtual {v0, p0}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v0, p1}, Ljava/lang/StringBuilder;->append(I)Ljava/lang/StringBuilder;

    invoke-virtual {v0}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object p0

    const-string p1, "VSTeamCameraConfig"

    invoke-static {p1, p0}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method

.method private static declared-synchronized logRealtimeApplied(Ljava/lang/String;Ljava/lang/String;)V
    .registers 5

    sget-object v0, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->sLoggedTags:Ljava/util/HashSet;

    if-nez v0, :has_set

    new-instance v0, Ljava/util/HashSet;

    invoke-direct {v0}, Ljava/util/HashSet;-><init>()V

    sput-object v0, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->sLoggedTags:Ljava/util/HashSet;

    :has_set
    invoke-virtual {v0, p1}, Ljava/util/HashSet;->add(Ljava/lang/Object;)Z

    move-result v0

    if-eqz v0, :return

    new-instance v0, Ljava/lang/StringBuilder;

    const-string v1, "Camera-open applied "

    invoke-direct {v0, v1}, Ljava/lang/StringBuilder;-><init>(Ljava/lang/String;)V

    invoke-virtual {v0, p0}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    const-string p0, " tag="

    invoke-virtual {v0, p0}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v0, p1}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v0}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object p0

    const-string p1, "VSTeamCameraConfig"

    invoke-static {p1, p0}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I

    :return
    return-void
.end method

.method private static refreshRealtimeDelta()V
    .registers 10

    sget-boolean v0, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->sLoaded:Z

    if-nez v0, :return

    :try_start
    invoke-static {}, Lcom/oplus/ocs/camera/common/util/ContextHolder;->getContext()Landroid/content/Context;

    move-result-object v0

    if-eqz v0, :return

    invoke-virtual {v0}, Landroid/content/Context;->getContentResolver()Landroid/content/ContentResolver;

    move-result-object v0

    const-string v1, "vs_oplus_camera_config_delta"

    invoke-static {v0, v1}, Landroid/provider/Settings$Global;->getString(Landroid/content/ContentResolver;Ljava/lang/String;)Ljava/lang/String;

    move-result-object v0

    const/4 v1, 0x1

    sput-boolean v1, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->sLoaded:Z

    sget-object v1, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->sLastDelta:Ljava/lang/String;

    invoke-static {v0, v1}, Landroid/text/TextUtils;->equals(Ljava/lang/CharSequence;Ljava/lang/CharSequence;)Z

    move-result v1

    if-nez v1, :return

    invoke-static {v0}, Landroid/text/TextUtils;->isEmpty(Ljava/lang/CharSequence;)Z

    move-result v1

    if-eqz v1, :parse

    const/4 v1, 0x0

    sput-object v1, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->sRealtimeSet:Lorg/json/JSONObject;

    sput-object v1, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->sRealtimeAdd:Lorg/json/JSONArray;

    sput-object v1, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->sLoggedTags:Ljava/util/HashSet;

    sput-object v0, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->sLastDelta:Ljava/lang/String;

    const-string v0, "VSTeamCameraConfig"

    const-string v1, "Camera-open delta cleared"

    invoke-static {v0, v1}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I

    return-void

    :parse
    new-instance v1, Lorg/json/JSONObject;

    invoke-direct {v1, v0}, Lorg/json/JSONObject;-><init>(Ljava/lang/String;)V

    const-string v2, "set"

    invoke-virtual {v1, v2}, Lorg/json/JSONObject;->optJSONObject(Ljava/lang/String;)Lorg/json/JSONObject;

    move-result-object v2

    const-string v3, "add"

    invoke-virtual {v1, v3}, Lorg/json/JSONObject;->optJSONArray(Ljava/lang/String;)Lorg/json/JSONArray;

    move-result-object v3

    sput-object v2, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->sRealtimeSet:Lorg/json/JSONObject;

    sput-object v3, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->sRealtimeAdd:Lorg/json/JSONArray;

    const/4 v1, 0x0

    sput-object v1, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->sLoggedTags:Ljava/util/HashSet;

    sput-object v0, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->sLastDelta:Ljava/lang/String;

    const/4 v4, 0x0

    if-eqz v2, :set_count_ready

    invoke-virtual {v2}, Lorg/json/JSONObject;->length()I

    move-result v4

    :set_count_ready
    const/4 v5, 0x0

    if-eqz v3, :add_count_ready

    invoke-virtual {v3}, Lorg/json/JSONArray;->length()I

    move-result v5

    :add_count_ready
    new-instance v6, Ljava/lang/StringBuilder;

    const-string v7, "Camera-open delta loaded: edit="

    invoke-direct {v6, v7}, Ljava/lang/StringBuilder;-><init>(Ljava/lang/String;)V

    invoke-virtual {v6, v4}, Ljava/lang/StringBuilder;->append(I)Ljava/lang/StringBuilder;

    const-string v4, ", add="

    invoke-virtual {v6, v4}, Ljava/lang/StringBuilder;->append(Ljava/lang/String;)Ljava/lang/StringBuilder;

    invoke-virtual {v6, v5}, Ljava/lang/StringBuilder;->append(I)Ljava/lang/StringBuilder;

    invoke-virtual {v6}, Ljava/lang/StringBuilder;->toString()Ljava/lang/String;

    move-result-object v4

    const-string v5, "VSTeamCameraConfig"

    invoke-static {v5, v4}, Landroid/util/Log;->i(Ljava/lang/String;Ljava/lang/String;)I
    :try_end
    .catchall {:try_start .. :try_end} :catchall

    return-void

    :catchall
    move-exception v0

    const-string v1, "VSTeamCameraConfig"

    const-string v2, "Camera-open delta load failed"

    invoke-static {v1, v2, v0}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;Ljava/lang/Throwable;)I

    :return
    return-void
.end method

.method public static getRealtimeOverride(Ljava/lang/String;)Ljava/lang/String;
    .registers 8

    invoke-static {}, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->refreshRealtimeDelta()V

    sget-object v0, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->sRealtimeAdd:Lorg/json/JSONArray;

    const/4 v1, 0x0

    if-eqz v0, :check_set

    :add_loop
    invoke-virtual {v0}, Lorg/json/JSONArray;->length()I

    move-result v2

    if-ge v1, v2, :check_set

    invoke-virtual {v0, v1}, Lorg/json/JSONArray;->optJSONObject(I)Lorg/json/JSONObject;

    move-result-object v2

    if-eqz v2, :add_next

    const-string v3, "VendorTag"

    invoke-virtual {v2, v3}, Lorg/json/JSONObject;->optString(Ljava/lang/String;)Ljava/lang/String;

    move-result-object v3

    invoke-virtual {p0, v3}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v3

    if-eqz v3, :add_next

    const-string v3, "ADD"

    invoke-static {v3, p0}, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->logRealtimeApplied(Ljava/lang/String;Ljava/lang/String;)V

    const-string v3, "Value"

    const-string v4, ""

    invoke-virtual {v2, v3, v4}, Lorg/json/JSONObject;->optString(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;

    move-result-object p0

    return-object p0

    :add_next
    add-int/lit8 v1, v1, 0x1

    goto :add_loop

    :check_set
    sget-object v0, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->sRealtimeSet:Lorg/json/JSONObject;

    if-eqz v0, :not_found

    invoke-virtual {v0}, Lorg/json/JSONObject;->keys()Ljava/util/Iterator;

    move-result-object v1

    :set_loop
    invoke-interface {v1}, Ljava/util/Iterator;->hasNext()Z

    move-result v2

    if-eqz v2, :not_found

    invoke-interface {v1}, Ljava/util/Iterator;->next()Ljava/lang/Object;

    move-result-object v2

    check-cast v2, Ljava/lang/String;

    invoke-virtual {v0, v2}, Lorg/json/JSONObject;->optJSONObject(Ljava/lang/String;)Lorg/json/JSONObject;

    move-result-object v2

    if-eqz v2, :set_loop

    const-string v3, "VendorTag"

    invoke-virtual {v2, v3}, Lorg/json/JSONObject;->optString(Ljava/lang/String;)Ljava/lang/String;

    move-result-object v3

    invoke-virtual {p0, v3}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v3

    if-eqz v3, :set_loop

    const-string v3, "EDIT"

    invoke-static {v3, p0}, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->logRealtimeApplied(Ljava/lang/String;Ljava/lang/String;)V

    const-string v3, "Value"

    const-string v4, ""

    invoke-virtual {v2, v3, v4}, Lorg/json/JSONObject;->optString(Ljava/lang/String;Ljava/lang/String;)Ljava/lang/String;

    move-result-object p0

    return-object p0

    :not_found
    const/4 p0, 0x0

    return-object p0
.end method

.method public static merge(Landroid/content/Context;Ljava/lang/String;)Ljava/lang/String;
    .registers 10

    .line 18
    if-eqz p0, :cond_dc

    invoke-static {p1}, Landroid/text/TextUtils;->isEmpty(Ljava/lang/CharSequence;)Z

    move-result v0

    if-nez v0, :cond_dc

    .line 19
    invoke-virtual {p0}, Landroid/content/Context;->getPackageName()Ljava/lang/String;

    move-result-object v0

    const-string v1, "com.bhlnk.vsteam"

    invoke-virtual {v1, v0}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v0

    if-eqz v0, :cond_16

    goto/16 :goto_dc

    .line 21
    :cond_16
    :try_start_16
    invoke-virtual {p0}, Landroid/content/Context;->getContentResolver()Landroid/content/ContentResolver;

    move-result-object p0

    const-string v0, "vs_oplus_camera_config_delta"

    invoke-static {p0, v0}, Landroid/provider/Settings$Global;->getString(Landroid/content/ContentResolver;Ljava/lang/String;)Ljava/lang/String;

    move-result-object p0

    .line 22
    invoke-static {p0}, Landroid/text/TextUtils;->isEmpty(Ljava/lang/CharSequence;)Z

    move-result v0

    if-eqz v0, :cond_27

    return-object p1

    .line 23
    :cond_27
    new-instance v0, Lorg/json/JSONObject;

    invoke-direct {v0, p0}, Lorg/json/JSONObject;-><init>(Ljava/lang/String;)V

    .line 24
    invoke-virtual {p1}, Ljava/lang/String;->trim()Ljava/lang/String;

    move-result-object p0

    const-string v1, "["

    invoke-virtual {p0, v1}, Ljava/lang/String;->startsWith(Ljava/lang/String;)Z

    move-result p0

    if-eqz p0, :cond_3a

    const/4 p0, 0x0

    goto :goto_3f

    :cond_3a
    new-instance p0, Lorg/json/JSONObject;

    invoke-direct {p0, p1}, Lorg/json/JSONObject;-><init>(Ljava/lang/String;)V
    :try_end_3f
    .catchall {:try_start_16 .. :try_end_3f} :catchall_d3

    .line 25
    :goto_3f
    const-string v1, "file_data"

    if-nez p0, :cond_49

    :try_start_43
    new-instance v2, Lorg/json/JSONArray;

    invoke-direct {v2, p1}, Lorg/json/JSONArray;-><init>(Ljava/lang/String;)V

    goto :goto_4d

    :cond_49
    invoke-virtual {p0, v1}, Lorg/json/JSONObject;->optJSONArray(Ljava/lang/String;)Lorg/json/JSONArray;

    move-result-object v2

    .line 26
    :goto_4d
    if-eqz v2, :cond_d2

    invoke-virtual {v2}, Lorg/json/JSONArray;->length()I

    move-result v3

    if-nez v3, :cond_57

    goto/16 :goto_d2

    .line 28
    :cond_57
    const-string v3, "set"

    invoke-virtual {v0, v3}, Lorg/json/JSONObject;->optJSONObject(Ljava/lang/String;)Lorg/json/JSONObject;

    move-result-object v3

    .line 29
    if-eqz v3, :cond_85

    .line 30
    invoke-virtual {v3}, Lorg/json/JSONObject;->keys()Ljava/util/Iterator;

    move-result-object v4

    .line 31
    :goto_63
    invoke-interface {v4}, Ljava/util/Iterator;->hasNext()Z

    move-result v5

    if-eqz v5, :cond_85

    .line 32
    invoke-interface {v4}, Ljava/util/Iterator;->next()Ljava/lang/Object;

    move-result-object v5

    check-cast v5, Ljava/lang/String;

    .line 33
    invoke-static {v5}, Ljava/lang/Integer;->parseInt(Ljava/lang/String;)I

    move-result v6

    .line 34
    invoke-virtual {v3, v5}, Lorg/json/JSONObject;->optJSONObject(Ljava/lang/String;)Lorg/json/JSONObject;

    move-result-object v5

    .line 35
    if-eqz v5, :cond_84

    if-ltz v6, :cond_84

    invoke-virtual {v2}, Lorg/json/JSONArray;->length()I

    move-result v7

    if-ge v6, v7, :cond_84

    invoke-virtual {v2, v6, v5}, Lorg/json/JSONArray;->put(ILjava/lang/Object;)Lorg/json/JSONArray;

    const-string v7, "EDIT"

    invoke-static {v7, v6}, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->logApplied(Ljava/lang/String;I)V

    .line 36
    :cond_84
    goto :goto_63

    .line 38
    :cond_85
    const-string v3, "delete"

    invoke-virtual {v0, v3}, Lorg/json/JSONObject;->optJSONArray(Ljava/lang/String;)Lorg/json/JSONArray;

    move-result-object v3

    .line 39
    const/4 v4, 0x0

    if-eqz v3, :cond_a8

    .line 40
    move v5, v4

    :goto_8f
    invoke-virtual {v3}, Lorg/json/JSONArray;->length()I

    move-result v6

    if-ge v5, v6, :cond_a8

    .line 41
    const/4 v6, -0x1

    invoke-virtual {v3, v5, v6}, Lorg/json/JSONArray;->optInt(II)I

    move-result v6

    .line 42
    if-ltz v6, :cond_a5

    invoke-virtual {v2}, Lorg/json/JSONArray;->length()I

    move-result v7

    if-ge v6, v7, :cond_a5

    invoke-virtual {v2, v6}, Lorg/json/JSONArray;->remove(I)Ljava/lang/Object;

    const-string v7, "DELETE"

    invoke-static {v7, v6}, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->logApplied(Ljava/lang/String;I)V

    .line 40
    :cond_a5
    add-int/lit8 v5, v5, 0x1

    goto :goto_8f

    .line 45
    :cond_a8
    const-string v3, "add"

    invoke-virtual {v0, v3}, Lorg/json/JSONObject;->optJSONArray(Ljava/lang/String;)Lorg/json/JSONArray;

    move-result-object v0

    .line 46
    if-eqz v0, :cond_c3

    .line 47
    nop

    :goto_b1
    invoke-virtual {v0}, Lorg/json/JSONArray;->length()I

    move-result v3

    if-ge v4, v3, :cond_c3

    .line 48
    invoke-virtual {v0, v4}, Lorg/json/JSONArray;->optJSONObject(I)Lorg/json/JSONObject;

    move-result-object v3

    .line 49
    if-eqz v3, :cond_c0

    invoke-virtual {v2}, Lorg/json/JSONArray;->length()I

    move-result v5

    invoke-virtual {v2, v3}, Lorg/json/JSONArray;->put(Ljava/lang/Object;)Lorg/json/JSONArray;

    const-string v3, "ADD"

    invoke-static {v3, v5}, Lcom/oplus/ocs/camera/consumer/apsAdapter/update/VSTeamConfigOverride;->logApplied(Ljava/lang/String;I)V

    .line 47
    :cond_c0
    add-int/lit8 v4, v4, 0x1

    goto :goto_b1

    .line 52
    :cond_c3
    if-nez p0, :cond_ca

    invoke-virtual {v2}, Lorg/json/JSONArray;->toString()Ljava/lang/String;

    move-result-object p0

    return-object p0

    .line 53
    :cond_ca
    invoke-virtual {p0, v1, v2}, Lorg/json/JSONObject;->put(Ljava/lang/String;Ljava/lang/Object;)Lorg/json/JSONObject;

    .line 54
    invoke-virtual {p0}, Lorg/json/JSONObject;->toString()Ljava/lang/String;

    move-result-object p0
    :try_end_d1
    .catchall {:try_start_43 .. :try_end_d1} :catchall_d3

    return-object p0

    .line 26
    :cond_d2
    :goto_d2
    return-object p1

    .line 55
    :catchall_d3
    move-exception p0

    .line 56
    const-string v0, "VSTeamCameraConfig"

    const-string v1, "Ignoring invalid camera config delta"

    invoke-static {v0, v1, p0}, Landroid/util/Log;->w(Ljava/lang/String;Ljava/lang/String;Ljava/lang/Throwable;)I

    .line 57
    return-object p1

    .line 19
    :cond_dc
    :goto_dc
    return-object p1
.end method
