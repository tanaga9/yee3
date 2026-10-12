import plistlib
import tempfile
import unittest
from pathlib import Path

from scripts.set_version import set_version


class VersionToolsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "pyproject.toml"
        self.original = (
            '# Release 0.3.0\n[project]\nversion = "0.3.0" # keep this comment\n'
            'description = "example 0.3.0"\n[tool.example]\nversion = "0.3.0"\n'
        )
        self.project.write_text(self.original, encoding="utf-8")
        self.plist = self.root / "Info.plist"
        self.info = {
            "CFBundleShortVersionString": "0.3.0", "CFBundleVersion": "1",
            "CFBundleIdentifier": "com.example.yee3",
            "CFBundleDocumentTypes": [{"CFBundleTypeName": "PNG 0.3.0"}],
        }
        self.plist.write_bytes(plistlib.dumps(self.info, sort_keys=False))

    def test_only_version_keys_change(self):
        self.assertEqual(set_version("0.4.0", self.root), ("0.3.0", "0.4.0"))
        self.assertEqual(self.project.read_text(encoding="utf-8"),
                         self.original.replace('version = "0.3.0" #', 'version = "0.4.0" #'))
        self.assertEqual(plistlib.loads(self.plist.read_bytes()), {
            **self.info, "CFBundleVersion": "0.4.0", "CFBundleShortVersionString": "0.4.0",
        })

    def test_invalid_version_does_not_write_either_file(self):
        original_plist = self.plist.read_bytes()
        for value in ("v0.4.0", "0.4", "0.4.0rc1", "01.4.0", "0.4.0\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                set_version(value, self.root)
        self.assertEqual(self.project.read_text(encoding="utf-8"), self.original)
        self.assertEqual(self.plist.read_bytes(), original_plist)

    def test_malformed_plist_does_not_partially_update_project(self):
        self.plist.write_bytes(b"invalid plist")
        with self.assertRaises(plistlib.InvalidFileException):
            set_version("0.4.0", self.root)
        self.assertEqual(self.project.read_text(encoding="utf-8"), self.original)


if __name__ == "__main__":
    unittest.main()
