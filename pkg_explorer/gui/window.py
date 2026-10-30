# SPDX-License-Identifier: GPL-2.0-or-later
"""Main window: header with the PKG info, filter, file list and actions."""

from __future__ import annotations

import contextlib
import os
import threading
from pathlib import Path

from PySide6.QtCore import QModelIndex, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QStyle,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import __version__, desktop, kde_dnd, shadps4
from ..i18n import _
from ..pkg import Node, Pkg, PkgError
from ..util import human_size
from .extraction import make_temp_dir, run_extraction
from .model import PkgTreeModel, SortProxy
from .tree import PkgTreeView

# Dragging out and opening with a double-click extract to a temporary folder first;
# above this size we ask for confirmation.
LARGE_TEMP_EXTRACT = 512 * 1024 * 1024

KIND_LABELS = {"game": _("Game"), "patch": _("Update"), "dlc": "DLC"}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.pkg: Pkg | None = None
        self.model: PkgTreeModel | None = None
        self.dragged_nodes: list[Node] = []
        self._extracting = False
        self._cancel_extraction: threading.Event | None = None
        self._close_when_done = False
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
        self.act_install = QAction(
            QIcon.fromTheme("run-install", style.standardIcon(QStyle.SP_ArrowDown)),
            _("Install in shadPS4"),
            self,
        )
        self.act_install.triggered.connect(self.install_shadps4)
        self.act_shadps4_folder = QAction(_("shadPS4 folder…"), self)
        self.act_shadps4_folder.triggered.connect(self.choose_shadps4_folder)
        self.act_integrate = QAction(_("Open .pkg files with PKG Explorer"), self)
        self.act_integrate.setCheckable(True)
        self.act_integrate.setChecked(desktop.is_installed())
        self.act_integrate.toggled.connect(self.toggle_integration)
        self.act_about = QAction(_("About"), self)
        self.act_about.triggered.connect(self.about)

        toolbar = QToolBar(_("Main"))
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toolbar.addActions(
            [self.act_open, self.act_extract, self.act_extract_all, self.act_install]
        )
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(spacer)
        menu = QMenu(self)
        if desktop.is_supported():
            menu.addAction(self.act_integrate)
            menu.addSeparator()
        menu.addAction(self.act_shadps4_folder)
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
        for act in (self.act_extract, self.act_extract_all, self.act_install):
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
        if self._extracting:  # the current PKG is still being read by the extraction thread
            QMessageBox.information(
                self, _("Please wait"), _("An extraction is already in progress.")
            )
            return
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

    def _extract(self, jobs: list[tuple[Node, str]]) -> bool:
        """Every extraction goes through here: only one at a time, actions disabled meanwhile,
        and it can be cancelled when the window is closed (see closeEvent)."""
        if self._extracting:
            QMessageBox.information(
                self, _("Please wait"), _("An extraction is already in progress.")
            )
            return False
        self._extracting = True
        self._cancel_extraction = threading.Event()
        self.set_actions_enabled(False)
        self.act_open.setEnabled(False)
        try:
            return run_extraction(self, self.pkg, jobs, self._cancel_extraction)
        finally:
            self._extracting = False
            self._cancel_extraction = None
            self.set_actions_enabled(self.pkg is not None)
            self.act_open.setEnabled(True)
            if self._close_when_done:
                QTimer.singleShot(0, self.close)

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
        if self._extract([(n, dest) for n in nodes]):
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
        if self._extract([(self.pkg.root, target)]):
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
        if not os.path.isdir(dest):
            QMessageBox.warning(
                self,
                _("Unsupported destination"),
                _("Can only extract to local folders.\n\nDestination: {path}").format(path=dest),
            )
            return
        if not self.confirm_overwrite(nodes, dest):
            return
        if self._extract([(n, dest) for n in nodes]):
            self.statusBar().showMessage(_("Extracted to {path}").format(path=dest), 8000)

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
        if not self._extract([(n, temp) for n in nodes]):
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

    # ---- installing in shadPS4 (rules in shadps4.py) ----

    def shadps4_config(self) -> shadps4.Shadps4Config | None:
        """shadPS4's settings, found automatically or in the folder chosen in the ☰ menu."""
        user_dir = shadps4.find_user_dir(QSettings().value("shadps4/user_dir", "", str))
        if user_dir is None:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Information)
            box.setWindowTitle(_("Install in shadPS4"))
            box.setText(_("shadPS4's settings were not found."))
            box.setInformativeText(
                _(
                    "Open shadPS4 at least once, or choose its user folder (the one with "
                    'config.json, e.g. the "user" folder of a portable shadPS4).'
                )
            )
            choose = box.addButton(_("Choose folder…"), QMessageBox.AcceptRole)
            box.addButton(QMessageBox.Cancel)
            box.exec()
            if box.clickedButton() is not choose or not self.choose_shadps4_folder():
                return None
            user_dir = shadps4.find_user_dir(QSettings().value("shadps4/user_dir", "", str))
        try:
            return shadps4.load_config(user_dir)
        except shadps4.Shadps4Error as exc:
            QMessageBox.critical(self, _("Could not read shadPS4's settings"), str(exc))
            return None

    def choose_shadps4_folder(self) -> bool:
        settings = QSettings()
        current = settings.value("shadps4/user_dir", "", str) or str(
            shadps4.find_user_dir() or Path.home()
        )
        folder = QFileDialog.getExistingDirectory(
            self, _("shadPS4 user folder (with config.json)"), current
        )
        if not folder:
            return False
        if shadps4.find_user_dir(folder) is None:
            QMessageBox.warning(
                self,
                _("shadPS4 folder"),
                _("There is no config.json in {path}.").format(path=folder),
            )
            return False
        settings.setValue("shadps4/user_dir", folder)
        self.statusBar().showMessage(_("shadPS4 folder: {path}").format(path=folder), 8000)
        return True

    def install_shadps4(self) -> None:
        pkg = self.pkg
        config = self.shadps4_config()
        if config is None:
            return
        if pkg.kind != "dlc" and not config.install_dirs:
            QMessageBox.information(
                self,
                _("Install in shadPS4"),
                _("shadPS4 has no game folder yet. Add one in shadPS4's settings first."),
            )
            return
        plan = shadps4.plan_install(pkg, config)
        if plan.choices:
            names = [str(d) for d in plan.choices]
            folder, ok = QInputDialog.getItem(
                self, _("Install in shadPS4"), _("Game folder:"), names, 0, False
            )
            if not ok:
                return
            plan = shadps4.with_game_folder(plan, Path(folder))

        version = str(pkg.sfo.get("APP_VER") or pkg.sfo.get("VERSION") or "")
        notes = [_("Folder:") + f"\n{plan.target}"]
        if plan.kind != "game" and plan.base_game is None:
            notes.append(
                _("The base game {title_id} was not found in shadPS4's game folders.").format(
                    title_id=pkg.title_id
                )
            )
        if plan.target.exists():
            if plan.installed_version and version and version < plan.installed_version:
                notes.append(
                    _("Version {old} is installed, which is newer than this one ({new}).").format(
                        old=plan.installed_version, new=version
                    )
                )
            notes.append(_("The folder already exists: files with the same name will be replaced."))

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(_("Install in shadPS4"))
        what = KIND_LABELS.get(plan.kind, plan.kind) + (f" {version}" if version else "")
        box.setText(
            _("Install {title} ({what}) in shadPS4?").format(
                title=pkg.title or pkg.title_id, what=what
            )
        )
        box.setInformativeText("\n\n".join(notes))
        install = box.addButton(_("Install"), QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.exec()
        if box.clickedButton() is not install:
            return
        if self._extract([(pkg.root, str(plan.target))]):
            self.statusBar().showMessage(
                _("Installed in shadPS4: {path}").format(path=plan.target), 8000
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
        if self._extracting:
            # The extraction thread is still reading the PKG: cancel it, wait for it to stop
            # (in _extract) and only then close.
            answer = QMessageBox.question(
                self,
                _("Extraction in progress"),
                _("An extraction is in progress. Cancel it and close the window?"),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer == QMessageBox.Yes:
                self._close_when_done = True
                self._cancel_extraction.set()
            event.ignore()
            return
        if self.pkg is not None:
            self.pkg.close()
        super().closeEvent(event)
