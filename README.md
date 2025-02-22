
Yee 3
======

The Minimalist Image Viewer.
Sleek and fast.

![Yee 3](src/yee3/resources/yee3.iconset/icon_256x256.png)

[Xee 3](https://theunarchiver.com/xee) lacks Apple Silicon support and struggles with some image formats, so I built this with ChatGPT (o3-mini-high).

Designed for macOS.
half-hearted Windows support.

Concept and design
----------------

![Concept and design](docs/concept_and_design.png)

How to build an app on your local Mac
------------------------------------

- **Setup the environment**
    - `pip3 install pdm`
    - `pdm install --dev`  *(Run again if `pyproject.toml` or `pdm.lock` changes.)*
- **Build**
    - `iconutil -c icns src/yee3/resources/yee3.iconset`
    - `pdm run briefcase build macOS`
    - `cp ./Info.plist build/yee3/macos/app/Yee\ 3.app/Contents/.`


Used via Automator (without build)
----------------------------------

Use Automator for quick app wrapping.

- `Run Shell Script`
- Shell: `/bin/zsh`
- Pass input: `as arguments`

```
set -euo pipefail
# pip3 install PyQt5
python3 ${YEE3_SCRIPT_PATH}/src/yee3/app.py "$@"
```

Please either modify or configure the YEE3_SCRIPT_PATH part.
