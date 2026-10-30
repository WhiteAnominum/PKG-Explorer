# SPDX-License-Identifier: GPL-2.0-or-later
"""Drag and drop with extraction straight into the drop target, on KDE.

Uses the same protocol as Ark: the drag carries only this program's D-Bus address
(application/x-kde-ark-dndextract-service / -path). When the user drops onto a folder in
Dolphin, the Plasma desktop or any other KIO-based program, KIO calls
org.kde.ark.DndExtract.extractSelectedFilesTo(folder) here, and we extract into that folder.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from PySide6.QtCore import ClassInfo, QMimeData, QObject, QTimer, QUrl, Slot
from PySide6.QtDBus import QDBusConnection

MIME_SERVICE = "application/x-kde-ark-dndextract-service"
MIME_PATH = "application/x-kde-ark-dndextract-path"
OBJECT_PATH = f"/DndExtract/{os.getpid()}"


def is_available() -> bool:
    if (
        os.environ.get("PKG_EXPLORER_DND") == "temp"
    ):  # forces the temporary-folder mode (for testing)
        return False
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper().split(":")
    return "KDE" in desktop and QDBusConnection.sessionBus().isConnected()


@ClassInfo({"D-Bus Interface": "org.kde.ark.DndExtract"})
class DndExtractService(QObject):
    """Receives from KIO the folder where the items were dropped and passes it to `on_drop`."""

    def __init__(self, on_drop: Callable[[str], None], parent: QObject | None = None):
        super().__init__(parent)
        self._on_drop = on_drop
        bus = QDBusConnection.sessionBus()
        self.registered = bus.registerObject(OBJECT_PATH, self, QDBusConnection.ExportAllSlots)
        self.service_name = bus.baseService()

    def mime_data(self) -> QMimeData:
        mime = QMimeData()
        mime.setData(MIME_SERVICE, self.service_name.encode())
        mime.setData(MIME_PATH, OBJECT_PATH.encode())
        return mime

    @Slot(str)
    def extractSelectedFilesTo(self, destination: str) -> None:
        # KIO waits for this D-Bus call to return before releasing Dolphin, so we only
        # schedule the extraction and return right away.
        if "://" in destination:
            url = QUrl(destination)
            destination = url.toLocalFile() if url.isLocalFile() else destination
        QTimer.singleShot(0, lambda: self._on_drop(destination))
