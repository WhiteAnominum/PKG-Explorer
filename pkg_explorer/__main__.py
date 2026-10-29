# SPDX-License-Identifier: GPL-2.0-or-later
"""`python -m pkg_explorer [ARQUIVO.pkg]` abre a interface gráfica."""

import sys

from .gui import main

sys.exit(main())
