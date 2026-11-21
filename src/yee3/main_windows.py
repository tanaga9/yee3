import sys

from yee3.app import ImageViewerApplication, OSType, initialize_image_viewer


def main():
    app = ImageViewerApplication(sys.argv)

    imagePath = sys.argv[1] if len(sys.argv) > 1 else None
    if imagePath:
        initialize_image_viewer(imagePath, os_type=OSType.WINDOWS)
    else:
        initialize_image_viewer(os_type=OSType.WINDOWS)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
