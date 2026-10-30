#! /bin/bash
# AppImage entry point. "--cli" runs the command line, e.g.:
#   PKG-Explorer-x86_64.AppImage --cli info game.pkg
if [ "$1" = "--cli" ]; then
    shift
    exec {{ python-executable }} -I -m pkg_explorer.cli "$@"
fi
exec {{ python-executable }} -I -m pkg_explorer "$@"
