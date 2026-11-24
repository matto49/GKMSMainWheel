# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""cards, drinks and shop / interval purchases, valued in the 本戦 rating.

    python -B hif_decisions.py build LEDGER.jsonl ACTIVATIONS.jsonl LOADOUT_ROOT OUT.json
    python -B hif_decisions.py run DECISIONS.json OUT.jsonl --budget S --seeds N [--workers W]
        [--lib PATH] [--only KEY,KEY] [--shard i/n]          (appends; skips the rows OUT holds)
    python -B hif_decisions.py summary DECISIONS.json OUT.jsonl [--json OUT.json]
"""
import json
import math
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
import GakumasCLI.produce.produce_value as PV   # noqa: E402
import GakumasCLI.produce.produce_effects as pe   # noqa: E402

DRINK_CAP = 4
BASIC = ("-act-0_", "-men-0_", "p_card-00-acc-0_002")
CARD_KINDS = ("card_pick", "card_change_select", "present_pick")
FINAL = {"produce-008": ("AuditionMid1", "AuditionFinal")}


def _card(c):
    return {"id": c.get("resource_id") or c.get("id"), "upgrade": int(c.get("upgrade_count") or c.get("upgrade") or 0)}


# -- the loadout changes -----------------------------------------------------------------------
def apply(lo, ops):
    """The recorded loadout with `ops` applied, or None when an op does not apply to it."""
    lo = json.loads(json.dumps(lo))
    deck, drinks = lo["deck_cards"], lo["drink_ids"]

    def find(card, any_upgrade=True):
        for i, d in enumerate(deck):
            if d["produce_card_id"] == card["id"] and (any_upgrade or int(d.get("current_upgrade_count") or 0) == card["upgrade"]):
                return i
        return None

    for op in ops:
        k = op["op"]
        if k == "swap_card":
            i = find(op["remove"])
            if i is None:
                return None
            up = max(int(deck[i].get("current_upgrade_count") or 0), op["add"]["upgrade"])
            deck[i] = {"produce_card_id": op["add"]["id"], "current_upgrade_count": up}
        elif k == "add_card":
            deck.append({"produce_card_id": op["card"]["id"], "current_upgrade_count": op["card"]["upgrade"]})
        elif k == "remove_card":
            i = find(op["card"])
            if i is None:
                return None
            deck.pop(i)
        elif k == "upgrade_card":
            i = find(op["card"], any_upgrade=False)
            if i is None:
                return None
            deck[i] = dict(deck[i], current_upgrade_count=int(deck[i].get("current_upgrade_count") or 0) + 1)
        elif k == "downgrade_card":
            i = next((j for j, d in enumerate(deck) if d["produce_card_id"] == op["card"]["id"]
                      and int(d.get("current_upgrade_count") or 0) >= 1), None)
            if i is None:
                return None
            deck[i] = dict(deck[i], current_upgrade_count=int(deck[i]["current_upgrade_count"]) - 1)
        elif k == "swap_drink":
            if op["remove"] not in drinks:
                return None
            drinks[drinks.index(op["remove"])] = op["add"]
        elif k == "add_drink":
            if len(drinks) >= DRINK_CAP:
                return None
            drinks.append(op["drink"])
        elif k == "remove_drink":
            if op["drink"] not in drinks:
                return None
            drinks.remove(op["drink"])
        elif k == "stamina":
            st = int(lo.get("starting_stamina") or 0) + op["delta"]
            if st < 1 or st > int(lo.get("max_stamina") or st):
                return None
            lo["starting_stamina"] = st
        else:
            raise ValueError(k)
    return lo


# -- build ------------------------------------------------------------------------------------
def _pairs(act_path):
    A = [json.loads(line) for line in open(act_path, encoding="utf-8")]
    sel = {(p.get("summary") or {}).get("user_selection_memory_id"): p["uuid"] for p in A
           if p.get("produce_id") == "produce-007" and (p.get("summary") or {}).get("user_selection_memory_id")}
    out, auds = {}, {}
    for p in A:
        if p.get("produce_id") == "produce-008":
            s = sel.get(p.get("selection_memory_id"))
            if s:
                out[s] = p["uuid"]
            last = {}
            for a in p.get("auditions") or []:
                last[a["step_type"]] = a
            fin, mid = last.get("audition_final") or {}, last.get("audition_mid1") or {}
            auds[p["uuid"]] = {"params": [(fin.get("before") or {}).get(k) for k in pe.ATTR.values()],
                               "star": (fin.get("before") or {}).get("star"),
                               "recorded": {"AuditionMid1": (mid.get("server") or {}).get("score") or mid.get("score"),
                                            "AuditionFinal": (fin.get("server") or {}).get("score") or fin.get("score")}}
    return out, auds


def _visits(rows):
    """Shop / interval visits: the list the player last saw and every buy at that visit."""
    out, cur = [], None
    for r in rows:
        k = r["kind"]
        if k in ("shop_visit", "interval_visit"):
            cur = {"kind": k.split("_")[0], "first": r, "rows": [r], "buys": [], "list": r.get("candidates") or []}
            out.append(cur)
        elif cur and k in ("shop_buy", "interval_buy", "shop_reroll", "interval_reroll") and \
                r["uuid"] == cur["first"]["uuid"] and r["state"].get("step_number") == cur["first"]["state"].get("step_number"):
            cur["rows"].append(r)
            if k.endswith("_reroll"):
                cur["list"] = r.get("after") or cur["list"]
            elif r.get("slot"):
                cur["buys"].append(r)
                cur["list"] = r.get("candidates") or cur["list"]
    return out


def build(ledger, act_path, loadout_root):
    R = [json.loads(line) for line in open(ledger, encoding="utf-8")]
    R = [r for r in R if r.get("produce_id") in ("produce-007", "produce-008") and r["state"].get("step_number") is not None]
    pairs, auds = _pairs(act_path)
    templates = PV._templates(loadout_root)
    by_uuid = defaultdict(list)
    for r in R:
        by_uuid[r["uuid"]].append(r)
    stages = {u: PV._stage_templates(sorted(rs, key=lambda r: int(r["state"]["step_number"])), templates)
              for u, rs in by_uuid.items() if rs[0]["produce_id"] == "produce-008"}
    decisions, skipped = [], []

    def anchor(r):
        """(本戦 uuid, its stages the decision reaches) or a reason."""
        if r["produce_id"] == "produce-008":
            u, step = r["uuid"], int(r["state"]["step_number"])
            st = [stages.get(u, {}).get(s) for s, n in PV.AUDITION_STEPS["produce-008"] if n > step]
        else:
            u = pairs.get(r["uuid"])
            if not u:
                return None, "no recorded 本戦 for this 選抜"
            st = [stages.get(u, {}).get(s) for s in FINAL["produce-008"]]
        st = [x for x in st if x]
        return (u, st) if st else (None, "no recorded 本戦 loadout ahead")

    def add(r, kind, options, extra=None):
        u, st = anchor(r)
        if not u:
            skipped.append([r["source"], kind, st])
            return
        options = [o for o in options if o["ops"]]
        if not options:
            return
        d = {"key": f"{r['source']}|{kind}", "kind": kind, "uuid": r["uuid"], "produce_id": r["produce_id"],
             "idol_card_id": r["idol_card_id"], "step_number": int(r["state"]["step_number"]),
             "produce_point": r["state"].get("produce_point"), "anchor": u, "audition_final": auds.get(u),
             "stages": [{"step_id": s["step_id"], "template": s["template"], "loadout": s["loadout"]} for s in st],
             "options": options}
        d.update(extra or {})
        decisions.append(d)

    deck_of = lambda r: [{"id": c["id"], "upgrade": int(c.get("upgrade") or 0)} for c in r["state"]["deck"]]   # noqa: E731
    for r in R:
        k = r["kind"]
        cands = (r.get("offer") or {}).get("candidates") or r.get("candidates") or []
        chosen = r.get("chosen") or []
        if k in CARD_KINDS and cands and all(c.get("type") == "card" for c in cands) and chosen:
            c0 = _card(chosen[0])
            add(r, "card_offer", [{"label": f"take {_card(c)['id']}+{_card(c)['upgrade']}",
                                   "ops": [{"op": "swap_card", "remove": c0, "add": _card(c)}]}
                                  for c in cands if _card(c) != c0], {"chosen": c0})
        elif k == "drink_pick" and r["produce_id"] == "produce-008" and cands and chosen:
            d0 = chosen[0]["resource_id"]
            add(r, "drink_offer", [{"label": f"take {c['resource_id']}", "ops": [{"op": "swap_drink", "remove": d0, "add": c["resource_id"]}]}
                                   for c in cands if c.get("resource_id") != d0], {"chosen": d0})
        elif k == "discard_drink" and r["produce_id"] == "produce-008" and r.get("discarded"):
            gone = r["discarded"][0]
            kept = [x for x in r["state"]["drinks"] if x != gone]
            add(r, "drink_discard", [{"label": f"discard {x} instead", "ops": [{"op": "swap_drink", "remove": x, "add": gone}]}
                                     for x in dict.fromkeys(kept)], {"chosen": gone})
    for v in _visits(R):
        r0 = v["first"]
        opts, deck, pp = [], deck_of(r0), r0["state"].get("produce_point") or 0
        bought = set()
        for b in v["buys"]:
            s = b["slot"]
            bought.add(b.get("position"))
            t, price = s.get("type"), s.get("price") or 0
            ch = b.get("card_changes") or []
            if t == "card":
                opts.append({"label": f"skip bought {s['resource_id']}", "price": -price, "bought": True,
                             "ops": [{"op": "remove_card", "card": _card(s)}]})
            elif t == "drink" and r0["produce_id"] == "produce-008":
                opts.append({"label": f"skip bought {s['resource_id']}", "price": -price, "bought": True,
                             "ops": [{"op": "remove_drink", "drink": s["resource_id"]}]})
            elif t == "card_upgrade":
                up = next((c for c in ch if c["kind"] == "upgraded"), None)
                if up:
                    opts.append({"label": f"skip upgrade of {up['id']}", "price": -price, "bought": True,
                                 "ops": [{"op": "downgrade_card", "card": {"id": up["id"], "upgrade": up["upgrade"]}}]})
            elif t in ("card_delete", "card_change"):
                gone = next((c for c in ch if c["kind"] == "deleted"), None)
                new = next((c for c in ch if c["kind"] == "new"), None)
                ops = ([{"op": "remove_card", "card": {"id": new["id"], "upgrade": new["upgrade"]}}] if new else []) + \
                      ([{"op": "add_card", "card": {"id": gone["id"], "upgrade": gone["upgrade"]}}] if gone else [])
                opts.append({"label": f"skip {t} ({gone and gone['id']} -> {new and new['id']})", "price": -(price or 0),
                             "bought": True, "ops": ops})
            elif t == "stamina":
                n = int(b.get("target_number") or 1)
                gain = (s.get("stamina_recover_value") or 2) * n
                opts.append({"label": f"skip stamina +{gain}", "price": -price * n, "bought": True,
                             "ops": [{"op": "stamina", "delta": -gain}]})
        basics = [c for c in deck if any(x in c["id"] for x in BASIC)]
        for s in v["list"]:
            if s.get("position") in bought or not s.get("price"):
                continue
            t, price = s.get("type"), s.get("price")
            if t == "card":
                opts.append({"label": f"buy {s['resource_id']}+{int(s.get('upgrade_count') or 0)}", "price": price,
                             "ops": [{"op": "add_card", "card": _card(s)}]})
            elif t == "drink" and r0["produce_id"] == "produce-008":
                opts.append({"label": f"buy {s['resource_id']}", "price": price, "ops": [{"op": "add_drink", "drink": s["resource_id"]}]})
            elif t == "card_change" and basics:
                opts.append({"label": f"change {basics[0]['id']} -> {s['resource_id']}", "price": price,
                             "ops": [{"op": "remove_card", "card": basics[0]}, {"op": "add_card", "card": _card(s)}]})
            elif t == "card_delete" and basics:
                for c in list({c["id"]: c for c in basics}.values())[:3]:
                    opts.append({"label": f"delete {c['id']}", "price": price, "ops": [{"op": "remove_card", "card": c}]})
            elif t == "stamina" and v["kind"] == "interval":
                opts.append({"label": "stamina +10", "price": price * 5, "ops": [{"op": "stamina", "delta": 10}]})
        add(r0, v["kind"] + "_visit", opts, {"visit_rows": [x["source"] for x in v["rows"]]})
    return decisions, skipped


# -- run --------------------------------------------------------------------------------------
_RUN = {}


def _job(args):
    key, step_id, label, lo, seed, budget = args
    score, native, secs, roll = PV.search_score(lo, seed, budget, 182)
    return {"key": key, "stage": step_id, "option": label, "seed": seed, "budget": budget, "score": score,
            "native": native, "seconds": secs}


def jobs_of(decisions, seeds, budget, done):
    out = []
    for d in decisions:
        for st in d["stages"]:
            sd = PV.exam_seeds(d["key"], st["step_id"], seeds)
            los = {"base": st["loadout"]}
            for o in d["options"]:
                lo = apply(st["loadout"], o["ops"])
                if lo is not None:
                    los[o["label"]] = lo
            if len(los) < 2:
                continue
            for label, lo in los.items():
                for s in sd:
                    if (d["key"], st["step_id"], label, s, budget) not in done:
                        out.append((d["key"], st["step_id"], label, lo, s, budget))
    return out


def run(dec_path, out_path, budget, seeds, workers, lib, only=None, shard=None):
    from multiprocessing import Pool
    decisions = json.load(open(dec_path, encoding="utf-8"))["decisions"]
    if only:
        decisions = [d for d in decisions if d["key"] in only]
    if shard:
        i, n = shard
        decisions = decisions[i::n]
    done = set()
    if Path(out_path).exists():
        for line in open(out_path, encoding="utf-8"):
            r = json.loads(line)
            done.add((r["key"], r["stage"], r["option"], r["seed"], r["budget"]))
    jobs = jobs_of(decisions, seeds, budget, done)
    t0 = time.time()
    with open(out_path, "a", encoding="utf-8") as out, Pool(workers, initializer=PV._init, initargs=(lib,)) as pool:
        for r in pool.imap_unordered(_job, jobs):
            out.write(json.dumps(r) + "\n")
            out.flush()
    print(json.dumps({"decisions": len(decisions), "jobs": len(jobs), "skipped": len(done),
                      "wall_seconds": round(time.time() - t0, 1)}))


# -- summary ----------------------------------------------------------------------------------
_Z9 = (-1.593, -0.967, -0.589, -0.282, 0.0, 0.282, 0.589, 0.967, 1.593)


def rating_change(levels, rel, spread=0.0):
    """The H.I.F rating change when the 本戦 rounds move by the fractions in ``rel`` at the scores in
    ``levels`` ({"AuditionMid1": score, "AuditionFinal": score}; a round missing from ``rel`` stays).
    """
    l1, l2 = levels.get("AuditionMid1"), levels.get("AuditionFinal")
    if l1 is None or l2 is None:
        return None
    if spread:
        return sum(rating_change({"AuditionMid1": l1 * math.exp(spread * z), "AuditionFinal": l2 * math.exp(spread * z)},
                                 rel) for z in _Z9) / len(_Z9)
    r1 = l1 * (1 + rel.get("AuditionMid1", 0.0))
    r2 = l2 * (1 + rel.get("AuditionFinal", 0.0))
    return pe.hif_rating([0, 0, 0], 0, int(r1 * 1.2), int(r2)) - pe.hif_rating([0, 0, 0], 0, int(l1 * 1.2), int(l2))


def keep_rating(mid1, final, mid1_rel, final_rel, spread=0.0):
    """the 本戦 rating change of keeping bottles in the 上半場 for the 下半場 -- this round moves by ``mid1_rel`` at ``mid1``
    (the round being played: its level is known, so no spread), the 下半場 by ``final_rel`` at ``final`` (a prediction:
    the mean over a log-normal spread of that level alone).
    """
    zs = _Z9 if spread else (0.0,)
    out = 0.0
    for z in zs:
        f = final * math.exp(spread * z)
        out += pe.hif_rating([0, 0, 0], 0, int(mid1 * (1 + mid1_rel) * 1.2), int(f * (1 + final_rel))) \
            - pe.hif_rating([0, 0, 0], 0, int(mid1 * 1.2), int(f))
    return out / len(zs)


def summary(dec_path, out_path):
    decisions = {d["key"]: d for d in json.load(open(dec_path, encoding="utf-8"))["decisions"]}
    rows = [json.loads(line) for line in open(out_path, encoding="utf-8")]
    by = defaultdict(dict)
    for r in rows:
        by[r["key"]][(r["stage"], r["option"], r["seed"], r["budget"])] = r["score"]
    res = []
    for key, sc in by.items():
        d = decisions.get(key)
        if not d:
            continue
        fin = d.get("audition_final") or {}
        params, star = fin.get("params"), fin.get("star")
        budgets = sorted({b for _, _, _, b in sc})
        for budget in budgets:
            stages = [st["step_id"] for st in d["stages"]]
            seeds = {st: [s for (stg, o, s, b) in sc if stg == st and o == "base" and b == budget] for st in stages}
            base_mean = {st: (sum(sc[(st, "base", s, budget)] for s in seeds[st]) / len(seeds[st]) if seeds[st] else None)
                         for st in stages}
            rec = fin.get("recorded") or {}
            levels = {st: rec.get(st) for st in ("AuditionMid1", "AuditionFinal")}
            for o in d["options"]:
                lab = o["label"]
                d_score, d_rating, d_abs = defaultdict(list), [], []
                n = max(len(v) for v in seeds.values()) if seeds else 0
                for k in range(n):
                    vals, ok = {}, True
                    for st in stages:
                        if k >= len(seeds[st]):
                            ok = False
                            break
                        s = seeds[st][k]
                        b = sc.get((st, "base", s, budget))
                        x = sc.get((st, lab, s, budget), b)
                        if b is None:
                            ok = False
                            break
                        vals[st] = (b, x)
                        d_score[st].append(x - b)
                    if not ok or not params or star is None:
                        continue
                    r1b = vals.get("AuditionMid1", (rec.get("AuditionMid1"),) * 2)
                    r2b = vals.get("AuditionFinal", (rec.get("AuditionFinal"),) * 2)
                    if None in (r1b[0], r2b[0]):
                        continue
                    rb = pe.hif_rating(params, star, int(r1b[0] * 1.2), int(r2b[0]))
                    rx = pe.hif_rating(params, star, int(r1b[1] * 1.2), int(r2b[1]))
                    d_abs.append(rx - rb)
                    rel = {st: (x - b) / (base_mean[st] or 1) for st, (b, x) in vals.items()}
                    r = rating_change(levels, rel)
                    if r is not None:
                        d_rating.append(r)
                m, ci = PV._ci(d_rating)
                m_abs, ci_abs = PV._ci(d_abs)
                row = {"key": key, "kind": d["kind"], "uuid": d["uuid"][:8], "produce": d["produce_id"],
                       "step": d["step_number"], "budget": budget, "option": lab, "price": o.get("price"),
                       "bought": o.get("bought", False), "n": len(d_rating),
                       "rating_delta": None if m is None else round(m, 1), "rating_ci95": None if ci is None else round(ci, 1),
                       "rating_delta_search_level": None if m_abs is None else round(m_abs, 1),
                       "rating_ci95_search_level": None if ci_abs is None else round(ci_abs, 1),
                       "levels": levels,
                       "score_delta": {st: round(sum(v) / len(v)) for st, v in d_score.items() if v},
                       "base_score": {st: round(v) for st, v in base_mean.items() if v is not None}}
                if o.get("price") and m is not None:
                    row["rating_per_100p"] = round(100 * m / o["price"], 1)
                res.append(row)
    return res


def main(argv):
    cmd = argv[1]
    if cmd == "build":
        decisions, skipped = build(argv[2], argv[3], argv[4])
        json.dump({"schema": "gkms.hif_decisions.v1", "decisions": decisions, "skipped": skipped},
                  open(argv[5], "w", encoding="utf-8"), ensure_ascii=False)
        kinds = defaultdict(int)
        for d in decisions:
            kinds[d["kind"]] += 1
        print(json.dumps({"decisions": len(decisions), "kinds": kinds, "options": sum(len(d["options"]) for d in decisions),
                          "skipped": len(skipped)}, ensure_ascii=False))
    elif cmd == "run":
        budget = float(argv[argv.index("--budget") + 1])
        seeds = int(argv[argv.index("--seeds") + 1])
        workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else os.cpu_count()
        lib = argv[argv.index("--lib") + 1] if "--lib" in argv else os.environ.get("GKMS_NATIVE_LIB") or str(
            PV.G / "native" / "build" / "gkmscore.dll")
        only = set(argv[argv.index("--only") + 1].split(",")) if "--only" in argv else None
        shard = tuple(int(x) for x in argv[argv.index("--shard") + 1].split("/")) if "--shard" in argv else None
        run(argv[2], argv[3], budget, seeds, workers, lib, only, shard)
    elif cmd == "summary":
        res = summary(argv[2], argv[3])
        if "--json" in argv:
            json.dump(res, open(argv[argv.index("--json") + 1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        for r in res:
            print(f"{r['uuid']} {r['produce'][-3:]} s{r['step']:<2} {r['kind']:14s} {r['option'][:44]:44s} "
                  f"price {str(r['price']):>5} n{r['n']:<3} rating {r['rating_delta']} +- {r['rating_ci95']}")


if __name__ == "__main__":
    main(sys.argv)
