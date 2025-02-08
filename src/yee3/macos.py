import sys
from PyQt5.QtWidgets import QApplication
from Foundation import NSObject
from Cocoa import NSApp

from yee3.app import initialize_image_viewer

windows = []


class MacOSFileHandler(NSObject):
    def application_openFiles_(self, app, imagePaths):
        global windows

        imagePath = imagePaths[0]
        windows.append(initialize_image_viewer(imagePath))


def main():
    global windows

    app = QApplication(sys.argv)

    delegate = MacOSFileHandler.alloc().init()
    NSApp.setDelegate_(delegate)

    imagePath = sys.argv[1] if len(sys.argv) > 1 else None

    if imagePath:
        windows.append(initialize_image_viewer(imagePath))
    else:
        windows.append(initialize_image_viewer())

    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
