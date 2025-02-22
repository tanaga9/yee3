import plistlib
import toml
from pathlib import Path

def update_version():    
    plist_path = "Info.plist"
    toml_path = "pyproject.toml"

    with open(toml_path, "r", encoding="utf-8") as f:
        toml_data = toml.load(f)
    
    project_version = toml_data.get("project", {}).get("version")
    if not project_version:
        raise ValueError("Not found 'project.version' in pyproject.toml")

    with open(plist_path, "rb") as f:
        plist_data = plistlib.load(f)

    plist_data["CFBundleShortVersionString"] = project_version

    with open(plist_path, "wb") as f:
        plistlib.dump(plist_data, f)

    print(f"Info plist version updated to {project_version}")

update_version()
