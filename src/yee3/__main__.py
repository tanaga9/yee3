import sys

if __name__ == "__main__":
    if sys.platform == "darwin":
        from yee3.main_macos import main
    elif sys.platform == "win32":
        from yee3.main_windows import main
    else:
        raise RuntimeError(f"Unsupported platform: {sys.platform}")
    main()
