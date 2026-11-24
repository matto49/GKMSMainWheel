# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Independent process searches: a full wall budget per seed, score-only selection."""
from __future__ import annotations

import multiprocessing as mp
import os
import pickle
import subprocess
import tempfile
import time
import traceback

DEFAULT_BUDGET_SECONDS = 60.0
# None means physical cores - 1.
MEASURED_WORKER_CAP = None


def physical_cores():
    if os.name == "nt":
        try:
            output = subprocess.check_output(
                ["powershell.exe", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Processor | Measure-Object NumberOfCores -Sum).Sum"],
                creationflags=subprocess.CREATE_NO_WINDOW, text=True)
            return max(1, int(output.strip()))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    else:
        try:
            from pathlib import Path
            pairs = set()
            for cpu in Path('/sys/devices/system/cpu').glob('cpu[0-9]*'):
                t = cpu / 'topology'
                pairs.add(((t / 'physical_package_id').read_text().strip(),
                           (t / 'core_id').read_text().strip()))
            if pairs:
                return len(pairs)
        except OSError:
            pass
    return 1


def default_workers():
    count = max(1, physical_cores() - 1)
    return min(count, MEASURED_WORKER_CAP) if MEASURED_WORKER_CAP else count


def _solve_one(session, kwargs, cancel):
    from GakumasCLI.scenario.solver import solve_segment
    started = time.perf_counter()
    cpu = time.process_time()
    kwargs = dict(kwargs)
    deadline_at = kwargs.pop("deadline_at", None)
    if deadline_at is not None and kwargs.get("budget_seconds") is not None:
        kwargs["budget_seconds"] = max(1.0, deadline_at - time.time())
    try:
        result = solve_segment(session, cancel=cancel, **kwargs)
        return dict(status="ok", search_seed=kwargs["search_seed"], result=result,
                    cpu_seconds=time.process_time()-cpu,
                    elapsed_seconds=time.perf_counter()-started)
    except Exception:
        return dict(status="failed", search_seed=kwargs["search_seed"],
                    error=traceback.format_exc(),
                    cpu_seconds=time.process_time()-cpu,
                    elapsed_seconds=time.perf_counter()-started)


def _worker(connection, session, kwargs, cancel):
    try:
        connection.send(_solve_one(session, kwargs, cancel))
    finally:
        connection.close()


def _pool_worker(connection, cancel):
    """Long-lived worker: imports once, then solves one job per message."""
    from GakumasCLI.scenario import solver  # noqa: F401
    connection.send(("ready",))
    while True:
        message = connection.recv()
        if message[0] == "exit":
            break
        if message[0] == "warm":
            connection.send(("ready",))
            continue
        kind, payload, kwargs = message
        if kind == "solve_file":
            with open(payload, "rb") as handle:
                session = pickle.load(handle)
        else:
            session = payload
        connection.send(_solve_one(session, kwargs, cancel))
    connection.close()


class WarmPool:
    """Persistent search workers: the interpreter start-up, engine import and session unpickling stagger that
    spawn-per-call pays on every opening solve and replan is paid once here, at construction.
    """

    def __init__(self, workers=None, context="spawn"):
        self.count = default_workers() if workers is None else int(workers)
        if self.count < 1:
            raise ValueError("workers must be positive")
        self.context = mp.get_context(context)
        self.stop = self.context.Event()
        self.dump_seconds = None
        self.workers = [self._spawn() for _ in range(self.count)]

    def _spawn(self):
        parent, child = self.context.Pipe()
        process = self.context.Process(target=_pool_worker, args=(child, self.stop), daemon=True)
        process.start()
        child.close()
        return [process, parent, False]

    def warm(self, timeout=None):
        """Block until every worker has imported the engine."""
        started = time.perf_counter()
        for slot in self.workers:
            process, connection, ready = slot
            if ready:
                continue
            remaining = None if timeout is None else max(0.0, timeout-(time.perf_counter()-started))
            if remaining is not None and not connection.poll(remaining):
                continue
            try:
                if connection.recv()[0] == "ready":
                    slot[2] = True
            except EOFError:
                pass
        return time.perf_counter()-started

    def _live_slot(self, index):
        slot = self.workers[index]
        if not slot[0].is_alive():
            try:
                slot[1].close()
            except OSError:
                pass
            self.workers[index] = slot = self._spawn()
        if not slot[2]:
            if slot[1].recv()[0] == "ready":
                slot[2] = True
        return slot

    def submit(self, session, kwargs_per_worker):
        """Send one job per worker; returns [(index, process, connection)]."""
        self.stop.clear()
        self.release()
        handle, path = tempfile.mkstemp(prefix="gkms_session_", suffix=".pkl")
        dump_started = time.perf_counter()
        with os.fdopen(handle, "wb") as out:
            pickle.dump(session, out, protocol=pickle.HIGHEST_PROTOCOL)
        self.dump_seconds = time.perf_counter() - dump_started
        self._payload_path = path
        budget = kwargs_per_worker[0].get("budget_seconds") if kwargs_per_worker else None
        deadline_at = time.time() + float(budget) if budget is not None else None
        jobs = []
        for i, kwargs in enumerate(kwargs_per_worker):
            process, connection, _ready = self._live_slot(i)
            if deadline_at is not None:
                kwargs = dict(kwargs, deadline_at=deadline_at)
            connection.send(("solve_file", path, kwargs))
            jobs.append((i, process, connection))
        return jobs

    _payload_path = None

    def release(self):
        path, self._payload_path = self._payload_path, None
        if path:
            for _ in range(5):
                try:
                    os.remove(path)
                    break
                except FileNotFoundError:
                    break
                except OSError:
                    time.sleep(0.2)

    def close(self):
        self.release()
        for process, connection, _ready in self.workers:
            try:
                connection.send(("exit",))
            except OSError:
                pass
        for process, connection, _ready in self.workers:
            process.join(timeout=2)
            if process.is_alive():
                process.terminate()
                process.join()
            connection.close()
        self.workers = []


_WARM_POOL = None


def get_warm_pool(workers=None):
    """Process-wide pool, created (and warmed) on first use."""
    global _WARM_POOL
    count = default_workers() if workers is None else int(workers)
    if _WARM_POOL is None or _WARM_POOL.count != count or not _WARM_POOL.workers:
        if _WARM_POOL is not None:
            _WARM_POOL.close()
        _WARM_POOL = WarmPool(count)
        _WARM_POOL.warm()
    return _WARM_POOL


def solve_parallel(session, *, workers=None, budget_seconds=DEFAULT_BUDGET_SECONDS,
                   search_seed=182, mode="nrpa", policy_prior=None, cancel=None,
                   expansions=None, width=6, initial_route=None, pool=None):
    """Match solve_segment's mutation contract; retain each branch's own RNG."""
    from GakumasCLI.scenario import solver
    if pool is not None:
        count = pool.count if workers is None else int(workers)
        if count > pool.count:
            raise ValueError(f"workers={count} exceeds pool size {pool.count}")
    else:
        count = default_workers() if workers is None else int(workers)
    if count < 1 or (budget_seconds is not None and budget_seconds <= 0):
        raise ValueError("workers and budget_seconds must be positive")
    if budget_seconds is None and expansions is None:
        raise ValueError("a wall or logical budget is required")
    started = time.perf_counter()
    context = mp.get_context("spawn")
    stop = pool.stop if pool is not None else context.Event()
    jobs, outcomes, pending = [], [], []
    kwargs_list = [dict(budget_seconds=budget_seconds, search_seed=search_seed+i,
                        mode=mode, policy_prior=policy_prior, expansions=expansions,
                        width=width, initial_route=initial_route) for i in range(count)]
    try:
        if pool is not None:
            jobs = pool.submit(session, kwargs_list)
        else:
            for i, kwargs in enumerate(kwargs_list):
                receiver, sender = context.Pipe(duplex=False)
                process = context.Process(target=_worker, args=(sender, session, kwargs, stop))
                try:
                    process.start()
                except Exception as exc:
                    outcomes.append(dict(status="failed", search_seed=search_seed+i,
                                         error=f"spawn: {type(exc).__name__}: {exc}"))
                    receiver.close()
                else:
                    jobs.append((i, process, receiver))
                finally:
                    sender.close()
        pending = list(jobs)
        cancelled_at = None
        while pending:
            if cancel is not None and cancel.is_set():
                stop.set()
                cancelled_at = cancelled_at or time.perf_counter()
            for job in list(pending):
                i, process, receiver = job
                if receiver.poll():
                    try:
                        outcomes.append(receiver.recv())
                    except EOFError:
                        outcomes.append(dict(status="failed", search_seed=search_seed+i,
                                             error=f"worker exited without result ({process.exitcode})"))
                    pending.remove(job)
                elif not process.is_alive():
                    outcomes.append(dict(status="failed", search_seed=search_seed+i,
                                         error=f"worker exited without result ({process.exitcode})"))
                    pending.remove(job)
                elif cancelled_at and time.perf_counter()-cancelled_at > 2:
                    process.terminate()
            if pending:
                time.sleep(0.01)
    finally:
        if pool is not None:
            if pending:
                stop.set()
                for _, _process, receiver in pending:
                    if receiver.poll(2):
                        try:
                            receiver.recv()
                        except EOFError:
                            pass
            stop.clear()
            pool.release()
        else:
            stop.set()
            for _, process, receiver in jobs:
                process.join(timeout=2)
                if process.is_alive():
                    process.terminate()
                    process.join()
                receiver.close()
    good = [row for row in outcomes if row["status"] == "ok"]
    if not good:
        raise RuntimeError(f"all search workers failed: {outcomes!r}")
    best = min(good, key=lambda r: (-r["result"]["score"],
                                    r["result"]["action_path"], r["search_seed"]))
    result = best["result"]
    if solver.entry_state_hash(session) != result["entry_state_hash"]:
        raise ValueError("parallel entry changed during search")
    replayed = solver.replay(session, [tuple(a) for a in result["action_path"]])
    if replayed["score"] != result["score"] or solver.rng_trace_hash(session) != result["rng_trace_hash"]:
        raise ValueError("parallel winner independent replay mismatch")
    result["parallel"] = dict(workers=count, budget_seconds_per_worker=budget_seconds,
                              selected_seed=best["search_seed"],
                              elapsed_seconds=time.perf_counter()-started,
                              warm_pool=pool is not None,
                              independent_replay_verified=True,
                              outcomes=[{k: v for k, v in r.items() if k != "result"} |
                                        ({"score": r["result"]["score"],
                                          "search_stats": r["result"]["search_stats"]}
                                         if r["status"] == "ok" else {})
                                        for r in sorted(outcomes, key=lambda r: r["search_seed"])])
    result["budget_policy"] = {
        "initial_seconds": DEFAULT_BUDGET_SECONDS,
        "replan_seconds": DEFAULT_BUDGET_SECONDS,
        "replan_on": ["operator_deviation", "observed_state_mismatch", "rule_version_change"],
        "suffix_reuse": "delegated_to_live_workbench; not present in this engine-only lane",
    }
    solver._writeback_run(session, result)
    return result
