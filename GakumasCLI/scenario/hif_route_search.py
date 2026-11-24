# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Route search against the recorded H.I.F top-player rounds.

    python -B tools/hif_route_search.py <plain_dir> [<plain_dir> ...]
        [--idol hmsz,jsna] [--seeds 1570396333/16,...] [--limit N]
        [--nrpa-seconds 60] [--tail-seconds 20] [--drinks fair|used|all]
        [--search-seeds 1] [--search-seed 182] [--prior FILE] [--from-scratch]
        [--holdout none|even|odd] [--use-holdout|--use-train] [--exclude-seeds ...]
        [--reseed k] [--shard i/n] [--out <dir>]
    python -B tools/hif_route_search.py <plain_dir> ... --train-prior prior.json
        [--teacher-routes routes.jsonl[,...]] [--holdout even|odd] [--exclude-seeds ...]
"""
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import random
import statistics
import threading
import time
import traceback
from pathlib import Path
from functools import lru_cache

from GakumasCLI.scenario.hif_replay_import import extract, to_loadout          # noqa: E402
from GakumasCLI.scenario.exam_session import Master, SCORE_OBJECTIVE_VERSION   # noqa: E402
from GakumasCLI.scenario.hif import build_hif_loadout_session                  # noqa: E402
from GakumasCLI.scenario import solver as SV                                   # noqa: E402
from GakumasCLI.scenario.policy_search import policy_search, learn_prior, load_prior, save_prior   # noqa: E402
from GakumasCLI.scenario.search_context import SearchContext, SearchSwitches                # noqa: E402

SWITCHES = SearchSwitches.from_env()

BAD_SELECT = ("SELECT requires selected_card_numbers", "select_index_out_of_range")
ENGINE_ROOT = Path(__file__).resolve().parents[1]
MASTER_DIR = ENGINE_ROOT / "ro" / "master" / "tables"
DEFAULT_SEARCH_SEED = 182
WORKER_SETUP = None
CANCEL_GROUPS = 1024
_JOB_CANCEL = None


def _job_cancelled():
    """whether the job running in this worker was cancelled (False outside a cancellable job)."""
    held = _JOB_CANCEL
    return held is not None and held[0][held[1]] != 0


class _JobFlag:
    """_job_cancelled as the ``cancel.is_set`` the polishes read."""

    @staticmethod
    def is_set():
        return _job_cancelled()


_JOB_FLAG = _JobFlag()


DEFAULT_RESTARTS = 4
DEFAULT_STABILIZATION = 3
RUN_TRANSITIONS = SWITCHES.run_transitions
DEFAULT_STRATEGY = "er"
ER_JOB_SECONDS = 20.0
ER_JOB_TAIL_SECONDS = 5.0
ER_ELITES = 4
# smoothed (improved + 0.5) / (jobs + 1); GKMS_ER_CUT=production switches the table off.
ER_CUT_TABLE = {"hmsz": {1: 0.0007, 2: 0.0005, 3: 0.0157, 4: 0.1065, 5: 0.0768, 6: 0.1355,
                         7: 0.0433, 8: 0.0519, 9: 0.1077, 10: 0.0043, 11: 0.0414}}
ER_ROOT_BRANCH = "off"
ER_GATE_PER = 8
ER_ROOT_SCORE_SECONDS = 1.0
ER_ROOT_SCORE_CAP = 240
ALNS_ROUNDS = 10
ALNS_JOB_SECONDS = 15.0
ALNS_JOB_TAIL_SECONDS = 3.0
ALNS_ELITES = 4
ALNS_OPERATORS = ("cut_table", "cut_late", "cut_mid", "cut_early", "alt", "end", "drink")
ALNS_SIGMA = (33.0, 9.0, 0.0)
ALNS_REACTION = 0.3
ALNS_MIN_WEIGHT = 0.05
ALNS_SEED_OFFSET = 700000


def load_rounds(dirs):
    """rows keyed by (seed, step); group_of: key -> player group id."""
    rows, group_of = {}, {}
    for d in dirs:
        for p in sorted(Path(d).glob("ev*ProduceHistory.bin")):
            keys = []
            for r in extract(p.read_bytes()):
                r["actions"] = [tuple(a) if a[0] != "select" else ("select", tuple(a[1]))
                                for a in r["actions"]]
                key = (r["seed"], r["step_type"])
                rows[key] = r
                keys.append(key)
            gid = tuple(sorted(keys))
            for key in keys:
                group_of[key] = gid
    return rows, group_of


def parse_keys(text):
    return {tuple(int(v) for v in s.split("/")) for s in text.split(",") if s.strip()}


def reseeded_row(row, j):
    """The same loadout with a fresh seed: a new problem nobody has played."""
    seed = random.Random(f"{row['seed']}/{row['step_type']}/reseed/{j}").randrange(1, 2 ** 31 - 1)
    return dict(row, seed=seed, reseeded_from=row["seed"], reseed_index=j)


def player_split(rows, group_of, holdout, exclude=()):
    """Deterministic player-level split."""
    groups = {}
    for key, gid in group_of.items():
        if key in rows and key not in exclude:
            groups.setdefault(gid, []).append(key)
    ordered = sorted(groups, key=lambda g: (rows[g[0]]["idol_card_id"][7:11], min(g)))
    per_idol, report, train, test = {}, [], set(), set()
    for gid in ordered:
        idol = rows[gid[0]]["idol_card_id"][7:11]
        index = per_idol.get(idol, 0)
        per_idol[idol] = index + 1
        held = (holdout == "even" and index % 2 == 0) or (holdout == "odd" and index % 2 == 1)
        keys = sorted(groups[gid])
        (test if held else train).update(keys)
        report.append({"idol": idol, "index": index, "role": "test" if held else "train",
                       "rounds": [f"{k[0]}/{k[1]}" for k in keys],
                       "recorded": [int(rows[k]["recorded_score"]) for k in keys]})
    return train, test, report


def _bad_selects(session):
    return [u for u in session.state.unresolved_effects
            if isinstance(u, dict) and u.get("reason") in BAD_SELECT]


def build(master, row, drinks_mode):
    """Session plus the player's route (select entries removed, drink indexes
    remapped when the drink list is restricted)."""
    session, _ = build_hif_loadout_session(master, to_loadout(row))
    session.state.select_index_queue = [list(a[1]) for a in row["actions"] if a[0] == "select"]
    route = [tuple(a) for a in row["actions"] if a[0] != "select"]
    if drinks_mode == "fair":
        drinks_mode = "used" if int(row["step_type"]) == 16 else "all"
    if drinks_mode == "used":
        original = list(session.drinks)
        held, used = list(original), []
        for a in route:
            if a[0] == "drink":
                used.append(held.pop(a[1]))
        pool, kept = list(used), []
        for d in original:
            if d in pool:
                kept.append(d)
                pool.remove(d)
        session.drinks = kept
        held, current, remapped = list(original), list(kept), []
        for a in route:
            if a[0] == "drink":
                drink_id = held.pop(a[1])
                j = current.index(drink_id)
                current.pop(j)
                remapped.append(("drink", j))
            else:
                remapped.append(a)
        route = remapped
    return session, tuple(route)


def _valid_score(session, path):
    probe = SV.replay_session(session, path)
    return int(probe.state.score), not _bad_selects(probe)


def lesson_rank(session, path, score):
    """(score, stamina at the end) of a line of a 初 lesson -- lines of one score keep the one with more 体力
    (scenario.policy_search._better ranks them so); (score) for any other round."""
    if not str(session.config.segment_type).startswith("Lesson"):
        return (score,)
    return (score, int(SV.replay_session(session, [tuple(a) if a[0] != "select" else ("select", a[1])
                                                     for a in path]).state.stamina))


def _beats(session, path, score, best_path, best):
    """``path`` (``score``) replaces the job's best -- a higher score, or in a 初 lesson the same score with more 体力 left
    (lesson_rank); any other round keeps the best on a tie, as before."""
    if best is None:
        return True
    if score != best:
        return score > best
    return best_path is not None and lesson_rank(session, path, score) > lesson_rank(session, best_path, best)


TAIL_POLISH = None


def _tail_polish(session, path, turns, *, deadline, rules=None, max_turns=None):
    """``rules`` (a timeline's RuleSet at ``session``) and ``max_turns`` (its tail_turns) reach a polish that takes them
    (the native one); the Python polish knows no rules, so with rules it is skipped."""
    extra = {}
    if rules is not None:
        extra["rules"] = rules
    if max_turns is not None:
        extra["max_turns"] = int(max_turns)
    if _JOB_CANCEL is not None:
        extra["cancel"] = _JOB_FLAG
    if TAIL_POLISH is not None:
        if extra:
            try:
                return TAIL_POLISH(session, path, turns, deadline=deadline, **extra)
            except TypeError:
                if rules is not None:
                    return tuple(path), {"skipped": "rules"}
                return TAIL_POLISH(session, path, turns, deadline=deadline)
        return TAIL_POLISH(session, path, turns, deadline=deadline)
    if rules is not None:
        return tuple(path), {"skipped": "rules"}
    return SV.exact_tail_polish(session, path, turns, deadline=deadline, cancel=extra.get("cancel"))


def _value(o):
    """what ranks a pool outcome -- score minus a timeline's penalty when the job had rules, else the score."""
    v = o.get("objective")
    return o["best"] if v is None else v


def _fill_objective(outcome, session, rules):
    """an outcome from a worker that did not apply the rules (an older remote node) gets its objective here, by replaying
    its route (relative to ``session``, whose rule state is ``rules``' root)."""
    if rules is None or outcome.get("objective") is not None or outcome.get("route") is None:
        return outcome
    try:
        ev = rules.evaluate(session, [tuple(a) if a[0] != "select" else ("select", a[1]) for a in outcome["route"]])
        outcome["objective"], outcome["unmet"] = ev["objective"], ev["unmet"]
    except Exception:                           # noqa: BLE001
        from GakumasCLI.scenario.timeline import OBJECTIVE_WORST
        outcome["objective"] = OBJECTIVE_WORST
    return outcome


def _run_seed(seed, k, chunked):
    """The search seed of a job's k-th GNRPA run: seed + 1000 k."""
    if not chunked or k == 0:
        return int(seed) + 1000 * int(k)
    return ((int(seed) + 1) << 22) | int(k)


def _gnrpa_runs(nrpa_seconds, restarts, run_transitions=None, nrpa_transitions=None):
    """The GNRPA runs of one search job: yields (k, deadline, transitions, chunked) for each run, the next only once
    the previous has returned.
    """
    run_transitions = RUN_TRANSITIONS if run_transitions is None else int(run_transitions)
    restarts = max(1, int(restarts or 1))
    if nrpa_transitions is not None:
        if run_transitions > 0:
            total = max(1, int(nrpa_transitions))
            full, rest = divmod(total, run_transitions)
            for k in range(full):
                if _job_cancelled():
                    return
                yield k, None, run_transitions, True
            if rest and not _job_cancelled():
                yield full, None, rest, True
        else:
            for k in range(restarts):
                if _job_cancelled():
                    return
                yield k, None, max(1, int(nrpa_transitions) // restarts), False
        return
    if run_transitions > 0:
        end = time.perf_counter() + float(nrpa_seconds)
        k = 0
        while True:
            left = end - time.perf_counter()
            if (k and left <= 0) or _job_cancelled():
                return
            yield k, max(0.01, left), run_transitions, True
            k += 1
    per_run = float(nrpa_seconds) / restarts
    for k in range(restarts):
        if _job_cancelled():
            return
        yield k, per_run, None, False


def _run_score(session, probe, path, stats):
    """(score, valid) of a GNRPA run's line: the replay the native search already returned (it raises on an illegal
    line), else a replay of its own (_valid_score)."""
    if stats.get("native") and hasattr(probe, "state"):
        return int(probe.state.score), not _bad_selects(probe)
    return _valid_score(session, path)


def _run_step(steps, k, chunked, score, ok, seconds, stats, run_seed, improved, extra=None):
    """A job's record of one GNRPA run: every run of the restarts split (``gnrpa<k>``, as before); of runs of
    RUN_TRANSITIONS only the ones that improved the job's best (without their rollouts) and one ``gnrpa_runs`` summary
    the caller appends -- a 40 s job holds about a hundred runs."""
    if not chunked:
        steps.append({"step": f"gnrpa{k}", "score": score, "valid": ok, "seconds": round(seconds, 1),
                      "rollouts": stats.get("rollouts"), "search_seed": run_seed, **(extra or {})})
    elif improved:
        steps.append({"step": f"run{k}", "score": score, "valid": ok, "search_seed": run_seed, **(extra or {})})


def _time_run(timing, wall, stats):
    """one GNRPA run into a job's ``timing`` (None: not kept) -- its wall, the core's own search seconds and its
    preparation (the prior table and the core's copy of the round, native_search), the Python rest (the replay and the
    score of its line), and the core's counters."""
    if timing is None:
        return
    core = float(stats.get("elapsed_seconds") or 0.0)
    prep = float(stats.get("prep_seconds") or 0.0)
    timing["gnrpa"] = timing.get("gnrpa", 0.0) + wall
    timing["gnrpa_core"] = timing.get("gnrpa_core", 0.0) + core
    timing["gnrpa_prep"] = timing.get("gnrpa_prep", 0.0) + prep
    timing["gnrpa_python"] = timing.get("gnrpa_python", 0.0) + max(0.0, wall - core - prep)
    timing["runs"] = timing.get("runs", 0) + 1
    for k in ("expansions", "rollouts"):
        timing[k] = timing.get(k, 0) + int(stats.get(k) or 0)
    timing["native_runs"] = timing.get("native_runs", 0) + int(bool(stats.get("native")))


def _time_tail(timing, key, started):
    if timing is not None:
        timing[key] = timing.get(key, 0.0) + (time.perf_counter() - started)


@lru_cache(maxsize=1)
def _frozen_archive_prior(path):
    return load_prior(path)


def _search_paths(session, start_path, *, nrpa_seconds, tail_seconds, seed, prior,
                  from_scratch, polish_start=True, restarts=DEFAULT_RESTARTS,
                  stabilization=DEFAULT_STABILIZATION, rules=None, tail_turns=None, objective_out=None,
                  repetition_limit=0, run_transitions=None, nrpa_transitions=None, timing=None,
                  chunk_observer=None):
    """One search on one session with one search seed."""
    lr = {"repetition_limit": int(repetition_limit)} if repetition_limit else {}
    if rules is not None or tail_turns is not None:
        return _search_paths_rules(session, start_path, nrpa_seconds=nrpa_seconds, tail_seconds=tail_seconds,
                                   seed=seed, prior=prior, from_scratch=from_scratch, polish_start=polish_start,
                                   restarts=restarts, stabilization=stabilization, rules=rules,
                                   tail_turns=tail_turns, objective_out=objective_out,
                                   run_transitions=run_transitions, nrpa_transitions=nrpa_transitions,
                                   timing=timing, **lr)
    steps = []
    if from_scratch:
        best_path, best = None, None
    else:
        best_path, best = tuple(start_path), _valid_score(session, start_path)[0]
        if polish_start:
            for k in (1, 2):
                t_tail = time.perf_counter()
                path, meta = _tail_polish(session, best_path, k, deadline=tail_seconds)
                score, ok = _valid_score(session, path)
                _time_tail(timing, "tail_start", t_tail)
                steps.append({"step": f"tail{k}", "score": score, "valid": ok,
                              "exact": meta.get("exact")})
                if ok and _beats(session, path, score, best_path, best):
                    best_path, best = tuple(path), score
    if nrpa_seconds > 0 or nrpa_transitions:
        hint = None
        if best_path is not None:
            hint = {"format": "gkms.initial_route.v1", "objective_version": SCORE_OBJECTIVE_VERSION,
                    "entry_state_hash": SV.entry_state_hash(session),
                    "action_path": [list(a) for a in best_path], "source": "player_route"}
        restarts = max(1, int(restarts or 1))
        raw_best, raw_path = None, None
        n_runs, rollouts, chunked = 0, 0, False
        switches = SearchSwitches.from_env()
        shapes = None
        if switches.chunk_experiment and nrpa_transitions == 8_500_000 and run_transitions == 500_000:
            from GakumasCLI.scenario.chunk_shapes import ChunkShapes
            if switches.archive_prior:
                prior = _frozen_archive_prior(switches.archive_prior)
            shapes = ChunkShapes(switches, session, prior)
        started = time.perf_counter()
        for k, deadline, transitions, chunked in _gnrpa_runs(nrpa_seconds, restarts, run_transitions,
                                                            nrpa_transitions):
            run_seed = _run_seed(seed, k, chunked)
            t0 = time.perf_counter()
            root, prefix, local_prior, local_stabilization, details = session, (), prior, None, {}
            shape_args = lr
            if shapes is not None:
                root, prefix, local_prior, local_stabilization, details = shapes.prepare(k, transitions, raw_path)
                if switches.deep_nesting:
                    shape_args = dict(lr, levels=3, iterations=16)
            charged = details.get("charged", 0)
            if charged and not 0 < charged < transitions:
                raise ValueError("experimental traversal exceeds this chunk's budget")
            _b, path, stats = policy_search(root, deadline=deadline, seed=run_seed, variant="gnrpa",
                                            prior=local_prior, initial_route=hint if k == 0 and not chunked else None,
                                            transitions=transitions - charged if charged else transitions,
                                            stabilization=int(local_stabilization or stabilization or 1), **shape_args)
            if prefix:
                path = tuple(prefix) + tuple(path)
            score, ok = _run_score(session, _b, path, stats)
            if shapes is not None:
                shapes.observe(k, path, score, ok and _b.finished)
            _time_run(timing, time.perf_counter() - t0, stats)
            improved = ok and (raw_best is None or lesson_rank(session, path, score)
                               > lesson_rank(session, raw_path, raw_best))
            n_runs += 1
            rollouts += int(stats.get("rollouts") or 0)
            _run_step(steps, k, chunked, score, ok, deadline if deadline is not None else time.perf_counter() - t0,
                      stats, run_seed, improved,
                      {"repetition_stops": stats.get("repetition_stops")} if lr else None)
            if improved:
                raw_best, raw_path = score, path
            if chunk_observer is not None:
                chunk_observer(k, run_seed, score, ok and _b.finished, stats, len(prefix), details)
        if chunked:
            steps.append({"step": "gnrpa_runs", "runs": n_runs, "run_transitions": transitions, "score": raw_best,
                          "rollouts": rollouts, "seconds": round(time.perf_counter() - started, 1)})
        if raw_path is not None and _job_cancelled():
            steps.append({"step": "cancelled", "runs": n_runs, "score": raw_best})
            if _beats(session, raw_path, raw_best, best_path, best):
                best_path, best = tuple(raw_path), raw_best
        elif raw_path is not None:
            t_tail = time.perf_counter()
            path, _meta = _tail_polish(session, raw_path, 2, deadline=tail_seconds)
            score, ok = _valid_score(session, path)
            _time_tail(timing, "tail", t_tail)
            steps.append({"step": "tail2", "score": score, "valid": ok, "restarts": restarts,
                          "stabilization": int(stabilization or 1),
                          "tail_turns": (_meta or {}).get("turns"), "tail_native": bool((_meta or {}).get("native")),
                          "tail_exact": (_meta or {}).get("exact")})
            polished = lesson_rank(session, path, score)
            if ok and len(polished) > 1 and polished < lesson_rank(session, raw_path, raw_best):
                ok = False
            if ok and _beats(session, path, score, best_path, best):
                best_path, best = tuple(path), score
            elif raw_best is not None and _beats(session, raw_path, raw_best, best_path, best):
                best_path, best = tuple(raw_path), raw_best
    return best_path, best, steps


def _search_paths_rules(session, start_path, *, nrpa_seconds, tail_seconds, seed, prior, from_scratch,
                        polish_start, restarts, stabilization, rules, tail_turns, objective_out, repetition_limit=0,
                        run_transitions=None, nrpa_transitions=None, timing=None):
    """_search_paths with a timeline: the same steps, ranked by ``rules.evaluate``'s objective."""
    steps = []

    def value(path):
        score, ok = _valid_score(session, path)
        if rules is None:
            return score, ok, score, []
        ev = rules.evaluate(session, path)
        return score, ok, ev["objective"], ev["unmet"]

    kw = {"rules": rules} if rules is not None else {}
    best_path, best, best_v, best_unmet = None, None, None, []
    if not from_scratch:
        best_path = tuple(start_path)
        best, _ok, best_v, best_unmet = value(best_path)
        if polish_start:
            for k in (1, 2):
                t_tail = time.perf_counter()
                path, meta = _tail_polish(session, best_path, k, deadline=tail_seconds, rules=rules,
                                          max_turns=tail_turns)
                score, ok, v, unmet = value(path)
                _time_tail(timing, "tail_start", t_tail)
                steps.append({"step": f"tail{k}", "score": score, "valid": ok, "objective": v,
                              "exact": (meta or {}).get("exact")})
                if ok and v > best_v:
                    best_path, best, best_v, best_unmet = tuple(path), score, v, unmet
    if nrpa_seconds > 0 or nrpa_transitions:
        hint = None
        if best_path is not None:
            hint = {"format": "gkms.initial_route.v1", "objective_version": SCORE_OBJECTIVE_VERSION,
                    "entry_state_hash": SV.entry_state_hash(session),
                    "action_path": [list(a) for a in best_path], "source": "player_route"}
        restarts = max(1, int(restarts or 1))
        raw = None
        n_runs, rollouts, chunked = 0, 0, False
        started = time.perf_counter()
        for k, deadline, transitions, chunked in _gnrpa_runs(nrpa_seconds, restarts, run_transitions,
                                                            nrpa_transitions):
            run_seed = _run_seed(seed, k, chunked)
            t0 = time.perf_counter()
            _b, path, stats = policy_search(session, deadline=deadline, seed=run_seed, variant="gnrpa",
                                            prior=prior, initial_route=hint if k == 0 and not chunked else None,
                                            transitions=transitions, stabilization=int(stabilization or 1), **kw,
                                            **({"repetition_limit": int(repetition_limit)} if repetition_limit else {}))
            score, ok, v, unmet = value(path)
            _time_run(timing, time.perf_counter() - t0, stats)
            improved = ok and (raw is None or v > raw[2])
            n_runs += 1
            rollouts += int(stats.get("rollouts") or 0)
            _run_step(steps, k, chunked, score, ok, deadline if deadline is not None else time.perf_counter() - t0,
                      stats, run_seed, improved,
                      {"objective": v, "native": bool(stats.get("native")),
                       "native_fallback": stats.get("native_fallback")})
            if improved:
                raw = (tuple(path), score, v, unmet)
        if chunked:
            steps.append({"step": "gnrpa_runs", "runs": n_runs, "run_transitions": transitions,
                          "score": raw[1] if raw else None, "objective": raw[2] if raw else None,
                          "rollouts": rollouts, "seconds": round(time.perf_counter() - started, 1)})
        if raw is not None and _job_cancelled():
            steps.append({"step": "cancelled", "runs": n_runs, "score": raw[1], "objective": raw[2]})
            if best_v is None or raw[2] > best_v:
                best_path, best, best_v, best_unmet = raw
        elif raw is not None:
            t_tail = time.perf_counter()
            path, _meta = _tail_polish(session, raw[0], 2, deadline=tail_seconds, rules=rules, max_turns=tail_turns)
            score, ok, v, unmet = value(path)
            _time_tail(timing, "tail", t_tail)
            steps.append({"step": "tail2", "score": score, "valid": ok, "objective": v, "restarts": restarts,
                          "stabilization": int(stabilization or 1),
                          "tail_turns": (_meta or {}).get("turns"), "tail_native": bool((_meta or {}).get("native")),
                          "tail_exact": (_meta or {}).get("exact"), "tail_skipped": (_meta or {}).get("skipped")})
            if ok and (best_v is None or v > best_v):
                best_path, best, best_v, best_unmet = tuple(path), score, v, unmet
            elif best_v is None or raw[2] > best_v:
                best_path, best, best_v, best_unmet = raw
    if objective_out is not None:
        objective_out.update(objective=best_v, unmet=best_unmet)
    return best_path, best, steps


# --- process pool: one process per search seed, full budget each ------------

def _pool_worker(connection, prior_path, master_dir=None, cancel=None):
    """Long-lived worker: loads the master (and the prior) once, then runs one
    search per job.
    """
    global _JOB_CANCEL
    try:
        master = Master.load(master_dir or MASTER_DIR)
        prior = load_prior(prior_path) if prior_path else None
        if WORKER_SETUP is not None:
            try:
                WORKER_SETUP(master, Path(master_dir or MASTER_DIR))
            except Exception:                 # noqa: BLE001
                pass
        connection.send({"status": "ready"})
    except Exception:
        connection.send({"status": "failed", "error": traceback.format_exc()})
        return
    root = (None, None)
    while True:
        job = connection.recv()
        if job is None:
            break
        started = time.monotonic()
        if job.get("kind") == "ps_rollout":
            try:
                from GKMSPS.ps import forecast as PSF
                connection.send({"status": "ok", "result": PSF.run_job(job["state"], tuple(job["job"])),
                                 "seconds": round(time.monotonic() - started, 3)})
            except Exception:
                connection.send({"status": "failed", "error": traceback.format_exc()[-2000:],
                                 "seconds": round(time.monotonic() - started, 3)})
            continue
        if job.get("kind") == "produce_score":
            try:
                from GakumasCLI.native.native_session import make_session
                session, _ = make_session(master, job["loadout"], seed=int(job["exam_seed"]))
                shape = {k: int(job[k]) for k in ("levels", "iterations", "stabilization") if job.get(k)}
                best, _path, stats = policy_search(session, deadline=float(job["budget"]),
                                                   seed=int(job.get("search_seed") or DEFAULT_SEARCH_SEED), **shape)
                out = {"status": "ok", "seed": job.get("exam_seed"), "score": int(best.state.score),
                       "native": bool((stats or {}).get("native")),
                       "seconds": round(time.monotonic() - started, 3)}
                if job.get("split"):
                    st = best.state
                    out["split"] = [int(st.score_vocal), int(st.score_dance), int(st.score_visual)]
                connection.send(out)
            except Exception:
                connection.send({"status": "failed", "seed": job.get("exam_seed"),
                                 "error": traceback.format_exc()[-2000:],
                                 "seconds": round(time.monotonic() - started, 3)})
            continue
        os.environ["GKMS_PICK_VARIANTS"] = str(int(job.get("pick_variants")
                                                   or (job.get("loadout") or {}).get("pick_variants") or 0))
        key = job.get("cancel")
        _JOB_CANCEL = None
        if cancel is not None and key and key[0] == cancel[0] and 0 < int(key[1]) < len(cancel[1]):
            _JOB_CANCEL = (cancel[1], int(key[1]))
        try:
            t_build = time.perf_counter()
            if job.get("session_blob") is not None:
                from GakumasCLI.scenario.hif_live import load_session
                session = load_session(job["session_blob"], master)
            elif job.get("loadout") is not None:
                from GakumasCLI.native.native_session import make_session
                key = json.dumps(job["loadout"], sort_keys=True, default=str)
                if root[0] != key:
                    root = (None, None)
                    root = (key, make_session(master, job["loadout"])[0])
                session = root[1].clone()
            else:
                session, _route = build(master, job["row"], job["drinks_mode"])
            rules = None
            if job.get("rules"):
                from GakumasCLI.scenario import timeline as TL
                rules = TL.RuleSet.from_job(job["rules"])
            rule_state = rules.start() if rules is not None else None
            t_prefix = time.perf_counter()
            for action in job.get("prefix") or ():
                if action[0] == "select":
                    session.state.select_index_queue.append(list(action[1]))
                elif rules is not None:
                    rules.advance(rule_state, session, tuple(action))
                else:
                    session.step(tuple(action), observe=False)
            start = None if job["start_path"] is None else tuple(tuple(a) for a in job["start_path"])
            extra = {}
            if rules is not None:
                extra["rules"] = rules.with_state(rule_state)
                extra["objective_out"] = objective = {}
            if job.get("tail_turns"):
                extra["tail_turns"] = int(job["tail_turns"])
            if job.get("nrpa_transitions"):
                extra["nrpa_transitions"] = int(job["nrpa_transitions"])
            timing = {"build": t_prefix - t_build}
            t_search = time.perf_counter()
            timing["prefix"] = t_search - t_prefix
            path, best, steps = _search_paths(session, start, nrpa_seconds=job["nrpa_seconds"],
                                              tail_seconds=job["tail_seconds"], seed=job["seed"],
                                              prior=prior, from_scratch=job["from_scratch"],
                                              polish_start=False,
                                              restarts=job.get("restarts", DEFAULT_RESTARTS),
                                              stabilization=job.get("stabilization", DEFAULT_STABILIZATION),
                                              repetition_limit=int(job.get("repetition_limit") or 0),
                                              timing=timing, **extra)
            timing["search"] = time.perf_counter() - t_search
            timing["job"] = time.monotonic() - started
            reply = {"status": "ok", "seed": job["seed"], "best": best,
                     "path": None if path is None else [list(a) for a in path],
                     "steps": steps, "seconds": round(time.monotonic() - started, 1),
                     "timing": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in timing.items()}}
            if rules is not None:
                reply["objective"], reply["unmet"] = objective.get("objective"), objective.get("unmet") or []
            if _job_cancelled():
                reply["cancelled"] = True
            _JOB_CANCEL = None
            connection.send(reply)
        except Exception:
            _JOB_CANCEL = None
            connection.send({"status": "failed", "seed": job["seed"], "error": traceback.format_exc(),
                             "seconds": round(time.monotonic() - started, 1)})
    connection.close()


def route_turn_cuts(session, route):
    """{turn: number of actions of ``route`` that closes that turn}, replayed on
    a clone of ``session`` (the state the route starts from)."""
    probe = session.clone()
    cuts = {}
    for index, action in enumerate(route):
        turn = probe.turn
        try:
            probe.step(tuple(action), observe=False)
        except ValueError:
            break
        if probe.turn != turn or probe.finished:
            cuts[int(turn)] = index + 1
        if probe.finished:
            break
    return cuts


def turn_branches(session, drinks=False, cap=4000):
    """the distinct ways to finish ``session``'s current turn, as action tuples, deduplicated by the state they reach
    (SV.entry_state_hash) and ordered by that path.
    """
    turn = int(session.turn)
    out, seen = [], set()
    stack, nodes = [((), session.clone())], 0
    while stack and nodes < cap:
        path, node = stack.pop()
        nodes += 1
        for action in node.legal_actions():
            if action[0] == "drink" and not drinks:
                continue
            child = node.clone()
            try:
                child.step(action, observe=False)
            except Exception:                       # noqa: BLE001
                continue
            step_path = path + (tuple(action),)
            if child.finished or int(child.turn) != turn:
                key = SV.entry_state_hash(child)
                if key not in seen:
                    seen.add(key)
                    out.append(step_path)
            else:
                stack.append((step_path, child))
    return sorted(out)


GATE_NODE_CAP = 1500
GATE_SECONDS = 2.0
GATE_KEEP_CAP = 48


def gate_branches(session, gate, rules=None, cap=GATE_NODE_CAP, seconds=GATE_SECONDS):
    """the ways to finish the current turn that keep the battle's debuff gate (tower.debuff_gate: ``debuffs`` 低下状態 at the
    start of turn ``turn``, read on the state after the turn's last step -- the next turn's start-of-turn gimmicks and
    a turn skip's 低下状態回復 included), as {"branches": distinct ways seen, "kept_count": the kept ways seen, "kept":
    [ways], "partial": the node cap stopped the enumeration}; None when the gate does not apply here (no gate, its
    turn reached, or fewer 低下状態 already).
    """
    if not gate or not hasattr(session, "debuff_count"):
        return None
    need, turn = int(gate["debuffs"]), int(session.turn)
    if turn >= int(gate["turn"]) or int(session.debuff_count()) < need:
        return None
    seen, kept = set(), []
    stack, nodes, until = [((), session.clone())], 0, time.monotonic() + float(seconds)
    while stack and nodes < cap and (nodes == 0 or time.monotonic() < until):
        path, node = stack.pop()
        nodes += 1
        for action in node.legal_actions():
            if action[0] == "drink":
                continue
            child = node.clone()
            try:
                child.step(action, observe=False)
            except Exception:                       # noqa: BLE001
                continue
            step_path = path + (tuple(action),)
            if child.finished or int(child.turn) != turn:
                key = SV.entry_state_hash(child)
                if key not in seen:
                    seen.add(key)
                    if int(child.debuff_count()) >= need:
                        kept.append(step_path)
            else:
                stack.append((step_path, child))
    kept.sort()
    count = len(kept)
    if len(kept) > GATE_KEEP_CAP:
        kept = [kept[i * len(kept) // GATE_KEEP_CAP] for i in range(GATE_KEEP_CAP)]
    if rules is not None:
        kept = [b for b in kept if _rules_allow(session, rules, b)]
    return {"branches": len(seen), "kept_count": count, "kept": kept, "partial": bool(stack)}


def er_waves(total_seconds, job_seconds=None, tail_seconds=None):
    """How many ER waves a per-seed budget buys (at least 1); ``job_seconds``: a timeline's ER job seconds;
    ``tail_seconds``: the job's tail when it is not ER_JOB_TAIL_SECONDS (a shortened wave's)."""
    per = ((ER_JOB_SECONDS if job_seconds is None else float(job_seconds))
           + (ER_JOB_TAIL_SECONDS if tail_seconds is None else float(tail_seconds)))
    return max(1, int(round(float(total_seconds) / per)))


WAVE_SHAPES = ("short", "wide")
SHORT_JOB_MIN = 11.0
HIF_SHORT_JOB_MIN = 20.0
SHORT_JOB_TAIL = 3.0
SHORT_LAG_SHARE = 0.3


def shaped_job(pool, width, job_seconds, tail_seconds, shape="short", minimum=None):
    """(job seconds, tail seconds, shortened?) for one ER run ``width`` jobs wide on ``pool``."""
    job, tail = float(job_seconds), float(tail_seconds)
    if shape != "short" or getattr(pool, "wave_size", None) is None:
        return job, tail, False
    local = int(getattr(pool, "size", 0) or 0)
    if local <= 0 or int(width) <= local:
        return job, tail, False
    lag = float(getattr(pool, "lag", 0.0) or 0.0)
    floor = SHORT_JOB_MIN if minimum is None else float(minimum)
    short = max(floor, job * local / int(width), lag / SHORT_LAG_SHARE)
    if short >= job:
        return job, tail, False
    return round(short, 1), min(tail, SHORT_JOB_TAIL), True


HELPER_FEEDS = ("sync", "async")
HELPER_FEED = SWITCHES.helper_feed
HELPER_SEED_OFFSET = 10000
HELPER_SEED_LIMIT = 10000


class HelperFeed:
    """keeps ``stream``'s helper slots busy for one elite-restarts run."""

    def __init__(self, stream, base_job, base_prefix, first_seed, limit, budget):
        self.stream = stream
        self.base_job, self.base_prefix = base_job, [list(a) for a in base_prefix]
        self.first_seed, self.limit, self.budget = int(first_seed), int(limit), float(budget)
        self.k = 0
        self.specs = {}
        self.top, self.plan, self.wave, self.end_at = [], None, 1, None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="helper-feed", daemon=True)
        self._started = False
        self.wakeups = 0

    def publish(self, top, plan, wave, end_at):
        with self._lock:
            self.top = [{"route": o["route"], "cuts": dict(o.get("cuts") or {}), "best": o.get("best")} for o in top]
            self.plan, self.wave, self.end_at = (list(plan) if plan else None), int(wave), end_at
        if not self._started:
            self._started = True
            self._thread.start()

    def _spec(self, k):
        spec = {"seed": self.first_seed + HELPER_SEED_OFFSET + k, "elite_prefix": [], "cut_after_turn": None}
        if self.top:
            elite = self.top[k % len(self.top)]
            usable = [t for t in range(1, max(2, self.limit)) if t in elite["cuts"]]
            if usable:
                if self.plan:
                    turn = min(usable, key=lambda t: (abs(t - self.plan[k % len(self.plan)]), t))
                else:
                    turn = usable[(k // len(self.top) + self.wave) % len(usable)]
                spec.update(elite_prefix=elite["route"][:elite["cuts"][turn]], cut_after_turn=turn,
                            elite_best=elite["best"])
        return spec

    def _loop(self):
        while not self._stop.is_set():
            self.wakeups += 1
            seen = self.stream.seq
            items = []
            with self._lock:
                room = self.stream.room()
                if self.end_at is not None and time.monotonic() + self.budget > self.end_at:
                    room = 0
                while room > 0 and self.k < HELPER_SEED_LIMIT:
                    spec = self._spec(self.k)
                    self.specs[self.k] = spec
                    items.append((self.k, dict(self.base_job, seed=spec["seed"], start_path=None, from_scratch=True,
                                               prefix=self.base_prefix + [list(a) for a in spec["elite_prefix"]])))
                    self.k += 1
                    room -= 1
            if items and not self._stop.is_set():
                self.stream.submit(items)
            self.stream.wait(0.25, seen)

    def _pairs(self, arrived):
        with self._lock:
            return [(self.specs.pop(key, {"seed": None, "elite_prefix": [], "cut_after_turn": None}), outcome, node)
                    for key, outcome, node in arrived]

    def take(self):
        return self._pairs(self.stream.take())

    def finish(self, grace):
        self._stop.set()
        if self._started:
            self._thread.join(timeout=5)
        return self._pairs(self.stream.finish(grace))

    def summary(self):
        return dict(self.stream.counts, slots=self.stream.slots, jobs=self.k, wakeups=self.wakeups)


def _er_cut_table(session, switches=None):
    """The ER_CUT_TABLE row for this round's mechanism, or None (the rotation)."""
    if (switches or SearchSwitches.from_env()).er_cut == "production":
        return None
    try:
        segment = str(session.observe().get("segment") or "")
    except Exception:
        return None
    return next((rates for mech, rates in ER_CUT_TABLE.items() if f"-{mech}-" in segment), None)


def _er_cut_plan(table, session, limit, n, wave):
    """n wanted cut turns for one wave: turns from the current one to limit-1, allotted by largest
    remainder in proportion to table[turns left]; rotated by wave so elites meet different cuts."""
    turns = list(range(max(1, int(getattr(session, "turn", 1) or 1)), limit))
    if not turns:
        return None
    w = [table.get(limit - t, 0.0) for t in turns]
    total = sum(w)
    if total <= 0:
        return None
    quota = [n * x / total for x in w]
    counts = [int(q) for q in quota]
    for i in sorted(range(len(turns)), key=lambda i: (-(quota[i] - counts[i]), i))[: n - sum(counts)]:
        counts[i] += 1
    plan = [t for t, c in zip(turns, counts) for _ in range(c)]
    shift = (wave * 7) % len(plan)
    return plan[shift:] + plan[:shift]


def elite_restarts(pool, base_job, session, *, waves, workers, first_seed, elites=ER_ELITES,
                   base_prefix=(), on_wave=None, stop_score=None, root=None, cut_table=None, rules=None,
                   branch_at=None, helpers=None, gate=None, context=None, incumbents=None):
    """run ``waves`` pool waves of ``workers`` jobs."""
    context = SearchContext.here(context)
    if stop_score is None:
        stop_score = context.target
    found, log = [], []
    feed, durations, grace = None, [], 0.0
    if helpers is not None and waves > 1:
        from GakumasCLI.node import mesh_pool as MP
        budget = float(base_job.get("nrpa_seconds") or 0) + float(base_job.get("tail_seconds") or 0)
        feed = HelperFeed(helpers, base_job, base_prefix, first_seed,
                          int(getattr(session.state, "limit_turn", 0) or 0), budget)
        grace = max(MP.GRACE_MIN, MP.GRACE_SHARE * budget)
    elif helpers is not None:
        helpers.finish(0)

    def merge_helpers(pairs, wave):
        """the helper outcomes that came, as routes in ``found``; (came, ok)."""
        ok = 0
        for spec, outcome, node in pairs:
            outcome["wave"], outcome["cut_after_turn"] = wave, spec["cut_after_turn"]
            outcome["helper"] = node or True
            if outcome.get("status") == "ok" and outcome.get("path") is not None and outcome.get("best") is not None:
                outcome["route"] = [list(a) for a in spec["elite_prefix"]] + [list(a) for a in outcome["path"]]
                outcome["cuts"] = route_turn_cuts(session, outcome["route"])
                _fill_objective(outcome, session, rules)
                found.append(outcome)
                ok += 1
        return len(pairs), ok

    limit = int(getattr(session.state, "limit_turn", 0) or 0)
    mode = ER_ROOT_BRANCH if ER_ROOT_BRANCH in ("wave1", "diverse") else "off"
    env_root = context.switches.er_root
    if env_root in ("off", "wave1", "diverse", "scored"):
        mode = env_root
    if root in ("off", "wave1", "diverse", "scored"):
        mode = root
    branches = turn_branches(session) if mode != "off" else []
    if rules is not None and branches:
        branches = [b for b in branches if _rules_allow(session, rules, b)]
    here = int(session.turn)
    table = _er_cut_table(session, context.switches) if cut_table is None else cut_table
    n_gate = int(workers) // ER_GATE_PER if gate else 0
    gated = gate_branches(session, gate, rules) if (n_gate and not branches) else None
    gate_row = (None if gated is None else
                {"debuffs": int(gate["debuffs"]), "turn": int(gate["turn"]), "branches": gated["branches"],
                 "kept": gated["kept_count"], "partial": gated["partial"]})
    gated = gated["kept"] if gated else None

    def _root(o):
        cut = (o.get("cuts") or {}).get(here)
        return json.dumps(o["route"][:cut] if cut else o["route"])

    if mode == "scored" and branches:
        started = time.monotonic()
        cand = branches[:ER_ROOT_SCORE_CAP]
        score_jobs = [dict(base_job, seed=first_seed + 900000 + i, start_path=None, from_scratch=True,
                           nrpa_seconds=ER_ROOT_SCORE_SECONDS, tail_seconds=0.0, restarts=1,
                           prefix=[list(a) for a in base_prefix] + [list(a) for a in b])
                      for i, b in enumerate(cand)]
        results = []
        for k in range(0, len(score_jobs), max(1, workers)):
            results += pool.run(score_jobs[k:k + max(1, workers)])
        value = []
        for i, (b, o) in enumerate(zip(cand, results)):
            ok = o.get("status") == "ok" and o.get("path") is not None and o.get("best") is not None
            if ok:
                o["wave"], o["cut_after_turn"] = 0, here
                o["route"] = [list(a) for a in b] + [list(a) for a in o["path"]]
                o["cuts"] = route_turn_cuts(session, o["route"])
                _fill_objective(o, session, rules)
                found.append(o)
            value.append((-_value(o) if ok else 1, i))
        branches = [cand[i] for _, i in sorted(value)]
        found.sort(key=lambda o: (-_value(o), o["seed"]))
        log.append({"wave": 0, "jobs": len(score_jobs), "seconds": round(time.monotonic() - started, 1),
                    "wave_best": _value(found[0]) if found else None, "best": _value(found[0]) if found else None,
                    "from_cut": here, "failed": sum(1 for o in results if o.get("status") != "ok"),
                    "first_error": None, "root_mode": mode, "root_branches": len(cand), "cut_rule": "root-score"})
    warm = 0
    for route in incumbents or ():
        route = [list(a) for a in route]
        try:
            probe = SV.replay_session(session, [tuple(a) if a[0] != "select" else (a[0], a[1]) for a in route])
        except Exception:                       # noqa: BLE001
            continue
        if not probe.finished or _bad_selects(probe):
            continue
        o = {"status": "ok", "seed": -1 - warm, "best": int(probe.state.score), "path": route, "route": route,
             "wave": 0, "cut_after_turn": None, "incumbent": True, "steps": []}
        o["cuts"] = route_turn_cuts(session, route)
        _fill_objective(o, session, rules)
        found.append(o)
        warm += 1
    if warm:
        found.sort(key=lambda o: (-_value(o), o["seed"]))
    try:
        for wave in range(1, waves + 1):
            if context.cancel is not None and context.cancel():
                if log:
                    log[-1]["cancelled"] = True
                break
            t_wave = time.perf_counter()
            top, seen = [], set()
            for o in found:
                key = _root(o) if mode == "diverse" else json.dumps(o["route"])
                if key not in seen:
                    seen.add(key)
                    top.append(o)
                if len(top) >= elites:
                    break
            jobs, specs = [], []
            later = wave > 1 or warm > 0
            plan = _er_cut_plan(table, session, limit, workers, wave) if (table and later and top) else None
            if feed is not None:
                per = (sum(durations) / len(durations)) if durations else feed.budget + 1.0
                hplan = (_er_cut_plan(table, session, limit, max(1, helpers.slots), wave)
                         if (table and wave > 1 and top) else None)
                feed.publish(top, hplan, wave, time.monotonic() + per * (waves - wave + 1))
            for i in range(workers):
                seed = first_seed + (wave - 1) * workers + i
                spec = {"seed": seed, "elite_prefix": [], "cut_after_turn": None}
                if wave == 1 and i < len(branches):
                    spec.update(elite_prefix=[list(a) for a in branches[i]], cut_after_turn=here, root_branch=i)
                elif wave == 1 and gated and i >= workers - n_gate:
                    k = (i - (workers - n_gate)) % len(gated)
                    spec.update(elite_prefix=[list(a) for a in gated[k]], cut_after_turn=here, gate_branch=k)
                elif later and top:
                    elite = top[i % len(top)]
                    usable = [t for t in range(1, max(2, limit)) if t in elite["cuts"]]
                    if usable and plan:
                        turn = min(usable, key=lambda t: (abs(t - plan[i]), t))
                        spec.update(elite_prefix=elite["route"][:elite["cuts"][turn]], cut_after_turn=turn,
                                    elite_best=elite["best"])
                    elif usable:
                        turn = usable[(i // len(top) + wave) % len(usable)]
                        spec.update(elite_prefix=elite["route"][:elite["cuts"][turn]], cut_after_turn=turn,
                                    elite_best=elite["best"])
                job = dict(base_job, seed=seed, start_path=None, from_scratch=True,
                           prefix=[list(a) for a in base_prefix] + [list(a) for a in spec["elite_prefix"]])
                jobs.append(job)
                specs.append(spec)
            started = time.monotonic()
            t_run = time.perf_counter()
            outcomes = pool.run(jobs)
            t_merge = time.perf_counter()
            wave_best = None
            for spec, outcome in zip(specs, outcomes):
                outcome["wave"], outcome["cut_after_turn"] = wave, spec["cut_after_turn"]
                if spec.get("gate_branch") is not None:
                    outcome["gate_branch"] = spec["gate_branch"]
                if (outcome.get("status") == "ok" and outcome.get("path") is not None
                        and outcome.get("best") is not None):
                    outcome["route"] = [list(a) for a in spec["elite_prefix"]] + [list(a) for a in outcome["path"]]
                    outcome["cuts"] = route_turn_cuts(session, outcome["route"])
                    _fill_objective(outcome, session, rules)
                    found.append(outcome)
                    wave_best = max(wave_best or _value(outcome), _value(outcome))
            helper_row = None
            t_helpers = time.perf_counter()
            if feed is not None:
                durations.append(time.monotonic() - started)
                came, ok = merge_helpers(feed.take(), wave)
                helper_row = {"came": came, "ok": ok, "out": helpers.out, "slots": helpers.slots}
            t_sort = time.perf_counter()
            found.sort(key=lambda o: (-_value(o), o["seed"]))
            log.append({"wave": wave, "jobs": len(jobs), "seconds": round(time.monotonic() - started, 1),
                        "wave_best": wave_best, "best": _value(found[0]) if found else None,
                        "from_cut": found[0]["cut_after_turn"] if found else None,
                        "failed": sum(1 for o in outcomes if o.get("status") != "ok"),
                        "first_error": next((str(o.get("error"))[-400:] for o in outcomes
                                             if o.get("status") != "ok" and o.get("error")), None),
                        "root_mode": mode, "root_branches": len(branches),
                        "cut_rule": "table" if plan else "rotation"})
            if wave == 1 and gate_row is not None:
                log[-1]["gate"] = dict(gate_row, best=max((_value(o) for o in found if o.get("gate_branch") is not None),
                                                          default=None))
            if helper_row is not None:
                log[-1]["helpers"] = helper_row
            row = log[-1]
            t_after = time.perf_counter()
            row["timing"] = wave_timing(t_wave, t_run, t_merge, t_helpers, t_sort, t_after, outcomes)
            if wave == 1 and branch_at and found:
                for b in branch_at:
                    found, row = branch_wave(pool, base_job, session, found, b, workers=workers,
                                             first_seed=first_seed + 800000 + 10000 * len(log), base_prefix=base_prefix,
                                             rules=rules)
                    log.append(row)
            if on_wave is not None and found:
                on_wave(found[0], wave)
            close_wave_timing(row["timing"], t_wave, t_after)
            if stop_score is not None and found and _value(found[0]) >= int(stop_score):
                log[-1]["stopped_at_cap"] = int(stop_score)
                break
        if feed is not None:
            before = _value(found[0]) if found else None
            at_cap = bool(log) and log[-1].get("stopped_at_cap") is not None
            came, ok = merge_helpers(feed.finish(0 if at_cap else grace), wave)
            found.sort(key=lambda o: (-_value(o), o["seed"]))
            if log:
                log[-1]["helpers_end"] = dict(feed.summary(), end_came=came, end_ok=ok)
            if on_wave is not None and found and (before is None or _value(found[0]) > before):
                on_wave(found[0], wave)
    finally:
        if feed is not None:
            feed.finish(0)
    return found, log


JOB_PARTS = ("build", "prefix", "tail_start", "gnrpa", "gnrpa_core", "gnrpa_prep", "gnrpa_python", "tail", "search",
             "job")
JOB_COUNTS = ("runs", "native_runs", "expansions", "rollouts")


def wave_timing(t_wave, t_run, t_merge, t_helpers, t_sort, t_after, outcomes):
    """a wave's seconds by part, for its log row (hif-journal.jsonl through hif_live)."""
    jobs = {k: 0.0 for k in JOB_PARTS}
    jobs.update({k: 0 for k in JOB_COUNTS})
    slowest, untimed = None, 0
    for o in outcomes:
        t = o.get("timing") if isinstance(o, dict) else None
        if not t:
            untimed += 1
            continue
        for k in JOB_PARTS + JOB_COUNTS:
            jobs[k] += t.get(k) or 0
        if slowest is None or (t.get("job") or 0) > (slowest.get("job") or 0):
            slowest = t
    run = t_merge - t_run
    out = {"prep": t_run - t_wave, "run": run, "merge": t_helpers - t_merge, "helpers": t_sort - t_helpers,
           "sort": t_after - t_sort,
           "dispatch": max(0.0, run - float((slowest or {}).get("job") or 0.0)) if slowest else None,
           "jobs": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in jobs.items()},
           "slowest": dict(slowest) if slowest else None, "untimed": untimed}
    for k in ("prep", "run", "merge", "helpers", "sort", "dispatch"):
        if out[k] is not None:
            out[k] = round(out[k], 4)
    return out


def close_wave_timing(timing, t_wave, t_after):
    now = time.perf_counter()
    timing["after"] = round(now - t_after, 4)
    timing["wall"] = round(now - t_wave, 4)
    timing["other"] = round(timing["wall"] - sum(timing[k] for k in ("prep", "run", "merge", "helpers", "sort",
                                                                     "after")), 4)


def _rules_allow(session, rules, actions):
    """whether ``actions`` from ``session`` pass the rules' filter at every step."""
    node, rs = session.clone(), rules.start()
    for a in actions:
        a = tuple(a)
        if node.finished:
            return False
        rules.sync(rs, node)
        if a not in rules.filter(rs, node, sorted(node.legal_actions())):
            return False
        rules.advance(rs, node, a)
    return True


def _start_of_turn(session, outcome, turn):
    """The prefix of ``outcome``'s route that reaches the start of ``turn`` (None when it does not)."""
    here = int(session.turn)
    if turn == here:
        return []
    cut = (outcome.get("cuts") or {}).get(turn - 1)
    if cut is None:
        return None
    return [list(a) for a in outcome["route"][:cut]]


def _replay(session, prefix):
    node = session.clone()
    for a in prefix:
        if a[0] == "select":
            node.state.select_index_queue.append(list(a[1]))
        else:
            node.step(tuple(a), observe=False)
    return node


def branch_wave(pool, base_job, session, found, spec, *, workers, first_seed, base_prefix=(), rules=None):
    """at the start of each turn of ``spec["turns"]`` on the best route's line, every distinct way to finish that turn
    (turn_branches, at most ``cap``) gets one ``seconds`` search; the ``keep`` best join ``found``.
    """
    started = time.monotonic()
    best = found[0]
    jobs, specs = [], []
    for turn in spec["turns"]:
        prefix = _start_of_turn(session, best, int(turn))
        if prefix is None:
            continue
        state = _replay(session, prefix)
        if state.finished or int(state.turn) != int(turn):
            continue
        branches = turn_branches(state, cap=int(spec.get("cap", 200)))
        if rules is not None:
            branches = [b for b in branches if _rules_allow(session, rules, [tuple(a) for a in prefix] + list(b))]
        for b in branches[:int(spec.get("cap", 200))]:
            seed = first_seed + len(jobs)
            pre = prefix + [list(a) for a in b]
            jobs.append(dict(base_job, seed=seed, start_path=None, from_scratch=True,
                             nrpa_seconds=float(spec.get("seconds", 1.0)), tail_seconds=0.0, restarts=1,
                             prefix=[list(a) for a in base_prefix] + pre))
            specs.append({"turn": int(turn), "prefix": pre})
    results = []
    for k in range(0, len(jobs), max(1, workers)):
        results += pool.run(jobs[k:k + max(1, workers)])
    scored = []
    for sp, o in zip(specs, results):
        if o.get("status") == "ok" and o.get("path") is not None and o.get("best") is not None:
            o["wave"], o["cut_after_turn"], o["branch_turn"] = None, sp["turn"] - 1, sp["turn"]
            o["route"] = sp["prefix"] + [list(a) for a in o["path"]]
            o["cuts"] = route_turn_cuts(session, o["route"])
            _fill_objective(o, session, rules)
            scored.append(o)
    scored.sort(key=lambda o: (-_value(o), o["seed"]))
    keep = scored[:int(spec.get("keep", 4))]
    found = sorted(found + keep, key=lambda o: (-_value(o), o["seed"]))
    row = {"wave": "branch", "turns": list(spec["turns"]), "jobs": len(jobs),
           "seconds": round(time.monotonic() - started, 1), "wave_best": _value(keep[0]) if keep else None,
           "best": _value(found[0]) if found else None, "failed": sum(1 for o in results if o.get("status") != "ok"),
           "first_error": None, "kept": len(keep)}
    return found, row


def sweep(pool, base_job, session, found, entries, *, workers, first_seed, base_prefix=(), rules=None):
    """for each entry (a drink or a card) and each of its turns, the best route's prefix to the start of that turn, then
    the drink / the card at once, then one ``seconds`` search.
    """
    table, jobs, specs = [], [], []
    best = found[0] if found else None
    here = int(session.turn)
    for k, e in enumerate(entries):
        for turn in e["turns"]:
            row = {"entry": k, "turn": int(turn), "what": e.get("drink") or e.get("card")}
            table.append(row)
            if turn < here:
                row["skip"] = "已过"
                continue
            prefix = [] if turn == here else (_start_of_turn(session, best, int(turn)) if best else None)
            if prefix is None:
                row["skip"] = "最好路线没到这回合开头"
                continue
            state = _replay(session, prefix)
            if state.finished or int(state.turn) != int(turn):
                row["skip"] = "到不了"
                continue
            action = None
            if e.get("drink"):
                if e["drink"] in state.drinks:
                    action = ("drink", state.drinks.index(e["drink"]))
            else:
                for i, card in enumerate(state.deck.hand):
                    if card["produce_card_id"] == e["card"] and (e.get("upgrade") is None or int(
                            card.get("current_upgrade_count", 0) or 0) == int(e["upgrade"])) and state.can_play(card):
                        action = ("play", i)
                        break
            if action is None:
                row["skip"] = "这回合开头不在手上" if e.get("card") else "这时已经不在了"
                continue
            if rules is not None and not _rules_allow(session, rules, [tuple(a) for a in prefix] + [action]):
                row["skip"] = "被规则禁止"
                continue
            pre = prefix + [list(action)]
            jobs.append(dict(base_job, seed=first_seed + len(jobs), start_path=None, from_scratch=True,
                             nrpa_seconds=float(e.get("seconds", 15.0)), tail_seconds=2.0, restarts=1,
                             prefix=[list(a) for a in base_prefix] + pre))
            specs.append((row, pre))
    results = []
    for k in range(0, len(jobs), max(1, workers)):
        results += pool.run(jobs[k:k + max(1, workers)])
    for (row, pre), o in zip(specs, results):
        if o.get("status") == "ok" and o.get("path") is not None and o.get("best") is not None:
            o["wave"], o["sweep"] = None, {"entry": row["entry"], "turn": row["turn"]}
            o["route"] = pre + [list(a) for a in o["path"]]
            o["cuts"] = route_turn_cuts(session, o["route"])
            _fill_objective(o, session, rules)
            found.append(o)
            row["score"], row["objective"] = o["best"], _value(o)
        else:
            row["skip"] = "搜索失败"
    found.sort(key=lambda o: (-_value(o), o.get("seed", 0)))
    return found, table


def _alns_points(session, route):
    """For each position of ``route`` (relative to ``session``) where a different action could be
    taken: (index, turn, state clone, legal actions).
    """
    probe = session.clone()
    out = []
    for index, action in enumerate(route):
        if probe.finished:
            break
        if action[0] != "select" and (index == 0 or route[index - 1][0] != "select"):
            out.append((index, int(probe.turn), probe.clone()))
        try:
            if action[0] == "select":
                probe.state.select_index_queue.append([int(i) for i in action[1]])
            else:
                probe.step(tuple(action), observe=False)
        except ValueError:
            break
    return out


def _alns_move(op, elite, session, limit, rng, cut_table=None):
    """(prefix, info) for one move on ``elite`` (an outcome with ``route`` / ``cuts``), or None when the move does not
    apply to that route.
    """
    route, cuts = elite["route"], elite.get("cuts") or {}
    here = int(session.turn)
    if op == "cut_table":
        table = _er_cut_table(session) if cut_table is None else cut_table
        turns = sorted(t for t in cuts if cuts[t] < len(route) and (table or {}).get(limit - t, 0.0) > 0)
        if not turns:
            return None
        turn = rng.choices(turns, weights=[table[limit - t] for t in turns])[0]
        return [list(a) for a in route[:cuts[turn]]], {"cut_after_turn": turn}
    if op.startswith("cut_"):
        lo, hi = {"cut_late": (1, 3), "cut_mid": (4, 6), "cut_early": (7, 99)}[op]
        turns = [t for t in cuts if lo <= limit - t <= hi and cuts[t] < len(route)]
        if not turns:
            return None
        turn = rng.choice(sorted(turns))
        return [list(a) for a in route[:cuts[turn]]], {"cut_after_turn": turn}
    points = _alns_points(session, route)
    rng.shuffle(points)
    for index, turn, state in points:
        taken = tuple(tuple(x) if isinstance(x, list) else x for x in route[index])
        legal = [tuple(a) for a in state.legal_actions() if a[0] != "select"]
        if op == "alt":
            pick = [a for a in legal if a != taken]
        elif op == "end":
            pick = [a for a in legal if a[0] == "end"] if taken[0] != "end" else []
        else:
            pick = [a for a in legal if a[0] == "drink"] if taken[0] != "drink" else []
        if not pick:
            continue
        forced = rng.choice(pick)
        return [list(a) for a in route[:index]] + [list(forced)], {"at": index, "turn": turn,
                                                                   "forced": list(forced), "taken": list(taken)}
    return None


def alns_rounds(pool, base_job, session, found, *, rounds=ALNS_ROUNDS, workers, first_seed, base_prefix=(),
                on_round=None, stop_score=None, job_seconds=ALNS_JOB_SECONDS,
                job_tail_seconds=ALNS_JOB_TAIL_SECONDS, elites=ALNS_ELITES, rules=None, cut_table=None):
    """``rounds`` ALNS rounds from the routes in ``found`` (elite_restarts' outcomes, relative to ``session``, the state
    after ``base_prefix``).
    """
    rng = random.Random(int(first_seed) * 7919 + 13)
    found = sorted((o for o in found if o.get("route") is not None and o.get("best") is not None),
                   key=lambda o: (-_value(o), o.get("seed", 0)))
    limit = int(getattr(session.state, "limit_turn", 0) or 0)
    weights = {op: 1.0 for op in ALNS_OPERATORS}
    log = []
    for o in found:
        if "cuts" not in o:
            o["cuts"] = route_turn_cuts(session, o["route"])
    for rnd in range(1, int(rounds) + 1):
        pool_elites, seen = [], set()
        for o in found:
            key = json.dumps(o["route"])
            if key not in seen:
                seen.add(key)
                pool_elites.append(o)
            if len(pool_elites) >= elites:
                break
        if not pool_elites:
            break
        best_before = _value(pool_elites[0])
        jobs, specs = [], []
        rank_w = [1.0 / (k + 1) for k in range(len(pool_elites))]
        for i in range(max(1, int(workers))):
            spec = None
            for _try in range(8):
                op = rng.choices(ALNS_OPERATORS, weights=[weights[x] for x in ALNS_OPERATORS])[0]
                elite = rng.choices(pool_elites, weights=rank_w)[0]
                move = _alns_move(op, elite, session, limit, rng, cut_table)
                if move is not None and rules is not None and not _rules_allow(
                        session, rules, [tuple(a) if a[0] != "select" else ("select", a[1]) for a in move[0]]):
                    move = None
                if move is not None:
                    spec = {"op": op, "prefix": move[0], "info": move[1], "source_best": _value(elite)}
                    break
            if spec is None:
                spec = {"op": "fresh", "prefix": [], "info": {}, "source_best": None}
            seed = int(first_seed) + ALNS_SEED_OFFSET + (rnd - 1) * max(1, int(workers)) + i
            jobs.append(dict(base_job, seed=seed, start_path=None, from_scratch=True,
                             nrpa_seconds=float(job_seconds), tail_seconds=float(job_tail_seconds), restarts=1,
                             prefix=[list(a) for a in base_prefix] + [list(a) for a in spec["prefix"]]))
            specs.append(spec)
        started = time.monotonic()
        outcomes = pool.run(jobs)
        reward = {op: [] for op in ALNS_OPERATORS}
        new_best, round_best, uses = best_before, None, {}
        for spec, outcome in zip(specs, outcomes):
            op = spec["op"]
            uses[op] = uses.get(op, 0) + 1
            ok = outcome.get("status") == "ok" and outcome.get("path") is not None and outcome.get("best") is not None
            r = ALNS_SIGMA[2]
            if ok:
                outcome["wave"], outcome["alns_round"], outcome["alns_op"] = None, rnd, op
                outcome["alns_move"] = spec["info"]
                outcome["route"] = [list(a) for a in spec["prefix"]] + [list(a) for a in outcome["path"]]
                outcome["cuts"] = route_turn_cuts(session, outcome["route"])
                _fill_objective(outcome, session, rules)
                found.append(outcome)
                v = _value(outcome)
                round_best = max(round_best or v, v)
                if v > new_best:
                    r, new_best = ALNS_SIGMA[0], v
                elif spec["source_best"] is not None and v > spec["source_best"]:
                    r = ALNS_SIGMA[1]
            if op in reward:
                reward[op].append(r)
        for op in ALNS_OPERATORS:
            if reward[op]:
                weights[op] = max(ALNS_MIN_WEIGHT, (1 - ALNS_REACTION) * weights[op]
                                  + ALNS_REACTION * (sum(reward[op]) / len(reward[op])))
        found.sort(key=lambda o: (-_value(o), o.get("seed", 0)))
        log.append({"round": rnd, "jobs": len(jobs), "seconds": round(time.monotonic() - started, 1),
                    "round_best": round_best, "best": _value(found[0]) if found else None,
                    "improved": bool(found and _value(found[0]) > best_before), "uses": uses,
                    "weights": {op: round(w, 3) for op, w in weights.items()},
                    "failed": sum(1 for o in outcomes if o.get("status") != "ok"),
                    "first_error": next((str(o.get("error"))[-400:] for o in outcomes
                                         if o.get("status") != "ok" and o.get("error")), None)})
        if on_round is not None and found:
            on_round(found[0], rnd)
        if stop_score is not None and found and _value(found[0]) >= int(stop_score):
            log[-1]["stopped_at_cap"] = int(stop_score)
            break
    return found, log


def side_by_side(pool, width, count):
    """How many of ``count`` independent searches of this machine's width can run in one wave of ``width`` jobs on
    ``pool``: 1 unless ``pool`` lends helpers (a MeshPool: ``wave_size`` and ``size``, this machine's workers)."""
    if count <= 1 or getattr(pool, "wave_size", None) is None:
        return 1
    local = int(getattr(pool, "size", 0) or 0)
    if local <= 0:
        return 1
    return max(1, min(int(count), int(width) // local))


class WaveJoin:
    """the waves of ``parties`` searches run side by side as one pool wave."""

    def __init__(self, pool, parties):
        self.pool = pool
        self.active = set(range(int(parties)))
        self.pending, self.results = {}, {}
        self.running, self.stopped = False, False
        self.waves = 0
        self._cond = threading.Condition()

    class _View:
        def __init__(self, join, index):
            self.join, self.index = join, index

        def run(self, jobs):
            return self.join._run(self.index, list(jobs))

    def view(self, index):
        return self._View(self, index)

    def stop(self):
        with self._cond:
            self.stopped = True
            self._cond.notify_all()

    def leave(self, index):
        with self._cond:
            self.active.discard(index)
            self._cond.notify_all()

    @staticmethod
    def _stopped(jobs):
        return [{"status": "stopped", "seed": j.get("seed")} for j in jobs]

    def _run(self, index, jobs):
        with self._cond:
            if self.stopped:
                return self._stopped(jobs)
            self.pending[index] = jobs
            self._cond.notify_all()
            while index not in self.results:
                if self.stopped and index in self.pending:
                    del self.pending[index]
                    return self._stopped(jobs)
                if not self.running and self.pending and self.active <= set(self.pending):
                    self._lead()
                    continue
                self._cond.wait(1.0)
            return self.results.pop(index)

    def _lead(self):
        """Run every handed-in wave as one (called with the condition held; released while the pool runs)."""
        batch, self.pending = self.pending, {}
        self.running = True
        order = sorted(batch)
        joined, where = [], []
        for k in range(max(len(v) for v in batch.values())):
            for party in order:
                if k < len(batch[party]):
                    joined.append(batch[party][k])
                    where.append(party)
        self._cond.release()
        try:
            try:
                outcomes = self.pool.run(joined)
            except Exception as exc:              # noqa: BLE001
                outcomes = [{"status": "failed", "seed": j.get("seed"), "error": f"{type(exc).__name__}: {exc}"}
                            for j in joined]
        finally:
            self._cond.acquire()
        back = {party: [] for party in order}
        for party, outcome in zip(where, outcomes):
            back[party].append(outcome)
        self.results.update(back)
        self.waves += 1
        self.running = False
        self._cond.notify_all()


def run_side_by_side(pool, parties, join=None):
    """run ``parties`` (callables taking a pool) at once on ``pool`` through one WaveJoin (``join``, else a new one);
    their results in order.
    """
    join = join if join is not None else WaveJoin(pool, len(parties))
    results, errors = [None] * len(parties), []

    def work(i, fn):
        try:
            results[i] = fn(join.view(i))
        except BaseException as exc:              # noqa: BLE001
            errors.append(exc)
            join.stop()
        finally:
            join.leave(i)

    threads = [threading.Thread(target=work, args=(i, fn), name=f"side-by-side-{i}", daemon=True)
               for i, fn in enumerate(parties)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if errors:
        raise errors[0]
    return results, join


class SeedPool:
    """One long-lived process per search seed."""

    def __init__(self, count, prior_path, master_dir=None):
        self._lock = threading.Lock()
        self.context = mp.get_context("spawn")
        self.prior_path = prior_path
        self.master_dir = None if master_dir is None else str(master_dir)
        self._cancel_token = os.urandom(8).hex()
        self._cancel_flags = self.context.RawArray("b", CANCEL_GROUPS)
        self._cancel_next = 0
        self._cancel_lock = threading.Lock()
        self.workers = [self._spawn() for _ in range(count)]
        for slot in self.workers:
            self._ready(slot)

    def _spawn(self):
        parent, child = self.context.Pipe()
        process = self.context.Process(target=_pool_worker,
                                       args=(child, self.prior_path, self.master_dir,
                                             (self._cancel_token, self._cancel_flags)),
                                       daemon=True)
        process.start()
        child.close()
        return [process, parent, False]

    def _ready(self, slot):
        if slot[2]:
            return
        try:
            reply = slot[1].recv()
        except EOFError:
            reply = {"status": "failed", "error": "worker exited before reporting ready"}
        if reply.get("status") != "ready":
            raise RuntimeError("search worker failed to start:\n" + reply.get("error", ""))
        slot[2] = True

    def run(self, jobs):
        with self._lock:
            return self._run(jobs)

    def cancel_key(self):
        """a fresh cancel group, as the ``cancel`` value of the jobs it covers."""
        with self._cancel_lock:
            self._cancel_next = self._cancel_next % (CANCEL_GROUPS - 1) + 1
            group = self._cancel_next
            self._cancel_flags[group] = 0
        return [self._cancel_token, group]

    def cancel(self, key):
        """the running jobs of ``key``'s group stop at their next GNRPA run; True when the key is this pool's."""
        if not key or key[0] != self._cancel_token or not 0 < int(key[1]) < CANCEL_GROUPS:
            return False
        self._cancel_flags[int(key[1])] = 1
        return True

    def _run(self, jobs):
        pairs = []
        for i, job in enumerate(jobs):
            if not self.workers[i][0].is_alive():
                self.workers[i] = self._spawn()
                self._ready(self.workers[i])
            self.workers[i][1].send(job)
            pairs.append((self.workers[i], job))
        outcomes = []
        for slot, job in pairs:
            try:
                outcomes.append(slot[1].recv())
            except EOFError:
                outcomes.append({"status": "failed", "seed": job.get("seed", job.get("exam_seed")),
                                 "error": f"worker exited without a result (exit code {slot[0].exitcode})"})
        return outcomes

    def close(self):
        with self._lock:
            self._close()

    def _close(self):
        for process, connection, _ready in self.workers:
            try:
                connection.send(None)
            except OSError:
                pass
        for process, connection, _ready in self.workers:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
            connection.close()
        self.workers = []


def _rollouts(steps):
    """Rollouts summed over the GNRPA runs of one seed process."""
    counts = [s.get("rollouts") for s in steps or ()
              if s.get("step", "").startswith("gnrpa") and s.get("rollouts") is not None]
    return sum(counts) if counts else None


def search_round(master, row, nrpa_seconds, tail_seconds, drinks_mode, *, prior=None,
                 from_scratch=False, search_seeds=1, search_seed=DEFAULT_SEARCH_SEED, pool=None,
                 restarts=DEFAULT_RESTARTS, stabilization=DEFAULT_STABILIZATION,
                 strategy=DEFAULT_STRATEGY):
    started = time.monotonic()
    session, route = build(master, row, drinks_mode)
    reseeded = row.get("reseeded_from") is not None
    if reseeded:
        from_scratch, played = True, None
    else:
        played = SV.replay_session(session, route)
    record = {
        "seed": row["seed"], "step": row["step_type"], "idol": row["idol_card_id"],
        "recorded": None if reseeded else int(row["recorded_score"]),
        "engine_on_record": None if reseeded else int(played.state.score),
        "drinks_mode": drinks_mode, "drinks_available": list(session.drinks),
        "limit_turn": int(row["limit_turn"]),
        "from_scratch": bool(from_scratch), "prior": list(prior.sources) if prior is not None else None,
        "search_seeds": [search_seed + i for i in range(max(1, search_seeds))],
        "nrpa_seconds": nrpa_seconds, "tail_seconds": tail_seconds,
        "restarts": int(restarts), "stabilization": int(stabilization),
    }
    if reseeded:
        record.update(reseeded_from=row["reseeded_from"], reseed_index=row.get("reseed_index"))
    elif played.state.score != row["recorded_score"] or _bad_selects(played):
        record["status"] = "skipped_engine_inexact"
        record["seconds"] = round(time.monotonic() - started, 1)
        return record, None
    use_er = (strategy == "er" and search_seeds > 1 and pool is not None and from_scratch
              and er_waves(nrpa_seconds + tail_seconds) > 1)
    record["strategy"] = "er" if use_er else "seeds"
    if use_er:
        waves = er_waves(nrpa_seconds + tail_seconds)
        base_job = {"row": row, "drinks_mode": drinks_mode, "nrpa_seconds": ER_JOB_SECONDS,
                    "tail_seconds": ER_JOB_TAIL_SECONDS, "restarts": int(restarts),
                    "stabilization": int(stabilization)}
        fresh, _ = build(master, row, drinks_mode)
        found, wave_log = elite_restarts(pool, base_job, fresh, waves=waves, workers=search_seeds,
                                         first_seed=search_seed)
        record.update(er_waves=wave_log, er_job_seconds=ER_JOB_SECONDS, er_job_tail_seconds=ER_JOB_TAIL_SECONDS)
        seed_results = [{"seed": o["seed"], "best": o["best"], "status": "ok", "wave": o["wave"],
                         "cut_after_turn": o["cut_after_turn"], "seconds": o.get("seconds")} for o in found]
        best_path, best, steps = None, None, []
        for o in found:
            path = tuple(tuple(a) for a in o["route"])
            score, ok = _valid_score(session, path)
            if ok and score == o["best"]:
                best_path, best = path, score
                steps = [dict(s, search_seed=o["seed"]) for s in o["steps"]]
                break
    elif search_seeds <= 1 or pool is None:
        best_path, best, steps = _search_paths(session, route, nrpa_seconds=nrpa_seconds,
                                               tail_seconds=tail_seconds, seed=search_seed,
                                               prior=prior, from_scratch=from_scratch,
                                               restarts=restarts, stabilization=stabilization)
        seed_results = [{"seed": search_seed, "best": best, "status": "ok",
                         "rollouts": _rollouts(steps)}]
    else:
        steps = []
        if from_scratch:
            start_path, best_path, best = None, None, None
        else:
            best_path, best, steps = _search_paths(session, route, nrpa_seconds=0,
                                                   tail_seconds=tail_seconds, seed=search_seed,
                                                   prior=prior, from_scratch=False, restarts=1)
            start_path = [list(a) for a in best_path]
        jobs = [{"row": row, "drinks_mode": drinks_mode, "start_path": start_path,
                 "nrpa_seconds": nrpa_seconds, "tail_seconds": tail_seconds,
                 "seed": search_seed + i, "from_scratch": from_scratch,
                 "restarts": int(restarts), "stabilization": int(stabilization)} for i in range(search_seeds)]
        outcomes = pool.run(jobs)
        seed_results, candidates = [], []
        for o in sorted(outcomes, key=lambda o: o.get("seed", 0)):
            entry = {"seed": o.get("seed"), "best": o.get("best"), "status": o["status"],
                     "seconds": o.get("seconds")}
            if o["status"] == "ok":
                entry["rollouts"] = _rollouts(o["steps"])
                if o["path"] is not None and o["best"] is not None:
                    candidates.append(o)
            else:
                entry["error"] = (o.get("error") or "")[-400:]
            seed_results.append(entry)
        candidates.sort(key=lambda o: (-o["best"], o["path"], o["seed"]))
        for o in candidates:
            path = tuple(tuple(a) for a in o["path"])
            score, ok = _valid_score(session, path)
            if ok and score == o["best"] and (best is None or score > best):
                best_path, best = path, score
                steps = steps + [dict(s, search_seed=o["seed"]) for s in o["steps"]]
                break
    if best_path is None:
        record.update(status="no_valid_route", seed_results=seed_results, steps=steps,
                      seconds=round(time.monotonic() - started, 1))
        return record, None
    final = SV.replay_session(session, best_path)
    if reseeded:
        record.update(status="searched_reseed", best=best, steps=steps, seed_results=seed_results,
                      route=[list(a) for a in best_path], unresolved_in_engine_route=final.state.unresolved_effects[:10],
                      seconds=round(time.monotonic() - started, 1))
        return record, session
    player_unresolved = {json.dumps(u, sort_keys=True, ensure_ascii=False) for u in played.state.unresolved_effects}
    new_unresolved = [u for u in final.state.unresolved_effects
                      if json.dumps(u, sort_keys=True, ensure_ascii=False) not in player_unresolved]
    divergence = first_divergence(route, best_path)
    probe, divergence_turn = session.clone(), None
    for i, action in enumerate(best_path):
        if i == divergence:
            divergence_turn = probe.turn
            break
        probe.step(tuple(action), observe=False)
    record.update(first_divergence_action=divergence, first_divergence_turn=divergence_turn,
                  unresolved_only_in_engine_route=new_unresolved[:10])
    record.update(status="searched", best=best, gain=best - record["engine_on_record"],
                  gain_permil=round(1000.0 * (best - record["engine_on_record"]) / record["engine_on_record"], 1),
                  ratio=round(best / record["engine_on_record"], 4),
                  steps=steps, seed_results=seed_results,
                  route=[list(a) for a in best_path], player_route=[list(a) for a in route],
                  seconds=round(time.monotonic() - started, 1))
    return record, session


def describe(master, row, drinks_mode, path):
    """Turn-by-turn text of a route: hand, action, parameter gained."""
    session, _ = build(master, row, drinks_mode)
    lines, turn = [], None
    for action in path:
        if session.turn != turn:
            turn = session.turn
            hand = [(c.get("resolved_card") or session.deck.refresh_card(c)).get("name")
                    + "+" * int(c.get("current_upgrade_count") or 0) for c in session.deck.hand]
            lines.append(f"- t{turn}  hand: {' / '.join(hand)}")
        before = session.state.score
        if action[0] == "play":
            c = session.deck.hand[action[1]]
            name = (c.get("resolved_card") or session.deck.refresh_card(c)).get("name") \
                + "+" * int(c.get("current_upgrade_count") or 0)
            text = f"play {name}"
        elif action[0] == "drink":
            text = f"drink {master.drinks.get(session.drinks[action[1]], {}).get('name', session.drinks[action[1]])}"
        else:
            text = "end turn" + (f" ({session.state.playable} play left)" if session.state.playable > 0 else "")
        session.step(tuple(action), observe=False)
        lines.append(f"    - {text}  (+{session.state.score - before:,})")
    lines.append(f"- final {session.state.score:,}")
    return lines


def first_divergence(a, b):
    for i, (x, y) in enumerate(zip(a, b)):
        if list(x) != list(y):
            return i
    return min(len(a), len(b))


# --- prior training ----------------------------------------------------------

def train_prior(master, rows, train_keys, drinks_mode, teacher_files, out_path, holdout, teacher_keys=None):
    """Teachers: the players' own routes of the training players, plus the
    engine routes (gain > 0) an earlier run found for those same rounds."""
    examples, sources = [], []
    n_player = 0
    for key in sorted(train_keys):
        session, route = build(master, rows[key], drinks_mode)
        played = SV.replay_session(session, route)
        if played.state.score != rows[key]["recorded_score"] or _bad_selects(played) or not played.finished:
            continue
        examples.append((session, route))
        n_player += 1
    sources.append(f"player_routes:{n_player}")
    for f in teacher_files:
        n_engine = 0
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("loadout") is not None:
                if r.get("best") is None or not r.get("route"):
                    continue
                session, _ = build_hif_loadout_session(master, r["loadout"])
                path = tuple(tuple(a) for a in r["route"])
                score, ok = _valid_score(session, path)
                if not ok or score != r["best"] or not SV.replay_session(session, path).finished:
                    continue
                examples.append((session, path))
                n_engine += 1
                continue
            if r.get("reseeded_from") is not None:
                base = (r["reseeded_from"], r["step"])
                if base not in train_keys or r.get("status") != "searched_reseed" or r.get("best") is None:
                    continue
                row = reseeded_row(rows[base], r.get("reseed_index"))
                if row["seed"] != r["seed"]:
                    continue
            else:
                key = (r["seed"], r["step"])
                allowed = train_keys if teacher_keys is None else teacher_keys
                if key not in allowed or r.get("status") != "searched" or r.get("gain", 0) <= 0:
                    continue
                row = rows[key]
            mode = r.get("drinks_mode", drinks_mode)
            session, _route = build(master, row, mode)
            path = tuple(tuple(a) for a in r["route"])
            score, ok = _valid_score(session, path)
            if not ok or score != r["best"] or not SV.replay_session(session, path).finished:
                continue
            examples.append((session, path))
            n_engine += 1
        sources.append(f"engine_routes:{n_engine}:{Path(f).name}")
    sources.append(f"holdout:{holdout}")
    sources.append(f"drinks:{drinks_mode}")
    prior = learn_prior(examples, sources=tuple(sources))
    save_prior(prior, out_path)
    return prior, len(examples), sources


# --- main ---------------------------------------------------------------------

def _median(values):
    return round(statistics.median(values), 1) if values else None


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("plain", nargs="+")
    ap.add_argument("--idol", default="hmsz,jsna")
    ap.add_argument("--seeds", default="")
    ap.add_argument("--exclude-seeds", default="",
                    help="rounds to leave out of everything (search and teachers), e.g. the user's own history")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--nrpa-seconds", type=float, default=60.0)
    ap.add_argument("--tail-seconds", type=float, default=20.0)
    ap.add_argument("--drinks", choices=("fair", "used", "all"), default="fair")
    ap.add_argument("--search-seeds", type=int, default=1,
                    help="processes per round, each with its own search seed and the full budget")
    ap.add_argument("--search-seed", type=int, default=DEFAULT_SEARCH_SEED, help="first search seed")
    ap.add_argument("--restarts", type=int, default=DEFAULT_RESTARTS,
                    help="GNRPA runs per seed process, splitting --nrpa-seconds between them "
                         "")
    ap.add_argument("--stabilization", type=int, default=DEFAULT_STABILIZATION,
                    help="GNRPA stabilization")
    ap.add_argument("--strategy", choices=("er", "seeds"), default=DEFAULT_STRATEGY,
                    help="multi-seed strategy with --search-seeds > 1 and --from-scratch: elite prefix "
                         "restarts or best-of-N long seeds")
    ap.add_argument("--prior", default="", help="learned policy prior (gkms.score_policy_prior.v1)")
    ap.add_argument("--from-scratch", action="store_true",
                    help="do not use the player's route as the starting incumbent")
    ap.add_argument("--holdout", choices=("none", "even", "odd"), default="none",
                    help="player-level split: which players (by position per idol) are the held-out set")
    ap.add_argument("--use-holdout", action="store_true", help="search only the held-out (test) players")
    ap.add_argument("--use-train", action="store_true", help="search only the non-held-out (train) players")
    ap.add_argument("--train-prior", default="", help="write a prior learned from the non-held-out players; no search")
    ap.add_argument("--no-player-routes", action="store_true",
                    help="train only on --teacher-routes, leaving the players' own routes out "
                         "(tests whether the engine's own routes are enough on their own)")
    ap.add_argument("--teacher-routes", default="",
                    help="comma-separated routes.jsonl of earlier runs; their improved routes join the teachers")
    ap.add_argument("--reseed", type=int, default=0,
                    help="search k fresh-seed variants of every selected round (from scratch) instead of the rounds themselves")
    ap.add_argument("--shard", default="", help="i/n: take every n-th round starting at i (0-based)")
    ap.add_argument("--out", default="work/route_search")
    ap.add_argument("--merge", default="",
                    help="comma-separated output dirs of earlier (sharded) runs: no search, "
                         "their routes.jsonl are merged into <out>/routes.jsonl and routes.md")
    args = ap.parse_args(argv)
    master = Master.load(MASTER_DIR)
    rows, group_of = load_rounds(args.plain)
    idols = {x.strip() for x in args.idol.split(",") if x.strip()}
    exclude = parse_keys(args.exclude_seeds) if args.exclude_seeds else set()
    rows = {k: r for k, r in rows.items() if r["idol_card_id"][7:11] in idols and k not in exclude}
    train_keys, test_keys, split_report = player_split(rows, group_of, args.holdout, exclude)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "split.json").write_text(json.dumps({
        "holdout": args.holdout, "excluded": sorted(f"{k[0]}/{k[1]}" for k in exclude),
        "train_rounds": len(train_keys), "test_rounds": len(test_keys),
        "groups": split_report}, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.train_prior:
        teachers = [t for t in args.teacher_routes.split(",") if t.strip()]
        prior, n, sources = train_prior(master, rows, set() if args.no_player_routes else train_keys,
                                        args.drinks, teachers, args.train_prior, args.holdout,
                                        teacher_keys=train_keys)
        print("PRIOR", args.train_prior, "examples", n, "weights", len(prior.weights), "sources", sources)
        print("TRAIN players", sum(1 for g in split_report if g["role"] == "train"),
              "rounds", len(train_keys), "| TEST players", sum(1 for g in split_report if g["role"] == "test"),
              "rounds", len(test_keys))
        return

    keys = sorted(rows)
    if args.use_holdout:
        keys = [k for k in keys if k in test_keys]
    if args.use_train:
        keys = [k for k in keys if k in train_keys]
    if args.seeds:
        wanted = parse_keys(args.seeds)
        keys = [k for k in keys if k in wanted]
    if args.shard:
        i, n = (int(v) for v in args.shard.split("/"))
        keys = keys[i::n]
    if args.limit:
        keys = keys[: args.limit]
    if args.reseed:
        variants = []
        for key in keys:
            for j in range(args.reseed):
                row = reseeded_row(rows[key], j)
                rows[(row["seed"], row["step_type"])] = row
                variants.append((row["seed"], row["step_type"]))
        keys = variants
    prior = load_prior(args.prior) if args.prior else None
    records = []
    if args.merge:
        for d in args.merge.split(","):
            for line in (Path(d) / "routes.jsonl").read_text(encoding="utf-8").splitlines():
                if line.strip():
                    records.append(json.loads(line))
        records.sort(key=lambda r: (r["seed"], r["step"]))
        keys = []
        (out / "routes.jsonl").write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    else:
        (out / "routes.jsonl").write_text("", encoding="utf-8")
    pool = None
    if keys and args.search_seeds > 1:
        pool = SeedPool(args.search_seeds, args.prior or None)
    try:
        with (out / "routes.jsonl").open("a", encoding="utf-8") as fh:
            for key in keys:
                record, _ = search_round(master, rows[key], args.nrpa_seconds, args.tail_seconds, args.drinks,
                                         prior=prior, from_scratch=args.from_scratch,
                                         search_seeds=args.search_seeds, search_seed=args.search_seed, pool=pool,
                                         restarts=args.restarts, stabilization=args.stabilization,
                                         strategy=args.strategy)
                records.append(record)
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                fh.flush()
                print(key, record["idol"][7:11], record["status"], record.get("engine_on_record"),
                      record.get("best"), record.get("gain"), record.get("ratio"), record.get("seconds"), flush=True)
    finally:
        if pool is not None:
            pool.close()
    searched = [r for r in records if r["status"] == "searched"]
    improved = [r for r in searched if r["gain"] > 0]
    if args.merge:
        title = f"merged from {len(args.merge.split(','))} runs"
    else:
        title = (f"{args.drinks} drinks, GNRPA {args.nrpa_seconds:.0f}s, tail {args.tail_seconds:.0f}s, "
                 f"{args.search_seeds} seed(s) from {args.search_seed}"
                 + (", from scratch" if args.from_scratch else ", from player route")
                 + (f", prior {Path(args.prior).name}" if args.prior else "")
                 + (f", holdout {args.holdout}" + (" (test players only)" if args.use_holdout else "")
                    if args.holdout != "none" else ""))
    md = [f"# H.I.F route search ({title})", "",
          f"rounds {len(records)}, searched {len(searched)}, improved {len(improved)}", ""]
    if searched:
        md += ["| idol/step | searched | engine > player | median ratio | median gain ‰ |", "|---|---|---|---|---|"]
        for idol in sorted({r["idol"][7:11] for r in searched}):
            for step in (16, 18):
                sub = [r for r in searched if r["idol"][7:11] == idol and r["step"] == step]
                if sub:
                    md.append(f"| {idol} R{1 if step == 16 else 2} | {len(sub)} | {sum(1 for r in sub if r['gain'] > 0)} | "
                              f"{round(statistics.median(r['ratio'] for r in sub), 3)} | "
                              f"{_median([r['gain_permil'] for r in sub])} |")
        md.append(f"| all | {len(searched)} | {len(improved)} | "
                  f"{round(statistics.median(r['ratio'] for r in searched), 3)} | "
                  f"{_median([r['gain_permil'] for r in searched])} |")
        md.append("")
    md += ["| seed/step | idol | player | engine best | gain | gain ‰ | first different turn | engine-only unresolved | seeds (best/worst) |",
           "|---|---|---|---|---|---|---|---|---|"]
    for r in records:
        if r["status"] == "searched_reseed":
            continue
        if r["status"] != "searched":
            md.append(f"| {r['seed']}/{r['step']} | {r['idol'][7:11]} | {r['recorded']:,} | {r['status']} | | | | | |")
        else:
            seeds = [s["best"] for s in r.get("seed_results", []) if s.get("best") is not None]
            seed_text = f"{max(seeds):,} / {min(seeds):,}" if len(seeds) > 1 else ""
            md.append(f"| {r['seed']}/{r['step']} | {r['idol'][7:11]} | {r['recorded']:,} | {r['best']:,} | {r['gain']:+,} | {r['gain_permil']} | "
                      f"{r.get('first_divergence_turn') if r['gain'] > 0 else ''} | {len(r.get('unresolved_only_in_engine_route') or [])} | {seed_text} |")
    reseeded = [r for r in records if r["status"] == "searched_reseed"]
    if reseeded:
        md += ["", f"## reseeded problems ({len(reseeded)})", "",
               "| new seed/step | from seed | idol | engine best | seeds (best/worst) | unresolved |", "|---|---|---|---|---|---|"]
        for r in reseeded:
            seeds = [s["best"] for s in r.get("seed_results", []) if s.get("best") is not None]
            seed_text = f"{max(seeds):,} / {min(seeds):,}" if len(seeds) > 1 else ""
            md.append(f"| {r['seed']}/{r['step']} | {r['reseeded_from']} | {r['idol'][7:11]} | {r['best']:,} | {seed_text} | "
                      f"{len(r.get('unresolved_in_engine_route') or [])} |")
    for r in improved:
        row = rows.get((r["seed"], r["step"]))
        if row is None:
            continue
        div = first_divergence(r["player_route"], r["route"])
        md += ["", f"## {r['seed']}/{r['step']} ({r['idol'][7:11]}): {r['recorded']:,} -> {r['best']:,} ({r['gain']:+,})",
               f"routes diverge at action {div}", "", "player:"]
        md += describe(master, row, r.get("drinks_mode", args.drinks), r["player_route"])
        md += ["", "engine:"]
        md += describe(master, row, r.get("drinks_mode", args.drinks), r["route"])
    for r in reseeded:
        row = rows.get((r["seed"], r["step"]))
        if row is None:
            continue
        md += ["", f"## {r['seed']}/{r['step']} ({r['idol'][7:11]}, reseeded from {r['reseeded_from']}): {r['best']:,}", "", "engine:"]
        md += describe(master, row, r.get("drinks_mode", args.drinks), r["route"])
    (out / "routes.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("ROUNDS", len(records), "SEARCHED", len(searched), "IMPROVED", len(improved), "RESEEDED", len(reseeded),
          "MEDIAN_RATIO", round(statistics.median(r["ratio"] for r in searched), 4) if searched else None)


if __name__ == "__main__":
    main()
