# SPDX-License-Identifier: GPL-2.0-or-later
"""Interface gráfica (Qt / PySide6)."""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
import threading

from PySide6.QtCore import (
    QAbstractItemModel,
    QEventLoop,
    QMimeData,
    QModelIndex,
    QObject,
    QSettings,
    QSortFilterProxyModel,
    Qt,
    QThread,
    QTimer,
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
from .pkg import ExtractionCancelled, Node, Pkg, PkgError

# Arrastar para fora e abrir com duplo clique extraem para uma pasta temporária primeiro;
# acima deste tamanho pedimos confirmação.
LARGE_TEMP_EXTRACT = 512 * 1024 * 1024

KIND_LABELS = {"game": "Jogo base", "patch": "Atualização", "dlc": "DLC"}

_temp_dirs: list[str] = []


@atexit.register
def _cleanup_temp_dirs() -> None:
    for d in _temp_dirs:
        shutil.rmtree(d, ignore_errors=True)


def make_temp_dir() -> str:
    d = tempfile.mkdtemp(prefix="pkg-explorer-")
    _temp_dirs.append(d)
    return d


# ---------------------------------------------------------------------------- modelo


class PkgTreeModel(QAbstractItemModel):
    COLUMNS = ("Nome", "Tamanho", "Tipo")
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

    def index(self, row, column, parent=QModelIndex()):
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

    def rowCount(self, parent=QModelIndex()):
        if parent.column() > 0:
            return 0
        node = self.node(parent)
        return len(self._kids(node)) if node.is_dir else 0

    def hasChildren(self, parent=QModelIndex()):
        node = self.node(parent)
        return node.is_dir and bool(node.children)

    def columnCount(self, parent=QModelIndex()):
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
                    return "Pasta"
                ext = os.path.splitext(node.name)[1].lstrip(".").upper()
                return f"Arquivo {ext}" if ext else "Arquivo"
        elif role == Qt.DecorationRole and col == 0:
            return self._dir_icon if node.is_dir else self._file_icon
        elif role == Qt.TextAlignmentRole and col == 1:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        elif role == Qt.ToolTipRole and col == 0:
            return node.path
        elif role == self.SORT_ROLE:
            # Pastas sempre antes dos arquivos.
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
        if self.sortOrder() == Qt.DescendingOrder:
            # Mantém as pastas em cima mesmo na ordem decrescente.
            if a[0] != b[0]:
                return a[0] > b[0]
        return a < b


# ---------------------------------------------------------------------------- extração


class ExtractWorker(QObject):
    progress = Signal(int)  # permilagem (0..1000)
    current = Signal(str)
    done = Signal(bool, str)  # sucesso, mensagem de erro

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
                self.current.emit(node.path or "(tudo)")
                self.pkg.extract(node, dest, self._on_chunk, self.cancel)
        except ExtractionCancelled:
            self.done.emit(False, "")
        except Exception as exc:  # noqa: BLE001 - mostramos qualquer erro ao usuário
            self.done.emit(False, str(exc))
        else:
            self.done.emit(True, "")


def run_extraction(parent: QWidget, pkg: Pkg, jobs: list[tuple[Node, str]]) -> bool:
    """Extrai em outra thread mostrando uma janela de progresso. Retorna True se concluiu."""
    worker = ExtractWorker(pkg, jobs)
    thread = QThread(parent)
    worker.moveToThread(thread)

    dialog = QProgressDialog("Extraindo…", "Cancelar", 0, 1000, parent)
    dialog.setWindowTitle("Extraindo")
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
    worker.current.connect(lambda path: dialog.setLabelText(f"Extraindo:\n{path}"))
    worker.done.connect(finished)
    thread.started.connect(worker.run)
    thread.finished.connect(loop.quit)
    thread.start()
    loop.exec()
    dialog.close()
    thread.wait()

    if result.get("error"):
        QMessageBox.critical(parent, "Erro na extração", result["error"])
    return bool(result.get("ok"))


# ---------------------------------------------------------------------------- árvore


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
            # KDE: o destino nos avisa onde soltaram e extraímos direto lá (ver kde_dnd.py).
            window.dragged_nodes = nodes
            mime = window.kde_dnd.mime_data()
        else:
            # Outros ambientes: não há protocolo para isso, então extraímos antes numa pasta
            # temporária e arrastamos os arquivos prontos.
            temp = window.extract_to_temp(nodes)
            if temp is None:
                return
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(os.path.join(temp, n.name)) for n in nodes])
        drag = QDrag(self)
        drag.setMimeData(mime)
        icon = self.model().index(self.currentIndex().row(), 0, self.currentIndex().parent()).data(
            Qt.DecorationRole
        )
        if icon is not None:
            drag.setPixmap(icon.pixmap(32, 32))
        drag.exec(Qt.CopyAction)


# ---------------------------------------------------------------------------- janela


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
        self.act_open = QAction(style.standardIcon(QStyle.SP_DialogOpenButton), "Abrir PKG…", self)
        self.act_open.setShortcut(QKeySequence.Open)
        self.act_open.triggered.connect(self.choose_pkg)
        self.act_extract = QAction(
            style.standardIcon(QStyle.SP_DialogSaveButton), "Extrair selecionados…", self
        )
        self.act_extract.setShortcut("Ctrl+E")
        self.act_extract.triggered.connect(self.extract_selected)
        self.act_extract_all = QAction(
            style.standardIcon(QStyle.SP_DirIcon), "Extrair tudo…", self
        )
        self.act_extract_all.triggered.connect(self.extract_all)
        self.act_integrate = QAction("Abrir arquivos .pkg com o PKG Explorer", self)
        self.act_integrate.setCheckable(True)
        self.act_integrate.setChecked(desktop.is_installed())
        self.act_integrate.toggled.connect(self.toggle_integration)
        self.act_about = QAction("Sobre", self)
        self.act_about.triggered.connect(self.about)

        toolbar = QToolBar("Principal")
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
        menu_button.setIcon(QIcon.fromTheme("application-menu", style.standardIcon(QStyle.SP_TitleBarMenuButton)))
        menu_button.setToolTip("Menu")
        menu_button.setPopupMode(QToolButton.InstantPopup)
        menu_button.setMenu(menu)
        toolbar.addWidget(menu_button)
        self.addToolBar(toolbar)

        # Cabeçalho com ícone e informações do PKG.
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
        self.filter_edit.setPlaceholderText("Filtrar por nome…")
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

    # ---- estado ----

    def show_placeholder(self) -> None:
        self.icon_label.hide()
        self.info_label.setText(
            "<h3>Nenhum PKG aberto</h3>Use <b>Abrir PKG…</b> ou arraste um arquivo .pkg para cá."
        )
        self.set_actions_enabled(False)

    def set_actions_enabled(self, enabled: bool) -> None:
        for act in (self.act_extract, self.act_extract_all):
            act.setEnabled(enabled)
        self.filter_edit.setEnabled(enabled)

    def choose_pkg(self) -> None:
        start = os.path.dirname(self.pkg.path) if self.pkg else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Abrir PKG", start, "Pacotes PS4 (*.pkg *.PKG);;Todos os arquivos (*)"
        )
        if path:
            self.open_pkg(path)

    def open_pkg(self, path: str) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            pkg = Pkg(path)
        except (PkgError, OSError) as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Não foi possível abrir", f"{os.path.basename(path)}\n\n{exc}")
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
            try:
                pixmap.loadFromData(pkg.read_file(icon))
            except PkgError:
                pass
        self.icon_label.setPixmap(pixmap)
        self.icon_label.setVisible(not pixmap.isNull())

        version = pkg.sfo.get("APP_VER") or pkg.sfo.get("VERSION") or "-"
        files = [n for n in pkg.root.walk() if not n.is_dir]
        rows = [
            ("Title ID", pkg.title_id),
            ("Tipo", KIND_LABELS.get(pkg.kind, pkg.kind)),
            ("Versão", version),
            ("Content ID", pkg.content_id),
            ("Conteúdo", f"{len(files)} arquivos, {human_size(sum(n.size for n in files))}"),
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
            self.statusBar().showMessage(f"{len(nodes)} item(ns) selecionado(s), {human_size(size)}")
        else:
            self.statusBar().showMessage(self.pkg.path if self.pkg else "")

    def apply_filter(self, text: str) -> None:
        self.proxy.setFilterFixedString(text)
        if text:
            self.tree.expandAll()

    # ---- seleção ----

    def selected_nodes(self) -> list[Node]:
        if self.model is None:
            return []
        nodes = [
            self.model.node(self.proxy.mapToSource(i))
            for i in self.tree.selectionModel().selectedRows(0)
        ]
        # Se uma pasta e algo dentro dela estão selecionados, fica só a pasta.
        chosen = set(map(id, nodes))

        def inside_selected(node: Node) -> bool:
            p = node.parent
            while p is not None:
                if id(p) in chosen:
                    return True
                p = p.parent
            return False

        return [n for n in nodes if not inside_selected(n)]

    # ---- ações ----

    def confirm_overwrite(self, nodes: list[Node], dest: str) -> bool:
        existing = [n.name for n in nodes if os.path.exists(os.path.join(dest, n.name))]
        if not existing:
            return True
        names = "\n".join(existing[:10]) + ("\n…" if len(existing) > 10 else "")
        answer = QMessageBox.question(
            self,
            "Substituir?",
            f"Já existe(m) em {dest}:\n\n{names}\n\nSubstituir?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def extract_nodes(self, nodes: list[Node]) -> None:
        if not nodes:
            QMessageBox.information(self, "Extrair", "Selecione um ou mais arquivos ou pastas.")
            return
        dest = QFileDialog.getExistingDirectory(self, "Extrair para…", os.path.dirname(self.pkg.path))
        if not dest or not self.confirm_overwrite(nodes, dest):
            return
        if run_extraction(self, self.pkg, [(n, dest) for n in nodes]):
            self.statusBar().showMessage(f"Extraído para {dest}", 8000)

    def extract_selected(self) -> None:
        self.extract_nodes(self.selected_nodes())

    def extract_all(self) -> None:
        dest = QFileDialog.getExistingDirectory(self, "Extrair tudo para…", os.path.dirname(self.pkg.path))
        if not dest:
            return
        target = os.path.join(dest, self.pkg.output_folder_name)
        if os.path.exists(target):
            answer = QMessageBox.question(
                self, "Pasta já existe",
                f"{target}\n\njá existe. Extrair por cima (arquivos com o mesmo nome serão substituídos)?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        if run_extraction(self, self.pkg, [(self.pkg.root, target)]):
            self.statusBar().showMessage(f"Extraído para {target}", 8000)
            QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    def extract_dropped(self, dest: str) -> None:
        """Chamado (via D-Bus) quando os itens arrastados são soltos numa pasta no KDE."""
        nodes, self.dragged_nodes = self.dragged_nodes, []
        if not nodes or self.pkg is None:
            return
        if self._extracting:
            QMessageBox.information(self, "Aguarde", "Já existe uma extração em andamento.")
            return
        if not os.path.isdir(dest):
            QMessageBox.warning(
                self, "Destino não suportado",
                f"Só é possível extrair para pastas locais.\n\nDestino: {dest}",
            )
            return
        if not self.confirm_overwrite(nodes, dest):
            return
        self._extracting = True
        try:
            if run_extraction(self, self.pkg, [(n, dest) for n in nodes]):
                self.statusBar().showMessage(f"Extraído para {dest}", 8000)
        finally:
            self._extracting = False

    def extract_to_temp(self, nodes: list[Node]) -> str | None:
        size = sum(n.total_size() for n in nodes)
        if size > LARGE_TEMP_EXTRACT:
            answer = QMessageBox.question(
                self, "Arquivo grande",
                f"Isso vai extrair {human_size(size)} para uma pasta temporária antes. Continuar?\n\n"
                "Dica: para arquivos grandes, use “Extrair selecionados…”.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
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
            return  # o próprio QTreeView expande/recolhe
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
            menu.addAction("Abrir", lambda: self.open_item(index))
        menu.addAction(self.act_extract)
        menu.addSeparator()
        menu.addAction("Copiar caminho", lambda: QApplication.clipboard().setText(node.path))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def toggle_integration(self, enabled: bool) -> None:
        try:
            if enabled:
                desktop.install()
                self.statusBar().showMessage(
                    "Pronto: arquivos .pkg de PS4 agora abrem com o PKG Explorer.", 8000
                )
            else:
                desktop.uninstall()
                self.statusBar().showMessage("Associação de arquivos .pkg removida.", 8000)
        except OSError as exc:
            QMessageBox.critical(self, "Erro", str(exc))

    def ask_integration(self) -> None:
        """Pergunta (uma vez só) se arquivos .pkg devem abrir com este programa. Usado no
        AppImage e no Flatpak (ver desktop.py)."""
        settings = QSettings()
        if settings.value("integration/asked", False, bool):
            return
        settings.setValue("integration/asked", True)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Abrir arquivos .pkg")
        box.setText("Quer que arquivos .pkg de PS4 abram com o PKG Explorer ao dar duplo clique?")
        extra = "" if desktop.in_flatpak() else "Também cria um atalho no menu de aplicativos. "
        box.setInformativeText(f"{extra}Dá para mudar isso depois no menu ☰ da janela.")
        yes = box.addButton("Sim", QMessageBox.YesRole)
        box.addButton("Agora não", QMessageBox.NoRole)
        box.exec()
        if box.clickedButton() is yes:
            self.act_integrate.setChecked(True)

    def about(self) -> None:
        QMessageBox.about(
            self,
            "Sobre o PKG Explorer",
            f"<h3>PKG Explorer {__version__}</h3>"
            "<p>Navegue pelo conteúdo de PKGs de PS4 e extraia só o que quiser.</p>"
            "<p>Leitura de PKG/PFS adaptada do "
            "<a href='https://github.com/AzaharPlus/shadPS4Plus'>ShadPs4Plus PKG Extractor</a>, "
            "baseado no <a href='https://github.com/shadps4-emu/shadPS4'>shadPS4</a>.</p>"
            "<p>Licença GPL-2.0-or-later.<br>"
            "<a href='https://github.com/WhiteAnominum/PKG-Explorer'>"
            "github.com/WhiteAnominum/PKG-Explorer</a></p>",
        )

    # ---- arrastar um .pkg para a janela ----

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
    app.setDesktopFileName(desktop.APP_ID)  # Wayland: liga a janela ao atalho/ícone
    app.setWindowIcon(QIcon(str(desktop.DATA_DIR / "pkg-explorer.svg")))
    try:
        desktop.repair_if_moved()
    except OSError:
        pass
    window = MainWindow()
    window.show()
    if desktop.should_ask_on_first_run():
        QTimer.singleShot(0, window.ask_integration)
    if len(argv) > 1:
        window.open_pkg(argv[1])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
