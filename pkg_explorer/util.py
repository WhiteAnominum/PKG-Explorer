# SPDX-License-Identifier: GPL-2.0-or-later
"""Small helpers shared by the command line and the graphical interface."""


def human_size(n: float) -> str:
    """1536 -> "1.5 KB"."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)
