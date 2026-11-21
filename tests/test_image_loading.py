import json
import os
import tempfile
import time
from threading import Event
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QPointF, QTimer, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QKeyEvent, QKeySequence, QMouseEvent, QMovie, QPainter, QPdfWriter, QShortcut, QWheelEvent

from yee3.app import FastOrderedSet, ImageData, ImageLoaderWorker, ImageViewer, ImageViewerApplication, OSType


class ImageLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or ImageViewerApplication([])

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

    def test_folder_alias_keeps_current_image_and_reuses_indexed_paths(self):
        folder = Path(self.temp.name) / "images"
        folder.mkdir()
        alias = Path(self.temp.name) / "alias"
        try:
            alias.symlink_to(folder, target_is_directory=True)
        except (OSError, NotImplementedError) as error:
            self.skipTest(f"Directory symlinks unavailable: {error}")

        first = folder / "first.png"
        second = folder / "second.png"
        for path in (first, second):
            Image.new("RGB", (8, 12)).save(path)
        (folder / "broken.png").write_bytes(b"invalid image")
        self.viewer.loadImagesFromFolder(str(first))
        self.wait_for_scan()
        pixmap = self.viewer.currentPixmap

        self.viewer.loadImagesFromFolder(str(alias / "broken.png"))
        self.wait_for_scan()
        self.assertEqual(self.viewer.currentPath, str(first))
        self.assertIs(self.viewer.currentPixmap, pixmap)

        self.viewer.loadImagesFromFolder(str(alias / second.name))
        self.wait_for_scan()
        self.assertEqual(self.viewer.currentPath, str(second))
        self.assertEqual(
            {record.path_nf for record in self.viewer.mtimeOrderSet},
            {str(first), str(second), str(folder / "broken.png")},
        )

    def test_initial_batch_yields_between_failed_candidates(self):
        corrupt = Path(self.temp.name) / "corrupt.png"
        corrupt.write_bytes(b"invalid image")
        readable = self.make_image("readable.png", "PNG")
        self.viewer.imageLoader = ImageLoaderWorker(self.temp.name, self.viewer.pseudo_random_seed)
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

    def test_timestamp_filenames_follow_mtime_arrow_directions(self):
        older = self.make_image("20261121_0900.png", "PNG", mtime=1)
        middle = self.make_image("20261121_1000.png", "PNG", mtime=2)
        newer = self.make_image("20261121_1100.png", "PNG", mtime=3)
        for order in (self.viewer.fnameOrderSet, self.viewer.mtimeOrderSet):
            self.viewer.verticalOrderSet = order
            self.viewer.horizontalOrderSet = order
            for key, expected in (
                (Qt.Key_Up, older), (Qt.Key_Left, older),
                (Qt.Key_Down, newer), (Qt.Key_Right, newer),
            ):
                with self.subTest(order=order, key=key):
                    self.viewer.loadImageFromFile(middle)
                    event = QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier)
                    QApplication.sendEvent(self.viewer, event)
                    self.assertEqual(self.viewer.currentPath, expected.path_nf)

    def test_wheel_and_arrow_keys_use_the_same_image_directions(self):
        older = self.make_image("20261121_0900.png", "PNG", mtime=1)
        middle = self.make_image("20261121_1000.png", "PNG", mtime=2)
        newer = self.make_image("20261121_1100.png", "PNG", mtime=3)
        self.viewer.verticalOrderSet = self.viewer.mtimeOrderSet
        self.viewer.horizontalOrderSet = self.viewer.fnameOrderSet
        self.viewer.freeScroll.setChecked(True)
        viewport = self.viewer.scrollArea.viewport()

        for delta, expected in (
            (QPoint(0, 240), older), (QPoint(240, 0), older),
            (QPoint(0, -240), newer), (QPoint(-240, 0), newer),
        ):
            with self.subTest(delta=delta):
                self.viewer.loadImageFromFile(middle)
                self.viewer.scrollAccumulationX = 0
                self.viewer.scrollAccumulationY = 0
                event = QWheelEvent(
                    QPointF(5, 5), QPointF(viewport.mapToGlobal(QPoint(5, 5))),
                    QPoint(), delta, Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False,
                )
                QApplication.sendEvent(viewport, event)
                self.assertEqual(self.viewer.currentPath, expected.path_nf)

    def test_move_selects_the_same_image_as_down_and_skips_unreadable_candidates(self):
        self.make_image("older.png", "PNG", mtime=1)
        middle = self.make_image("middle.png", "PNG", mtime=2)
        broken = self.make_image("broken.png", "PNG", mtime=3)
        newer = self.make_image("newer.png", "PNG", mtime=4)
        Path(broken.path_nf).write_bytes(b"invalid image")
        self.viewer.loadImageFromFile(middle)

        with patch.object(self.viewer, "transferToDestination", return_value=True):
            self.assertTrue(self.viewer.moveToDestination(1))

        self.assertEqual(self.viewer.currentPath, newer.path_nf)
        self.assertIsNone(self.viewer.mtimeOrderSet.get(middle.path_nf))

    def test_unset_destination_double_click_only_configures(self):
        record = self.make_image("source.png", "PNG")
        self.viewer.loadImageFromFile(record)
        folder = Path(self.temp.name) / "destination"
        folder.mkdir()

        for widget, destinations in (
            (self.viewer.copyList, self.viewer.copyDestinations),
            (self.viewer.moveList, self.viewer.moveDestinations),
        ):
            with patch("yee3.app.QFileDialog.getExistingDirectory", return_value=str(folder)):
                widget.itemDoubleClicked.emit(widget.item(0))
            self.assertEqual(destinations["1"], str(folder))
            self.assertTrue(Path(record.path_nf).exists())
            self.assertFalse((folder / record.name).exists())

    def test_unset_destination_shortcuts_configure_and_transfer(self):
        folder = Path(self.temp.name) / "destination"
        folder.mkdir()

        for keys, move in (("Ctrl+1", False), ("Shift+1", True)):
            record = self.make_image(f"{keys}.png", "PNG")
            self.viewer.loadImageFromFile(record)
            shortcut = next(
                shortcut for shortcut in self.viewer.findChildren(QShortcut)
                if shortcut.key() == QKeySequence(keys)
            )
            with patch("yee3.app.QFileDialog.getExistingDirectory", return_value=str(folder)):
                shortcut.activated.emit()
            self.assertTrue((folder / record.name).exists())
            self.assertEqual(Path(record.path_nf).exists(), not move)
            self.assertEqual(self.viewer.destinationFor(1, move=move), str(folder))

    def test_clear_destination_removes_only_the_selected_assignment(self):
        for widget, destinations, refresh in (
            (self.viewer.copyList, self.viewer.copyDestinations, self.viewer.updateCopyList),
            (self.viewer.moveList, self.viewer.moveDestinations, self.viewer.updateMoveList),
        ):
            destinations.update({"1": self.temp.name, 1: self.temp.name, "2": self.temp.name})
            refresh()
            point = widget.visualItemRect(widget.item(0)).center()
            with patch("yee3.app.QMenu") as menu:
                menu.return_value.exec.return_value = menu.return_value.addAction.return_value
                widget.customContextMenuRequested.emit(point)
            self.assertEqual(destinations, {"2": self.temp.name})
            self.assertIn("(not set)", widget.item(0).text())
            self.assertTrue(Path(self.temp.name).is_dir())

    def test_double_click_fits_image_with_docks_and_keeps_window_position(self):
        path = Path(self.temp.name) / "portrait.png"
        Image.new("RGB", (300, 450), "red").save(path)
        self.viewer.loadImageFromFile(ImageData(path.name, str(path), 0, path.name))
        docks = (self.viewer.metadataDock, self.viewer.copyDock, self.viewer.moveDock)

        for shown in ((), *[(dock,) for dock in docks], docks):
            with self.subTest(docks=[dock.windowTitle() for dock in shown]):
                for dock in docks:
                    dock.setVisible(dock in shown)
                self.viewer.resize(760, 550)
                self.viewer.move(10, 30)
                self.qt_app.processEvents()

                target = self.viewer.imageDisplay
                for attempt in range(4):
                    event = QMouseEvent(
                        QEvent.MouseButtonDblClick, QPointF(5, 5),
                        QPointF(target.mapToGlobal(QPoint(5, 5))),
                        Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
                    )
                    QApplication.sendEvent(target, event)
                    self.qt_app.processEvents()
                    frame = self.viewer.frameGeometry()
                    if attempt == 0:
                        fitted_frame = frame
                    else:
                        self.assertEqual(frame, fitted_frame)

                viewport = self.viewer.scrollArea.viewport().size()
                image = target.size()
                self.assertLessEqual(abs(viewport.width() - image.width()), 2)
                self.assertLessEqual(abs(viewport.height() - image.height()), 2)
                self.assertTrue(self.viewer.screen().availableGeometry().contains(frame))

    def test_close_remains_responsive_until_active_scan_exits(self):
        entered_scan = Event()
        release_scan = Event()
        scandir = os.scandir

        def wait_in_scan(folder):
            entered_scan.set()
            release_scan.wait(5)
            return scandir(folder)

        self.viewer.pollToggle.setChecked(True)
        with patch("yee3.app.os.scandir", side_effect=wait_in_scan):
            self.viewer.loadImagesFromFolder(self.temp.name)
            try:
                self.assertTrue(entered_scan.wait(3))
                worker = self.viewer.imageLoader
                self.assertFalse(self.viewer.close())
                self.assertTrue(worker.isInterruptionRequested())
                self.assertTrue(worker.isRunning())
                self.assertTrue(self.viewer.isVisible())
                callback = Event()
                QTimer.singleShot(0, callback.set)
                self.qt_app.processEvents()
                self.assertTrue(callback.is_set())
            finally:
                release_scan.set()

            self.assertTrue(worker.wait(5000))
            self.qt_app.processEvents()

        self.assertFalse(self.viewer.isVisible())
        self.assertFalse(self.viewer.lazyLoadingInProgress)
        self.assertFalse(self.viewer._directory_poll_timer.isActive())

    def test_quit_requests_close_for_every_scanning_window(self):
        with patch.object(ImageViewer, "loadSettings"):
            other = ImageViewer(OSType.LINUX)
        other.saveSettings = lambda: None
        self.addCleanup(other.close)
        other.show()

        release_scan = Event()
        scandir = os.scandir

        def wait_in_scan(folder):
            release_scan.wait(5)
            return scandir(folder)

        viewers = (self.viewer, other)
        with patch("yee3.app.os.scandir", side_effect=wait_in_scan):
            try:
                for viewer in viewers:
                    viewer.loadImagesFromFolder(self.temp.name)
                QApplication.sendEvent(self.qt_app, QEvent(QEvent.Quit))
                for viewer in viewers:
                    self.assertTrue(viewer.imageLoader.isInterruptionRequested())
            finally:
                release_scan.set()

            for viewer in viewers:
                self.assertTrue(viewer.imageLoader.wait(5000))
            self.qt_app.processEvents()

        self.assertFalse(any(viewer.isVisible() for viewer in viewers))

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
