import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication

from yee3.app import ImageData, ImageViewer, OSType


class ImageLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        with patch.object(ImageViewer, "loadSettings"):
            self.viewer = ImageViewer(OSType.LINUX)
        self.viewer.saveSettings = lambda: None
        self.viewer.resize(800, 600)
        self.viewer.show()
        self.qt_app.processEvents()
        self.addCleanup(self.viewer.close)

    def make_image(self, name, fmt, mtime=0):
        path = Path(self.temp.name) / name
        Image.new("RGB", (8, 12), "red").save(path, format=fmt)
        record = ImageData(name, str(path), mtime, name)
        for order in (self.viewer.mtimeOrderSet, self.viewer.fnameOrderSet, self.viewer.randomOrderSet):
            order.add(record)
        return record

    def test_info_preserves_tga_navigation(self):
        first = self.make_image("first.png", "PNG", mtime=1)
        tga = self.make_image("second.tga", "TGA", mtime=2)
        self.assertIsNotNone(self.viewer.loadImageFromFile(first))
        self.viewer.showMetadataDock()
        self.qt_app.processEvents()
        self.assertTrue(self.viewer.verticalNextImage())
        self.assertEqual(self.viewer.currentPath, tga.path_nf)
        self.assertEqual(self.viewer.currentPixmap.width(), 8)
        self.assertEqual(self.viewer.currentPixmap.height(), 12)
        for order in (self.viewer.mtimeOrderSet, self.viewer.fnameOrderSet, self.viewer.randomOrderSet):
            self.assertIsNotNone(order.get(tga.path_nf))
        self.assertTrue(self.viewer.verticalPreviousImage())
        self.assertEqual(self.viewer.currentPath, first.path_nf)

    def test_info_handles_uppercase_and_mismatched_extensions(self):
        for name, fmt in (("image.TGA", "TGA"), ("png_data.tga", "PNG")):
            with self.subTest(name=name):
                self.viewer.metadataDock.hide()
                record = self.make_image(name, fmt)
                self.assertIsNotNone(self.viewer.loadImageFromFile(record))
                self.viewer.showMetadataDock()
                self.qt_app.processEvents()
                self.assertEqual(self.viewer.currentPath, record.path_nf)
                self.assertIsNotNone(self.viewer.loadImageFromFile(record))
                self.assertFalse(self.viewer.currentPixmap.isNull())


if __name__ == "__main__":
    unittest.main()
