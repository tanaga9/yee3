import errno
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import release_workspace


class ReleasePublicationTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        root = Path(workspace.name)
        root_override = patch.object(release_workspace, "ROOT", root)
        root_override.start()
        self.addCleanup(root_override.stop)

        self.source = root / "candidate"
        self.target = root / "app"
        self.artifact = root / "Yee 3.dmg"
        self.destination = root / "dist" / self.artifact.name
        for directory in (self.source, self.target, self.destination.parent):
            directory.mkdir()
        (self.source / "version").write_text("new")
        (self.target / "version").write_text("old")
        self.artifact.write_text("new")
        self.destination.write_text("old")

    def assert_published_version(self, expected):
        self.assertEqual((self.target / "version").read_text(), expected)
        self.assertEqual(self.destination.read_text(), expected)

    def test_artifact_replacement_failure_restores_previous_build(self):
        with patch.object(Path, "replace", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                release_workspace.publish(self.source, self.target, self.artifact)
        self.assert_published_version("old")
        self.assertTrue(self.source.is_dir())

    def test_cleanup_failure_keeps_new_build_and_unblocks_next_publish(self):
        backup = self.target.parent / ".previous-build"
        rmtree = release_workspace.shutil.rmtree

        def remove_directory(path, *args, **kwargs):
            if Path(path) == backup:
                raise OSError(errno.ENOTEMPTY, "Directory not empty")
            return rmtree(path, *args, **kwargs)

        with (
            patch.object(release_workspace.shutil, "rmtree", side_effect=remove_directory),
            patch("sys.stderr") as stderr,
        ):
            release_workspace.publish(self.source, self.target, self.artifact)

        self.assert_published_version("new")
        self.assertFalse(backup.exists())
        leftovers = list(self.target.parent.glob(".previous-build-*"))
        self.assertEqual(len(leftovers), 1)
        self.assertEqual((leftovers[0] / "version").read_text(), "old")
        stderr.write.assert_called()

        self.source.mkdir()
        (self.source / "version").write_text("next")
        release_workspace.publish(self.source, self.target)
        self.assertEqual((self.target / "version").read_text(), "next")

    def test_logging_failure_after_replacement_preserves_new_release(self):
        with patch("builtins.print", side_effect=BrokenPipeError):
            with self.assertRaises(BrokenPipeError):
                release_workspace.publish(self.source, self.target, self.artifact)
        self.assert_published_version("new")
