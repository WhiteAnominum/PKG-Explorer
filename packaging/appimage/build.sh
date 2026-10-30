#!/usr/bin/env bash
# Builds PKG-Explorer-x86_64.AppImage into dist/.
# Requires: python3 (with venv) and internet access (downloads the base Python and the dependencies).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
APP_ID="io.github.WhiteAnominum.PKGExplorer"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$ROOT/dist"

echo ">> Build tools"
python3 -m venv "$WORK/tools"
"$WORK/tools/bin/pip" install -q -r "$ROOT/packaging/appimage/requirements-build.txt"

echo ">> PKG Explorer wheel"
"$WORK/tools/bin/python" -m build --wheel --outdir "$WORK/wheel" "$ROOT"
WHEEL="$(ls "$WORK"/wheel/*.whl)"

echo ">> AppImage recipe"
RECIPE="$WORK/recipe"
mkdir -p "$RECIPE"
cp "$ROOT/packaging/appimage/entrypoint.sh" "$RECIPE/"
cp "$ROOT/packaging/$APP_ID.desktop" "$RECIPE/"
cp "$ROOT/packaging/$APP_ID.png" "$RECIPE/$APP_ID.png"
cp "$ROOT/packaging/$APP_ID.metainfo.xml" "$RECIPE/$APP_ID.appdata.xml"
# Pinned dependencies first; then the program's wheel (which then does not pull other versions).
grep -v '^#' "$ROOT/packaging/appimage/requirements-app.txt" > "$RECIPE/requirements.txt"
echo "$WHEEL" >> "$RECIPE/requirements.txt"

echo ">> Building the AppImage (Python $PYTHON_VERSION)"
cd "$WORK"
"$WORK/tools/bin/python-appimage" build app --no-packaging -p "$PYTHON_VERSION" -n PKG-Explorer "$RECIPE"
# The folder name comes from Name= in the .desktop file ("PKG Explorer"); build_appimage does
# not quote paths, so neither the AppDir nor the destination may contain spaces.
APPDIR="$WORK/AppDir"
mv "$WORK/PKG Explorer-$(uname -m)" "$APPDIR"
# python-appimage creates the AppDir with mode 0700, which appimagetool rejects.
chmod -R a+rX "$APPDIR"
"$WORK/tools/bin/python" -c 'import sys; from python_appimage.appimage.build import build_appimage; build_appimage(sys.argv[1], destination=sys.argv[2])' \
    "$APPDIR" "$WORK/PKG-Explorer-$(uname -m).AppImage"
mv "$WORK/PKG-Explorer-$(uname -m).AppImage" "$ROOT/dist/"
ls -lh "$ROOT"/dist/PKG-Explorer-*.AppImage
