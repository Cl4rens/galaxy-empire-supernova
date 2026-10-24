"""Code patches that let the client run on current Android versions.

The original client crashes on Android 6+ at its first start: while it asks for the storage and
phone permissions, onStart() starts a Flurry session whose API key is only set after the
permissions were granted. Besides, it keeps its data on the shared storage (/sdcard), which
needs a permission that Android keeps restricting.

classes.dex is disassembled with apktool (smali), patched as text and assembled again:

1. The tracking SDKs (Flurry, MobileAppTracker, AppsFlyer, Umeng, Facebook app events) are
   switched off: their setup methods become empty and calls into them are removed from the
   game's code. Their servers are gone; they only crashed (one even read the IMEI).
2. Environment.getExternalStorageDirectory() is replaced by Compat.externalDir(), the app's
   own folder on the shared storage (Android/data/com.tap4fun.ge/files), which needs no
   permission.
3. The client no longer asks for the storage and phone permissions (nothing needs them now).
"""

import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

APKTOOL_URL = "https://github.com/iBotPeaches/Apktool/releases/download/v3.0.3/apktool_3.0.3.jar"
APKTOOL_SHA256 = "dbf930b076c6b9be08d57c449cacefc3bdd6b71ebd59b3066fc0e1f5b14f9423"

GAME_CODE = "smali/com/tap4fun"
TRACKING_SDKS = ("Lcom/flurry/", "Lcom/mobileapptracker/", "Lcom/appsflyer/", "Lcom/umeng/",
                 "Lcom/facebook/appevents/AppEventsLogger;")
# GameActivity methods that only set up the tracking SDKs; they become empty.
STUBBED_METHODS = ("initFlurry", "initMobileAppTracking", "startMobileAppTracking", "initUMENG",
                   "initAppsflyer", "startAppsflyer")
SKIP_FILES = ("MyInstallReceiver.smali",)  # constructs SDK objects; harmless, and removing <init> breaks it
REMOVED_PERMISSIONS = ("android.permission.WRITE_EXTERNAL_STORAGE", "android.permission.READ_PHONE_STATE")

EXTERNAL_STORAGE = "Landroid/os/Environment;->getExternalStorageDirectory()Ljava/io/File;"
COMPAT_EXTERNAL_DIR = "Lorg/supernova/Compat;->externalDir()Ljava/io/File;"

COMPAT_SMALI = """.class public Lorg/supernova/Compat;
.super Ljava/lang/Object;
.source "Compat.java"

# Added by the Supernova patcher: the app's own folder on the shared storage.

.method public static externalDir()Ljava/io/File;
    .registers 4

    :try_start_0
    const-string v0, "android.app.ActivityThread"
    invoke-static {v0}, Ljava/lang/Class;->forName(Ljava/lang/String;)Ljava/lang/Class;
    move-result-object v0
    const-string v1, "currentApplication"
    const/4 v2, 0x0
    new-array v2, v2, [Ljava/lang/Class;
    invoke-virtual {v0, v1, v2}, Ljava/lang/Class;->getMethod(Ljava/lang/String;[Ljava/lang/Class;)Ljava/lang/reflect/Method;
    move-result-object v0
    const/4 v1, 0x0
    const/4 v2, 0x0
    new-array v2, v2, [Ljava/lang/Object;
    invoke-virtual {v0, v1, v2}, Ljava/lang/reflect/Method;->invoke(Ljava/lang/Object;[Ljava/lang/Object;)Ljava/lang/Object;
    move-result-object v0
    check-cast v0, Landroid/content/Context;
    if-eqz v0, :fallback
    const/4 v1, 0x0
    invoke-virtual {v0, v1}, Landroid/content/Context;->getExternalFilesDir(Ljava/lang/String;)Ljava/io/File;
    move-result-object v1
    if-eqz v1, :internal
    return-object v1
    :internal
    invoke-virtual {v0}, Landroid/content/Context;->getFilesDir()Ljava/io/File;
    move-result-object v1
    return-object v1
    :try_end_0
    .catch Ljava/lang/Exception; {:try_start_0 .. :try_end_0} :fallback

    :fallback
    invoke-static {}, Landroid/os/Environment;->getExternalStorageDirectory()Ljava/io/File;
    move-result-object v0
    return-object v0
.end method
"""

INVOKE = re.compile(r"^\s*invoke-[a-z/-]+ \{[^}]*\}, (L[^;]+;)->([^(]+)\(")
MOVE_RESULT = re.compile(r"^(\s*)move-result(?:-object|-wide)? (\w+)\s*$")


def get_apktool(tools_dir: Path) -> Path:
    jar = tools_dir / Path(APKTOOL_URL).name
    if not jar.exists():
        print(f"Downloading {jar.name} ...")
        tools_dir.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(APKTOOL_URL, jar)
    if hashlib.sha256(jar.read_bytes()).hexdigest() != APKTOOL_SHA256:
        jar.unlink()
        sys.exit(f"{jar.name}: checksum mismatch, download removed. Please try again.")
    return jar


def remove_tracking(text: str) -> tuple[str, int]:
    """Drop calls into the tracking SDKs. A result that was used becomes null; constructors stay."""
    lines = text.split("\n")
    out, removed, i = [], 0, 0
    while i < len(lines):
        m = INVOKE.match(lines[i])
        if m and m.group(1).startswith(TRACKING_SDKS) and m.group(2) != "<init>":
            removed += 1
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            result = MOVE_RESULT.match(lines[j]) if j < len(lines) else None
            if result:
                const = "const-wide/16" if "wide" in lines[j] else "const/4"
                out.append(f"{result.group(1)}{const} {result.group(2)}, 0x0")
                i = j + 1
            else:
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out), removed


def stub_methods(text: str, names: tuple[str, ...]) -> tuple[str, int]:
    """Replace the bodies of the void methods `names` with return-void."""
    pattern = re.compile(r"^(\.method [^\n]* (?:" + "|".join(map(re.escape, names)) + r")\(\)V)\n.*?^\.end method",
                         re.M | re.S)
    return pattern.subn(lambda m: m.group(1) + "\n    .registers 1\n\n    return-void\n.end method", text)


def patch_smali(root: Path) -> dict[str, int]:
    stats = {"tracking setups": 0, "tracking calls": 0, "storage calls": 0, "permission requests": 0}
    for path in (root / GAME_CODE).rglob("*.smali"):
        text = path.read_text(encoding="utf-8")
        original = text
        if path.name not in SKIP_FILES:
            text, n = remove_tracking(text)
            stats["tracking calls"] += n
        stats["storage calls"] += text.count(EXTERNAL_STORAGE)
        text = text.replace(EXTERNAL_STORAGE, COMPAT_EXTERNAL_DIR)
        if path.name == "GameActivity.smali":
            text, n = stub_methods(text, STUBBED_METHODS)
            stats["tracking setups"] += n
            for permission in REMOVED_PERMISSIONS:
                pattern = re.compile(rf'^(\s*)const-string (\w+), "{re.escape(permission)}"\n(?:\s*\n)*'
                                     r'\s*invoke-direct \{[^}]*\}, Lcom/tap4fun/spartanwar/GameActivity;->addPermission'
                                     r'\(Ljava/util/List;Ljava/lang/String;\)V\n', re.M)
                text, n = pattern.subn("", text)
                stats["permission requests"] += n
        if text != original:
            path.write_text(text, encoding="utf-8")
    compat = root / "smali" / "org" / "supernova" / "Compat.smali"
    compat.parent.mkdir(parents=True, exist_ok=True)
    compat.write_text(COMPAT_SMALI, encoding="utf-8")
    return stats


def patched_dex(java: str, apk: Path, tools_dir: Path) -> bytes:
    """classes.dex of `apk` with the compatibility patches."""
    apktool = get_apktool(tools_dir)
    with tempfile.TemporaryDirectory() as tmp:
        decoded, built = Path(tmp) / "decoded", Path(tmp) / "code.apk"
        subprocess.run([java, "-jar", str(apktool), "d", "--no-res", "--no-assets", "-f", "-o", str(decoded), str(apk)],
                       check=True, stdout=subprocess.DEVNULL)
        stats = patch_smali(decoded)
        print("Compatibility patches: " + ", ".join(f"{n} {what}" for what, n in stats.items()))
        if (stats["permission requests"] != len(REMOVED_PERMISSIONS) or not stats["storage calls"]
                or stats["tracking setups"] != len(STUBBED_METHODS)):
            sys.exit("The client code does not look as expected; compatibility patches failed.")
        subprocess.run([java, "-jar", str(apktool), "b", "-f", "-o", str(built), str(decoded)],
                       check=True, stdout=subprocess.DEVNULL)
        with zipfile.ZipFile(built) as z:
            return z.read("classes.dex")


if __name__ == "__main__":  # python tools/compat.py <decoded dir>: patch an apktool folder in place
    print(patch_smali(Path(sys.argv[1])))
    shutil.rmtree(Path(sys.argv[1]) / "build", ignore_errors=True)
