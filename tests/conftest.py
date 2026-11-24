# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "GKMSUINet"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from GakumasCLI import paths  # noqa: E402


@pytest.fixture(scope="session")
def master():
    """The engine's master (built by ``gkms.py master-bootstrap build``) with the native core; skipped without them."""
    if not (paths.MASTER_DIR / "ProduceCard.jsonl").is_file():
        pytest.skip("no master: run gkms.py master-bootstrap build --mirror <gakumasu-diff>")
    if not paths.NATIVE_LIB.is_file():
        pytest.skip("no native core: build GakumasCLI/native first")
    from GakumasCLI.scenario.exam_session import Master
    return Master.load(paths.MASTER_DIR)


@pytest.fixture(scope="session")
def loadouts():
    doc = json.loads((ROOT / "GakumasCLI" / "produce" / "coverage_baselines.json").read_text(encoding="utf-8"))
    return {kind: b["loadout"] for kind, b in doc["baselines"].items()}
