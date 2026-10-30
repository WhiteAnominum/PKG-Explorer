# SPDX-License-Identifier: GPL-2.0-or-later
import os

# English messages in the tests, whatever the machine's language (set before importing the app).
os.environ["PKG_EXPLORER_LANG"] = "en"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import random  # noqa: E402

import pytest  # noqa: E402
from pkgbuilder import build_pkg  # noqa: E402

try:
    from PySide6.QtCore import qInstallMessageHandler
except ImportError:  # GUI not installed: only the non-GUI tests run
    qInstallMessageHandler = None

# Qt warns (instead of crashing right away) about many thread-safety mistakes. Any of these
# during a test makes it fail, so this kind of bug cannot come back silently.
QT_THREAD_WARNINGS = (
    "another thread",
    "different thread",
    "Destroyed while thread",
    "Cannot create children for a parent",
)
_qt_thread_problems: list[str] = []


def _qt_message_handler(_mode, _context, message: str) -> None:
    if any(pattern in message for pattern in QT_THREAD_WARNINGS):
        _qt_thread_problems.append(message)


if qInstallMessageHandler is not None:
    qInstallMessageHandler(_qt_message_handler)


@pytest.fixture(autouse=True)
def no_qt_thread_warnings():
    _qt_thread_problems.clear()
    yield
    assert not _qt_thread_problems, f"Qt thread-safety warnings: {_qt_thread_problems}"


def sample_tree(seed: int = 1) -> dict:
    rng = random.Random(seed)
    return {
        "eboot.bin": b"\x7fELF" + bytes(rng.randrange(256) for _ in range(5000)),
        "version.xml": b"<version>1.0</version>",
        "data": {
            # several blocks, half compressible, half random
            "big.bin": b"A" * 100_000 + bytes(rng.randrange(256) for _ in range(150_000)),
            "exact.bin": b"B" * 0x10000,  # exactly one block
            "empty.txt": b"",
            "sub": {"deep": {"x.txt": b"hello"}},
        },
        "emptydir": {},
    }


@pytest.fixture
def make_pkg(tmp_path):
    """Factory: make_pkg(tree, **options) -> (path, plaintext entries)."""
    counter = iter(range(1000))

    def make(tree=None, **kwargs):
        path = tmp_path / f"test{next(counter)}.pkg"
        plain = build_pkg(path, sample_tree() if tree is None else tree, **kwargs)
        return str(path), plain

    return make


def files_of(tree: dict, prefix: str = "") -> dict[str, bytes]:
    out = {}
    for name, value in tree.items():
        if isinstance(value, dict):
            out.update(files_of(value, f"{prefix}{name}/"))
        else:
            out[prefix + name] = value
    return out
