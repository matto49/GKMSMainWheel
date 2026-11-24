# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
import json

import pytest

from conftest import ROOT


def test_a_selection_runs_to_its_end(master):
    if not (ROOT / "GKMSPS" / "data" / "master_ps.json").is_file():
        pytest.skip("no GKMSPS/data/master_ps.json: run gkms.py master-bootstrap build")
    from GKMSPS.ps.cli import auto
    from GKMSPS.ps.sim import Sim
    formation = json.loads((ROOT / "GKMSPS" / "examples" / "formation.json").read_text(encoding="utf-8"))
    sim = Sim(formation, timestamp_ms=1763900000000)
    auto(sim, 0, sensible=True)
    assert sim.decision() is None
    assert (sim.s.get("result") or {}).get("outcome")
