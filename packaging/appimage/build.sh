#!/usr/bin/env bash
# Gera PKG-Explorer-x86_64.AppImage em dist/.
# Requer: python3 (com venv) e acesso à internet (baixa o Python base e as dependências).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
APP_ID="io.github.WhiteAnominum.PKGExplorer"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$ROOT/dist"

echo ">> Ferramentas de build"
python3 -m venv "$WORK/tools"
"$WORK/tools/bin/pip" install -q --upgrade pip python-appimage build

echo ">> Wheel do PKG Explorer"
"$WORK/tools/bin/python" -m build --wheel --outdir "$WORK/wheel" "$ROOT"
WHEEL="$(ls "$WORK"/wheel/*.whl)"

echo ">> Receita do AppImage"
RECIPE="$WORK/recipe"
mkdir -p "$RECIPE"
cp "$ROOT/packaging/appimage/entrypoint.sh" "$RECIPE/"
cp "$ROOT/packaging/$APP_ID.desktop" "$RECIPE/"
cp "$ROOT/packaging/$APP_ID.png" "$RECIPE/$APP_ID.png"
cp "$ROOT/packaging/$APP_ID.metainfo.xml" "$RECIPE/$APP_ID.appdata.xml"
echo "$WHEEL" > "$RECIPE/requirements.txt"

echo ">> Montando o AppImage (Python $PYTHON_VERSION)"
cd "$WORK"
"$WORK/tools/bin/python-appimage" build app --no-packaging -p "$PYTHON_VERSION" -n PKG-Explorer "$RECIPE"
# O nome da pasta vem do Name= do .desktop ("PKG Explorer"); o build_appimage não
# põe aspas nos caminhos, então nem a AppDir nem o destino podem ter espaços.
APPDIR="$WORK/AppDir"
mv "$WORK/PKG Explorer-$(uname -m)" "$APPDIR"
# O python-appimage cria a AppDir com modo 0700, que o appimagetool recusa.
chmod -R a+rX "$APPDIR"
"$WORK/tools/bin/python" -c 'import sys; from python_appimage.appimage.build import build_appimage; build_appimage(sys.argv[1], destination=sys.argv[2])' \
    "$APPDIR" "$WORK/PKG-Explorer-$(uname -m).AppImage"
mv "$WORK/PKG-Explorer-$(uname -m).AppImage" "$ROOT/dist/"
ls -lh "$ROOT"/dist/PKG-Explorer-*.AppImage
