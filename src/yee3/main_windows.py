import sys

from yee3.app import initialize_image_viewer


def main():
    app = QApplication(sys.argv)

    imagePath = sys.argv[1] if len(sys.argv) > 1 else None
    if imagePath:
        initialize_image_viewer(imagePath)
    else:
        initialize_image_viewer()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
