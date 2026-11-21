import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QMovie, QPainter, QPdfWriter

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

    def test_gif_and_webp_play_then_stop_on_navigation(self):
        for extension in ("gif", "webp"):
            with self.subTest(format=extension):
                path = Path(self.temp.name) / f"animated.{extension}"
                frames = [Image.new("RGB", (8, 12), color) for color in ("red", "blue")]
                frames[0].save(path, save_all=True, append_images=frames[1:],
                               duration=60, loop=0, lossless=True)
                record = ImageData(path.name, str(path), 0, path.name)
                self.assertIsNotNone(self.viewer.loadImageFromFile(record))
                movie = self.viewer.imageDisplay.movie
                self.assertIsNotNone(movie)
                self.assertTrue(movie.isValid())
                seen = set()
                movie.frameChanged.connect(seen.add)
                deadline = time.monotonic() + 2
                while len(seen) < 2 and time.monotonic() < deadline:
                    self.qt_app.processEvents()
                    time.sleep(0.01)
                self.assertEqual(seen, {0, 1})
                still = self.make_image(f"still-{extension}.png", "PNG")
                self.assertIsNotNone(self.viewer.loadImageFromFile(still))
                self.assertIsNone(self.viewer.imageDisplay.movie)
                self.assertEqual(movie.state(), QMovie.MovieState.NotRunning)

    def test_release_image_codecs_decode(self):
        for extension, fmt in (
            ("png", "PNG"), ("jpg", "JPEG"), ("bmp", "BMP"), ("tiff", "TIFF"),
            ("tga", "TGA"), ("ico", "ICO"), ("webp", "WEBP"), ("avif", "AVIF"),
        ):
            with self.subTest(format=fmt):
                path = Path(self.temp.name) / f"image.{extension}"
                Image.new("RGB", (32, 32), "red").save(path, format=fmt, sizes=[(32, 32)])
                record = ImageData(path.name, str(path), 0, path.name)
                self.assertIsNotNone(self.viewer.loadImageFromFile(record))
                self.assertEqual(self.viewer.currentPixmap.size().width(), 32)
                self.assertEqual(self.viewer.currentPixmap.size().height(), 32)

    def test_release_pdf_and_svg_plugins_decode(self):
        svg = Path(self.temp.name) / "image.svg"
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="8" height="12">'
                       '<rect width="8" height="12" fill="red"/></svg>', encoding="utf-8")
        pdf = Path(self.temp.name) / "image.pdf"
        writer = QPdfWriter(str(pdf))
        writer.setResolution(72)
        painter = QPainter(writer)
        self.assertTrue(painter.isActive())
        painter.drawText(20, 20, "Yee 3 PDF release check")
        painter.end()
        del writer
        for path in (svg, pdf):
            with self.subTest(format=path.suffix):
                record = ImageData(path.name, str(path), 0, path.name)
                self.assertIsNotNone(self.viewer.loadImageFromFile(record))
                self.assertFalse(self.viewer.currentPixmap.isNull())


if __name__ == "__main__":
    unittest.main()
