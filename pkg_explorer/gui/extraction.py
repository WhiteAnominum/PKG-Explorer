# SPDX-License-Identifier: GPL-2.0-or-later
"""Long jobs (extraction, full integrity check) in a worker thread with a progress dialog,
and temporary folders.

Threading design: the worker is a plain Python thread that never touches Qt. It only updates
an `ExtractionState` (plain numbers and strings). The GUI thread polls that state with a QTimer
and updates the dialog. Every Qt object is created, used and destroyed in the GUI thread, so
the cross-thread Qt mistakes that caused random crashes before cannot happen here.
"""

from __future__ import annotations

import atexit
import shutil
import tempfile
import threading

import shiboken6
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QWidget

from .. import integrity
from ..i18n import _
from ..pkg import ExtractionCancelled, Node, Pkg

POLL_INTERVAL_MS = 100

_temp_dirs: list[str] = []


@atexit.register
def _cleanup_temp_dirs() -> None:
    for d in _temp_dirs:
        shutil.rmtree(d, ignore_errors=True)


def make_temp_dir() -> str:
    d = tempfile.mkdtemp(prefix="pkg-explorer-")
    _temp_dirs.append(d)
    return d


class ExtractionState:
    """Shared between the worker thread (writes) and the GUI thread (reads). Plain Python values
    only: single attribute reads and writes are atomic in Python, so no lock is needed."""

    def __init__(self, total: int):
        self.total = max(1, total)
        self.written = 0
        self.current = ""
        self.finished = False
        self.ok = False
        self.error = ""
        self.result = None  # job-specific (verify_job: integrity.Result)

    def add(self, n: int) -> None:
        self.written += n


def extract_jobs(
    pkg: Pkg, jobs: list[tuple[Node, str]], cancel: threading.Event, state: ExtractionState
) -> None:
    """Runs in the worker thread. Must not use any Qt object."""
    try:
        for node, dest in jobs:
            state.current = node.path or _("(everything)")
            pkg.extract(node, dest, state.add, cancel)
    except ExtractionCancelled:
        pass
    except Exception as exc:  # noqa: BLE001 - any error is shown to the user
        state.error = str(exc)
    else:
        state.ok = True
    finally:
        state.finished = True


def verify_job(pkg: Pkg, cancel: threading.Event, state: ExtractionState) -> None:
    """Runs in the worker thread. Must not use any Qt object."""
    try:
        state.current = _("game data")
        state.result = integrity.verify(pkg, full=True, progress=state.add, cancel=cancel)
    except ExtractionCancelled:
        pass
    except Exception as exc:  # noqa: BLE001 - any error is shown to the user
        state.error = str(exc)
    else:
        state.ok = True
    finally:
        state.finished = True


def run_extraction(
    parent: QWidget,
    pkg: Pkg,
    jobs: list[tuple[Node, str]],
    cancel: threading.Event | None = None,
) -> bool:
    """Extracts in another thread while showing a progress dialog. Returns True if it finished."""
    cancel = cancel or threading.Event()
    state = ExtractionState(sum(node.total_size() for node, _dest in jobs))
    run_with_progress(
        parent,
        _("Extracting"),
        _("Extracting:\n{path}"),
        state,
        extract_jobs,
        (pkg, jobs, cancel, state),
        cancel,
    )
    if state.error:
        QMessageBox.critical(parent, _("Extraction error"), state.error)
    return state.ok


def run_verification(
    parent: QWidget, pkg: Pkg, cancel: threading.Event | None = None
) -> integrity.Result | None:
    """Full integrity check in another thread with a progress dialog. None if cancelled."""
    cancel = cancel or threading.Event()
    state = ExtractionState(integrity.full_check_size(pkg))
    run_with_progress(
        parent,
        _("Verifying"),
        _("Verifying:\n{path}"),
        state,
        verify_job,
        (pkg, cancel, state),
        cancel,
    )
    if state.error:
        QMessageBox.critical(parent, _("Error"), state.error)
    return state.result if state.ok else None


def run_with_progress(
    parent: QWidget,
    title: str,
    label: str,
    state: ExtractionState,
    job,
    args: tuple,
    cancel: threading.Event,
) -> None:
    """Runs `job(*args)` in a worker thread while showing a progress dialog fed by `state`.

    Blocks (running a local event loop) until the worker thread has really finished, so the PKG
    is never used by the worker after this returns. The dialog's Cancel sets `cancel`."""
    worker = threading.Thread(target=job, args=args, name="pkg-explorer-worker")

    dialog = QProgressDialog(title + "…", _("Cancel"), 0, 1000, parent)
    dialog.setWindowTitle(title)
    dialog.setWindowModality(Qt.WindowModal)
    dialog.setMinimumDuration(300)
    dialog.setMinimumWidth(480)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.canceled.connect(cancel.set)

    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(POLL_INTERVAL_MS)
    shown = {"current": None, "busy": False}

    def poll() -> None:
        # setValue() of a visible modal dialog processes events, which can fire the timer again
        # while we are still in here: ignore those nested calls.
        if shown["busy"]:
            return
        shown["busy"] = True
        try:
            if state.current != shown["current"]:
                shown["current"] = state.current
                dialog.setLabelText(label.format(path=state.current))
            dialog.setValue(min(1000, state.written * 1000 // state.total))
            if state.finished:
                timer.stop()
                loop.quit()
        finally:
            shown["busy"] = False

    timer.timeout.connect(poll)
    worker.start()
    timer.start()
    loop.exec()
    worker.join()
    dialog.close()
    # Destroy the Qt objects now, in the GUI thread, instead of leaving them to the garbage
    # collector (a closed dialog would otherwise stay alive as a hidden child of the window).
    for obj in (timer, loop, dialog):
        shiboken6.delete(obj)
