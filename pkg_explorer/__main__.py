# SPDX-License-Identifier: GPL-2.0-or-later
"""`python -m pkg_explorer [FILE.pkg]` opens the graphical interface."""

import sys

from .gui import main

sys.exit(main())
