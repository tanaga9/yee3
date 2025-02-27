import os
import sys
from PySide6.QtWidgets import QApplication, QMessageBox
from Foundation import NSObject
from Cocoa import NSApp

from yee3.app import initialize_image_viewer, OSType


class MacOSFileHandler(NSObject):
    windows = []

    def application_openFiles_(self, app, imagePaths):
        try:
            # QMessageBox.information(None, "", "event: " + str(self.windows))
            imagePath = imagePaths[0]
            if len(self.windows) == 1 and self.windows[0].currentPath is None:
                window = self.windows[0]
                window.loadImagesFromFolder(imagePath)
            else:
                window = initialize_image_viewer(imagePath, os_type=OSType.MACOS)
                self.windows.append(window)
        except Exception as e:
            QMessageBox.critical(None, "", str(e))


def main():
    app = QApplication(sys.argv)
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
    #     + str(window.currentPath)
    #     + " "
    #     + str(sys.argv),
    # )
    NSApp.setDelegate_(delegate)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
