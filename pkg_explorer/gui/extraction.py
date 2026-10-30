# SPDX-License-Identifier: GPL-2.0-or-later
"""Extraction in a worker thread with a progress dialog, and temporary folders."""

from __future__ import annotations

import atexit
import shutil
import tempfile
import threading

from PySide6.QtCore import QEventLoop, QObject, Qt, QThread, Signal, Slot
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

    def __init__(self, pkg: Pkg, jobs: list[tuple[Node, str]], cancel: threading.Event):
        super().__init__()
        self.pkg = pkg
        self.jobs = jobs
        self.cancel = cancel
        self.total = max(1, sum(node.total_size() for node, _ in jobs))
        self._written = 0
        self._last_progress = -1

    def _on_chunk(self, n: int) -> None:
        self._written += n
        # Only when the value changes: at most 1000 signals instead of one per 64 KB block.
        progress = int(self._written * 1000 / self.total)
        if progress != self._last_progress:
            self._last_progress = progress
            self.progress.emit(progress)

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


class _DialogBridge(QObject):
    """Lives in the GUI thread. The worker's signals are connected to these slots, so Qt
    delivers them through the GUI thread's event queue and the dialog is only ever touched
    from the GUI thread (widgets must not be used from other threads)."""

    def __init__(self, dialog: QProgressDialog, thread: QThread, result: dict):
        super().__init__()
        self._dialog = dialog
        self._thread = thread
        self._result = result

    @Slot(int)
    def show_progress(self, per_mille: int) -> None:
        self._dialog.setValue(per_mille)

    @Slot(str)
    def show_current(self, path: str) -> None:
        self._dialog.setLabelText(_("Extracting:\n{path}").format(path=path))

    @Slot(bool, str)
    def finish(self, ok: bool, error: str) -> None:
        self._result["ok"], self._result["error"] = ok, error
        self._thread.quit()


def run_extraction(
    parent: QWidget,
    pkg: Pkg,
    jobs: list[tuple[Node, str]],
    cancel: threading.Event | None = None,
) -> bool:
    """Extracts in another thread while showing a progress dialog. Returns True if it finished.

    Blocks (running a local event loop) until the worker thread has really finished, so `pkg`
    is never used by the worker after this returns. Setting `cancel` stops the extraction."""
    worker = ExtractWorker(pkg, jobs, cancel or threading.Event())
    thread = QThread()  # no parent: it is a local object, released after wait() below
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
    bridge = _DialogBridge(dialog, thread, result)

    # Receivers are QObjects in the GUI thread, so these connections are queued.
    worker.progress.connect(bridge.show_progress)
    worker.current.connect(bridge.show_current)
    worker.done.connect(bridge.finish)
    thread.started.connect(worker.run)
    thread.finished.connect(loop.quit)
    thread.start()
    loop.exec()
    dialog.close()
    thread.wait()

    if result.get("error"):
        QMessageBox.critical(parent, _("Extraction error"), result["error"])
    return bool(result.get("ok"))
