# SPDX-License-Identifier: GPL-2.0-or-later
"""Command line: pkg-explorer-cli info|list|extract|verify FILE.pkg ..."""

import argparse
import sys

from . import integrity
from .i18n import _
from .pkg import Pkg, PkgError
from .util import human_size


def cmd_info(pkg: Pkg, _args) -> None:
    files = [n for n in pkg.root.walk() if not n.is_dir]
    rows = [
        (_("Title:"), pkg.title),
        ("Title ID:", pkg.title_id),
        ("Content ID:", pkg.content_id),
        (_("Type:"), f"{pkg.kind}  ({_('category')} '{pkg.category}')"),
        ("Flags:", ", ".join(pkg.flags) or "-"),
        (_("Version:"), pkg.sfo.get("APP_VER") or pkg.sfo.get("VERSION", "-")),
        (_("Files:"), f"{len(files)}  ({human_size(sum(n.size for n in files))})"),
    ]
    width = max(len(label) for label, _value in rows) + 2
    for label, value in rows:
        print(f"{label:<{width}}{value}")


def cmd_list(pkg: Pkg, _args) -> None:
    for node in pkg.root.walk():
        if node.parent is None:
            continue
        if node.is_dir:
            print(f"{'':>12}  {node.path}/")
        else:
            print(f"{node.size:>12}  {node.path}")


def cmd_extract(pkg: Pkg, args) -> None:
    paths = args.paths or [""]
    for path in paths:
        node = pkg.find(path)
        if node is None:
            sys.exit(_("not found in the PKG: {path}").format(path=path))
        out = pkg.extract(node, args.output)
        print(_("extracted: {path}").format(path=out))


def cmd_verify(pkg: Pkg, args) -> None:
    result = integrity.verify(pkg, full=not args.quick)
    for part in result.checked:
        status = _("BAD") if part in result.bad else "ok"
        print(f"{status:>4}  {integrity.part_label(part)}")
    if not result.checked:
        print(_("This PKG has no digests to check."))
    elif result.ok:
        print(_("The PKG is intact.") if result.full else _("OK (quick check)."))
    else:
        sys.exit(_("The PKG is damaged."))


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="pkg-explorer-cli", description=_("Browse PS4 PKGs"))
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("info", _("show information about the PKG")),
        ("list", _("list the files in the PKG")),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("pkg")
    p = sub.add_parser("extract", help=_("extract files/folders (or everything, if none is given)"))
    p.add_argument("pkg")
    p.add_argument("paths", nargs="*", help=_("paths inside the PKG, e.g. sce_sys/icon0.png"))
    p.add_argument("-o", "--output", default=".", help=_("destination folder"))
    p = sub.add_parser("verify", help=_("check the PKG's digests (detects damaged files)"))
    p.add_argument("pkg")
    p.add_argument(
        "--quick", action="store_true", help=_("skip the game data (instant, but less thorough)")
    )
    sub.add_parser(
        "integrate",
        help=_("make .pkg files open with PKG Explorer and add it to the applications menu"),
    )
    sub.add_parser("unintegrate", help=_("undo 'integrate'"))
    args = parser.parse_args(argv)

    if args.command in ("integrate", "unintegrate"):
        from . import desktop

        if args.command == "integrate":
            desktop.install()
            print(
                _("Done. PS4 .pkg files now open with: {command}").format(
                    command=desktop.describe_command()
                )
            )
        else:
            removed = desktop.uninstall()
            print(_("Integration removed.") if removed else _("Nothing to remove."))
        return

    try:
        with Pkg(args.pkg) as pkg:
            {"info": cmd_info, "list": cmd_list, "extract": cmd_extract, "verify": cmd_verify}[
                args.command
            ](pkg, args)
    except (PkgError, OSError) as exc:
        sys.exit(_("error: {error}").format(error=exc))


if __name__ == "__main__":
    main()
