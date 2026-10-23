"""Turn an original Galaxy Empire APK into one that connects to a Supernova server.

Steps:
  1. verify the APK is a supported version (SHA-256)
  2. patch the gateway address in AutoUpdateInit (data1.pak and data1_64.pak)
     and switch the gateway from https to http
  3. rebuild the APK and sign it with a debug key (uber-apk-signer, needs Java)

Usage: python tools/patch_apk.py apk/galaxy-empire-1-9-36.apk --server 192.168.0.10:8000
"""

import argparse
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import lua51
import pak

SUPPORTED = {
    "cfea85feeb17bc5c1573dc9856e75b783a31b5e0d85f469a475917b58abaf4c6": "1.9.36",
}

DATA_DIR = "tap4fun/galaxy_empire/AppOriginalData/Android/"
SCRIPT_PAKS = [DATA_DIR + "data1.pak", DATA_DIR + "data1_64.pak"]
OFFICIAL_GATEWAYS = [
    b"b.portal-platform.tap4fun.com/ge/url_get/online/world",
    b"b.portal-platform.t4f.cn/ge/url_get/online/world",
]

SIGNER_URL = "https://github.com/patrickfav/uber-apk-signer/releases/download/v1.3.0/uber-apk-signer-1.3.0.jar"
SIGNER_SHA256 = "e1299fd6fcf4da527dd53735b56127e8ea922a321128123b9c32d619bba1d835"
TOOLS_DIR = Path("work/tools")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def patch_auto_update(chunk: bytes, gateway: bytes) -> bytes:
    header, main = lua51.load(chunk)
    gateways = scheme = 0
    for fn in main.walk():
        consts = fn.constants
        for i, const in enumerate(consts):
            if const in OFFICIAL_GATEWAYS:
                consts[i] = gateway
                gateways += 1
            # AutoUpdateInit builds every gateway URL as "https://" .. gateway.
            # Our gateway speaks plain HTTP.
            elif const == b"https://":
                consts[i] = b"http://"
                scheme += 1
    if not gateways or not scheme:
        raise RuntimeError(f"AutoUpdateInit not as expected (gateways={gateways}, scheme={scheme})")
    return lua51.dump(header, main)


def patch_game_data(game_zip: bytes, gateway: bytes) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(game_zip))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as dst:
        for info in src.infolist():
            data = src.read(info)
            if info.filename in SCRIPT_PAKS:
                archive = pak.read(data)
                # Entry 0 is AutoUpdateInit.lua, the first script the engine runs.
                init = archive.entries[0]
                init.data = patch_auto_update(init.data, gateway)
                data = pak.write(archive)
            dst.writestr(info, data, compress_type=info.compress_type)
    return out.getvalue()


def rebuild_apk(original: Path, target: Path, gateway: bytes) -> None:
    src = zipfile.ZipFile(original)
    with zipfile.ZipFile(target, "w") as dst:
        for info in src.infolist():
            if info.filename.startswith("META-INF/"):
                continue  # old signature, replaced when signing
            data = src.read(info)
            if info.filename == "assets/tap4fun.zip":
                data = patch_game_data(data, gateway)
            dst.writestr(info, data, compress_type=info.compress_type)


def find_java(explicit: str | None) -> str:
    candidates = [explicit, os.environ.get("JAVA_HOME") and str(Path(os.environ["JAVA_HOME"]) / "bin" / "java")]
    candidates += [shutil.which("java")]
    candidates += [str(p) for p in TOOLS_DIR.glob("jdk*/bin/java*")]
    for candidate in candidates:
        if candidate and shutil.which(candidate):
            return candidate
    sys.exit("Java not found. Install Java 17+ or pass --java <path to java>.")


def get_signer() -> Path:
    jar = TOOLS_DIR / Path(SIGNER_URL).name
    if not jar.exists():
        print(f"Downloading {jar.name} ...")
        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(SIGNER_URL, jar)
    if sha256(jar) != SIGNER_SHA256:
        jar.unlink()
        sys.exit(f"{jar.name}: checksum mismatch, download removed. Please try again.")
    return jar


def sign(java: str, unsigned: Path, target: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [java, "-jar", str(get_signer()), "--apks", str(unsigned), "--out", tmp, "--allowResign"],
            check=True,
        )
        signed = list(Path(tmp).glob("*.apk"))
        if len(signed) != 1:
            sys.exit(f"Signing failed: expected one signed APK, found {len(signed)}")
        shutil.move(signed[0], target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("apk", type=Path, help="your original Galaxy Empire APK")
    parser.add_argument("--server", required=True, help="host[:port] of the Supernova gateway")
    parser.add_argument("--out", type=Path, default=Path("build/galaxy-empire-supernova.apk"))
    parser.add_argument("--java", help="path to the java executable")
    parser.add_argument("--force", action="store_true", help="patch even if the APK version is unknown")
    args = parser.parse_args()

    digest = sha256(args.apk)
    version = SUPPORTED.get(digest)
    if version:
        print(f"Found Galaxy Empire {version}")
    elif args.force:
        print(f"Warning: unknown APK (SHA-256 {digest}), patching anyway")
    else:
        sys.exit(f"Unknown APK (SHA-256 {digest}). See apk/README.md for supported versions.")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    unsigned = args.out.with_name(args.out.stem + "-unsigned.apk")
    print(f"Patching gateway -> http://{args.server}/")
    rebuild_apk(args.apk, unsigned, args.server.encode())

    sign(find_java(args.java), unsigned, args.out)
    unsigned.unlink()
    print(f"Done: {args.out}")


if __name__ == "__main__":
    main()
