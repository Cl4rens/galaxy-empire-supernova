"""The client code patches, on small smali samples shaped like the client's code."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import compat  # noqa: E402

ACTIVITY = """.class public Lcom/tap4fun/spartanwar/GameActivity;
.super Landroid/app/Activity;

.method private CheckPermissionForAndroid_M()V
    .locals 6

    new-instance v1, Ljava/util/ArrayList;
    invoke-direct {v1}, Ljava/util/ArrayList;-><init>()V

    const-string v3, "android.permission.WRITE_EXTERNAL_STORAGE"

    invoke-direct {p0, v1, v3}, Lcom/tap4fun/spartanwar/GameActivity;->addPermission(Ljava/util/List;Ljava/lang/String;)V

    const-string v3, "android.permission.READ_PHONE_STATE"

    invoke-direct {p0, v1, v3}, Lcom/tap4fun/spartanwar/GameActivity;->addPermission(Ljava/util/List;Ljava/lang/String;)V

    return-void
.end method

.method private startMobileAppTracking()V
    .locals 4

    invoke-virtual {v1}, Landroid/telephony/TelephonyManager;->getDeviceId()Ljava/lang/String;
    move-result-object v1
    return-void
.end method

.method protected onStart()V
    .locals 1

    sget-object v0, Lcom/tap4fun/spartanwar/GameActivity;->FLURRY_API_KEY:Ljava/lang/String;

    invoke-static {p0, v0}, Lcom/flurry/android/FlurryAgent;->onStartSession(Landroid/content/Context;Ljava/lang/String;)V

    invoke-super {p0}, Landroid/app/Activity;->onStart()V

    return-void
.end method

.method private logEvent()V
    .locals 2

    invoke-static {v0, v1}, Lcom/flurry/android/FlurryAgent;->logEvent(Ljava/lang/String;Ljava/util/Map;)Lcom/flurry/android/FlurryEventRecordStatus;

    move-result-object v0

    new-instance v1, Lcom/appsflyer/AppsFlyerLib;
    invoke-direct {v1}, Lcom/appsflyer/AppsFlyerLib;-><init>()V
    return-void
.end method
"""

DATA = """.method public static path()Ljava/lang/String;
    .locals 1
    invoke-static {}, Landroid/os/Environment;->getExternalStorageDirectory()Ljava/io/File;
    move-result-object v0
    return-object v0
.end method
"""


def decoded(tmp_path):
    game = tmp_path / "smali" / "com" / "tap4fun" / "spartanwar"
    game.mkdir(parents=True)
    (game / "GameActivity.smali").write_text(ACTIVITY.replace(
        ".method private logEvent", "\n".join(f".method private {m}()V\n    .locals 0\n    return-void\n.end method\n"
                                              for m in compat.STUBBED_METHODS if m != "startMobileAppTracking")
        + "\n.method private logEvent"))
    (game / "DataUtils.smali").write_text(DATA)
    return tmp_path


def test_patches(tmp_path):
    root = decoded(tmp_path)
    stats = compat.patch_smali(root)
    assert stats == {"tracking setups": len(compat.STUBBED_METHODS), "tracking calls": 2,
                     "storage calls": 1, "permission requests": 2}
    activity = (root / "smali/com/tap4fun/spartanwar/GameActivity.smali").read_text()
    assert "FlurryAgent" not in activity and "addPermission" not in activity and "getDeviceId" not in activity
    assert "invoke-super {p0}, Landroid/app/Activity;->onStart()V" in activity
    assert "const/4 v0, 0x0" in activity  # the dropped logEvent result
    assert "AppsFlyerLib;-><init>()V" in activity  # constructors stay
    data = (root / "smali/com/tap4fun/spartanwar/DataUtils.smali").read_text()
    assert compat.COMPAT_EXTERNAL_DIR in data and "getExternalStorageDirectory" not in data
    assert (root / "smali/org/supernova/Compat.smali").exists()
