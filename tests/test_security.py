# SPDX-License-Identifier: GPL-2.0-or-later
"""PKGs maliciosos e malformados."""

import os
import random
import struct
import zlib

import pytest

from pkg_explorer import pkg as pkgmod
from pkg_explorer.pkg import Node, Pkg, PkgError


@pytest.mark.parametrize("name", ["..", ".", "../evil.txt", "/etc/evil", "a/../../b", "a\\b", "nul\0x"])
def test_malicious_names_are_rejected_on_open(make_pkg, name):
    path, _ = make_pkg({"ok.txt": b"ok", name: b"evil"})
    with pytest.raises(PkgError, match="invalid file name"):
        Pkg(path)


def test_malicious_folder_name_is_rejected(make_pkg):
    path, _ = make_pkg({"..": {"evil.txt": b"x"}})
    with pytest.raises(PkgError, match="invalid file name"):
        Pkg(path)


def _fake_pkg_with(names):
    """Pkg sem arquivo por trás, com uma árvore montada à mão (para testar só a extração)."""
    pkg = object.__new__(Pkg)
    pkg.iter_file = lambda node: iter([b"data"])
    root = Node("", True)
    for name in names:
        root.children[name] = Node(name, False, root, 4)
    return pkg, root


def test_extract_refuses_traversal_even_if_tree_is_bad(tmp_path):
    pkg, root = _fake_pkg_with(["../outside.txt"])
    (tmp_path / "dest").mkdir()
    with pytest.raises(PkgError):
        pkg.extract(root, tmp_path / "dest")
    assert not (tmp_path / "outside.txt").exists()


def test_extract_does_not_follow_symlinks_in_destination(tmp_path):
    dest, outside = tmp_path / "dest", tmp_path / "outside"
    dest.mkdir(); outside.mkdir()
    (outside / "victim.txt").write_text("original")
    (dest / "link.txt").symlink_to(outside / "victim.txt")
    (dest / "linkdir").symlink_to(outside)

    pkg, root = _fake_pkg_with(["link.txt"])
    with pytest.raises(PkgError):
        pkg.extract(root, dest)

    pkg, root = _fake_pkg_with([])
    folder = Node("linkdir", True, root)
    root.children["linkdir"] = folder
    folder.children["x.txt"] = Node("x.txt", False, folder, 4)
    with pytest.raises(PkgError):
        pkg.extract(root, dest)

    assert (outside / "victim.txt").read_text() == "original"
    assert sorted(os.listdir(outside)) == ["victim.txt"]
    assert (dest / "link.txt").is_symlink()


def test_failed_open_does_not_delete_existing_file(tmp_path):
    # Se o destino não pode ser aberto para escrita, o que já existe lá não pode ser apagado.
    pkg, root = _fake_pkg_with(["busy"])
    (tmp_path / "busy").mkdir()  # abrir uma pasta como arquivo falha
    with pytest.raises(OSError):
        pkg.extract(root, tmp_path)
    assert (tmp_path / "busy").is_dir()


def test_compression_bomb_is_rejected():
    pkg = object.__new__(Pkg)
    pkg._block_size = pkgmod.PFS_BLOCK_SIZE
    with pytest.raises(PkgError):
        pkg._decode_block(zlib.compress(b"\0" * (10 << 20), 9))


def _only_pkg_errors(path):
    try:
        with Pkg(path) as pkg:
            for node in pkg.root.walk():
                if not node.is_dir:
                    pkg.read_file(node)
    except PkgError:
        pass


def test_fuzz_header_and_file(make_pkg, tmp_path):
    path, _ = make_pkg()
    base = open(path, "rb").read()
    rng = random.Random(7)
    target = tmp_path / "fuzz.pkg"
    for _ in range(300):
        data = bytearray(base)
        for _ in range(rng.randint(1, 8)):
            pos = rng.randrange(0x1000) if rng.random() < 0.6 else rng.randrange(len(data))
            data[pos] = rng.choice([0, 0xFF, rng.randrange(256)])
        target.write_bytes(data)
        _only_pkg_errors(target)  # qualquer exceção que não seja PkgError faz o teste falhar


def test_fuzz_pfs_structures(make_pkg, monkeypatch):
    path, _ = make_pkg()
    original = Pkg._decode_block
    for i in range(300):
        rng = random.Random(i)
        targets = set(rng.sample(range(4), rng.randint(1, 2)))
        state = {"n": 0}

        def mutated(self, raw, rng=rng, targets=targets, state=state):
            data = bytearray(original(self, raw))
            if state["n"] in targets:
                for _ in range(rng.randint(1, 6)):
                    pos = rng.randrange(0x400) if rng.random() < 0.5 else rng.randrange(len(data))
                    data[pos] = rng.choice([0, 0xFF, rng.randrange(256)])
                if rng.random() < 0.3:
                    struct.pack_into("<q", data, rng.choice([0x30, 0x48]), rng.choice([-1, 0, 2**40, 10**6]))
            state["n"] += 1
            return bytes(data)

        monkeypatch.setattr(Pkg, "_decode_block", mutated)
        _only_pkg_errors(path)
