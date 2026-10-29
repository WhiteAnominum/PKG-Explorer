# SPDX-License-Identifier: GPL-2.0-or-later
"""Integração com o desktop Linux (só para o usuário atual, em ~/.local/share):
tipo MIME application/x-ps4-pkg, atalho no menu, ícone e associação de arquivos .pkg."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

APP_ID = "io.github.WhiteAnominum.PKGExplorer"
MIME_TYPE = "application/x-ps4-pkg"
DATA_DIR = Path(__file__).parent / "data"


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _paths() -> dict[str, Path]:
    data = _data_home()
    icons = data / "icons" / "hicolor" / "scalable"
    return {
        "desktop": data / "applications" / f"{APP_ID}.desktop",
        "mime": data / "mime" / "packages" / f"{APP_ID}.xml",
        "app_icon": icons / "apps" / f"{APP_ID}.svg",
        "mime_icon": icons / "mimetypes" / f"{MIME_TYPE.replace('/', '-')}.svg",
    }


def _desktop_quote(arg: str) -> str:
    """Aspas no formato da especificação Desktop Entry (para caminhos com espaços)."""
    if not any(c in arg for c in ' \t\n"\'\\><~|&;$*?#()`'):
        return arg
    escaped = arg.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$")
    return f'"{escaped}"'


def launch_command() -> list[str]:
    """Comando que abre a interface gráfica desta instalação."""
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return [appimage]
    script = Path(sys.executable).parent / "pkg-explorer"
    if script.exists():
        return [str(script)]
    return [sys.executable, "-m", "pkg_explorer"]


def desktop_entry() -> str:
    exec_line = " ".join(_desktop_quote(a) for a in launch_command())
    return f"""[Desktop Entry]
Type=Application
Name=PKG Explorer
GenericName=PS4 PKG Explorer
GenericName[pt_BR]=Explorador de PKG de PS4
Comment=Browse PS4 .pkg files and extract only what you need
Comment[pt_BR]=Navegue por arquivos .pkg de PS4 e extraia só o que quiser
Exec={exec_line} %f
Icon={APP_ID}
Terminal=false
Categories=Utility;Archiving;Game;
MimeType={MIME_TYPE};
Keywords=ps4;pkg;extract;shadps4;
StartupWMClass={APP_ID}
"""


def _run(*cmd: str) -> None:
    if shutil.which(cmd[0]):
        subprocess.run(cmd, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _refresh() -> None:
    data = _data_home()
    _run("update-mime-database", str(data / "mime"))
    _run("update-desktop-database", str(data / "applications"))
    _run("gtk-update-icon-cache", "-f", "-t", str(data / "icons" / "hicolor"))
    _run("kbuildsycoca6")  # KDE: atualiza o cache de aplicativos/associações


def install(make_default: bool = True) -> list[Path]:
    paths = _paths()
    for p in paths.values():
        p.parent.mkdir(parents=True, exist_ok=True)
    paths["desktop"].write_text(desktop_entry(), encoding="utf-8")
    shutil.copyfile(DATA_DIR / "x-ps4-pkg.xml", paths["mime"])
    shutil.copyfile(DATA_DIR / "pkg-explorer.svg", paths["app_icon"])
    shutil.copyfile(DATA_DIR / "pkg-explorer.svg", paths["mime_icon"])
    _refresh()
    if make_default:
        _run("xdg-mime", "default", f"{APP_ID}.desktop", MIME_TYPE)
    return list(paths.values())


def uninstall() -> list[Path]:
    removed = []
    for p in _paths().values():
        if p.exists():
            p.unlink()
            removed.append(p)
    _refresh()
    return removed


def is_installed() -> bool:
    return _paths()["desktop"].exists()


def describe_command() -> str:
    return shlex.join(launch_command())
