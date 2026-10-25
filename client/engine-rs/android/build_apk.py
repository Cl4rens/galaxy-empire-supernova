"""Build the Supernova Android app (an APK without anything from the game in it).

Needs the Android SDK with build-tools, a platform and the NDK, cargo-ndk, and Java (for
Ruffle's build and for signing). Without Gradle: cargo-ndk builds libsupernova.so per ABI,
aapt2 makes the APK from AndroidManifest.xml, zipalign and apksigner finish it.

    python android/build_apk.py                      # arm64-v8a (phones) and x86_64 (emulators)
    python android/build_apk.py --abi x86_64         # one ABI only
    python android/build_apk.py --skip-build         # package the libraries built last time

The APK is signed with a key made on first use (target/android/supernova.keystore); keep it to
install updates over an installed app.
"""

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent
OUT = ENGINE / "target" / "android"
ABIS = ["arm64-v8a", "x86_64"]
MIN_SDK = "24"
KEY_ALIAS = "supernova"
KEY_PASS = "supernova"


def sdk_root() -> Path:
    for name in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(name):
            return Path(os.environ[name])
    default = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Android" / "Sdk"
    if default.exists():
        return default
    sys.exit("Android SDK not found: set ANDROID_HOME")


def newest(folder: Path) -> Path:
    versions = sorted((p for p in folder.iterdir() if p.is_dir()), key=lambda p: [int(x) for x in p.name.split(".") if x.isdigit()])
    if not versions:
        sys.exit(f"nothing installed in {folder}")
    return versions[-1]


def tool(folder: Path, name: str) -> str:
    for suffix in ("", ".exe", ".bat"):
        if (folder / (name + suffix)).exists():
            return str(folder / (name + suffix))
    sys.exit(f"{name} not found in {folder}")


def run(*args: str, env=None) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, check=True, env=env)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--abi", action="append", choices=ABIS, help="ABI to build (repeatable)")
    parser.add_argument("--skip-build", action="store_true", help="only package")
    args = parser.parse_args()
    abis = args.abi or ABIS

    sdk = sdk_root()
    build_tools = newest(sdk / "build-tools")
    platform = newest(sdk / "platforms")
    libs = OUT / "lib"

    if not args.skip_build:
        env = dict(os.environ, ANDROID_HOME=str(sdk), ANDROID_NDK_HOME=str(newest(sdk / "ndk")))
        targets = [x for abi in abis for x in ("-t", abi)]
        run("cargo", "ndk", *targets, "--platform", MIN_SDK, "-o", str(libs),
            "build", "--release", "-p", "supernova-android", env=env)

    OUT.mkdir(parents=True, exist_ok=True)
    unsigned = OUT / "unsigned.apk"
    run(tool(build_tools, "aapt2"), "link", "-o", str(unsigned), "--manifest", str(HERE / "AndroidManifest.xml"),
        "-I", str(platform / "android.jar"))
    with zipfile.ZipFile(unsigned, "a", zipfile.ZIP_DEFLATED) as apk:
        for abi in abis:
            lib = libs / abi / "libsupernova.so"
            if not lib.exists():
                sys.exit(f"missing {lib}: build without --skip-build")
            apk.write(lib, f"lib/{abi}/libsupernova.so")

    aligned = OUT / "aligned.apk"
    run(tool(build_tools, "zipalign"), "-f", "-p", "4", str(unsigned), str(aligned))

    keystore = OUT / "supernova.keystore"
    if not keystore.exists():
        keytool = shutil.which("keytool") or sys.exit("keytool (Java) not found")
        run(keytool, "-genkeypair", "-keystore", str(keystore), "-alias", KEY_ALIAS, "-keyalg", "RSA",
            "-keysize", "2048", "-validity", "10000", "-storepass", KEY_PASS, "-keypass", KEY_PASS,
            "-dname", "CN=Supernova")
    final = OUT / "supernova.apk"
    run(tool(build_tools, "apksigner"), "sign", "--ks", str(keystore), "--ks-key-alias", KEY_ALIAS,
        "--ks-pass", f"pass:{KEY_PASS}", "--key-pass", f"pass:{KEY_PASS}", "--out", str(final), str(aligned))
    unsigned.unlink()
    aligned.unlink()
    print(f"built {final} ({final.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main()
