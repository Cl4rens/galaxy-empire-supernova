"""Response encryption used by the client for game requests. See docs/protocol.md.

The AES key is a constant inside the client's native library. We do not ship it:
it is read from the player's own APK and verified against a known hash.
"""

import base64
import hashlib
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# (file inside the APK, offset, length, SHA-256 of the key) per supported client build
KEY_LOCATIONS = [
    ("lib/armeabi-v7a/libgalaxyempire.so", 0x1B1EA5, 32,
     "9a0e7ff690dbe1cf60884aaad9cdc3c10d9637eb1b9d3e0dd9c092a431fe7b08"),
]

IV = bytes(16)


def load_key(apk: Path) -> bytes:
    with zipfile.ZipFile(apk) as z:
        names = set(z.namelist())
        for member, offset, length, digest in KEY_LOCATIONS:
            if member not in names:
                continue
            key = z.read(member)[offset : offset + length]
            if hashlib.sha256(key).hexdigest() == digest:
                return key
    raise ValueError(f"{apk}: no known client key found (unsupported APK version?)")


def encrypt(key: bytes, text: str) -> bytes:
    """Base64(AES-256-CBC(key, zero IV, PKCS#7(text)))."""
    padder = padding.PKCS7(128).padder()
    data = padder.update(text.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(IV)).encryptor()
    return base64.b64encode(encryptor.update(data) + encryptor.finalize())


def decrypt(key: bytes, payload: bytes) -> str:
    decryptor = Cipher(algorithms.AES(key), modes.CBC(IV)).decryptor()
    data = decryptor.update(base64.b64decode(payload)) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    return (unpadder.update(data) + unpadder.finalize()).decode("utf-8")
