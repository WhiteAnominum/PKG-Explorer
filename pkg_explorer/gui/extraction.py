# SPDX-License-Identifier: GPL-2.0-or-later
"""Extraction in a worker thread with a progress dialog, and temporary folders."""

from __future__ import annotations

import atexit
import shutil
import tempfile
import threading

from PySide6.QtCore import QEventLoop, QObject, Qt, QThread, Signal
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from ..i18n import _
from ..pkg import ExtractionCancelled, Node, Pkg

_temp_dirs: list[str] = []


@atexit.register
def _cleanup_temp_dirs() -> None:
    for d in _temp_dirs:
        shutil.rmtree(d, ignore_errors=True)


def make_temp_dir() -> str:
    d = tempfile.mkdtemp(prefix="pkg-explorer-")
    _temp_dirs.append(d)
    return d


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
