
Yee 3
======

The Minimalist Image Viewer.
Sleek and fast.

[Xee 3](https://theunarchiver.com/xee) does not support Apple Silicon and could not open some image formats correctly, so I tried creating an Image Viewer using ChatGPT (o3-mini-high).

It is intended for macOS. If you want to make it into an application, wrapping it with Automator would be a good option.


How to build an app on your local Mac
------------------------------------

- `pip3 install pdm`
- `pdm install --dev`
- `iconutil -c icns src/yee3/resources/yee3.iconset`
- `pdm run briefcase build macOS`
- `patch -u < Info.plist.patch`


Used via Automator (without build)
----------------------------------

- `Run Shell Script`
- Shell: `/bin/zsh`
- Pass input: `as arguments`

```
set -euo pipefail
# pip3 install PyQt5
python3 ${YEE3_SCRIPT_PATH}/src/yee3/app.py "$@"
```

Please either modify or configure the YEE3_SCRIPT_PATH part.
