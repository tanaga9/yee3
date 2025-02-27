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
    QThread,
    Signal,
    QFileSystemWatcher,
    QByteArray,
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


def copy_with_unique_name(src, dst_dir):
    """
    Copies a file to the specified directory.
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

    shutil.copy2(src, dst_path)
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


class SortedList:
    """A simple sorted list implementation using bisect."""

    def __init__(self):
        self._list = []

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


class FastOrderedSet:
    """
    An ordered set that mimics list behavior while ensuring unique elements.
    """

    def __init__(self, iterable=None, key_func=None):
        """Initialize an ordered set. O(N) if iterable is provided, otherwise O(1)."""
        self.items: List[ImageData] = []  # List for index-based access
        self.keys = SortedList()  # Binary search optimized
        self.index_map: Dict[str, ImageData] = {}  # Map paths to objects
        self.key_func = key_func
        if iterable:
            self.update(iterable)

    def add(self, item: ImageData):
        """Add an item while ensuring uniqueness (O(log N))"""
        if item.path_nf in self.index_map:
            return  # Duplicate, skip insertion

        if self.key_func is None:
            # Append if no custom sorting is required
            index = random.randint(0, len(self.items))
        else:
            key = self.key_func(item)
            index = self.keys.bisect_left(key)  # Get insertion index (O(log N))
            self.keys.add(key)  # Maintain sorted order (O(log N))

        self.items.insert(index, item)  # Insert at the correct position (O(N))
        self.index_map[item.path_nf] = item  # Store reference for quick lookup

    def update(self, sequence):
        """
        Add all elements from a sequence to the set. O(N).

        - Iterates through the sequence and adds new elements.
        - Duplicates are ignored.
        """
        for item in sequence:
            self.add(item)

    def remove(self, item: ImageData):
        """
        Remove the specified element from the set.
        Raises a KeyError if the element does not exist.
        """
        # Check if the element exists in index_map
        if item.path_nf not in self.index_map:
            # raise KeyError(f"'{item.path_nf}' not found in FastOrderedSet")
            return  # idempotent

        # Remove from index_map
        del self.index_map[item.path_nf]

        # Remove from the items list
        self.items.remove(item)

        # If a key function is set, remove the key from SortedList as well
        if self.key_func is not None:
            key = self.key_func(item)
            self.keys.remove(key)

    def clear(self):
        """Remove all elements from the set. O(1)."""
        self.items.clear()
        self.keys.clear()
        self.index_map.clear()

    def index(self, value: str) -> int:
        """
        Return the index of a given string value. O(1).

        - If found, returns the index.
        - If not found, raises a `ValueError`.
        """
        if value in self.index_map:
            return self.items.index(self.index_map[value])
        raise ValueError(f"'{value}' not found in FastOrderedSet")

    def __len__(self):
        """Return the number of elements. O(1)."""
        return len(self.items)

    def __getitem__(self, index: int) -> ImageData:
        """
        Retrieve:
        - String when given an integer index. O(1).
        - Slice when given a slice. O(K), where K is the slice size.
        """
        if isinstance(index, int):
            return self.items[index]  # O(1)
        raise TypeError("Index must be an integer")

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
    Positive direction (right scroll) is blue, negative direction (left scroll) is red.
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
    imageLoaded = Signal(str)
    finishedLoading = Signal()

    def __init__(self, folder, pseudo_random_seed, filePath=None, parent=None):
        super().__init__(parent)
        self.folder = unicodedata.normalize("NFD", folder)
        self.pseudo_random_seed = pseudo_random_seed
        self.filePath = os.path.abspath(filePath) if filePath is not None else None

    def run(self):
        imageExtensions = supportedImageFormats()

        is_supported_image_format = lambda name: any(
            name.lower().endswith("." + ext) for ext in imageExtensions
        )
        if self.filePath is not None:
            imagefile = ImageFile(path=self.filePath)
            if is_supported_image_format(imagefile.name):
                if imagefile.stat():
                    imageData = ImageData.generate(self.pseudo_random_seed, imagefile)
                    self.imageLoaded.emit(json.dumps([asdict(imageData)]))
        data = []
        last_emit_timestamp = datetime.now()
        try:
            for entry in os.scandir(self.folder):
                imagefile = ImageFile(entry=entry)
                if is_supported_image_format(imagefile.name):
                    if imagefile.stat():
                        imageData = ImageData.generate(
                            self.pseudo_random_seed, imagefile
                        )
                        data.append(asdict(imageData))
                now = datetime.now()
                if (
                    len(data) >= 100
                    or (now - last_emit_timestamp).total_seconds() > 0.25
                ):
                    if len(data) > 0:
                        self.imageLoaded.emit(json.dumps(data))
                        data = []
                    self.msleep(10)
                    last_emit_timestamp = now
            if len(data) > 0:
                self.imageLoaded.emit(json.dumps(data))
        except Exception as e:
            print("Error during folder scanning:", e)
        self.finishedLoading.emit()


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
        self.copyDestinations = {}  # Mapping for keys 1..9 to destination folders.
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
        self.updateCopyList()
        # Adjust image scaling after the dock's visibility changes.
        self.copyDock.visibilityChanged.connect(
            lambda visible: QTimer.singleShot(0, self.adjustImageScale)
        )

        # Create the menu bar and add the "File" menu with several actions.
        self.createMenus()

        # mtime order: images sorted by last modified time (newest first).
        self.mtimeOrderSet = FastOrderedSet(key_func=lambda p: -1 * p.st_mtime)
        # random order: images in random order (can be changed later to filename order).
        self.randomOrderSet = FastOrderedSet(key_func=lambda p: p.pseudo_random_hash)
        # fname order: file name order
        self.fnameOrderSet = FastOrderedSet(key_func=lambda p: p.name)

        self.pseudo_random_seed = uuid.UUID(int=random.getrandbits(128))

        self.verticalOrderSet = self.mtimeOrderSet
        self.horizontalOrderSet = self.randomOrderSet

        # Store the currently loaded image (original, unscaled) and the current file path.
        self.originalPixmap = None
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
        self.selected_file_path = None

        self.counter = RecentCounter()

    def remove(self, imageData: ImageData):
        if len(self.mtimeOrderSet) == 0:
            return

        self.mtimeOrderSet.remove(imageData)
        self.randomOrderSet.remove(imageData)
        self.fnameOrderSet.remove(imageData)

        self.label.setText(f"count: {len(self.mtimeOrderSet)}")

        if len(self.mtimeOrderSet) == 0 or self.currentPath == imageData.path_nf:
            self.originalPixmap = None
            self.currentPath = None

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

        self.refreshFolder = QAction("Reload CurrentFolder", self)
        self.refreshFolder.triggered.connect(self.reloadCurrentFolder)

        self.copyToAct = QAction("Copy to ...", self)
        # self.copyToAct.setShortcut(QKeySequence("Meta+Ctrl+C"))
        self.copyToAct.triggered.connect(self.showCopyDock)

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

        self.count_label = QLabel("")
        self.count_label.setFixedWidth(68)
        self.count_label.setFont(font)

        self.label = QLabel("count: ")
        self.label.setFixedWidth(150)
        self.label.setFont(font)

    def createToolbar(self):
        """
        Create a toolbar and add the actions for opening a folder and zooming.
        """
        toolbar = QToolBar("Toolbar")
        self.addToolBar(toolbar)
        toolbar.addAction(self.refreshFolder)
        toolbar.addAction(self.copyToAct)
        # toolbar.addAction(self.zoomInAct)
        # toolbar.addAction(self.zoomOutAct)
        toolbar.addAction(self.normalSizeAct)
        toolbar.addWidget(self.VScroll)
        toolbar.addWidget(self.HScroll)
        toolbar.addWidget(self.loopScroll)
        toolbar.addWidget(self.freeScroll)

        count_label_action = QWidgetAction(toolbar)
        count_label_action.setDefaultWidget(self.count_label)
        toolbar.addAction(count_label_action)

        label_action = QWidgetAction(toolbar)
        label_action.setDefaultWidget(self.label)
        toolbar.addAction(label_action)

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
        if self.currentPath:
            return self.loadImagesFromFolder(self.currentPath)

    def loadImagesFromFolder(self, path, refresh_random_seed=False):
        """
        Load all image files from the specified folder, create two sort orders,
        and display the first image.

        :param path: The path to the folder or file to load images from.
        """

        if self.lazyLoadingInProgress:
            return

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

        self.lazyLoadingInProgress = True
        self.watcher.removePaths(self.watcher.directories())

        if not (
            self.currentPath
            and os.path.samefile(os.path.dirname(self.currentPath), folderPath)
        ):
            # Clear all image sets
            self.fnameOrderSet.clear()
            self.mtimeOrderSet.clear()
            self.randomOrderSet.clear()
        else:
            self.selected_file_path = filePath

        self.currentPath = None
        self.originalPixmap = None

        self.statusBar().showMessage("loading...", 2000)

        if refresh_random_seed:
            self.pseudo_random_seed = uuid.UUID(int=random.getrandbits(128))
        self.imageLoader = ImageLoaderWorker(
            folderPath, self.pseudo_random_seed, filePath
        )
        self.imageLoader.imageLoaded.connect(self.handleNewImage)
        self.imageLoader.finishedLoading.connect(self.finishLoadingImages)
        self.imageLoader.start()

    def handleNewImage(self, imageDataListJson):
        """ """

        imageDataList = [ImageData(**i) for i in json.loads(imageDataListJson)]

        existing_image_count = len(self.mtimeOrderSet)

        self.fnameOrderSet.update(imageDataList)
        self.mtimeOrderSet.update(imageDataList)
        self.randomOrderSet.update(imageDataList)

        # Load the first image if no image is currently displayed
        if (existing_image_count == 0 or self.selected_file_path) and imageDataList:
            self.loadImageFromFile(imageDataList[0])
        self.selected_file_path = None

        # self.statusBar().showMessage(f"Found file {imagePath}", 100)
        self.label.setText(f"count: {len(self.mtimeOrderSet)} ...")

    def finishLoadingImages(self):
        """ """
        self.statusBar().showMessage("Complete", 3000)
        self.label.setText(f"count: {len(self.mtimeOrderSet)}")

        self.lazyLoadingInProgress = False
        if self.currentPath:
            self.watcher.addPath(os.path.dirname(self.currentPath))

    def on_directory_changed(self, path):
        self.reloadCurrentFolder()

    def loadImageFromFile(self, imageData: ImageData):
        """
        Load and display the image specified by filePath.

        :param filePath: The full path to the image file.
        """
        filePath = imageData.path_nf
        currentPath = unicodedata.normalize("NFD", filePath)
        self.setWindowTitle(f"Yee3 - {os.path.basename(filePath)}")
        ext = Path(filePath).suffix[1:]
        if ext in image_format_extractors.keys():
            try:
                image_data = image_format_extractors[ext](filePath)
            except Exception as e:
                print("Error loading image:", e, filePath)
                return None
            if image_data:
                image = QPixmap()
                image.loadFromData(QByteArray(image_data))
            else:
                return None
        else:
            image = QPixmap(filePath)
        if image.isNull():
            self.imageDisplay.clearData()
            return None
        else:
            self.currentPath = currentPath
            self.originalPixmap = image
            self.imageDisplay.setData(
                self.originalPixmap,
                QMovie(filePath) if ext in image_format_animated else None,
            )
            self.adjustImageScale()
            count = self.counter.count()
            self.count_label.setText(f"{count:>3} ips")
            return image

    def adjustImageScale(self):
        """
        Adjust the image scale so that it fits optimally in the available central area.
        """
        if self.originalPixmap:
            availableWidth = self.scrollArea.viewport().width()
            availableHeight = self.scrollArea.viewport().height()
            imageSize = self.originalPixmap.size()
            # Calculate the optimal scale for the screen
            self.fittedScale = min(
                availableWidth / imageSize.width(), availableHeight / imageSize.height()
            )
            # Adjust the current scale to the optimal size
            self.scaleFactor = self.fittedScale
            newSize = imageSize * self.scaleFactor
            scaledPixmap = self.originalPixmap.scaled(
                newSize, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.imageDisplay.setPixmap(scaledPixmap)
            self.imageDisplay.setScaleFactor(self.scaleFactor)
            self.imageDisplay.resize(scaledPixmap.size())

    # --- Vertical Navigation (sorted by last modified time) ---
    def verticalPreviousImage(self):
        """
        Show the previous image in vertical order (sorted by last modified time).
        """
        if self.verticalOrderSet:
            currentPath = self.currentPath
            while len(self.verticalOrderSet) and currentPath:
                index = self.verticalOrderSet.index(currentPath)
                indexNext = (index + 1) % len(self.verticalOrderSet)
                if not self.loopScroll.isChecked() and indexNext < index:
                    return
                newCurrentPath = self.verticalOrderSet[indexNext]
                if self.loadImageFromFile(newCurrentPath) is None:
                    self.remove(newCurrentPath)
                else:
                    break

    def verticalNextImage(self):
        """
        Show the next image in vertical order (sorted by last modified time).
        """
        if self.verticalOrderSet:
            currentPath = self.currentPath
            while len(self.verticalOrderSet) and currentPath:
                index = self.verticalOrderSet.index(currentPath)
                indexNext = (index - 1) % len(self.verticalOrderSet)
                if not self.loopScroll.isChecked() and indexNext > index:
                    return
                newCurrentPath = self.verticalOrderSet[indexNext]
                if self.loadImageFromFile(newCurrentPath) is None:
                    self.remove(newCurrentPath)
                else:
                    break

    # --- Horizontal Navigation (random order) ---
    def horizontalNextImage(self):
        """
        Show the next image in horizontal order (random order).
        """
        if self.horizontalOrderSet:
            currentPath = self.currentPath
            while len(self.horizontalOrderSet) and currentPath:
                index = self.horizontalOrderSet.index(currentPath)
                indexNext = (index + 1) % len(self.horizontalOrderSet)
                if not self.loopScroll.isChecked() and indexNext < index:
                    return
                newCurrentPath = self.horizontalOrderSet[indexNext]
                if self.loadImageFromFile(newCurrentPath) is None:
                    self.remove(newCurrentPath)
                else:
                    break

    def horizontalPreviousImage(self):
        """
        Show the previous image in horizontal order (random order).
        """
        if self.horizontalOrderSet:
            currentPath = self.currentPath
            while len(self.horizontalOrderSet) and currentPath:
                index = self.horizontalOrderSet.index(currentPath)
                indexNext = (index - 1) % len(self.horizontalOrderSet)
                if not self.loopScroll.isChecked() and indexNext > index:
                    return
                newCurrentPath = self.horizontalOrderSet[indexNext]
                if self.loadImageFromFile(newCurrentPath) is None:
                    self.remove(newCurrentPath)
                else:
                    break

    def keyPressEvent(self, event):
        """
        Handle key press events:
          - Up/Down keys: navigate vertical order (last modified time).
          - Left/Right keys: navigate horizontal order (random order).
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
        """
        Handle mouse wheel events:
          - If vertical delta is dominant, use vertical navigation.
          - If horizontal delta is dominant, use horizontal navigation.
        """
        deltaY = event.angleDelta().y()
        deltaX = event.angleDelta().x()

        now = datetime.now()
        elapsed_time_since_last_display = (
            now - self.last_display_datetime
        ).total_seconds()

        if self.freeScroll.isChecked():
            scroll_factors = scroll_factors_dict["free"]
        else:
            scroll_factors = scroll_factors_dict["limit"]

            # If zoomed in and the scrollbar is visible, reduce sensitivity
            if (
                self.scaleFactor / self.fittedScale >= 1.25
                and self.scrollArea.verticalScrollBar().maximum() > 0
            ):
                threshold = 180
                if max(abs(deltaY), abs(deltaX)) < threshold:
                    return

        # Vertical scroll accumulation
        self.scrollAccumulationY += deltaY * scroll_factors["vertical"]["scroll"]
        self.scrollAccumulationY = min(
            self.scrollAccumulationY, scroll_factors["vertical"]["max"]
        )

        # Horizontal scroll accumulation
        self.scrollAccumulationX += deltaX * scroll_factors["horizontal"]["scroll"]
        self.scrollAccumulationX = min(
            self.scrollAccumulationX, scroll_factors["horizontal"]["max"]
        )

        self.statusBar().showMessage(
            f"wheelEvent: {deltaX}, {deltaY} - X: {self.scrollAccumulationX}, Y: {self.scrollAccumulationY}",
            1000,
        )

        if elapsed_time_since_last_display > scroll_factors["interval"]:
            if abs(self.scrollAccumulationY) >= abs(self.scrollAccumulationX):
                # Vertical scrolling
                self.verticalGauge.updateGauge(self.scrollAccumulationY)

                if (
                    abs(self.scrollAccumulationY)
                    >= scroll_factors["vertical"]["threshold"]
                ):
                    if self.scrollAccumulationY > 0:
                        self.verticalPreviousImage()
                    else:
                        self.verticalNextImage()

                    self.scrollAccumulationY /= scroll_factors["vertical"]["release"]
                    self.last_display_datetime = now
            else:
                # Horizontal scrolling
                self.horizontalGauge.updateGauge(self.scrollAccumulationX)

                if (
                    abs(self.scrollAccumulationX)
                    >= scroll_factors["horizontal"]["threshold"]
                ):
                    if self.scrollAccumulationX > 0:
                        self.horizontalPreviousImage()
                    else:
                        self.horizontalNextImage()

                    self.scrollAccumulationX /= scroll_factors["horizontal"]["release"]
                    self.last_display_datetime = now

        # Update the gauge
        self.verticalGauge.updateGauge(self.scrollAccumulationY)
        self.horizontalGauge.updateGauge(self.scrollAccumulationX)

        # Start the timer (execute the decay process)
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
        if self.originalPixmap:
            self.scaleFactor *= factor
            newSize = self.originalPixmap.size() * self.scaleFactor
            scaledPixmap = self.originalPixmap.scaled(
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
        action = menu.exec_(event.globalPos())
        if action in actions:
            if actions[action] == "reveal":
                if self.currentPath is not None:
                    try:
                        subprocess.call(["open", "-R", self.currentPath])
                    except Exception as e:
                        print("Error revealing file in Finder:", e)

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
            except Exception as e:
                print("Error loading settings:", e)
        else:
            self.copyDestinations = {}

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

    def copyToDestination(self, index):
        """
        Copy the current image file to the destination folder associated with the given index.
        If no destination is set for that index, prompt the user to select one.

        :param index: The index (1-9) corresponding to the copy destination.
        """
        print(f"copyToDestination called with index: {index}")  # Debug output.
        dest = self.copyDestinations.get(str(index)) or self.copyDestinations.get(index)
        if not dest:
            folder = QFileDialog.getExistingDirectory(
                self, f"Select destination for Cmd+{index}"
            )
            if not folder:
                print("No destination selected.")
                return
            dest = folder
            self.copyDestinations[str(index)] = dest
            self.updateCopyList()

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
                    # Replace copy
                    try:
                        shutil.copy2(self.currentPath, targetPath)
                        self.statusBar().showMessage(f"Replaced file at {dest}", 3000)
                    except Exception as e:
                        self.statusBar().showMessage(f"Copy failed: {e}", 3000)
                elif result == ReplaceDialogResult.RENAME:
                    # Copy with a new name
                    try:
                        copy_with_unique_name(self.currentPath, dest)
                        self.statusBar().showMessage(
                            f"Copied to {dest} with a new name", 3000
                        )
                    except Exception as e:
                        self.statusBar().showMessage(f"Copy failed: {e}", 3000)
                elif result == ReplaceDialogResult.CANCEL:
                    # Cancel
                    self.statusBar().showMessage("Copy canceled", 3000)
                    return
            else:
                # If there is no file with the same name, perform a normal copy
                try:
                    shutil.copy2(self.currentPath, targetPath)
                    self.statusBar().showMessage(f"Copied to {dest}", 3000)
                except Exception as e:
                    self.statusBar().showMessage(f"Copy failed: {e}", 3000)
        else:
            print("No current file available.")

    def onCopyListDoubleClicked(self, item):
        """
        Handle double-click events on the copy destination list.
        Double-clicking an item will trigger copying to that destination.
        """
        row = self.copyList.row(item)
        index = row + 1
        self.copyToDestination(index)

    def eventFilter(self, obj, event):
        """
        Event filter to capture double-click events on the image label and scroll area.
        On a double-click, resize the window:
          - Set height to the maximum available screen height.
          - Adjust width to maintain the aspect ratio of the currently displayed image.
        """
        if (
            obj == self.imageDisplay or obj == self.scrollArea
        ) and event.type() == QEvent.MouseButtonDblClick:
            screen_geometry = QApplication.primaryScreen().availableGeometry()
            screen_height = screen_geometry.height()
            window_center_x = self.geometry().center().x()

            status_bar_height = self.statusBar().height()
            adjusted_height = screen_height - status_bar_height

            if self.originalPixmap:
                image_size = self.originalPixmap.size()
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
