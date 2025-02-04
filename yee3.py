#!/usr/bin/env python3
import sys
import os
import json
import random
import shutil
import subprocess
from datetime import datetime
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QLabel,
    QScrollArea,
    QFileDialog,
    QAction,
    QToolBar,
    QSizePolicy,
    QMenu,
    QDockWidget,
    QListWidget,
    QShortcut,
    QStatusBar,
    QToolButton,
    QWidget,
)
from PyQt5.QtGui import (
    QPixmap,
    QPalette,
    QImageReader,
    QKeySequence,
    QPainter,
    QColor,
    QBrush,
)
from PyQt5.QtCore import Qt, QTimer, QEvent, QPoint

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

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Yee3")

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
        self.imageLabel = QLabel()
        self.imageLabel.setBackgroundRole(QPalette.Base)
        self.imageLabel.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.imageLabel.setScaledContents(True)
        # Install an event filter to capture double-click events for copying.
        self.imageLabel.installEventFilter(self)

        # Set up a scroll area with a black background.
        self.scrollArea = QScrollArea()
        self.scrollArea.setAlignment(Qt.AlignCenter)
        self.scrollArea.setStyleSheet("background-color: #222;")
        self.scrollArea.setWidget(self.imageLabel)
        # Prevent the scroll area from taking focus so that key events are handled by the main window.
        self.scrollArea.setFocusPolicy(Qt.NoFocus)
        self.setCentralWidget(self.scrollArea)

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

        # Lists for image file paths.
        self.allImages = []  # Unsorted list of image file paths.
        # mtime order: images sorted by last modified time (newest first).
        self.mtimeOrder = []
        # random order: images in random order (can be changed later to filename order).
        self.randomOrder = []
        # fname order: file name order
        self.fnameOrder = []

        self.verticalOrder = self.mtimeOrder
        self.horizontalOrder = self.randomOrder

        # Store the currently loaded image (original, unscaled) and the current file path.
        self.originalPixmap = None
        self.currentFile = None

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
            sc = QShortcut(QKeySequence(f"Meta+{i}"), self)
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
        supportedFormats = QImageReader.supportedImageFormats()
        extensions = " ".join(
            ["*." + str(fmt, "utf-8").lower() for fmt in supportedFormats]
        )
        fileFilter = f"Images ({extensions})"
        filePath, _ = QFileDialog.getOpenFileName(
            self, "Open File", os.getcwd(), fileFilter
        )
        if filePath:
            folder = os.path.dirname(filePath)
            self.loadImagesFromFolder(folder)
            self.loadImageFromFile(filePath)

    def revealInFinder(self):
        """
        Reveal the currently displayed file in Finder (macOS).
        """
        if self.currentFile:
            try:
                subprocess.call(["open", "-R", self.currentFile])
            except Exception as e:
                print("Error revealing file in Finder:", e)

    def showCopyDock(self):
        """
        Show the copy destination dock widget.
        """
        self.copyDock.show()

    def get_order_name(self, order):
        """Return the order type as a string based on the list object"""
        if order is self.mtimeOrder:
            return "mtime"
        elif order is self.fnameOrder:
            return "fname"
        elif order is self.randomOrder:
            return "random"
        return "mtime"  # Default in case of an unexpected value

    def get_order_by_name(self, name, default=None):
        """Return the corresponding order list based on the given string"""
        if name == "mtime":
            return self.mtimeOrder
        elif name == "fname":
            return self.fnameOrder
        elif name == "random":
            return self.randomOrder
        return default if default is not None else self.mtimeOrder

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
        if self.verticalOrder:
            # Get the current vertical and horizontal order as strings
            current = self.get_order_name(self.verticalOrder)
            other = self.get_order_name(self.horizontalOrder)
            # Determine the next order to set
            new_order_name = self.toggled_order(current, other)
            self.verticalOrder = self.get_order_by_name(new_order_name)
            self.VScroll.setText("VScroll: " + new_order_name)

    def onHScrollClicked(self):
        if self.horizontalOrder:
            # Get the current horizontal and vertical order as strings
            current = self.get_order_name(self.horizontalOrder)
            other = self.get_order_name(self.verticalOrder)
            # Determine the next order to set
            new_order_name = self.toggled_order(current, other)
            self.horizontalOrder = self.get_order_by_name(new_order_name)
            self.HScroll.setText("HScroll: " + new_order_name)

    def createActions(self):
        """
        Create actions for opening a folder, zooming in, zooming out, and resetting the image size.
        """
        self.openFolderAct = QAction("Open Folder", self)
        self.openFolderAct.setShortcut(QKeySequence.Open)
        self.openFolderAct.triggered.connect(self.openFolder)

        self.zoomInAct = QAction("Zoom In", self)
        self.zoomInAct.setShortcut(QKeySequence.ZoomIn)
        self.zoomInAct.triggered.connect(self.zoomIn)

        self.zoomOutAct = QAction("Zoom Out", self)
        self.zoomOutAct.setShortcut(QKeySequence.ZoomOut)
        self.zoomOutAct.triggered.connect(self.zoomOut)

        self.normalSizeAct = QAction("Normal Size", self)
        self.normalSizeAct.triggered.connect(self.normalSize)

        self.VScroll = QToolButton()
        self.VScroll.setText("VScroll: mtime")
        self.VScroll.clicked.connect(self.onVScrollClicked)

        self.HScroll = QToolButton()
        self.HScroll.setText("HScroll: random")
        self.HScroll.clicked.connect(self.onHScrollClicked)

        self.freeScroll = QToolButton()
        self.freeScroll.setText("Free Scroll")
        self.freeScroll.setCheckable(True)
        self.freeScroll.setChecked(False)

    def createToolbar(self):
        """
        Create a toolbar and add the actions for opening a folder and zooming.
        """
        toolbar = QToolBar("Toolbar")
        self.addToolBar(toolbar)
        toolbar.addAction(self.openFolderAct)
        toolbar.addAction(self.zoomInAct)
        toolbar.addAction(self.zoomOutAct)
        toolbar.addAction(self.normalSizeAct)
        toolbar.addWidget(self.VScroll)
        toolbar.addWidget(self.HScroll)
        toolbar.addWidget(self.freeScroll)

    def openFolder(self):
        """
        Open a folder dialog and load images from the selected directory.
        """
        folder = QFileDialog.getExistingDirectory(self, "Open Folder", os.getcwd())
        if folder:
            self.loadImagesFromFolder(folder)

    def loadImagesFromFolder(self, folder):
        """
        Load all image files from the specified folder, create two sort orders,
        and display the first image.

        :param folder: The folder from which to load images.
        """
        supportedFormats = QImageReader.supportedImageFormats()
        imageExtensions = [str(fmt, "utf-8").lower() for fmt in supportedFormats]
        files = os.listdir(folder)
        imageFiles = [
            os.path.join(folder, f)
            for f in files
            if any(f.lower().endswith("." + ext) for ext in imageExtensions)
        ]
        if imageFiles:
            # Store the current vertical and horizontal order types
            vscroll = self.get_order_name(self.verticalOrder)
            hscroll = self.get_order_name(self.horizontalOrder)

            self.allImages = imageFiles
            # fname order: Sort by file name.
            self.fnameOrder = sorted(self.allImages)
            # mtime order: Sort by last modified time (newest first).
            self.mtimeOrder = sorted(
                self.allImages, key=lambda p: os.path.getmtime(p), reverse=True
            )
            # random order: Shuffle images randomly.
            self.randomOrder = list(self.allImages)
            random.shuffle(self.randomOrder)
            # Initialize indices using the first image in mtime order.
            currentFile = self.mtimeOrder[0]

            self.verticalOrder = self.get_order_by_name(vscroll, self.mtimeOrder)
            self.horizontalOrder = self.get_order_by_name(hscroll, self.randomOrder)

            self.loadImageFromFile(currentFile)

    def loadImageFromFile(self, filePath):
        """
        Load and display the image specified by filePath.

        :param filePath: The full path to the image file.
        """
        self.currentFile = filePath
        image = QPixmap(filePath)
        if image.isNull():
            self.imageLabel.setText("Unable to load image.")
        else:
            self.originalPixmap = image
            self.adjustImageScale()
            self.setWindowTitle(f"Yee3 - {os.path.basename(filePath)}")

    def adjustImageScale(self):
        """
        Adjust the image scale so that it fits optimally in the available central area.
        """
        if self.originalPixmap:
            availableWidth = self.scrollArea.viewport().width()
            availableHeight = self.scrollArea.viewport().height()
            imageSize = self.originalPixmap.size()
            scale = min(
                availableWidth / imageSize.width(), availableHeight / imageSize.height()
            )
            self.scaleFactor = scale
            newSize = imageSize * self.scaleFactor
            scaledPixmap = self.originalPixmap.scaled(
                newSize, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            self.imageLabel.setPixmap(scaledPixmap)
            self.imageLabel.resize(scaledPixmap.size())

    # --- Vertical Navigation (sorted by last modified time) ---
    def verticalNextImage(self):
        """
        Show the next image in vertical order (sorted by last modified time).
        """
        if self.verticalOrder:
            index = self.verticalOrder.index(self.currentFile)
            index = (index + 1) % len(self.verticalOrder)
            currentFile = self.verticalOrder[index]
            self.loadImageFromFile(currentFile)

    def verticalPreviousImage(self):
        """
        Show the previous image in vertical order (sorted by last modified time).
        """
        if self.verticalOrder:
            index = self.verticalOrder.index(self.currentFile)
            index = (index - 1) % len(self.verticalOrder)
            currentFile = self.verticalOrder[index]
            self.loadImageFromFile(currentFile)

    # --- Horizontal Navigation (random order) ---
    def horizontalNextImage(self):
        """
        Show the next image in horizontal order (random order).
        """
        if self.horizontalOrder:
            index = self.horizontalOrder.index(self.currentFile)
            index = (index + 1) % len(self.horizontalOrder)
            currentFile = self.horizontalOrder[index]
            self.loadImageFromFile(currentFile)

    def horizontalPreviousImage(self):
        """
        Show the previous image in horizontal order (random order).
        """
        if self.horizontalOrder:
            index = self.horizontalOrder.index(self.currentFile)
            index = (index - 1) % len(self.horizontalOrder)
            currentFile = self.horizontalOrder[index]
            self.loadImageFromFile(currentFile)

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
            self.imageLabel.setPixmap(scaledPixmap)
            self.imageLabel.resize(scaledPixmap.size())

    def contextMenuEvent(self, event):
        """
        Display a context menu on right-click with an option to reveal the current file in Finder.
        """
        menu = QMenu(self)
        revealAction = menu.addAction("Reveal in Finder")
        action = menu.exec_(event.globalPos())
        if action == revealAction:
            if self.currentFile is not None:
                try:
                    subprocess.call(["open", "-R", self.currentFile])
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
                supportedFormats = QImageReader.supportedImageFormats()
                imageExtensions = [
                    str(fmt, "utf-8").lower() for fmt in supportedFormats
                ]
                if any(filePath.lower().endswith(ext) for ext in imageExtensions):
                    folder = os.path.dirname(filePath)
                    self.loadImagesFromFolder(folder)
                    self.loadImageFromFile(filePath)
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
        if self.currentFile:
            print(f"Copying '{self.currentFile}' to destination '{dest}'")
            try:
                copy_with_unique_name(self.currentFile, dest)
                self.statusBar().showMessage(f"Copied file to {dest}", 3000)
            except Exception as e:
                self.statusBar().showMessage(f"Copy failed: {e}", 3000)
        else:
            print("No current file set for copying.")

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
        Event filter to capture double-click events on the image label.
        On a double-click, attempt to copy the current file using the currently selected
        copy destination from the dock list (or default to Cmd+1 if none is selected).

        :param obj: The object for which the event is being filtered.
        :param event: The event.
        :return: True if the event is handled, otherwise call the base implementation.
        """
        if obj == self.imageLabel and event.type() == QEvent.MouseButtonDblClick:
            row = self.copyList.currentRow()
            index = row + 1 if row >= 0 else 1
            self.copyToDestination(index)
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


if __name__ == "__main__":
    app = QApplication(sys.argv)
    viewer = ImageViewer()

    # If an image file is provided as a command-line argument, load its folder and display that image.
    if len(sys.argv) > 1:
        imagePath = sys.argv[1]
        if os.path.isfile(imagePath):
            folder = os.path.dirname(imagePath)
            viewer.loadImagesFromFolder(folder)
            try:
                viewer.mtimeOrder.index(imagePath)
            except ValueError:
                viewer.loadImageFromFile(viewer.mtimeOrder[0])
                viewer.statusBar().showMessage(f"NotFound file {imagePath}", 10000)
            else:
                viewer.loadImageFromFile(imagePath)

    viewer.show()
    sys.exit(app.exec_())
