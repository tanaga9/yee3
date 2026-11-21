"""Trim Briefcase release dependencies and verify the packaged image viewer."""

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib


ROOT = Path(__file__).resolve().parents[1]
VIEWER_MODULES = {"QtCore", "QtGui", "QtWidgets"}
TOOLS = (
    "Assistant.app", "Designer.app", "Linguist.app", "assistant", "designer",
    "linguist", "balsam", "balsamui", "lrelease", "lupdate", "qmlformat",
    "qmllint", "qmlls", "qsb", "svgtoqml", "QtWebEngineProcess",
)
MACH_O_MAGICS = {
    bytes.fromhex(value) for value in (
        "feedface", "cefaedfe", "feedfacf", "cffaedfe",
        "cafebabe", "bebafeca", "cafebabf", "bfbafeca",
    )
}


def thin_macos_bundle(bundle: Path) -> None:
    """Remove Intel slices from every Mach-O, including extensionless frameworks."""
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise ValueError("macOS releases must be built on Apple Silicon (arm64).")
    binaries = []
    for path in bundle.rglob("*"):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                if stream.read(4) in MACH_O_MAGICS:
                    binaries.append(path)
    if not binaries:
        raise ValueError(f"No Mach-O binaries found in {bundle}.")
    # Inspect the whole bundle before changing files; reject Intel-only code.
    to_thin = []
    for path in binaries:
        arches = subprocess.check_output(["lipo", "-archs", str(path)], text=True).split()
        if "arm64" not in arches:
            raise ValueError(f"{path} has no arm64 slice: {arches}")
        if arches != ["arm64"]:
            to_thin.append(path)
    for path in to_thin:
        with tempfile.NamedTemporaryFile(prefix=".yee3-arm64-", dir=path.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name)
        try:
            subprocess.run(["lipo", str(path), "-thin", "arm64", "-output", str(temporary)],
                           check=True, capture_output=True, text=True)
            shutil.copystat(path, temporary)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    for path in binaries:
        arches = subprocess.check_output(["lipo", "-archs", str(path)], text=True).split()
        if arches != ["arm64"]:
            raise RuntimeError(f"Release binary is not arm64-only: {path}: {arches}")
    print(f"Verified {len(binaries)} arm64-only Mach-O binaries; thinned {len(to_thin)}.")


def removal_paths(packages: Path) -> list[Path]:
    pyside = packages / "PySide6"
    paths = set()
    for pattern in ("Qt*.so", "Qt*.pyd"):
        paths.update(p for p in pyside.glob(pattern)
                     if p.name.split(".")[0] not in VIEWER_MODULES)
    paths.update(pyside.glob("*.pyi"))
    for name in (*TOOLS, "include", "typesystems", "glue", "doc", "scripts", "QtAsyncio"):
        paths.update(p for p in (pyside / name, pyside / f"{name}.exe") if p.exists())
    # Keep all image/platform plugins and their native libraries, including
    # QtPdf (in Addons). Only the unused browser engine is removed wholesale.
    for pattern in (
        "Qt/lib/QtWebEngine*.framework", "Qt6WebEngine*.dll",
        "Qt/qml/QtWebEngine", "qml/QtWebEngine",
        "Qt/libexec/QtWebEngineProcess*",
        "Qt/translations/qtwebengine*", "translations/qtwebengine*",
        "resources/qtwebengine*", "resources/icudtl.dat",
        "resources/v8_context_snapshot*",
    ):
        paths.update(pyside.glob(pattern))
    paths.update(packages.glob("PyObjCTest"))
    paths.update(packages.rglob("*.dSYM"))
    # Avoid removing a child twice when its parent is already selected.
    return sorted(p for p in paths if not any(parent in paths for parent in p.parents))


def file_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*")
               if p.is_file() and not p.is_symlink())


def run_packaged(packages: Path, app: Path, code: str) -> str:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(map(str, (packages, app, ROOT / "tests")))
    env["QT_QPA_PLATFORM"] = "offscreen"
    # -S prevents installed development packages from hiding missing release files.
    result = subprocess.run([sys.executable, "-S", "-c", code], env=env,
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"Packaged runtime check failed:\n{result.stdout}{result.stderr}")
    return result.stdout


def formats(packages: Path, app: Path) -> dict:
    return json.loads(run_packaged(packages, app, """
import json, sys
import PySide6
from PySide6.QtGui import QImageReader, QMovie
from PySide6.QtWidgets import QApplication
from yee3.app import supportedImageFormats
if sys.platform == 'darwin':
    import yee3.main_macos
else:
    import yee3.main_windows
application = QApplication([])
print(json.dumps({
    'version': PySide6.__version__,
    'images': sorted(bytes(f).decode() for f in QImageReader.supportedImageFormats()),
    'animations': sorted(bytes(f).decode() for f in QMovie.supportedFormats()),
    'viewer': sorted(supportedImageFormats()),
}))
"""))


def compact(platform: str, dry_run: bool = False, build: Path | None = None) -> None:
    build = (build or ROOT / "build" / "yee3" / platform.lower() / "app").resolve()
    config = tomllib.loads((build / "briefcase.toml").read_text(encoding="utf-8"))
    packages = (build / config["paths"]["app_packages_path"]).resolve()
    app = (build / config["paths"]["app_path"]).resolve()
    if not packages.is_relative_to(build.resolve()) or not app.is_relative_to(build.resolve()):
        raise ValueError("Release paths must be inside the Briefcase build directory.")
    before = formats(packages, app)
    if before["version"] != "6.9.1":
        raise ValueError("Review the release exclusions when changing PySide6 from 6.9.1.")
    paths = removal_paths(packages)
    if dry_run:
        for path in paths:
            print(path.relative_to(build))
        return
    original_size = file_size(packages)
    for path in paths:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    if platform == "macOS":
        thin_macos_bundle(build / "Yee 3.app")
    after = formats(packages, app)
    if before != after:
        raise RuntimeError(f"Release formats changed: {before!r} -> {after!r}")
    print(run_packaged(packages, app, """
import sys, unittest
suite = unittest.defaultTestLoader.loadTestsFromNames([
    'test_image_loading', 'test_image_metadata_encoding', 'test_metadata_viewer',
])
result = unittest.TextTestRunner(verbosity=1, stream=sys.stdout).run(suite)
sys.exit(not result.wasSuccessful())
"""), end="")
    final_size = file_size(packages)
    print(f"Release dependencies: {original_size / 2**20:.1f} -> {final_size / 2**20:.1f} MiB")
    print(f"Preserved image formats: {', '.join(after['images'])}")
    print(f"Preserved animations: {', '.join(after['animations'])}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("platform", choices=("macOS", "windows"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--build-dir", type=Path)
    args = parser.parse_args()
    compact(args.platform, args.dry_run, args.build_dir)


if __name__ == "__main__":
    main()
