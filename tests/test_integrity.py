# SPDX-License-Identifier: GPL-2.0-or-later
"""Integrity check (pkg_explorer/integrity.py) against synthetic PKGs with valid digests."""

import threading

import pytest

from pkg_explorer import cli, integrity
from pkg_explorer.pkg import ExtractionCancelled, Pkg


def damage(path, offset):
    with open(path, "r+b") as f:
        f.seek(offset)
        byte = f.read(1)
        f.seek(offset)
        f.write(bytes([byte[0] ^ 0xFF]))


@pytest.fixture
def pkg_path(make_pkg):
    # An encrypted entry with a size that is not a multiple of 16 (hashed with its padding).
    path, _ = make_pkg(np_data=b"x" * 21, icon=b"icon" * 50)
    return path


def entry_offset(path, entry_id):
    with Pkg(path) as pkg:
        return next(e.offset for e in pkg.entries if e.id == entry_id)


def test_intact_pkg(pkg_path):
    with Pkg(pkg_path) as pkg:
        quick = integrity.verify(pkg)
        full = integrity.verify(pkg, full=True)
    assert quick.ok and full.ok
    assert {"header", "digest table", "package body", "param.sfo", "license.dat"} <= set(
        quick.checked
    )
    assert "game data" not in quick.checked and "game data" in full.checked


def test_damaged_entry(pkg_path):
    damage(pkg_path, entry_offset(pkg_path, 0x1200) + 10)
    with Pkg(pkg_path) as pkg:
        assert integrity.verify(pkg).bad == ["icon0.png", "package body"]


def test_damaged_header(pkg_path):
    damage(pkg_path, 0x500)
    with Pkg(pkg_path) as pkg:
        assert integrity.verify(pkg).bad == ["header"]


def test_damaged_game_data_needs_full_check(pkg_path):
    with Pkg(pkg_path) as pkg:
        middle = pkg.pfs_image_offset + pkg.pfs_image_size // 2
    damage(pkg_path, middle)
    with Pkg(pkg_path) as pkg:
        assert integrity.verify(pkg).ok
        assert integrity.verify(pkg, full=True).bad == ["game data"]


def test_pkg_without_digests(make_pkg):
    path, _ = make_pkg(digests=False)
    with Pkg(path) as pkg:
        result = integrity.verify(pkg, full=True)
    assert result.checked == [] and result.ok


def test_progress_and_cancel(pkg_path):
    with Pkg(pkg_path) as pkg:
        read = []
        integrity.verify(pkg, full=True, progress=read.append)
        assert sum(read) == integrity.full_check_size(pkg)

        cancel = threading.Event()
        cancel.set()
        with pytest.raises(ExtractionCancelled):
            integrity.verify(pkg, full=True, cancel=cancel)


def test_cli_verify(pkg_path, capsys):
    cli.main(["verify", pkg_path])
    assert "The PKG is intact." in capsys.readouterr().out
    damage(pkg_path, 0x500)
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["verify", "--quick", pkg_path])
    assert exit_info.value.code == "The PKG is damaged."
    assert "BAD  header" in capsys.readouterr().out
