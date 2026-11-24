# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Trigger conditions the shared parser (GakumasCLI.produce.produce_effects.Model.parse_trigger) leaves as text."""
from __future__ import annotations

import dataclasses
import re
from functools import lru_cache

THRESHOLD_RE = re.compile(r"-(vocal|dance|visual|stamina_ratio|produce_point|produce_card_count)-(\d{4})_(\d{4})$")
OTHER_SCENARIO_RE = re.compile(r"-for_(?!hif_memory$)[a-z_]+$")


@lru_cache(maxsize=None)
def _parse(model, trigger_id):
    """(phase, cond, held) for a trigger id, the thresholds in cond["limits"] as {kind: (min, max)}."""
    if not trigger_id:
        return model.parse_trigger(trigger_id)
    phase = model.triggers.get(trigger_id, "UNKNOWN")
    t = trigger_id
    if OTHER_SCENARIO_RE.search(t):
        return phase, {}, OTHER_SCENARIO_RE.search(t).group(0)[1:]
    limits = {}
    while True:
        m = THRESHOLD_RE.search(t)
        if not m:
            break
        limits[m.group(1)] = (int(m.group(2)), int(m.group(3)))
        t = t[:m.start()]
    if not limits:
        return model.parse_trigger(trigger_id)
    _phase, cond, held = model.parse_trigger(t)
    cond = dict(cond)
    cond["limits"] = limits
    return phase, cond, held


def refine(model, skill, trigger_id):
    """The skill with its trigger read in full (a new Skill when anything changes)."""
    if not trigger_id:
        return skill
    phase, cond, held = _parse(model, trigger_id)
    if (phase, cond, held) == (skill.phase, skill.cond, skill.held):
        return skill
    return dataclasses.replace(skill, phase=phase, cond=cond, held=held)


def limits_met(cond, s, deck_size):
    """The thresholds of a trigger against the produce's state at the moment it fires."""
    for kind, (lo, hi) in (cond.get("limits") or {}).items():
        if kind in ("vocal", "dance", "visual"):
            v = s["params"][kind]
        elif kind == "stamina_ratio":
            v = s["stamina"] * 1000 // max(1, s["max_stamina"])
        elif kind == "produce_point":
            v = s["pp"]
        else:
            v = deck_size
        if lo and v < lo:
            return False
        if hi and v > hi:
            return False
    return True
