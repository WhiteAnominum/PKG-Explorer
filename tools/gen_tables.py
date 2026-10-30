#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Regenerates pkg_explorer/keys.py and pkg_explorer/entry_names.py from the shadPS4 sources.

Usage:
    git clone --depth 1 https://github.com/AzaharPlus/shadPS4Plus.git /tmp/shadps4plus
    python3 tools/gen_tables.py /tmp/shadps4plus

It reads src/core/crypto/keys.h (RSA key sets; only the modulus and private exponent are needed
for decryption) and src/core/file_format/pkg_type.cpp (PKG entry id -> name table).
"""

import argparse
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEADER = (
    "# SPDX-FileCopyrightText: Copyright 2024 shadPS4 Emulator Project\n"
    "# SPDX-License-Identifier: GPL-2.0-or-later\n"
    "# Generated from {source} of ShadPs4Plus / shadPS4 by tools/gen_tables.py. Do not edit.\n\n"
)
KEY_SETS = (("FakeKeyset", "FAKE"), ("PkgDerivedKey3Keyset", "DK3"))
KEY_FIELDS = (("Modulus", "MODULUS"), ("PrivateExponent", "PRIVATE_EXPONENT"))


def parse_key_sets(source: str) -> dict[str, dict[str, bytes]]:
    """{class name: {array name: bytes}} for every `class X { ... byte Name[] = {...}; }`."""
    classes = {}
    for cls in re.finditer(r"class (\w+)\s*\{(.*?)\n\};", source, re.S):
        arrays = {}
        for arr in re.finditer(r"(\w+)\[\]\s*=\s*\{(.*?)\};", cls.group(2), re.S):
            numbers = re.findall(r"0x[0-9A-Fa-f]+|\b\d+\b", arr.group(2))
            arrays[arr.group(1)] = bytes(int(n, 0) for n in numbers)
        classes[cls.group(1)] = arrays
    return classes


def hex_lines(data: bytes) -> str:
    text = data.hex()
    return "\n".join(f'    "{text[i:i + 64]}"' for i in range(0, len(text), 64))


def write_keys(shadps4: pathlib.Path) -> None:
    source = "src/core/crypto/keys.h"
    key_sets = parse_key_sets((shadps4 / source).read_text())
    out = HEADER.format(source=source)
    for cls, prefix in KEY_SETS:
        for field, name in KEY_FIELDS:
            out += f"{prefix}_{name} = int(\n{hex_lines(key_sets[cls][field])},\n    16,\n)\n\n"
    (ROOT / "pkg_explorer/keys.py").write_text(out)


def write_entry_names(shadps4: pathlib.Path) -> None:
    source = "src/core/file_format/pkg_type.cpp"
    entries = re.findall(r'\{(0x[0-9A-Fa-f]+), "([^"]*)"\}', (shadps4 / source).read_text())
    out = HEADER.format(source=source) + "ENTRY_NAMES = {\n"
    out += "".join(f'    0x{int(entry_id, 16):04X}: "{name}",\n' for entry_id, name in entries)
    out += "}\n"
    (ROOT / "pkg_explorer/entry_names.py").write_text(out)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("shadps4", type=pathlib.Path, help="path to a ShadPs4Plus/shadPS4 checkout")
    args = parser.parse_args()
    write_keys(args.shadps4)
    write_entry_names(args.shadps4)
    print("updated pkg_explorer/keys.py and pkg_explorer/entry_names.py")


if __name__ == "__main__":
    main()
