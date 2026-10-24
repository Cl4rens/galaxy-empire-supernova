"""Test environment. Set before any supernova module reads its configuration."""

import os
import tempfile

os.environ["SUPERNOVA_REQUIRE_KEY"] = "0"
os.environ["SUPERNOVA_DATA"] = tempfile.mkdtemp()
os.environ["SUPERNOVA_APK_DIR"] = tempfile.mkdtemp()  # never use a local APK
os.environ.pop("SUPERNOVA_APK", None)
# Most tests fight between fresh players; test_protection.py switches the protection on.
os.environ["SUPERNOVA_NEWBIE_POINTS"] = "0"
os.environ["SUPERNOVA_STRONG_POINTS"] = "0"
