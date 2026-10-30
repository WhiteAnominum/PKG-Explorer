# SPDX-License-Identifier: GPL-2.0-or-later
"""Window tests (offscreen Qt)."""

import gc

import pytest

pytest.importorskip("PySide6.QtWidgets")

import shiboken6  # noqa: E402
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
    # Destroy the window now, at a safe point. Left to Python's garbage collector, old windows
    # were destroyed at random moments, e.g. inside a later test's extraction event loop, and
    # Qt crashed (seen in CI).
    shiboken6.delete(w)
    gc.collect()


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


def _slow_reads(monkeypatch, delay=0.005):
    """Makes extraction slow enough for actions to happen while it is running."""
    import time

    from pkg_explorer.pkg import Pkg

    original = Pkg._decode_block

    def slow(self, raw):
        time.sleep(delay)
        return original(self, raw)

    monkeypatch.setattr(Pkg, "_decode_block", slow)


def test_many_extractions_in_a_row(window, make_pkg, tmp_path):
    path, _ = make_pkg()
    window.open_pkg(path)
    for i in range(30):
        dest = tmp_path / str(i)
        assert window._extract([(window.pkg.find("data"), str(dest))])
        assert (dest / "data/sub/deep/x.txt").read_bytes() == b"hello"
    # Each extraction destroys its dialog right away and its worker thread has ended.
    import threading

    from pkg_explorer.gui import extraction

    assert not window.findChildren(extraction.QProgressDialog)
    assert not [t for t in threading.enumerate() if t.name == "pkg-explorer-worker"]


def test_closing_during_extraction_cancels_waits_and_closes(
    window, make_pkg, tmp_path, monkeypatch, app
):
    from PySide6.QtCore import QTimer

    path, _ = make_pkg({"big.bin": bytes(range(256)) * 16384})  # 4 MB, 64 blocks
    window.open_pkg(path)
    window.show()
    _slow_reads(monkeypatch)
    errors = []
    monkeypatch.setattr(QMessageBox, "critical", lambda *a: errors.append(a))
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.Yes)

    QTimer.singleShot(50, window.close)
    assert not window._extract([(window.pkg.find("big.bin"), str(tmp_path))])  # cancelled
    app.processEvents()

    assert not errors  # no "seek of closed file" or similar
    assert not window.isVisible()
    assert not (tmp_path / "big.bin").exists()  # half-written file removed


def test_only_one_extraction_at_a_time(window, make_pkg, tmp_path, monkeypatch):
    from PySide6.QtCore import QTimer

    path, _ = make_pkg({"big.bin": bytes(range(256)) * 16384})
    window.open_pkg(path)
    _slow_reads(monkeypatch)
    refused = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: refused.append(a[1]))
    other, _ = make_pkg()

    def meanwhile():
        refused.append(window._extract([(window.pkg.find("big.bin"), str(tmp_path / "x"))]))
        window.open_pkg(other)  # must not replace the PKG being read

    QTimer.singleShot(30, meanwhile)
    assert window._extract([(window.pkg.find("big.bin"), str(tmp_path))])
    assert refused.count("Please wait") == 2 and False in refused
    assert window.pkg.path == path
    assert (tmp_path / "big.bin").stat().st_size == 4 << 20


def accept_message_boxes(monkeypatch):
    """Custom QMessageBoxes (install confirmation) answer with their AcceptRole button."""
    shown = []

    def exec_(box):
        shown.append(box.informativeText())
        for button in box.buttons():
            if box.buttonRole(button) == QMessageBox.AcceptRole:
                button.click()
                return 0
        return 0

    monkeypatch.setattr(QMessageBox, "exec", exec_)
    return shown


def test_install_in_shadps4(window, make_pkg, tmp_path, monkeypatch):
    import json

    from PySide6.QtCore import QSettings

    games = tmp_path / "games"
    user = tmp_path / "shadPS4"
    user.mkdir()
    general = {"install_dirs": [{"path": str(games), "enabled": True}]}
    (user / "config.json").write_text(json.dumps({"General": general}))
    monkeypatch.setattr(QSettings, "value", lambda self, key, default=None, type=None: str(user))
    shown = accept_message_boxes(monkeypatch)

    path, _ = make_pkg()
    window.open_pkg(path)
    assert window.act_install.isEnabled()
    window.install_shadps4()
    assert (games / "TEST00001" / "eboot.bin").is_file()
    assert str(games / "TEST00001") in shown[-1]

    # The update goes next to the game, and the confirmation says the folder already exists
    # the second time.
    path, _ = make_pkg(content_flags=0x40000000)
    window.open_pkg(path)
    window.install_shadps4()
    window.install_shadps4()
    assert (games / "TEST00001-patch" / "eboot.bin").is_file()
    assert "already exists" in shown[-1] and "not found" not in shown[-1]


def test_integrity_warning_and_full_check(window, make_pkg, monkeypatch):
    path, _ = make_pkg()
    window.open_pkg(path)
    assert "Damaged" not in window.info_label.text()
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a: messages.append(a[2]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: messages.append(a[2]))
    window.verify_full()
    assert "intact" in messages[-1]

    with open(path, "r+b") as f:  # damage the header: the quick check on open catches it
        f.seek(0x500)
        f.write(b"\xff")
    window.open_pkg(path)
    assert "Damaged PKG: header" in window.info_label.text()
    window.verify_full()
    assert "damaged" in messages[-1] and "header" in messages[-1]
