import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, PngImagePlugin
from PySide6.QtWidgets import QApplication

from yee3.app import ImageData, ImageViewer, MetadataPanel, OSType
from yee3.image_metadata import ImageMetadataResult, parse_image_metadata_bytes


class MetadataViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_plain_png_metadata_is_displayed(self):
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text("Title", "猫の写真")
        metadata.add_text("Author", "Alice")
        stream = io.BytesIO()
        Image.new("RGB", (8, 12)).save(stream, format="PNG", pnginfo=metadata)
        result = parse_image_metadata_bytes("image.png", stream.getvalue())
        panel = MetadataPanel()
        self.addCleanup(panel.close)
        panel.setResult(result)
        panel.show()
        self.qt_app.processEvents()
        self.assertTrue(panel.rawSection.isVisible())
        self.assertIn("猫の写真", panel.rawSection.textEdit.toPlainText())
        self.assertIn("Alice", panel.rawSection.textEdit.toPlainText())
        self.assertEqual(panel.metaLabel.text(), "Format: png")

    def test_parameters_keep_compact_display_and_empty_metadata_clears_it(self):
        panel = MetadataPanel()
        self.addCleanup(panel.close)
        panel.setResult(ImageMetadataResult(
            path="image.png", format="png",
            fields={"parameters_raw": "cat\nSteps: 20"},
            raw={"parameters": "cat\nSteps: 20"},
        ))
        self.assertFalse(panel.parametersSection.isHidden())
        self.assertTrue(panel.rawSection.isHidden())
        self.assertEqual(panel.metaLabel.text(), "Format: png")
        panel.setResult(ImageMetadataResult(path="empty.png", format="png"))
        self.assertTrue(panel.parametersSection.isHidden())
        self.assertTrue(panel.rawSection.isHidden())
        self.assertEqual(panel.metaLabel.text(), "No metadata found.")

    def make_viewer(self):
        with patch.object(ImageViewer, "loadSettings"):
            viewer = ImageViewer(OSType.LINUX)
        viewer.saveSettings = lambda: None
        self.addCleanup(viewer.close)
        viewer.show()
        viewer.showMetadataDock()
        self.qt_app.processEvents()
        return viewer

    def test_unsupported_formats_do_not_read_entire_file_for_metadata(self):
        viewer = self.make_viewer()
        with tempfile.TemporaryDirectory() as folder:
            for name, fmt in (("image.gif", "GIF"), ("image.tga", "TGA"), ("image.tiff", "TIFF")):
                with self.subTest(format=fmt):
                    path = Path(folder) / name
                    Image.new("RGB", (8, 12), "red").save(path, format=fmt)
                    record = ImageData(name, str(path), 0, name)
                    with patch("yee3.app.open", create=True, side_effect=AssertionError("unexpected full file read")) as read:
                        self.assertIsNotNone(viewer.loadImageFromFile(record))
                        read.assert_not_called()
                    self.assertEqual(viewer.currentPixmap.width(), 8)
                    self.assertEqual(viewer.currentPath, str(path))
                    self.assertIn("unavailable", viewer.metadataPanel.metaLabel.text())

    def test_supported_formats_still_load_metadata_with_uppercase_extensions(self):
        viewer = self.make_viewer()
        with tempfile.TemporaryDirectory() as folder:
            for extension, fmt in (("PNG", "PNG"), ("JPG", "JPEG"), ("JPEG", "JPEG"), ("WEBP", "WEBP")):
                with self.subTest(format=fmt, extension=extension):
                    path = Path(folder) / f"image.{extension}"
                    if fmt == "PNG":
                        metadata = PngImagePlugin.PngInfo()
                        metadata.add_text("parameters", "猫の写真\nSteps: 20")
                        options = {"pnginfo": metadata}
                    else:
                        exif = Image.Exif()
                        exif[0x010E] = "猫の写真\nSteps: 20".encode("utf-8") + b"\x00"
                        options = {"exif": exif}
                    Image.new("RGB", (8, 12), "red").save(path, format=fmt, **options)
                    record = ImageData(path.name, str(path), 0, path.name)
                    with patch("yee3.app.open", create=True, wraps=open) as read:
                        self.assertIsNotNone(viewer.loadImageFromFile(record))
                        read.assert_called_once_with(str(path), "rb")
                    self.assertIn("猫の写真", viewer.metadataPanel.parametersSection.textEdit.toPlainText())


if __name__ == "__main__":
    unittest.main()
