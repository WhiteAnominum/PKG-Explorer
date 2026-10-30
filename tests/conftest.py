# SPDX-License-Identifier: GPL-2.0-or-later
import os

# Mensagens em inglês nos testes, qualquer que seja o idioma da máquina (antes de importar o app).
os.environ["PKG_EXPLORER_LANG"] = "en"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import random  # noqa: E402

import pytest  # noqa: E402

from pkgbuilder import build_pkg  # noqa: E402


def sample_tree(seed: int = 1) -> dict:
    rng = random.Random(seed)
    return {
        "eboot.bin": b"\x7fELF" + bytes(rng.randrange(256) for _ in range(5000)),
        "version.xml": b"<version>1.0</version>",
        "data": {
            # vários blocos, metade compressível, metade aleatória
            "big.bin": b"A" * 100_000 + bytes(rng.randrange(256) for _ in range(150_000)),
            "exact.bin": b"B" * 0x10000,  # exatamente um bloco
            "empty.txt": b"",
            "sub": {"deep": {"x.txt": b"hello"}},
        },
        "emptydir": {},
    }


@pytest.fixture
def make_pkg(tmp_path):
    """Fábrica: make_pkg(tree, **opções) -> (caminho, entradas em claro)."""
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
