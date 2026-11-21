#!/usr/bin/env python3
import sys
import os
import json
import random
import bisect
import shutil
import subprocess
import unicodedata
from datetime import datetime
from typing import Dict, List
from dataclasses import dataclass, asdict
from enum import IntEnum, Enum, auto
import platform
import uuid
import io
import zipfile
from pathlib import Path
import time
from collections import deque
import math

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QLabel,
    QScrollArea,
    QFileDialog,
    QToolBar,
    QSizePolicy,
    QMenu,
    QDockWidget,
    QListWidget,
    QStatusBar,
    QToolButton,
    QWidget,
    QWidgetAction,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QPinchGesture,
    QPlainTextEdit,
    QFrame,
)
from PySide6.QtGui import (
    QPixmap,
    QPalette,
    QImageReader,
    QKeySequence,
    QPainter,
    QColor,
    QBrush,
    QAction,
    QShortcut,
    QFont,
    QMovie,
)
from PySide6.QtCore import (
    Qt,
    QTimer,
    QEvent,
    QPoint,
    QSize,
    QThread,
    Signal,
    QFileSystemWatcher,
    QByteArray,
)

try:
    from yee3.image_metadata import (
        ImageMetadataResult,
        parse_image_metadata_bytes,
        supports_image_metadata,
    )
except ModuleNotFoundError:
    from image_metadata import (
        ImageMetadataResult,
        parse_image_metadata_bytes,
        supports_image_metadata,
    )

# 0 <= decay < 1
scroll_factors_dict = {
    "limit": {
        "vertical": {
            "scroll": 2.5,
            "decay": 0.7,
            "release": 100.0,
            "threshold": 100,
            "max": 120,
        },
        "horizontal": {
            "scroll": 0.8,
            "decay": 0.5,
            "release": 2.0,
            "threshold": 80,
            "max": 100,
        },
        "interval": 0.2,
    },
    "free": {
        "vertical": {
            "scroll": 2.5,
            "decay": 0.7,
            "release": 1.1,
            "threshold": 100,
            "max": 1000,
        },
        "horizontal": {
            "scroll": 0.5,
            "decay": 0.9,
            "release": 1.1,
            "threshold": 100,
            "max": 1000,
        },
        "interval": -1,
    },
}


class OSType(Enum):
    WINDOWS = auto()
    LINUX = auto()
    MACOS = auto()
    UNKNOWN = auto()


def extract_preview_from_pxd(pxd_path):
    preview_paths = ["QuickLook/Thumbnail.webp", "QuickLook/Thumbnail.tiff"]
    if os.path.isfile(pxd_path):
        with open(pxd_path, "rb") as f:
            pxd_data = f.read()

        with zipfile.ZipFile(io.BytesIO(pxd_data), "r") as zip_ref:
            file_list = zip_ref.namelist()
            for preview_path in preview_paths:
                if preview_path in file_list:
                    with zip_ref.open(preview_path) as preview_file:
                        return preview_file.read()
    elif os.path.isdir(pxd_path):
        for preview_path in preview_paths:
            thumbnail_path = os.path.join(pxd_path, preview_path)
            if os.path.isfile(thumbnail_path):
                with open(thumbnail_path, "rb") as preview_file:
                    return preview_file.read()

    return None


def load_and_convert_avif(avif_path):
    img = Image.open(avif_path)
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PNG")
    img_bytes.seek(0)
    return img_bytes.read()


image_format_animated = ["gif", "webp"]
image_format_extractors = {
    "pxm": extract_preview_from_pxd,
    "pxd": extract_preview_from_pxd,
}
try:
    import pillow_avif
    from PIL import Image
except ImportError:
    pass
else:
    image_format_extractors["avif"] = load_and_convert_avif


def supportedImageFormats():
    supportedFormats = QImageReader.supportedImageFormats()
    imageExtensions = [str(fmt, "utf-8").lower() for fmt in supportedFormats]
    return imageExtensions + list(image_format_extractors.keys())


def has_image_extension(name, extensions):
    _, separator, extension = name.rpartition(".")
    return bool(separator) and extension.lower() in extensions


def transfer_with_unique_name(src, dst_dir, move=False):
    """
    Transfer a file to the specified directory.
    If a file with the same name already exists, it renames the new file to avoid overwriting.

    :param src: Source file path
    :param dst_dir: Destination directory
    :return: The new file path
    """
    if not os.path.exists(src):
        raise FileNotFoundError(f"Source file not found: {src}")

    os.makedirs(dst_dir, exist_ok=True)

    base_name = os.path.basename(src)
    name, ext = os.path.splitext(base_name)
    dst_path = os.path.join(dst_dir, base_name)

    counter = 1
    while os.path.exists(dst_path):
        dst_path = os.path.join(dst_dir, f"{name}-{counter}{ext}")
        counter += 1

    transfer_function = shutil.move if move else shutil.copy2
    transfer_function(src, dst_path)
    return dst_path


class RecentCounter:
    def __init__(self, time_window=1):
        self.times = deque()
        self.time_window = time_window

    def count(self):
        now = time.time()
        self.times.append(now)

        # Remove old entries
        while self.times and self.times[0] < now - self.time_window:
            self.times.popleft()

        return len(self.times)


class IterationTimer:
    def __init__(self):
        self.start_time = None
        self.total_time = 0
        self.count = 0
        self.recent_len = 100
        self.recent_times = deque(maxlen=self.recent_len)

    def start(self):
        self.start_time = time.perf_counter()

    def stop(self):
        if self.start_time is not None:
            elapsed = time.perf_counter() - self.start_time
            self.total_time += elapsed
            self.recent_times.append(elapsed)
            self.count += 1
            self.start_time = None

    def average_time(self):
        if len(self.recent_times) < self.recent_len:
            return None
        return sum(self.recent_times) / self.recent_len


class SortedList:
    """A simple sorted list implementation using bisect."""

    def __init__(self, sorted_items=()):
        self._list = list(sorted_items)

    def add(self, item):
        """Insert an item while keeping the list sorted."""
        bisect.insort_left(self._list, item)

    def bisect_left(self, item):
        """Return the insertion index for item."""
        return bisect.bisect_left(self._list, item)

    def remove(self, item):
        index = bisect.bisect_left(self._list, item)
        if index < len(self._list) and self._list[index] == item:
            self._list.pop(index)
        else:
            raise ValueError(f"{item} not found in SortedList")

    def retain(self, predicate):
        self._list = [key for key in self._list if predicate(key)]

    def clear(self):
        """Clear all elements in the list."""
        self._list.clear()

    def __getitem__(self, index):
        return self._list[index]

    def __len__(self):
        return len(self._list)

    def __repr__(self):
        return repr(self._list)


class ImageFile:
    entry: os.DirEntry  # | None
    path_nf: str
    stat_result: os.stat_result
    name: str

    def __init__(self, entry: os.DirEntry = None, path: str = ""):
        self.entry = entry
        if entry:
            self.path_nf = unicodedata.normalize("NFD", entry.path)
        elif path:
            path = os.path.abspath(path)
            self.path_nf = unicodedata.normalize("NFD", path)
        else:
            raise ValueError("Either entry or path must be provided")
        self.name = os.path.basename(self.path_nf)

    def stat(self):
        try:
            if self.entry:
                self.stat_result = self.entry.stat()
            else:
                self.stat_result = os.stat(self.path_nf)
        except FileNotFoundError:
            self.stat_result = None
            return None
        return self.stat_result

    def __str__(self):
        return self.path_nf


@dataclass
class ImageData:
    name: str
    path_nf: str
    st_mtime: float
    pseudo_random_hash: str

    @staticmethod
    def generate(pseudo_random_seed, imagefile: ImageFile):
        if imagefile.stat_result.st_ino > 0:
            image_identifier = f"{imagefile.stat_result.st_ino}"
        else:
            image_identifier = f"{imagefile.name}:{imagefile.stat_result.st_ctime:.32f}"
        pseudo_random_hash = str(uuid.uuid5(pseudo_random_seed, image_identifier))
        return ImageData(
            name=imagefile.name,
            path_nf=imagefile.path_nf,
            st_mtime=imagefile.stat_result.st_mtime,
            pseudo_random_hash=pseudo_random_hash,
        )


class DescendingFilename(str):
    """Reverse lexical comparison while retaining normal string equality."""

    def __lt__(self, other):
        return str.__gt__(self, other)


class FastOrderedSet:
    """
    An ordered set optimized for path-based lookup and next/previous navigation.
    """

    def __init__(self, iterable=None, key_func=None):
        """Initialize an ordered set, optionally inserting its initial items."""
        self.items: List[ImageData] = []  # List for index-based access
        self.keys = SortedList()  # Binary search optimized
        self.index_map: Dict[str, ImageData] = {}  # Map paths to objects
        self.next_map: Dict[str, str | None] = {}  # Path links for this set's items[] order
        self.prev_map: Dict[str, str | None] = {}  # Path links for this set's items[] order
        self.key_func = key_func
        if iterable:
            self.update(iterable)

    def add(self, item: ImageData):
        """Insert a new path in O(N) worst-case time."""
        if item.path_nf in self.index_map:
            return  # Duplicate, skip insertion

        if self.key_func is None:
            # Insert at a random position if no sorting key is provided.
            index = random.randint(0, len(self.items))
        else:
            key = (self.key_func(item), item.path_nf)
            index = self.keys.bisect_left(key)  # Get insertion index (O(log N))
            self.keys.add(key)

        prev_path = self.items[index - 1].path_nf if index > 0 else None
        next_path = self.items[index].path_nf if index < len(self.items) else None
        self.items.insert(index, item)  # Insert at the correct position (O(N))
        self.index_map[item.path_nf] = item  # Store reference for quick lookup
        self.prev_map[item.path_nf] = prev_path
        self.next_map[item.path_nf] = next_path
        if prev_path is not None:
            self.next_map[prev_path] = item.path_nf
        if next_path is not None:
            self.prev_map[next_path] = item.path_nf

    def update(self, sequence):
        """
        Insert new paths from a sequence.

        - Iterates through the sequence and adds new elements.
        - Duplicates are ignored.
        """
        for item in sequence:
            self.add(item)

    def upsert_batch(self, records):
        """Apply a scan batch to a keyed order, preserving unchanged positions."""
        reordered = {}
        for record in records:
            path = record.path_nf
            existing = self.index_map.get(path)
            if existing is None or self.key_func(existing) != self.key_func(record):
                reordered[path] = record
            else:
                key = (self.key_func(record), path)
                self.items[self.keys.bisect_left(key)] = record
                self.index_map[path] = record

        # Rebuild only when changes are numerous relative to the index, so
        # successive fixed-size batches cannot keep rebuilding a growing order.
        if len(reordered) < max(64, (len(self.items) + 7) // 8):
            for path, record in reordered.items():
                existing = self.index_map.get(path)
                if existing is not None:
                    self.remove(existing)
                self.add(record)
            return

        entries = [
            (key, record)
            for key, record in zip(self.keys, self.items)
            if record.path_nf not in reordered
        ]
        entries.extend(
            ((self.key_func(record), path), record)
            for path, record in reordered.items()
        )
        entries.sort(key=lambda entry: entry[0])
        self.clear()
        self.keys = SortedList(key for key, _ in entries)
        previous = None
        for _, record in entries:
            path = record.path_nf
            self.items.append(record)
            self.index_map[path] = record
            self.prev_map[path] = previous
            self.next_map[path] = None
            if previous is not None:
                self.next_map[previous] = path
            previous = path

    def remove(self, item: ImageData):
        """Remove the stored record for this path, if present."""
        item = self.index_map.get(item.path_nf)
        if item is None:
            return

        prev_path = self.prev_map.get(item.path_nf)
        next_path = self.next_map.get(item.path_nf)

        # Remove from index_map
        del self.index_map[item.path_nf]
        self.prev_map.pop(item.path_nf, None)
        self.next_map.pop(item.path_nf, None)

        if prev_path is not None:
            self.next_map[prev_path] = next_path
        if next_path is not None:
            self.prev_map[next_path] = prev_path

        # Remove from the items list
        self.items.remove(item)

        # If a key function is set, remove the key from SortedList as well
        if self.key_func is not None:
            key = (self.key_func(item), item.path_nf)
            self.keys.remove(key)

    def discard_paths(self, paths):
        """Remove paths in one pass while preserving the order of survivors."""
        self.items = [item for item in self.items if item.path_nf not in paths]
        self.keys.retain(lambda key: key[1] not in paths)
        previous = None
        for item in self.items:
            path = item.path_nf
            self.prev_map[path] = previous
            if previous is not None:
                self.next_map[previous] = path
            previous = path
        if previous is not None:
            self.next_map[previous] = None
        for path in paths:
            self.index_map.pop(path, None)
            self.prev_map.pop(path, None)
            self.next_map.pop(path, None)

    def clear(self):
        """Remove all elements from the set."""
        self.items.clear()
        self.keys.clear()
        self.index_map.clear()
        self.next_map.clear()
        self.prev_map.clear()

    def get(self, value: str):
        """Return the ImageData for the given path in this set."""
        return self.index_map.get(value)

    def first_path(self):
        """Return the first path in this set's items[] order."""
        if not self.items:
            return None
        return self.items[0].path_nf

    def last_path(self):
        """Return the last path in this set's items[] order."""
        if not self.items:
            return None
        return self.items[-1].path_nf

    def next_path(self, value: str, loop=False):
        """Return the next path in this set's items[] order."""
        if value not in self.index_map:
            raise ValueError(f"'{value}' not found in FastOrderedSet")
        next_path = self.next_map.get(value)
        if next_path is None and loop:
            return self.first_path()
        return next_path

    def prev_path(self, value: str, loop=False):
        """Return the previous path in this set's items[] order."""
        if value not in self.index_map:
            raise ValueError(f"'{value}' not found in FastOrderedSet")
        prev_path = self.prev_map.get(value)
        if prev_path is None and loop:
            return self.last_path()
        return prev_path

    def __len__(self):
        """Return the number of elements. O(1)."""
        return len(self.items)

    def __iter__(self):
        """Iterate through elements in order. O(N)."""
        return iter(self.items)

    def __repr__(self):
        """Return a string representation of the set. O(N)."""
        return f"FastOrderedSet({self.items})"


class VerticalGauge(QWidget):
    """
    Gauge for vertical scrolling (displayed on the left edge of the screen).
    Positive direction (upward scroll) is blue, negative direction (downward scroll) is red.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.value = 0
        self.max_value = 300
        self.opacity = 0.5
        self.bar_width = 10  # Fixed width of the vertical gauge
        self.fixed_height = 200  # Fixed max height of the vertical gauge
        self.hide()

    def updateGauge(self, value):
        """Update the length of the gauge and refresh the display"""
        self.value = max(-self.max_value, min(value, self.max_value))
        if abs(self.value) > 0:
            self.show()
        else:
            self.hide()
        self.repaint()

    def paintEvent(self, event):
        """Render the gauge"""
        if self.value == 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        gauge_length = (self.fixed_height / self.max_value) * abs(self.value)
        color = (
            QColor(100, 200, 255, int(self.opacity * 255))
            if self.value > 0
            else QColor(255, 100, 100, int(self.opacity * 255))
        )
        y_pos = 0 if self.value > 0 else self.fixed_height - gauge_length
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.NoPen)
        painter.drawRect(0, int(y_pos), int(self.bar_width), int(gauge_length))


class HorizontalGauge(QWidget):
    """
    Gauge for horizontal scrolling (displayed at the top of the screen).
    Positive values follow the Left arrow and are blue. Negative values follow the Right arrow and are red.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.value = 0
        self.max_value = 300
        self.opacity = 0.5
        self.bar_height = 10  # Fixed height of the horizontal gauge
        self.fixed_width = 200  # Fixed max width of the horizontal gauge
        self.hide()

    def updateGauge(self, value):
        """Update the length of the gauge and refresh the display"""
        self.value = max(-self.max_value, min(value, self.max_value))
        if abs(self.value) > 0:
            self.show()
        else:
            self.hide()
        self.repaint()

    def paintEvent(self, event):
        """Render the gauge"""
        if self.value == 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        gauge_length = (self.fixed_width / self.max_value) * abs(self.value)
        color = (
            QColor(100, 200, 255, int(self.opacity * 255))
            if self.value > 0
            else QColor(255, 100, 100, int(self.opacity * 255))
        )
        x_pos = 0 if self.value > 0 else self.fixed_width - gauge_length
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.NoPen)
        painter.drawRect(int(x_pos), 0, int(gauge_length), int(self.bar_height))


class ImageDisplayWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pixmap = None
        self.movie = None
        self.scaleFactor = 1.0  # Scale factor, adjusted as needed

    def setData(self, pixmap: QPixmap, movie: QMovie = None):
        """Set the image data"""
        if self.movie:
            self.movie.stop()
            self.movie.frameChanged.disconnect(self.update)
            # self.movie.deleteLater()
        if movie:
            self.movie = movie
            self.movie.frameChanged.connect(self.update)
            self.movie.start()
        else:
            self.movie = None
        self.setPixmap(pixmap)

    def setPixmap(self, pixmap: QPixmap):
        """Set the image pixmap"""
        self.pixmap = pixmap
        self.update()  # Redraw to update the image

    def clearData(self):
        """Clear the displayed image"""
        if self.movie:
            self.movie.stop()
            self.movie.frameChanged.disconnect(self.update)
            # self.movie.deleteLater()
        self.pixmap = None
        self.update()  # Redraw to reflect the change

    def setScaleFactor(self, factor: float):
        """Set the scale factor"""
        self.scaleFactor = factor
        self.update()

    def paintEvent(self, event):
        """Render the image on the widget"""
        painter = QPainter(self)
        scaleFactor = 1
        if self.movie:
            # Draw the movie frame
            self.pixmap = self.movie.currentPixmap()
            scaleFactor = self.scaleFactor
        if self.pixmap:
            if scaleFactor == 1:
                scaled_pixmap = self.pixmap
            else:
                # Scale the image as needed
                new_width = int(self.pixmap.width() * scaleFactor)
                new_height = int(self.pixmap.height() * scaleFactor)
                scaled_pixmap = self.pixmap.scaled(
                    new_width, new_height, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            # Calculate position to center the image within the widget
            x = (self.width() - scaled_pixmap.width()) // 2
            y = (self.height() - scaled_pixmap.height()) // 2
            painter.drawPixmap(x, y, scaled_pixmap)


class MetadataPlainTextEdit(QPlainTextEdit):
    def wheelEvent(self, event):
        super().wheelEvent(event)
        event.accept()


class MetadataScrollArea(QScrollArea):
    def wheelEvent(self, event):
        super().wheelEvent(event)
        event.accept()


class MetadataSection(QWidget):
    def __init__(self, title: str, compact=False, parent=None):
        super().__init__(parent)
        self.compact = compact
        self._copy_feedback_timer = QTimer(self)
        self._copy_feedback_timer.setSingleShot(True)
        self._copy_feedback_timer.timeout.connect(
            lambda: self.copyButton.setText("Copy")
        )
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        self.titleLabel = QLabel(title)
        self.titleLabel.setStyleSheet("font-size: 18px; font-weight: 700; color: #f2f2f2;")
        header.addWidget(self.titleLabel)
        header.addStretch()

        self.copyButton = QPushButton("Copy")
        self.copyButton.clicked.connect(self.copyContent)
        self.copyButton.setStyleSheet(
            "QPushButton {"
            "background: #3a3a3a; color: #f2f2f2; border: 1px solid #4c4c4c;"
            "border-radius: 10px; padding: 8px 14px; font-size: 14px;"
            "}"
            "QPushButton:hover { background: #474747; }"
        )
        header.addWidget(self.copyButton)
        layout.addLayout(header)

        self.textEdit = MetadataPlainTextEdit()
        self.textEdit.setReadOnly(True)
        self.textEdit.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.textEdit.setStyleSheet(
            "QPlainTextEdit {"
            "background: #1f1f1f; color: #ededed; border: 1px solid #3a3a3a;"
            "border-radius: 12px; padding: 12px; font-size: 14px;"
            "}"
        )
        font = QFont("Courier New")
        font.setStyleHint(QFont.Monospace)
        self.textEdit.setFont(font)
        if compact:
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
            self.textEdit.setMinimumHeight(88)
            self.textEdit.setMaximumHeight(180)
        else:
            self.textEdit.setMinimumHeight(100)
            self.textEdit.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        layout.addWidget(self.textEdit)
        self.hide()

    def setTitle(self, title: str):
        self.titleLabel.setText(title)

    def setContent(self, text: str | None):
        content = text or ""
        self.textEdit.setPlainText(content)
        self.copyButton.setEnabled(bool(content.strip()))
        self.setVisible(bool(content.strip()))

    def copyContent(self):
        QApplication.clipboard().setText(self.textEdit.toPlainText())
        self.copyButton.setText("Copied")
        self._copy_feedback_timer.start(900)
        window = self.window()
        if isinstance(window, QMainWindow) and window.statusBar() is not None:
            window.statusBar().showMessage("Copied metadata", 1200)


class MetadataPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        self.metaLabel = QLabel("Image metadata will appear here.")
        self.metaLabel.setWordWrap(True)
        self.metaLabel.setStyleSheet("font-size: 13px; color: #b8b8b8;")
        root.addWidget(self.metaLabel)

        self.scroll = MetadataScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        root.addWidget(self.scroll, 1)

        self.content = QWidget()
        self.contentLayout = QVBoxLayout(self.content)
        self.contentLayout.setContentsMargins(0, 0, 0, 0)
        self.contentLayout.setSpacing(16)
        self.scroll.setWidget(self.content)

        self.parametersSection = MetadataSection("Parameters")
        self.promptSection = MetadataSection("Positive Prompt", compact=True)
        self.negativePromptSection = MetadataSection("Negative Prompt", compact=True)
        self.promptJsonSection = MetadataSection("Prompt JSON")
        self.workflowJsonSection = MetadataSection("Workflow JSON")
        self.rawSection = MetadataSection("Raw Metadata")

        self.contentLayout.addWidget(self.parametersSection, 1)
        self.contentLayout.addWidget(self.promptSection, 0)
        self.contentLayout.addWidget(self.negativePromptSection, 0)
        self.contentLayout.addWidget(self.promptJsonSection, 1)
        self.contentLayout.addWidget(self.workflowJsonSection, 1)
        self.contentLayout.addWidget(self.rawSection, 1)

        self.setStyleSheet("MetadataPanel { background: #262626; }")

    def sizeHint(self):
        return QSize(360, 640)

    def minimumSizeHint(self):
        return QSize(260, 240)

    def setPlaceholder(self, text: str):
        self.metaLabel.setText(text)
        self.clearSections()

    def setLoading(self, path: str):
        self.metaLabel.setText("Loading metadata...")
        self.clearSections()

    def setError(self, path: str, fmt: str, error: str):
        self.metaLabel.setText(f"Metadata\nFormat: {fmt}\nError: {error}")
        self.clearSections()

    def setResult(self, result: ImageMetadataResult):
        facts = [f"Format: {result.format}"]
        generator = result.fields.get("generator")
        if generator:
            facts.append(f"Generator: {generator}")
        self.metaLabel.setText("  |  ".join(str(item) for item in facts))

        parameters_text = result.fields.get("parameters_raw") or result.raw.get("parameters")
        is_comfy = bool(
            generator == "ComfyUI"
            or result.fields.get("prompt_json") is not None
            or result.fields.get("workflow_json") is not None
        )
        self.parametersSection.setTitle(
            "Parameters" if parameters_text else "Metadata"
        )
        self.parametersSection.setContent(parameters_text)

        prompt_text = result.fields.get("prompt")
        if prompt_text == parameters_text:
            prompt_text = None
        if is_comfy:
            self.promptSection.setContent(prompt_text)
            self.negativePromptSection.setContent(result.fields.get("negative_prompt"))
        else:
            self.promptSection.setContent(None)
            self.negativePromptSection.setContent(None)

        prompt_json = stringify_json_section(result.fields.get("prompt_json"))
        workflow_json = stringify_json_section(result.fields.get("workflow_json"))
        self.promptJsonSection.setContent(prompt_json)
        self.workflowJsonSection.setContent(workflow_json)

        raw_payload = build_raw_metadata_text(result.raw)
        # Keep the parameters view compact, but expose other metadata when
        # there is no parameters section to display it.
        if is_comfy or not parameters_text:
            self.rawSection.setContent(
                raw_payload if raw_payload != parameters_text else None
            )
        else:
            self.rawSection.setContent(None)

        if not any(
            not section.isHidden()
            for section in (
                self.parametersSection,
                self.promptSection,
                self.negativePromptSection,
                self.promptJsonSection,
                self.workflowJsonSection,
                self.rawSection,
            )
        ):
            self.metaLabel.setText("No metadata found.")

    def clearSections(self):
        for section in (
            self.parametersSection,
            self.promptSection,
            self.negativePromptSection,
            self.promptJsonSection,
            self.workflowJsonSection,
            self.rawSection,
        ):
            section.setContent(None)


def stringify_json_section(value):
    if value is None:
        return None
    try:
        return json.dumps(value, indent=2, ensure_ascii=False)
    except Exception:
        return str(value)


def build_raw_metadata_text(raw: dict):
    if not raw:
        return None
    try:
        return json.dumps(raw, indent=2, ensure_ascii=False)
    except Exception:
        lines = []
        for key, value in raw.items():
            lines.append(f"{key}\n{value}")
        return "\n\n".join(lines)


class ReplaceDialogResult(IntEnum):
    CANCEL = 0
    REPLACE = 1
    RENAME = 2


class ReplaceDialog(QDialog):
    def __init__(self, filePath, pixmap, parent=None):
        super().__init__(parent)
        self.setWindowTitle("File Already Exists")
        self.resize(500, 400)  # Adjust size as needed

        # Create layout
        main_layout = QVBoxLayout(self)

        # Message label
        message = QLabel(f"{filePath} already exists.")
        message.setFixedWidth(400)
        message.setWordWrap(True)
        message.setMaximumHeight(400)
        message.setAlignment(Qt.AlignCenter | Qt.AlignTop)
        main_layout.addWidget(message)

        # Image preview label
        if pixmap:
            image_label = QLabel()
            image_label.setPixmap(pixmap)
            image_label.setAlignment(Qt.AlignCenter)
            main_layout.addWidget(image_label)

        # Create button layout
        button_layout = QHBoxLayout()
        self.cancelButton = QPushButton("Cancel")
        self.replaceButton = QPushButton("Replace")
        self.renameButton = QPushButton("Rename")
        button_layout.addWidget(self.cancelButton)
        button_layout.addWidget(self.replaceButton)
        button_layout.addWidget(self.renameButton)
        main_layout.addLayout(button_layout)

        # Connect button signals (returns 1, 2, or 0 respectively)
        self.replaceButton.clicked.connect(
            lambda: self.done(ReplaceDialogResult.REPLACE)
        )
        self.renameButton.clicked.connect(lambda: self.done(ReplaceDialogResult.RENAME))
        self.cancelButton.clicked.connect(lambda: self.done(ReplaceDialogResult.CANCEL))


class ImageLoaderWorker(QThread):
    imageLoaded = Signal(str, bool)
    finishedLoading = Signal(bool, object)

    def __init__(self, folder, pseudo_random_seed, filePath=None, parent=None):
        super().__init__(parent)
        self.folder = unicodedata.normalize("NFD", folder)
        self.pseudo_random_seed = pseudo_random_seed
        self.filePath = os.path.abspath(filePath) if filePath is not None else None
        self.batch_count_threshold = 10

    def run(self):
        scan_succeeded = False
        candidate_names = set()
        try:
            extensions = set(supportedImageFormats())

            def compute_batch_count(x):
                min = 10
                if x <= 0:
                    return min
                y = 10 * x**-1.16
                if y <= min:
                    return min
                return math.ceil(y)

            self.batch_count_threshold = 10
            data = []
            last_emit_timestamp = datetime.now()
            timer = IterationTimer()
            if self.filePath is not None:
                imagefile = ImageFile(path=self.filePath)
                if has_image_extension(imagefile.name, extensions):
                    if imagefile.stat():
                        imageData = ImageData.generate(self.pseudo_random_seed, imagefile)
                        self.imageLoaded.emit(json.dumps([asdict(imageData)]), False)
            timer.start()
            for entry in os.scandir(self.folder):
                # Never use ‘continue’; msleep must run at regular intervals
                imagefile = ImageFile(entry=entry)
                if has_image_extension(imagefile.name, extensions):
                    candidate_names.add(imagefile.name)
                    if imagefile.stat():
                        imageData = ImageData.generate(
                            self.pseudo_random_seed, imagefile
                        )
                        data.append(asdict(imageData))
                now = datetime.now()
                timer.stop()
                if (avg := timer.average_time()) is not None:
                    self.batch_count_threshold = compute_batch_count(avg * 1000)
                if (
                    len(data) >= self.batch_count_threshold
                    or (now - last_emit_timestamp).total_seconds() > 0.25
                ):
                    if len(data) > 0:
                        # handleNewImage
                        self.imageLoaded.emit(json.dumps(data), True)
                        data = []
                    self.msleep(10)
                    last_emit_timestamp = now
                timer.start()
            if len(data) > 0:
                # handleNewImage
                self.imageLoaded.emit(json.dumps(data), True)
            scan_succeeded = True
        except Exception as e:
            print("Error during folder scanning:", e)
        finally:
            self.finishedLoading.emit(scan_succeeded, candidate_names)


class ImageViewer(QMainWindow):
    """
    ImageViewer is a PyQt5 application that displays images from a selected folder.
    It supports two distinct navigation orders:
      - Vertical navigation: images sorted by last modified time (newest first).
      - Horizontal navigation: images displayed in a random order.

    The application also supports:
      - Copying the current image to a designated folder via keyboard shortcuts
        (Command+1 through Command+9) or by double–clicking the image.
      - A right–side dock showing copy destination assignments, which is hidden by default
        and can be shown via the File menu.

    The File menu includes the following actions:
      - Open Folder: Opens a folder dialog to load images.
      - Open File: Opens a file dialog to select an image file.
      - Reveal in Finder: Reveals the current file in Finder (macOS).
      - Copy to …: Shows the copy destination dock.

    The status bar is always visible from the start.
    """

    def __init__(self, os_type: OSType):
        super().__init__()
        self.setWindowTitle("Yee3")

        self.os_type = os_type
        if self.os_type == OSType.MACOS:
            self.setUnifiedTitleAndToolBarOnMac(True)
            # self.setWindowFlags(Qt.Window)
            # self.setWindowFlags(Qt.Window | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
            self.grabGesture(Qt.PinchGesture)
        elif self.os_type == OSType.WINDOWS:
            # self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
            pass
        elif self.os_type == OSType.LINUX:
            # self.setWindowFlags(Qt.Window | Qt.WindowTitleHint | Qt.WindowCloseButtonHint)
            pass

        # Load window settings (size, position, and copy destinations) from configuration file.
        self.copyDestinations = {}  # Mapping for keys 1..9 to copy destination folders.
        self.moveDestinations = {}  # Mapping for keys 1..9 to move destination folders.
        self.loadSettings()
        if self.size().isEmpty():
            self.resize(800, 600)
        # Ensure the main window gets focus for key events.
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocus()

        self.firstShow = (
            True  # Flag to ensure first-show adjustments are made only once.
        )

        # Create a label to display images.
        self.imageDisplay = ImageDisplayWidget()
        self.imageDisplay.setBackgroundRole(QPalette.Base)
        self.imageDisplay.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

        # Set up a scroll area with a black background.
        self.scrollArea = QScrollArea()
        self.scrollArea.setAlignment(Qt.AlignCenter)
        self.scrollArea.setStyleSheet("background-color: #222;")
        self.scrollArea.setWidget(self.imageDisplay)
        # Prevent the scroll area from taking focus so that key events are handled by the main window.
        self.scrollArea.setFocusPolicy(Qt.NoFocus)
        self.setCentralWidget(self.scrollArea)

        # Install an event filter to capture double-click events.
        self.imageDisplay.installEventFilter(self)
        self.scrollArea.installEventFilter(self)
        self.scrollArea.viewport().installEventFilter(self)

        # Create and always show the status bar.
        self.setStatusBar(QStatusBar(self))
        self.statusBar().show()

        # Create a dock widget for copy destinations.
        self.copyDock = QDockWidget("Copy Destinations", self)
        self.copyList = QListWidget()
        self.copyDock.setWidget(self.copyList)
        # Initially hide the dock widget.
        self.copyDock.hide()
        self.addDockWidget(Qt.RightDockWidgetArea, self.copyDock)
        self.copyList.itemDoubleClicked.connect(self.onCopyListDoubleClicked)
        self.copyList.setContextMenuPolicy(Qt.CustomContextMenu)
        self.copyList.customContextMenuRequested.connect(
            lambda pos: self.showDestinationMenu(pos, move=False)
        )
        self.updateCopyList()
        # Adjust image scaling after the dock's visibility changes.
        self.copyDock.visibilityChanged.connect(
            lambda visible: QTimer.singleShot(0, self.adjustImageScale)
        )

        # Create a dock widget for move destinations.
        self.moveDock = QDockWidget("Move Destinations", self)
        self.moveList = QListWidget()
        self.moveDock.setWidget(self.moveList)
        self.moveDock.hide()
        self.addDockWidget(Qt.RightDockWidgetArea, self.moveDock)
        self.moveList.itemDoubleClicked.connect(self.onMoveListDoubleClicked)
        self.moveList.setContextMenuPolicy(Qt.CustomContextMenu)
        self.moveList.customContextMenuRequested.connect(
            lambda pos: self.showDestinationMenu(pos, move=True)
        )
        self.updateMoveList()
        self.moveDock.visibilityChanged.connect(
            lambda visible: QTimer.singleShot(0, self.adjustImageScale)
        )

        self.metadataDock = QDockWidget("Info", self)
        self.metadataPanel = MetadataPanel()
        self.metadataDock.setWidget(self.metadataPanel)
        self.metadataDock.setMinimumWidth(260)
        self.metadataDock.hide()
        self.addDockWidget(Qt.RightDockWidgetArea, self.metadataDock)
        self.metadataDock.visibilityChanged.connect(self.onMetadataDockVisibilityChanged)

        # Create the menu bar and add the "File" menu with several actions.
        self.createMenus()

        # mtime order: images sorted by last modified time (newest first).
        self.mtimeOrderSet = FastOrderedSet(key_func=lambda p: -1 * p.st_mtime)
        # random order: images in random order (can be changed later to filename order).
        self.randomOrderSet = FastOrderedSet(key_func=lambda p: p.pseudo_random_hash)
        # fname order: file name order
        self.fnameOrderSet = FastOrderedSet(key_func=lambda p: DescendingFilename(p.name))

        self.pseudo_random_seed = uuid.UUID(int=random.getrandbits(128))

        self.verticalOrderSet = self.mtimeOrderSet
        self.horizontalOrderSet = self.randomOrderSet

        # Store the current image render source and the current file path.
        self.currentPixmap = None
        self.currentPath = None

        # The current scale factor.
        self.scaleFactor = 1.0

        # Enable drag & drop.
        self.setAcceptDrops(True)

        # Create toolbar actions.
        self.createActions()
        self.createToolbar()

        # Create keyboard shortcuts for copying with Command+1 ... Command+9.
        self.copyShortcuts = {}
        for i in range(1, 10):
            # On macOS, "Meta" represents the Command key.
            # sc = QShortcut(QKeySequence(f"Meta+{i}"), self)
            # I’m not sure why, but it works as intended when I change Meta to Ctrl.
            sc = QShortcut(QKeySequence(f"Ctrl+{i}"), self)
            sc.activated.connect(lambda i=i: self.copyToDestination(i))
            self.copyShortcuts[i] = sc

        # Create keyboard shortcuts for moving with Ctrl+Shift+1 ... Ctrl+Shift+9.
        self.moveShortcuts = {}
        for i in range(1, 10):
            sc = QShortcut(QKeySequence(f"Shift+{i}"), self)
            sc.activated.connect(lambda i=i: self.moveToDestination(i))
            self.moveShortcuts[i] = sc

        # Variables for dragging
        self.dragging = False
        self.drag_start_position = QPoint()

        self.last_display_datetime = datetime.now()

        # Accumulated scroll values
        self.scrollAccumulationX = 0
        self.scrollAccumulationY = 0

        # Vertical gauge (left edge)
        self.verticalGauge = VerticalGauge(self)
        self.verticalGauge.setGeometry(0, 0, 10, 200)

        # Horizontal gauge (top edge)
        self.horizontalGauge = HorizontalGauge(self)
        self.horizontalGauge.setGeometry(0, 0, 200, 10)

        # Create a timer for decay (reduces values every 50ms)
        self.decayTimer = QTimer(self)
        self.decayTimer.timeout.connect(self.decayScrollValues)
        self.decayTimer.start(50)  # Called every 50ms

        self.lazyLoadingInProgress = False
        self.watcher = QFileSystemWatcher()
        self.watcher.directoryChanged.connect(self.on_directory_changed)
        # Polling fallback for network shares (e.g., Samba) to detect renames
        self._watched_folder = None
        self._last_dir_snapshot = None
        self._first_poll_after_scan = False
        self._directory_poll_timer = QTimer(self)
        self._directory_poll_timer.timeout.connect(self._poll_directory_changes)
        self._empty_poll_count = 0
        self._empty_poll_threshold = (
            30  # number of consecutive empty polls before stopping
        )
        # Require an explicit reload when changes overlap a scan.
        self._folder_changed_during_scan = False
        self._reload_was_required = False
        self.selected_file_path = None
        self._scan_seen_paths = set()
        self._scan_refresh_path = None
        self._image_candidates = deque()
        self._candidate_paths = set()
        self._candidate_retry_pending = False

        self.counter = RecentCounter()

    def remove(self, imageData: ImageData):
        if len(self.mtimeOrderSet) == 0:
            return

        self.mtimeOrderSet.remove(imageData)
        self.randomOrderSet.remove(imageData)
        self.fnameOrderSet.remove(imageData)

        self.label.setText(f"count: {len(self.mtimeOrderSet)}")

        if len(self.mtimeOrderSet) == 0 or self.currentPath == imageData.path_nf:
            self.currentPixmap = None
            self.currentPath = None
            if self.metadataDock.isVisible():
                self.metadataPanel.setPlaceholder("Image metadata will appear here.")

    def createMenus(self):
        """
        Create the menu bar and add the "File" menu with actions:
          - Open Folder: Opens a folder dialog to load images.
          - Open File: Opens a file dialog to select an image file.
          - Reveal in Finder: Reveals the current file in Finder (macOS).
          - Copy to ...: Shows the copy destination dock.
        """
        menubar = self.menuBar()
        fileMenu = menubar.addMenu("File")

        openFolderAction = QAction("Open Folder", self)
        openFolderAction.triggered.connect(self.openFolder)
        fileMenu.addAction(openFolderAction)

        openFileAction = QAction("Open File", self)
        openFileAction.triggered.connect(self.openFile)
        fileMenu.addAction(openFileAction)

        if self.os_type == OSType.MACOS:
            revealAction = QAction("Reveal in Finder", self)
            revealAction.triggered.connect(self.revealInFinder)
            fileMenu.addAction(revealAction)

        copyToAction = QAction("Copy to ...", self)
        copyToAction.triggered.connect(self.showCopyDock)
        fileMenu.addAction(copyToAction)

        moveToAction = QAction("Move to ...", self)
        moveToAction.triggered.connect(self.showMoveDock)
        fileMenu.addAction(moveToAction)

        metadataAction = QAction("Info", self)
        metadataAction.triggered.connect(self.showMetadataDock)
        fileMenu.addAction(metadataAction)

    def openFile(self):
        """
        Open a file dialog to select an image file, load its folder,
        and display the selected image.
        """
        # Build a file filter from supported image formats.
        supportedFormats = supportedImageFormats()
        extensions = " ".join(["*." + fmt for fmt in supportedFormats])
        fileFilter = f"Images ({extensions})"
        filePath, _ = QFileDialog.getOpenFileName(
            self, "Open File", os.getcwd(), fileFilter
        )
        if filePath:
            self.loadImagesFromFolder(filePath)

    def revealInFinder(self):
        """
        Reveal the currently displayed file in Finder (macOS).
        """
        if self.currentPath:
            try:
                subprocess.call(["open", "-R", self.currentPath])
            except Exception as e:
                print("Error revealing file in Finder:", e)

    def showCopyDock(self):
        """
        Show the copy destination dock widget.
        """
        self.copyDock.show()

    def showMoveDock(self):
        """Show the move destination dock widget."""
        self.moveDock.show()

    def showMetadataDock(self):
        """Show the metadata dock widget."""
        if self.metadataDock.isVisible():
            self.refreshMetadataForCurrentImage()
        else:
            # visibilityChanged refreshes metadata when the dock opens.
            self.metadataDock.show()

    def onMetadataDockVisibilityChanged(self, visible):
        QTimer.singleShot(0, self.adjustImageScale)
        if visible:
            self.refreshMetadataForCurrentImage()
        else:
            self.metadataPanel.setPlaceholder("Image metadata will appear here.")

    def refreshMetadataForCurrentImage(self):
        if not self.metadataDock.isVisible():
            return
        if not self.currentPath:
            self.metadataPanel.setPlaceholder("Image metadata will appear here.")
            return
        current_image = self.verticalOrderSet.get(self.currentPath)
        if current_image is not None:
            self.loadImageFromFile(current_image)

    def get_order_name(self, order):
        """Return the order type as a string based on the list object"""
        if order is self.mtimeOrderSet:
            return "mtime"
        elif order is self.fnameOrderSet:
            return "fname"
        elif order is self.randomOrderSet:
            return "random"
        return "mtime"  # Default in case of an unexpected value

    def get_order_by_name(self, name, default=None):
        """Return the corresponding order list based on the given string"""
        if name == "mtime":
            return self.mtimeOrderSet
        elif name == "fname":
            return self.fnameOrderSet
        elif name == "random":
            return self.randomOrderSet
        return default if default is not None else self.mtimeOrderSet

    def toggled_order(self, current, other):
        """
        The `current` and `other` parameters should be one of "mtime", "random", or "fname".
        Determines and returns the next order to switch to based on the given two values.
        """
        if current == "mtime":
            # If horizontal is "random", return "fname"; otherwise, return "random"
            return "fname" if other == "random" else "random"
        elif current == "random":
            # If horizontal is "fname", return "mtime"; otherwise, return "fname"
            return "mtime" if other == "fname" else "fname"
        elif current == "fname":
            # If horizontal is "mtime", return "random"; otherwise, return "mtime"
            return "random" if other == "mtime" else "mtime"
        return "mtime"

    def onVScrollClicked(self):
        if self.verticalOrderSet:
            # Get the current vertical and horizontal order as strings
            current = self.get_order_name(self.verticalOrderSet)
            other = self.get_order_name(self.horizontalOrderSet)
            # Determine the next order to set
            new_order_name = self.toggled_order(current, other)
            self.verticalOrderSet = self.get_order_by_name(new_order_name)
            self.VScroll.setText("VScroll: " + new_order_name)

    def onHScrollClicked(self):
        if self.horizontalOrderSet:
            # Get the current horizontal and vertical order as strings
            current = self.get_order_name(self.horizontalOrderSet)
            other = self.get_order_name(self.verticalOrderSet)
            # Determine the next order to set
            new_order_name = self.toggled_order(current, other)
            self.horizontalOrderSet = self.get_order_by_name(new_order_name)
            self.HScroll.setText("HScroll: " + new_order_name)

    def createActions(self):
        """
        Create actions for opening a folder, zooming in, zooming out, and resetting the image size.
        """

        self.refreshFolder = QAction("Reload Current Folder", self)
        self.refreshFolder.triggered.connect(self.reloadCurrentFolder)

        self.copyToAct = QAction("Copy to ...", self)
        # self.copyToAct.setShortcut(QKeySequence("Meta+Ctrl+C"))
        self.copyToAct.triggered.connect(self.showCopyDock)

        self.moveToAct = QAction("Move to ...", self)
        self.moveToAct.triggered.connect(self.showMoveDock)

        self.metadataAct = QAction("Info", self)
        self.metadataAct.triggered.connect(self.showMetadataDock)

        # self.zoomInAct = QAction("Zoom In", self)
        # self.zoomInAct.setShortcut(QKeySequence.ZoomIn)
        # self.zoomInAct.triggered.connect(self.zoomIn)

        # self.zoomOutAct = QAction("Zoom Out", self)
        # self.zoomOutAct.setShortcut(QKeySequence.ZoomOut)
        # self.zoomOutAct.triggered.connect(self.zoomOut)

        self.normalSizeAct = QAction("Normal Size", self)
        self.normalSizeAct.triggered.connect(self.normalSize)

        self.VScroll = QToolButton()
        self.VScroll.setText("VScroll: mtime")
        self.VScroll.clicked.connect(self.onVScrollClicked)

        self.HScroll = QToolButton()
        self.HScroll.setText("HScroll: random")
        self.HScroll.clicked.connect(self.onHScrollClicked)

        self.loopScroll = QToolButton()
        self.loopScroll.setText("Loop")
        self.loopScroll.setCheckable(True)
        self.loopScroll.setChecked(False)

        self.freeScroll = QToolButton()
        self.freeScroll.setText("Free Scroll")
        self.freeScroll.setCheckable(True)
        self.freeScroll.setChecked(False)

        font = QFont("Courier New")
        font.setStyleHint(QFont.Monospace)

        # Watch status indicator
        self.watchStatusLabel = QLabel("Watch: --")
        self.watchStatusLabel.setFixedWidth(80)
        self.watchStatusLabel.setFont(font)

        # --- Add Poll toggle toolbutton ---
        self.pollToggle = QToolButton()
        self.pollToggle.setText("Poll")
        self.pollToggle.setCheckable(True)
        self.pollToggle.setChecked(False)
        self.pollToggle.toggled.connect(self.handlePollToggled)

        self.count_label = QLabel("")
        self.count_label.setFixedWidth(68)
        self.count_label.setFont(font)

        self.label = QLabel("count: ")
        self.label.setFixedWidth(200)
        self.label.setFont(font)

    def createToolbar(self):
        """
        Create a toolbar and add the actions for opening a folder and zooming.
        """
        toolbar = QToolBar("Toolbar")
        self.addToolBar(toolbar)
        toolbar.addAction(self.refreshFolder)
        toolbar.addAction(self.copyToAct)
        toolbar.addAction(self.moveToAct)
        toolbar.addAction(self.metadataAct)
        # toolbar.addAction(self.zoomInAct)
        # toolbar.addAction(self.zoomOutAct)
        toolbar.addAction(self.normalSizeAct)
        toolbar.addWidget(self.VScroll)
        toolbar.addWidget(self.HScroll)
        toolbar.addWidget(self.loopScroll)
        toolbar.addWidget(self.freeScroll)
        toolbar.addWidget(self.watchStatusLabel)
        toolbar.addWidget(self.pollToggle)

        count_label_action = QWidgetAction(toolbar)
        count_label_action.setDefaultWidget(self.count_label)
        toolbar.addAction(count_label_action)

        label_action = QWidgetAction(toolbar)
        label_action.setDefaultWidget(self.label)
        toolbar.addAction(label_action)

    def updateWatchStatusLabel(self):
        """Update the watchStatusLabel to reflect Watch/Polling status."""
        if self._folder_changed_during_scan and not self.lazyLoadingInProgress:
            self.watchStatusLabel.setText("Reload Current Folder")
            return
        watch_on = any(self.watcher.directories())
        poll_on = self._directory_poll_timer.isActive()
        if watch_on and poll_on:
            status = "wp"
        elif watch_on:
            status = "w-"
        elif poll_on:
            status = "-p"
        else:
            status = "--"
        self.watchStatusLabel.setText(f"Watch: {status}")

    def handlePollToggled(self, checked):
        """Start or stop the directory polling timer based on the toggle state."""
        if checked and not self.lazyLoadingInProgress and not self._folder_changed_during_scan:
            if not self._directory_poll_timer.isActive():
                self._directory_poll_timer.start(2000)
        else:
            self._directory_poll_timer.stop()
        self.updateWatchStatusLabel()

    def openFolder(self):
        """
        Open a folder dialog and load images from the selected directory.
        """
        folder = QFileDialog.getExistingDirectory(self, "Open Folder", os.getcwd())
        if folder:
            self.loadImagesFromFolder(folder)

    def reloadCurrentFolder(self):
        """
        Reload the current folder and refresh the displayed images.
        """
        if self._watched_folder:
            return self.loadImagesFromFolder(self._watched_folder)

    def loadImagesFromFolder(self, path, refresh_random_seed=False):
        """
        Scan the folder and reconcile its navigation orders incrementally.

        :param path: The path to the folder or file to load images from.
        """
        if self.lazyLoadingInProgress:
            return

        self._scan_start_time = time.monotonic()
        path = os.path.abspath(path)

        if os.path.isfile(path):
            filePath = path
            folderPath = os.path.dirname(path)
        elif os.path.isdir(path):
            dir_path = Path(path)
            ext = dir_path.suffix[1:]
            if ext in image_format_extractors.keys():
                filePath = path
                folderPath = dir_path.parent.as_posix()
            else:
                filePath = None
                folderPath = path
        else:
            return
            # raise ValueError("The specified path is neither a file nor a directory.")

        same_folder = self._watched_folder == folderPath
        self._scan_refresh_path = self.currentPath if same_folder and not filePath else None
        self._watched_folder = folderPath
        self._scan_seen_paths.clear()
        self.resetImageCandidates()
        self.selected_file_path = (
            unicodedata.normalize("NFD", filePath) if filePath else None
        )
        if not same_folder:
            self._last_dir_snapshot = None
        self._first_poll_after_scan = False

        self.lazyLoadingInProgress = True
        self._reload_was_required = same_folder and self._folder_changed_during_scan
        self._folder_changed_during_scan = False
        if not same_folder:
            watched_paths = self.watcher.directories()
            if watched_paths:
                self.watcher.removePaths(watched_paths)
        self._directory_poll_timer.stop()
        self.updateWatchStatusLabel()

        if not same_folder:
            self.fnameOrderSet.clear()
            self.mtimeOrderSet.clear()
            self.randomOrderSet.clear()
            self.currentPath = None
            self.currentPixmap = None
            self.imageDisplay.clearData()
            self.refreshMetadataForCurrentImage()

        self.statusBar().showMessage("loading...", 2000)

        if refresh_random_seed:
            self.pseudo_random_seed = uuid.UUID(int=random.getrandbits(128))
        self.imageLoader = ImageLoaderWorker(
            folderPath, self.pseudo_random_seed, filePath
        )
        self.imageLoader.imageLoaded.connect(self.handleNewImage)
        self.imageLoader.finishedLoading.connect(self.finishLoadingImages)
        self.imageLoader.start()

    def handleNewImage(self, imageDataListJson, from_scan):
        orders = (self.fnameOrderSet, self.mtimeOrderSet, self.randomOrderSet)
        records = [ImageData(**item) for item in json.loads(imageDataListJson)]
        if from_scan:
            self._scan_seen_paths.update(record.path_nf for record in records)
        changes = [
            record for record in records
            if self.mtimeOrderSet.get(record.path_nf) != record
        ]
        if changes:
            for order in orders:
                order.upsert_batch(changes)

        if self.selected_file_path is not None:
            for record in records:
                if record.path_nf == self.selected_file_path:
                    self.selected_file_path = None
                    if self.loadImageFromFile(record) is None:
                        self._candidate_paths.add(record.path_nf)
                        self.statusBar().showMessage(f"Unable to open {record.name}", 3000)
                    break

        if self.currentPath is None and self.selected_file_path is None:
            self.queueImageCandidates(records)

        self.label.setText(
            f"count: {len(self.mtimeOrderSet)} ... ({self.imageLoader.batch_count_threshold})"
        )

    def resetImageCandidates(self):
        self._image_candidates = deque()
        self._candidate_paths = set()
        self._candidate_retry_pending = False

    def queueImageCandidates(self, records):
        candidates = self._image_candidates
        for record in records:
            path = record.path_nf
            if path not in self._candidate_paths:
                self._candidate_paths.add(path)
                candidates.append(path)
        if self._candidate_retry_pending:
            return

        def try_next():
            if self._image_candidates is not candidates:
                return
            self._candidate_retry_pending = False
            if self.currentPath is not None:
                self._candidate_paths.difference_update(candidates)
                candidates.clear()
            elif candidates:
                path = candidates.popleft()
                record = self.mtimeOrderSet.get(path)
                if record is None:
                    self._candidate_paths.discard(path)
                if record is not None and self.loadImageFromFile(record) is not None:
                    self._candidate_paths.difference_update(candidates)
                    candidates.clear()
                elif candidates:
                    self._candidate_retry_pending = True
                    QTimer.singleShot(0, self, try_next)

        try_next()

    def showFirstReadableImage(self, skip_path):
        self.queueImageCandidates(
            record for record in self.mtimeOrderSet if record.path_nf != skip_path
        )

    def finishLoadingImages(self, scan_succeeded, candidate_names):
        if scan_succeeded:
            self._last_dir_snapshot = candidate_names
        elif self._reload_was_required:
            self._folder_changed_during_scan = True
        self._reload_was_required = False
        self._first_poll_after_scan = self._last_dir_snapshot is not None

        failed_path = None
        if scan_succeeded:
            missing = {
                record.path_nf for record in self.mtimeOrderSet
                if record.path_nf not in self._scan_seen_paths
            }
            if missing:
                for order in (self.mtimeOrderSet, self.fnameOrderSet, self.randomOrderSet):
                    order.discard_paths(missing)

            if self.currentPath in missing:
                self.currentPath = None
            elif self.currentPath and self.currentPath == self._scan_refresh_path:
                record = self.mtimeOrderSet.get(self.currentPath)
                if record is not None and self.loadImageFromFile(record) is None:
                    failed_path = self.currentPath
                    self.currentPath = None

        if self.currentPath is None:
            self.currentPixmap = None
            self.refreshMetadataForCurrentImage()
            self.imageDisplay.clearData()
            self.setWindowTitle("Yee3")
            self.showFirstReadableImage(failed_path)

        self.selected_file_path = None
        self._scan_refresh_path = None
        self._scan_seen_paths.clear()
        elapsed_time = time.monotonic() - self._scan_start_time
        count = len(self.mtimeOrderSet)

        message = "Complete" if scan_succeeded else "Unable to finish folder scan"
        if self._folder_changed_during_scan:
            if scan_succeeded:
                message = "Folder changed during scan"
            self.statusBar().showMessage(f"{message}. Use Reload Current Folder.")
        else:
            self.statusBar().showMessage(message, 3000)
        self.label.setText(f"count: {count}")

        self.lazyLoadingInProgress = False
        if self._watched_folder:
            if self._watched_folder not in self.watcher.directories() and (
                count < 100 or count / elapsed_time > 1000
            ):
                self.watcher.addPath(self._watched_folder)
            # Start polling after the folder scan is complete
            if (
                not self._directory_poll_timer.isActive()
                and self.pollToggle.isChecked()
                and not self._folder_changed_during_scan
            ):
                self._empty_poll_count = 0
                self._directory_poll_timer.start(2000)  # poll every 2 seconds
            self.updateWatchStatusLabel()

    def on_directory_changed(self, path):
        if path != self._watched_folder:
            return
        if self.lazyLoadingInProgress:
            self._folder_changed_during_scan = True
            return
        if self._folder_changed_during_scan:
            return
        if not self._directory_poll_timer.isActive() and self.pollToggle.isChecked():
            self._empty_poll_count = 0
            self._directory_poll_timer.start(2000)  # poll every 2 seconds
        self.updateWatchStatusLabel()
        self.reloadCurrentFolder()

    def _poll_directory_changes(self):
        """Poll directory entries to detect renames on network shares."""
        # self.statusBar().showMessage("checking", 1000)
        folder = self._watched_folder
        if not folder or self.lazyLoadingInProgress or self._folder_changed_during_scan:
            return
        try:
            names = os.listdir(folder)
        except OSError:
            return
        extensions = set(supportedImageFormats())
        current = {
            unicodedata.normalize("NFD", name)
            for name in names
            if has_image_extension(name, extensions)
        }

        previous = self._last_dir_snapshot
        first_poll_after_scan = self._first_poll_after_scan
        self._first_poll_after_scan = False
        if previous is None:
            self._last_dir_snapshot = current
            return
        if current != previous:
            self._empty_poll_count = 0
            if first_poll_after_scan:
                self._folder_changed_during_scan = True
                self._directory_poll_timer.stop()
                self.updateWatchStatusLabel()
            else:
                self.on_directory_changed(folder)
        else:
            self._empty_poll_count += 1
            if self._empty_poll_count >= self._empty_poll_threshold:
                self._directory_poll_timer.stop()
                self._empty_poll_count = 0
                self.updateWatchStatusLabel()
                self.statusBar().showMessage("Polling stopped", 1000)

    def loadImageFromFile(self, imageData: ImageData):
        """
        Load and display the image represented by the given ImageData.

        :param imageData: Metadata for the image to display.
        """
        filePath = imageData.path_nf
        currentPath = unicodedata.normalize("NFD", filePath)
        ext = Path(filePath).suffix[1:]
        should_read_metadata = self.metadataDock.isVisible()
        image_bytes = None
        metadata_read_error = None

        # Preserve streaming image reads for formats the metadata parser
        # cannot handle, especially large animations on network storage.
        if (
            should_read_metadata
            and supports_image_metadata(filePath)
            and ext not in image_format_extractors.keys()
        ):
            try:
                with open(filePath, "rb") as f:
                    image_bytes = f.read()
            except Exception as e:
                metadata_read_error = str(e)

        if ext in image_format_extractors.keys():
            try:
                image_data = image_format_extractors[ext](filePath)
            except Exception as e:
                print("Error loading image:", e, filePath)
                return None
            if image_data:
                if should_read_metadata:
                    image_bytes = image_data
                image = QPixmap()
                image.loadFromData(QByteArray(image_data))
            else:
                return None
        else:
            if image_bytes is not None:
                image = QPixmap()
                encoded_image = QByteArray(image_bytes)
                if not image.loadFromData(encoded_image):
                    # Formats such as TGA need an extension hint. Try the
                    # same bytes again before reopening high-latency files.
                    if not image.loadFromData(encoded_image, ext.lower()):
                        image = QPixmap(filePath)
            else:
                image = QPixmap(filePath)
        if image.isNull():
            return None
        else:
            self.setWindowTitle(f"Yee3 - {os.path.basename(filePath)}")
            self.currentPath = currentPath
            self.currentPixmap = image
            self.imageDisplay.setData(
                self.currentPixmap,
                QMovie(filePath) if ext in image_format_animated else None,
            )
            self.adjustImageScale()
            if self.metadataDock.isVisible():
                if metadata_read_error is not None:
                    self.metadataPanel.setError(currentPath, ext or "unknown", metadata_read_error)
                elif image_bytes is None:
                    self.metadataPanel.setPlaceholder("Image metadata is unavailable for this image.")
                else:
                    metadata = parse_image_metadata_bytes(currentPath, image_bytes)
                    if metadata.error:
                        self.metadataPanel.setError(
                            metadata.path, metadata.format, metadata.error
                        )
                    else:
                        self.metadataPanel.setResult(metadata)
            count = self.counter.count()
            self.count_label.setText(f"{count:>3} ips")
            return image

    def adjustImageScale(self):
        """
        Adjust the image scale so that it fits optimally in the available central area.
        """
        if self.currentPixmap:
            availableWidth = self.scrollArea.viewport().width()
            availableHeight = self.scrollArea.viewport().height()
            imageSize = self.currentPixmap.size()
            # Calculate the optimal scale for the screen
            self.fittedScale = min(
                availableWidth / imageSize.width(), availableHeight / imageSize.height()
            )
            # Adjust the current scale to the optimal size
            self.scaleFactor = self.fittedScale
            newSize = imageSize * self.scaleFactor
            scaledPixmap = self.currentPixmap.scaled(
                newSize, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.imageDisplay.setPixmap(scaledPixmap)
            self.imageDisplay.setScaleFactor(self.scaleFactor)
            self.imageDisplay.resize(scaledPixmap.size())

    def navigateImage(self, order, *, previous):
        current_path = self.currentPath
        if current_path is None or order.get(current_path) is None:
            return False

        # The chronological orders are stored newest first. Up and left
        # advance through the stored order, toward older images.
        neighbor = order.next_path if previous else order.prev_path
        loop = self.loopScroll.isChecked()
        while (path := neighbor(current_path, loop=loop)) not in (None, current_path):
            record = order.get(path)
            if self.loadImageFromFile(record) is not None:
                return True
            self.remove(record)
        return False

    def verticalPreviousImage(self):
        return self.navigateImage(self.verticalOrderSet, previous=True)

    def verticalNextImage(self):
        return self.navigateImage(self.verticalOrderSet, previous=False)

    def horizontalPreviousImage(self):
        return self.navigateImage(self.horizontalOrderSet, previous=True)

    def horizontalNextImage(self):
        return self.navigateImage(self.horizontalOrderSet, previous=False)

    def keyPressEvent(self, event):
        """
        Handle key press events:
          - Up/Down keys: navigate using the currently assigned vertical order.
          - Left/Right keys: navigate using the currently assigned horizontal order.
          - Plus/Equal keys: zoom in.
          - Minus key: zoom out.
        """
        key = event.key()
        if key == Qt.Key_Up:
            self.verticalPreviousImage()
        elif key == Qt.Key_Down:
            self.verticalNextImage()
        elif key == Qt.Key_Left:
            self.horizontalPreviousImage()
        elif key == Qt.Key_Right:
            self.horizontalNextImage()
        elif key in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoomIn()
        elif key == Qt.Key_Minus:
            self.zoomOut()
        else:
            super().keyPressEvent(event)

    def wheelEvent(self, event):
        """Pan a zoomed image or navigate along the dominant scroll axis."""
        delta = event.angleDelta()
        if delta.isNull():
            delta = event.pixelDelta()
        if delta.isNull():
            event.ignore()
            return

        vertical = abs(delta.y()) >= abs(delta.x())
        if vertical:
            amount = delta.y()
            accumulation = self.scrollAccumulationY
            scrollbar = self.scrollArea.verticalScrollBar()
            order = self.verticalOrderSet
        else:
            amount = delta.x()
            accumulation = self.scrollAccumulationX
            scrollbar = self.scrollArea.horizontalScrollBar()
            order = self.horizontalOrderSet
        if scrollbar.minimum() != scrollbar.maximum():
            event.ignore()
            return

        mode = "free" if self.freeScroll.isChecked() else "limit"
        settings = scroll_factors_dict[mode]
        factors = settings["vertical" if vertical else "horizontal"]
        if amount * accumulation < 0:
            accumulation = 0
        limit = factors["max"]
        accumulation = max(-limit, min(limit, accumulation + amount * factors["scroll"]))

        now = datetime.now()
        elapsed = (now - self.last_display_datetime).total_seconds()
        if abs(accumulation) >= factors["threshold"] and elapsed > settings["interval"]:
            self.navigateImage(order, previous=accumulation > 0)
            accumulation /= factors["release"]
            self.last_display_datetime = now

        if vertical:
            self.scrollAccumulationY = accumulation
            self.verticalGauge.updateGauge(accumulation)
        else:
            self.scrollAccumulationX = accumulation
            self.horizontalGauge.updateGauge(accumulation)
        if not self.decayTimer.isActive():
            self.decayTimer.start()
        event.accept()

    def decayScrollValues(self):
        """Reduce and update the gauge display"""

        if self.freeScroll.isChecked():
            scroll_factors = scroll_factors_dict["free"]
        else:
            scroll_factors = scroll_factors_dict["limit"]

        threshold = 1  # Threshold (set to zero when below this value)

        # Vertical scroll decay
        self.scrollAccumulationY *= scroll_factors["vertical"]["decay"]
        if abs(self.scrollAccumulationY) < threshold:
            self.scrollAccumulationY = 0

        # Horizontal scroll decay
        self.scrollAccumulationX *= scroll_factors["horizontal"]["decay"]
        if abs(self.scrollAccumulationX) < threshold:
            self.scrollAccumulationX = 0

        # Update the gauge
        self.verticalGauge.updateGauge(self.scrollAccumulationY)
        self.horizontalGauge.updateGauge(self.scrollAccumulationX)

        # Stop the timer if both values reach zero
        if self.scrollAccumulationX == 0 and self.scrollAccumulationY == 0:
            self.decayTimer.stop()

    def zoomIn(self):
        """
        Zoom in on the image by increasing the scale factor.
        """
        self.scaleImage(1.25)

    def zoomOut(self):
        """
        Zoom out of the image by decreasing the scale factor.
        """
        self.scaleImage(0.8)

    def normalSize(self):
        """
        Reset the image to its optimal size.
        """
        self.adjustImageScale()

    def scaleImage(self, factor):
        """
        Scale the image by the given factor relative to the current scale.

        :param factor: Multiplicative factor to adjust the scale.
        """
        if self.currentPixmap:
            self.scaleFactor *= factor
            newSize = self.currentPixmap.size() * self.scaleFactor
            scaledPixmap = self.currentPixmap.scaled(
                newSize, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.imageDisplay.setPixmap(scaledPixmap)
            self.imageDisplay.setScaleFactor(self.scaleFactor)
            self.imageDisplay.resize(scaledPixmap.size())

    def contextMenuEvent(self, event):
        """
        Display a context menu on right-click with an option to reveal the current file in Finder.
        """
        menu = QMenu(self)
        actions = {}
        if self.os_type == OSType.MACOS:
            actions[menu.addAction("Reveal in Finder")] = "reveal"
        actions[menu.addAction("Info")] = "info"
        action = menu.exec_(event.globalPos())
        if action in actions:
            if actions[action] == "reveal":
                if self.currentPath is not None:
                    try:
                        subprocess.call(["open", "-R", self.currentPath])
                    except Exception as e:
                        print("Error revealing file in Finder:", e)
            elif actions[action] == "info":
                self.showMetadataDock()

    def dragEnterEvent(self, event):
        """
        Accept drag events that contain file URLs.
        """
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        """
        Handle drop events: load the first dropped image file and its folder.
        """
        for url in event.mimeData().urls():
            filePath = url.toLocalFile()
            if os.path.isfile(filePath):
                imageExtensions = supportedImageFormats()
                if any(filePath.lower().endswith(ext) for ext in imageExtensions):
                    self.loadImagesFromFolder(filePath)
                    break
            elif os.path.isdir(filePath):
                self.loadImagesFromFolder(filePath)
                break

    def resizeEvent(self, event):
        """
        Adjust the image scale when the window is resized.
        """
        super().resizeEvent(event)
        self.adjustImageScale()

    def closeEvent(self, event):
        """
        Save window settings and copy destination assignments when the application is closed.
        """
        self.saveSettings()
        super().closeEvent(event)

    def loadSettings(self):
        """
        Load the window size, position, and copy destination assignments from a configuration file.
        """
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "yee3_config.json"
        )
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    config = json.load(f)
                if "window_width" in config and "window_height" in config:
                    self.resize(config["window_width"], config["window_height"])
                if "window_x" in config and "window_y" in config:
                    self.move(config["window_x"], config["window_y"])
                if "copy_destinations" in config:
                    self.copyDestinations = config["copy_destinations"]
                else:
                    self.copyDestinations = {}
                if "move_destinations" in config:
                    self.moveDestinations = config["move_destinations"]
                else:
                    self.moveDestinations = {}
            except Exception as e:
                print("Error loading settings:", e)
        else:
            self.copyDestinations = {}
            self.moveDestinations = {}

    def saveSettings(self):
        """
        Save the window size, position, and copy destination assignments to a configuration file.
        """
        config = {
            "window_width": self.width(),
            "window_height": self.height(),
            "window_x": self.x(),
            "window_y": self.y(),
            "copy_destinations": self.copyDestinations,
            "move_destinations": self.moveDestinations,
        }
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "yee3_config.json"
        )
        try:
            with open(config_path, "w") as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print("Error saving settings:", e)

    def updateCopyList(self):
        """
        Update the copy destination list in the dock widget to reflect current assignments.
        """
        self.copyList.clear()
        for i in range(1, 10):
            dest = self.copyDestinations.get(str(i)) or self.copyDestinations.get(i)
            if dest:
                text = f"Cmd+{i}: {dest}"
            else:
                text = f"Cmd+{i}: (not set)"
            self.copyList.addItem(text)

    def updateMoveList(self):
        """Update the move destination list in the dock widget."""
        self.moveList.clear()
        for i in range(1, 10):
            dest = self.moveDestinations.get(str(i)) or self.moveDestinations.get(i)
            if dest:
                text = f"Shift+{i}: {dest}"
            else:
                text = f"Shift+{i}: (not set)"
            self.moveList.addItem(text)

    def destinationFor(self, index, *, move):
        destinations = self.moveDestinations if move else self.copyDestinations
        return destinations.get(str(index)) or destinations.get(index)

    def selectDestination(self, index, *, move):
        shortcut = f"Shift+{index}" if move else f"Cmd+{index}"
        folder = QFileDialog.getExistingDirectory(self, f"Select destination for {shortcut}")
        if folder:
            destinations = self.moveDestinations if move else self.copyDestinations
            destinations[str(index)] = folder
            (self.updateMoveList if move else self.updateCopyList)()
        return folder

    def showDestinationMenu(self, pos, *, move):
        widget = self.moveList if move else self.copyList
        item = widget.itemAt(pos)
        if item is None:
            return
        index = widget.row(item) + 1
        if not self.destinationFor(index, move=move):
            return

        menu = QMenu()
        clear = menu.addAction("Clear Destination")
        if menu.exec(widget.viewport().mapToGlobal(pos)) == clear:
            destinations = self.moveDestinations if move else self.copyDestinations
            destinations.pop(str(index), None)
            destinations.pop(index, None)
            (self.updateMoveList if move else self.updateCopyList)()

    def transferToDestination(self, index, move=False):
        """
        Transfer the current image file to the destination folder associated with the given index.
        If no destination is set for that index, prompt the user to select one.

        :param index: The index (1-9) corresponding to the Transfer destination.
        """
        label = "Move" if move else "Copy"
        label_result = "Moved" if move else "Copied"
        transfer_function = shutil.move if move else shutil.copy2
        dest = self.destinationFor(index, move=move) or self.selectDestination(index, move=move)
        if not dest:
            return False

        if self.currentPath:
            fileName = os.path.basename(self.currentPath)
            targetPath = os.path.join(dest, fileName)
            if os.path.exists(targetPath):
                # If an existing file is an image, generate a thumbnail
                pixmap = QPixmap(targetPath)
                if not pixmap.isNull():
                    # For example, scale to 400×400
                    scaled_pixmap = pixmap.scaled(
                        400, 400, Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
                else:
                    scaled_pixmap = None

                # Display a custom dialog
                dialog = ReplaceDialog(targetPath, scaled_pixmap, self)
                result = dialog.exec()

                if result == ReplaceDialogResult.REPLACE:
                    # Replace Transfer
                    try:
                        transfer_function(self.currentPath, targetPath)
                        self.statusBar().showMessage(f"Replaced file at {dest}", 3000)
                        return True
                    except Exception as e:
                        self.statusBar().showMessage(f"{label} failed: {e}", 3000)
                        return False
                elif result == ReplaceDialogResult.RENAME:
                    # Transfer with a new name
                    try:
                        transfer_with_unique_name(self.currentPath, dest, move)
                        self.statusBar().showMessage(
                            f"{label_result} to {dest} with a new name", 3000
                        )
                        return True
                    except Exception as e:
                        self.statusBar().showMessage(f"{label} failed: {e}", 3000)
                        return False
                elif result == ReplaceDialogResult.CANCEL:
                    # Cancel
                    self.statusBar().showMessage(f"{label} canceled", 3000)
                    return False
            else:
                # If there is no file with the same name, perform a normal copy or move
                try:
                    transfer_function(self.currentPath, targetPath)
                    self.statusBar().showMessage(f"{label_result} to {dest}", 3000)
                    return True
                except Exception as e:
                    self.statusBar().showMessage(f"{label} failed: {e}", 3000)
                    return False
        else:
            print("No current file available.")
        return False

    def copyToDestination(self, index):
        """
        Copy the current image file to the copy destination folder for the given index.
        """
        return self.transferToDestination(index, move=False)

    def moveToDestination(self, index):
        """
        Move the current image file to the move destination folder for the given index.
        """
        current_image = self.verticalOrderSet.get(self.currentPath)
        if not self.transferToDestination(index, move=True):
            return False

        if current_image is not None:
            # Use the same direction and loop behavior as Down, then try Up
            # if there is no readable image in the preferred direction.
            if not self.verticalNextImage():
                self.verticalPreviousImage()
            self.remove(current_image)
            if self.currentPath is None:
                self.imageDisplay.clearData()
                self.setWindowTitle("Yee3")
        return True

    def onCopyListDoubleClicked(self, item):
        index = self.copyList.row(item) + 1
        if self.destinationFor(index, move=False):
            self.copyToDestination(index)
        else:
            self.selectDestination(index, move=False)

    def onMoveListDoubleClicked(self, item):
        index = self.moveList.row(item) + 1
        if self.destinationFor(index, move=True):
            self.moveToDestination(index)
        else:
            self.selectDestination(index, move=True)

    def eventFilter(self, obj, event):
        """
        Route wheel navigation and handle double-clicks in the image area.
        On a double-click, resize the window:
          - Set height to the maximum available screen height.
          - Adjust width to maintain the aspect ratio of the currently displayed image.
        """
        if obj is self.scrollArea.viewport() and event.type() == QEvent.Wheel:
            self.wheelEvent(event)
            return event.isAccepted()

        if (
            obj == self.imageDisplay or obj == self.scrollArea
        ) and event.type() == QEvent.MouseButtonDblClick:
            screen_geometry = QApplication.primaryScreen().availableGeometry()
            screen_height = screen_geometry.height()
            window_center_x = self.geometry().center().x()

            status_bar_height = self.statusBar().height()
            adjusted_height = screen_height - status_bar_height

            if self.currentPixmap:
                image_size = self.currentPixmap.size()
                aspect_ratio = image_size.width() / image_size.height()
                new_width = int(adjusted_height * aspect_ratio)
            else:
                new_width = self.width()

            new_x = window_center_x - (new_width // 2)
            new_y = self.geometry().y()
            self.move(new_x, new_y)
            self.resize(new_width, adjusted_height)

            self.adjustImageScale()
            # self.scaleFactor = self.fittedScale

            return True

        return super().eventFilter(obj, event)

    def showEvent(self, event):
        """
        Handle the show event. On the first display of the window, adjust the image scaling.
        """
        super().showEvent(event)
        if self.firstShow:
            QTimer.singleShot(0, self.adjustImageScale)
            self.firstShow = False

    def mousePressEvent(self, event):
        """Triggered when the mouse button is pressed."""
        if event.button() == Qt.LeftButton:
            self.dragging = True
            # Calculate the offset between the mouse click and the window's top-left corner
            self.drag_start_position = (
                event.globalPos() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event):
        """Triggered when the mouse is dragged."""
        if self.dragging:
            # Move the window according to the mouse movement
            self.move(event.globalPos() - self.drag_start_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        """Triggered when the mouse button is released."""
        if event.button() == Qt.LeftButton:
            self.dragging = False
            event.accept()

    def event(self, event):
        if event.type() == QEvent.Gesture:
            return self.gestureEvent(event)
        return super().event(event)

    def gestureEvent(self, event):
        pinch = event.gesture(Qt.PinchGesture)
        if pinch:
            if pinch.changeFlags() & QPinchGesture.ScaleFactorChanged:
                self.handlePinch(pinch)
            return True
        return False

    def handlePinch(self, pinch):
        factor = pinch.scaleFactor()
        new_scale = self.scaleFactor * factor

        # Limit the maximum and minimum zoom scale
        max_zoom = self.fittedScale * 5.0  # Maximum 5x
        min_zoom = self.fittedScale * 0.2  # Minimum 20%

        if new_scale > max_zoom:
            factor = max_zoom / self.scaleFactor
        elif new_scale < min_zoom:
            factor = min_zoom / self.scaleFactor

        # Determine the pinch center point
        if pinch.centerPoint().isNull():
            # hotSpot() is already in global coordinates, so use it as is
            centerPoint = pinch.hotSpot().toPoint()
            # Convert to the coordinate system of imageDisplay
            localPos = self.imageDisplay.mapFromGlobal(centerPoint)
        else:
            centerPoint = pinch.centerPoint().toPoint()
            # If centerPoint is in global coordinates, directly convert to viewport coordinates
            localPos = self.scrollArea.viewport().mapFromGlobal(centerPoint)

        # Adjust scroll position
        hbar = self.scrollArea.horizontalScrollBar()
        vbar = self.scrollArea.verticalScrollBar()
        oldHValue = hbar.value()
        oldVValue = vbar.value()

        # Apply the zoom scale to the image
        self.scaleImage(factor)

        # Adjust scroll position after zooming
        newHValue = int(factor * (oldHValue + localPos.x()) - localPos.x())
        newVValue = int(factor * (oldVValue + localPos.y()) - localPos.y())
        hbar.setValue(newHValue)
        vbar.setValue(newVValue)


def get_os_type():
    system_name = platform.system().lower()
    if "windows" in system_name:
        return OSType.WINDOWS
    elif "linux" in system_name:
        return OSType.LINUX
    elif "darwin" in system_name:
        return OSType.MACOS
    else:
        return OSType.UNKNOWN


def initialize_image_viewer(imagePath=None, os_type: OSType = get_os_type()):
    viewer = ImageViewer(os_type)

    # If an image file is provided as a command-line argument, load its folder and display that image.
    if imagePath is not None:
        imagePath = unicodedata.normalize("NFD", imagePath)
        viewer.loadImagesFromFolder(imagePath)

    viewer.show()
    return viewer


def main():
    app = QApplication(sys.argv)
    imagePath = sys.argv[1] if len(sys.argv) > 1 else None
    viewer = initialize_image_viewer(imagePath)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
