# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Replay files (``gkms.ps_replay.v1``): the formation document (or the fixed root), the code and data versions, every
action, and the state hash after each one.  ``verify`` plays a replay again and stops at the first step whose hash
differs; a replay from another master / exam-rule / PS version is reported as such, not compared.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from GKMSPS.ps.sim import Sim, ruleset

FORMAT = "gkms.ps_replay.v1"


def code_commit():
    here = Path(__file__).resolve().parents[2]
    r = subprocess.run(["git", "-C", str(here), "rev-parse", "HEAD"], capture_output=True, text=True)
    return r.stdout.strip() or None


def record(sim, hashes=None):
    s = sim.s
    hashes = sim.trace if hashes is None else hashes
    return {"format": FORMAT, "ruleset": ruleset(), "code": code_commit(), "formation": s["formation"],
            "timestamp_ms": s["document"]["timestamp_ms"], "root": s["root"] if s["fixed_root"] else None,
            "engine": s.get("engine", False), "selection_memory": s.get("selection_memory"),
            "actions": list(s["actions"]), "hashes": list(hashes)}


def start(rep):
    if rep.get("selection_memory"):
        return Sim.honsen(rep["selection_memory"], timestamp_ms=rep["timestamp_ms"], root=rep.get("root"),
                          engine=rep.get("engine", False))
    return Sim(rep["formation"], timestamp_ms=rep["timestamp_ms"], root=rep.get("root"), engine=rep.get("engine", False))


def verify(rep):
    if rep.get("format") != FORMAT:
        return {"ok": False, "why": "not a PS replay"}
    if rep.get("ruleset") != ruleset():
        return {"ok": None, "why": "版本不同", "replay": rep.get("ruleset"), "here": ruleset()}
    sim = start(rep)
    for i, (a, h) in enumerate(zip(rep["actions"], rep["hashes"])):
        got = sim.act(a)
        if got != h:
            return {"ok": False, "step": i, "want": h, "got": got}
    return {"ok": True, "steps": len(rep["actions"]), "final": sim.state_hash()}


def dumps(rep):
    return json.dumps(rep, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
