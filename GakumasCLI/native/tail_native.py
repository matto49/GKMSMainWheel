# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exact tail search on the native core."""
import ctypes as C
import json
import os
import threading
import time

T_MAXPATH = 256


class TailParams(C.Structure):
    _fields_ = [("node_cap", C.c_int64), ("deadline", C.c_double), ("tt_capacity", C.c_int64),
                ("use_tt", C.c_int32), ("threads", C.c_int32),
                ("items_per_thread", C.c_int32), ("pad", C.c_int32)]


class TailResult(C.Structure):
    _fields_ = [("status", C.c_int32), ("unsup_line", C.c_int32), ("unsup_info", C.c_int32),
                ("complete", C.c_int32), ("best_score", C.c_int64), ("n_path", C.c_int32),
                ("path", C.c_uint16 * T_MAXPATH), ("nodes", C.c_int64), ("leaves", C.c_int64),
                ("tt_hits", C.c_int64), ("tt_entries", C.c_int64),
                ("best_major", C.c_int64),
                ("greedy_choices", C.c_int64),
                ("stack_levels", C.c_int64)]


STATUS = {0: "complete", 1: "node_cap", 2: "deadline", 3: "unsupported", 4: "too_deep", 5: "no_memory"}
_NATIVE = None
_STOP_LOCK = threading.Lock()


def decode(code):
    kind, index = code >> 8, code & 0xFF
    return ("play", index) if kind == 0 else ("drink", index) if kind == 1 else ("end",)


def _bind(nat):
    """Declare gk_tail_exact on a loaded library (idempotent)."""
    lib = nat.lib
    if getattr(lib, "_gk_tail_bound", False):
        return nat
    lib.gk_tail_exact.argtypes = [C.c_void_p, C.POINTER(TailParams), C.POINTER(TailResult)]
    assert lib.gk_tail_result_size() == C.sizeof(TailResult), "TailResult layout"
    assert lib.gk_tail_params_size() == C.sizeof(TailParams), "TailParams layout"
    if hasattr(lib, "gk_tail_exact_controlled"):
        from GakumasCLI.native.native_search import RuleTable
        lib.gk_tail_stop_new.argtypes = []
        lib.gk_tail_stop_new.restype = C.c_void_p
        lib.gk_tail_stop_set.argtypes = lib.gk_tail_stop_free.argtypes = [C.c_void_p]
        lib.gk_tail_stop_set.restype = lib.gk_tail_stop_free.restype = None
        lib.gk_tail_exact_controlled.argtypes = [C.c_void_p, C.POINTER(TailParams), C.POINTER(RuleTable),
                                                  C.POINTER(TailResult), C.c_void_p]
        lib.gk_tail_exact_controlled.restype = C.c_int
    lib._gk_tail_bound = True
    return nat


def _native(master):
    global _NATIVE
    from GakumasCLI.native.diffrun import Native
    if _NATIVE is None:
        nat = _bind(Native(os.environ["GKMS_NATIVE_LIB"]))
        nat.load_master(master, json.load(open(os.environ["GKMS_NATIVE_STRINGS"], encoding="utf-8")))
        _NATIVE = nat
    elif _NATIVE.source_master is not master:
        _NATIVE.load_master(master, _NATIVE.strings)
    return _NATIVE


def tail_exact(session, *, node_cap=20_000_000, seconds=60.0, tt_capacity=1 << 23, use_tt=True, nat=None,
               control=None, threads=1, rules=None, items_per_thread=0):
    """(path, score, stats) for the best line from ``session`` to the end."""
    from GakumasCLI.native import portable as P
    nat = _native(session.master) if nat is None else _bind(nat)
    if rules is not None:
        from GakumasCLI.native.native_search import has_rules, rule_table, RuleTable, timeline_version
        if not has_rules(nat.lib):
            raise RuntimeError("this core takes no timeline rules")
        nat.lib.gk_tail_exact_rules.argtypes = [C.c_void_p, C.POINTER(TailParams), C.POINTER(RuleTable),
                                                C.POINTER(TailResult)]
    handle = params = None
    own = None
    if getattr(session, "native", False) and getattr(session, "shadow", None) is None:
        own = session.search_tables()
    else:
        nat.load_round(session)
        nat.import_bytes(P.encode_session(session))
    try:
        core = nat.core if own is None else own.core
        index = nat.index if own is None else own.index
        params = TailParams(node_cap=int(node_cap), deadline=-1.0 if seconds is None else time.monotonic() + seconds,
                            tt_capacity=int(tt_capacity) if use_tt else 0, use_tt=int(bool(use_tt)),
                            threads=max(1, int(threads or 1)), items_per_thread=max(0, int(items_per_thread or 0)))
        if seconds is not None:
            params.deadline = _host_now(nat) + float(seconds)
        if control is not None:
            if hasattr(nat.lib, "gk_tail_exact_controlled"):
                handle = nat.lib.gk_tail_stop_new()
                if not handle:
                    raise MemoryError("native tail stop")
            with _STOP_LOCK:
                control["params"] = params
                if handle:
                    control["_native_stop"] = (nat.lib.gk_tail_stop_set, handle)
                if control.get("stopped"):
                    _signal(control)
        result = TailResult()
        started = time.perf_counter()
        rule_ptr = None
        if rules is not None:
            rt = rule_table(rules, index, getattr(nat, "exam_rules_version", 0), timeline_version(nat.lib))
            rule_ptr = C.byref(rt)
            params.threads = 1
        if handle:
            rc = nat.lib.gk_tail_exact_controlled(core, C.byref(params), rule_ptr, C.byref(result), handle)
        elif rule_ptr is not None:
            rc = nat.lib.gk_tail_exact_rules(core, C.byref(params), rule_ptr, C.byref(result))
        else:
            rc = nat.lib.gk_tail_exact(core, C.byref(params), C.byref(result))
    finally:
        if control is not None and params is not None:
            with _STOP_LOCK:
                if control.get("params") is params:
                    control.pop("_native_stop", None)
                    control.pop("params")
        if handle:
            nat.lib.gk_tail_stop_free(handle)
        if own is not None:
            own.free()
    elapsed = time.perf_counter() - started
    path = [decode(result.path[i]) for i in range(result.n_path)]
    stats = dict(status=STATUS.get(int(rc), int(rc)), complete=bool(result.complete), nodes=int(result.nodes),
                 leaves=int(result.leaves), tt_hits=int(result.tt_hits), tt_entries=int(result.tt_entries),
                 unsup_line=int(result.unsup_line), unsup_info=int(result.unsup_info), seconds=round(elapsed, 3),
                 threads=int(params.threads), greedy_choices=int(result.greedy_choices),
                 stack_levels=int(result.stack_levels))
    return path, int(result.best_score) - int(result.best_major) * (1 << 64), stats


def _signal(control):
    native_stop = control.get("_native_stop")
    if native_stop:
        request, handle = native_stop
        request(handle)
    elif control.get("params") is not None:
        control["params"].deadline = 0.0


def stop(control):
    """Latch a stop before or during a tail search, from any thread."""
    with _STOP_LOCK:
        control["stopped"] = True
        _signal(control)


def _host_now(nat):
    """The native side compares against std::chrono::steady_clock; Python's
    time.monotonic() is the same clock on Windows and Linux (CLOCK_MONOTONIC)."""
    return time.monotonic()


from GakumasCLI.scenario.search_context import SearchSwitches as _Switches      # noqa: E402
TAIL_MAX_TURNS = _Switches.from_env().tail_max_turns
TAIL_POLISH_NODE_CAP = _Switches.from_env().tail_node_cap
DEFAULT_GROWTH = 30.0


def remaining_turns(session):
    state = session.state
    limit = getattr(state, "limit_turn", None)
    if limit is None:
        limit = session.config.limit_turn
    return int(limit) + int(getattr(state, "extra_turns", 0) or 0) - int(session.turn) + 1


def _forks(session, path, deepest):
    """[(remaining_turns, prefix_length, state)] at the start of each of the
    route's last ``deepest`` turns (and at the route's own start when it is
    already inside them), nearest the end first."""
    probe = session.clone()
    out = []
    if not probe.finished and remaining_turns(probe) <= deepest:
        out.append((remaining_turns(probe), 0, probe.clone()))
    turn = probe.turn
    for index, action in enumerate(path):
        if probe.finished:
            break
        probe.step(tuple(action), observe=False)
        if not probe.finished and probe.turn != turn:
            turn = probe.turn
            left = remaining_turns(probe)
            if left <= deepest:
                out.append((left, index + 1, probe.clone()))
    out.sort(key=lambda f: f[0])
    return out


def polish(session, path, turns=2, *, deadline=5.0, cancel=None, nat=None, fallback=None,
           max_turns=None, node_cap=None, rules=None):
    """Drop-in for ``scenario.solver.exact_tail_polish`` (same arguments and
    return shape) on the native core, deepening as described above.
    """
    from GakumasCLI.scenario import solver as SV
    started = time.monotonic()
    budget = max(0.0, float(deadline or 0.0))
    deepest = int(max_turns or TAIL_MAX_TURNS)
    cap = int(node_cap or TAIL_POLISH_NODE_CAP)
    path = tuple(tuple(a) for a in path)

    def value_of(line):
        if rules is not None:
            return rules.evaluate(session, line)["objective"]
        return SV.segment_value(SV.replay_session(session, line))

    def cancelled():
        return cancel is not None and cancel.is_set()

    before = value_of(path)
    best_path, best = path, before
    meta = {"turns": 0, "applied": False, "gain": 0, "native": True, "levels": [], "before": before}
    if cancelled():
        meta["cancelled"] = True
        return best_path, meta
    try:
        forks = _forks(session, path, max(deepest, int(turns)))
    except Exception as exc:                                    # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        forks = []
    done_prefix, last_nodes, growth, rate = None, None, None, None
    for left, prefix_len, state in forks:
        if done_prefix is not None and prefix_len >= done_prefix:
            continue
        if cancelled():
            meta["cancelled"] = True
            break
        elapsed = time.monotonic() - started
        remaining = budget - elapsed
        if remaining <= 0.02:
            break
        if last_nodes is not None and rate:
            projected = last_nodes * (growth or DEFAULT_GROWTH)
            if projected > cap or projected / rate > remaining:
                meta["stopped"] = {"turns": left, "projected_nodes": int(projected),
                                   "seconds_left": round(remaining, 3)}
                break
        try:
            extra = {}
            if rules is not None:
                fork_state, _node = rules.state_after(session, path[:prefix_len])
                extra["rules"] = rules.with_state(fork_state)
            tail, score, stats = tail_exact(state, node_cap=cap, seconds=remaining, nat=nat, **extra)
        except Exception as exc:                                # noqa: BLE001
            meta["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            break
        level = {"turns": left, "complete": stats["complete"], "nodes": stats["nodes"],
                 "seconds": stats["seconds"], "status": stats["status"],
                 "greedy_choices": stats["greedy_choices"]}
        meta["levels"].append(level)
        if stats["status"] == "unsupported":
            meta["error"] = f"native unsupported line {stats['unsup_line']} info {stats['unsup_info']}"
            break
        if tail and (score >= 0 or rules is not None):
            candidate = path[:prefix_len] + tuple(tuple(a) for a in tail)
            try:
                value = value_of(candidate)
            except Exception as exc:                            # noqa: BLE001
                level["replay_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
                value = None
            if value is not None and value > best:
                best_path, best = candidate, value
        if not stats["complete"]:
            break
        meta["turns"] = left
        if last_nodes:
            growth = max(2.0, stats["nodes"] / max(1, last_nodes))
        last_nodes = max(1, stats["nodes"])
        rate = stats["nodes"] / max(1e-3, stats["seconds"])
        done_prefix = prefix_len
        if prefix_len == 0:
            break
    if meta.get("error") and not meta["levels"] and fallback is not None:
        out_path, out_meta = fallback(session, path, turns, deadline=max(0.0, budget - (time.monotonic() - started)),
                                      cancel=cancel)
        out_meta = dict(out_meta or {}, native_error=meta["error"])
        return out_path, out_meta
    meta.update(exact=bool(meta["levels"]) and bool(meta["levels"][-1]["complete"]),
                after=best, seconds=round(time.monotonic() - started, 3))
    if best > before:
        meta.update(applied=True, gain=best - before)
    return best_path, meta
