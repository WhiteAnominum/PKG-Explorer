# SPDX-License-Identifier: GPL-2.0-or-later
"""The file list, and dragging items out of it."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from PySide6.QtCore import QMimeData, Qt, QTimer, QUrl
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import QAbstractItemView, QTreeView

if TYPE_CHECKING:
    from .window import MainWindow

# KDE: how long after a drag ends we still accept the extraction request from the drop target
# (the D-Bus call may arrive right after the drag finishes).
DND_GRACE_MS = 10_000


class PkgTreeView(QTreeView):
    def __init__(self, window: MainWindow):
        super().__init__()
        self.window_ = window
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)
        self.setUniformRowHeights(True)
        self.setSortingEnabled(True)
        self.setAlternatingRowColors(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)

    def startDrag(self, supported_actions):
        window = self.window_
        nodes = window.selected_nodes()
        if not nodes:
            return
        if window.kde_dnd is not None:
            # KDE: the drop target tells us where the items were dropped and we extract there
            # (see kde_dnd.py).
            window.dragged_nodes = nodes
            mime = window.kde_dnd.mime_data()
        else:
            # Other desktops: there is no protocol for this, so we extract to a temporary folder
            # first and drag the finished files.
            temp = window.extract_to_temp(nodes)
            if temp is None:
                return
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(os.path.join(temp, n.name)) for n in nodes])
        drag = QDrag(self)
        drag.setMimeData(mime)
        icon = (
            self.model()
            .index(self.currentIndex().row(), 0, self.currentIndex().parent())
            .data(Qt.DecorationRole)
        )
        if icon is not None:
            drag.setPixmap(icon.pixmap(32, 32))
        drag.exec(Qt.CopyAction)
        if window.kde_dnd is not None:
            # If nobody requests the extraction (drag cancelled or dropped somewhere unsupported),
            # forget the items so that another program cannot request them later.
            pending = window.dragged_nodes
            QTimer.singleShot(DND_GRACE_MS, lambda: window.forget_drag(pending))
