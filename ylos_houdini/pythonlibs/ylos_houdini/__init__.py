# -*- coding: utf-8 -*-
# ylos_houdini -- Houdini adapter for the Ylos Pipeline.
# Requires ylos_core on sys.path (vendored into pythonlibs/ by build.py).
#
# Python floor: 3.10 (Houdini 20.5+). Checked at import time.

import sys

if sys.version_info < (3, 10):
    raise RuntimeError(
        f"ylos_houdini requires Python >= 3.10; "
        f"got {sys.version_info.major}.{sys.version_info.minor}. "
        f"Please use Houdini 20.5 or later."
    )
