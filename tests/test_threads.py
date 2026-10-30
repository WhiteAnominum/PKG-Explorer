# SPDX-License-Identifier: GPL-2.0-or-later
"""Static checks for the threading rules of the GUI (see CONTRIBUTING.md, "Qt threads").

Signals emitted by the extraction worker run in the worker thread. If such a signal is connected
to a lambda or plain function, Qt calls it *in the worker thread*, and touching a widget from
there crashes at random (this happened before, and Qt gives no warning for it). They must be
connected to slots of a QObject that lives in the GUI thread (`_DialogBridge`).
"""

import ast
import pathlib

GUI = pathlib.Path(__file__).parent.parent / "pkg_explorer" / "gui"
WORKER_SIGNALS = {"progress", "current", "done"}


def worker_connections():
    tree = ast.parse((GUI / "extraction.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "connect"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr in WORKER_SIGNALS
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "worker"
        ):
            yield node.func.value.attr, node.args[0]


def test_worker_signals_go_to_the_gui_thread_bridge():
    connections = list(worker_connections())
    assert {signal for signal, _target in connections} == WORKER_SIGNALS
    for signal, target in connections:
        is_bridge_slot = (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "bridge"
        )
        assert is_bridge_slot, f"worker.{signal} must be connected to a _DialogBridge slot"


def test_bridge_methods_are_qt_slots():
    # Without @Slot, PySide may call a plain Python method directly in the emitting thread.
    tree = ast.parse((GUI / "extraction.py").read_text(encoding="utf-8"))
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_DialogBridge"]
    assert classes, "extraction.py must route worker signals through _DialogBridge"
    bridge = classes[0]
    for method in bridge.body:
        if isinstance(method, ast.FunctionDef) and not method.name.startswith("__"):
            decorators = [ast.unparse(d) for d in method.decorator_list]
            assert any(d.startswith("Slot(") for d in decorators), method.name
