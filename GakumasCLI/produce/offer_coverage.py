# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A5: can the engine play every card a Produce can bring, and every idol's own card and P-item?

    python -B offer_coverage.py OUT.json [--seeds 4] [--workers 2] [--lib PATH] [--only cards|idols]
                                [--cards ID,ID] [--idols ID,ID] [--limit N]
"""
import argparse
import json
import os
import random
import time
import traceback
from collections import Counter
from multiprocessing import Pool
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
E = G = REPO / "GakumasCLI"
MASTER_DIR = E / "ro" / "master" / "tables"
BASELINE_FILE = Path(__file__).resolve().with_name("coverage_baselines.json")
PLAN_SUBTYPES = {"PLAN1": ("PARAMETER_BUFF", "LESSON_BUFF"), "PLAN2": ("REVIEW", "CARD_PLAY_AGGRESSIVE"),
                 "PLAN3": ("CONCENTRATION", "FULL_POWER")}
ALL_SUBTYPES = sum(PLAN_SUBTYPES.values(), ())
OFFER_RARITIES = ("R", "SR", "SSR", "LEGEND")
# effect types the native core handed back to Python; none
NATIVE_PYTHON_ONLY = set()

_W = {}


def _rows(table):
    with open(MASTER_DIR / f"{table}.jsonl", encoding="utf-8") as fh:
        return [json.loads(line)["decoded"] for line in fh if line.strip()]


def _tail(v, sep):
    return v["name"].split(sep)[-1] if isinstance(v, dict) else str(v)


def _subtype(idol_row):
    return _tail(idol_row["examEffectType"], "EFFECT_TYPE_EXAM_")


def card_set():
    """{card id: {"plan", "rarity", "name", "sources": [...]}}, {(id, upgrade): row}, {idol id: row}."""
    rows = {}
    for r in _rows("ProduceCard"):
        rows[(r["id"], r.get("upgradeCount", 0))] = r
    out = {}
    for (cid, up), r in rows.items():
        if up or _tail(r["category"], "CATEGORY_") not in ("ACTIVE_SKILL", "MENTAL_SKILL"):
            continue
        rar = _tail(r["rarity"], "RARITY_")
        if rar in OFFER_RARITIES and not r.get("libraryHidden") and not any(
                r.get(k) for k in ("originIdolCardId", "originSupportCardId", "originCharacterId",
                                   "originPrimaStellaIdolCardId")):
            out.setdefault(cid, {"sources": []})["sources"].append("support_set")
        if r.get("originSupportCardId"):
            out.setdefault(cid, {"sources": []})["sources"].append("support_event")
        if r.get("originCharacterId"):
            out.setdefault(cid, {"sources": []})["sources"].append("character")
    for p in _rows("ProduceCardPool"):
        for x in p.get("produceCardRatios") or ():
            out.setdefault(x["id"], {"sources": []})["sources"].append(p["id"])
    for cid, v in out.items():
        r = rows[(cid, 0)]
        v.update(plan=_tail(r["planType"], "TYPE_"), rarity=_tail(r["rarity"], "RARITY_"), name=r.get("name"))
    idols = {r["id"]: r for r in _rows("IdolCard")}
    return out, rows, idols


def card_variants(cards, rows):
    out = []
    for cid, v in sorted(cards.items()):
        subtypes = ALL_SUBTYPES if v["plan"] == "COMMON" else PLAN_SUBTYPES[v["plan"]]
        for up in (0, 1):
            if (cid, up) not in rows:
                continue
            custom = [None] + (list(rows[(cid, up)].get("produceCardCustomizeIds") or ()) if up == 1 else [])
            for cu in custom:
                for st in subtypes:
                    out.append({"kind": "card", "card": cid, "upgrade": up, "customize": cu, "subtype": st})
    return out


def idol_variants(rows, idols):
    prima = {}
    for (cid, up), r in rows.items():
        if not up and r.get("originPrimaStellaIdolCardId"):
            prima.setdefault(r["originPrimaStellaIdolCardId"], []).append(cid)
    out = []
    for iid, i in sorted(idols.items()):
        unique = [c for c in (i.get("produceCardId"), i.get("secondProduceCardId")) if c]
        before, after = i.get("beforeProduceItemId"), i.get("afterProduceItemId")
        base = {"kind": "idol", "idol": iid, "subtype": _subtype(i), "unique": unique, "name": i.get("name")}
        for target in unique:
            out.append(dict(base, item=before, card=target, upgrade=0, customize=None))
            if (target, 1) in rows:
                out.append(dict(base, item=after, card=target, upgrade=1, customize=None))
                for cu in rows[(target, 1)].get("produceCardCustomizeIds") or ():
                    out.append(dict(base, item=after, card=target, upgrade=1, customize=cu))
        for target in sorted(prima.get(iid, ())):
            for up in (0, 1):
                if (target, up) in rows:
                    out.append(dict(base, item=after, card=target, upgrade=up, customize=None, prima=True))
    return out


def _init(lib):
    os.chdir(E)
    os.environ["GKMS_NATIVE_LIB"] = str(lib)
    from GakumasCLI.native.native_session import NativeUnsupported
    from GakumasCLI.scenario.exam_session import Master
    from GakumasCLI.scenario.hif import build_hif_loadout_session
    from GakumasCLI.engine.semantics import MasterCatalog, implemented_effect_types, implemented_grow_types
    master = Master.load(MASTER_DIR)
    base = json.load(open(BASELINE_FILE, encoding="utf-8"))["baselines"]
    idols = {r["id"]: r for r in _rows("IdolCard")}
    cat = MasterCatalog(MASTER_DIR)
    _W.update(Unsupported=NativeUnsupported, master=master, build=build_hif_loadout_session,
              base=base, idols=idols, cat=cat, eff_ok=implemented_effect_types(), grow_ok=implemented_grow_types())


def _static(v):
    cat = _W["cat"]
    out = {}
    card = cat.cards.get((v["card"], v["upgrade"]))
    if card is None:
        return {"missing_row": True}
    types = set(cat.card_effect_types(card))
    out["missing_effect_types"] = sorted(t for t in types if t not in _W["eff_ok"])
    out["native_python_only_types"] = sorted(types & NATIVE_PYTHON_ONLY)
    out["missing_grow_types"] = []
    if v["customize"]:
        c = cat.customizes.get(v["customize"])
        out["missing_grow_types"] = sorted({cat.grows[g].type_number for g in (c.grow_effect_ids if c else ())
                                            if g in cat.grows and cat.grows[g].type_number not in _W["grow_ok"]})
    if v["kind"] == "idol" and v.get("item"):
        item = cat.items.get(v["item"])
        if item is None:
            out["missing_item_row"] = True
        else:
            itypes = set(cat.item_effect_types(item))
            out["item_missing_effect_types"] = sorted(t for t in itypes if t not in _W["eff_ok"])
            out["item_native_python_only_types"] = sorted(itypes & NATIVE_PYTHON_ONLY)
    return out


def make_loadout(v):
    b = _W["base"][v["subtype"]]
    lo = json.loads(json.dumps(b["loadout"]))
    if v["kind"] == "card":
        in_base = any(c["produce_card_id"] == v["card"] for c in lo["deck_cards"])
        if not in_base or v["upgrade"] or v["customize"]:
            entry = {"produce_card_id": v["card"], "current_upgrade_count": v["upgrade"]}
            if v["customize"]:
                entry["customizes"] = [{"id": v["customize"], "customize_count": 1}]
            lo["deck_cards"].append(entry)
        return lo
    bi = _W["idols"][b["idol_card_id"]]
    drop_items = {bi.get("beforeProduceItemId"), bi.get("afterProduceItemId")}
    drop_cards = {bi.get("produceCardId"), bi.get("secondProduceCardId")} - {None}
    lo["produce_item_ids"] = [x for x in lo["produce_item_ids"] if x not in drop_items]
    lo["deck_cards"] = [c for c in lo["deck_cards"] if c["produce_card_id"] not in drop_cards]
    lo["idol_card_id"] = v["idol"]
    lo["difficulty_id"] = b["difficulty_id"]
    if v.get("item"):
        lo["produce_item_ids"].append(v["item"])
    for cid in v["unique"] + ([v["card"]] if v.get("prima") else []):
        entry = {"produce_card_id": cid,
                 "current_upgrade_count": v["upgrade"] if cid == v["card"] else int(v.get("item") == _W["idols"][v["idol"]].get("afterProduceItemId"))}
        if cid == v["card"] and v["customize"]:
            entry["customizes"] = [{"id": v["customize"], "customize_count": 1}]
        lo["deck_cards"].append(entry)
    return lo


def run_variant(args):
    v, seeds = args
    res = dict(v, static=_static(v), played=0, rollouts=0, python_errors=[], unimplemented=[], native_refused=[],
               handbacks=Counter(), mismatches=[], scores=[])
    t0 = time.time()
    try:
        lo = make_loadout(v)
    except Exception as exc:          # noqa: BLE001
        res["python_errors"].append(f"loadout: {type(exc).__name__}: {str(exc)[:160]}")
        seeds = 0
    for k in range(seeds):
        rng = random.Random(k)
        try:
            s, _ = _W["build"](_W["master"], lo, seed=int(lo["seed"]) + k)
        except _W["Unsupported"] as exc:
            res["native_refused"].append(f"open: {str(exc)[:160]}")
            break
        except Exception as exc:
            res["python_errors"].append(f"build: {type(exc).__name__}: {str(exc)[:160]}")
            break
        res["rollouts"] += 1
        n = 0
        try:
            while not s.finished:
                la = sorted(s.legal_actions())
                first = [x for x in la if x[0] == "play" and s.deck.hand[x[1]]["produce_card_id"] == v["card"]]
                a = first[0] if first else rng.choice([x for x in la if x[0] != "end"] or la)
                res["played"] += bool(first)
                s.step(a, observe=False)
                n += 1
            res["scores"].append(int(s.state.score))
        except _W["Unsupported"] as exc:
            res["native_refused"].append(f"seed {k} step {n}: {str(exc)[:120]}")
        except Exception as exc:
            tb = traceback.extract_tb(exc.__traceback__)[-1]
            res["python_errors"].append(f"seed {k} step {n}: {type(exc).__name__}: {str(exc)[:160]} "
                                        f"@{Path(tb.filename).name}:{tb.lineno}")
    res["handbacks"] = dict(res["handbacks"])
    res["seconds"] = round(time.time() - t0, 2)
    return res


def _summary(out):
    def n(key):
        return sum(1 for r in out if r[key])
    st = [r["static"] for r in out]
    return {"variants": len(out), "rollouts": sum(r["rollouts"] for r in out),
            "never_played": sum(1 for r in out if not r["played"]),
            "static_missing_effect": sorted({(r["card"], r["upgrade"]) for r, s in zip(out, st) if s.get("missing_effect_types")}),
            "static_missing_grow": sorted({(r["card"], r["customize"]) for r, s in zip(out, st) if s.get("missing_grow_types")}),
            "static_native_python_only": sorted({(r["card"], tuple(s["native_python_only_types"])) for r, s in zip(out, st)
                                                 if s.get("native_python_only_types")}),
            "item_missing_effect": sorted({(r.get("item"), tuple(s["item_missing_effect_types"])) for r, s in zip(out, st)
                                           if s.get("item_missing_effect_types")}),
            "item_native_python_only": sorted({(r.get("item"), tuple(s["item_native_python_only_types"])) for r, s in zip(out, st)
                                               if s.get("item_native_python_only_types")}),
            "python_errors": n("python_errors"), "unimplemented": n("unimplemented"),
            "native_refused": n("native_refused"), "handbacks": n("handbacks"), "mismatches": n("mismatches")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--seeds", type=int, default=4)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only", choices=("cards", "idols"), default=None)
    ap.add_argument("--cards", default="", help="only these card ids (comma separated)")
    ap.add_argument("--idols", default="", help="only these idol card ids (comma separated)")
    ap.add_argument("--lib", default=os.environ.get("GKMS_NATIVE_LIB") or str(G / "native" / "build" / "gkmscore.dll"))
    a = ap.parse_args()
    cards, rows, idols = card_set()
    if a.cards:
        cards = {k: v for k, v in cards.items() if k in a.cards.split(",")}
    if a.idols:
        idols = {k: v for k, v in idols.items() if k in a.idols.split(",")}
    vs = ([] if a.only == "idols" else card_variants(cards, rows)) + ([] if a.only == "cards" else idol_variants(rows, idols))
    if a.limit:
        vs = vs[:a.limit]
    print("cards", len(cards), "idols", len(idols), "variants", len(vs), flush=True)
    t0 = time.time()
    out = []
    with Pool(a.workers, initializer=_init, initargs=(a.lib,)) as pool:
        for i, r in enumerate(pool.imap(run_variant, [(v, a.seeds) for v in vs], chunksize=4)):
            out.append(r)
            if (i + 1) % 200 == 0:
                print(i + 1, "done", round(time.time() - t0), "s", flush=True)
    base = json.load(open(BASELINE_FILE, encoding="utf-8"))["baselines"]
    summary = {"cards": _summary([r for r in out if r["kind"] == "card"]),
               "idols": _summary([r for r in out if r["kind"] == "idol"]),
               "card_count": len(cards), "idol_count": len(idols),
               "baselines": {k: v["idol_card_id"] + " " + v["source"] for k, v in base.items()},
               "seeds": a.seeds, "lib": a.lib, "seconds": round(time.time() - t0)}
    json.dump({"summary": summary, "cards": cards, "variants": out}, open(a.out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=list)
    short = {k: ({kk: (vv if not isinstance(vv, list) else len(vv)) for kk, vv in v.items()} if isinstance(v, dict) and k in ("cards", "idols") else v)
             for k, v in summary.items() if k not in ("baselines", "lib")}
    print(json.dumps(short, ensure_ascii=False))


if __name__ == "__main__":
    main()
