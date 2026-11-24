# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""the 日程 simulated from the produce the player is in, instead of from the schedules of earlier recorded produces.

    python -B -m GKMSPS.ps.forecast FRAMES.jsonl[.gz] [--at STEP] [--rollouts N] [--transitions T] [--jobs J]
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import time

from GKMSPS.ps.data import ATTRS, data, name
from GKMSPS.ps.rng import Rng
from GKMSPS.ps.server_state import LiveServer, formation_of, import_state, main_sim
from GKMSPS.ps.sim import MAIN, SELECTION, Sim

FORMAT = "gkms.ps.forecast.v1"
STRONG = "dance"
TRANSITIONS = None
SEARCH_TRANSITIONS = 120_000
FULL_MAIN = {"mid1": 1_400_000, "final": 2_400_000}
ROLLOUTS = 12
OBJECTIVE = "full"
LOW_STAMINA_PCT = 40
ROOT = b"GKMS-forecast"
SHORT = {"vocal": "Vo", "dance": "Da", "visual": "Vi"}


# -- taking the produce over ----------------------------------------------------------------------------------------
def at_home(produce):
    """The player is between two steps of an H.I.F produce: nothing pending, no shop or interval open, not ended."""
    if produce is None or getattr(produce, "ended", False):
        return False
    if (produce.progress or {}).get("produce_id") not in (SELECTION, MAIN):
        return False
    visit = getattr(produce, "visit", None)
    return not produce.effects and not (visit and visit not in getattr(produce, "visit_closed", ()))


def formation(sv):
    """The formation of the produce ``sv`` shows (the account's growth panels at their maximum: a produce followed
    from its middle has no Start to read them from)."""
    p = sv.p
    upd = {"user_produce_progress": p,
           "user_produce_progress_support_card_list": list(sv.lists["user_produce_progress_support_card_list"].values()),
           "user_produce_progress_memory_list": list(sv.lists["user_produce_progress_memory_list"].values())}
    f = formation_of(data(), upd, {})
    if len(f["supports"]) != 6:
        raise LookupError(f"面板只看到这局 {len(f['supports'])} 张支援卡（培育中途打开的面板读不到全部 6 张），模拟不了")
    f["account"].pop("growth_panel", None)
    f["idol"]["potential_rank"] = int(p.get("idol_card_potential_rank") or 0)
    f["idol"]["prima_stella"] = bool(p.get("is_prima_stella"))
    return f


def from_server(sv, scores=None, root=None):
    """A simulator at the next step's first decision of the produce ``sv`` shows at home."""
    root = root or hashlib.sha256(ROOT).digest()
    p = sv.p
    f = formation(sv)
    sim = main_sim(f, sv, root=root.hex()) if p.get("produce_id") == MAIN else Sim(f, root=root.hex())
    import_state(sim, sv)
    s = sim.s
    s["step"] = int(p.get("step_number") or 0)
    s["done"] = False
    s.pop("result", None)
    if p.get("produce_id") == MAIN and (scores or {}).get(7) is not None:
        s["auditions"]["mid1"] = {"score": int(scores[7]), "rank": 1, "threshold": 1, "npcs": [], "try": 1,
                                  "parts": [0, 0, 0], "star_before": s["star"], "seed": 0}
    s["queue"] = [{"t": "next_step"}]
    sim.exam = None
    sim._run()
    return sim


def from_produce(produce, root=None):
    return from_server(LiveServer(produce), getattr(produce, "scores", None), root)


# -- the policy -------------------------------------------------------------------------------------------------------
def first_attr(sim):
    """The idol's first attribute: the highest line of her final audition's 審査基準 (ProduceExamBattleConfig)."""
    s, d = sim.s, sim.d
    row = d.difficulty(s["idol"], s["produce_id"], "AUDITION_FINAL")
    cfg = d.battle.get(row.get("produceExamBattleConfigId")) or {}
    return max(ATTRS, key=lambda a: (int(cfg.get(a) or 0), -ATTRS.index(a)))


def form_attr(sim, form, strong=STRONG):
    return first_attr(sim) if form == "normal" else strong


def full_score(sim, t):
    """(score, [Vo, Da, Vi]) of the exam task ``t`` played to its full scores (TRANSITIONS)."""
    from GKMSPS.ps.sim import SELECTION_STAR_LINE, STEP_TAIL
    s, d = sim.s, sim.d
    row = d.difficulty(s["idol"], s["produce_id"], STEP_TAIL[t["stage"]])
    cfg = d.battle.get(row.get("produceExamBattleConfigId")) or {}
    score = FULL_MAIN[t["stage"]] if s["produce_id"] == MAIN else SELECTION_STAR_LINE[t["stage"]]
    score = max(score, max([int(x) for x in t.get("npcs") or ()] + [0]) + 1)
    w = [max(1, int(cfg.get(a) or 0)) for a in ATTRS]
    parts = [score * x // sum(w) for x in w]
    parts[0] += score - sum(parts)
    return score, parts


def attr_order(sim):
    """The idol's attributes by her final audition's 審査基準, highest first."""
    s, d = sim.s, sim.d
    row = d.difficulty(s["idol"], s["produce_id"], "AUDITION_FINAL")
    cfg = d.battle.get(row.get("produceExamBattleConfigId")) or {}
    return sorted(ATTRS, key=lambda a: (-int(cfg.get(a) or 0), ATTRS.index(a)))


CAP_MARGIN = {SELECTION: 600, MAIN: 200}
RATING_CAP = 3200
FREE_ORDER = ("present", "shop", "customize", "activity")


class Policy:
    def __init__(self, attr, transitions=TRANSITIONS):
        self.attr, self.transitions = attr, (int(transitions) if transitions else None)
        self._order = None

    def train(self, sim):
        """The attribute to train now: of the form's attribute and the next of the idol's order, the lower one not yet
        within CAP_MARGIN of the rating's 3,200; once both are, the third.
        """
        if self._order is None:
            self._order = [self.attr] + [a for a in attr_order(sim) if a != self.attr]
        s = sim.s
        line = RATING_CAP - CAP_MARGIN.get(s["produce_id"], 0)
        two = [a for a in self._order[:2] if s["params"][a] < line]
        if two:
            return min(two, key=lambda a: (s["params"][a], self._order.index(a)))
        return next((a for a in self._order if s["params"][a] < line), self.attr)

    def day(self, sim, opts, ok, pol):
        s = sim.s
        attr = self.train(sim)
        rest = [j for j in ok if opts[j].get("do") == "refresh"]
        if any(o.get("do") == "lesson" for o in opts):
            sp = [j for j in ok if opts[j].get("do") == "lesson" and "（SP）" in opts[j]["label"]
                  and s["params"][opts[j]["attr"]] < RATING_CAP - CAP_MARGIN.get(s["produce_id"], 0)]
            if sp:
                return min(sp, key=lambda j: self._order.index(opts[j]["attr"]))
            want = [j for j in ok if opts[j].get("do") == "lesson" and opts[j].get("attr") == attr]
            if want:
                return want[0]
            lessons = [j for j in ok if opts[j].get("do") == "lesson"]
            return (rest or lessons or ok)[0]
        schools = [j for j in ok if opts[j].get("do") == "school"]
        if schools:
            return next((j for j in schools if opts[j].get("attr") == attr), schools[0])
        if s["stamina"] * 100 < s["max_stamina"] * LOW_STAMINA_PCT:
            out = [j for j in ok if opts[j].get("do") == "activity"] or rest
            if out:
                return out[0]
        others = [j for j in ok if opts[j].get("do") != "refresh"] or ok
        for want in FREE_ORDER:
            hit = [j for j in others if opts[j].get("do") == want]
            if hit:
                return hit[0]
        return others[0]

    def other(self, sim, dec, opts, ok):
        """Every other decision, without a draw of its own (a choice drawn at random would differ between the choices
        compared, on the same root): offered cards by rarity (upgraded first); in a shop the cards by rarity, then its
        drinks (DRINKS_NOTE), then the shop's upgrade, then leave; anything else its first enabled option."""
        kind = dec["kind"]
        t = sim.s["queue"][0]
        if kind == "offer":
            picks = [j for j in ok if "pick" in opts[j]]
            if picks and t.get("what") == "card":
                return max(picks, key=lambda j: (_rarity(sim, *t["cands"][opts[j]["pick"]]), -j))
            return (picks or ok)[0]
        if kind in ("shop", "interval"):
            shelf = sim.s["shop"]["shelf"] or []
            slot = "buy" if kind == "shop" else "slot"
            cards = [j for j in ok if slot in opts[j] and shelf[opts[j][slot]]["what"] == "card"]
            drinks = [j for j in ok if slot in opts[j] and shelf[opts[j][slot]]["what"] == "drink"]
            if cards:
                return max(cards, key=lambda j: (_rarity(sim, shelf[opts[j][slot]]["id"], shelf[opts[j][slot]]["up"]), -j))
            if drinks:
                return drinks[0]
            ups = [j for j in ok if opts[j].get("service") == "upgrade"
                   or (slot in opts[j] and shelf[opts[j][slot]]["what"] == "upgrade")]
            leave = [j for j in ok if opts[j].get("leave")]
            return (ups or leave or ok)[0]
        if kind in ("customize", "customize_pick_card"):
            raise_up = kind == "customize_pick_card"
            for j in ok:
                if opts[j].get("n") is None:
                    continue
                c = sim._card_by_number(opts[j]["n"])
                if any(price <= sim.s["pp"] for _cz, _lv, price in sim._customize_choices(c, raise_up=raise_up)):
                    return j
            return next((j for j in ok if opts[j].get("leave") or ("n" in opts[j] and opts[j]["n"] is None)), ok[-1])
        return next((j for j in ok if not opts[j].get("back")), ok[0])


RARITY = {"N": 1, "R": 2, "SR": 3, "SSR": 4, "LEGEND": 5}


def _rarity(sim, cid, up):
    return RARITY.get(name(sim.d.card(cid, up).get("rarity"), "PRODUCE_CARD_RARITY_"), 0), int(up)


def _quiet(sim):
    sim._traced = lambda: None
    return sim


def fork(sim, root):
    st = copy.deepcopy(sim.s)
    st.update(root=root.hex(), rng={}, log=[], actions=[])
    return _quiet(Sim(None, _state=st))


def _choice(o):
    return o.get("do") + (":" + o["attr"] if o.get("attr") else "")


def play(sim, policy, pol, line=None):
    """``policy`` plays ``sim`` to its end (or to a decision it cannot take)."""
    n = 0
    while not sim.s["done"] and sim.s["queue"] and n < 5000:
        n += 1
        if sim.s["queue"][0]["t"] == "exam":
            if policy.transitions:
                sim.exam_auto(transitions=policy.transitions)
            else:
                sim.exam_assume(*full_score(sim, sim.s["queue"][0]))
            continue
        dec = sim.decision()
        if dec is None:
            break
        kind, opts = dec["kind"], dec["options"]
        ok = [j for j, o in enumerate(opts) if not o.get("disabled")]
        if not ok:
            break
        if kind == "day":
            i = policy.day(sim, opts, ok, pol)
            if line is not None:
                line.append([sim.s["produce_id"], sim.s["step"], _choice(opts[i])])
        elif kind == "lost":
            i = 0 if not opts[0].get("disabled") else ok[0]
        elif kind == "passed":
            i = 0
        else:
            i = policy.other(sim, dec, opts, ok)
        sim.act(i)


def rollout_root(base, k):
    return hashlib.sha256(ROOT + b"|" + base + b"|" + str(int(k)).encode()).digest()


def rollout(sim, first, policy, k):
    """Choice ``first`` of the current decision, then ``policy`` to the produce's end (a 選抜 on into a 本戦):
    {rating, grade, outcome, line}."""
    root = rollout_root(bytes.fromhex(sim.s["root"]), k)
    f = fork(sim, root)
    pol = Rng(root)("forecast/policy")
    line = [[f.s["produce_id"], f.s["step"], _choice(f.options()[first])]]
    f.act(first)
    play(f, policy, pol, line)
    res = f.s.get("result") or {}
    end = f
    if res.get("outcome") == "selection_memory":
        end = _quiet(Sim.honsen(res["selection_memory"], root=hashlib.sha256(root + b"|honsen").hexdigest()))
        play(end, policy, pol, line)
        res = end.s.get("result") or {}
    rating = res.get("rating") if res.get("outcome") == "memory" else None
    full = None
    if rating is not None:
        from GakumasCLI.produce import produce_effects as pe
        es = end.s
        star = (es["auditions"].get("final") or {}).get("star_before", es["star"])
        full = pe.hif_rating_full([es["params"][a] for a in ATTRS], star, es["star_permil"])
    return {"rating": rating, "full": full, "grade": res.get("grade") if rating is not None else None,
            "grade_full": _grade(full), "outcome": res.get("outcome") or "unfinished", "line": line}


def _grade(rating):
    if rating is None:
        return None
    from GakumasCLI.produce import produce_effects as pe
    return pe.rank_of(rating)


# -- the forecast -----------------------------------------------------------------------------------------------------
def _quantile(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))] if xs else None


def summary(results, key="full"):
    gkey = "grade_full" if key == "full" else "grade"
    results = [r for r in results if r is not None]
    vals = [r[key] or 0 for r in results]
    rated = [r[key] for r in results if r[key] is not None]
    grades = {}
    for r in results:
        if r[gkey]:
            grades[r[gkey]] = grades.get(r[gkey], 0) + 1
    n = len(results)
    mean = sum(vals) / n if n else 0
    sd = (sum((v - mean) ** 2 for v in vals) / (n - 1)) ** 0.5 if n > 1 else 0
    return {"n": n, "mean": round(mean), "se": round(sd / n ** 0.5) if n > 1 else None,
            "p10": _quantile(vals, 0.1), "p50": _quantile(vals, 0.5), "p90": _quantile(vals, 0.9),
            "finished": len(rated), "grades": dict(sorted(grades.items(), key=lambda kv: -kv[1]))}


def paired(results, base, key="full"):
    """The mean and standard error of the rating difference to ``base``, rollout by rollout (same roots)."""
    d = [(a[key] or 0) - (b[key] or 0) for a, b in zip(results, base) if a is not None and b is not None]
    n = len(d)
    if n < 2:
        return {"diff": round(sum(d) / n) if n else 0, "se": None}
    m = sum(d) / n
    return {"diff": round(m), "se": round((sum((x - m) ** 2 for x in d) / (n - 1) / n) ** 0.5)}


def line_of(results, after):
    """At each later step, the choice most of ``results`` made there and its share."""
    steps = {}
    for r in results:
        for pid, step, ch in (r or {}).get("line") or ():
            if (pid, step) <= after:
                continue
            steps.setdefault((pid, step), {}).setdefault(ch, 0)
            steps[(pid, step)][ch] += 1
    out = []
    for (pid, step), cs in sorted(steps.items(), key=lambda kv: (kv[0][0] != SELECTION, kv[0][1])):
        ch, k = max(cs.items(), key=lambda kv: kv[1])
        out.append({"produce_id": pid, "step": step, "choice": ch, "share": round(k / sum(cs.values()), 2)})
    return out


def jobs_of(sim, forms, strong):
    """The options, the enabled ones, and {form: attribute} of the forms (one when both name the same attribute)."""
    opts = sim.options()
    cands = [j for j, o in enumerate(opts) if not o.get("disabled")]
    attrs = {}
    for form in forms:
        a = form_attr(sim, form, strong)
        if a not in attrs.values():
            attrs[form] = a
    return opts, cands, attrs


def forecast(sim, forms=("normal", "strong"), rollouts=ROLLOUTS, transitions=TRANSITIONS, strong=STRONG, run=None,
             objective=OBJECTIVE):
    """Every enabled choice of the current day decision under each form: {format, produce_id, step, options, forms:
    {form: {attr, rows: [{choice, label, mean, se, p10, p50, p90, finished, grades}], best, line}}, seconds}.
    """
    t0 = time.time()
    dec = sim.decision()
    if dec is None or dec["kind"] != "day":
        return None
    opts, cands, attrs = jobs_of(sim, forms, strong)
    jobs = [(form, j, k) for form in attrs for j in cands for k in range(int(rollouts))]
    if run is None:
        results = [rollout(sim, j, Policy(attrs[form], transitions), k) for form, j, k in jobs]
    else:
        results = run(sim.to_json(), [(attrs[form], j, k, transitions) for form, j, k in jobs])
    s = sim.s
    out = {"format": FORMAT, "produce_id": s["produce_id"], "step": s["step"], "strong": strong, "objective": objective,
           "options": [o["label"] for o in opts], "rollouts": int(rollouts),
           "exams": "searched" if transitions else "full", "transitions": transitions, "forms": {}}
    by = {}
    for (form, j, k), r in zip(jobs, results):
        by.setdefault((form, j), []).append(r)
    for form, attr in attrs.items():
        rows = []
        for j in cands:
            row = summary(by[(form, j)], objective)
            row.update(choice=_choice(opts[j]), label=opts[j]["label"], index=j,
                       simulated=summary(by[(form, j)], "rating" if objective == "full" else "full"))
            rows.append(row)
        best = max(rows, key=lambda r: (r["n"] > 0, r["mean"]))
        for row in rows:
            row["vs_best"] = paired(by[(form, row["index"])], by[(form, best["index"])], objective)
        order = attr_order(sim)
        out["forms"][form] = {"attr": attr, "train": [attr] + [a for a in order if a != attr][:1],
                              "rows": rows, "best": best["index"],
                              "line": line_of(by[(form, best["index"])], (s["produce_id"], s["step"]))}
    out["failed"] = sum(1 for r in results if r is None)
    out["seconds"] = round(time.time() - t0, 1)
    return out


def run_job(state_json, job):
    """One rollout job of ``forecast``'s ``run`` (a worker process keeps the simulator data loaded)."""
    attr, j, k, transitions = job
    sim = Sim.from_json(state_json)
    return rollout(sim, j, Policy(attr, transitions), k)


# -- command line ---------------------------------------------------------------------------------------------------
def _frames(path):
    import gzip
    with open(path, "rb") as fh:
        opener = gzip.open if fh.read(2) == b"\x1f\x8b" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def _pool_job(args):
    state_json, job = args
    return run_job(state_json, job)


def main(argv):
    import argparse
    from GakumasCLI.produce import hif_advice as A
    ap = argparse.ArgumentParser(prog="GKMSPS.ps.forecast")
    ap.add_argument("frames")
    ap.add_argument("--at", type=int, help="only the home moment before this step")
    ap.add_argument("--produce", choices=("007", "008"))
    ap.add_argument("--rollouts", type=int, default=ROLLOUTS)
    ap.add_argument("--transitions", type=int, default=TRANSITIONS,
                    help="play the auditions on the native search (that many transitions each) instead of at full scores")
    ap.add_argument("--strong", default=STRONG)
    ap.add_argument("--jobs", type=int, default=1)
    a = ap.parse_args(argv[1:])
    pool = None
    if a.jobs > 1:
        import multiprocessing as mp
        pool = mp.get_context("spawn").Pool(a.jobs)

    def run(state_json, jobs):
        return pool.map(_pool_job, [(state_json, j) for j in jobs], chunksize=1)

    tr = A.Tracker()
    for frame in _frames(a.frames):
        if (frame.get("dir") == "out" and (frame.get("rpc") or "").endswith("/Next") and at_home(tr.current)):
            p = tr.current.progress
            step = int(p.get("step_number") or 0) + 1
            pid = p.get("produce_id")
            if (a.at is None or a.at == step) and (a.produce is None or pid.endswith(a.produce)):
                try:
                    sim = from_produce(tr.current)
                except LookupError as exc:
                    print(json.dumps({"produce_id": pid, "step": step, "error": str(exc)}, ensure_ascii=False), flush=True)
                    tr.feed(frame)
                    continue
                out = forecast(sim, rollouts=a.rollouts, transitions=a.transitions, strong=a.strong, run=run if pool else None)
                if out is not None:
                    out["played"] = (frame.get("decoded") or {}).get("step_type")
                    print(json.dumps(out, ensure_ascii=False), flush=True)
        tr.feed(frame)
    if pool:
        pool.close()


if __name__ == "__main__":
    main(sys.argv)
