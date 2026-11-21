#!/bin/bash
set -euo pipefail

cd -- "$(dirname -- "$0")"

if [[ "$(uname -m)" != "arm64" ]]; then
    printf 'macOS releases must be built on Apple Silicon (arm64).\n' >&2
    exit 1
fi

case "${1:-}" in
    ""|--package) ;;
    *) printf 'Usage: %s [--package]\n' "$0" >&2; exit 1 ;;
esac

project_dir="$PWD"
build_dir="$PWD/build/yee3/macos/app"
app_bundle="$build_dir/Yee 3.app"

pdm lock --check
plutil -lint Info.plist
mkdir -p "$(dirname -- "$build_dir")"
staging="$(mktemp -d "$(dirname -- "$build_dir")/.release.XXXXXXXX")"
trap 'rm -rf -- "$staging"' EXIT

python="$(pdm run python -c 'import sys; print(sys.executable)')"
release_workspace="$project_dir/scripts/release_workspace.py"
"$python" "$release_workspace" prepare "$staging"

(
    cd "$staging"
    PIP_CONSTRAINT="$staging/release-constraints.txt" "$python" -m briefcase build macOS --no-input
    staged_build="$staging/build/yee3/macos/app"
    staged_app="$staged_build/Yee 3.app"
    "$python" "$project_dir/scripts/compact_release.py" macOS --build-dir "$staged_build"

    "$python" -m briefcase build macOS --no-update --no-input
    cp Info.plist "$staged_app/Contents/Info.plist"
    codesign --force --sign - --entitlements "$staged_build/Entitlements.plist" "$staged_app"
    codesign --verify --deep --strict "$staged_app"
    "$python" "$project_dir/scripts/check_macos_startup.py" "$staged_app"
    if [[ "${1:-}" == "--package" ]]; then
        "$python" "$release_workspace" package "$staging" macOS
    fi
)

publish_args=("$staging/build/yee3/macos/app" "$build_dir")
if [[ "${1:-}" == "--package" ]]; then
    publish_args+=("$staging")
fi
"$python" "$release_workspace" publish "${publish_args[@]}"

printf '\nBuilt: %s\n' "$app_bundle"
