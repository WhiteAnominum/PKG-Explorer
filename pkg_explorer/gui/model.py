# SPDX-License-Identifier: GPL-2.0-or-later
"""Tree model of the PKG contents for the QTreeView, and the proxy that sorts and filters it."""

from __future__ import annotations

import os

from PySide6.QtCore import QAbstractItemModel, QModelIndex, QObject, QSortFilterProxyModel, Qt
from PySide6.QtWidgets import QFileIconProvider

from ..i18n import _
from ..pkg import Node
from ..util import human_size

# Default "no parent" index for the model methods (avoids a call in default arguments).
NO_INDEX = QModelIndex()


class PkgTreeModel(QAbstractItemModel):
    COLUMNS = (_("Name"), _("Size"), _("Type"))
    SORT_ROLE = Qt.UserRole + 1

    def __init__(self, root: Node, parent: QObject | None = None):
        super().__init__(parent)
        self._root = root
        self._children: dict[int, list[Node]] = {}
        self._sizes: dict[int, int] = {}
        provider = QFileIconProvider()
        self._dir_icon = provider.icon(QFileIconProvider.Folder)
        self._file_icon = provider.icon(QFileIconProvider.File)

    def _kids(self, node: Node) -> list[Node]:
        kids = self._children.get(id(node))
        if kids is None:
            kids = list(node.children.values())
            self._children[id(node)] = kids
        return kids

    def _size(self, node: Node) -> int:
        size = self._sizes.get(id(node))
        if size is None:
            size = node.total_size()
            self._sizes[id(node)] = size
        return size

    def node(self, index: QModelIndex) -> Node:
        return index.internalPointer() if index.isValid() else self._root

    def index(self, row, column, parent=NO_INDEX):
        kids = self._kids(self.node(parent))
        if 0 <= row < len(kids):
            return self.createIndex(row, column, kids[row])
        return QModelIndex()

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        parent = index.internalPointer().parent
        if parent is None or parent is self._root:
            return QModelIndex()
        row = self._kids(parent.parent).index(parent)
        return self.createIndex(row, 0, parent)

    def rowCount(self, parent=NO_INDEX):
        if parent.column() > 0:
            return 0
        node = self.node(parent)
        return len(self._kids(node)) if node.is_dir else 0

    def hasChildren(self, parent=NO_INDEX):
        node = self.node(parent)
        return node.is_dir and bool(node.children)

    def columnCount(self, parent=NO_INDEX):
        return len(self.COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        node: Node = index.internalPointer()
        col = index.column()
        if role == Qt.DisplayRole:
            if col == 0:
                return node.name
            if col == 1:
                return human_size(self._size(node))
            if col == 2:
                if node.is_dir:
                    return _("Folder")
                ext = os.path.splitext(node.name)[1].lstrip(".").upper()
                return _("{ext} file").format(ext=ext) if ext else _("File")
        elif role == Qt.DecorationRole and col == 0:
            return self._dir_icon if node.is_dir else self._file_icon
        elif role == Qt.TextAlignmentRole and col == 1:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        elif role == Qt.ToolTipRole and col == 0:
            return node.path
        elif role == self.SORT_ROLE:
            # Folders always before files.
            key = (node.name.lower(), self._size(node), self.data(index, Qt.DisplayRole))[col]
            return (0 if node.is_dir else 1, key)
        return None

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled

    def mimeTypes(self):
        return ["text/uri-list"]


class SortProxy(QSortFilterProxyModel):
    def lessThan(self, left, right):
        a = left.data(PkgTreeModel.SORT_ROLE)
        b = right.data(PkgTreeModel.SORT_ROLE)
        # Keep folders on top even in descending order.
        if self.sortOrder() == Qt.DescendingOrder and a[0] != b[0]:
            return a[0] > b[0]
        return a < b
