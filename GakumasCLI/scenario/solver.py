# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""scenario.solver - a beam search over the environment interface.

    python scenario/solver.py                # 初 PRO route, beam 6, R21 seeds
    python scenario/solver.py 12             # beam width 12
    python scenario/solver.py 6 7            # beam 6, base seed 7
"""
from __future__ import annotations

import sys
import os
import time
import copy
import hashlib
import json
import pickle
from contextvars import ContextVar
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from GakumasCLI.engine.produce_run import ProduceRun  # noqa: E402
from GakumasCLI.scenario.exam_session import ExamSession, Master, ProduceState, SegmentConfig, greedy_policy, SCORE_OBJECTIVE_VERSION  # noqa: E402

# Telemetry is scoped to one solver call, never cloned with game state.
_ACTIVE_SHUFFLE_STATS = ContextVar("shuffle_statistics", default=None)


DEFAULT_R21_CORPUS = ROOT / "ro" / "corpora" / "R21"
# under GKMS_TEMP_ROOT, else the system temp.
DEFAULT_RECORDS_DIR = Path(os.environ.get("GKMS_TEMP_ROOT") or __import__("tempfile").gettempdir()) / "segment_records"
_R21_SEEDS = (
    1221133133,
    650592160,
    1287411125,
    892421402,
    256530169,
    1072633962,
    1750782030,
    2012812433,
    407116536,
)
_R21_CARD_POOL_IDS = (
    "p_random_pool-all-upgrade_1",
    "p_random_pool-produce_007-create_set-card_play_aggressive",
    "p_random_pool-produce_007-create_set-concentration",
    "p_random_pool-produce_007-create_set-lesson_buff",
    "p_random_pool-produce_007-create_set-parameter_buff",
    "p_random_pool-produce_007-create_set-review",
    "p_random_pool-produce_007-full_power",
    "p_random_pool-rush-ssr-upgrade_1",
    "p_random_pool-rush_idol_unique_set-ssr-upgrade_1",
    "p_random_pool-ssr-upgrade_1",
)


def _jsonable(value):
    """Convert the small state/provenance objects used by records to JSON."""

    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted((_jsonable(item) for item in value), key=lambda item: repr(item))
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _digest(value) -> str:
    payload = json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _action_path_json(path):
    return [[_jsonable(part) for part in action] for action in path]


def _runtime_card(card):
    """Project a live deck card back to the ProduceRun card contract."""

    keys = (
        "number", "guid", "produce_card_id", "base_upgrade_count",
        "current_upgrade_count", "temporary_upgrade_count", "support_upgrade_ids",
        "customizes", "origin_type", "is_initial",
    )
    result = {}
    for key in keys:
        if key in card:
            result[key] = copy.deepcopy(card[key])
    result.setdefault("produce_card_id", "")
    result.setdefault("base_upgrade_count", 0)
    result.setdefault("origin_type", 0)
    result.setdefault("is_initial", False)
    return result


def _all_runtime_cards(session):
    cards = []
    seen = set()
    for zone in ("deck", "hand", "grave", "hold", "lost"):
        for card in getattr(session.deck, zone, ()):
            marker = id(card)
            if marker in seen:
                continue
            seen.add(marker)
            cards.append(_runtime_card(card))
    return cards


def _session_entry_state(session):
    state = {}
    skip = {"deck", "exam_effects", "status_enchants", "exam_triggers", "exam_setting", "gimmick_rows"}
    skip |= _SEARCH_AUDIT_STATE_FIELDS
    for key, value in vars(session.state).items():
        if key not in skip:
            state[key] = value
    server_draw = getattr(session.produce, "server_draw", None)
    if server_draw is None:
        server_draw_state = None
    else:
        server_draw_state = {
            "run_seed": getattr(server_draw, "run_seed", None),
            "observed": getattr(server_draw, "observed", {}),
            "log": getattr(server_draw, "log", []),
            "streams": {
                str(kind): getattr(stream, "state", None)
                for kind, stream in getattr(server_draw, "_streams", {}).items()
            },
        }
    return {
        "config": vars(session.config),
        "step_index": session.step_index,
        "produce": _jsonable(getattr(session, "produce", None)),
        "server_draw": server_draw_state,
        "deck": {
            "deck": [_runtime_card(card) for card in session.deck.deck],
            "hand": [_runtime_card(card) for card in session.deck.hand],
            "grave": [_runtime_card(card) for card in session.deck.grave],
            "hold": [_runtime_card(card) for card in session.deck.hold],
            "lost": [_runtime_card(card) for card in session.deck.lost],
            "rng_state": session.deck.rng.state,
        },
        "state": state,
        "turn": session.turn,
        "finished": session.finished,
        "cleared": session.cleared,
    }


def rng_trace_hash(session) -> str:
    """the RNG evidence of a replayed route -- the digest of the final state's portable encoding, which holds the RNG
    state and what every draw did; the same for a Python and a native session (the core keeps no ``deck.rng_trace``,
    the Python telemetry this hashed before)."""
    from GakumasCLI.native.native_session import portable_bytes
    return hashlib.sha256(portable_bytes(session)).hexdigest()


def entry_state_hash(session) -> str:
    """Return a stable identity for the exact session state before solving."""
    from GakumasCLI.native.native_session import portable_bytes
    produce = getattr(session, "produce", None)
    server_draw = getattr(produce, "server_draw", None)
    fixed = {
        "config": vars(session.config),
        "step_index": session.step_index,
        "produce": _jsonable(produce),
        "server_draw": None if server_draw is None else {
            "run_seed": getattr(server_draw, "run_seed", None),
            "observed": getattr(server_draw, "observed", {}),
            "log": getattr(server_draw, "log", []),
            "streams": {str(kind): getattr(stream, "state", None)
                        for kind, stream in getattr(server_draw, "_streams", {}).items()},
        },
    }
    payload = json.dumps(_jsonable(fixed), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256(payload.encode("utf-8"))
    h.update(portable_bytes(session))
    return h.hexdigest()


_SEARCH_STATIC_STATE_FIELDS = frozenset({
    "deck", "exam_effects", "status_enchants", "exam_triggers",
    "exam_setting", "gimmick_rows", "card_searches", "card_pools",
    "idol_unique_item_enchant_ids",
    "research_card_ids",
    "audition_permils",
    "card_status_enchants",
})
_SEARCH_AUDIT_STATE_FIELDS = frozenset({
    "score_trace", "review_events", "stance_events", "trigger_events",
    "gimmick_events", "unresolved_effects", "unresolved_statuses",
    "gimmick_lb_proxy_used",
    "gain_split",
})
_SEARCH_CARD_FIELDS = (
    "number", "guid", "produce_card_id", "base_upgrade_count",
    "current_upgrade_count", "temporary_upgrade_count", "support_upgrade_ids",
    "customizes", "origin_type", "is_initial", "is_end_turn_lost",
    "dynamic_grow_effect_ids", "card_enchant_fired", "_grave_source",
    "play_move_position_type", "produceCardStatusEnchantId", "playProduceExamTriggerId",
)
def search_state_key(session):
    """Return the fast, transition-complete key used by the search TT."""
    if getattr(session, "native", False):
        return session.search_key()

    deck = session.deck
    zones = tuple(
        tuple(
            tuple(card.get(field) for field in _SEARCH_CARD_FIELDS)
            for card in getattr(deck, zone, ())
        )
        for zone in ("deck", "hand", "grave", "hold", "lost")
    )
    state = tuple(
        (name, value)
        for name, value in vars(session.state).items()
        if name not in _SEARCH_STATIC_STATE_FIELDS
        and name not in _SEARCH_AUDIT_STATE_FIELDS
    )
    deck_dynamic = tuple(
        (name, getattr(deck, name))
        for name in (
            "initialized", "current_turn", "current_parameter_type",
            "turn_parameter_types", "turn_used_support_ids",
            "get_card_upgrade_count", "generated_card_count", "is_hand_hold",
            "hold_limit", "hand_limit", "turn_start_distribute",
            "dynamic_grow_by_number",
        )
        if hasattr(deck, name)
    )
    produce = tuple(
        (name, getattr(session.produce, name))
        for name in ("stamina", "max_stamina", "vocal", "dance", "visual", "trace")
        if hasattr(session.produce, name)
    )
    drinks = tuple(getattr(session, "drinks", ()) or ())
    return pickle.dumps(
        (
            session.turn,
            session.step_index,
            session.finished,
            session.cleared,
            zones,
            state,
            deck_dynamic,
            getattr(deck.rng, "state", None),
            produce,
            drinks,
        ),
        protocol=5,
    )


def _route_provenance(run):
    raw = getattr(run, "provenance", {})
    return copy.deepcopy(raw) if isinstance(raw, Mapping) else {}


def _provenance_summary(run, master_only=()):
    counts = {"a": 0, "b": 0, "c": 0}
    for entry in _route_provenance(run).values():
        if isinstance(entry, Mapping) and entry.get("source") in counts:
            counts[entry["source"]] += 1
    return counts


def _master_only_paths(session):
    """Identify master-only mechanics present on this concrete input line."""

    try:
        from GakumasCLI.engine.semantics import ENGINE_IMPLEMENTATION
    except ImportError:
        return []
    effect_status = ENGINE_IMPLEMENTATION.get("effect_types", {})
    grow_status = ENGINE_IMPLEMENTATION.get("grow_types", {})
    paths = []
    seen = set()

    def add(kind, identifier, number, status):
        if status == "master_only":
            value = f"{kind}:{identifier}"
            if value not in seen:
                seen.add(value)
                paths.append({"kind": kind, "id": identifier, "type": number, "status": status})

    def walk(value, path="input"):
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key == "produceExamEffectId":
                    effect = session.master.effects.get(child, {})
                    number = (effect.get("effectType") or {}).get("number")
                    add("effect", child, number, effect_status.get(number))
                elif key == "produceCardGrowEffectId":
                    grow = session.master.grow_effects.get(child, {})
                    number = (grow.get("growEffectType") or {}).get("number")
                    add("grow", child, number, grow_status.get(number))
                elif key == "produceCardGrowEffectIds":
                    for identifier in child or ():
                        grow = session.master.grow_effects.get(identifier, {})
                        number = (grow.get("growEffectType") or {}).get("number")
                        add("grow", identifier, number, grow_status.get(number))
                walk(child, f"{path}.{key}")
        elif isinstance(value, (list, tuple)):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")

    for zone in ("deck", "hand", "grave", "hold", "lost"):
        walk(getattr(session.deck, zone, ()), f"deck.{zone}")
    walk(getattr(session.config, "gimmick_group_id", None), "config.gimmick_group_id")
    walk(getattr(session.state, "gimmick_rows", ()), "state.gimmick_rows")
    return paths


def _input_unverified(run, master_only=()):
    for entry in _route_provenance(run).values():
        if not isinstance(entry, Mapping):
            continue
        if entry.get("source") == "c":
            return True
        locator = str(entry.get("locator") or "").lower()
        if "unverified" in locator or "approximation" in locator or "master_only" in locator:
            return True
    return bool(master_only)


def _writeback_run(session, result):
    """Store one solved segment on a ProduceRun and expose its derivation."""

    run = session.produce
    if not isinstance(run, ProduceRun) or session.step_index is None:
        return result
    index = int(session.step_index)
    snapshot = _all_runtime_cards(session)
    snapshots = getattr(run, "deck_snapshots", None)
    if not isinstance(snapshots, dict):
        snapshots = {}
        run.deck_snapshots = snapshots
    snapshots[index] = copy.deepcopy(snapshot)
    provider = getattr(run, "_provider", None)
    if provider is None or provider.__class__.__name__ != "CorpusProvider":
        run.deck_cards = [
            copy.deepcopy(card) for card in snapshot
            if card.get("origin_type") not in (4,)
        ]
    run.stamina = int(session.state.stamina)
    end_stamina = dict(getattr(run, "segment_end_stamina", {}) or {})
    end_stamina[index] = int(session.state.stamina)
    run.segment_end_stamina = end_stamina

    activations = []
    try:
        activations = run.memory_ability_enchant_ids(
            session.master,
            index,
            getattr(run, "server_draw", None),
        )
    except (AttributeError, KeyError):
        activations = []
    activation_map = dict(getattr(run, "memory_activations", {}) or {})
    activation_map[index] = list(activations)
    run.memory_activations = activation_map

    segment_provenance = dict(getattr(run, "segment_provenance", {}) or {})
    segment_provenance[index] = {
        "stamina": {"source": "derived", "locator": f"solver:segment[{index}]/end_stamina"},
        "deck_snapshot": {"source": "derived", "locator": f"solver:segment[{index}]/runtime_deck"},
        "memory_activations": {"source": "derived", "locator": f"solver:segment[{index}]/opening_statuses"},
    }
    run.segment_provenance = segment_provenance
    result["deck_snapshot"] = copy.deepcopy(snapshot)
    result["memory_activations"] = list(activations)
    result["writeback_provenance"] = copy.deepcopy(segment_provenance[index])
    return result


def score_only_value(session):
    """Return the actual terminal score for score-only-v1."""
    return int(session.state.score)


def segment_value(session):
    """Compatibility name for the active score-only-v1 objective."""
    return score_only_value(session)


def _turn_expansions(node, path, leaf_budget, stop=None):
    """All ways to finish the turn ``node`` is currently in."""
    turn = node.turn
    stack = [(node, path)]
    leaves = 0
    while stack:
        current, current_path = stack.pop()
        for action in current.legal_actions():
            if stop is not None and stop():
                return
            child = current.clone()
            child.step(action, observe=False)
            child_path = current_path + (action,)
            if child.finished or child.turn != turn:
                leaves += 1
                yield child, child_path
                if leaves >= leaf_budget:
                    return
            else:
                stack.append((child, child_path))


def rollout_value(node, value=segment_value, policy=greedy_policy):
    """Rank an unfinished node by the value it actually reaches when the
    default policy finishes the segment from there.
    """
    probe = node.clone()
    while not probe.finished:
        choice = policy(probe, list(probe.deck.hand))
        probe.step(("end",) if choice is None else ("play", int(choice)), observe=False)
    return value(probe)


def _search_budget(width, leaf_budget, max_turns, budget=None, deadline=None, expansions=None):
    """Normalize the optional budget without changing the search ordering."""

    values = {}
    if isinstance(budget, Mapping):
        values.update(budget)
    elif budget is not None:
        raise TypeError("budget must be a mapping when supplied")
    selected_width = int(values.get("width", width))
    selected_leaf_budget = int(values.get("leaf_budget", leaf_budget))
    selected_max_turns = int(values.get("max_turns", max_turns))
    selected_deadline = values.get("deadline", deadline)
    selected_expansions = values.get("expansions", expansions)
    if selected_width < 1:
        raise ValueError("beam width must be positive")
    if selected_leaf_budget < 1 or selected_max_turns < 1:
        raise ValueError("leaf_budget and max_turns must be positive")
    if selected_expansions is not None:
        selected_expansions = int(selected_expansions)
        if selected_expansions < 0:
            raise ValueError("expansions must be non-negative")
    if selected_deadline is not None:
        selected_deadline = float(selected_deadline)
        if selected_deadline < 0:
            raise ValueError("deadline must be non-negative")
    return {
        "width": selected_width,
        "leaf_budget": selected_leaf_budget,
        "max_turns": selected_max_turns,
        "deadline": selected_deadline,
        "expansions_limit": selected_expansions,
    }


def _coerce_budget_argument(width, budget):
    """Accept the ``solve_segment(session, budget)`` spelling."""

    if isinstance(width, Mapping):
        if budget is not None:
            raise TypeError("budget supplied twice")
        budget = width
        width = budget.get("width", 6)
    return width, budget


def _greedy_path(session):
    probe = session.clone()
    path = []
    while not probe.finished:
        choice = greedy_policy(probe, list(probe.deck.hand))
        action = ("end",) if choice is None else ("play", int(choice))
        path.append(action)
        probe.step(action, observe=False)
    return tuple(path), probe


def validate_initial_route(session, document):
    """Re-score a supplied complete route on an isolated current-engine fork."""
    started = time.monotonic()
    if (not isinstance(document, Mapping) or
            document.get("format") != "gkms.initial_route.v1" or
            document.get("objective_version") != SCORE_OBJECTIVE_VERSION):
        raise ValueError("initial route format/objective mismatch")
    if document.get("entry_state_hash") != entry_state_hash(session):
        raise ValueError("initial route entry_state_hash mismatch")
    raw_path = document.get("action_path")
    if not isinstance(raw_path, (list, tuple)):
        raise ValueError("initial route action_path must be a sequence")
    probe, path = session.clone(), []
    for index, raw in enumerate(raw_path):
        if (isinstance(raw, (list, tuple)) and len(raw) == 2 and raw[0] == "select" and isinstance(raw[1], (list, tuple))
                and all(type(i) is int for i in raw[1])):
            if probe.finished:
                raise ValueError(f"initial route illegal action at {index}: {tuple(raw)!r}")
            action = ("select", list(raw[1]))
            probe.step(action, observe=False)
            path.append(action)
            continue
        if (not isinstance(raw, (list, tuple)) or not raw or
                not ((len(raw) == 1 and raw[0] == "end") or
                     (len(raw) == 2 and raw[0] in ("play", "drink")
                      and type(raw[1]) is int))):
            raise ValueError(f"initial route unsupported action at {index}")
        action = tuple(raw)
        if probe.finished or action not in probe.legal_actions():
            raise ValueError(f"initial route illegal action at {index}: {action!r}")
        probe.step(action, observe=False)
        path.append(action)
    if not probe.finished:
        raise ValueError("initial route is not a complete segment")
    rng_hash = rng_trace_hash(probe)
    if document.get("rng_trace_hash") is not None and document["rng_trace_hash"] != rng_hash:
        raise ValueError("initial route rng_trace_hash mismatch")
    meta = {"source": document.get("source", "explicit_route"),
            "score": segment_value(probe), "claimed_score": document.get("score"),
            "transitions": len(path), "validation_seconds": time.monotonic() - started,
            "entry_state_hash": document["entry_state_hash"],
            "rng_trace_hash": rng_hash, "replay_verified": True}
    return probe, tuple(path), meta


def reuse_route_suffix(session, initial_route, executed_prefix, current_observation_state):
    """Reuse the remaining actions of a previously validated complete route."""
    if not isinstance(initial_route, Mapping):
        return {"status": "invalid", "reason": "rule_version_change", "turn": int(session.turn)}
    if (initial_route.get("objective_version") != SCORE_OBJECTIVE_VERSION or
            getattr(session.config, "objective_id", SCORE_OBJECTIVE_VERSION) != SCORE_OBJECTIVE_VERSION):
        return {"status": "invalid", "reason": "rule_version_change", "turn": int(session.turn)}

    _, route_path, _ = validate_initial_route(session, initial_route)
    prefix = tuple(
        tuple(action) if isinstance(action, (list, tuple)) else None
        for action in (executed_prefix or ())
    )
    predicted = session.clone()
    done = cut = 0
    for index, expected_action in enumerate(route_path):
        if done >= len(prefix):
            break
        if expected_action[0] == "select":
            predicted.step(expected_action)
            cut = index + 1
            continue
        if prefix[done] != expected_action:
            return {"status": "invalid", "reason": "operator_deviation", "turn": int(predicted.turn)}
        predicted.step(expected_action)
        done += 1
        cut = index + 1
    if len(prefix) > done:
        return {"status": "invalid", "reason": "operator_deviation", "turn": int(predicted.turn)}

    if isinstance(current_observation_state, ExamSession) or getattr(current_observation_state, "native", False):
        actual_hash = entry_state_hash(current_observation_state)
        actual = current_observation_state
    elif isinstance(current_observation_state, Mapping):
        actual_hash = current_observation_state.get("entry_state_hash")
        actual = None
    else:
        raise TypeError("current_observation_state must be an ExamSession or a mapping with entry_state_hash")
    if actual_hash != entry_state_hash(predicted):
        return {"status": "invalid", "reason": "observed_state_mismatch", "turn": int(predicted.turn)}

    replay_session = actual.clone() if actual is not None else predicted
    suffix = route_path[cut:]
    result = replay(replay_session, suffix)
    return {
        "status": "reusable",
        "suffix": _action_path_json(suffix),
        "score": int(result["score"]),
    }


def beam_search(session, width=6, value=segment_value, rank=rollout_value, leaf_budget=400, max_turns=64,
                *, budget=None, deadline=None, expansions=None, cancel=None, initial_route=None):
    """Drive ``session`` (a live, un-finished session) to the end of the
    segment by beam search over its own forks.
    """
    width, budget = _coerce_budget_argument(width, budget)
    limits = _search_budget(width, leaf_budget, max_turns, budget, deadline, expansions)
    started = time.monotonic()
    expansion_limit = limits["expansions_limit"]
    deadline_limit = limits["deadline"]
    budget_exhausted = False
    stop = lambda: (
        (cancel is not None and cancel.is_set())
        or (expansion_limit is not None and expansion_count >= expansion_limit)
        or (deadline_limit is not None and time.monotonic() - started >= deadline_limit)
    )
    frontier = [(session.clone(), ())]
    done = []
    incumbent_path, incumbent = _greedy_path(session)
    incumbent_value = value(incumbent)
    initial_meta = None
    if initial_route is not None:
        supplied, supplied_path, initial_meta = validate_initial_route(session, initial_route)
        if (value(supplied) > incumbent_value or
                (value(supplied) == incumbent_value and supplied_path < incumbent_path)):
            incumbent, incumbent_path, incumbent_value = supplied, supplied_path, value(supplied)
    incumbent_updates = 0
    expansion_count = rollouts = 0
    for _round in range(limits["max_turns"]):
        if not frontier:
            break
        if stop():
            budget_exhausted = True
            break
        children = []
        for node, path in frontier:
            for leaf, leaf_path in _turn_expansions(node, path, limits["leaf_budget"], stop=lambda: stop()):
                if expansion_limit is not None and expansion_count >= expansion_limit:
                    budget_exhausted = True
                    break
                expansion_count += 1
                if leaf.finished:
                    done.append((leaf, leaf_path))
                    candidate, candidate_path = leaf, leaf_path
                else:
                    rollouts += 1
                    rollout_path, candidate = _greedy_path(leaf)
                    candidate_path = leaf_path + rollout_path
                    rank_value = (value(candidate) if rank is rollout_value
                                  else rank(leaf, value))
                    children.append((rank_value, leaf, leaf_path))
                candidate_value = value(candidate)
                if (candidate_value > incumbent_value or
                        (candidate_value == incumbent_value and candidate_path < incumbent_path)):
                    incumbent_value = candidate_value
                    incumbent_path = candidate_path
                    incumbent = candidate
                    incumbent_updates += 1
                if stop():
                    budget_exhausted = True
                    break
            if budget_exhausted:
                break
        children.sort(key=lambda item: (-item[0], item[2]))
        frontier = [(leaf, leaf_path) for _r, leaf, leaf_path in children[:limits["width"]]]
    if frontier and not done:
        budget_exhausted = True
    if not done:
        budget_exhausted = True
    elif _round + 1 >= limits["max_turns"] and frontier:
        budget_exhausted = True
    done.sort(key=lambda item: (-value(item[0]), item[1]))
    best, path = incumbent, incumbent_path
    if done:
        done_value, done_path = value(done[0][0]), done[0][1]
        best_value = value(best)
        if done_value > best_value or (done_value == best_value and done_path < path):
            best, path = done[0]
    elapsed = time.monotonic() - started
    return best, path, {
        "objective_version": SCORE_OBJECTIVE_VERSION,
        "expansions": expansion_count,
        "rollouts": rollouts,
        "incumbent_updates": incumbent_updates,
        "initial_incumbent": initial_meta,
        "terminals": len(done),
        "search_complete": not budget_exhausted,
        "budget_exhausted": budget_exhausted,
        "stopped_reason": "cancelled" if cancel is not None and cancel.is_set() else
                          "budget" if budget_exhausted else "beam_complete",
        "elapsed_seconds": elapsed,
        "budget": {
            "width": limits["width"],
            "deadline": limits["deadline"],
            "expansions": limits["expansions_limit"],
            "leaf_budget": limits["leaf_budget"],
            "max_turns": limits["max_turns"],
        },
    }


def replay(session, path):
    """Apply a searched action path to the real session."""
    for action in path:
        session.step(action, observe=False)
    return session.result()


def exact_search(session, *, deadline=None, expansions=None, max_depth=128, cancel=None, initial_route=None):
    """Depth-first search on the deterministic engine state."""
    started = time.monotonic()
    node_limit = None if expansions is None else int(expansions)
    if node_limit is not None and node_limit < 1:
        raise ValueError("expansions must be positive")
    table = {}
    stats = {"expansions": 0, "rollouts": 0, "transposition_hits": 0, "terminals": 0,
             "stopped_reason": None, "max_depth": int(max_depth),
             "search_key": "pickle-structural-v4-drinks",
             "transposition_reopens": 0,
             "objective_version": SCORE_OBJECTIVE_VERSION}
    greedy_path, greedy_end = _greedy_path(session)
    best_value, best_path, best_session = segment_value(greedy_end), tuple(greedy_path), greedy_end
    stats["initial_incumbent"] = None
    if initial_route is not None:
        supplied, supplied_path, meta = validate_initial_route(session, initial_route)
        stats["initial_incumbent"] = meta
        candidate = segment_value(supplied)
        if candidate > best_value or (candidate == best_value and supplied_path < best_path):
            best_value, best_path, best_session = candidate, supplied_path, supplied
    complete = True

    def over_budget():
        if cancel is not None and cancel.is_set():
            stats["stopped_reason"] = "cancelled"
            return True
        if node_limit is not None and stats["expansions"] >= node_limit:
            stats["stopped_reason"] = "node_budget"
            return True
        if deadline is not None and time.monotonic() - started >= float(deadline):
            stats["stopped_reason"] = "time_budget"
            return True
        return False

    def visit(node, path, depth):
        nonlocal best_value, best_path, best_session, complete
        if over_budget():
            complete = False
            return
        if depth > max_depth:
            complete = False
            stats["stopped_reason"] = "max_depth"
            return
        key = search_state_key(node)
        prior_path = table.get(key)
        if prior_path is not None and tuple(path) >= prior_path:
            stats["transposition_hits"] += 1
            return
        if prior_path is not None:
            stats["transposition_reopens"] += 1
        table[key] = tuple(path)
        stats["expansions"] += 1
        if node.finished:
            stats["terminals"] += 1
            candidate = segment_value(node)
            if candidate > best_value or (candidate == best_value and tuple(path) < tuple(best_path)):
                best_value, best_path, best_session = candidate, tuple(path), node
            return
        actions = node.legal_actions()
        if not actions:
            complete = False
            stats["stopped_reason"] = "no_legal_actions"
            return
        preferred = greedy_policy(node, list(node.deck.hand))
        preferred_action = None if preferred is None else ("play", int(preferred))

        def action_rank(action):
            is_end = action[0] == "end"
            if action == preferred_action:
                return (0, action)
            if is_end:
                return (2, action)
            return (1, action)

        actions = sorted(actions, key=action_rank)
        for action in actions:
            if over_budget():
                complete = False
                return
            child = node.clone()
            child.step(action, observe=False)
            visit(child, path + (action,), depth + 1)

    visit(session.clone(), (), 0)
    if complete:
        stats["stopped_reason"] = "complete"
    stats["transposition_entries"] = len(table)
    stats["elapsed_seconds"] = time.monotonic() - started
    stats["search_complete"] = bool(complete)
    stats["budget"] = {"deadline": deadline, "expansions": node_limit,
                       "max_depth": int(max_depth), "mode": "deterministic-dfs"}
    return best_session, best_path, stats


def exact_tail_polish(session, path, turns, *, deadline=5.0, cancel=None):
    """Replace the last `turns` turns of a complete route by the exact optimum."""
    turns = int(turns)
    if turns < 1:
        return tuple(path), {"turns": 0, "applied": False}
    start_turn = int(session.config.limit_turn) - turns + 1
    probe, prefix, index = session.clone(), [], 0
    for action in path:
        if probe.finished or probe.turn >= start_turn:
            break
        probe.step(tuple(action), observe=False)
        prefix.append(tuple(action))
        index += 1
    suffix = [list(a) for a in path[index:]]
    meta = {"turns": turns, "start_turn": start_turn, "prefix_actions": len(prefix),
            "applied": False, "gain": 0}
    if probe.finished:
        return tuple(path), meta
    before = segment_value(replay_session(session, path))
    hint = {"format": "gkms.initial_route.v1", "objective_version": SCORE_OBJECTIVE_VERSION,
            "entry_state_hash": entry_state_hash(probe), "action_path": suffix,
            "source": "nrpa_incumbent_suffix"}
    _tail_session, tail_path, tail_stats = exact_search(probe, deadline=deadline, cancel=cancel,
                                                        initial_route=hint)
    candidate = tuple(prefix) + tuple(tuple(a) for a in tail_path)
    after = segment_value(replay_session(session, candidate))
    meta.update(exact=bool(tail_stats["search_complete"]), nodes=tail_stats["expansions"],
                seconds=tail_stats["elapsed_seconds"], before=before, after=after)
    if after > before:
        meta.update(applied=True, gain=after - before)
        return candidate, meta
    return tuple(path), meta


def replay_session(session, path):
    """``session``'s clone after ``path``."""
    probe = session.clone()
    for action in path:
        probe.step(tuple(action), observe=False)
    return probe


def solve_segment(session, width=6, value=segment_value, *, budget=None,
                  budget_seconds=None, deadline=None, expansions=None, mode="beam", search_seed=182,
                  policy_prior=None, cancel=None, initial_route=None, tail_exact_turns=2):
    """Solve one segment within the declared search budget."""
    if budget_seconds is not None:
        if deadline is not None and float(deadline) != float(budget_seconds):
            raise TypeError("budget_seconds and deadline disagree")
        deadline = budget_seconds
    width, budget = _coerce_budget_argument(width, budget)
    entry_snapshot = session.clone()
    entry_hash = entry_state_hash(session)
    search_shuffle = {"phase": "search", "computed": 0, "events": 0, "sizes": set()}
    token = _ACTIVE_SHUFFLE_STATS.set(search_shuffle)
    try:
        if mode == "exact":
            _best, path, stats = exact_search(session, deadline=deadline,
                                              expansions=expansions, cancel=cancel, initial_route=initial_route)
        elif mode == "beam":
            _best, path, stats = beam_search(
                session, width=width, value=value, budget=budget,
                deadline=deadline, expansions=expansions, cancel=cancel, initial_route=initial_route)
        elif mode in ("nrpa", "gnrpa"):
            from GakumasCLI.scenario.policy_search import policy_search
            _best, path, stats = policy_search(session, deadline=deadline, transitions=expansions,
                                               seed=search_seed, variant=mode, prior=policy_prior, cancel=cancel,
                                               initial_route=initial_route)
            if tail_exact_turns:
                path, stats["tail_polish"] = exact_tail_polish(session, path, tail_exact_turns, cancel=cancel)
        else:
            raise ValueError("mode must be 'beam', 'exact', 'nrpa' or 'gnrpa'")
    finally:
        _ACTIVE_SHUFFLE_STATS.reset(token)
    stats.update(search_shuffle_cache_hits=0,
                 search_shuffle_cache_computed=search_shuffle["computed"],
                 search_shuffle_events=search_shuffle["events"],
                 search_shuffle_sizes=sorted(search_shuffle["sizes"]))
    replay_shuffle = {"phase": "replay", "computed": 0, "events": 0, "sizes": set()}
    token = _ACTIVE_SHUFFLE_STATS.set(replay_shuffle)
    try:
        result = replay(session, path)
    finally:
        _ACTIVE_SHUFFLE_STATS.reset(token)
    result["expansions"] = stats["expansions"]
    result["rollouts"] = stats["rollouts"]
    result["action_path"] = _action_path_json(path)
    route_probe = entry_snapshot.clone()
    future_hands = [{"turn": route_probe.turn, "cards": [
        {"produce_card_id": c["produce_card_id"],
         "upgrade": int(c.get("upgrade") or 0)}
        for c in route_probe.observe().get("hand", ())]}]
    for action in path:
        before_turn = route_probe.turn
        route_probe.step(tuple(action), observe=False)
        if route_probe.turn != before_turn:
            future_hands.append({"turn": route_probe.turn, "cards": [
                {"produce_card_id": c["produce_card_id"],
                 "upgrade": int(c.get("upgrade") or 0)}
                for c in route_probe.observe().get("hand", ())]})
    result["future_hands"] = future_hands
    result["entry_state_hash"] = entry_hash
    result["budget"] = copy.deepcopy(stats["budget"])
    result["search_complete"] = bool(stats["search_complete"])
    result["search_mode"] = mode
    result["objective_version"] = SCORE_OBJECTIVE_VERSION
    result["solution_status"] = (
        "whole_path_optimal" if stats["search_complete"] and mode == "exact"
        else "best_within_budget")
    result["search_complete_means"] = (
        "deterministic tree fully traversed; whole-path optimum for this segment"
        if stats["search_complete"] and mode == "exact"
        else "best within declared budget; not an optimality proof")
    master_only = _master_only_paths(session)
    result["master_only_unverified"] = master_only
    result["unverified_input"] = _input_unverified(session.produce, master_only)
    result["provenance_summary"] = _provenance_summary(session.produce, master_only)
    result["provenance"] = _route_provenance(session.produce)
    result["draw_log"] = copy.deepcopy(getattr(session.deck, "rng_trace", ()))
    result["rng_trace_hash"] = rng_trace_hash(session)
    result["search_stats"] = copy.deepcopy(stats)
    try:
        from src.neu import semantics as _semantics
    except ImportError:
        from GakumasCLI.engine import semantics as _semantics
    by_id = {str(effect.get("id")): effect for effect in session.master.effects.values()}
    degraded = {}
    confidence_events = []
    for event in getattr(session.state, "gimmick_events", ()):
        effect_type = event.get("effect_type")
        status = _semantics.ENGINE_IMPLEMENTATION["effect_types"].get(int(effect_type), "partial") if effect_type is not None else "partial"
        turn = int(event.get("turn") or 0)
        if status != "calibrated":
            degraded.setdefault(turn, []).append({"effect_type": effect_type, "status": status,
                                                   "effect_id": event.get("effect_id")})
        confidence_events.append({"turn": turn, "effect_type": effect_type, "status": status,
                                  "effect_id": event.get("effect_id")})
    result["confidence"] = {
        "degraded_turns": [{"turn": turn, "effects": degraded[turn]}
                           for turn in sorted(degraded)],
        "degraded_turn_count": len(degraded),
        "earliest_degraded_turn": min(degraded) if degraded else None,
        "events": confidence_events,
        "basis": "engine replay gimmick_events + semantics registry",
    }
    result["transposition_hits"] = int(stats.get("transposition_hits", 0))
    result["search_shuffle_cache_hits"] = int(stats.get("search_shuffle_cache_hits", 0))
    result["search_shuffle_cache_computed"] = int(stats.get("search_shuffle_cache_computed", 0))
    result["search_shuffle_events"] = int(stats.get("search_shuffle_events", 0))
    result["search_shuffle_sizes"] = list(stats.get("search_shuffle_sizes", ()))
    result["replay_shuffle_cache_hits"] = 0
    result["replay_shuffle_cache_computed"] = replay_shuffle["computed"]
    result["replay_shuffle_events"] = replay_shuffle["events"]
    result["shuffle_cache_hits"] = result["replay_shuffle_cache_hits"]
    result["shuffle_cache_computed"] = result["replay_shuffle_cache_computed"]
    result["shuffle_events"] = result["replay_shuffle_events"]
    _writeback_run(session, result)
    return result


# ------------------------------------------------------------------ route
def _copy_produce(produce):
    return ProduceState(stamina=produce.stamina, max_stamina=produce.max_stamina,
                        deck_cards=[dict(c) for c in produce.deck_cards],
                        item_enchant_ids=list(produce.item_enchant_ids),
                        memory_ability_ids=list(produce.memory_ability_ids), plan_type=produce.plan_type,
                        item_enchant_limits=dict(getattr(produce, "item_enchant_limits", None) or {}))


def assert_r21_input_parity(run, master):
    """Assert the identity/pool/seed inputs before the first segment."""

    identity = run.identity
    expected_identity = {
        "produce_id": "produce-002",
        "idol_card_id": "i_card-hmsz-3-000",
        "plan_type": 4,
        "exam_effect_type": 47,
        "difficulty_id": "p_step_audition_difficulty-i_card-hmsz-3-000",
        "initial_deck_id": "initial_deck-full_power",
        "idol_produce_card_id": "p_card-03-ido-3_077",
        "base_vocal": 65,
        "base_dance": 70,
        "base_visual": 80,
        "base_stamina": 27,
        "before_item_id": "pitem_03-3-133-0",
        "after_item_id": "pitem_03-3-133-1",
        "exam_setting_id": "p_exam_setting-1",
        "produce_setting_id": "p_setting-2",
        "steps": 16,
    }
    actual_identity = {key: getattr(identity, key) for key in expected_identity}
    if actual_identity != expected_identity:
        raise AssertionError(f"R21 identity mismatch: {actual_identity!r}")
    actual_seeds = tuple(int(step.exam_seed_value) for step in run.steps)
    if actual_seeds != _R21_SEEDS:
        raise AssertionError(f"R21 seed mismatch: {actual_seeds!r}")
    actual_pools = tuple(sorted(getattr(master, "card_pools", {}) or {}))
    if actual_pools != _R21_CARD_POOL_IDS:
        raise AssertionError(f"R21 card-pool mismatch: {actual_pools!r}")
    return {"identity": actual_identity, "card_pools": list(actual_pools), "seeds": list(actual_seeds)}


def _build_route_source(base_seed, master_dir, provider, run, source):
    """Resolve a provider/run source while preserving the legacy state path."""

    selected = source if source is not None else provider if provider is not None else run
    if selected is None and base_seed is not None and not isinstance(base_seed, (int, str)):
        selected = base_seed
        base_seed = None
    if isinstance(selected, ProduceState):
        return None, selected, False
    if isinstance(selected, ProduceRun):
        return getattr(selected, "_provider", None), selected, True
    if selected is None:
        if base_seed is not None:
            from GakumasCLI.engine.providers.self_drive import SelfDriveProvider, default_r21_spec
            selected = SelfDriveProvider(default_r21_spec(run_seed=int(base_seed)), master_dir=master_dir)
        else:
            from GakumasCLI.engine.providers.corpus import CorpusProvider
            selected = CorpusProvider(DEFAULT_R21_CORPUS, master_dir=master_dir)
    if not callable(getattr(selected, "prepare_step", None)) and not callable(getattr(selected, "build", None)):
        raise TypeError("solve_route source must be a provider, ProduceRun, or ProduceState")
    selected_run = run if isinstance(run, ProduceRun) else None
    if selected_run is None:
        try:
            selected_run = selected.run
        except RuntimeError:
            selected_run = selected.build(master_dir)
    if not isinstance(selected_run, ProduceRun):
        raise TypeError("provider.run must be a ProduceRun")
    return selected, selected_run, True


def _prepare_provider_step(provider, run, index):
    if provider is None:
        return run.step(index)
    return provider.prepare_step(index, run)


def segment_record(result, *, index=None):
    """Project a solver result to the stable per-segment JSONL schema."""

    record = {
        "objective_version": result.get("objective_version"),
        "segment_index": index,
        "segment": result.get("segment"),
        "entry_state_hash": result.get("entry_state_hash"),
        "action_path": copy.deepcopy(result.get("action_path", [])),
        "score": result.get("score"),
        "stamina": result.get("stamina"),
        "draw_log": copy.deepcopy(result.get("draw_log", [])),
        "rng_trace_hash": result.get("rng_trace_hash"),
        "budget": copy.deepcopy(result.get("budget", {})),
        "expansions": result.get("expansions", 0),
        "search_complete": bool(result.get("search_complete", False)),
        "solution_status": result.get("solution_status", "best_within_budget"),
        "provenance_summary": copy.deepcopy(result.get("provenance_summary", {"a": 0, "b": 0, "c": 0})),
        "unverified_input": bool(result.get("unverified_input", False)),
        "provenance": copy.deepcopy(result.get("provenance", {})),
        "master_only_unverified": copy.deepcopy(result.get("master_only_unverified", [])),
        "memory_activations": copy.deepcopy(result.get("memory_activations", [])),
        "deck_snapshot": copy.deepcopy(result.get("deck_snapshot", [])),
        "writeback_provenance": copy.deepcopy(result.get("writeback_provenance", {})),
    }
    return _jsonable(record)


def write_segment_records(records, records_dir=DEFAULT_RECORDS_DIR, route_name="route"):
    """Write one UTF-8 JSONL file for a solved route and return its path."""

    directory = Path(records_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (str(route_name) if str(route_name).endswith(".jsonl") else f"{route_name}.jsonl")
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for record in records:
            stream.write(json.dumps(_jsonable(record), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return path


def read_segment_records(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def replay_record(session, record):
    """Replay a persisted path on a fresh session and check score/stamina."""

    expected_entry = record.get("entry_state_hash")
    if expected_entry and entry_state_hash(session) != expected_entry:
        raise AssertionError("segment record entry_state_hash does not match replay session")
    result = replay(session, record.get("action_path", ()))
    for key in ("score", "stamina"):
        if key in record and result.get(key) != record[key]:
            raise AssertionError(f"segment record {key} mismatch: {result.get(key)!r} != {record[key]!r}")
    _writeback_run(session, result)
    return result


def solve_route(base_seed=None, width=6, master_dir=ROOT / "ro" / "master" / "tables", verbose=True,
                *, provider=None, run=None, source=None, records_dir=DEFAULT_RECORDS_DIR, budget=None,
                route_name=None):
    """Solve every segment from a provider/ProduceRun and persist its records."""

    provider, produce, is_new_run = _build_route_source(base_seed, master_dir, provider, run, source)
    if not is_new_run:
        from GakumasCLI.scenario import hajime_pro as H
        master = Master.load(master_dir)
        rows = []
        for index, row in enumerate(H.resolve_route(master_dir), 1):
            sid, stype, limit, cap, ptype, seed, gimmick, permils, values, final = row
            joining = [dict(c) for c in H.MEMORY_CARDS] if index == 5 else []
            produce.deck_cards.extend(joining)
            config = SegmentConfig(
                segment_id=sid,
                segment_type=stype,
                limit_turn=limit,
                score_cap=cap,
                parameter_type=ptype,
                seed=seed if base_seed is None else int(base_seed) + index,
                gimmick_group_id=gimmick,
                audition_permils=permils,
                audition_parameter_values=values,
                final_audition=final,
            )
            greedy_result = ExamSession(master, _copy_produce(produce), config).run(greedy_policy)
            started = time.time()
            beam_result = solve_segment(ExamSession(master, produce, config), width=width, budget=budget)
            elapsed = time.time() - started
            rows.append((index, stype, greedy_result, beam_result, elapsed))
            if verbose:
                print(f"s{index} {stype:20} greedy score={greedy_result['score']:>6} cleared={str(greedy_result['cleared']):5} "
                      f"stamina={greedy_result['stamina']:>2} | beam score={beam_result['score']:>6} "
                      f"cleared={str(beam_result['cleared']):5} stamina={beam_result['stamina']:>2} "
                      f"unresolved={len(beam_result['unresolved'])} rollouts={beam_result['rollouts']:>5} {elapsed:6.1f}s")
        return rows

    master = Master.load(master_dir, exam_setting_id=produce.identity.exam_setting_id)
    parity = None
    from GakumasCLI.engine.providers.corpus import CorpusProvider
    if isinstance(provider, CorpusProvider) and produce.identity.produce_id == "produce-002" and produce.identity.idol_card_id == "i_card-hmsz-3-000":
        parity = assert_r21_input_parity(produce, master)
    rows = []
    records = []
    for index, step in enumerate(produce.steps, 1):
        _prepare_provider_step(provider, produce, step.index)
        config = produce.segment_config(master, step.index)
        session = ExamSession(master, produce, config)
        if parity is not None:
            if session.state.plan_type != parity["identity"]["plan_type"]:
                raise AssertionError("solver session plan_type diverged from R21 identity")
            if session.state.card_pools is not master.card_pools:
                raise AssertionError("solver session did not receive the R21 card pools")
            if int(config.seed) != parity["seeds"][index - 1]:
                raise AssertionError("solver session seed diverged from R21 parity")
        greedy_probe = session.clone()
        greedy_result = greedy_probe.run(greedy_policy)
        started = time.time()
        beam_result = solve_segment(session, width=width, budget=budget)
        elapsed = time.time() - started
        rows.append((index, step.step_type, greedy_result, beam_result, elapsed))
        records.append(segment_record(beam_result, index=index))
        if verbose:
            print(f"s{index} {step.step_type:20} greedy score={greedy_result['score']:>6} cleared={str(greedy_result['cleared']):5} "
                  f"stamina={greedy_result['stamina']:>2} | beam score={beam_result['score']:>6} "
                  f"cleared={str(beam_result['cleared']):5} stamina={beam_result['stamina']:>2} "
                  f"unresolved={len(beam_result['unresolved'])} rollouts={beam_result['rollouts']:>5} {elapsed:6.1f}s")
    selected_name = route_name
    if selected_name is None:
        selected_name = "r21" if parity is not None else "route"
    record_path = write_segment_records(records, records_dir, selected_name)
    if verbose and rows:
        gc = sum(1 for _i, _t, g, _b, _e in rows if g["cleared"])
        bc = sum(1 for _i, _t, _g, b, _e in rows if b["cleared"])
        print(f"TOTAL greedy(same entry) final={rows[-1][2]['score']} cleared={gc}/{len(rows)} | "
              f"beam final={rows[-1][3]['score']} cleared={bc}/{len(rows)} "
              f"(beam width {width}, {sum(r[4] for r in rows):.1f}s; records={record_path})")
    return rows


if __name__ == "__main__":
    width = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    seed = sys.argv[2] if len(sys.argv) > 2 else None
    solve_route(base_seed=seed, width=width)
