"""Start the signed application and wait for its first event loop iteration."""

import os
from pathlib import Path
import plistlib
import subprocess
import sys


def check_startup(bundle: Path) -> None:
    contents = bundle / "Contents"
    with (contents / "Info.plist").open("rb") as stream:
        executable = plistlib.load(stream)["CFBundleExecutable"]
    env = os.environ.copy()
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env["QT_QPA_PLATFORM"] = "cocoa"
    env["YEE3_CHECK_STARTUP"] = "1"
    subprocess.run([contents / "MacOS" / executable], env=env, check=True, timeout=30)


if __name__ == "__main__":
    check_startup(Path(sys.argv[1]).resolve())
