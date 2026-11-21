"""Prepare release inputs and install a verified Briefcase build."""

from contextlib import contextmanager, nullcontext
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def prepare(workspace: Path) -> None:
    for name in ("pyproject.toml", "LICENSE", "Info.plist"):
        shutil.copy2(ROOT / name, workspace / name)
    lock = tomllib.loads((ROOT / "pdm.lock").read_text(encoding="utf-8"))
    constraints = []
    for package in lock["package"]:
        if "default" in package["groups"]:
            marker = f"; {package['marker']}" if package.get("marker") else ""
            constraints.append(f"{package['name']}=={package['version']}{marker}")
    (workspace / "release-constraints.txt").write_text(
        "\n".join(constraints) + "\n", encoding="utf-8"
    )
    paths = subprocess.check_output(["git", "ls-files", "-z", "--", "src"], cwd=ROOT)
    for item in paths.split(b"\0"):
        if not item:
            continue
        path = Path(os.fsdecode(item))
        target = workspace / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / path, target, follow_symlinks=False)


@contextmanager
def publish_lock(parent: Path):
    lock = parent / ".release-publish.lock"
    try:
        lock.mkdir()
    except FileExistsError:
        raise RuntimeError(f"Another release is being installed (lock: {lock})") from None
    try:
        yield
    finally:
        lock.rmdir()


def publish(source: Path, target: Path, artifact: Path | None = None) -> None:
    """Install the app and its Briefcase metadata as a single directory."""
    prepared = prepare_artifact(artifact) if artifact is not None else nullcontext()
    with prepared as candidate, publish_lock(target.parent):
        backup = target.parent / ".previous-build"
        if backup.exists():
            raise RuntimeError(f"A previous build needs recovery: {backup}")
        if target.exists():
            target.rename(backup)
        try:
            source.rename(target)
            if candidate is not None:
                temporary, destination = candidate
                temporary.replace(destination)
        except BaseException:
            if target.exists():
                target.rename(source)
            if backup.exists():
                backup.rename(target)
            raise
        # Publication is committed. Cleanup failures must not roll it back.
        if backup.exists():
            shutil.rmtree(backup)

    if candidate is not None:
        print(f"Packaged: {destination}")


def release_artifact(workspace: Path, platform: str) -> Path | None:
    extension = ".dmg" if platform == "macOS" else ".msi"
    artifacts = list((workspace / "dist").glob(f"*{extension}"))
    if len(artifacts) > 1:
        raise RuntimeError(f"Expected at most one {extension} package, found {len(artifacts)}")
    return artifacts[0] if artifacts else None


def package(workspace: Path, platform: str) -> Path:
    command = [sys.executable, "-m", "briefcase", "package", platform, "--no-input"]
    if platform == "macOS":
        command.append("--adhoc-sign")
    subprocess.run(command, cwd=workspace, check=True)
    artifact = release_artifact(workspace, platform)
    if artifact is None:
        raise RuntimeError(f"No {platform} package was produced")
    return artifact


@contextmanager
def prepare_artifact(artifact: Path):
    destination = ROOT / "dist"
    destination.mkdir(exist_ok=True)
    with TemporaryDirectory(prefix=".release-", dir=destination) as temporary:
        candidate = Path(temporary) / artifact.name
        shutil.copy2(artifact, candidate)
        yield candidate, destination / artifact.name


def build_windows(with_package: bool = False) -> None:
    build = ROOT / "build" / "yee3" / "windows"
    build.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".release-", dir=build) as temporary:
        workspace = Path(temporary)
        prepare(workspace)
        subprocess.run([sys.executable, "-m", "briefcase", "build", "windows", "--no-input"],
                       cwd=workspace, check=True,
                       env={**os.environ, "PIP_CONSTRAINT": str(workspace / "release-constraints.txt")})
        output = workspace / "build" / "yee3" / "windows" / "app"
        subprocess.run([sys.executable, ROOT / "scripts" / "compact_release.py",
                        "windows", "--build-dir", output], check=True)
        artifact = package(workspace, "windows") if with_package else None
        publish(output, build / "app", artifact)


if __name__ == "__main__":
    action, *args = sys.argv[1:]
    if action == "prepare":
        prepare(Path(args[0]))
    elif action == "publish":
        source, target = map(Path, args[:2])
        artifact = release_artifact(Path(args[2]), "macOS") if len(args) == 3 else None
        if len(args) == 3 and artifact is None:
            raise SystemExit("No macOS package was produced")
        publish(source, target, artifact)
    elif action == "windows":
        if args not in ([], ["--package"]):
            raise SystemExit("Usage: release_workspace.py windows [--package]")
        build_windows(bool(args))
    elif action == "package":
        package(Path(args[0]), args[1])
    else:
        raise SystemExit(f"Unknown release action: {action}")
