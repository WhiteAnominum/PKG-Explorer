# SPDX-License-Identifier: GPL-2.0-or-later
"""Window tests (offscreen Qt)."""

import pytest

pytest.importorskip("PySide6.QtWidgets")

from conftest import sample_tree  # noqa: E402
from PySide6.QtCore import QItemSelectionModel  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from pkg_explorer import kde_dnd  # noqa: E402
from pkg_explorer.gui.extraction import run_extraction  # noqa: E402
from pkg_explorer.gui.window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, monkeypatch):
    monkeypatch.setattr(kde_dnd, "is_available", lambda: False)
    w = MainWindow()
    yield w
    w.close()


def rows(proxy, parent=None):
    from PySide6.QtCore import QModelIndex

    parent = parent or QModelIndex()
    return [proxy.index(r, 0, parent).data() for r in range(proxy.rowCount(parent))]


def test_open_shows_tree_and_info(window, make_pkg):
    path, _ = make_pkg()
    window.open_pkg(path)
    assert rows(window.proxy) == [
        "data",
        "emptydir",
        "sce_sys",
        "eboot.bin",
        "version.xml",
    ]  # folders first
    assert "Test Package" in window.info_label.text() and "TEST00001" in window.info_label.text()
    assert window.act_extract.isEnabled()


def test_open_invalid_file_shows_error(window, tmp_path, monkeypatch):
    bad = tmp_path / "bad.pkg"
    bad.write_bytes(b"x" * 5000)
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a: errors.append(a))
    window.open_pkg(str(bad))
    assert errors and window.pkg is None


def test_selection_and_extraction(window, make_pkg, tmp_path):
    path, _ = make_pkg()
    window.open_pkg(path)
    proxy = window.proxy
    eboot = next(
        proxy.index(r, 0)
        for r in range(proxy.rowCount())
        if proxy.index(r, 0).data() == "eboot.bin"
    )
    window.tree.selectionModel().select(
        eboot, QItemSelectionModel.Select | QItemSelectionModel.Rows
    )
    nodes = window.selected_nodes()
    assert [n.name for n in nodes] == ["eboot.bin"]
    assert run_extraction(window, window.pkg, [(n, str(tmp_path)) for n in nodes])
    assert (tmp_path / "eboot.bin").read_bytes() == sample_tree()["eboot.bin"]


def test_dropped_extraction_and_forget(window, make_pkg, tmp_path):
    path, _ = make_pkg()
    window.open_pkg(path)
    window.dragged_nodes = [window.pkg.find("data/sub")]
    window.extract_dropped(str(tmp_path))  # what KDE calls via D-Bus on drop
    assert (tmp_path / "sub/deep/x.txt").read_bytes() == b"hello"
    assert window.dragged_nodes == []

    pending = [window.pkg.find("eboot.bin")]
    window.dragged_nodes = pending
    window.forget_drag(pending)  # the grace period ended without a request from the target
    window.extract_dropped(str(tmp_path))
    assert not (tmp_path / "eboot.bin").exists()


def test_filter(window, make_pkg, app):
    path, _ = make_pkg()
    window.open_pkg(path)
    window.filter_edit.setText("x.txt")
    app.processEvents()
    assert rows(window.proxy) == ["data"]


def test_progress_dialog_is_only_touched_from_the_gui_thread(make_pkg, tmp_path, monkeypatch, app):
    # Widgets must not be used from the extraction thread (it crashed at random before).
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QWidget

    from pkg_explorer.gui import extraction
    from pkg_explorer.pkg import Pkg

    gui_thread = QThread.currentThread()
    calls = []

    class SpyDialog(extraction.QProgressDialog):
        def setLabelText(self, text):
            calls.append(QThread.currentThread() is gui_thread)
            super().setLabelText(text)

        def setValue(self, value):
            calls.append(QThread.currentThread() is gui_thread)
            super().setValue(value)

    monkeypatch.setattr(extraction, "QProgressDialog", SpyDialog)
    path, _ = make_pkg()
    with Pkg(path) as pkg:
        assert run_extraction(QWidget(), pkg, [(pkg.find("data"), str(tmp_path))])
    assert calls and all(calls)
