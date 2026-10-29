# SPDX-License-Identifier: GPL-2.0-or-later
"""Linha de comando: pkg-explorer-cli info|list|extract ARQUIVO.pkg ..."""

import argparse
import sys

from .pkg import Pkg, PkgError


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def cmd_info(pkg: Pkg, _args) -> None:
    print(f"Título:      {pkg.title}")
    print(f"Title ID:    {pkg.title_id}")
    print(f"Content ID:  {pkg.content_id}")
    print(f"Tipo:        {pkg.kind}  (categoria '{pkg.category}')")
    print(f"Flags:       {', '.join(pkg.flags) or '-'}")
    print(f"Versão:      {pkg.sfo.get('APP_VER') or pkg.sfo.get('VERSION', '-')}")
    files = [n for n in pkg.root.walk() if not n.is_dir]
    print(f"Arquivos:    {len(files)}  ({human_size(sum(n.size for n in files))})")


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
            sys.exit(f"não encontrado no PKG: {path}")
        out = pkg.extract(node, args.output)
        print(f"extraído: {out}")


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="pkg-explorer-cli", description="Explora PKGs de PS4")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("info", "list"):
        p = sub.add_parser(name)
        p.add_argument("pkg")
    p = sub.add_parser("extract", help="extrai arquivos/pastas (ou tudo, se nenhum for dado)")
    p.add_argument("pkg")
    p.add_argument("paths", nargs="*", help="caminhos dentro do PKG, ex.: sce_sys/icon0.png")
    p.add_argument("-o", "--output", default=".", help="pasta de destino")
    args = parser.parse_args(argv)

    try:
        with Pkg(args.pkg) as pkg:
            {"info": cmd_info, "list": cmd_list, "extract": cmd_extract}[args.command](pkg, args)
    except (PkgError, OSError) as exc:
        sys.exit(f"erro: {exc}")


if __name__ == "__main__":
    main()
