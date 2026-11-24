# Copyright (C) 2026 花吹雪また一年P and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""H.I.F live link: follow one round of the user's own game."""
from __future__ import annotations

import threading
import time
from collections import Counter

import pickle

from GakumasCLI.scenario.search_context import SearchContext
from GakumasCLI.scenario import solver as SV
from GakumasCLI.scenario.exam_session import SCORE_OBJECTIVE_VERSION, preview_action, preview_hand
from GakumasCLI.scenario.policy_search import load_prior

def _route_search():
    """``scenario/hif_route_search.py`` holds the seed pool and the search entry; imported here, not at module import,
    because it imports this module."""
    from GakumasCLI.scenario import hif_route_search
    return hif_route_search


def _master_refs(master):
    """every object the master owns that a state can point at -- each table and each row of a table -- keyed by id, for
    pickling them as references instead of copies."""
    refs = {}
    for name, table in vars(master).items():
        if isinstance(table, dict):
            refs[id(table)] = ("t", name)
            for key, row in table.items():
                if isinstance(row, (dict, list)):
                    refs.setdefault(id(row), ("r", name, key))
        elif isinstance(table, list):
            refs[id(table)] = ("t", name)
            for index, row in enumerate(table):
                if isinstance(row, (dict, list)):
                    refs.setdefault(id(row), ("i", name, index))
    return refs

class _MasterPickler(pickle.Pickler):
    def __init__(self, file, refs):
        super().__init__(file, protocol=5)
        self._refs = refs

    def persistent_id(self, obj):
        return self._refs.get(id(obj))


class _MasterUnpickler(pickle.Unpickler):
    def __init__(self, file, master):
        super().__init__(file)
        self._master = master

    def persistent_load(self, pid):
        kind, name = pid[0], pid[1]
        table = getattr(self._master, name)
        return table if kind == "t" else table[pid[2]]


def dump_session(session):
    """Serialise a live session so a pool worker can search from it."""
    from GakumasCLI.native.native_session import native_dump
    return native_dump(session)


def load_session(blob, master):
    """Revive a session from :func:`dump_session` against this process's master."""
    from GakumasCLI.native import native_session as _ns
    return _ns.native_load(blob, master)


LIVE_SCHEMA = "gkms.hif_live.v1"
DEFAULT_SEARCH_SEEDS = 19
DEFAULT_OPEN_SECONDS = 180
DEFAULT_OPEN_SECONDS_BY_PRODUCE = {"produce-008": 430, "produce-007": 180}
DEFAULT_OPEN_RUNS = 3
OPEN_RUN_SEED_STRIDE = 100000
QUICK_REASONS = ("open_quick", "resolve_quick")
QUICK_SEED_OFFSET = 500000
ALGORITHMS = ("default", "alns10")
WAVE_SHAPES = ("short", "wide")
ALNS_REASONS = ("open", "resolve_deep", "main", "main_moved", "manual")
from GakumasCLI.produce.produce_effects import SELECTION_STAR_LINE as SELECTION_STAR_CAP  # noqa: E402


def hif_round_budget(loadout):
    """(open_seconds, score_cap) for a H.I.F loadout document."""
    produce_id = str((loadout or {}).get("produce_id") or "produce-008")
    from GakumasCLI.scenario import nia
    if nia.is_nia(produce_id):
        return nia.round_budget(loadout)
    from GakumasCLI.scenario import hajime
    if hajime.is_hajime(produce_id):
        return hajime.round_budget(loadout)
    from GakumasCLI.scenario import tower
    if tower.is_tower(produce_id):
        return tower.round_budget(loadout)
    seconds = DEFAULT_OPEN_SECONDS_BY_PRODUCE.get(produce_id, DEFAULT_OPEN_SECONDS)
    cap = None
    if produce_id == "produce-007":
        cap = SELECTION_STAR_CAP.get(str((loadout or {}).get("round") or "final").lower())
    return seconds, cap
DEFAULT_RESOLVE_SECONDS = 20
DEFAULT_TAIL_SECONDS = 20
DEFAULT_RESOLVE_TAIL_SECONDS = 5
DEFAULT_SEARCH_SEED = 182
DEFAULT_OPEN_RESTARTS = 4
DEFAULT_RESOLVE_RESTARTS = 1
DEFAULT_STABILIZATION = 3

SCALAR_FIELDS = ("score", "stamina", "block", "playable", "turn", "full_power_point",
                 "lesson_buff", "review", "aggressive", "stance", "extra_turns",
                 "deck_count", "grave_count")
HARD_FIELDS = ("score", "stamina", "block", "playable", "turn", "full_power_point",
               "lesson_buff", "review", "aggressive", "stance", "extra_turns",
               "hand", "hold", "action", "select", "deck_count", "grave_count")


class LiveActionError(ValueError):
    """An observed action the engine cannot map onto its own state."""

    def __init__(self, reason, detail=None):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail or {}


# ------------------------------------------------------------------ helpers
def card_label(session, instance):
    card = instance.get("resolved_card") or session.deck.refresh_card(instance)
    name = card.get("name") or instance.get("produce_card_id")
    return name.rstrip("+") + "+" * int(instance.get("current_upgrade_count") or 0)


def action_text(session, action):
    """Human text of an engine action *at the current state of* ``session``."""
    action = tuple(action)
    if action[0] == "play":
        index = int(action[1])
        if 0 <= index < len(session.deck.hand):
            return f"play {card_label(session, session.deck.hand[index])}"
        return f"play #{index} (not in hand)"
    if action[0] == "drink":
        index = int(action[1])
        if 0 <= index < len(session.drinks):
            drink_id = session.drinks[index]
            return f"drink {session.master.drinks.get(drink_id, {}).get('name', drink_id)}"
        return f"drink #{index} (not held)"
    if action[0] == "select":
        return f"select {list(action[1])}"
    return "end turn"


def same_copy(session, a, b):
    """plays A and B take two hand cards alike in everything but their number (and guid) whose removal leaves the same
    hand (card ids and upgrades in order) -- what the client shows after either is the same."""
    if a[0] != "play" or b[0] != "play" or len(a) != 2 or len(b) != 2:
        return False
    hand = session.deck.hand
    i, j = int(a[1]), int(b[1])
    if i == j or not (0 <= i < len(hand) and 0 <= j < len(hand)):
        return False
    strip = lambda card: {k: v for k, v in card.items() if k not in ("number", "guid")}     # noqa: E731
    if strip(hand[i]) != strip(hand[j]):
        return False
    ids = [(c.get("produce_card_id"), int(c.get("current_upgrade_count") or 0)) for c in hand]
    return ids[:i] + ids[i + 1:] == ids[:j] + ids[j + 1:]


def resolve_action(session, action):
    """Normalise one observed action to an engine tuple."""
    picks = []
    if isinstance(action, (list, tuple)):
        if not action:
            raise LiveActionError("empty_action")
        kind = action[0]
        if kind == "select":
            return ("select", [int(i) for i in action[1]]), picks, []
        if kind == "end":
            return ("end",), picks, []
        if kind in ("play", "drink") and len(action) == 2:
            return (kind, int(action[1])), picks, []
        raise LiveActionError("unsupported_action", {"action": list(action)})
    if not isinstance(action, dict):
        raise LiveActionError("unsupported_action", {"action": repr(action)})
    kind = action.get("kind")
    for entry in action.get("select") or ():
        picks.append(("select", [int(i) for i in entry]))
    if kind == "end":
        return ("end",), picks, []
    if kind == "select":
        return ("select", [int(i) for i in action.get("indexes") or ()]), picks, []
    if kind == "play":
        hand = session.deck.hand
        if action.get("index") is not None:
            index = int(action["index"])
            if not 0 <= index < len(hand):
                raise LiveActionError("hand_index_out_of_range",
                                      {"index": index, "hand_size": len(hand)})
            return ("play", index), picks, []
        if action.get("number") is not None:
            number = action["number"]
            for index, instance in enumerate(hand):
                if instance.get("number") == number:
                    return ("play", index), picks, []
            raise LiveActionError("card_number_not_in_hand",
                                  {"number": number,
                                   "hand_numbers": [c.get("number") for c in hand]})
        if action.get("card_id") is not None:
            card_id, upgrade = action["card_id"], action.get("upgrade")
            matches = [i for i, c in enumerate(hand)
                       if c.get("produce_card_id") == card_id
                       and (upgrade is None or int(c.get("current_upgrade_count") or 0) == int(upgrade))]
            if not matches:
                raise LiveActionError("card_id_not_in_hand",
                                      {"card_id": card_id,
                                       "hand": [c.get("produce_card_id") for c in hand]})
            playable = [i for i in matches if session.can_play(hand[i])]
            chosen = playable or matches
            return ("play", chosen[0]), picks, [("play", i) for i in chosen]
        raise LiveActionError("play_needs_index_number_or_card_id", {"action": action})
    if kind == "drink":
        if action.get("index") is not None:
            index = int(action["index"])
            if not 0 <= index < len(session.drinks):
                raise LiveActionError("drink_index_out_of_range",
                                      {"index": index, "drinks": list(session.drinks)})
            return ("drink", index), picks, []
        if action.get("drink_id") is not None:
            drink_id = action["drink_id"]
            held = [i for i, d in enumerate(session.drinks) if d == drink_id]
            if not held:
                raise LiveActionError("drink_not_held",
                                      {"drink_id": drink_id, "drinks": list(session.drinks)})
            return ("drink", held[0]), picks, [("drink", i) for i in held]
        raise LiveActionError("drink_needs_index_or_drink_id", {"action": action})
    raise LiveActionError("unknown_action_kind", {"kind": kind})


def apply_prefix(session, prefix):
    """Replay an executed prefix (engine tuples, ``select`` entries queue the
    pick for the play that follows) onto ``session`` in place."""
    for action in prefix:
        action = tuple(action)
        if action[0] == "select":
            session.state.select_index_queue.append(list(action[1]))
        else:
            session.step(action, observe=False)
    return session


def route_plays(route):
    """a route's actions without its ``("select", [...])`` steps -- what the plays done are compared with."""
    return [a for a in route or () if a[0] != "select"]


def route_after(route, plays):
    """the index in ``route`` right after its first ``plays`` plays -- the select steps of the next play stay in what is
    left; ``len(route)`` when the route holds no more."""
    done = 0
    for index, action in enumerate(route or ()):
        if done >= plays:
            return index
        if action[0] != "select":
            done += 1
    return len(route or ())


def route_table(session, route):
    """The plan as the panel shows it: one row per turn with the hand at turn
    start, each action's text and gain, and the state after the turn.
    """
    probe = session.clone()
    if getattr(probe.deck, "select_choices", None) is None:
        probe.deck.select_choices = []
    seen = len(probe.deck.select_choices)
    split = callable(getattr(probe, "split_sources", None))
    rows, turn = [], None
    index = -1
    for action in route:
        action = tuple(action)
        if action[0] == "select":
            probe.step(("select", list(action[1])), observe=False)
            continue
        index += 1
        if probe.turn != turn:
            turn = probe.turn
            rows.append({"turn": turn,
                         "hand": [card_label(probe, c) for c in probe.deck.hand],
                         "hand_ids": [c.get("produce_card_id") for c in probe.deck.hand],
                         "hand_numbers": [c.get("number") for c in probe.deck.hand],
                         "start_score": probe.state.score,
                         "start_stamina": probe.state.stamina,
                         "actions": []})
        before = probe.state.score
        text = action_text(probe, action)
        if split:
            probe._split(True)
        probe.step(action, observe=False)
        entry = {"index": index, "action": list(action), "text": text,
                 "gain": probe.state.score - before, "score": probe.state.score,
                 "stamina": probe.state.stamina}
        if split:
            entry["sources"] = probe.split_sources()
            probe._split(False)
        if len(probe.deck.select_choices) > seen:
            entry["select"] = [dict(c) for c in probe.deck.select_choices[seen:]]
            seen = len(probe.deck.select_choices)
        rows[-1]["actions"].append(entry)
        rows[-1].update(score=probe.state.score, stamina=probe.state.stamina,
                        block=probe.state.block)
    return rows, probe


def _alternatives(session):
    rows = []
    for row in preview_hand(session):
        action = tuple(row["action"])
        entry = {"action": list(action), "text": action_text(session, action),
                 "legal": row["legal"], "reason": row["reason"]}
        if row["legal"]:
            entry["after_action"] = row["after_action"]
            entry["after_settlement"] = row["after_settlement"]
            entry["gain_after_turn"] = row["after_settlement"]["gain"]
        rows.append(entry)
    return rows


def _resync_apply(session, state):
    """LiveRound.resync's writes on ``session`` (a Python one: a native session's shadow)."""
    view = session.observe()
    applied = {}
    for zone in ("hand", "hold"):
        key = f"{zone}_numbers"
        if state.get(key) is not None:
            cards = getattr(session.deck, zone)
            by_number = {c["number"]: c for c in cards}
            if Counter(state[key]) != Counter(by_number):
                raise LiveActionError(f"{zone}_set_mismatch",
                                      {"engine": sorted(by_number), "observed": sorted(state[key])})
            cards[:] = [by_number[n] for n in state[key]]
            applied[key] = list(state[key])
    for field in ("score", "stamina", "block"):
        if state.get(field) is not None and view[field] != state[field]:
            setattr(session.state, field, int(state[field]))
            applied[field] = int(state[field])
    return view, applied


# --------------------------------------------------------------- LiveRound
class LiveRound:
    """One H.I.F round followed live."""

    def __init__(self, master, loadout=None, *, session=None, meta=None,
                 prior=None, prior_path=None,
                 search_seeds=DEFAULT_SEARCH_SEEDS, open_seconds=None,
                 resolve_seconds=DEFAULT_RESOLVE_SECONDS, tail_seconds=DEFAULT_TAIL_SECONDS,
                 search_seed=DEFAULT_SEARCH_SEED, master_dir=None, pool=None,
                 auto_resolve=True, open_restarts=DEFAULT_OPEN_RESTARTS,
                 resolve_restarts=DEFAULT_RESOLVE_RESTARTS, stabilization=DEFAULT_STABILIZATION,
                 resolve_tail_seconds=DEFAULT_RESOLVE_TAIL_SECONDS, strategy="er", on_wave=None,
                 score_cap=None, open_runs=None, algorithm="default", timeline=None,
                 er_job_seconds=None, repetition_limit=0, split=None, goal=None, wave_shape="short",
                 helper_feed=None):
        """``loadout`` builds the session here; ``session`` adopts one built elsewhere (and the live link rides alongside
        that session rather than building a second one from a document).
        """
        if (loadout is None) == (session is None):
            raise ValueError("pass exactly one of loadout= or session=")
        self.master = master
        self.loadout = None if loadout is None else dict(loadout)
        if loadout is not None:
            from GakumasCLI.native.native_session import make_session
            self.session, self.meta = make_session(master, self.loadout)
        else:
            self.session, self.meta = session, dict(meta or {})
            deck = self.session.deck
            if getattr(deck, "select_policy", None) is None:
                from GakumasCLI.engine.card_data import greedy_select_policy
                deck.select_policy = greedy_select_policy(self.master.cards)
            if getattr(deck, "select_choices", None) is None:
                deck.select_choices = []
        self.entry = self.session.clone()
        self.prior_path = prior_path
        self.prior = prior if prior is not None else (load_prior(prior_path) if prior_path else None)
        self.search_seeds = max(1, int(search_seeds))
        budget_seconds, budget_cap = hif_round_budget(dict(self.loadout or {}, **self.meta))
        self.open_seconds = float(budget_seconds if open_seconds is None else open_seconds)
        self.score_cap = budget_cap if score_cap is None else int(score_cap)
        if open_runs is None and self.meta.get("open_runs") is not None:
            open_runs = self.meta["open_runs"]
        self.open_runs = max(1, int((DEFAULT_OPEN_RUNS if self.loadout is not None and self.meta.get("scenario") != "hajime" else 1)
                                    if open_runs is None else open_runs))
        self.resolve_seconds = float(resolve_seconds)
        self.tail_seconds = float(tail_seconds)
        self.resolve_tail_seconds = float(resolve_tail_seconds)
        self.strategy = strategy
        self.on_wave = on_wave
        if er_job_seconds is None:
            er_job_seconds = self.meta.get("er_job_seconds")
        self.er_job_seconds = None if er_job_seconds is None else float(er_job_seconds)
        self._last_er_waves = None
        self.wave_shape = wave_shape if wave_shape in WAVE_SHAPES else "short"
        self._last_wave_shape = None
        self.helper_feed = helper_feed
        self._last_helper_feed = None
        self.gate_status = None
        self.cancel = None
        self.cancel_key = None
        self.incumbent_route = None
        self.algorithm = algorithm if algorithm in ALGORITHMS else "default"
        self._last_alns = None
        self.search_seed = int(search_seed)
        self.master_dir = str(master_dir or master.master_dir)
        self.auto_resolve = bool(auto_resolve)
        self.open_restarts = max(1, int(open_restarts))
        self.resolve_restarts = max(1, int(resolve_restarts))
        self.stabilization = max(1, int(stabilization))
        self.repetition_limit = max(0, int(repetition_limit or 0))
        self._pool = pool
        self._owns_pool = pool is None
        self.timeline = None
        self.rules = None
        self._rule_cache = None
        self._last_sweep = None
        self.split = None
        self.goal = None
        self._free_best = None
        if timeline:
            self.apply_timeline(timeline)
        if split:
            self.apply_split(split)
        if goal:
            self.apply_goal(goal)
        self.executed = []
        self.plan = None
        self.plans = []
        self.plan_session = None
        self.plan_doc = None
        self.ledger = []
        self.observations = []
        self.desynced = False
        self.first_divergence = None
        self.finished_observed = None
        self.opened_at = None
        self._stand_in = None

    # -- timeline -------------------------------------------------------
    def apply_timeline(self, plan):
        """Install a RoundPlan: its budget fields replace this round's defaults, its rules go to every search
        from the next solve on.
        """
        if not plan:
            return
        self.timeline = dict(plan)
        b = plan.get("budget") or {}
        if "open_seconds" in b:
            self.open_seconds = float(b["open_seconds"])
        if "open_runs" in b:
            self.open_runs = max(1, int(b["open_runs"]))
        if "restarts" in b:
            self.open_restarts = max(1, int(b["restarts"]))
        if "stabilization" in b:
            self.stabilization = max(1, int(b["stabilization"]))
        if "repetition_limit" in b:
            self.repetition_limit = max(0, int(b["repetition_limit"]))
        if "resolve_seconds" in b:
            self.resolve_seconds = float(b["resolve_seconds"])
        if "open_tail_seconds" in b:
            self.tail_seconds = float(b["open_tail_seconds"])
        if "resolve_tail_seconds" in b:
            self.resolve_tail_seconds = float(b["resolve_tail_seconds"])
        if "stop_score" in b:
            self.score_cap = int(b["stop_score"]) or None
        if "max_cores" in b:
            self.search_seeds = max(1, min(self.search_seeds, int(b["max_cores"])))
        if plan.get("algorithm") in ALGORITHMS:
            self.algorithm = plan["algorithm"]
        self._rebuild_rules()

    def apply_split(self, split):
        """控分 -- the split's rules go to every search from the next solve on (None: clear it)."""
        from GakumasCLI.scenario import timeline as TL
        self.split, _errors = TL.split_check(split)
        self._rebuild_rules()

    def apply_goal(self, goal):
        """审查目标 -- a resolved goal's rules go to every search from the next solve on (None: by score)."""
        self.goal = dict(goal) if goal else None
        self._rebuild_rules()

    def _rebuild_rules(self):
        """The timeline's rules, then the split's, then the goal's; None when there are none."""
        from GakumasCLI.scenario import timeline as TL
        rules = (list((self.timeline or {}).get("rules") or ()) + TL.split_rules(getattr(self, "split", None))
                 + TL.goal_rules(getattr(self, "goal", None)))
        self.rules = TL.RuleSet(rules) if rules else None
        self._rule_cache = None

    def rule_state(self):
        """The rules' state after what has been played (replayed from the round's entry)."""
        if self.rules is None:
            return None
        key = repr(self.executed)
        if self._rule_cache is not None and self._rule_cache[0] == key:
            return self._rule_cache[1].copy()
        node, rs = self.entry.clone(), self.rules.start()
        for a in self.executed:
            if node.finished:
                break
            self.rules.advance(rs, node, a)
        self._rule_cache = (key, rs.copy())
        return rs

    def _tl(self, track, key, default=None):
        return ((getattr(self, "timeline", None) or {}).get(track) or {}).get(key, default)

    # -- lifecycle -----------------------------------------------------------
    def open(self, seconds=None):
        self.opened_at = time.time()
        self.solve(seconds=self.open_seconds if seconds is None else seconds, reason="open")
        return self.advice()

    def close(self):
        if self._pool is not None and self._owns_pool:
            self._pool.close()
        self._pool = None

    def _ensure_pool(self):
        if self.search_seeds <= 1:
            return None
        if self._pool is None:
            SeedPool = _route_search().SeedPool
            self._pool = SeedPool(self.search_seeds, self.prior_path, master_dir=self.master_dir)
        return self._pool

    # -- solving ---------------------------------------------------------------
    def _plays_only(self, actions):
        return [tuple(a) for a in actions if a[0] != "select"]

    def solve(self, seconds=None, reason="manual", seeds=None, restarts=None, tail_seconds=None):
        """Search from the *current* engine state and install the result as the plan."""
        _search_paths, _valid_score = _route_search()._search_paths, _route_search()._valid_score
        if self.session.finished:
            return None
        seconds = self.resolve_seconds if seconds is None else float(seconds)
        seeds = self.search_seeds if seeds is None else max(1, int(seeds))
        if restarts is None:
            restarts = self.open_restarts if reason == "open" else self.resolve_restarts
        restarts = max(1, int(restarts))
        if tail_seconds is None:
            tail_seconds = self.tail_seconds if reason == "open" else self.resolve_tail_seconds
        tail_seconds = float(tail_seconds)
        started = time.monotonic()
        session = self.session
        seed0 = self.search_seed + (QUICK_SEED_OFFSET if reason in QUICK_REASONS else 0)
        pool = self._ensure_pool() if seeds > 1 else None
        seed_results, path, best, steps = [], None, None, []
        rejected = []
        self._free_best = None
        self._last_er_waves = None
        self._last_alns = None
        self._last_side_by_side = 1
        self._last_wave_shape = None
        self._last_helper_feed = None
        rules_here = None
        if self.rules is not None:
            rules_here = self.rules.with_state(self.rule_state())
        tail_turns = self._tl("exhaust", "tail_turns")
        if self.timeline is not None and self._tl("exhaust", "tail_seconds") is not None:
            tail_seconds = float(self._tl("exhaust", "tail_seconds"))
        tl_extra = {}
        if rules_here is not None:
            tl_extra["rules"] = rules_here
        if tail_turns is not None:
            tl_extra["tail_turns"] = int(tail_turns)
        self._last_sweep = None
        if pool is None:
            path, best, steps = _search_paths(session, None, nrpa_seconds=seconds,
                                              tail_seconds=tail_seconds,
                                              seed=seed0, prior=self.prior,
                                              from_scratch=True, restarts=restarts,
                                              stabilization=self.stabilization, **tl_extra)
            seed_results = [{"seed": seed0, "best": best, "status": "ok"}]
        else:
            if self.loadout is not None:
                shared = {"loadout": self.loadout,
                          "prefix": [list(a) if a[0] != "select" else ["select", list(a[1])]
                                     for a in self.executed]}
            else:
                shared = {"session_blob": dump_session(session)}
            if rules_here is not None:
                rs0 = self.rules.start() if self.loadout is not None else rules_here.start()
                shared["rules"] = {"rules": self.rules.rules, "state": rs0.as_dict()}
            if tail_turns is not None:
                shared["tail_turns"] = int(tail_turns)
            HRS = _route_search()
            er_job = self._tl("budget", "er_job_seconds")
            if er_job is None:
                er_job = getattr(self, "er_job_seconds", None)
            job_tail = HRS.ER_JOB_TAIL_SECONDS
            tl_tail = self.timeline is not None and self._tl("exhaust", "tail_seconds") is not None
            if tl_tail:
                job_tail = float(self._tl("exhaust", "tail_seconds"))
            waves = HRS.er_waves(seconds + tail_seconds, er_job, job_tail) if self.strategy == "er" else 1
            if waves > 1:
                base_job = dict(shared, row=None, drinks_mode="all",
                                nrpa_seconds=HRS.ER_JOB_SECONDS if er_job is None else float(er_job),
                                tail_seconds=job_tail, restarts=restarts,
                                stabilization=self.stabilization)
                if self.cancel_key is not None:
                    base_job["cancel"] = list(self.cancel_key)
                if self.repetition_limit:
                    base_job["repetition_limit"] = self.repetition_limit
                base_prefix = base_job.pop("prefix", [])
                runs = self.open_runs if reason == "open" else 1
                alns = self.alns_rounds_for(reason)
                width = min(seeds, self.search_seeds)
                together = HRS.side_by_side(pool, width, runs)
                groups = [list(range(g, min(runs, g + together))) for g in range(0, runs, together)]
                self._last_side_by_side = together
                group_jobs, group_waves = [], []
                for group in groups:
                    share = max(1, width // len(group))
                    job, tail, short = base_job["nrpa_seconds"], base_job["tail_seconds"], False
                    if self._tl("budget", "er_job_seconds") is None and not tl_tail:
                        floor = HRS.HIF_SHORT_JOB_MIN if er_job is None else HRS.SHORT_JOB_MIN
                        job, tail, short = HRS.shaped_job(pool, share, job, tail, self.wave_shape, floor)
                    group_jobs.append(dict(base_job, nrpa_seconds=job, tail_seconds=tail) if short else base_job)
                    group_waves.append(HRS.er_waves(seconds + tail_seconds, job, tail) if short else waves)
                    if short and self._last_wave_shape is None:
                        self._last_wave_shape = {"shape": self.wave_shape, "job": job, "tail": tail,
                                                 "base_job": base_job["nrpa_seconds"],
                                                 "base_tail": base_job["tail_seconds"], "width": share,
                                                 "local": int(getattr(pool, "size", 0) or 0),
                                                 "lag": round(float(getattr(pool, "lag", 0.0) or 0.0), 2),
                                                 "waves": group_waves[-1], "base_waves": waves}
                offsets = [sum(group_waves[:k]) for k in range(len(groups))]
                total_waves = sum(group_waves) + alns
                local = int(getattr(pool, "size", 0) or 0)
                feed_mode = self.helper_feed if self.helper_feed in HRS.HELPER_FEEDS else HRS.HELPER_FEED
                use_feed = (feed_mode == "async" and hasattr(pool, "stream") and 0 < local < width
                            and isinstance(base_job.get("loadout"), dict) and "session_blob" not in base_job)

                def split(total, n):
                    return [total // n + (1 if i < total % n else 0) for i in range(n)]
                callback = None
                if self.on_wave is not None:
                    def callback(outcome, wave):
                        candidate = tuple(tuple(a) for a in outcome["route"])
                        score, ok = _valid_score(session, candidate)
                        if not ok or score != outcome["best"]:
                            return
                        plan, plan_session, plan_doc = self.build_plan(
                            session, candidate, score, reason, seconds=round(time.monotonic() - started, 1),
                            steps=outcome.get("steps") or [], restarts=restarts, tail_seconds=tail_seconds)
                        plan["wave"], plan["waves"] = wave, total_waves
                        if rules_here is not None:
                            plan["timeline"] = self._timeline_record(session, candidate, rules_here)
                        self.on_wave({"best": score, "route": [list(a) for a in candidate], "wave": wave,
                                      "waves": total_waves, "from_action_index": len(self.executed), "plan": plan,
                                      "plan_session": plan_session, "plan_doc": plan_doc}, wave)
                found, wave_log, carried = [], [], {"best": None}
                er_extra = {"rules": rules_here} if rules_here is not None else {}
                if self.timeline is not None:
                    ct = self._tl("exhaust", "cut_table")
                    er_extra = {"root": self._tl("exhaust", "root"),
                                "cut_table": {int(k): float(v) for k, v in ct.items()} if ct else None,
                                "rules": rules_here,
                                "branch_at": self._tl("exhaust", "branch_at") if reason == "open" else None}
                    if self._tl("budget", "er_elites") is not None:
                        er_extra["elites"] = int(self._tl("budget", "er_elites"))
                if (self.meta or {}).get("debuff_gate"):
                    er_extra["gate"] = self.meta["debuff_gate"]
                if self.incumbent_route:
                    er_extra["incumbents"] = [[list(a) for a in self.incumbent_route]]
                hand = threading.Lock()
                halted = {"why": None}
                for group_index, group in enumerate(groups):
                    join = HRS.WaveJoin(pool, len(group)) if len(group) > 1 else None

                    def make_callback(_offset=offsets[group_index], _join=join):
                        if callback is None and _join is None:
                            return None

                        def run_callback(outcome, wave):
                            with hand:
                                if halted["why"] is not None:
                                    return
                                if carried["best"] is None or HRS._value(outcome) > HRS._value(carried["best"]):
                                    carried["best"] = outcome
                                try:
                                    if callback is not None:
                                        callback(carried["best"], _offset + wave)
                                except BaseException:
                                    halted["why"] = "callback"
                                    raise
                                if (_join is not None and self.score_cap is not None
                                        and HRS._value(carried["best"]) >= int(self.score_cap)):
                                    _join.stop()
                        return run_callback

                    def one_run(run_pool, run, _width, _helpers=None, _job=group_jobs[group_index],
                                _waves=group_waves[group_index],
                                _context=SearchContext.here(target=self.score_cap, cancel=self.cancel)):
                        return HRS.elite_restarts(
                            run_pool, _job, session, waves=_waves, workers=_width,
                            first_seed=seed0 + OPEN_RUN_SEED_STRIDE * run, base_prefix=base_prefix,
                            on_wave=make_callback(), helpers=_helpers, context=_context, **er_extra)

                    if use_feed:
                        widths = split(local, len(group))
                        lent = split(width - local, len(group))
                        streams = [pool.stream(n) if group_waves[group_index] > 1 else None for n in lent]
                        self._last_helper_feed = {"mode": "async", "local": local, "lent": width - local,
                                                  "runs_side_by_side": len(group)}
                    else:
                        widths, streams = [max(1, width // len(group))] * len(group), [None] * len(group)
                    try:
                        if join is None:
                            results = [one_run(pool, group[0], widths[0] if use_feed else width, streams[0])]
                        else:
                            results, _join = HRS.run_side_by_side(
                                pool, [lambda run_pool, _run=run, _w=w, _h=h: one_run(run_pool, _run, _w, _h)
                                       for run, w, h in zip(group, widths, streams)],
                                join=join)
                    finally:
                        for stream in streams:
                            if stream is not None:
                                stream.finish(0)
                    for run, (found_run, log_run) in zip(group, results):
                        for row in log_run:
                            row["run"] = run
                            if len(group) > 1:
                                row["side_by_side"] = len(group)
                            if row.get("gate") is not None:
                                self.gate_status = dict(row["gate"], at_turn=int(session.turn))
                        found += found_run
                        wave_log += log_run
                    if (self.score_cap is not None and found
                            and max(HRS._value(o) for o in found) >= int(self.score_cap)):
                        break
                if (self.timeline is not None and reason == "open" and self._tl("exhaust", "sweep") and found):
                    found, self._last_sweep = HRS.sweep(
                        pool, base_job, session, found, self._tl("exhaust", "sweep"),
                        workers=min(seeds, self.search_seeds),
                        first_seed=seed0 + OPEN_RUN_SEED_STRIDE * runs + 600000, base_prefix=base_prefix,
                        rules=rules_here)
                at_cap = (self.score_cap is not None and found
                          and max(HRS._value(o) for o in found) >= int(self.score_cap))
                if alns and found and not at_cap:
                    alns_callback = None
                    if callback is not None:
                        def alns_callback(outcome, rnd, _offset=sum(group_waves)):
                            if carried["best"] is None or HRS._value(outcome) > HRS._value(carried["best"]):
                                carried["best"] = outcome
                            callback(carried["best"], _offset + rnd)
                    alns_extra = {"rules": rules_here} if rules_here is not None else {}
                    if self.timeline is not None:
                        ct = self._tl("exhaust", "cut_table")
                        alns_extra = {"rules": rules_here,
                                      "cut_table": {int(k): float(v) for k, v in ct.items()} if ct else None}
                    alns_job = self._tl("budget", "alns_job_seconds")
                    found, alns_log = HRS.alns_rounds(
                        pool, base_job, session, found, rounds=alns, workers=min(seeds, self.search_seeds),
                        first_seed=seed0 + OPEN_RUN_SEED_STRIDE * runs, base_prefix=base_prefix,
                        on_round=alns_callback, stop_score=self.score_cap,
                        job_seconds=HRS.ALNS_JOB_SECONDS if alns_job is None else float(alns_job),
                        job_tail_seconds=HRS.ALNS_JOB_TAIL_SECONDS, **alns_extra)
                    self._last_alns = alns_log
                    for row in alns_log:
                        if row.get("failed"):
                            seed_results.append({"seed": None, "status": "failed", "alns_round": row["round"],
                                                 "failed_jobs": row["failed"], "error": row.get("first_error")})
                found.sort(key=lambda o: (-HRS._value(o), o["seed"]))
                self._free_best = max((int(o["best"]) for o in found if o.get("best") is not None), default=None)
                self._last_er_waves = wave_log
                if self._last_helper_feed is not None:
                    self._last_helper_feed["runs"] = [dict(row["helpers_end"], run=row.get("run"))
                                                      for row in wave_log if row.get("helpers_end")]
                outcomes = [dict(o, path=o["route"]) for o in found]
                for row in wave_log:
                    if row.get("failed"):
                        seed_results.append({"seed": None, "status": "failed", "run": row.get("run"),
                                             "wave": row.get("wave"), "failed_jobs": row["failed"],
                                             "error": row.get("first_error")})
            else:
                self._last_er_waves = None
                jobs = [dict(shared, row=None, drinks_mode="all", start_path=None,
                             nrpa_seconds=seconds, tail_seconds=tail_seconds,
                             seed=seed0 + i, from_scratch=True,
                             restarts=restarts, stabilization=self.stabilization)
                        for i in range(min(seeds, self.search_seeds))]
                outcomes = pool.run(jobs)
            candidates = []
            for outcome in sorted(outcomes, key=lambda o: o.get("seed", 0)):
                entry = {"seed": outcome.get("seed"), "best": outcome.get("best"),
                         "status": outcome["status"], "seconds": outcome.get("seconds")}
                if outcome["status"] != "ok":
                    entry["error"] = (outcome.get("error") or "")[-300:]
                elif outcome.get("path") and outcome.get("best") is not None:
                    candidates.append(outcome)
                seed_results.append(entry)
            if rules_here is not None:
                for outcome in candidates:
                    if outcome.get("objective") is None:
                        ev = rules_here.evaluate(session, [tuple(a) if a[0] != "select" else ("select", a[1])
                                                           for a in outcome["path"]])
                        outcome["objective"] = ev["objective"]
            if str(session.config.segment_type).startswith("Lesson") and rules_here is None:
                for outcome in candidates:
                    outcome["_rank"] = _route_search().lesson_rank(session, outcome["path"], outcome["best"])
                candidates.sort(key=lambda o: (-_route_search()._value(o), -o["_rank"][-1], o["path"], o["seed"]))
            else:
                candidates.sort(key=lambda o: (-_route_search()._value(o), o["path"], o["seed"]))
            for outcome in candidates:
                candidate = tuple(tuple(a) for a in outcome["path"])
                score, ok = _valid_score(session, candidate)
                if ok and score == outcome["best"]:
                    path, best, steps = candidate, score, outcome.get("steps") or []
                    break
                rejected.append({"seed": outcome.get("seed"), "best": outcome.get("best"), "replay": score,
                                 "valid": ok})
        elapsed = round(time.monotonic() - started, 1)
        if path is None:
            record = {"reason": reason, "status": "no_valid_route", "seconds": elapsed,
                      "restarts": restarts, "stabilization": self.stabilization, "tail_seconds": tail_seconds,
                      "er_waves": self._last_er_waves, "rejected": rejected[:3],
                      "seed_results": seed_results, "from_action_index": len(self.executed),
                      "turn": int(session.turn)}
            self.plans.append(record)
            self.plan, self.plan_session, self.plan_doc = None, None, None
            self.ledger.append(self._row(session.turn, "search", "no_valid_route", None, None,
                                         action_index=len(self.executed)))
            return record
        self.plan, self.plan_session, self.plan_doc = self.build_plan(
            session, path, best, reason, seconds=elapsed, seed_results=seed_results, steps=steps,
            restarts=restarts, tail_seconds=tail_seconds, er_waves=self._last_er_waves)
        self.plan["algorithm"] = self.algorithm
        if self.split is not None and self.plan.get("split") is not None and self._free_best is not None:
            self.plan["split"]["free_best"] = self._free_best
        if self.goal is not None and self.plan.get("goal") is not None and self._free_best is not None:
            self.plan["goal"]["free_best"] = self._free_best
        if self.timeline is not None:
            self.plan["timeline"] = self._timeline_record(session, path, rules_here)
            if self._last_sweep is not None:
                self.plan["sweep"] = self._last_sweep
        if self._last_alns is not None:
            self.plan["alns"] = self._last_alns
        self.plans.append({k: v for k, v in self.plan.items() if k != "table"})
        return self.plan

    def alns_rounds_for(self, reason):
        """how many ALNS rounds a solve for ``reason`` runs (0 unless the algorithm asks)."""
        if self._tl("budget", "alns_rounds") is not None:
            return int(self._tl("budget", "alns_rounds")) if reason in ALNS_REASONS else 0
        if self.algorithm != "alns10" or reason not in ALNS_REASONS:
            return 0
        return int(_route_search().ALNS_ROUNDS)

    def alns_seconds(self, reason):
        """The wall time the ALNS rounds of ``reason`` add (rounds x one job)."""
        HRS = _route_search()
        job = self._tl("budget", "alns_job_seconds")
        return self.alns_rounds_for(reason) * ((HRS.ALNS_JOB_SECONDS if job is None else float(job))
                                               + HRS.ALNS_JOB_TAIL_SECONDS)

    def _timeline_record(self, session, path, rules_here):
        """what the plan says about the timeline: id, hash, the line's objective and the rules it misses."""
        from GakumasCLI.scenario import timeline as TL
        tl = self.timeline or {}
        rec = {"id": (tl.get("timeline") or {}).get("id"), "name": (tl.get("timeline") or {}).get("name"),
               "hash": tl.get("hash"), "stage": tl.get("stage"), "dropped": tl.get("dropped") or []}
        if rules_here is not None:
            ev = rules_here.evaluate(session, [tuple(a) if a[0] != "select" else ("select", a[1]) for a in path])
            rules = tl.get("rules") or []
            rec.update(objective=ev["objective"], penalty=ev["penalty"], soft=ev["soft"], unmet=ev["unmet"],
                       relaxed=[TL.describe_rule(rules[k], tl.get("limit_turn")) for k in ev["unmet"]
                                if k < len(rules)])
        return rec

    def build_plan(self, session, path, best, reason, *, seconds, seed_results=(), steps=(), restarts=None,
                   tail_seconds=None, er_waves=None):
        """(plan, plan_session, plan_doc) for ``path`` from ``session`` -- what ``solve`` installs, also built for each
        ER wave so an anytime caller can install a wave's best route the same way."""
        table, final = route_table(session, path)
        plan_session = session.clone()
        plan_doc = {"format": "gkms.initial_route.v1", "objective_version": SCORE_OBJECTIVE_VERSION,
                    "entry_state_hash": SV.entry_state_hash(plan_session),
                    "action_path": [list(a) for a in path], "source": f"hif_live:{reason}",
                    "score": int(best)}
        plan = {"reason": reason, "status": "ok", "score": int(best), "route": [list(a) for a in path],
                "from_action_index": len(self.executed), "turn": int(session.turn),
                "seconds": seconds, "seed_results": list(seed_results), "steps": list(steps),
                "restarts": restarts, "stabilization": self.stabilization, "tail_seconds": tail_seconds,
                "er_waves": er_waves,
                "table": table, "select_choices": [dict(c) for c in
                                                   (final.deck.select_choices or [])[len(session.deck.select_choices or []):]],
                "unresolved": list(final.state.unresolved_effects)[:10],
                "entry_state_hash": plan_doc["entry_state_hash"]}
        if getattr(self, "split", None) is not None:
            from GakumasCLI.scenario import timeline as TL
            plan["split"] = dict(TL.split_report(final.state, self.split), spec=self.split,
                                 want=TL.split_text(self.split))
        if getattr(self, "goal", None) is not None:
            from GakumasCLI.scenario import timeline as TL
            plan["goal"] = dict(TL.goal_report(final.state, self.goal), spec=self.goal, want=TL.goal_text(self.goal))
        return plan, plan_session, plan_doc

    # -- following -------------------------------------------------------------
    def _suffix(self):
        """The remaining plan from here, through the solver's own contract."""
        if self.plan is None or self.plan_session is None:
            return {"status": "invalid", "reason": "no_plan"}
        since = self._plays_only(self.executed[self.plan["from_action_index"]:])
        return SV.reuse_route_suffix(self.plan_session, self.plan_doc, since, self.session)

    def expected_action(self):
        """The plan's next action at the current decision point, or None."""
        if self.plan is None:
            return None
        since = self._plays_only(self.executed[self.plan["from_action_index"]:])
        route = route_plays(self.plan["route"])
        if len(since) >= len(route):
            return None
        for i, action in enumerate(since):
            if list(action) != list(route[i]):
                return None
        expected = tuple(route[len(since)])
        if expected not in self.session.legal_actions():
            return None
        return expected

    @property
    def status(self):
        if self.session.finished:
            return "finished"
        if self.desynced:
            return "desynced"
        if self.plan is None:
            return "no_plan"
        return "on_plan" if self.expected_action() is not None else "deviated"

    def _row(self, turn, kind, field, predicted, observed, action_index=None, hard=None):
        delta = None
        if isinstance(predicted, (int, float)) and isinstance(observed, (int, float)) \
                and not isinstance(predicted, bool) and not isinstance(observed, bool):
            delta = observed - predicted
        row = {"turn": int(turn), "kind": kind, "field": field, "predicted": predicted,
               "observed": observed, "delta": delta,
               "action_index": len(self.executed) if action_index is None else action_index,
               "hard": bool(field in HARD_FIELDS if hard is None else hard),
               "at": round(time.time(), 3)}
        return row

    def observe(self, action, state=None, auto_resolve=None):
        """Apply what the player actually did, compare, and advise again."""
        auto = self.auto_resolve if auto_resolve is None else bool(auto_resolve)
        if self.session.finished:
            raise RuntimeError("round finished")
        turn = int(self.session.turn)
        index = len(self.executed)
        try:
            resolved, picks, equivalents = resolve_action(self.session, action)
        except LiveActionError as error:
            self.ledger.append(self._row(turn, "action", "illegal_in_engine", None,
                                         {"reason": error.reason, **error.detail}, action_index=index))
            self._mark_divergence(self.ledger[-1])
            self.observations.append({"turn": turn, "action": action, "state": state,
                                      "applied": False, "error": error.reason})
            return self.advice()
        if resolved[0] == "select":
            if self._stand_in is not None and self._stand_in["left"] > 0:
                return self._late_pick(turn, action, list(resolved[1]), state, auto)
            self.session.state.select_index_queue.append(list(resolved[1]))
            self.executed.append(("select", list(resolved[1])))
            self.observations.append({"turn": turn, "action": action, "state": state, "applied": True})
            return self.advice()
        if resolved not in self.session.legal_actions():
            self.ledger.append(self._row(turn, "action", "illegal_in_engine",
                                         action_text(self.session, resolved), list(resolved),
                                         action_index=index))
            self._mark_divergence(self.ledger[-1])
            self.observations.append({"turn": turn, "action": action, "state": state,
                                      "applied": False, "error": "illegal_in_engine"})
            return self.advice()

        expected = self.expected_action()
        if expected is not None and equivalents and expected in equivalents:
            resolved = expected
        copy_of = None
        if expected is not None and resolved != expected and same_copy(self.session, resolved, expected):
            copy_of, resolved = list(resolved), expected
        expected_pick = None
        if expected is not None:
            position = len(self._plays_only(self.executed[self.plan["from_action_index"]:]))
            for row in self.plan["table"]:
                for entry in row["actions"]:
                    if entry["index"] == position:
                        expected_pick = entry.get("select")
        deviated = expected is not None and resolved != expected
        if deviated:
            self.ledger.append(self._row(turn, "action", "action", action_text(self.session, expected),
                                         action_text(self.session, resolved), action_index=index, hard=False))
        for pick in picks:
            self.session.state.select_index_queue.append(list(pick[1]))
            self.executed.append(("select", list(pick[1])))
        before_choices = len(getattr(self.session.deck, "select_choices", None) or [])
        self.session.step(resolved, observe=False)
        self.executed.append(resolved)
        stand_in = (getattr(self.session.deck, "select_choices", None) or [])[before_choices:]
        for choice in stand_in:
            self.ledger.append(self._row(turn, "select", "stand_in_pick",
                                         choice.get("picked_numbers"), None, action_index=index, hard=False))
        self._stand_in = {"at": len(self.executed) - 1, "left": len(stand_in)} if stand_in else None
        if picks and expected_pick and not deviated:
            observed_numbers = []
            for pick, entry in zip(picks, expected_pick):
                candidates = entry.get("candidate_numbers") or []
                observed_numbers.append([candidates[i] for i in pick[1] if 0 <= i < len(candidates)])
            predicted_numbers = [entry.get("picked_numbers") for entry in expected_pick]
            if observed_numbers != predicted_numbers:
                self.ledger.append(self._row(turn, "select", "select", predicted_numbers,
                                             observed_numbers, action_index=index, hard=False))
                deviated = True
        record = {"turn": turn, "action": action, "resolved": list(resolved), "state": state,
                  "applied": True, "deviated": deviated}
        if copy_of is not None:
            record["copy_of"] = copy_of
        self.observations.append(record)
        if state:
            self.compare(state)
        if self.session.finished:
            if state and state.get("final_score") is not None:
                self.finished_observed = int(state["final_score"])
                row = self._row(self.session.turn, "final", "final_score", int(self.session.state.score),
                                self.finished_observed)
                if row["delta"]:
                    self.ledger.append(row)
                    self._mark_divergence(row)
            return self.advice()
        if deviated and auto and not self.desynced:
            self.solve(seconds=self.resolve_seconds, reason="operator_deviation")
        elif self.plan is not None and self.expected_action() is None and auto and not self.desynced:
            self.solve(seconds=self.resolve_seconds, reason="plan_exhausted")
        return self.advice()

    def withdraw(self, start):
        """take back every executed action from ``start`` on (已做 actions the game did not make,
        HifFollower._manual_withdraw): the session becomes the entry with the executed prefix before ``start``
        replayed.
        """
        start = max(0, int(start))
        if start >= len(self.executed):
            return False
        turn = int(self.session.turn)
        taken = [list(a) for a in self.executed[start:]]
        del self.executed[start:]
        session = apply_prefix(self.entry.clone(), self.executed)
        if getattr(session, "native", False):
            session.watch()
        self.session = session
        if self._stand_in is not None and self._stand_in["at"] >= start:
            self._stand_in = None
        dropped = self.plan is not None and self.plan["from_action_index"] > start
        if dropped:
            self.plan, self.plan_session, self.plan_doc = None, None, None
        self.ledger.append(self._row(turn, "action", "withdrawn", taken, None, action_index=start, hard=False))
        self.observations.append({"turn": turn, "action": None, "state": None, "applied": False,
                                  "withdrawn": taken, "at_index": start})
        return dropped

    def _late_pick(self, turn, action, pick, state, auto):
        """a pick observed *after* the play whose picker the engine already stood in for (the natural order of a live
        client and of the recorded ``Produce/History`` action lists)."""
        at = self._stand_in["at"]
        self.executed.insert(at, ("select", pick))
        self._stand_in = {"at": at + 1, "left": self._stand_in["left"] - 1}
        if self.plan is not None and self.plan["from_action_index"] > at:
            self.plan["from_action_index"] += 1
        session = apply_prefix(self.entry.clone(), self.executed)
        if getattr(session, "native", False):
            session.watch()
        self.session = session
        self.ledger.append(self._row(turn, "select", "stand_in_corrected", None, list(pick),
                                     action_index=at, hard=False))
        self.observations.append({"turn": turn, "action": action, "state": state, "applied": True,
                                  "late_pick_for": at + 1})
        if self._stand_in["left"] <= 0:
            self._stand_in = None
        if state:
            self.compare(state)
        if (auto and not self.desynced and not session.finished and self.plan is not None
                and self._suffix().get("status") != "reusable"):
            self.solve(seconds=self.resolve_seconds, reason="pick_corrected")
        return self.advice()

    def compare(self, state):
        """Ledger every field of ``state`` that disagrees with the engine."""
        view = self.session.observe()
        turn = int(self.session.turn)
        added = []
        for field in SCALAR_FIELDS:
            if field in state and state[field] is not None and field in view:
                if view[field] != state[field]:
                    added.append(self._row(turn, "state", field, view[field], state[field]))
        for zone in ("hand", "hold"):
            predicted_ids = [c["produce_card_id"] for c in view[zone]]
            predicted_numbers = [c["number"] for c in view[zone]]
            if state.get(f"{zone}_numbers") is not None:
                observed = list(state[f"{zone}_numbers"])
                if observed != predicted_numbers:
                    same_set = Counter(observed) == Counter(predicted_numbers)
                    added.append(self._row(turn, "state", f"{zone}_order" if same_set else zone,
                                           predicted_numbers, observed, hard=not same_set))
            elif state.get(zone) is not None:
                observed = list(state[zone])
                if observed != predicted_ids:
                    same_set = Counter(observed) == Counter(predicted_ids)
                    added.append(self._row(turn, "state", f"{zone}_order" if same_set else zone,
                                           predicted_ids, observed, hard=not same_set))
        for row in added:
            self.ledger.append(row)
            if row["hard"]:
                self._mark_divergence(row)
        return added

    def _mark_divergence(self, row):
        self.desynced = True
        if self.first_divergence is None:
            self.first_divergence = {"turn": row["turn"], "field": row["field"],
                                     "predicted": row["predicted"], "observed": row["observed"],
                                     "action_index": row["action_index"]}

    def resync(self, state, resolve=True):
        """Explicitly overwrite the visible scalars (score / stamina / block)
        and the hand order with what the client shows, ledger it, clear the
        desync flag and (by default) re-solve.
        """
        if getattr(self.session, "native", False):
            view, applied = self.session.edit(_resync_apply, state)
        else:
            view, applied = _resync_apply(self.session, state)
        self.ledger.append(self._row(self.session.turn, "resync", "resync",
                                     {k: view[k] for k in applied if k in view}, applied, hard=False))
        self.desynced = False
        if resolve and not self.session.finished:
            self.solve(seconds=self.resolve_seconds, reason="observed_state_mismatch")
        return self.advice()

    # -- reporting -------------------------------------------------------------
    def advice(self):
        session = self.session
        view = session.observe()
        expected = self.expected_action()
        suffix = self._suffix() if self.plan is not None else {"status": "invalid", "reason": "no_plan"}
        out = {"schema": LIVE_SCHEMA, "turn": int(session.turn), "limit_turn": view["limit_turn"],
               "finished": bool(session.finished), "status": self.status, "desynced": self.desynced,
               "executed": len(self.executed), "score": view["score"], "stamina": view["stamina"],
               "block": view["block"], "playable": view["playable"],
               "hand": view["hand"], "hold": view["hold"], "drinks": list(session.drinks),
               "rivals": self.meta.get("rival_scores"),
               "plan": None, "next": None, "predicted": None, "alternatives": [],
               "suffix": suffix, "ledger_count": len(self.ledger),
               "first_divergence": self.first_divergence,
               "needs_resolve": (not session.finished and not self.desynced
                                 and (self.plan is None or expected is None))}
        if self.plan is not None:
            out["plan"] = {"reason": self.plan["reason"], "score": self.plan["score"],
                           "turn": self.plan["turn"], "seconds": self.plan["seconds"],
                           "from_action_index": self.plan["from_action_index"],
                           "seeds": sum(1 for s in self.plan["seed_results"] if s["status"] == "ok"),
                           "select_choices": self.plan["select_choices"],
                           "unresolved": self.plan["unresolved"]}
            out["table"] = self.plan["table"]
        if session.finished:
            out["final_score"] = int(session.state.score)
            out["final_score_observed"] = self.finished_observed
            return out
        out["alternatives"] = _alternatives(session)
        if expected is not None:
            preview = preview_action(session, expected)
            card = drink = None
            if expected[0] == "play":
                card = view["hand"][expected[1]]
            elif expected[0] == "drink":
                drink_id = session.drinks[expected[1]]
                drink = {"index": expected[1], "drink_id": drink_id,
                         "name": session.master.drinks.get(drink_id, {}).get("name", drink_id)}
            out["next"] = {"action": list(expected), "text": action_text(session, expected),
                           "card": card, "drink": drink}
            probe = session.clone()
            probe.step(expected, observe=False)
            after = probe.observe()
            out["predicted"] = {"final_score": self.plan["score"] if suffix.get("status") != "reusable"
                                else int(suffix["score"]),
                                "after_action": preview.get("after_action"),
                                "after_settlement": preview.get("after_settlement"),
                                "next_state": {"turn": after["turn"], "score": after["score"],
                                               "stamina": after["stamina"], "block": after["block"],
                                               "playable": after["playable"], "finished": after["finished"],
                                               "hand": [c["produce_card_id"] for c in after["hand"]],
                                               "hand_numbers": [c["number"] for c in after["hand"]],
                                               "hold_numbers": [c["number"] for c in after["hold"]]},
                                "remaining_route": [list(a) for a in
                                                    self.plan["route"][route_after(self.plan["route"], len(
                                                        self._plays_only(self.executed[self.plan["from_action_index"]:])))
                                                    :]]}
        return out

    def report(self):
        loadout = self.loadout or {}
        return {"schema": LIVE_SCHEMA, "seed": loadout.get("seed"), "round": loadout.get("round"),
                "idol_card_id": loadout.get("idol_card_id"),
                "source": "loadout" if self.loadout is not None else "adopted_session",
                "status": self.status,
                "desynced": self.desynced, "opened_at": self.opened_at,
                "open_plan_score": next((p["score"] for p in self.plans if p.get("status") == "ok"), None),
                "current_plan_score": self.plan["score"] if self.plan else None,
                "final_score_engine": int(self.session.state.score) if self.session.finished else None,
                "final_score_observed": self.finished_observed,
                "executed": [list(a) if a[0] != "select" else ["select", list(a[1])] for a in self.executed],
                "solves": self.plans, "ledger": self.ledger, "first_divergence": self.first_divergence,
                "unresolved": list(self.session.state.unresolved_effects)[:10],
                "meta": {k: self.meta.get(k) for k in ("limit_turn", "rival_scores", "unverified",
                                                       "effective_permils", "npc_count")}}
