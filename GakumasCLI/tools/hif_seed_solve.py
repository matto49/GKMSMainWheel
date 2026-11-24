# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""hif_seed_solve.py -- recover the H.I.F exam seed from what a player can see.

    usage:
      python -B tools/hif_seed_solve.py solve <loadout.json> <observed.json> [--lo N --hi N] [--turns 3,2,1,...]
      python -B tools/hif_seed_solve.py validate <plain dir>... [--window 22] [--draws 9] [--full SEED]
"""
import argparse
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
import GakumasCLI.paths as _gkms_paths                                 # noqa: E402
_gkms_paths.ensure_roots()

from GakumasCLI.engine.card_data import _f32                                    # noqa: E402
from GakumasCLI.engine.rng import ExamRandom                                   # noqa: E402
from GakumasCLI.scenario.exam_session import Master                             # noqa: E402
from GakumasCLI.scenario.hif import build_hif_loadout_session                   # noqa: E402

KERNEL_SRC = Path(__file__).with_name('seed_solver.c')
KERNEL = Path(__file__).with_name('seed_solver.exe' if os.name == 'nt' else 'seed_solver')
# the kernel is a console program: run from the tray (pythonw) it would open a window per range
NO_WINDOW = {'creationflags': 0x08000000} if os.name == 'nt' else {}
_MASTER = None


def master():
    global _MASTER
    if _MASTER is None:
        _MASTER = Master.load(ROOT / 'ro' / 'master' / 'tables')
    return _MASTER


def ident(card):
    return (card['produce_card_id'], int(card.get('current_upgrade_count', card.get('upgrade_count', 0)) or 0))


def capture_opening(loadout, seed):
    """Build the session with ``seed`` and capture: the pre-shuffle deck, the
    raw post-shuffle order, and how many RNG calls came before the shuffle.
    """
    from GakumasCLI.scenario.exam_session import deferred_open
    from GakumasCLI.native.native_session import gk_shuffle, open_loading
    doc = dict(loadout, seed=int(seed))
    with deferred_open():
        loading, _meta = build_hif_loadout_session(master(), doc)
    dk = loading.deck
    pre = [ident(c) for c in dk.deck]
    if dk.audition_parameter_values is not None:
        k = max(0, int(dk.audition_limit_turn) - 3) + int(dk.audition_npc_rng_advances or 0)
    else:
        k = int(dk.segment_start_rng_advances or 0)
    rng = ExamRandom(int(dk.rng.state))
    for _ in range(k):
        rng.next_u32()
    post, _state = gk_shuffle(pre, rng.state)
    return pre, post, k, open_loading(loading, doc)


def prepare(loadout):
    """Seed-independent parts of the opening: pre-shuffle order and k."""
    pre_a, _, k_a, _ = capture_opening(loadout, 1)
    pre_b, _, k_b, _ = capture_opening(loadout, 0x11E12C8F)
    if pre_a != pre_b or k_a != k_b:
        raise RuntimeError(f'opening depends on the seed (k {k_a}/{k_b}); the kernel cannot model it')
    return pre_a, k_a


def build_kernel():
    """Use a prebuilt kernel if it is newer than the source; otherwise build
    with the first compiler found (gcc, clang; on Windows MSVC ``cl`` must be
    run from a Developer prompt: ``cl /TP /O2 /openmp GakumasCLI\\tools\\seed_solver.c
    /Fe:GakumasCLI\\tools\\seed_solver.exe``)."""
    import shutil
    if KERNEL.exists() and KERNEL.stat().st_mtime >= KERNEL_SRC.stat().st_mtime:
        return KERNEL
    for cc in ('gcc', 'clang'):
        if shutil.which(cc):
            subprocess.run([cc, '-O3', '-march=native', '-fopenmp', str(KERNEL_SRC), '-o', str(KERNEL)], check=True)
            return KERNEL
    if shutil.which('cl'):
        subprocess.run(['cl', '/nologo', '/TP', '/O2', '/openmp', str(KERNEL_SRC), '/Fe:' + str(KERNEL)], **NO_WINDOW,
                       check=True, cwd=str(KERNEL.parent))
        return KERNEL
    raise RuntimeError('no C compiler found; build tools/seed_solver.c by hand (see the docstring)')


def turn_pool(session):
    """The pool the audition draws its turn attributes from, in the engine's
    order (neu.deck.calculate_audition_turn_parameter_types, mirrored), and how
    many turns draw from it (``limit_turn - 3``)."""
    import math
    f32 = _f32
    values = session.deck.audition_parameter_values
    limit = int(session.deck.audition_limit_turn)
    (t0, p0), (t1, p1), (t2, p2) = sorted(values.items(), key=lambda item: -item[1])
    mutable = limit - 3
    total = p0 + p1 + p2
    c0 = math.ceil(f32(f32(f32(mutable) * f32(p0)) / f32(total)))
    remaining = mutable - c0
    c1 = round(f32(f32(f32(remaining) * f32(p1)) / f32(p1 + p2)))
    c2 = remaining - c1
    return [t0] * c0 + [t1] * c1 + [t2] * c2, mutable


def turns_from_seed(pool, mutable, seed):
    """The kernel's prefilter in Python: the first ``mutable`` turn attributes of ``seed``."""
    left, state, out = list(pool), int(seed) & 0xFFFFFFFF, []
    for _ in range(mutable):
        j = (state * len(left)) >> 32
        state ^= (state << 13) & 0xFFFFFFFF
        state ^= state >> 17
        state ^= (state << 5) & 0xFFFFFFFF
        out.append(left.pop(j))
    return out


def run_kernel(k, pre, observed, lo=0, hi=1 << 32, min_len=None, pool=None, turns=None, threads=None):
    ids = {v: i for i, v in enumerate(sorted(set(pre)))}
    missing = [o for o in observed if o not in ids]
    if missing:
        raise ValueError(f'observed cards not in the deck: {missing}')
    args = [str(build_kernel()), str(k), ','.join(str(ids[v]) for v in pre),
            ','.join(str(ids[v]) for v in observed), str(lo), str(hi), str(min_len or len(observed))]
    if pool and turns:
        args += [','.join(str(int(t)) for t in pool), ','.join(str(int(t)) for t in turns)]
    env = None
    if threads:
        env = dict(os.environ, OMP_NUM_THREADS=str(int(threads)))
    out = subprocess.run(args, check=True, capture_output=True, text=True, env=env, **NO_WINDOW).stdout
    rows = [line.split() for line in out.splitlines() if line and not line.startswith('#')]
    tail = [line for line in out.splitlines() if line.startswith('#')]
    return [(int(a), int(b)) for a, b in rows], (tail[-1] if tail else '')


def verify(loadout, seed, observed, turn_types=None):
    """Stage 2: the engine's own opening under ``seed`` puts ``observed`` on top
    (and, when given, shows ``turn_types`` -- all of them, the fixed last three too)."""
    _pre, post, _k, session = capture_opening(loadout, seed)
    if turn_types and list(session.deck.turn_parameter_types[:len(turn_types)]) != [int(t) for t in turn_types]:
        return False
    return post[:len(observed)] == list(observed)


def plan(loadout, observed, turn_types=None):
    """Everything seed-independent the kernel needs."""
    pre, k = prepare(loadout)
    observed = [tuple(o) for o in observed]
    ids = {v: i for i, v in enumerate(sorted(set(pre)))}
    missing = [o for o in observed if o not in ids]
    if missing:
        raise ValueError(f'observed cards not in the deck: {missing}')
    out = {'k': k, 'pre': [ids[v] for v in pre], 'obs': [ids[v] for v in observed], 'deck_in_shuffle': len(pre),
           'pool': None, 'turns': None}
    if turn_types:
        _pre, _post, _k, session = capture_opening(loadout, 1)
        pool, mutable = turn_pool(session)
        if turns_from_seed(pool, mutable, 1) != list(session.deck.turn_parameter_types[:mutable]):
            raise RuntimeError('turn pool mirror disagrees with the engine')
        out['pool'], out['turns'] = pool, [int(t) for t in turn_types[:mutable]]
    return out


def scan(job, threads=1):
    """One range of a ``plan``: the kernel over [lo, hi); returns (seed, matched) hits."""
    args = [str(build_kernel()), str(job['k']), ','.join(map(str, job['pre'])), ','.join(map(str, job['obs'])),
            str(job['lo']), str(job['hi']), str(len(job['obs']))]
    if job.get('pool') and job.get('turns'):
        args += [','.join(map(str, job['pool'])), ','.join(map(str, job['turns']))]
    env = dict(os.environ, OMP_NUM_THREADS=str(int(threads)))
    out = subprocess.run(args, check=True, capture_output=True, text=True, env=env, **NO_WINDOW).stdout
    return [int(line.split()[0]) for line in out.splitlines() if line and not line.startswith('#')]


def solve(loadout, observed, lo=0, hi=1 << 32, turn_types=None):
    work = plan(loadout, observed, turn_types)
    started = time.time()
    hits = scan(dict(work, lo=lo, hi=hi), threads=os.cpu_count() or 1)
    confirmed = [seed for seed in hits if verify(loadout, seed, [tuple(o) for o in observed], turn_types)]
    return {'k': work['k'], 'deck_in_shuffle': work['deck_in_shuffle'], 'observed': len(observed),
            'turns': len(work['turns'] or ()), 'kernel_hits': len(hits), 'confirmed': confirmed,
            'seconds': round(time.time() - started, 1)}


def _rounds(dirs):
    from GakumasCLI.scenario.hif_replay_import import extract, to_loadout
    rows = {}
    for d in dirs:
        for p in sorted(Path(d).glob('ev*ProduceHistory.bin')):
            for r in extract(p.read_bytes()):
                rows[(r['seed'], r['step_type'])] = (r, to_loadout(r))
    return rows


def validate(dirs, window_bits=22, draws=9, full_seed=None, full_all=False):
    """Every recorded round: hide the seed, give the kernel the first ``draws``
    drawn deck cards, search a window around the true seed (the full 2^32 for
    ``full_seed``), and check the true seed is the one survivor."""
    report = []
    for (seed, step), (record, loadout) in sorted(_rounds(dirs).items()):
        pre, post, k, _ = capture_opening(loadout, seed)
        observed = post[:draws]
        if full_seed is not None and seed != full_seed:
            continue
        if full_seed is None and not full_all:
            lo, hi = max(0, seed - (1 << window_bits)), min(1 << 32, seed + (1 << window_bits))
        else:
            lo, hi = 0, 1 << 32
        started = time.time()
        hits, summary = run_kernel(k, pre, observed, lo, hi, min_len=min(6, draws))
        full = [s for s, m in hits if m >= draws]
        row = {'seed': seed, 'idol': record['idol_card_id'][7:11], 'k': k, 'n': len(pre), 'window': [lo, hi],
               'survivors_by_len': {L: sum(1 for _, m in hits if m >= L) for L in range(min(6, draws), draws + 1)},
               'true_seed_found': seed in full, 'others_at_full_len': len([s for s in full if s != seed]),
               'engine_confirms': verify(loadout, seed, observed), 'seconds': round(time.time() - started, 2)}
        report.append(row)
        print(json.dumps(row), flush=True)
    return report


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('solve'); s.add_argument('loadout'); s.add_argument('observed')
    s.add_argument('--lo', type=int, default=0); s.add_argument('--hi', type=int, default=1 << 32)
    s.add_argument('--turns', default=None, help='turn attributes seen at the start, e.g. 3,2,1,3,1,3,2,1,3')
    v = sub.add_parser('validate'); v.add_argument('dirs', nargs='+')
    v.add_argument('--window', type=int, default=22); v.add_argument('--draws', type=int, default=9)
    v.add_argument('--full', type=int, default=None)
    v.add_argument('--full-all', action='store_true', help='search all 2^32 seeds for every round')
    a = ap.parse_args()
    if a.cmd == 'solve':
        loadout = json.loads(Path(a.loadout).read_text(encoding='utf-8'))
        observed = json.loads(Path(a.observed).read_text(encoding='utf-8'))
        turns = [int(t) for t in a.turns.split(',')] if a.turns else None
        print(json.dumps(solve(loadout, observed, a.lo, a.hi, turns), ensure_ascii=False))
    else:
        rep = validate(a.dirs, a.window, a.draws, a.full, a.full_all)
        ok = sum(1 for r in rep if r['true_seed_found'] and r['engine_confirms'])
        print(f'# rounds {len(rep)}  true seed found and engine-confirmed {ok}  '
              f'rounds with another seed at full length {sum(1 for r in rep if r["others_at_full_len"])}')


if __name__ == '__main__':
    main()
