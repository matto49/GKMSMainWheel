# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""GakumasCLI's one entry: ``python -B gkms.py <command> [args]`` (see __main__.py for the commands)."""
import sys
from pathlib import Path

sys.path[0] = str(Path(__file__).resolve().parents[1])

from GakumasCLI import paths  # noqa: E402
from GakumasCLI.__main__ import main  # noqa: E402

paths.ensure_roots()
if __name__ == "__main__":
    sys.exit(main())
