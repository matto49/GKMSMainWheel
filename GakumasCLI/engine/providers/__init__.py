# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Produce input providers."""

from .corpus import CorpusProvider
from .self_drive import SelfDriveProvider

__all__ = ["CorpusProvider", "SelfDriveProvider"]
