# SPDX-License-Identifier: GPL-2.0-or-later
"""Static checks for the threading rule of the GUI (see CONTRIBUTING.md, "Qt threads").

The extraction worker is a plain Python thread. Code that runs in it must not use any Qt object:
Qt objects used from another thread crash at random, often without any warning (this happened
before). The worker only updates an ExtractionState that the GUI thread polls.
"""

import ast
import pathlib

EXTRACTION = pathlib.Path(__file__).parent.parent / "pkg_explorer" / "gui" / "extraction.py"
WORKER_SIDE = {"extract_jobs", "ExtractionState"}
QT_MODULES = ("PySide6", "shiboken6")


def module():
    return ast.parse(EXTRACTION.read_text(encoding="utf-8"))


def test_worker_side_code_does_not_use_qt():
    qt_names = set()
    for node in module().body:
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(QT_MODULES):
            qt_names |= {alias.asname or alias.name for alias in node.names}
        if isinstance(node, ast.Import):
            qt_names |= {a.asname or a.name for a in node.names if a.name.startswith(QT_MODULES)}
    assert qt_names, "expected Qt imports in extraction.py"

    worker_side = [n for n in module().body if getattr(n, "name", None) in WORKER_SIDE]
    assert {n.name for n in worker_side} == WORKER_SIDE
    for definition in worker_side:
        used = {n.id for n in ast.walk(definition) if isinstance(n, ast.Name)}
        assert not used & qt_names, f"{definition.name} uses Qt: {sorted(used & qt_names)}"


def test_no_qt_threads_or_cross_thread_signals():
    source = EXTRACTION.read_text(encoding="utf-8")
    for forbidden in ("QThread", "Signal(", "moveToThread"):
        assert forbidden not in source, forbidden
