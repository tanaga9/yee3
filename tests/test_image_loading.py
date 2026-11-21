import json
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

from yee3.app import FastOrderedSet, ImageData, ImageViewer, OSType


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

    def wait_for_scan(self):
        loader = self.viewer.imageLoader
        self.assertTrue(loader.wait(5000))
        self.qt_app.processEvents()
        self.assertFalse(self.viewer.lazyLoadingInProgress)

    def test_remove_uses_stored_record_and_ties_are_deterministic(self):
        first = ImageData("a", "/a", 1, "a")
        second = ImageData("b", "/b", 1, "b")
        for records in ([first, second], [second, first]):
            order = FastOrderedSet(records, key_func=lambda record: record.st_mtime)
            self.assertEqual(list(order), [first, second])
            order.remove(ImageData("a", "/a", 2, "a"))
            self.assertEqual(list(order), [second])
            self.assertIsNone(order.get(first.path_nf))
            self.assertIsNone(order.prev_path(second.path_nf))

    def test_reload_reconciles_all_orders_after_displayed_file_disappears(self):
        first = self.make_image("first.png", "PNG")
        second = self.make_image("second.png", "PNG")
        self.viewer.loadImagesFromFolder(first.path_nf)
        self.wait_for_scan()
        self.assertEqual(self.viewer.currentPath, first.path_nf)

        os.unlink(first.path_nf)
        os.utime(second.path_nf, (123, 123))
        added = Path(self.temp.name) / "added.png"
        Image.new("RGB", (8, 12)).save(added)
        self.viewer.reloadCurrentFolder()
        self.wait_for_scan()

        for order in (
            self.viewer.mtimeOrderSet, self.viewer.fnameOrderSet, self.viewer.randomOrderSet
        ):
            self.assertEqual({record.path_nf for record in order}, {second.path_nf, str(added)})
            self.assertEqual(order.get(second.path_nf).st_mtime, 123)
        self.assertEqual(self.viewer.currentPath, str(added))

    def test_bulk_removal_preserves_links_and_sorted_insertion(self):
        records = [ImageData(name, name, 1, name) for name in "abcdef"]
        order = FastOrderedSet(records, key_func=lambda record: record.st_mtime)
        order.discard_paths({"a", "c", "d", "f"})
        self.assertEqual([record.name for record in order], ["b", "e"])
        self.assertIsNone(order.get("c"))
        self.assertEqual(order.next_path("b"), "e")
        self.assertEqual(order.prev_path("e"), "b")
        self.assertEqual(order.next_path("e", loop=True), "b")
        order.add(records[2])
        self.assertEqual([record.name for record in order], ["b", "c", "e"])
        self.assertEqual(order.prev_path("e"), "c")
        order.discard_paths({"b", "c", "e"})
        self.assertEqual(len(order), 0)
        order.add(records[0])
        self.assertIsNone(order.next_path("a"))

    def test_reload_refreshes_current_pixels_without_a_timestamp_change(self):
        first = self.make_image("first.png", "PNG")
        self.viewer.loadImagesFromFolder(first.path_nf)
        self.wait_for_scan()
        self.assertEqual(self.viewer.currentPixmap.toImage().pixelColor(0, 0).name(), "#ff0000")

        stat = os.stat(first.path_nf)
        Image.new("RGB", (8, 12), "blue").save(first.path_nf)
        os.utime(first.path_nf, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.viewer.reloadCurrentFolder()
        self.wait_for_scan()

        self.assertEqual(self.viewer.currentPath, first.path_nf)
        self.assertEqual(self.viewer.currentPixmap.toImage().pixelColor(0, 0).name(), "#0000ff")

    def test_batch_update_preserves_unchanged_keys_and_rebuilds_changed_order(self):
        records = [ImageData(str(i), str(i), i, str(i)) for i in range(80)]
        updates = [
            ImageData(record.name, record.path_nf, -record.st_mtime, record.pseudo_random_hash)
            for record in records[1:]
        ]
        filename_order = FastOrderedSet(records, key_func=lambda record: record.name)
        mtime_order = FastOrderedSet(records, key_func=lambda record: record.st_mtime)
        with patch.object(filename_order, "remove") as remove:
            filename_order.upsert_batch(updates)
            remove.assert_not_called()
        mtime_order.upsert_batch(updates)
        self.assertEqual(list(mtime_order), [*reversed(updates), records[0]])
        self.assertEqual(mtime_order.next_path("79"), "78")
        self.assertEqual(mtime_order.prev_path("0"), "1")
        self.assertIs(filename_order.get("79"), updates[-1])

    def test_failed_scan_releases_loading_state_and_keeps_existing_records(self):
        first = self.make_image("first.png", "PNG")
        self.viewer.loadImagesFromFolder(first.path_nf)
        self.wait_for_scan()
        with patch("yee3.app.os.scandir", side_effect=RuntimeError("scan failed")):
            self.viewer.reloadCurrentFolder()
            self.wait_for_scan()
        self.assertIsNotNone(self.viewer.mtimeOrderSet.get(first.path_nf))
        self.assertEqual(self.viewer.currentPath, first.path_nf)
        self.viewer.reloadCurrentFolder()
        self.wait_for_scan()

    def test_fixed_size_batch_does_not_rebuild_a_large_order(self):
        records = [ImageData(str(i), str(i), i, str(i)) for i in range(600)]
        order = FastOrderedSet(records, key_func=lambda record: record.st_mtime)
        with patch.object(order, "clear", wraps=order.clear) as clear:
            order.upsert_batch([
                ImageData(record.name, record.path_nf, -record.st_mtime, record.pseudo_random_hash)
                for record in records[1:65]
            ])
            clear.assert_not_called()
        self.assertEqual(order.first_path(), "64")
        self.assertEqual(order.next_path("64"), "63")

    def test_readable_image_fallback_yields_between_attempts(self):
        first = self.make_image("first.png", "PNG", mtime=2)
        second = self.make_image("second.png", "PNG", mtime=1)
        self.viewer.imageLoader = object()
        load = self.viewer.loadImageFromFile
        with patch.object(
            self.viewer, "loadImageFromFile",
            side_effect=lambda record: None if record == first else load(record),
        ) as load_image:
            self.viewer.showFirstReadableImage(None)
            load_image.assert_called_once_with(first)
            self.qt_app.processEvents()
            self.assertEqual(self.viewer.currentPath, second.path_nf)

    def test_opening_corrupt_file_preserves_current_image(self):
        first = self.make_image("first.png", "PNG")
        corrupt = Path(self.temp.name) / "corrupt.png"
        corrupt.write_bytes(b"invalid image")
        self.viewer.loadImagesFromFolder(first.path_nf)
        self.wait_for_scan()
        pixmap = self.viewer.currentPixmap
        title = self.viewer.windowTitle()

        self.viewer.loadImagesFromFolder(str(corrupt))
        self.wait_for_scan()

        self.assertEqual(self.viewer.currentPath, first.path_nf)
        self.assertIs(self.viewer.currentPixmap, pixmap)
        self.assertEqual(self.viewer.windowTitle(), title)

    def test_initial_batch_yields_between_failed_candidates(self):
        corrupt = Path(self.temp.name) / "corrupt.png"
        corrupt.write_bytes(b"invalid image")
        readable = self.make_image("readable.png", "PNG")
        self.viewer.imageLoader = type("Loader", (), {"batch_count_threshold": 10})()
        batch = [
            {"name": path.name, "path_nf": str(path), "st_mtime": 1, "pseudo_random_hash": path.name}
            for path in (corrupt, Path(readable.path_nf))
        ]
        self.viewer.handleNewImage(json.dumps(batch), True)
        self.assertIsNone(self.viewer.currentPath)
        self.qt_app.processEvents()
        self.assertEqual(self.viewer.currentPath, readable.path_nf)

    def test_scan_completion_keeps_pending_candidate_without_retrying_failed_image(self):
        for succeeded in (True, False):
            with self.subTest(succeeded=succeeded):
                self.viewer.currentPath = None
                self.viewer.resetImageCandidates()
                first = self.make_image("corrupt.png", "PNG", mtime=2)
                Path(first.path_nf).write_bytes(b"invalid image")
                second = self.make_image("readable.png", "PNG", mtime=1)
                self.viewer._scan_seen_paths.update((first.path_nf, second.path_nf))
                self.viewer._scan_start_time = time.monotonic()
                with patch.object(
                    self.viewer, "loadImageFromFile", wraps=self.viewer.loadImageFromFile
                ) as load:
                    self.viewer.queueImageCandidates([first, second])
                    self.viewer.finishLoadingImages(succeeded, {first.name, second.name})
                    load.assert_called_once_with(first)
                    self.qt_app.processEvents()
                    self.assertEqual(self.viewer.currentPath, second.path_nf)
                    self.assertEqual([args[0] for args, _ in load.call_args_list], [first, second])

    def test_changes_during_scan_require_manual_reload(self):
        first = self.make_image("first.png", "PNG")
        second = self.make_image("second.png", "PNG")
        folder = self.temp.name
        self.viewer.pollToggle.setChecked(False)
        self.viewer.loadImagesFromFolder(first.path_nf)
        self.wait_for_scan()

        self.viewer.reloadCurrentFolder()
        self.assertIn(folder, self.viewer.watcher.directories())
        self.assertTrue(self.viewer.imageLoader.wait(5000))
        # The worker has already observed the file. Deliver the notification
        # before the queued scan completion reaches the viewer.
        os.unlink(second.path_nf)
        self.viewer.on_directory_changed(folder)
        self.wait_for_scan()
        self.assertIn("Reload Current Folder", self.viewer.statusBar().currentMessage())
        self.assertIsNotNone(self.viewer.mtimeOrderSet.get(second.path_nf))
        loader = self.viewer.imageLoader
        self.viewer.on_directory_changed(folder)
        self.assertIs(self.viewer.imageLoader, loader)

        self.viewer.refreshFolder.trigger()
        self.assertIsNot(self.viewer.imageLoader, loader)
        self.wait_for_scan()
        self.assertIsNone(self.viewer.mtimeOrderSet.get(second.path_nf))
        self.assertEqual(self.viewer.currentPath, first.path_nf)

    def test_first_poll_after_scan_requires_manual_reload_for_changes(self):
        first = self.make_image("first.png", "PNG")
        second = self.make_image("second.png", "PNG")
        self.viewer.pollToggle.setChecked(True)
        with patch.object(self.viewer.watcher, "addPath", return_value=False):
            self.viewer.loadImagesFromFolder(first.path_nf)
            self.wait_for_scan()
            loader = self.viewer.imageLoader
            os.unlink(second.path_nf)
            self.viewer._poll_directory_changes()
            self.assertIs(self.viewer.imageLoader, loader)
            self.assertFalse(self.viewer._directory_poll_timer.isActive())
            self.viewer.pollToggle.setChecked(False)
            self.viewer.pollToggle.setChecked(True)
            self.assertFalse(self.viewer._directory_poll_timer.isActive())
            with patch("yee3.app.os.listdir") as listdir:
                self.viewer._poll_directory_changes()
                listdir.assert_not_called()
            self.assertIsNotNone(self.viewer.mtimeOrderSet.get(second.path_nf))

            self.viewer.statusBar().showMessage("Polling stopped")
            self.assertEqual(self.viewer.watchStatusLabel.text(), "Reload Current Folder")
            self.viewer.refreshFolder.trigger()
            self.wait_for_scan()

        self.assertTrue(self.viewer._directory_poll_timer.isActive())
        self.assertIsNone(self.viewer.mtimeOrderSet.get(second.path_nf))
        self.assertNotEqual(self.viewer.watchStatusLabel.text(), "Reload Current Folder")

    def test_poll_uses_images_discovered_during_scan_as_baseline(self):
        first = self.make_image("first.png", "PNG")
        added = Path(self.temp.name) / "added.PNG"
        scandir = os.scandir

        def scan_with_new_image(folder):
            Image.new("RGB", (8, 12)).save(added)
            return scandir(folder)

        self.viewer.pollToggle.setChecked(True)
        with (
            patch.object(self.viewer.watcher, "addPath", return_value=False),
            patch("yee3.app.os.scandir", side_effect=scan_with_new_image),
            patch("yee3.app.os.listdir", wraps=os.listdir) as listdir,
        ):
            self.viewer.loadImagesFromFolder(first.path_nf)
            self.wait_for_scan()
            listdir.assert_not_called()

        loader = self.viewer.imageLoader
        self.assertIsNotNone(self.viewer.mtimeOrderSet.get(str(added)))
        (Path(self.temp.name) / "notes.txt").write_text("notes")
        self.viewer._poll_directory_changes()
        self.assertIs(self.viewer.imageLoader, loader)
        self.assertNotEqual(self.viewer.watchStatusLabel.text(), "Reload Current Folder")

    def test_poll_ignores_unchanged_broken_image_symlink(self):
        first = self.make_image("first.png", "PNG")
        broken = Path(self.temp.name) / "broken.png"
        try:
            broken.symlink_to(Path(self.temp.name) / "missing.png")
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Symlinks unavailable: {error}")
        self.viewer.pollToggle.setChecked(True)
        with patch.object(self.viewer.watcher, "addPath", return_value=False):
            self.viewer.loadImagesFromFolder(first.path_nf)
            self.wait_for_scan()
        loader = self.viewer.imageLoader

        self.viewer._poll_directory_changes()

        self.assertIs(self.viewer.imageLoader, loader)
        self.assertIsNone(self.viewer.mtimeOrderSet.get(str(broken)))
        self.assertNotEqual(self.viewer.watchStatusLabel.text(), "Reload Current Folder")

    def test_failed_rescan_keeps_last_successful_poll_baseline(self):
        first = self.make_image("first.png", "PNG")
        second = self.make_image("second.png", "PNG")
        self.viewer.pollToggle.setChecked(True)
        with patch.object(self.viewer.watcher, "addPath", return_value=False):
            self.viewer.loadImagesFromFolder(first.path_nf)
            self.wait_for_scan()
            os.unlink(second.path_nf)
            with patch("yee3.app.os.scandir", side_effect=OSError("scan failed")):
                self.viewer.reloadCurrentFolder()
                self.wait_for_scan()
            loader = self.viewer.imageLoader

            self.viewer._poll_directory_changes()

        self.assertIs(self.viewer.imageLoader, loader)
        self.assertIsNotNone(self.viewer.mtimeOrderSet.get(second.path_nf))
        self.assertEqual(self.viewer.watchStatusLabel.text(), "Reload Current Folder")

    def test_failed_automatic_rescan_does_not_consume_poll_difference(self):
        first = self.make_image("first.png", "PNG")
        second = self.make_image("second.png", "PNG")
        self.viewer.pollToggle.setChecked(True)
        with patch.object(self.viewer.watcher, "addPath", return_value=False):
            self.viewer.loadImagesFromFolder(first.path_nf)
            self.wait_for_scan()
            self.viewer._poll_directory_changes()
            initial_loader = self.viewer.imageLoader
            os.unlink(second.path_nf)

            with patch("yee3.app.os.scandir", side_effect=OSError("scan failed")):
                self.viewer._poll_directory_changes()
                self.assertIsNot(self.viewer.imageLoader, initial_loader)
                self.wait_for_scan()

            failed_loader = self.viewer.imageLoader
            self.viewer._poll_directory_changes()
            self.assertIs(self.viewer.imageLoader, failed_loader)
            self.assertIsNotNone(self.viewer.mtimeOrderSet.get(second.path_nf))
            self.assertEqual(self.viewer.watchStatusLabel.text(), "Reload Current Folder")

            self.viewer.refreshFolder.trigger()
            self.wait_for_scan()

        self.assertIsNone(self.viewer.mtimeOrderSet.get(second.path_nf))

    def test_failed_manual_reload_preserves_reload_required_without_polling(self):
        first = self.make_image("first.png", "PNG")
        second = self.make_image("second.png", "PNG")
        self.viewer.pollToggle.setChecked(False)
        self.viewer.loadImagesFromFolder(first.path_nf)
        self.wait_for_scan()

        self.viewer.reloadCurrentFolder()
        self.assertTrue(self.viewer.imageLoader.wait(5000))
        os.unlink(second.path_nf)
        self.viewer.on_directory_changed(self.temp.name)
        self.wait_for_scan()
        self.assertEqual(self.viewer.watchStatusLabel.text(), "Reload Current Folder")

        with patch("yee3.app.os.scandir", side_effect=OSError("scan failed")):
            self.viewer.refreshFolder.trigger()
            self.wait_for_scan()
        self.assertEqual(self.viewer.watchStatusLabel.text(), "Reload Current Folder")
        self.assertIn("Unable to finish folder scan", self.viewer.statusBar().currentMessage())
        self.assertIsNotNone(self.viewer.mtimeOrderSet.get(second.path_nf))

        self.viewer.refreshFolder.trigger()
        self.wait_for_scan()
        self.assertNotEqual(self.viewer.watchStatusLabel.text(), "Reload Current Folder")
        self.assertIsNone(self.viewer.mtimeOrderSet.get(second.path_nf))

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
