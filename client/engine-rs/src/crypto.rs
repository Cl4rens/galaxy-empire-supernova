//! The game's encryption: AES-256-CBC with a zero IV and PKCS#7, base64 (see docs/protocol.md).

use aes::cipher::{BlockDecryptMut, BlockEncryptMut, KeyIvInit, block_padding::Pkcs7};
use anyhow::{Result, anyhow, bail};
use base64::Engine;
use base64::engine::general_purpose::STANDARD;

type Enc = cbc::Encryptor<aes::Aes256>;
type Dec = cbc::Decryptor<aes::Aes256>;

/// Where the client key is in the supported APK, and its SHA-256 (as in server/supernova/crypto.py).
const KEY_MEMBER: &str = "lib/armeabi-v7a/libgalaxyempire.so";
const KEY_OFFSET: usize = 0x1B_1EA5;
const KEY_SHA256: &str = "9a0e7ff690dbe1cf60884aaad9cdc3c10d9637eb1b9d3e0dd9c092a431fe7b08";

pub fn md5_hex(data: &[u8]) -> String {
    use md5::{Digest, Md5};
    Md5::digest(data).iter().map(|b| format!("{b:02x}")).collect()
}

/// The response key from the APK's native library.
pub fn client_key(apk: &std::path::Path) -> Result<[u8; 32]> {
    use std::io::Read;
    let mut zip = zip::ZipArchive::new(std::fs::File::open(apk)?)?;
    let mut lib = Vec::new();
    zip.by_name(KEY_MEMBER)?.read_to_end(&mut lib)?;
    let key: [u8; 32] = lib.get(KEY_OFFSET..KEY_OFFSET + 32).ok_or_else(|| anyhow!("library too short"))?.try_into()?;
    use sha2::{Digest, Sha256};
    let digest: String = Sha256::digest(key).iter().map(|b| format!("{b:02x}")).collect();
    if digest != KEY_SHA256 {
        bail!("unsupported APK: unknown client key");
    }
    Ok(key)
}

pub fn encrypt(key: &[u8], data: &[u8]) -> String {
    let key: [u8; 32] = key[..32].try_into().unwrap();
    STANDARD.encode(Enc::new(&key.into(), &[0u8; 16].into()).encrypt_padded_vec_mut::<Pkcs7>(data))
}

pub fn decrypt(key: &[u8], text: &[u8]) -> Result<Vec<u8>> {
    let key: [u8; 32] = key[..32].try_into()?;
    let data = STANDARD.decode(text.trim_ascii())?;
    Dec::new(&key.into(), &[0u8; 16].into())
        .decrypt_padded_vec_mut::<Pkcs7>(&data)
        .map_err(|_| anyhow!("bad padding"))
}
