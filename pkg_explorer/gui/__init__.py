# SPDX-License-Identifier: GPL-2.0-or-later
"""Graphical interface (Qt / PySide6).

- window.py: main window and its actions
- tree.py: the file list and dragging items out of it
- model.py: tree model of the PKG contents (sorting, filtering)
- extraction.py: extraction thread, progress dialog, temporary folders
"""

from __future__ import annotations

import contextlib
import sys

from PySide6.QtCore import QLibraryInfo, QLocale, QTimer, QTranslator
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .. import desktop
from ..i18n import LANGUAGE
from .window import MainWindow

__all__ = ["MainWindow", "main"]


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    app = QApplication(argv)
    app.setApplicationName("PKG Explorer")
    app.setOrganizationName("PKG Explorer")
    app.setDesktopFileName(desktop.APP_ID)  # Wayland: ties the window to the shortcut/icon
    app.setWindowIcon(QIcon(str(desktop.DATA_DIR / "pkg-explorer.svg")))
    if LANGUAGE == "pt":
        # Qt's own texts (Yes/No buttons, file dialogs).
        translator = QTranslator(app)
        if translator.load(
            QLocale("pt_BR"), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.TranslationsPath)
        ):
            app.installTranslator(translator)
    with contextlib.suppress(OSError):
        desktop.repair_if_moved()
    window = MainWindow()
    window.show()
    if desktop.should_ask_on_first_run():
        QTimer.singleShot(0, window.ask_integration)
    if len(argv) > 1:
        window.open_pkg(argv[1])
    return app.exec()
