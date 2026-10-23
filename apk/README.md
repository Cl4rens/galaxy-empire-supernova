# Put your original APK here

Copy your own copy of the original Galaxy Empire APK into this folder.
Everything in here except this file is ignored by git, so it is never committed.

Supported versions:

| Version | Package          | SHA-256                                                            |
|---------|------------------|--------------------------------------------------------------------|
| 1.9.36  | `com.tap4fun.ge` | `cfea85feeb17bc5c1573dc9856e75b783a31b5e0d85f469a475917b58abaf4c6` |

Check your file's hash:

```sh
# Linux / macOS / Git Bash
sha256sum galaxy-empire-1-9-36.apk

# Windows PowerShell
Get-FileHash galaxy-empire-1-9-36.apk -Algorithm SHA256
```

If your hash differs, you have a different build. Please open an issue with the
version number and hash so it can be added.
