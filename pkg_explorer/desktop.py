# SPDX-License-Identifier: GPL-2.0-or-later
"""Integração com o desktop Linux (só para o usuário atual, em ~/.local/share):
tipo MIME application/x-ps4-pkg, atalho no menu, ícone e associação de arquivos .pkg.

No Flatpak o atalho e o tipo MIME já são exportados pelo próprio Flatpak, mas ele remove a
detecção pela assinatura do arquivo (<magic>) e rebaixa a prioridade do *.pkg, para que apps não
possam tomar extensões de outros tipos. Como .pkg já é application/x-xar (instalador do macOS),
o sistema continuaria abrindo no programa errado. Por isso, também no Flatpak, instalamos o tipo
MIME completo na pasta do usuário (o atalho continua sendo o exportado pelo Flatpak)."""

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


def in_flatpak() -> bool:
    return "FLATPAK_ID" in os.environ


def _data_home() -> Path:
    if in_flatpak():
        # Dentro do Flatpak, XDG_DATA_HOME aponta para ~/.var/app/<id>; queremos a do sistema.
        return Path(os.environ.get("HOST_XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def _paths() -> dict[str, Path]:
    data = _data_home()
    icons = data / "icons" / "hicolor" / "scalable"
    paths = {
        "mime": data / "mime" / "packages" / f"{APP_ID}.xml",
        "mime_icon": icons / "mimetypes" / f"{MIME_TYPE.replace('/', '-')}.svg",
    }
    if not in_flatpak():  # no Flatpak, atalho e ícone do app vêm do próprio pacote
        paths["desktop"] = data / "applications" / f"{APP_ID}.desktop"
        paths["app_icon"] = icons / "apps" / f"{APP_ID}.svg"
    return paths


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
    # KDE: atualiza o cache de aplicativos/associações (Plasma 6 e Plasma 5).
    _run("kbuildsycoca6")
    _run("kbuildsycoca5")


def install(make_default: bool = True) -> list[Path]:
    paths = _paths()
    for p in paths.values():
        p.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DATA_DIR / "x-ps4-pkg.xml", paths["mime"])
    shutil.copyfile(DATA_DIR / "pkg-explorer.svg", paths["mime_icon"])
    if "desktop" in paths:
        paths["desktop"].write_text(desktop_entry(), encoding="utf-8")
        shutil.copyfile(DATA_DIR / "pkg-explorer.svg", paths["app_icon"])
    _refresh()
    if make_default and not in_flatpak():
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
    return all(p.exists() for key, p in _paths().items() if key in ("mime", "desktop"))


def is_supported() -> bool:
    return sys.platform.startswith("linux")


def should_ask_on_first_run() -> bool:
    """AppImage e Flatpak não têm um passo de instalação que associe os .pkg sozinho."""
    return is_supported() and (is_appimage() or in_flatpak()) and not is_installed()


def is_appimage() -> bool:
    return bool(os.environ.get("APPIMAGE"))


def _installed_program() -> str | None:
    """Primeiro item do Exec= do atalho instalado (o programa que ele abre)."""
    try:
        text = _paths()["desktop"].read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("Exec="):
            try:
                return shlex.split(line[len("Exec="):])[0]
            except (ValueError, IndexError):
                return None
    return None


def repair_if_moved() -> bool:
    """Se a integração está instalada mas aponta para um programa que não existe mais (pasta
    movida, AppImage apagado) ou para um AppImage antigo enquanto rodamos de um AppImage
    (atualização), aponta de novo para esta instalação. Retorna True se corrigiu."""
    if not is_supported() or in_flatpak() or not is_installed():
        return False
    installed = _installed_program()
    current = launch_command()[0]
    if installed == current:
        return False
    moved = installed is None or not os.path.exists(installed)
    appimage_update = is_appimage() and not moved and installed.lower().endswith(".appimage")
    if not (moved or appimage_update):
        return False
    install(make_default=False)
    return True


def describe_command() -> str:
    return shlex.join(launch_command())
