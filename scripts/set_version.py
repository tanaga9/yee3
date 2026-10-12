"""Update release versions in TOML and plist; Git operations remain manual."""

import argparse
import plistlib
import re
from pathlib import Path

import tomlkit


def set_version(value: str, root: Path) -> tuple[str, str]:
    if not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value):
        raise ValueError("Version must be three numbers, such as 0.4.0 (without a v prefix).")
    project_path = root / "pyproject.toml"
    plist_path = root / "Info.plist"
    project = tomlkit.parse(project_path.read_text(encoding="utf-8"))
    info = plistlib.loads(plist_path.read_bytes())
    old = str(project["project"]["version"])
    project["project"]["version"] = value
    info["CFBundleShortVersionString"] = value
    info["CFBundleVersion"] = value
    # Parse and serialize both files before writing either one.
    project_text = tomlkit.dumps(project)
    plist_bytes = plistlib.dumps(info, sort_keys=False)
    project_path.write_text(project_text, encoding="utf-8")
    plist_path.write_bytes(plist_bytes)
    return old, value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="Release version, such as 0.4.0")
    args = parser.parse_args()
    try:
        old, new = set_version(args.version, Path(__file__).resolve().parents[1])
    except (ValueError, KeyError, OSError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(f"Release version: {old} -> {new} (pyproject.toml and Info.plist)")


if __name__ == "__main__":
    main()
