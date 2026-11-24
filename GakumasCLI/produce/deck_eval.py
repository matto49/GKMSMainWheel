# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""deck evaluation for one idol card on H.I.F 本戦 Final.

    python -B deck_eval.py templates IDOL_CARD_ID OUT.json [--histories HISTORIES.json] [--loadouts ROOT] [--step 18|16]
    python -B deck_eval.py measure TEMPLATES.json OUT.jsonl [--template I] [--budgets 0.5,1,2]
        [--m1-seeds 32] [--decks 16] [--deck-seeds 16] [--swaps 4] [--pairs 12] [--pair-seeds 24]
        [--pair-budget 1] [--workers W] [--lib PATH]
    python -B deck_eval.py report OUT.jsonl [--json OUT.json]
"""
import argparse
import json
import math
import os
import random
import re
import sys
import time
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
import GakumasCLI.produce.produce_value as PV                                   # noqa: E402

FINAL_STEP_TYPE = 18
SEARCH_SEED = 182


EVENT_WINDOWS = [
    ("あさり先生のプロデュースゼミ 第6回", "2026-04-20 11:00", "2026-04-29 10:59"),
    ("アイドル強化月間（美鈴・広）", "2026-04-29 11:00", "2026-05-10 04:59"),
    ("アイドル強化月間（麻央・莉波）", "2026-06-15 11:00", "2026-06-26 04:59"),
    ("アイドル強化月間（燕・星南）", "2026-07-31 11:00", "2026-08-10 04:59"),
    ("あさり先生のプロデュースゼミ 第7回", "2026-08-26 11:00", "2026-09-04 10:59"),
    ("あさり先生のプロデュースゼミ 第8回", "2026-09-20 11:00", "2026-09-30 10:59"),
    ("あさり先生のプロデュースゼミ 第9回", "2026-10-09 11:00", "2026-10-20 10:59"),
    ("十王邦夫のアイドル強化月間 改", "2026-10-20 11:00", "2026-10-28 04:59"),
]
EVENT_GRACE_S = 6 * 3600


def event_of(cleared_ms):
    """The event window a produce cleared at CLEARED_MS (epoch ms) belongs to, or None."""
    from datetime import datetime, timedelta, timezone
    jst = timezone(timedelta(hours=9))
    t = datetime.fromtimestamp(cleared_ms / 1000, jst)
    for name, a, b in EVENT_WINDOWS:
        start = datetime.strptime(a, "%Y-%m-%d %H:%M").replace(tzinfo=jst)
        end = datetime.strptime(b, "%Y-%m-%d %H:%M").replace(tzinfo=jst) + timedelta(seconds=EVENT_GRACE_S)
        if start <= t <= end:
            return name
    return None


# ----------------------------------------------------------------------------- templates
def own_templates(idol, root, step=FINAL_STEP_TYPE):
    """the player's own 本戦 rounds of IDOL from workbench loadouts under ROOT/<run>/hif/ (see the docstring)."""
    import glob
    from datetime import datetime, timedelta, timezone
    name = {FINAL_STEP_TYPE: "AuditionFinal", 16: "AuditionMid1"}[step]
    res, left_out, seen = [], [], set()
    for path in sorted(glob.glob(os.path.join(root, "*", "hif", f"loadout_{name}_*.json"))):
        run = Path(path).parent.parent.name
        lo = json.loads(Path(path).read_text(encoding="utf-8"))
        if lo.get("idol_card_id") != idol or lo.get("produce_id") != "produce-008" or lo.get("seed") in seen:
            continue
        seen.add(lo.get("seed"))
        m = re.match(r"tl\d+-(\d{8}-\d{6})", run)
        when = datetime.strptime(m.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=timezone(timedelta(hours=8))) if m else None
        ev = event_of(when.timestamp() * 1000) if when else None
        if ev:
            left_out.append((run, ev))
            continue
        res.append({"key": f"{lo['seed']}_{step}", "seed": lo["seed"], "record": run, "source": "own",
                    "recorded_score": None, "deck_size": len(lo["deck_cards"]), "loadout": lo})
    return res, left_out


def templates(idol, out, step=FINAL_STEP_TYPE, histories=None, loadouts=None):
    if not histories:
        res, left_out = own_templates(idol, loadouts, step)
        Path(out).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
        print(idol, "step type", step, "own rounds", len(res), "deck sizes",
              sorted(Counter(t["deck_size"] for t in res).items()), "left out (event weeks)", left_out)
        return
    raise SystemExit("this evaluation replays recorded captures, which this repository does not carry")
    import GakumasCLI.paths as gkms_paths
    os.environ.setdefault("GKMS_CORPUS_ROOT", str(gkms_paths.CORPUS_ROOT))
    import GakumasCLI.produce.hif_histories as HH
    hist = json.loads(Path(histories).read_text(encoding="utf-8"))
    cleared = {r["capture"]: r.get("cleared_time_ms") for r in hist}
    flagged = {r["capture"]: r["event_mode"] for r in hist if r.get("event_mode")}
    again = {c for r in hist for c in r.get("also") or ()}
    rows, left_out = {}, []
    for cap, data in HH.captures(os.environ["GKMS_CORPUS_ROOT"]):
        if cap in again:
            continue
        if cap not in cleared:
            raise SystemExit(f"{cap}: not in {histories} (its cleared time decides the event weeks)")
        ev = flagged.get(cap) or (event_of(cleared[cap]) if cleared[cap] else None)
        for r in M.extract(data):
            if ev:
                if r["idol_card_id"] == idol and r["step_type"] == step:
                    left_out.append((cap, ev))
                continue
            r["record"] = cap
            rows[(r["seed"], r["step_type"])] = r
    res = []
    for (seed, step_type), r in sorted(rows.items()):
        if r["idol_card_id"] != idol or step_type != step:
            continue
        lo = M.to_loadout(r)
        res.append({"key": f"{seed}_{step_type}", "seed": seed, "record": r["record"], "source": "record",
                    "recorded_score": r.get("recorded_score"), "deck_size": len(lo["deck_cards"]), "loadout": lo})
    if loadouts:
        own, own_out = own_templates(idol, loadouts, step)
        res += own
        left_out += own_out
    Path(out).write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    print(idol, "step type", step, "rounds", len(res), "deck sizes", sorted(Counter(t["deck_size"] for t in res).items()),
          "left out (event weeks)", left_out)


def card_key(c):
    return (c["produce_card_id"], int(c.get("current_upgrade_count") or 0),
            json.dumps(c.get("customizes"), sort_keys=True) if c.get("customizes") else "")


def pool_of(tpls):
    """The swap pool: every card instance of the templates' decks, weighted by how many decks hold it; the idol's
    own cards (`-ido-`) and trouble cards (`-acc-0`) never leave or enter a deck."""
    n = Counter()
    proto = {}
    for t in tpls:
        for k in {card_key(c) for c in t["loadout"]["deck_cards"]}:
            n[k] += 1
        for c in t["loadout"]["deck_cards"]:
            proto.setdefault(card_key(c), c)
    keep = [k for k in sorted(n) if not fixed(k[0])]
    return [proto[k] for k in keep], [n[k] for k in keep]


def fixed(card_id):
    return "-ido-" in card_id or "-acc-0" in card_id


def swapped(deck, pool, weights, rng, swaps):
    deck = [dict(c) for c in deck]
    for _ in range(swaps):
        free = [i for i, c in enumerate(deck) if not fixed(c["produce_card_id"])]
        if not free:
            break
        i = rng.choice(free)
        deck[i] = dict(rng.choices(pool, weights)[0])
    return deck


def seeds_for(name, n):
    rng = random.Random(name)
    return [rng.randrange(1, 2 ** 31 - 1) for _ in range(n)]


# ----------------------------------------------------------------------------- games
def _game(job):
    lo = json.loads(job["loadout"])
    lo["deck_cards"] = job["deck_cards"]
    t0 = time.perf_counter()
    score, native, secs, rollouts = PV.search_score(lo, job["seed"], job["budget"], SEARCH_SEED)
    wall = round(time.perf_counter() - t0, 3)
    out = {k: job[k] for k in ("m", "deck", "seed", "budget")}
    out.update(score=score, native=native, seconds=secs, wall=wall, rollouts=rollouts,
               cards=[[c["produce_card_id"], int(c.get("current_upgrade_count") or 0)] for c in job["deck_cards"]])
    return out


def jobs_for(a):
    tpls = json.loads(Path(a.templates).read_text(encoding="utf-8"))
    t = tpls[a.template]
    pool, weights = pool_of(tpls)
    lo = dict(t["loadout"])
    base = lo.pop("deck_cards")
    lo_s = json.dumps(lo, ensure_ascii=False)
    budgets = [float(x) for x in a.budgets.split(",")]
    rng = random.Random(f"{t['key']}|decks")
    decks = [base] + [swapped(base, pool, weights, rng, a.swaps) for _ in range(a.decks - 1)]
    rng = random.Random(f"{t['key']}|pairs")
    if a.pair_kind == "add":
        pairs = [base] + [base + [dict(rng.choices(pool, weights)[0])] for _ in range(a.pairs)]
    else:
        pairs = [base] + [swapped(base, pool, weights, rng, 1) for _ in range(a.pairs)]
    jobs = []

    def add(m, deck_i, deck, seed, budget):
        jobs.append({"m": m, "deck": deck_i, "seed": seed, "budget": budget, "deck_cards": deck, "loadout": lo_s})
    for b in budgets:
        for s in seeds_for(f"{t['key']}|m1", a.m1_seeds):
            add("m1", 0, base, s, b)
        for i, d in enumerate(decks):
            for s in seeds_for(f"{t['key']}|m2", a.deck_seeds):
                add("m2", i, d, s, b)
    for i, d in enumerate(pairs):
        for s in seeds_for(f"{t['key']}|m3", a.pair_seeds):
            add("m3", i, d, s, a.pair_budget)
    return jobs


def measure(a):
    jobs = jobs_for(a)
    out = Path(a.out)
    done = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["m"], r["deck"], r["seed"], r["budget"]))
    todo = [j for j in jobs if (j["m"], j["deck"], j["seed"], j["budget"]) not in done]
    print("games", len(jobs), "to run", len(todo), flush=True)
    lib = a.lib or os.environ.get("GKMS_NATIVE_LIB") or str(PV.G / "native" / "build" / "gkmscore.dll")
    with Pool(a.workers, initializer=PV._init, initargs=(lib,)) as pool, out.open("a", encoding="utf-8") as fh:
        for n, r in enumerate(pool.imap_unordered(_game, todo, chunksize=1), 1):
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            if n % 50 == 0:
                print(n, "/", len(todo), flush=True)
    return 0


# ----------------------------------------------------------------------------- report
def _mean_sd(xs):
    n = len(xs)
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    return m, sd


def _ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(x, y):
    if len(x) < 3:
        return None
    rx, ry = _ranks(x), _ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return round(num / den, 3) if den else None


def report(path, json_out=None):
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    res = {"games": len(rows), "python_fallback": sum(1 for r in rows if not r["native"])}
    m1 = defaultdict(list)
    for r in rows:
        if r["m"] == "m1":
            m1[r["budget"]].append(r)
    res["m1"] = {}
    for b, rs in sorted(m1.items()):
        m, sd = _mean_sd([r["score"] for r in rs])
        res["m1"][b] = {"n": len(rs), "mean": round(m), "sd": round(sd), "cv": round(sd / m, 4),
                        "search_seconds": round(sum(r["seconds"] for r in rs) / len(rs), 3),
                        "wall_seconds": round(sum(r.get("wall", r["seconds"]) for r in rs) / len(rs), 3),
                        "rollouts": round(sum(r["rollouts"] or 0 for r in rs) / len(rs))}
    m2 = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        if r["m"] == "m2":
            m2[r["budget"]][r["deck"]][r["seed"]] = r["score"]
    res["m2"] = {}
    budgets = sorted(m2)
    if budgets:
        top = budgets[-1]
        means = {b: {d: sum(v.values()) / len(v) for d, v in m2[b].items()} for b in budgets}
        decks = sorted(set.intersection(*[set(means[b]) for b in budgets]))
        for b in budgets:
            seeds = sorted({s for v in m2[b].values() for s in v})
            half = [s for i, s in enumerate(seeds) if i % 2 == 0], [s for i, s in enumerate(seeds) if i % 2 == 1]
            h = [[sum(m2[b][d][s] for s in hs if s in m2[b][d]) / max(1, sum(1 for s in hs if s in m2[b][d]))
                  for d in decks] for hs in half]
            res["m2"][b] = {"decks": len(decks), "seeds": len(seeds),
                            "spearman_vs_" + str(top): spearman([means[b][d] for d in decks],
                                                                [means[top][d] for d in decks]),
                            "spearman_seed_halves": spearman(h[0], h[1]),
                            "deck_mean_spread_cv": round(_mean_sd([means[b][d] for d in decks])[1]
                                                         / _mean_sd([means[b][d] for d in decks])[0], 4)}
    m3 = defaultdict(dict)
    for r in rows:
        if r["m"] == "m3" and not r.get("coupled"):
            m3[r["deck"]][r["seed"]] = r["score"]
    base = m3.get(0, {})
    sds, means = [], []
    for d, v in sorted(m3.items()):
        diffs = [v[s] - base[s] for s in v if s in base]
        if d and len(diffs) > 1:
            m, sd = _mean_sd(diffs)
            sds.append(sd)
            means.append(m)
    if sds and len(base) > 1:
        rms = math.sqrt(sum(x * x for x in sds) / len(sds))
        single = _mean_sd(list(base.values()))[1]
        res["m3"] = {"pairs": len(sds), "paired_diff_sd_rms": round(rms), "base_sd": round(single),
                     "ratio_to_independent": round(rms / (math.sqrt(2) * single), 3),
                     "mean_diffs": [round(x) for x in means]}
    res["ridge"] = ridge([r for r in rows if r["m"] == "m2" and budgets and r["budget"] == budgets[-1]])
    text = json.dumps(res, indent=1, ensure_ascii=False)
    print(text)
    if json_out:
        Path(json_out).write_text(text, encoding="utf-8")


def ridge(rows, lam=1.0):
    """score ~ card counts (card id + upgrade), ridge; in-sample R^2 and the largest coefficients."""
    if len(rows) < 10:
        return None
    import numpy as np
    feats = sorted({f"{c}+{u}" for r in rows for c, u in r["cards"]})
    ix = {f: i for i, f in enumerate(feats)}
    X = np.zeros((len(rows), len(feats)))
    for n, r in enumerate(rows):
        for c, u in r["cards"]:
            X[n, ix[f"{c}+{u}"]] += 1
    y = np.array([r["score"] for r in rows], dtype=float)
    Xc, yc = X - X.mean(0), y - y.mean()
    w = np.linalg.solve(Xc.T @ Xc + lam * np.eye(len(feats)), Xc.T @ yc)
    r2 = 1 - float(((yc - Xc @ w) ** 2).sum() / (yc ** 2).sum())
    top = sorted(zip(feats, w), key=lambda t: -abs(t[1]))[:8]
    return {"games": len(rows), "features": len(feats), "r2_in_sample": round(r2, 3),
            "top": [[f, round(float(v))] for f, v in top]}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("templates")
    p.add_argument("idol")
    p.add_argument("out")
    p.add_argument("--step", type=int, default=FINAL_STEP_TYPE, help="step type: 18 本戦 Final (default), 16 本戦 Mid1")
    p.add_argument("--histories", help="hif_histories.py decode's OUT: each capture's cleared time (event weeks)")
    p.add_argument("--loadouts", help="a tree of exported runs (<run>/hif/loadout_*.json): the player's own rounds too")
    p = sub.add_parser("measure")
    p.add_argument("templates")
    p.add_argument("out")
    p.add_argument("--template", type=int, default=0)
    p.add_argument("--budgets", default="0.5,1,2")
    p.add_argument("--m1-seeds", type=int, default=32)
    p.add_argument("--decks", type=int, default=16)
    p.add_argument("--deck-seeds", type=int, default=16)
    p.add_argument("--swaps", type=int, default=4)
    p.add_argument("--pairs", type=int, default=12)
    p.add_argument("--pair-seeds", type=int, default=24)
    p.add_argument("--pair-budget", type=float, default=1.0)
    p.add_argument("--pair-kind", choices=("swap", "add"), default="swap")
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--lib")
    p = sub.add_parser("report")
    p.add_argument("out")
    p.add_argument("--json")
    a = ap.parse_args()
    if a.cmd == "templates":
        if not (a.histories or a.loadouts):
            ap.error("templates: --histories or --loadouts")
        templates(a.idol, str(Path(a.out).resolve()), a.step, a.histories, a.loadouts)
    elif a.cmd == "measure":
        a.templates = str(Path(a.templates).resolve())
        a.out = str(Path(a.out).resolve())
        return measure(a)
    else:
        report(a.out, a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
