import os
import sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QMessageBox
from Foundation import NSObject
from Cocoa import NSApp

from yee3.app import ImageViewerApplication, OSType, initialize_image_viewer


class MacOSFileHandler(NSObject):
    windows = []

    def application_openFiles_(self, app, imagePaths):
        try:
            # QMessageBox.information(None, "", "event: " + str(self.windows))
            imagePath = imagePaths[0]
            if len(self.windows) == 1 and self.windows[0].current_path is None:
                window = self.windows[0]
                window.loadImagesFromFolder(imagePath)
            else:
                window = initialize_image_viewer(imagePath, os_type=OSType.MACOS)
                self.windows.append(window)
        except Exception as e:
            QMessageBox.critical(None, "", str(e))


def main():
    app = ImageViewerApplication(sys.argv)
    delegate = MacOSFileHandler.alloc().init()

    imagePath = sys.argv[1] if len(sys.argv) > 1 else None
    if imagePath:
        window = initialize_image_viewer(imagePath, os_type=OSType.MACOS)
    else:
        window = initialize_image_viewer(os_type=OSType.MACOS)

    delegate.windows.append(window)
    # QMessageBox.information(
    #     None,
    #     "",
    #     "new: "
    #     + str(delegate.windows)
    #     + " "
    #     + str(window.current_path)
    #     + " "
    #     + str(sys.argv),
    # )
    NSApp.setDelegate_(delegate)

    if os.environ.get("YEE3_CHECK_STARTUP") == "1":
        QTimer.singleShot(0, app.quit)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
