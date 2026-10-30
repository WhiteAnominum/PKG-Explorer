# SPDX-License-Identifier: GPL-2.0-or-later
"""Desktop integration in isolated folders (never touches the real system)."""

import pytest

from pkg_explorer import desktop


@pytest.fixture
def home(tmp_path, monkeypatch):
    for var in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "HOST_XDG_DATA_HOME", "APPIMAGE", "FLATPAK_ID"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / ".local/share"))
    commands = []
    monkeypatch.setattr(
        desktop, "_run", lambda *cmd: commands.append(cmd)
    )  # no update-mime-database etc.
    return tmp_path, commands


def test_install_and_uninstall(home):
    root, commands = home
    desktop.install()
    data = root / ".local/share"
    assert (data / "applications" / f"{desktop.APP_ID}.desktop").exists()
    assert (data / "mime/packages" / f"{desktop.APP_ID}.xml").read_text().count("0x7F434E54") == 1
    assert (data / "icons/hicolor/scalable/mimetypes/application-x-ps4-pkg.svg").exists()
    assert desktop.is_installed()
    assert ("xdg-mime", "default", f"{desktop.APP_ID}.desktop", desktop.MIME_TYPE) in commands

    desktop.uninstall()
    assert not desktop.is_installed()
    assert not list((data / "applications").iterdir())


def test_desktop_entry_quotes_paths_with_spaces(home, monkeypatch, tmp_path):
    app = tmp_path / "my apps" / "PKG Explorer.AppImage"
    monkeypatch.setenv("APPIMAGE", str(app))
    assert f'Exec="{app}" %f' in desktop.desktop_entry()


def test_repair_after_appimage_update(home, monkeypatch, tmp_path):
    old, new = tmp_path / "v1.AppImage", tmp_path / "v2.AppImage"
    old.touch()
    new.touch()
    monkeypatch.setenv("APPIMAGE", str(old))
    desktop.install()
    monkeypatch.setenv("APPIMAGE", str(new))
    assert desktop.repair_if_moved()
    assert desktop._installed_program() == str(new)
    assert not desktop.repair_if_moved()


def test_no_repair_when_other_install_still_exists(home, monkeypatch, tmp_path):
    other = tmp_path / "other.AppImage"
    other.touch()
    monkeypatch.setenv("APPIMAGE", str(other))
    desktop.install()
    monkeypatch.delenv("APPIMAGE")  # now running "from source"
    assert not desktop.repair_if_moved()
    other.unlink()  # the old installation is gone: fix it
    assert desktop.repair_if_moved()


def test_first_run_question(home, monkeypatch, tmp_path):
    assert not desktop.should_ask_on_first_run()  # from source: does not ask
    monkeypatch.setenv("APPIMAGE", str(tmp_path / "x.AppImage"))
    assert desktop.should_ask_on_first_run()
    desktop.install()
    assert not desktop.should_ask_on_first_run()


def test_flatpak_installs_only_mime_in_host_home(home, monkeypatch):
    root, commands = home
    monkeypatch.setenv("FLATPAK_ID", desktop.APP_ID)
    monkeypatch.setenv("XDG_DATA_HOME", str(root / ".var/app/x/data"))  # as inside Flatpak
    assert desktop.should_ask_on_first_run()
    desktop.install()
    data = root / ".local/share"
    assert (data / "mime/packages" / f"{desktop.APP_ID}.xml").exists()
    assert not (data / "applications").exists()  # the shortcut comes from Flatpak itself
    assert not (root / ".var").exists()
    assert not any(cmd[0] == "xdg-mime" for cmd in commands)
    assert not desktop.repair_if_moved()
