# SPDX-License-Identifier: GPL-2.0-or-later
import pytest
from conftest import sample_tree

from pkg_explorer import cli


def test_info(make_pkg, capsys):
    path, _ = make_pkg()
    cli.main(["info", path])
    out = capsys.readouterr().out
    assert "Test Package" in out and "TEST00001" in out and "Version:" in out


def test_list(make_pkg, capsys):
    path, _ = make_pkg()
    cli.main(["list", path])
    lines = capsys.readouterr().out.splitlines()
    assert any(line.endswith(" data/sub/deep/x.txt") for line in lines)
    assert any(line.strip() == "data/" for line in lines)


def test_extract_selected_paths(make_pkg, tmp_path, capsys):
    path, _ = make_pkg()
    cli.main(["extract", path, "eboot.bin", "data/sub", "-o", str(tmp_path)])
    assert (tmp_path / "eboot.bin").read_bytes() == sample_tree()["eboot.bin"]
    assert (tmp_path / "sub/deep/x.txt").read_bytes() == b"hello"


def test_extract_missing_path_and_bad_file(make_pkg, tmp_path):
    path, _ = make_pkg()
    with pytest.raises(SystemExit, match="not found in the PKG"):
        cli.main(["extract", path, "nope.bin", "-o", str(tmp_path)])
    bad = tmp_path / "bad.pkg"
    bad.write_bytes(b"x" * 5000)
    with pytest.raises(SystemExit, match="error: "):
        cli.main(["info", str(bad)])
