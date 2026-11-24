# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The simulator on the command line: play a 選抜 by typing option numbers, or let a policy play it.

    python -B -m GKMSPS.ps.cli play FORMATION.json [--timestamp MS | --root HEX] [--save STATE.json]
    python -B -m GKMSPS.ps.cli auto FORMATION.json [--timestamp MS | --root HEX] [--policy SEED] [--replay OUT.json]
    python -B -m GKMSPS.ps.cli resume STATE.json
    python -B -m GKMSPS.ps.cli verify REPLAY.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time

from GKMSPS.ps import replay as R
from GKMSPS.ps.rng import Rng
from GKMSPS.ps.sim import Sim


def greedy_exam_index(sim, opts):
    best, best_i = None, None
    for i, o in enumerate(opts):
        if o.get("disabled"):
            continue
        if o["a"][0] == "end":
            score = -1
        else:
            c = sim.exam.clone()
            c.step(tuple(o["a"]), observe=False)
            score = int(c.state.score)
        if best is None or score > best:
            best, best_i = score, i
    return best_i


def sensible_choices(sim, dec, opts, ok, pol):
    """A smoke policy that plays like a person would at a glance: take offered cards (no reroll / exclude), work
    rather than rest while stamina lasts, buy cards in a shop."""
    k = dec["kind"]
    if k == "offer":
        return [j for j in ok if "pick" in opts[j]] or ok
    if k == "day":
        low = sim.s["stamina"] < 14
        work = [j for j in ok if opts[j].get("do") != "refresh"]
        return [j for j in ok if opts[j].get("do") == "refresh"] if low and len(work) < len(ok) else (work or ok)
    if k == "shop":
        buy = [j for j in ok if "buy" in opts[j] and sim.s["shop"]["shelf"][opts[j]["buy"]]["what"] == "card"]
        return buy or [j for j in ok if opts[j].get("leave")] or ok
    if k == "interval":
        buy = [j for j in ok if "slot" in opts[j] and sim.s["shop"]["shelf"][opts[j]["slot"]]["what"] in ("card", "upgrade")]
        return buy or [j for j in ok if opts[j].get("leave")] or ok
    return ok


def auto(sim, policy_seed=0, limit=20000, engine=False, budget=2.0, sensible=False):
    pol = Rng(hashlib.sha256(b"GKMS-PS-policy" + str(policy_seed).encode()).digest())("policy")
    hashes = []
    n = 0
    while not sim.s["done"] and n < limit:
        dec = sim.decision()
        if dec is None:
            break
        opts = dec["options"]
        if dec["kind"] == "exam":
            if engine:
                sim.exam_auto(budget)
                n += 1
                continue
            i = greedy_exam_index(sim, opts)
        elif dec["kind"] == "lost":
            i = 0 if not opts[0].get("disabled") else 1
        elif dec["kind"] == "passed":
            i = 0
        else:
            ok = [j for j, o in enumerate(opts) if not o.get("disabled")]
            if sensible:
                ok = sensible_choices(sim, dec, opts, ok, pol)
            if dec["kind"] in ("shop", "customize", "interval") and pol.uniform(3) == 0:
                ok = [j for j in ok if opts[j].get("leave")] or ok
            i = ok[pol.uniform(len(ok))]
        sim.act(i)
        n += 1
    return sim.trace


def show(sim):
    s = sim.s
    print(f"\n[{s['produce_id']} 第 {s.get('step')} 天] Vo {s['params']['vocal']} Da {s['params']['dance']} "
          f"Vi {s['params']['visual']} 体力 {s['stamina']}/{s['max_stamina']} P点 {s['pp']} 星 {s['star']} "
          f"饮料 {len(s['drinks'])}/{s['drink_limit']} 牌组 {len(sim._deck())}")
    dec = sim.decision()
    if dec is None:
        print("结束：", json.dumps(s.get("result"), ensure_ascii=False)[:300])
        return None
    if dec["kind"] == "exam":
        from GKMSPS.ps import exam as X
        v = X.view(sim.exam)
        print(f"  考试 回合 {v['turn']}/{v['limit_turn']} 分数 {v['score']:,} 体力 {v['stamina']}")
    print("  " + (dec["title"] or dec["kind"]))
    for i, o in enumerate(dec["options"]):
        print(f"   {i:2d}. {o['label']}" + (f"  [不可：{o['disabled']}]" if o.get("disabled") else ""))
    return dec


def main(argv):
    ap = argparse.ArgumentParser(prog="GKMSPS.ps.cli")
    ap.add_argument("cmd", choices=("play", "auto", "resume", "verify", "honsen"))
    ap.add_argument("path")
    ap.add_argument("--timestamp", type=int)
    ap.add_argument("--root")
    ap.add_argument("--policy", type=int, default=0)
    ap.add_argument("--save")
    ap.add_argument("--replay")
    ap.add_argument("--engine", action="store_true", help="exams played by the engine (auto) / offered on the exam screen")
    ap.add_argument("--budget", type=float, default=2.0)
    ap.add_argument("--sensible", action="store_true", help="the smoke policy takes cards and works instead of random choices")
    ap.add_argument("--then-honsen", action="store_true", help="auto: go on into a 本戦 from the selection memory made")
    ap.add_argument("--auto", action="store_true", help="honsen: let the policy play it")
    a = ap.parse_args(argv[1:])
    if a.cmd == "verify":
        print(json.dumps(R.verify(json.load(open(a.path, encoding="utf-8"))), ensure_ascii=False))
        return
    if a.cmd == "resume":
        sim = Sim.from_json(open(a.path, encoding="utf-8").read())
    elif a.cmd == "honsen":
        sm = json.load(open(a.path, encoding="utf-8"))
        sim = Sim.honsen(sm, timestamp_ms=a.timestamp if a.timestamp is not None else int(time.time() * 1000),
                         root=a.root, engine=a.engine)
        if a.auto:
            a.cmd = "auto"
    else:
        f = json.load(open(a.path, encoding="utf-8"))
        sim = Sim(f, timestamp_ms=a.timestamp if a.timestamp is not None else int(time.time() * 1000), root=a.root,
                  engine=a.engine)
    print("种子码", sim.s["short"], sim.s["root"])
    if a.cmd == "auto":
        t = time.time()
        hashes = auto(sim, a.policy, engine=a.engine, budget=a.budget, sensible=a.sensible)
        print(json.dumps({"actions": len(sim.s["actions"]), "seconds": round(time.time() - t, 1),
                          "result": (sim.s.get("result") or {}).get("outcome"), "params": sim.s["params"],
                          "star": sim.s["star"], "auditions": {k: (v["score"], v["rank"]) for k, v in sim.s["auditions"].items()},
                          "unmodelled": sorted({x["text"] for x in sim.s["log"] if x.get("kind") == "unmodelled"})},
                         ensure_ascii=False))
        if a.replay:
            with open(a.replay, "w", encoding="utf-8") as fh:
                fh.write(R.dumps(R.record(sim, hashes)))
        res = sim.s.get("result") or {}
        if a.then_honsen and res.get("outcome") == "selection_memory":
            hon = Sim.honsen(res["selection_memory"], timestamp_ms=(a.timestamp or 0) + 1, engine=a.engine)
            auto(hon, a.policy, engine=a.engine, budget=a.budget, sensible=a.sensible)
            r2 = hon.s.get("result") or {}
            print(json.dumps({"honsen_actions": len(hon.s["actions"]), "result": r2.get("outcome"),
                              "rating": r2.get("rating"), "grade": r2.get("grade"), "params": hon.s["params"],
                              "auditions": {k: (v["score"], v["rank"]) for k, v in hon.s["auditions"].items()},
                              "memory": {k: (r2.get("memory") or {}).get(k) for k in
                                         ("grade", "produce_card", "produce_card_phase_type", "abilities",
                                          "exam_battle_produce_item_ids")},
                              "unmodelled": sorted({x["text"] for x in hon.s["log"] if x.get("kind") == "unmodelled"})},
                             ensure_ascii=False)[:2500])
            if a.replay:
                with open(a.replay.replace(".json", "_honsen.json"), "w", encoding="utf-8") as fh:
                    fh.write(R.dumps(R.record(hon)))
        return
    while True:
        dec = show(sim)
        if dec is None:
            break
        try:
            x = input("选择> ").strip()
        except EOFError:
            break
        if x in ("q", "quit"):
            break
        try:
            sim.act(int(x))
        except (ValueError, Exception) as exc:                     # noqa: BLE001
            print("  不行：", exc)
        if a.save:
            open(a.save, "w", encoding="utf-8").write(sim.to_json())


if __name__ == "__main__":
    main(sys.argv)
