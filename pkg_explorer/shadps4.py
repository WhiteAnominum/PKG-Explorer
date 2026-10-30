# SPDX-License-Identifier: GPL-2.0-or-later
"""Where a PKG goes in shadPS4, read from shadPS4's own settings.

Folder rules, from shadPS4 (https://github.com/shadps4-emu/shadPS4):
- game: a folder inside one of the game folders (`install_dirs`) - src/core/emulator_settings.h
- update: next to the game folder, named `<game folder>-UPDATE` or `<game folder>-patch`
  (src/core/file_sys/fs.cpp; -UPDATE is tried first)
- DLC: `<DLC folder>/<game title ID>/<any name>`, where the DLC folder is `addon_install_dir`
  or `<user folder>/addcont` (src/core/emulator_settings.cpp, libraries/app_content/app_content.cpp)

The user folder holds config.json (config.toml in older versions) - src/common/path_util.cpp.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .desktop import in_flatpak
from .pkg import Pkg
from .sfo import parse_sfo

UPDATE_SUFFIXES = ("-UPDATE", "-patch")
OTHER_SUFFIXES = ("-mods",)
SEARCH_DEPTH = 2  # how deep to look for an installed game inside each game folder


class Shadps4Error(Exception):
    pass


@dataclass
class Shadps4Config:
    user_dir: Path
    install_dirs: list[Path]  # enabled game folders, in shadPS4's order
    addon_dir: Path


@dataclass
class InstallPlan:
    kind: str  # 'game', 'patch' or 'dlc' (Pkg.kind)
    target: Path
    base_game: Path | None  # the installed game this update/DLC belongs to, if found
    installed_version: str = ""  # version already at `target`, if any
    choices: list[Path] = field(default_factory=list)  # game folders to pick from (new game)


def default_user_dirs() -> list[Path]:
    """Where shadPS4 keeps config.json: the normal install and the Flatpak."""
    home = Path.home()
    if in_flatpak():  # XDG_DATA_HOME points inside our sandbox; we want the host's
        data = Path(os.environ.get("HOST_XDG_DATA_HOME") or home / ".local" / "share")
    else:
        data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local" / "share")
    return [
        data / "shadPS4",
        home / ".var" / "app" / "net.shadps4.shadPS4" / "data" / "shadPS4",
    ]


def find_user_dir(custom: str = "") -> Path | None:
    candidates = [Path(custom)] if custom else default_user_dirs()
    for d in candidates:
        if (d / "config.json").is_file() or (d / "config.toml").is_file():
            return d
    return None


def load_config(user_dir: Path) -> Shadps4Config:
    install_dirs: list[Path] = []
    addon = ""
    json_path = user_dir / "config.json"
    toml_path = user_dir / "config.toml"
    try:
        if json_path.is_file():
            general = json.loads(json_path.read_text(encoding="utf-8")).get("General", {})
            for item in general.get("install_dirs", []):
                if item.get("enabled", True) and item.get("path"):
                    install_dirs.append(Path(item["path"]))
            addon = general.get("addon_install_dir") or ""
        elif toml_path.is_file():
            import tomllib  # Python 3.11+; older shadPS4 versions only

            gui = tomllib.loads(toml_path.read_text(encoding="utf-8")).get("GUI", {})
            paths = gui.get("installDirs", [])
            enabled = gui.get("installDirsEnabled", [])
            for i, p in enumerate(paths):
                if p and (enabled[i] if i < len(enabled) else True):
                    install_dirs.append(Path(p))
            addon = gui.get("addonInstallDir") or ""
        else:
            raise Shadps4Error(f"no config.json in {user_dir}")
    except (OSError, ValueError, AttributeError, TypeError, ImportError) as exc:
        raise Shadps4Error(f"{user_dir}: {exc}") from exc
    return Shadps4Config(user_dir, install_dirs, Path(addon) if addon else user_dir / "addcont")


def folder_info(folder: Path) -> dict:
    """param.sfo of an installed game/update/DLC folder ({} if there is none)."""
    try:
        return parse_sfo((folder / "sce_sys" / "param.sfo").read_bytes())
    except (OSError, ValueError, IndexError, KeyError):
        return {}


def find_game(install_dirs: list[Path], title_id: str) -> Path | None:
    """The installed base game with this title ID, searched like shadPS4's game list."""

    def scan(folder: Path, depth: int) -> Path | None:
        try:
            children = sorted(p for p in folder.iterdir() if p.is_dir())
        except OSError:
            return None
        for child in children:
            if child.name.endswith(UPDATE_SUFFIXES + OTHER_SUFFIXES):
                continue
            info = folder_info(child)
            if info:
                if info.get("TITLE_ID") == title_id and info.get("CATEGORY") != "gp":
                    return child
            elif depth > 1 and (found := scan(child, depth - 1)):
                return found
        return None

    for d in install_dirs:
        if (found := scan(d, SEARCH_DEPTH)) is not None:
            return found
    return None


def version_of(folder: Path) -> str:
    info = folder_info(folder)
    return str(info.get("APP_VER") or info.get("VERSION") or "")


def plan_install(pkg: Pkg, config: Shadps4Config) -> InstallPlan:
    """Decides where `pkg` goes. For a new game with several game folders, `target` uses the
    first one and `choices` lists them all (see with_game_folder)."""
    game = find_game(config.install_dirs, pkg.title_id)
    if pkg.kind == "dlc":
        # Pkg.output_folder_name is "<title ID>/<DLC label>" for DLC
        target = config.addon_dir / pkg.output_folder_name
        return InstallPlan("dlc", target, game, version_of(target))
    if pkg.kind == "patch":
        if game is None:
            if not config.install_dirs:
                raise Shadps4Error("no game folder")
            base = config.install_dirs[0] / pkg.title_id
        else:
            base = game
        # Reuse an existing update folder: shadPS4 only reads one (-UPDATE before -patch).
        target = next(
            (
                base.parent / (base.name + s)
                for s in UPDATE_SUFFIXES
                if (base.parent / (base.name + s)).is_dir()
            ),
            base.parent / (base.name + "-patch"),
        )
        return InstallPlan("patch", target, game, version_of(target))
    if game is not None:  # reinstall over the existing game
        return InstallPlan("game", game, game, version_of(game))
    if not config.install_dirs:
        raise Shadps4Error("no game folder")
    plan = InstallPlan("game", config.install_dirs[0] / pkg.title_id, None)
    if len(config.install_dirs) > 1:
        plan.choices = list(config.install_dirs)
    return plan


def with_game_folder(plan: InstallPlan, folder: Path) -> InstallPlan:
    plan.target = folder / plan.target.name
    plan.installed_version = version_of(plan.target)
    return plan
