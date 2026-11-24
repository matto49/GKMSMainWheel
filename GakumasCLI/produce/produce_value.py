# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""deck-delta evaluator for Produce card offers (v1, H.I.F).

    python -B produce_value.py offers LEDGER.jsonl LOADOUT_ROOT OUT.json
    python -B produce_value.py eval OFFERS.json OUT.jsonl --budget 2 --seeds 32
        [--workers N] [--search-seed 182] [--only KEY,KEY] [--shard i/n] [--per-offer] [--eliminate]
    python -B produce_value.py summary OUT.jsonl [OUT2.jsonl]
    python -B produce_value.py c1 ROUNDS.json OUT.jsonl --budgets 0.5,1,2,5 --seeds 8
"""
import argparse
import glob
import json
import math
import os
import random
import time
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
E = G = REPO / "GakumasCLI"
MASTER_DIR = E / "ro" / "master" / "tables"
AUDITION_STEPS = {"produce-007": [("AuditionMid1", 7), ("AuditionMid2", 13), ("AuditionFinal", 20)],
                  "produce-008": [("AuditionMid1", 7), ("AuditionFinal", 9)]}
CARD_KINDS = ("card_pick", "card_change_select", "present_pick", "reward_reroll", "reward_exclude")


# ----------------------------------------------------------------------------- offers
def _deck_entry(card, template_pool):
    e = {"produce_card_id": card["id"], "current_upgrade_count": int(card.get("upgrade") or 0)}
    if card.get("customizes"):
        for i, t in enumerate(template_pool):
            if (t["produce_card_id"] == e["produce_card_id"] and t.get("customizes")
                    and int(t.get("current_upgrade_count") or 0) == e["current_upgrade_count"]):
                e["customizes"] = template_pool.pop(i)["customizes"]
                break
        else:
            e["customizes_unknown"] = int(card["customizes"])
    return e


def _templates(loadout_root):
    out = []
    for f in sorted(glob.glob(os.path.join(loadout_root, "*", "loadout_*.json"))):
        d = json.load(open(f, encoding="utf-8"))
        if any(int(v) for v in (d.get("score_bonus_permils") or {}).values()):
            out.append((os.path.relpath(f, loadout_root), d))
    return out


def _stage_templates(records, templates):
    """{step_id: (template path, loadout)} for one produce: each audition is matched with the produce's last recorded
    state before it; a loadout serves one produce.
    """
    out = {}
    r0 = records[0]
    runs = {r["source"].split("#")[0] for r in records}
    for step_id, number in AUDITION_STEPS[r0["produce_id"]]:
        before = [r for r in records if int(r["state"]["step_number"]) < number and r["state"].get("deck")]
        if not before:
            continue
        last = before[-1]["state"]
        deck = defaultdict(int)
        for c in last["deck"]:
            deck[c["id"]] += 1
        params = [last["vocal"], last["dance"], last["visual"]]
        best = None
        for path, d in templates:
            if (d.get("produce_id"), d.get("idol_card_id"), d.get("round")) != (
                    r0["produce_id"], r0["idol_card_id"], step_id.replace("Audition", "").lower()):
                continue
            tp = [int(d["parameters"][k]) for k in "123"]
            if any(a < b for a, b in zip(tp, params)):
                continue
            td = defaultdict(int)
            for c in d["deck_cards"]:
                td[c["produce_card_id"]] += 1
            cont = sum(min(n, td[i]) for i, n in deck.items()) / max(1, sum(deck.values()))
            key = (cont, -sum(a - b for a, b in zip(tp, params)), path)
            ok = cont >= 0.9 or (cont >= 0.6 and path.replace("\\", "/").split("/")[0] in runs)
            if ok and (best is None or key > best[0]):
                best = (key, path, d)
        if best:
            out[step_id] = {"step_id": step_id, "template": best[1], "loadout": best[2],
                            "match": {"containment": round(best[0][0], 3), "state_step": before[-1]["state"]["step_number"],
                                      "state_source": before[-1]["source"]}}
    return out


def build_offers(ledger, loadout_root):
    R = [json.loads(line) for line in open(ledger, encoding="utf-8")]
    templates = _templates(loadout_root)
    by_uuid = defaultdict(list)
    for r in R:
        if r["produce_id"] in AUDITION_STEPS and r["state"].get("step_number") is not None:
            by_uuid[r["uuid"]].append(r)
    stage_of = {u: _stage_templates(sorted(rs, key=lambda r: int(r["state"]["step_number"])), templates)
                for u, rs in by_uuid.items()}
    owner = {}
    for u, st in stage_of.items():
        for step_id, v in st.items():
            k = v["template"]
            if k not in owner or v["match"]["containment"] > stage_of[owner[k]][step_id]["match"]["containment"]:
                owner[k] = u
    for u, st in stage_of.items():
        for step_id in [s for s, v in st.items() if owner[v["template"]] != u]:
            del st[step_id]
    offers, skipped = [], []
    for r in R:
        if r["kind"] not in CARD_KINDS or r["produce_id"] not in AUDITION_STEPS:
            continue
        cands = (r.get("offer") or {}).get("candidates") or r.get("candidates") or []
        if not cands or any(c.get("type") != "card" for c in cands):
            continue
        step = int(r["state"]["step_number"])
        stages = [stage_of[r["uuid"]][s] for s, n in AUDITION_STEPS[r["produce_id"]]
                  if n > step and s in stage_of[r["uuid"]]]
        if not stages:
            skipped.append([r["source"], r["kind"], "no recorded audition ahead"])
            continue
        pool = [dict(c) for c in stages[0]["loadout"]["deck_cards"]]
        deck = [_deck_entry(c, pool) for c in sorted(r["state"]["deck"], key=lambda c: c["number"])]
        removed = [c["number"] for c in r.get("card_changes") or () if c["kind"] == "deleted"]
        if r["kind"].startswith("reward_") and not removed:
            nxt = next((x for x in R[R.index(r) + 1:] if x["uuid"] == r["uuid"] and x["kind"] == "card_change_select"
                        and (x.get("offer") or {}).get("effect_number") == (r.get("offer") or {}).get("effect_number")), None)
            removed = [c["number"] for c in (nxt or {}).get("card_changes") or () if c["kind"] == "deleted"]
        numbers = [c["number"] for c in sorted(r["state"]["deck"], key=lambda c: c["number"])]
        removed_number = None
        if r["kind"] == "card_change_select" or (r["kind"].startswith("reward_") and
                                                  (r.get("offer") or {}).get("effect_type") == "card_change_select"):
            if len(removed) != 1 or removed[0] not in numbers:
                skipped.append([r["source"], r["kind"], "changed card not in deck"])
                continue
            removed_number = removed[0]
            deck = [d for d, n in zip(deck, numbers) if n != removed_number]
        chosen = [c.get("resource_id") for c in r.get("chosen") or ()]
        options = [{"id": c["resource_id"], "upgrade": int(c.get("upgrade_count") or 0)} for c in cands]
        offers.append({
            "key": r["source"], "kind": r["kind"], "uuid": r["uuid"], "produce_id": r["produce_id"],
            "idol_card_id": r["idol_card_id"], "step_number": step, "removed_number": removed_number,
            "chosen_index": next((i for i, o in enumerate(options) if o["id"] in chosen), None),
            "options": options, "deck": deck, "stages": stages,
            "missing_stages": [s for s, n in AUDITION_STEPS[r["produce_id"]] if n > step and s not in stage_of[r["uuid"]]],
            "flags": ["myopic", "params_from_template"]})
    return offers, skipped


# ----------------------------------------------------------------------------- workers
_W = {}


def _init(lib):
    os.chdir(E)
    from GakumasCLI.native.diffrun import Native
    from GakumasCLI.native.native_search import NativeSearch
    from GakumasCLI.scenario.exam_session import Master
    from GakumasCLI.scenario.hif import build_hif_loadout_session
    from GakumasCLI.scenario import policy_search as PS
    from GakumasCLI.scenario import solver as SV
    master = Master.load(MASTER_DIR)
    nat = Native(lib)
    nat.load_master(master, json.load(open(G / "data" / "strings.json", encoding="utf-8")))
    _W.update(master=master, ns=NativeSearch(nat), build=build_hif_loadout_session, py=PS.policy_search, sv=SV)


def option_loadout(offer, stage, option):
    lo = json.loads(json.dumps(stage["loadout"]))
    deck = [{k: v for k, v in d.items() if k != "customizes_unknown"} for d in offer["deck"]]
    if option is not None:
        o = offer["options"][option]
        deck.append({"produce_card_id": o["id"], "current_upgrade_count": o["upgrade"]})
    lo["deck_cards"] = deck
    return lo


def exam_seeds(key, step_id, n):
    rng = random.Random(f"{key}|{step_id}")
    return [rng.randrange(1, 2 ** 31 - 1) for _ in range(n)]


def search_score(loadout, seed, budget, search_seed):
    s, _ = _W["build"](_W["master"], loadout, seed=seed)
    t0 = time.perf_counter()
    native, rollouts = True, None
    try:
        path, st = _W["ns"].policy_search(s, deadline=budget, seed=search_seed)
        ok = st["status"] == 0 and path
        rollouts = st.get("rollouts")
    except Exception:          # noqa: BLE001
        ok = False
    if ok:
        score = int(_W["sv"].replay_session(s, path).state.score)
    else:
        native = False
        best, path, st = _W["py"](s, deadline=budget, seed=search_seed)
        score = int(best.state.score)
    return score, native, round(time.perf_counter() - t0, 3), rollouts


def _job(args):
    offer, stage_i, option, k, seed, budget, search_seed = args
    stage = offer["stages"][stage_i]
    score, native, secs, rollouts = search_score(option_loadout(offer, stage, option), seed, budget, search_seed)
    return {"key": offer["key"], "stage": stage["step_id"], "option": "base" if option is None else option,
            "k": k, "seed": seed, "budget": budget, "score": score, "native": native, "seconds": secs,
            "rollouts": rollouts}


def _ci(xs):
    n = len(xs)
    if n == 0:
        return None, None
    m = sum(xs) / n
    if n < 2:
        return m, None
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m, 1.96 * sd / math.sqrt(n)


def table(rows):
    """{offer key: {option: {"next": (mean, ci, n), "remaining": (...)}}} of paired deltas."""
    by = defaultdict(dict)
    for r in rows:
        by[r["key"]][(r["stage"], r["option"], r["k"])] = r["score"]
    out = {}
    for key, d in by.items():
        stages = sorted({s for s, _, _ in d}, key=lambda s: ("Mid1" not in s, "Mid2" not in s))
        options = sorted({o for _, o, _ in d if o != "base"})
        res = {}
        for o in options:
            nxt, rem = [], []
            ks = sorted({k for s, oo, k in d if oo == o})
            for k in ks:
                diffs = [d[(s, o, k)] - d[(s, "base", k)] for s in stages
                         if (s, o, k) in d and (s, "base", k) in d]
                if (stages[0], o, k) in d and (stages[0], "base", k) in d:
                    nxt.append(d[(stages[0], o, k)] - d[(stages[0], "base", k)])
                if len(diffs) == len(stages):
                    rem.append(sum(diffs))
            m1, c1 = _ci(nxt)
            m2, c2 = _ci(rem)
            res[o] = {"next": [m1, c1, len(nxt)], "remaining": [m2, c2, len(rem)], "stages": stages}
        out[key] = res
    return out


def _jobs(offer, alive, lo, hi, seeds, budget, search_seed, done_jobs):
    jobs = []
    for si, st in enumerate(offer["stages"]):
        sd = exam_seeds(offer["key"], st["step_id"], seeds)
        jobs += [(offer, si, o, k, sd[k], budget, search_seed) for o in alive for k in range(lo, hi)
                 if (offer["key"], st["step_id"], "base" if o is None else o, k) not in done_jobs]
    return jobs


def evaluate(offers, budget, seeds, workers, search_seed, eliminate=False, out=None, done_jobs=(), per_offer=False):
    """Run every (offer, stage, option in [base] + options, seed)."""
    rows = []
    done_jobs = set(done_jobs)
    with Pool(workers, initializer=_init, initargs=(os.environ.get("GKMS_NATIVE_LIB")
                                                    or str(G / "native" / "build" / "gkmscore.dll"),)) as pool:
        if not (eliminate or per_offer):
            jobs = [j for offer in offers for j in _jobs(offer, [None] + list(range(len(offer["options"]))),
                                                          0, seeds, seeds, budget, search_seed, done_jobs)]
            for r in pool.imap_unordered(_job, jobs):
                rows.append(r)
                if out:
                    out.write(json.dumps(r) + "\n")
                    out.flush()
            return rows
        for offer in offers:
            alive = [None] + list(range(len(offer["options"])))
            done = 0
            batch = 8 if eliminate else seeds
            t0 = time.time()
            while done < seeds and len(alive) > 1:
                hi = min(seeds, done + batch)
                jobs = _jobs(offer, alive, done, hi, seeds, budget, search_seed, done_jobs)
                for r in pool.imap_unordered(_job, jobs):
                    rows.append(r)
                    if out:
                        out.write(json.dumps(r) + "\n")
                done = hi
                if eliminate and done >= 16:
                    t = table([r for r in rows if r["key"] == offer["key"]])[offer["key"]]
                    rem = {o: v["remaining"] for o, v in t.items() if o in alive and v["remaining"][1] is not None}
                    if rem:
                        best = max(rem, key=lambda o: rem[o][0])
                        floor = rem[best][0] - rem[best][1]
                        alive = [None] + [o for o in alive if o is not None and
                                          (o not in rem or rem[o][0] + rem[o][1] >= floor)]
                        if len(alive) == 2:
                            break
            if out:
                out.write(json.dumps({"key": offer["key"], "offer_seconds": round(time.time() - t0, 2),
                                      "workers": workers}) + "\n")
                out.flush()
    return rows


# ----------------------------------------------------------------------------- reports
def _load_rows(path):
    rows, offer_secs = [], {}
    for line in open(path, encoding="utf-8"):
        r = json.loads(line)
        if "offer_seconds" in r:
            offer_secs[r["key"]] = r
        else:
            rows.append(r)
    return rows, offer_secs


def _rank(t, col):
    vals = {o: v[col][0] for o, v in t.items() if v[col][0] is not None}
    return sorted(vals, key=lambda o: -vals[o])


def summary(path, ref_path=None):
    rows, secs = _load_rows(path)
    t = table(rows)
    out = {"file": path, "offers": len(t), "jobs": len(rows),
           "native_share": round(sum(r["native"] for r in rows) / max(1, len(rows)), 4),
           "job_seconds_mean": round(sum(r["seconds"] for r in rows) / max(1, len(rows)), 3),
           "offer_wall_seconds": {k: v["offer_seconds"] for k, v in secs.items()}, "table": t}
    if ref_path:
        ref_rows, _ = _load_rows(ref_path)
        rt = table(ref_rows)
        c2 = {}
        for col in ("next", "remaining"):
            keys = [k for k in t if k in rt and _rank(t[k], col) and _rank(rt[k], col)]
            agree = [k for k in keys if _rank(t[k], col)[0] == _rank(rt[k], col)[0]]
            signs = [(k, o) for k in keys for o in rt[k] if rt[k][o][col][0] and t[k].get(o, {}).get(col, [None])[0]]
            sign_ok = sum((t[k][o][col][0] > 0) == (rt[k][o][col][0] > 0) for k, o in signs)
            c2[col] = {"offers": len(keys), "top1_agree": len(agree),
                       "top1_rate": round(len(agree) / len(keys), 3) if keys else None,
                       "delta_sign_agree": [sign_ok, len(signs)],
                       "disagree": [[k, _rank(t[k], col)[0], _rank(rt[k], col)[0]] for k in keys if k not in agree]}
        per_job = out["job_seconds_mean"]
        jobs_per_offer = defaultdict(int)
        for r in rows:
            jobs_per_offer[r["key"]] += 1
        c2["proxy_seconds_per_offer_at_19_workers"] = {
            k: round(n * per_job / 19, 1) for k, n in jobs_per_offer.items()}
        c2["ref_jobs"] = len(ref_rows)
        c2["ref_native_share"] = round(sum(r["native"] for r in ref_rows) / max(1, len(ref_rows)), 4)
        c2["ref_job_seconds_mean"] = round(sum(r["seconds"] for r in ref_rows) / max(1, len(ref_rows)), 3)
        c2["criteria"] = {"top1_rate": ">= 0.70", "sign_rate": ">= 0.80 (only when skipping is allowed)",
                          "proxy_latency": "<= 60 s per offer, 19 host workers", "offers": ">= 40"}
        out["c2"] = c2
    return out


# ----------------------------------------------------------------------------- C1
def _c1_job(args):
    label, lo, budget, search_seed = args
    s, _ = _W["build"](_W["master"], lo, seed=int(lo["seed"]))
    path, st = _W["ns"].policy_search(s, deadline=budget, seed=search_seed)
    score = int(_W["sv"].replay_session(s, path).state.score) if st["status"] == 0 and path else None
    return {"round": label, "budget": budget, "search_seed": search_seed, "score": score,
            "status": st["status"], "rollouts": st.get("rollouts"), "seconds": round(st["elapsed_seconds"], 3),
            "rollouts_per_second": round(st.get("rollouts", 0) / max(st["elapsed_seconds"], 1e-9), 1)}


def c1(rounds_path, out_path, budgets, seeds, ref, ref_seeds, workers):
    rounds = json.load(open(rounds_path, encoding="utf-8"))["rounds"]
    jobs = [(r["label"], r["loadout"], b, 182 + i) for r in rounds for b in budgets for i in range(seeds)]
    jobs += [(r["label"], r["loadout"], ref, 182 + i) for r in rounds for i in range(ref_seeds)]
    rows = []
    with Pool(workers, initializer=_init, initargs=(os.environ.get("GKMS_NATIVE_LIB")
                                                    or str(G / "native" / "build" / "gkmscore.dll"),)) as pool, \
            open(out_path, "w", encoding="utf-8") as out:
        for r in pool.imap_unordered(_c1_job, jobs):
            rows.append(r)
            out.write(json.dumps(r) + "\n")
            out.flush()
    refs = defaultdict(list)
    for r in rows:
        if r["budget"] == ref and r["score"] is not None:
            refs[r["round"]].append(r["score"])
    print("round budget n score_mean ratio_to_ref rollouts_per_s")
    for label in [r["label"] for r in rounds]:
        best = max(refs[label]) if refs[label] else None
        for b in budgets + [ref]:
            xs = [r for r in rows if r["round"] == label and r["budget"] == b and r["score"] is not None]
            if not xs:
                print(label, b, 0)
                continue
            m = sum(r["score"] for r in xs) / len(xs)
            rps = sum(r["rollouts_per_second"] for r in xs) / len(xs)
            print(label, b, len(xs), round(m), round(m / best, 4) if best else None, round(rps))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("offers"); a.add_argument("ledger"); a.add_argument("loadout_root"); a.add_argument("out")
    b = sub.add_parser("eval"); b.add_argument("offers"); b.add_argument("out")
    b.add_argument("--budget", type=float, default=2.0); b.add_argument("--seeds", type=int, default=32)
    b.add_argument("--workers", type=int, default=os.cpu_count()); b.add_argument("--search-seed", type=int, default=182)
    b.add_argument("--only", default=""); b.add_argument("--shard", default=""); b.add_argument("--eliminate", action="store_true")
    b.add_argument("--per-offer", action="store_true", help="one offer at a time; writes each offer's wall seconds")
    c = sub.add_parser("summary"); c.add_argument("rows"); c.add_argument("ref", nargs="?")
    c.add_argument("--json", default="")
    d = sub.add_parser("c1"); d.add_argument("rounds"); d.add_argument("out")
    d.add_argument("--budgets", default="0.5,1,2,5"); d.add_argument("--seeds", type=int, default=8)
    d.add_argument("--ref", type=float, default=120.0); d.add_argument("--ref-seeds", type=int, default=2)
    d.add_argument("--workers", type=int, default=os.cpu_count())
    x = ap.parse_args()
    if x.cmd == "offers":
        offers, skipped = build_offers(x.ledger, x.loadout_root)
        json.dump({"schema": "gkms.produce_offers.v1", "ledger": os.path.basename(x.ledger), "offers": offers,
                   "skipped": skipped}, open(x.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("offers", len(offers), "skipped", len(skipped), "stages", sum(len(o["stages"]) for o in offers))
    elif x.cmd == "eval":
        offers = json.load(open(x.offers, encoding="utf-8"))["offers"]
        if x.only:
            offers = [o for o in offers if o["key"] in x.only.split(",")]
        if x.shard:
            i, n = (int(v) for v in x.shard.split("/"))
            offers = offers[i::n]
        done = set()
        if os.path.exists(x.out):
            done = {(r["key"], r["stage"], r["option"], r["k"]) for r in _load_rows(x.out)[0]}
        with open(x.out, "a", encoding="utf-8") as out:
            evaluate(offers, x.budget, x.seeds, x.workers, x.search_seed, x.eliminate, out, done, x.per_offer)
    elif x.cmd == "summary":
        res = summary(x.rows, x.ref)
        if x.json:
            json.dump(res, open(x.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(json.dumps({k: v for k, v in res.items() if k != "table"}, ensure_ascii=False, indent=1))
    elif x.cmd == "c1":
        c1(x.rounds, x.out, [float(v) for v in x.budgets.split(",")], x.seeds, x.ref, x.ref_seeds, x.workers)


if __name__ == "__main__":
    main()
