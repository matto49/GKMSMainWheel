# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""the deck value V of one idol card on H.I.F 本戦 Final.

    python -B deck_value.py records HISTORIES.json IDOL OUT.json
    python -B deck_value.py run TEMPLATES.json OUTDIR [--records RECORDS.json] [--games 100000] [--budget 0.5]
        [--max-swaps 10] [--val-seeds 512] [--held 50] [--held-seeds 256] [--local-swaps 4] [--workers W] [--lib PATH]
        [--extra-pool auto|PLAN1|PLAN2|PLAN3] [--extra-upgrade 1]
"""
import argparse
import hashlib
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
import GakumasCLI.produce.produce_value as PV                                   # noqa: E402
from GakumasCLI.produce.deck_eval import SEARCH_SEED, event_of, fixed, seeds_for, spearman, _mean_sd   # noqa: E402

BAR_SPEARMAN = 0.8
BAR_GAIN = 0.02


# ----------------------------------------------------------------------------- decks
def key_of(c):
    k = f"{c['produce_card_id']}+{int(c.get('current_upgrade_count') or 0)}"
    if c.get("customizes"):
        k += "*" + hashlib.blake2b(json.dumps(c["customizes"], sort_keys=True).encode(), digest_size=4).hexdigest()
    return k


def base_of(key):
    return key.split("+")[0]


LEGEND = "#legend"
LEGEND_CAP = 1


def card_rules():
    """(card ids whose ProduceCard row is `noDeckDuplication` (重複不可: one per deck, any upgrade or customize),
    card ids of rarity LEGEND (at most LEGEND_CAP of them in one deck))."""
    nodup, legend = set(), set()
    for line in (PV.MASTER_DIR / "ProduceCard.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            d = r.get("decoded") or r
            if d.get("noDeckDuplication"):
                nodup.add(d["id"])
            if (d.get("rarity") or {}).get("name") == "PRODUCE_CARD_RARITY_LEGEND":
                legend.add(d["id"])
    return nodup, legend


def arm_keys(d, arm, own_keys):
    """The deck one arm of a decks.json row holds, as card keys: vbest_cards; own the template's deck."""
    if arm == "vbest":
        return list(d["vbest_cards"])
    if arm == "local":
        if "local_cards" in d:
            return list(d["local_cards"])
        c = Counter(own_keys)
        c.subtract(d["local_changes"]["out"])
        c.update(d["local_changes"]["in"])
        return sorted(c.elements())
    return list(own_keys)


def support_event_cards():
    """support card id -> the produce cards its events give (master_extra.json, effect ids
    `p_effect-produce_reward-…-produce_card-<card>-<n>`)."""
    d = json.loads((HERE / "master_extra.json").read_text(encoding="utf-8"))["support_events"]
    out = defaultdict(set)
    for sid, events in d.items():
        for e in events:
            for eid in e["effects"]:
                m = re.search(r"produce_card-(p_card-[^-]+-[^-]+-[0-9_]+)-\d+$", eid)
                if m:
                    out[sid].add(m.group(1))
    return out


def extra_cards(tpls, spec, upgrade=1):
    """the offer support set of COMMON and the plan as deck cards (see the docstring); [] without SPEC."""
    if not spec:
        return []
    from GakumasCLI.produce.offer_coverage import _tail, card_set
    cards, rows, idols = card_set()
    plan = spec
    if spec == "auto":
        plan = _tail(idols[tpls[0]["loadout"]["idol_card_id"]]["planType"], "TYPE_")
    out = []
    for cid, v in sorted(cards.items()):
        if "support_set" in v["sources"] and v["plan"] in ("COMMON", plan):
            up = upgrade if (cid, upgrade) in rows else 0
            out.append({"produce_card_id": cid, "current_upgrade_count": up})
    return out


class Space:
    """The templates, the card vocabulary and each template's pool, caps and fixed cards; with records, the records'
    decks as the starting population and their adoption counts as the pool weights; with EXTRA, more cards in the
    vocabulary and the pools (weight 1, one copy)."""

    def __init__(self, tpls, records=None, extra=None):
        self.tpls = tpls
        self.T = len(tpls)
        proto, held, cap = {}, Counter(), Counter()
        decks = [t["loadout"]["deck_cards"] for t in tpls]
        rec_decks = [r["deck_cards"] for r in records or ()]
        for d in decks + rec_decks:
            for c in d:
                proto.setdefault(key_of(c), {k: v for k, v in c.items() if k != "customizes_unknown"})
            for k, v in Counter(key_of(c) for c in d).items():
                cap[k] = max(cap[k], v)
        for c in extra or ():
            k = key_of(c)
            if k not in proto:
                proto[k] = dict(c)
                cap[k] = 1
        for d in rec_decks or decks:
            for k in {key_of(c) for c in d}:
                held[k] += 1
        self.vocab = sorted(proto)
        self.ix = {k: i for i, k in enumerate(self.vocab)}
        self.proto = [proto[k] for k in self.vocab]
        self.cap = [cap[k] for k in self.vocab]
        (nodup, legend), bcap = card_rules(), Counter()
        for d in decks + rec_decks:
            for b, v in Counter(c["produce_card_id"] for c in d).items():
                bcap[b] = max(bcap[b], v)
        for c in extra or ():
            bcap[c["produce_card_id"]] = max(bcap[c["produce_card_id"]], 1)
        self.base = [base_of(k) for k in self.vocab]
        self.bcap = {b: 1 if b in nodup or "-sup-" in b else v for b, v in bcap.items()}
        self.bcap[LEGEND] = LEGEND_CAP
        self.legend = legend
        events = support_event_cards()
        self.own, self.fixed, self.pool, self.weights = [], [], [], []
        for t in tpls:
            own = [self.ix[key_of(c)] for c in t["loadout"]["deck_cards"]]
            grants = {c for s in t["loadout"].get("support_cards") or () for c in events.get(s["support_card_id"], ())}
            pool = [i for i, k in enumerate(self.vocab) if not fixed(k.split("+")[0])
                    and ("-sup-" not in k or k.split("+")[0] in grants)]
            self.own.append(own)
            self.fixed.append([i for i in own if fixed(self.vocab[i].split("+")[0])])
            self.pool.append(pool)
            self.weights.append([max(1, held[self.vocab[i]]) for i in pool])
        self.records = [[self.ix[key_of(c)] for c in d] for d in rec_decks]

    def counts(self, deck):
        """Key counts and card id counts of a deck (LEGEND counts the legend cards)."""
        kc, bc = Counter(deck), Counter(self.base[i] for i in deck)
        bc[LEGEND] = sum(n for b, n in bc.items() if b in self.legend)
        return kc, bc

    def add(self, i, kc, bc, n=1):
        kc[i] += n
        bc[self.base[i]] += n
        if self.base[i] in self.legend:
            bc[LEGEND] += n

    def fits(self, i, kc, bc):
        """Card i can join a deck with counts kc, bc."""
        b = self.base[i]
        return (kc[i] < self.cap[i] and bc[b] < self.bcap.get(b, self.cap[i])
                and (b not in self.legend or bc[LEGEND] < self.bcap[LEGEND]))

    def violations(self, deck):
        """Cards over a cap: 重複不可, a support event card twice, more copies of a card id than one template or
        record deck holds, more legend cards than LEGEND_CAP."""
        _kc, bc = self.counts(deck)
        return sum(max(0, n - self.bcap.get(b, n)) for b, n in bc.items() if not fixed(b))

    def draw(self, deck, n, t, rng):
        """A pool card of template t for position n of the deck that keeps it legal (n's card leaves first)."""
        kc, bc = self.counts(deck)
        self.add(deck[n], kc, bc, -1)
        cand = [(i, w) for i, w in zip(self.pool[t], self.weights[t]) if self.fits(i, kc, bc)]
        return rng.choices([i for i, _ in cand], [w for _, w in cand])[0] if cand else deck[n]

    def on_template(self, deck, t, rng):
        """A record's deck put on template t: t's own fixed cards (idol card, trouble cards), the record's other
        cards, a support event card t's support cards do not grant replaced from t's pool."""
        allowed = set(self.pool[t])
        out = list(self.fixed[t]) + [i for i in deck if not fixed(self.base[i])]
        for n in range(len(self.fixed[t]), len(out)):
            if out[n] not in allowed:
                out[n] = self.draw(out, n, t, rng)
        return out

    def sample(self, rng, max_swaps):
        t = rng.randrange(self.T)
        if self.records and rng.random() < 0.5:
            deck = self.on_template(self.records[rng.randrange(len(self.records))], t, rng)
        else:
            deck = list(self.own[t])
        for _ in range(rng.randint(0, max_swaps)):
            free = [n for n, i in enumerate(deck) if i not in self.fixed[t]]
            if not free:
                break
            n = rng.choice(free)
            deck[n] = self.draw(deck, n, t, rng)
        return t, sorted(deck)


# ----------------------------------------------------------------------------- games
_V = {}


def _init(lib, tpl_path, rec_path=None, extra_spec=None, extra_upgrade=1):
    PV._init(lib)
    tpls = json.loads(Path(tpl_path).read_text(encoding="utf-8"))
    recs = json.loads(Path(rec_path).read_text(encoding="utf-8")) if rec_path else None
    _V["space"] = Space(tpls, recs, extra_cards(tpls, extra_spec, extra_upgrade))
    _V["lo"] = []
    for t in tpls:
        lo = dict(t["loadout"])
        lo.pop("deck_cards")
        _V["lo"].append(lo)


def _game(job):
    sp = _V["space"]
    lo = dict(_V["lo"][job["t"]])
    lo["deck_cards"] = [dict(sp.proto[i]) for i in job["x"]]
    t0 = time.perf_counter()
    score, native, _secs, _r = PV.search_score(lo, job["seed"], job["budget"], SEARCH_SEED)
    return dict(job, score=score, native=native, wall=round(time.perf_counter() - t0, 3))


def _done(path, key):
    out = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.add(key(json.loads(line)))
    return out


def play(jobs, path, key, a):
    done = _done(path, key)
    todo = [j for j in jobs if key(j) not in done]
    print(path.name, "games", len(jobs), "to run", len(todo), flush=True)
    if not todo:
        return
    lib = a.lib or os.environ.get("GKMS_NATIVE_LIB") or str(PV.G / "native" / "build" / "gkmscore.dll")
    t0 = time.monotonic()
    with Pool(a.workers, initializer=_init,
              initargs=(lib, a.templates, a.records, getattr(a, "extra_pool", None), getattr(a, "extra_upgrade", 1))) as pool, path.open("a", encoding="utf-8") as fh:
        for n, r in enumerate(pool.imap_unordered(_game, todo, chunksize=4), 1):
            fh.write(json.dumps(r) + "\n")
            if n % 1000 == 0 or n == len(todo):
                fh.flush()
                print(path.name, n, "/", len(todo), round(time.monotonic() - t0), "s", flush=True)


def train_jobs(sp, a):
    jobs = []
    for i in range(a.games):
        rng = random.Random(f"train|{i}")
        t, x = sp.sample(rng, a.max_swaps)
        jobs.append({"m": "train", "i": i, "t": t, "x": x, "seed": rng.randrange(1, 2 ** 31 - 1), "budget": a.budget})
    return jobs


# ----------------------------------------------------------------------------- fit
def design(sp, rows):
    import numpy as np
    X = np.zeros((len(rows), len(sp.vocab)))
    for n, r in enumerate(rows):
        for i in r["x"]:
            X[n, i] += 1
    y = np.log(np.maximum(1.0, np.array([r["score"] for r in rows], dtype=float)))
    t = np.array([r["t"] for r in rows])
    return X, y, t


def _within(X, y, t, T):
    """Remove each template's mean (the unpenalised template effect)."""
    import numpy as np
    Xw, yw = X.copy(), y.copy()
    mx, my = np.zeros((T, X.shape[1])), np.zeros(T)
    for k in range(T):
        m = t == k
        if m.any():
            mx[k], my[k] = X[m].mean(0), y[m].mean()
            Xw[m] -= mx[k]
            yw[m] -= my[k]
    return Xw, yw, mx, my


def fit(sp, rows, lambdas=(0.1, 1.0, 10.0, 100.0, 1000.0)):
    import numpy as np
    X, y, t = design(sp, rows)
    used = np.where(X.sum(0) > 0)[0]
    Xu = X[:, used]
    fold = np.array([random.Random(f"fold|{r['i']}").randrange(5) for r in rows])

    def solve(Xa, ya, ta, lam):
        Xw, yw, mx, my = _within(Xa, ya, ta, sp.T)
        A = Xw.T @ Xw + lam * np.eye(Xw.shape[1])
        w = np.linalg.solve(A, Xw.T @ yw)
        b = my - mx @ w
        return w, b, A, Xw, yw

    cv = {}
    for lam in lambdas:
        err = 0.0
        for f in range(5):
            tr, te = fold != f, fold == f
            w, b, *_ = solve(Xu[tr], y[tr], t[tr], lam)
            err += float(((y[te] - b[t[te]] - Xu[te] @ w) ** 2).sum())
        cv[lam] = err / len(rows)
    lam = min(cv, key=cv.get)
    w, b, A, Xw, yw = solve(Xu, y, t, lam)
    resid = yw - Xw @ w
    sigma2 = float((resid ** 2).sum() / max(1, len(rows) - len(used) - sp.T))
    Ainv = np.linalg.inv(A)
    se = np.sqrt(np.maximum(0.0, np.diag(Ainv @ (Xw.T @ Xw) @ Ainv) * sigma2))
    var_y = float(((y - y.mean()) ** 2).mean())
    W = np.zeros(len(sp.vocab))
    S = np.full(len(sp.vocab), np.nan)
    W[used], S[used] = w, se
    return {"lambda": lam, "cv_mse": {str(k): round(v, 6) for k, v in cv.items()}, "games": len(rows),
            "r2_cv": round(1 - cv[lam] / var_y, 4), "sigma": round(math.sqrt(sigma2), 4),
            "template_effect": [round(float(v), 6) for v in b],
            "weights": {sp.vocab[i]: {"w": round(float(W[i]), 6), "se": None if math.isnan(S[i]) else round(float(S[i]), 6),
                                      "n_games": int((X[:, i] > 0).sum())} for i in range(len(sp.vocab))}}


# ----------------------------------------------------------------------------- decks
def optimise(sp, model, local_swaps):
    w = [model["weights"][k]["w"] for k in sp.vocab]
    out = []
    for t in range(sp.T):
        own = list(sp.own[t])
        size = len(own)
        deck = list(sp.fixed[t])
        kc, bc = sp.counts(deck)
        for i in sorted(sp.pool[t], key=lambda i: -w[i]):
            while len(deck) < size and sp.fits(i, kc, bc):
                deck.append(i)
                sp.add(i, kc, bc)
        local = list(own)
        for _ in range(local_swaps):
            best = None
            for n, o in enumerate(local):
                if o in sp.fixed[t]:
                    continue
                kc, bc = sp.counts(local)
                sp.add(o, kc, bc, -1)
                for i in sp.pool[t]:
                    if i != o and sp.fits(i, kc, bc) and (best is None or w[i] - w[o] > best[0]):
                        best = (w[i] - w[o], n, i)
            if best is None or best[0] <= 0:
                break
            local[best[1]] = best[2]
        val = lambda d: sum(w[i] for i in d)                 # noqa: E731
        out.append({"template": sp.tpls[t]["key"], "own": sorted(own), "vbest": sorted(deck), "local": sorted(local),
                    "pred_gain_vbest": round(math.exp(val(deck) - val(own)) - 1, 4),
                    "pred_gain_local": round(math.exp(val(local) - val(own)) - 1, 4),
                    "vbest_cards": [sp.vocab[i] for i in sorted(deck)],
                    "local_cards": [sp.vocab[i] for i in sorted(local)],
                    "local_changes": {"out": sorted(sp.vocab[i] for i in (Counter(own) - Counter(local)).elements()),
                                      "in": sorted(sp.vocab[i] for i in (Counter(local) - Counter(own)).elements())},
                    "short": size - len(deck),
                    "illegal": {"own": sp.violations(own), "vbest": sp.violations(deck), "local": sp.violations(local)}})
    return out


def val_jobs(sp, decks, a):
    jobs = []
    seeds = seeds_for("val", a.val_seeds)
    for j, s in enumerate(seeds):
        t = j % sp.T
        for arm in ("own", "vbest", "local"):
            jobs.append({"m": arm, "i": j, "t": t, "x": decks[t][arm], "seed": s, "budget": a.budget})
    for h in range(a.held):
        rng = random.Random(f"held|{h}")
        t, x = sp.sample(rng, a.max_swaps)
        for j, s in enumerate(seeds_for(f"held|{h}", a.held_seeds)):
            jobs.append({"m": "held", "i": h * 100000 + j, "h": h, "t": t, "x": x, "seed": s, "budget": a.budget})
    return jobs


def verdict(sp, model, rows):
    w = {k: v["w"] for k, v in model["weights"].items()}
    b = model["template_effect"]
    res = {"python_fallback": sum(1 for r in rows if not r["native"])}
    held = defaultdict(list)
    for r in rows:
        if r["m"] == "held":
            held[r["h"]].append(r)
    pred, obs = [], []
    for h, rs in sorted(held.items()):
        x = rs[0]["x"]
        pred.append(sum(w[sp.vocab[i]] for i in x))
        obs.append(sum(math.log(max(1, r["score"])) for r in rs) / len(rs) - b[rs[0]["t"]])
    res["held"] = {"decks": len(pred), "seeds_per_deck": min((len(v) for v in held.values()), default=0),
                   "spearman": spearman(pred, obs), "bar": BAR_SPEARMAN}
    res["held"]["pass"] = bool(res["held"]["spearman"] is not None and res["held"]["spearman"] >= BAR_SPEARMAN)
    by = defaultdict(dict)
    for r in rows:
        if r["m"] in ("own", "vbest", "local"):
            by[r["i"]][r["m"]] = r
    for arm in ("vbest", "local"):
        ratios, per_t = [], defaultdict(list)
        for i, d in by.items():
            if arm in d and "own" in d and d["own"]["score"] > 0:
                q = d[arm]["score"] / d["own"]["score"] - 1
                ratios.append(q)
                per_t[d["own"]["t"]].append(q)
        if len(ratios) > 1:
            m, sd = _mean_sd(ratios)
            ci = 1.96 * sd / math.sqrt(len(ratios))
            res[arm + "/own"] = {"pairs": len(ratios), "mean": round(m, 4), "ci95": [round(m - ci, 4), round(m + ci, 4)],
                                 "per_template": {sp.tpls[t]["key"]: round(sum(v) / len(v), 4) for t, v in sorted(per_t.items())}}
    v = res.get("vbest/own")
    res["vbest_pass"] = bool(v and v["mean"] >= BAR_GAIN and v["ci95"][0] > 0)
    res["bars"] = {"spearman": BAR_SPEARMAN, "vbest_gain": BAR_GAIN, "vbest_ci_low": 0}
    return res


# ----------------------------------------------------------------------------- run
def records(histories, idol, out):
    rows, left_out = [], []
    for r in json.loads(Path(histories).read_text(encoding="utf-8")):
        if r.get("idol_card_id") != idol:
            continue
        ev = event_of(r["cleared_time_ms"]) if r.get("cleared_time_ms") else None
        if ev:
            left_out.append((r.get("capture"), ev))
            continue
        deck = []
        for c in r.get("produce_cards") or ():
            pc = c.get("produce_card") or {}
            e = {"produce_card_id": pc["id"], "current_upgrade_count": int(pc.get("upgrade_count") or 0)}
            if pc.get("customizes"):
                e["customizes"] = [{"id": x["id"], "customize_count": int(x.get("customize_count") or 1)}
                                   for x in pc["customizes"]]
            deck.append(e)
        rows.append({"source": r.get("capture"), "produce_id": r.get("produce_id"), "score": r.get("score"),
                     "audition_scores": [a.get("score") for a in r.get("auditions") or ()],
                     "support_card_ids": [s.get("id") for s in r.get("support_cards") or ()], "deck_cards": deck})
    Path(out).write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(idol, "records", len(rows), "deck sizes", sorted(Counter(len(r["deck_cards"]) for r in rows).items()),
          "left out (event weeks)", left_out)


def run(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tpls = json.loads(Path(a.templates).read_text(encoding="utf-8"))
    recs = json.loads(Path(a.records).read_text(encoding="utf-8")) if a.records else None
    sp = Space(tpls, recs, extra_cards(tpls, a.extra_pool, a.extra_upgrade))
    print("templates", sp.T, "extra pool", a.extra_pool, "records", len(sp.records), "vocabulary", len(sp.vocab), "pool sizes",
          [len(p) for p in sp.pool], flush=True)
    t0 = time.monotonic()
    play(train_jobs(sp, a), out / "train.jsonl", lambda r: (r["m"], r["i"]), a)
    times = {"train_s": round(time.monotonic() - t0)}
    rows = [json.loads(line) for line in (out / "train.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    t1 = time.monotonic()
    model = fit(sp, rows)
    times["fit_s"] = round(time.monotonic() - t1)
    (out / "model.json").write_text(json.dumps(model, ensure_ascii=False, indent=0), encoding="utf-8")
    decks = optimise(sp, model, a.local_swaps)
    (out / "decks.json").write_text(json.dumps(decks, ensure_ascii=False, indent=0), encoding="utf-8")
    t2 = time.monotonic()
    play(val_jobs(sp, decks, a), out / "val.jsonl", lambda r: (r["m"], r["i"]), a)
    times["val_s"] = round(time.monotonic() - t2)
    vrows = [json.loads(line) for line in (out / "val.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    res = verdict(sp, model, vrows)
    walls = [r["wall"] for r in rows]
    top = sorted(((k, v) for k, v in model["weights"].items() if v["n_games"]), key=lambda kv: -kv[1]["w"])
    res.update(idol=tpls[0]["loadout"].get("idol_card_id"), templates=sp.T, records=len(sp.records),
               vocabulary=len(sp.vocab),
               illegal_decks={arm: sum(1 for d in decks if d["illegal"][arm]) for arm in ("own", "vbest", "local")},
               short_decks=sum(1 for d in decks if d["short"]),
               train_games=len(rows), train_python_fallback=sum(1 for r in rows if not r["native"]),
               wall_per_game=round(sum(walls) / max(1, len(walls)), 3), times=times,
               model={k: model[k] for k in ("lambda", "r2_cv", "sigma", "games")},
               top_cards=[[k, v["w"], v["se"]] for k, v in top[:10]],
               bottom_cards=[[k, v["w"], v["se"]] for k, v in top[-5:]],
               pred_gain_vbest={d["template"]: d["pred_gain_vbest"] for d in decks},
               args={k: getattr(a, k) for k in ("games", "budget", "max_swaps", "val_seeds", "held", "held_seeds",
                                                 "local_swaps", "workers", "extra_pool", "extra_upgrade")})
    text = json.dumps(res, ensure_ascii=False, indent=1)
    (out / "verdict.json").write_text(text, encoding="utf-8")
    print(text, flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("records")
    p.add_argument("histories")
    p.add_argument("idol")
    p.add_argument("out")
    p = sub.add_parser("run")
    p.add_argument("templates")
    p.add_argument("out")
    p.add_argument("--records")
    p.add_argument("--games", type=int, default=100000)
    p.add_argument("--budget", type=float, default=0.5)
    p.add_argument("--max-swaps", type=int, default=10)
    p.add_argument("--val-seeds", type=int, default=512)
    p.add_argument("--held", type=int, default=50)
    p.add_argument("--held-seeds", type=int, default=256)
    p.add_argument("--local-swaps", type=int, default=4)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--lib")
    p.add_argument("--extra-pool", help="auto | PLAN1 | PLAN2 | PLAN3 -- the plan's offer support set joins the vocabulary")
    p.add_argument("--extra-upgrade", type=int, default=1)
    a = ap.parse_args()
    if a.cmd == "records":
        records(a.histories, a.idol, a.out)
        return 0
    a.templates = str(Path(a.templates).resolve())
    a.out = str(Path(a.out).resolve())
    a.records = str(Path(a.records).resolve()) if a.records else None
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
