#!/bin/bash
set -euo pipefail

cd -- "$(dirname -- "$0")"

build_dir="$PWD/build/yee3/macos/app"
app_bundle="$build_dir/Yee 3.app"

plutil -lint Info.plist
pdm run python -m briefcase build macOS \
    --update --update-requirements --update-resources --no-input

cp Info.plist "$app_bundle/Contents/Info.plist"
codesign --force --sign - --entitlements "$build_dir/Entitlements.plist" "$app_bundle"
codesign --verify --deep --strict "$app_bundle"

printf '\nBuilt: %s\n' "$app_bundle"
