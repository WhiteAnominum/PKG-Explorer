# SPDX-License-Identifier: GPL-2.0-or-later
"""Every translatable message has a translation, and translations use the same {…} fields."""

import ast
import pathlib
import string

from pkg_explorer.i18n import PT

SOURCES = pathlib.Path(__file__).parent.parent / "pkg_explorer"


def translatable_strings() -> set[str]:
    found = set()
    for path in SOURCES.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_" and node.args:
                arg = node.args[0]
                assert isinstance(arg, ast.Constant), f"{path.name}: _() with a non-literal text"
                found.add(arg.value)
    return found


def fields(text: str) -> set[str]:
    return {name for _lit, name, _spec, _conv in string.Formatter().parse(text) if name}


def test_every_string_is_translated():
    assert translatable_strings() - PT.keys() == set()


def test_no_unused_translations():
    assert PT.keys() - translatable_strings() == set()


def test_translations_keep_format_fields():
    for english, portuguese in PT.items():
        assert fields(english) == fields(portuguese), english
