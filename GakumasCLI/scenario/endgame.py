# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exact finite suffix search for an already opened exam turn."""
from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from GakumasCLI.scenario.solver import search_state_key  # noqa: E402


@dataclass
class TailResult:
    status: str
    L: int | None
    U: int | float
    suffix_for_L: list | None
    scope: dict
    stats: dict = field(default_factory=dict)

    def as_dict(self):
        return {
            "status": self.status,
            "L": self.L,
            "U": self.U,
            "suffix_for_L": self.suffix_for_L,
            "scope": self.scope,
            "stats": self.stats,
        }


class _BudgetStop(Exception):
    pass


def namespace_for(session, version="tl187-endgame-v1"):
    """Bind a TT to code, objective, stage/effect data and the epoch input."""
    files = [
        ROOT / "scenario" / "exam_session.py",
        ROOT / "scenario" / "solver.py",
        ROOT / "engine" / "exam.py",
        ROOT / "ro" / "master" / "tables" / "Setting.jsonl",
        ROOT / "ro" / "master" / "tables" / "ProduceExamEffect.jsonl",
        ROOT / "ro" / "master" / "tables" / "ProduceExamGimmickEffectGroup.jsonl",
    ]
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.name).encode())
        if path.exists():
            digest.update(path.read_bytes())
    config = getattr(session, "config", None)
    return (
        version,
        str(getattr(config, "objective_id", "score-only-v1")),
        str(getattr(config, "segment_id", "")),
        str(getattr(config, "gimmick_group_id", "")),
        int(getattr(session, "turn", 0)),
        digest.hexdigest(),
    )


def _key(session, namespace):
    return namespace, search_state_key(session)


def extract_turn_entry(session, actions, turn=18):
    """Replay a complete route and return the pre-choice state at ``turn``."""
    node = session.clone()
    normalized = [tuple(action) for action in actions]
    consumed = []
    for action in normalized:
        if node.finished:
            break
        if node.turn == turn:
            return node, consumed
        if action not in node.legal_actions():
            raise ValueError(f"illegal action before turn {turn}: {action!r} at turn {node.turn}")
        node.step(action)
        consumed.append(list(action))
    if node.turn == turn and not node.finished:
        return node, consumed
    raise ValueError(f"route did not reach pre-choice turn {turn}; stopped at {node.turn}")


def exact_tail(session, *, namespace=None, deadline=None, node_cap=None,
               memory_cap=None, max_decisions=256, table=None):
    """Enumerate every legal suffix from ``session``."""
    namespace = namespace or namespace_for(session)
    table = {} if table is None else table
    started = time.monotonic()
    stats = {
        "nodes": 0, "terminals": 0, "tt_hits": 0, "tt_entries_before": len(table),
        "max_decisions": 0, "cycle_detected": 0, "budget_stop": None,
        "peak_key_bytes": 0,
    }
    scope = {
        "entry_turn": int(session.turn),
        "limit_turn": int(getattr(session.config, "limit_turn", 0) or 0),
        "objective_id": getattr(session.config, "objective_id", "score-only-v1"),
        "engine_order": "ExamSession.legal_actions -> step -> end_turn/start_turn",
        "decision_bound": max_decisions,
        "bound_proven": False,
    }
    path_keys = set()

    def check_budget():
        if deadline is not None and time.monotonic() >= deadline:
            stats["budget_stop"] = "deadline"
            raise _BudgetStop
        if node_cap is not None and stats["nodes"] >= int(node_cap):
            stats["budget_stop"] = "node_cap"
            raise _BudgetStop
        if memory_cap is not None and len(table) >= int(memory_cap):
            stats["budget_stop"] = "memory_cap"
            raise _BudgetStop

    def walk(node, depth):
        check_budget()
        stats["nodes"] += 1
        stats["max_decisions"] = max(stats["max_decisions"], depth)
        if depth > max_decisions:
            stats["budget_stop"] = "decision_bound"
            raise _BudgetStop
        key = _key(node, namespace)
        stats["peak_key_bytes"] = max(stats["peak_key_bytes"], len(key[1]))
        cached = table.get(key)
        if cached is not None and cached.status == "EXACT":
            stats["tt_hits"] += 1
            return cached
        if key in path_keys:
            stats["cycle_detected"] += 1
            return TailResult("INCOMPLETE", None, math.inf, None,
                              {**scope, "bound_proven": False}, {"reason": "cycle"})
        if node.finished:
            stats["terminals"] += 1
            result = TailResult("EXACT", int(node.state.score), int(node.state.score), [],
                                {**scope, "bound_proven": True}, {})
            table[key] = result
            return result
        actions = list(node.legal_actions())
        if not actions:
            return TailResult("INCOMPLETE", None, math.inf, None,
                              {**scope, "bound_proven": False}, {"reason": "no_legal_actions"})
        path_keys.add(key)
        best = None
        all_exact = True
        try:
            for action in actions:
                check_budget()
                child = node.clone()
                child.step(tuple(action))
                result = walk(child, depth + 1)
                if result.status != "EXACT":
                    all_exact = False
                if result.L is not None and (best is None or result.L > best[0]):
                    best = (result.L, [list(action)] + (result.suffix_for_L or []))
        except _BudgetStop:
            all_exact = False
        finally:
            path_keys.remove(key)
        if best is None:
            result = TailResult("INCOMPLETE", None, math.inf, None,
                                {**scope, "bound_proven": False}, {"reason": stats.get("budget_stop")})
        elif all_exact:
            result = TailResult("EXACT", int(best[0]), int(best[0]), best[1],
                                {**scope, "bound_proven": True}, {})
        else:
            result = TailResult("INCOMPLETE", int(best[0]), math.inf, best[1],
                                {**scope, "bound_proven": False}, {"reason": stats.get("budget_stop") or "child_incomplete"})
        if result.status == "EXACT":
            table[key] = result
        return result

    result = walk(session.clone(), 0)
    result = TailResult(
        result.status, result.L, result.U,
        list(result.suffix_for_L) if result.suffix_for_L is not None else None,
        dict(result.scope), dict(result.stats),
    )
    stats["elapsed_seconds"] = time.monotonic() - started
    stats["tt_entries_after"] = len(table)
    stats["key_bytes_total"] = sum(len(key[1]) for key in table if key[0] == namespace)
    result.stats = {**result.stats, **stats}
    return result


def replay_suffix(session, suffix):
    node = session.clone()
    for raw in suffix or ():
        action = tuple(raw)
        if node.finished or action not in node.legal_actions():
            raise ValueError(f"illegal suffix action: {action!r}")
        node.step(action)
    return node
