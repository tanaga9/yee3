
# osascript -e 'tell application "Finder" to delete POSIX file "'"$(realpath ./dist)"'"'
osascript -e 'tell application "Finder" to delete POSIX file "'"$(realpath ./build)"'"'

iconutil -c icns src/yee3/resources/yee3.iconset
# pdm run briefcase update macOS
pdm run briefcase build macOS
patch -u < Info.plist.patch
# printf "1\n" | pdm run briefcase package macOS
