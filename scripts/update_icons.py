"""Regenerate the committed app icons from the PNG iconset."""

from contextlib import ExitStack
from pathlib import Path

from PIL import Image


def main():
    resources = Path(__file__).resolve().parents[1] / "src/yee3/resources"
    iconset = resources / "yee3.iconset"
    with ExitStack() as stack:
        images = {
            size: stack.enter_context(Image.open(iconset / f"icon_{size}x{size}.png"))
            for size in (16, 32, 64, 128, 256, 512, 1024)
        }
        images[1024].save(
            resources / "yee3.icns", format="ICNS", append_images=list(images.values())
        )
        images[256].save(
            resources / "yee3.ico", format="ICO",
            sizes=[(16, 16), (32, 32), (48, 48), (256, 256)],
        )


if __name__ == "__main__":
    main()
