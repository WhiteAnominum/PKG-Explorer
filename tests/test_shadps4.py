# SPDX-License-Identifier: GPL-2.0-or-later
"""Where PKGs are installed in shadPS4 (pkg_explorer/shadps4.py)."""

import json

import pytest
from pkgbuilder import build_sfo

from pkg_explorer import shadps4
from pkg_explorer.pkg import Pkg

PATCH = 0x40000000  # SUBSEQUENT_PATCH
DLC_SFO = {"TITLE": "DLC", "CATEGORY": "ac", "CONTENT_ID": "UP0000-TEST00001_00-MYDLC0000000001"}


def write_config(user_dir, install_dirs, addon=""):
    user_dir.mkdir(parents=True, exist_ok=True)
    general = {
        "install_dirs": [{"path": str(d), "enabled": on} for d, on in install_dirs],
        "addon_install_dir": addon,
    }
    (user_dir / "config.json").write_text(json.dumps({"General": general}))


def fake_game(folder, title_id="TEST00001", version="01.00", category="gd"):
    (folder / "sce_sys").mkdir(parents=True)
    sfo = {"TITLE_ID": title_id, "APP_VER": version, "CATEGORY": category}
    (folder / "sce_sys" / "param.sfo").write_bytes(build_sfo(sfo))
    return folder


@pytest.fixture
def config(tmp_path):
    games = tmp_path / "games"
    games.mkdir()
    user = tmp_path / "user"
    write_config(user, [(tmp_path / "disabled", False), (games, True)])
    return shadps4.load_config(user)


def test_load_config_json(config, tmp_path):
    assert config.install_dirs == [tmp_path / "games"]  # disabled folders are ignored
    assert config.addon_dir == tmp_path / "user" / "addcont"  # shadPS4's default


def test_load_config_custom_addon_dir(tmp_path):
    write_config(tmp_path / "u", [], addon=str(tmp_path / "dlcs"))
    assert shadps4.load_config(tmp_path / "u").addon_dir == tmp_path / "dlcs"


def test_load_legacy_toml(tmp_path):
    pytest.importorskip("tomllib")
    (tmp_path / "config.toml").write_text(
        '[GUI]\ninstallDirs = ["/a", "/b"]\ninstallDirsEnabled = [true, false]\n'
        'addonInstallDir = "/dlc"\n'
    )
    cfg = shadps4.load_config(tmp_path)
    assert [str(d) for d in cfg.install_dirs] == ["/a"] and str(cfg.addon_dir) == "/dlc"


def test_broken_config_raises(tmp_path):
    (tmp_path / "config.json").write_text("{not json")
    with pytest.raises(shadps4.Shadps4Error):
        shadps4.load_config(tmp_path)


def test_find_user_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    assert shadps4.find_user_dir() is None
    write_config(tmp_path / "data" / "shadPS4", [])
    assert shadps4.find_user_dir() == tmp_path / "data" / "shadPS4"
    assert shadps4.find_user_dir(str(tmp_path / "nothing")) is None


def test_new_game_goes_to_game_folder(config, make_pkg):
    path, _ = make_pkg()
    with Pkg(path) as pkg:
        plan = shadps4.plan_install(pkg, config)
    assert plan.target == config.install_dirs[0] / "TEST00001"
    assert plan.base_game is None and not plan.choices


def test_new_game_with_several_folders_offers_a_choice(tmp_path, make_pkg):
    write_config(tmp_path / "u", [(tmp_path / "a", True), (tmp_path / "b", True)])
    cfg = shadps4.load_config(tmp_path / "u")
    path, _ = make_pkg()
    with Pkg(path) as pkg:
        plan = shadps4.plan_install(pkg, cfg)
    assert plan.choices == [tmp_path / "a", tmp_path / "b"]
    assert shadps4.with_game_folder(plan, tmp_path / "b").target == tmp_path / "b" / "TEST00001"


def test_reinstall_uses_existing_game_folder(config, make_pkg):
    game = fake_game(config.install_dirs[0] / "My Game" / "Bloodborne", version="01.09")
    path, _ = make_pkg()
    with Pkg(path) as pkg:
        plan = shadps4.plan_install(pkg, config)
    assert plan.target == game and plan.installed_version == "01.09"


def test_update_goes_next_to_the_game(config, make_pkg):
    game = fake_game(config.install_dirs[0] / "Renamed")
    path, _ = make_pkg(content_flags=PATCH)
    with Pkg(path) as pkg:
        plan = shadps4.plan_install(pkg, config)
    assert plan.kind == "patch" and plan.base_game == game
    assert plan.target == game.parent / "Renamed-patch"


def test_update_reuses_existing_update_folder(config, make_pkg):
    game = fake_game(config.install_dirs[0] / "CUSA1")
    fake_game(game.parent / "CUSA1-UPDATE", version="01.02", category="gp")
    path, _ = make_pkg(content_flags=PATCH)
    with Pkg(path) as pkg:
        plan = shadps4.plan_install(pkg, config)
    assert plan.target == game.parent / "CUSA1-UPDATE" and plan.installed_version == "01.02"


def test_update_without_game(config, make_pkg):
    path, _ = make_pkg(content_flags=PATCH)
    with Pkg(path) as pkg:
        plan = shadps4.plan_install(pkg, config)
    assert plan.base_game is None
    assert plan.target == config.install_dirs[0] / "TEST00001-patch"


def test_dlc_goes_to_addon_folder(config, make_pkg):
    path, _ = make_pkg(sfo=DLC_SFO)
    with Pkg(path) as pkg:
        plan = shadps4.plan_install(pkg, config)
    assert plan.kind == "dlc"
    assert plan.target == config.addon_dir / "TEST00001" / "MYDLC0000000001"


def test_other_titles_are_not_matched(config, make_pkg):
    fake_game(config.install_dirs[0] / "Other", title_id="CUSA99999")
    path, _ = make_pkg()
    with Pkg(path) as pkg:
        assert shadps4.plan_install(pkg, config).base_game is None
