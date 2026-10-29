#! /bin/bash
# Entrada do AppImage. "--cli" roda a linha de comando, ex.:
#   PKG-Explorer-x86_64.AppImage --cli info jogo.pkg
if [ "$1" = "--cli" ]; then
    shift
    exec {{ python-executable }} -I -m pkg_explorer.cli "$@"
fi
exec {{ python-executable }} -I -m pkg_explorer "$@"
