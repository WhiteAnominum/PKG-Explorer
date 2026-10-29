# SPDX-License-Identifier: GPL-2.0-or-later
"""Arrastar e soltar com extração direta no destino, no KDE.

Usa o mesmo protocolo do Ark: o arrasto leva só o endereço D-Bus deste programa
(application/x-kde-ark-dndextract-service / -path). Quando o usuário solta numa pasta do
Dolphin, da área de trabalho do Plasma ou de outro programa que usa o KIO, o KIO chama
org.kde.ark.DndExtract.extractSelectedFilesTo(pasta) aqui, e extraímos direto nessa pasta.
"""

from __future__ import annotations

import os
from typing import Callable

from PySide6.QtCore import ClassInfo, QMimeData, QObject, QTimer, QUrl, Slot
from PySide6.QtDBus import QDBusConnection

MIME_SERVICE = "application/x-kde-ark-dndextract-service"
MIME_PATH = "application/x-kde-ark-dndextract-path"
OBJECT_PATH = f"/DndExtract/{os.getpid()}"


def is_available() -> bool:
    if os.environ.get("PKG_EXPLORER_DND") == "temp":  # força o modo antigo (para testes)
        return False
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper().split(":")
    return "KDE" in desktop and QDBusConnection.sessionBus().isConnected()


@ClassInfo({"D-Bus Interface": "org.kde.ark.DndExtract"})
class DndExtractService(QObject):
    """Recebe do KIO a pasta onde os itens foram soltos e repassa para `on_drop`."""

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
        # O KIO espera esta chamada D-Bus terminar antes de liberar o Dolphin, então só
        # agendamos a extração e retornamos na hora.
        if "://" in destination:
            url = QUrl(destination)
            destination = url.toLocalFile() if url.isLocalFile() else destination
        QTimer.singleShot(0, lambda: self._on_drop(destination))
