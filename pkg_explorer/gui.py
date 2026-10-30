# SPDX-License-Identifier: GPL-2.0-or-later
"""Graphical interface (Qt / PySide6)."""

from __future__ import annotations

import atexit
import contextlib
import os
import shutil
import sys
import tempfile
import threading

from PySide6.QtCore import (
    QAbstractItemModel,
    QEventLoop,
    QLibraryInfo,
    QLocale,
    QMimeData,
    QModelIndex,
    QObject,
    QSettings,
    QSortFilterProxyModel,
    Qt,
    QThread,
    QTimer,
    QTranslator,
    QUrl,
    Signal,
)
from PySide6.QtGui import QAction, QDesktopServices, QDrag, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QFileIconProvider,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressDialog,
    QSizePolicy,
    QStyle,
    QToolBar,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from . import __version__, desktop, kde_dnd
from .cli import human_size
from .i18n import LANGUAGE, _
from .pkg import ExtractionCancelled, Node, Pkg, PkgError

# Dragging out and opening with a double-click extract to a temporary folder first;
# above this size we ask for confirmation.
LARGE_TEMP_EXTRACT = 512 * 1024 * 1024

# KDE: how long after a drag ends we still accept the extraction request from the drop target
# (the D-Bus call may arrive right after the drag finishes).
DND_GRACE_MS = 10_000

KIND_LABELS = {"game": _("Game"), "patch": _("Update"), "dlc": "DLC"}

# Default "no parent" index for the model methods (avoids a call in default arguments).
NO_INDEX = QModelIndex()

_temp_dirs: list[str] = []


@atexit.register
def _cleanup_temp_dirs() -> None:
    for d in _temp_dirs:
        shutil.rmtree(d, ignore_errors=True)


def make_temp_dir() -> str:
    d = tempfile.mkdtemp(prefix="pkg-explorer-")
    _temp_dirs.append(d)
    return d


# ---------------------------------------------------------------------------- model


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


# ---------------------------------------------------------------------------- extraction


class ExtractWorker(QObject):
    progress = Signal(int)  # per mille (0..1000)
    current = Signal(str)
    done = Signal(bool, str)  # success, error message

    def __init__(self, pkg: Pkg, jobs: list[tuple[Node, str]]):
        super().__init__()
        self.pkg = pkg
        self.jobs = jobs
        self.cancel = threading.Event()
        self.total = max(1, sum(node.total_size() for node, _ in jobs))
        self._written = 0

    def _on_chunk(self, n: int) -> None:
        self._written += n
        self.progress.emit(int(self._written * 1000 / self.total))

    def run(self) -> None:
        try:
            for node, dest in self.jobs:
                self.current.emit(node.path or _("(everything)"))
                self.pkg.extract(node, dest, self._on_chunk, self.cancel)
        except ExtractionCancelled:
            self.done.emit(False, "")
        except Exception as exc:  # noqa: BLE001 - any error is shown to the user
            self.done.emit(False, str(exc))
        else:
            self.done.emit(True, "")


def run_extraction(parent: QWidget, pkg: Pkg, jobs: list[tuple[Node, str]]) -> bool:
    """Extracts in another thread while showing a progress dialog. Returns True if it finished."""
    worker = ExtractWorker(pkg, jobs)
    thread = QThread(parent)
    worker.moveToThread(thread)

    dialog = QProgressDialog(_("Extracting…"), _("Cancel"), 0, 1000, parent)
    dialog.setWindowTitle(_("Extracting"))
    dialog.setWindowModality(Qt.WindowModal)
    dialog.setMinimumDuration(300)
    dialog.setMinimumWidth(480)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.canceled.connect(worker.cancel.set)

    result: dict = {}
    loop = QEventLoop()

    def finished(ok: bool, error: str) -> None:
        result["ok"], result["error"] = ok, error
        thread.quit()

    worker.progress.connect(dialog.setValue)
    worker.current.connect(
        lambda path: dialog.setLabelText(_("Extracting:\n{path}").format(path=path))
    )
    worker.done.connect(finished)
    thread.started.connect(worker.run)
    thread.finished.connect(loop.quit)
    thread.start()
    loop.exec()
    dialog.close()
    thread.wait()

    if result.get("error"):
        QMessageBox.critical(parent, _("Extraction error"), result["error"])
    return bool(result.get("ok"))


# ---------------------------------------------------------------------------- tree view


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


# ---------------------------------------------------------------------------- window


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.pkg: Pkg | None = None
        self.model: PkgTreeModel | None = None
        self.dragged_nodes: list[Node] = []
        self._extracting = False
        self.kde_dnd: kde_dnd.DndExtractService | None = None
        if kde_dnd.is_available():
            service = kde_dnd.DndExtractService(self.extract_dropped, self)
            if service.registered:
                self.kde_dnd = service
        self.setWindowTitle("PKG Explorer")
        self.resize(900, 640)
        self.setAcceptDrops(True)

        style = self.style()
        self.act_open = QAction(
            style.standardIcon(QStyle.SP_DialogOpenButton), _("Open PKG…"), self
        )
        self.act_open.setShortcut(QKeySequence.Open)
        self.act_open.triggered.connect(self.choose_pkg)
        self.act_extract = QAction(
            style.standardIcon(QStyle.SP_DialogSaveButton), _("Extract selected…"), self
        )
        self.act_extract.setShortcut("Ctrl+E")
        self.act_extract.triggered.connect(self.extract_selected)
        self.act_extract_all = QAction(
            style.standardIcon(QStyle.SP_DirIcon), _("Extract all…"), self
        )
        self.act_extract_all.triggered.connect(self.extract_all)
        self.act_integrate = QAction(_("Open .pkg files with PKG Explorer"), self)
        self.act_integrate.setCheckable(True)
        self.act_integrate.setChecked(desktop.is_installed())
        self.act_integrate.toggled.connect(self.toggle_integration)
        self.act_about = QAction(_("About"), self)
        self.act_about.triggered.connect(self.about)

        toolbar = QToolBar(_("Main"))
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toolbar.addActions([self.act_open, self.act_extract, self.act_extract_all])
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)
        menu = QMenu(self)
        if desktop.is_supported():
            menu.addAction(self.act_integrate)
            menu.addSeparator()
        menu.addAction(self.act_about)
        menu_button = QToolButton()
        if QIcon.hasThemeIcon("application-menu"):
            menu_button.setIcon(QIcon.fromTheme("application-menu"))
        else:  # without an icon theme (e.g. AppImage outside KDE) Qt's fallback icon is its logo
            menu_button.setText("☰")
        menu_button.setToolTip("Menu")
        menu_button.setPopupMode(QToolButton.InstantPopup)
        menu_button.setMenu(menu)
        toolbar.addWidget(menu_button)
        self.addToolBar(toolbar)

        # Header with the PKG icon and information.
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(96, 96)
        self.icon_label.setScaledContents(True)
        self.info_label = QLabel()
        self.info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info_label.setWordWrap(True)
        header = QHBoxLayout()
        header.addWidget(self.icon_label)
        header.addWidget(self.info_label, 1)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText(_("Filter by name…"))
        self.filter_edit.setClearButtonEnabled(True)
        self.filter_edit.textChanged.connect(self.apply_filter)

        self.tree = PkgTreeView(self)
        self.tree.doubleClicked.connect(self.open_item)
        self.tree.customContextMenuRequested.connect(self.context_menu)

        layout = QVBoxLayout()
        layout.addLayout(header)
        layout.addWidget(self.filter_edit)
        layout.addWidget(self.tree, 1)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.show_placeholder()

    # ---- state ----

    def show_placeholder(self) -> None:
        self.icon_label.hide()
        self.info_label.setText(
            _("<h3>No PKG open</h3>Use <b>Open PKG…</b> or drag a .pkg file here.")
        )
        self.set_actions_enabled(False)

    def set_actions_enabled(self, enabled: bool) -> None:
        for act in (self.act_extract, self.act_extract_all):
            act.setEnabled(enabled)
        self.filter_edit.setEnabled(enabled)

    def choose_pkg(self) -> None:
        start = os.path.dirname(self.pkg.path) if self.pkg else ""
        path, _filter = QFileDialog.getOpenFileName(
            self, _("Open PKG"), start, _("PS4 packages (*.pkg *.PKG);;All files (*)")
        )
        if path:
            self.open_pkg(path)

    def open_pkg(self, path: str) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            pkg = Pkg(path)
        except (PkgError, OSError) as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, _("Could not open"), f"{os.path.basename(path)}\n\n{exc}")
            return
        QApplication.restoreOverrideCursor()

        if self.pkg is not None:
            self.pkg.close()
        self.pkg = pkg
        self.model = PkgTreeModel(pkg.root, self)
        self.proxy = SortProxy(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setRecursiveFilteringEnabled(True)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(0)
        self.tree.setModel(self.proxy)
        self.tree.sortByColumn(0, Qt.AscendingOrder)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setStretchLastSection(False)
        self.tree.setColumnWidth(1, 110)
        self.tree.setColumnWidth(2, 130)
        self.tree.selectionModel().selectionChanged.connect(self.update_status)
        self.filter_edit.clear()

        self.update_header()
        self.set_actions_enabled(True)
        self.setWindowTitle(f"{os.path.basename(path)} — PKG Explorer")
        self.update_status()

    def update_header(self) -> None:
        pkg = self.pkg
        icon = pkg.find("sce_sys/icon0.png")
        pixmap = QPixmap()
        if icon is not None and not icon.is_dir:
            with contextlib.suppress(PkgError):
                pixmap.loadFromData(pkg.read_file(icon))
        self.icon_label.setPixmap(pixmap)
        self.icon_label.setVisible(not pixmap.isNull())

        version = pkg.sfo.get("APP_VER") or pkg.sfo.get("VERSION") or "-"
        files = [n for n in pkg.root.walk() if not n.is_dir]
        rows = [
            ("Title ID", pkg.title_id),
            (_("Type"), KIND_LABELS.get(pkg.kind, pkg.kind)),
            (_("Version"), version),
            ("Content ID", pkg.content_id),
            (
                _("Contents"),
                _("{count} files, {size}").format(
                    count=len(files), size=human_size(sum(n.size for n in files))
                ),
            ),
        ]
        table = "".join(
            f"<tr><td style='padding-right:12px; color:gray'>{k}</td><td>{v}</td></tr>"
            for k, v in rows
        )
        title = pkg.title or os.path.basename(pkg.path)
        self.info_label.setText(f"<h3 style='margin:0'>{title}</h3><table>{table}</table>")

    def update_status(self, *_args) -> None:
        nodes = self.selected_nodes()
        if nodes:
            size = sum(n.total_size() for n in nodes)
            self.statusBar().showMessage(
                _("{count} item(s) selected, {size}").format(
                    count=len(nodes), size=human_size(size)
                )
            )
        else:
            self.statusBar().showMessage(self.pkg.path if self.pkg else "")

    def apply_filter(self, text: str) -> None:
        self.proxy.setFilterFixedString(text)
        if text:
            self.tree.expandAll()

    # ---- selection ----

    def selected_nodes(self) -> list[Node]:
        if self.model is None:
            return []
        nodes = [
            self.model.node(self.proxy.mapToSource(i))
            for i in self.tree.selectionModel().selectedRows(0)
        ]
        # If a folder and something inside it are both selected, keep only the folder.
        chosen = set(map(id, nodes))

        def inside_selected(node: Node) -> bool:
            p = node.parent
            while p is not None:
                if id(p) in chosen:
                    return True
                p = p.parent
            return False

        return [n for n in nodes if not inside_selected(n)]

    # ---- actions ----

    def confirm_overwrite(self, nodes: list[Node], dest: str) -> bool:
        existing = [n.name for n in nodes if os.path.exists(os.path.join(dest, n.name))]
        if not existing:
            return True
        names = "\n".join(existing[:10]) + ("\n…" if len(existing) > 10 else "")
        answer = QMessageBox.question(
            self,
            _("Replace?"),
            _("Already exists in {dest}:\n\n{names}\n\nReplace?").format(dest=dest, names=names),
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def extract_nodes(self, nodes: list[Node]) -> None:
        if not nodes:
            QMessageBox.information(self, _("Extract"), _("Select one or more files or folders."))
            return
        dest = QFileDialog.getExistingDirectory(
            self, _("Extract to…"), os.path.dirname(self.pkg.path)
        )
        if not dest or not self.confirm_overwrite(nodes, dest):
            return
        if run_extraction(self, self.pkg, [(n, dest) for n in nodes]):
            self.statusBar().showMessage(_("Extracted to {path}").format(path=dest), 8000)

    def extract_selected(self) -> None:
        self.extract_nodes(self.selected_nodes())

    def extract_all(self) -> None:
        dest = QFileDialog.getExistingDirectory(
            self, _("Extract all to…"), os.path.dirname(self.pkg.path)
        )
        if not dest:
            return
        target = os.path.join(dest, self.pkg.output_folder_name)
        if os.path.exists(target):
            answer = QMessageBox.question(
                self,
                _("Folder already exists"),
                _(
                    "{path}\n\nalready exists. Extract over it (files with the same name will be "
                    "replaced)?"
                ).format(path=target),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        if run_extraction(self, self.pkg, [(self.pkg.root, target)]):
            self.statusBar().showMessage(_("Extracted to {path}").format(path=target), 8000)
            QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def forget_drag(self, nodes: list[Node]) -> None:
        if self.dragged_nodes is nodes:
            self.dragged_nodes = []

    def extract_dropped(self, dest: str) -> None:
        """Called (via D-Bus) when the dragged items are dropped on a folder in KDE."""
        nodes, self.dragged_nodes = self.dragged_nodes, []
        if not nodes or self.pkg is None:
            return
        if self._extracting:
            QMessageBox.information(
                self, _("Please wait"), _("An extraction is already in progress.")
            )
            return
        if not os.path.isdir(dest):
            QMessageBox.warning(
                self,
                _("Unsupported destination"),
                _("Can only extract to local folders.\n\nDestination: {path}").format(path=dest),
            )
            return
        if not self.confirm_overwrite(nodes, dest):
            return
        self._extracting = True
        try:
            if run_extraction(self, self.pkg, [(n, dest) for n in nodes]):
                self.statusBar().showMessage(_("Extracted to {path}").format(path=dest), 8000)
        finally:
            self._extracting = False

    def extract_to_temp(self, nodes: list[Node]) -> str | None:
        size = sum(n.total_size() for n in nodes)
        if size > LARGE_TEMP_EXTRACT:
            answer = QMessageBox.question(
                self,
                _("Large file"),
                _(
                    "This will first extract {size} to a temporary folder. Continue?\n\n"
                    "Tip: for large files, use “Extract selected…”."
                ).format(size=human_size(size)),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return None
        temp = make_temp_dir()
        if not run_extraction(self, self.pkg, [(n, temp) for n in nodes]):
            return None
        return temp

    def open_item(self, proxy_index: QModelIndex) -> None:
        node = self.model.node(self.proxy.mapToSource(proxy_index))
        if node.is_dir:
            return  # QTreeView itself expands/collapses it
        temp = self.extract_to_temp([node])
        if temp is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.join(temp, node.name)))

    def context_menu(self, pos) -> None:
        index = self.tree.indexAt(pos)
        if not index.isValid() or self.model is None:
            return
        node = self.model.node(self.proxy.mapToSource(index))
        menu = QMenu(self)
        if not node.is_dir:
            menu.addAction(_("Open"), lambda: self.open_item(index))
        menu.addAction(self.act_extract)
        menu.addSeparator()
        menu.addAction(_("Copy path"), lambda: QApplication.clipboard().setText(node.path))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def toggle_integration(self, enabled: bool) -> None:
        try:
            if enabled:
                desktop.install()
                self.statusBar().showMessage(
                    _("Done: PS4 .pkg files now open with PKG Explorer."), 8000
                )
            else:
                desktop.uninstall()
                self.statusBar().showMessage(_(".pkg file association removed."), 8000)
        except OSError as exc:
            QMessageBox.critical(self, _("Error"), str(exc))

    def ask_integration(self) -> None:
        """Asks (only once) whether .pkg files should open with this program. Used by the
        AppImage and the Flatpak (see desktop.py)."""
        settings = QSettings()
        if settings.value("integration/asked", False, bool):
            return
        settings.setValue("integration/asked", True)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(_("Open .pkg files"))
        box.setText(
            _("Do you want PS4 .pkg files to open with PKG Explorer when you double-click them?")
        )
        extra = (
            ""
            if desktop.in_flatpak()
            else _("This also adds a shortcut to the applications menu. ")
        )
        box.setInformativeText(extra + _("You can change this later in the window's ☰ menu."))
        yes = box.addButton(_("Yes"), QMessageBox.YesRole)
        box.addButton(_("Not now"), QMessageBox.NoRole)
        box.exec()
        if box.clickedButton() is yes:
            self.act_integrate.setChecked(True)

    def about(self) -> None:
        QMessageBox.about(
            self,
            _("About PKG Explorer"),
            f"<h3>PKG Explorer {__version__}</h3>"
            f"<p>{_('Browse the contents of PS4 PKGs and extract only what you need.')}</p>"
            "<p>"
            + _("PKG/PFS reading adapted from {extractor}, based on {emulator}.").format(
                extractor="<a href='https://github.com/AzaharPlus/shadPS4Plus'>"
                "ShadPs4Plus PKG Extractor</a>",
                emulator="<a href='https://github.com/shadps4-emu/shadPS4'>shadPS4</a>",
            )
            + f"</p><p>{_('License: GPL-2.0-or-later.')}<br>"
            "<a href='https://github.com/WhiteAnominum/PKG-Explorer'>"
            "github.com/WhiteAnominum/PKG-Explorer</a></p>",
        )

    # ---- dropping a .pkg onto the window ----

    def dragEnterEvent(self, event):
        if event.source() is None and event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile():
                self.open_pkg(url.toLocalFile())
                break

    def closeEvent(self, event):
        if self.pkg is not None:
            self.pkg.close()
        super().closeEvent(event)


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


if __name__ == "__main__":
    sys.exit(main())
