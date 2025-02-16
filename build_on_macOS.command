
# osascript -e 'tell application "Finder" to delete POSIX file "'"$(realpath ./dist)"'"'
osascript -e 'tell application "Finder" to delete POSIX file "'"$(realpath ./build)"'"'

iconutil -c icns src/yee3/resources/yee3.iconset
# pdm run briefcase update macOS
pdm run briefcase build macOS
cp ./Info.plist build/yee3/macos/app/Yee\ 3.app/Contents/.
# printf "1\n" | pdm run briefcase package macOS
