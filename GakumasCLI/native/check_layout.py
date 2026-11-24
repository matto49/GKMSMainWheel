# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""layout roundtrip over every step-boundary state.

    python -B GakumasCLI/gkms.py native.check_layout --engine <GakumasCLI> --plain <plain> --random K --out <dir>
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path


def collect_strings(v, acc):
    if isinstance(v, tuple) and v and isinstance(v[0], str) and v[0].startswith("$"):
        if v[0] == "$ref":
            acc.add(v[1].split("|", 1)[1])
            return
        for x in v[1]:
            if v[0] in ("$rec", "$map"):
                collect_strings(x[0], acc)
                collect_strings(x[1], acc)
            else:
                collect_strings(x, acc)
    elif isinstance(v, list):
        for x in v:
            collect_strings(x, acc)
    elif isinstance(v, str):
        acc.add(v)


def master_strings(master):
    acc = set()
    for name in ("cards", "effects", "triggers", "enchants", "searches", "customizes", "grow_effects",
                 "items", "item_effects", "card_pools", "drinks", "drink_effects", "gimmicks"):
        t = getattr(master, name, None) or {}
        for k in t:
            acc.add(k[0] if isinstance(k, tuple) else str(k))
    return acc


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", required=True)
    ap.add_argument("--plain", required=True)
    ap.add_argument("--random", type=int, default=2)
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    plain = str(Path(args.plain).resolve())
    engine = str(Path(args.engine).resolve())
    os.chdir(engine)
    from GakumasCLI.scenario.hif_replay_import import load_plain_rounds as load_rounds, build_plain_round as build
    from GakumasCLI.native import portable as P, layout as L
    from GakumasCLI.scenario.exam_session import Master

    master = Master.load(Path("ro/master/tables"))
    base = P.master_refmap(master)
    rows = load_rounds(plain)
    keys = sorted(rows)
    if args.limit:
        keys = keys[: args.limit]
    states = []
    acc = master_strings(master)
    t0 = time.time()
    for ri, key in enumerate(keys):
        row = rows[key]
        b = build(master, row)
        routes = [[tuple(a) for a in row["actions"] if a[0] != "select"]]
        rng = random.Random(4242 + ri)
        for _ in range(args.random):
            routes.append(None)
        for acts in routes:
            s = b.clone()
            rm = P.session_refmap(s, base)
            enc = P.encode_session(s, rm)
            states.append((key[0], enc))
            i = 0
            while not s.finished:
                if acts is not None:
                    if i >= len(acts):
                        break
                    a = acts[i]
                    i += 1
                else:
                    la = s.legal_actions()
                    plays = [x for x in la if x[0] != "end"]
                    a = rng.choice(plays) if plays and rng.random() < 0.9 else ("end",)
                s.step(a, observe=False)
                states.append((key[0], P.encode_session(s, rm)))
    for _, enc in states:
        v, _ = P.decode(enc)
        collect_strings(v, acc)
    strings = sorted(acc)
    (out / "strings.json").write_text(json.dumps(strings, ensure_ascii=False, indent=0), encoding="utf-8")
    ctx = L.Ctx(strings)
    ok = 0
    fails = Counter()
    first = {}
    for seed, enc in states:
        ctx.round_seed = seed
        v, _ = P.decode(enc)
        try:
            g = L.pack(v, ctx)
        except L.Unsupported as exc:
            fails[str(exc)[:120]] += 1
            continue
        back = P.encode_decoded(L.unpack(g, ctx))
        if back == enc:
            ok += 1
        else:
            d = P.diff(enc, back, 5)
            fails["roundtrip:" + str(d[0][0])] += 1
            first.setdefault("roundtrip", d)
    report = {"states": len(states), "roundtrip_ok": ok, "failures": dict(fails.most_common(30)),
              "first_diff": repr(first)[:2000], "strings": len(strings),
              "state_size": L.C.sizeof(L.GkmsState), "seconds": round(time.time() - t0, 1)}
    (out / "check_layout.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    (out / "gkms_state.h").write_text(L.gen_header(), encoding="utf-8")
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0 if ok == len(states) else 1


if __name__ == "__main__":
    sys.exit(main())
