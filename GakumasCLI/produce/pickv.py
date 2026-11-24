# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""the H.I.F card picks by V, the card frequency of the idol card's top-100 ranking records.

    python -B GakumasCLI/gkms.py produce.pickv build OUTDIR --ranking DIR [--templates DIR ...] [--idol ID ...]
    python -B GakumasCLI/gkms.py produce.pickv validate OUTDIR --ranking DIR [--k1a K1A.json]
    python -B GakumasCLI/gkms.py produce.pickv show IDOL [--dir OUTDIR]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import threading
import time
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parent
REPO = PACKAGE.parent

FORMAT = "gkms.pickv.v1"
ALGO = 2
SWAP_K = 8
ALPHA = 10.0
TOP_RECORDS, TOP_WEIGHT, REST_WEIGHT = 50, 2.0, 1.0
TAU_GRID = (0.05, 0.1, 0.15, 0.2, 0.3, 0.5)
TAU = 0.05
REROLL_GAIN = 0.1
V_TIE = 0.05
X_TIE = 0.25
ST_Z = 1.96
MIN_STORED = 0.005
OFFER_SIZE = 3
# reroll: an offer's rarity shares
OFFER_RARITY = {"produce-007": {"R": 0.342, "SR": 0.503, "SSR": 0.156},
                "produce-008": {"R": 0.231, "SR": 0.505, "SSR": 0.264}}
PACK_DIR = PACKAGE / "data" / "pickv"
CACHE_DIR = PACKAGE / "data" / "pickv_cache"
UNIQUE = "-ido-"
_LOCK = threading.Lock()
_PACKS = {}
_FETCHING = set()


# -- master ----------------------------------------------------------------------------------------------------------
def _master_dir(master_dir=None):
    import GakumasCLI.paths as P
    return Path(master_dir or P.MASTER_DIR)


def _rows(name, master_dir=None):
    with open(_master_dir(master_dir) / f"{name}.jsonl", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                r = json.loads(line)
                yield r.get("decoded") or r


def _nm(x):
    return x.get("name") if isinstance(x, dict) else x


_CARDS = {}


def card_table(master_dir=None):
    """{card id: {rarity, plan, support, kind}} of the master's +0 rows (cached by directory)."""
    key = str(_master_dir(master_dir))
    if key not in _CARDS:
        out = {}
        for r in _rows("ProduceCard", master_dir):
            if r.get("upgradeCount"):
                continue
            cid = r["id"]
            cat = _nm(r.get("category")) or ""
            kind = ("unique" if UNIQUE in cid else "trouble" if cat.endswith("TROUBLE") else
                    "support" if r.get("originSupportCardId") else "basic" if "-0_" in cid else "pool")
            out[cid] = {"rarity": (_nm(r.get("rarity")) or "").rsplit("_", 1)[-1], "plan": _nm(r.get("planType")),
                        "support": r.get("originSupportCardId"), "kind": kind}
        _CARDS[key] = out
    return _CARDS[key]


def master_digest(master_dir=None):
    """The first 12 hex characters of ProduceCard.jsonl's sha256: a pack built on another master is stale."""
    h = hashlib.sha256((_master_dir(master_dir) / "ProduceCard.jsonl").read_bytes()).hexdigest()
    return h[:12]


def idol_plans(master_dir=None):
    return {r["id"]: _nm(r.get("planType")) for r in _rows("IdolCard", master_dir)}


def exam_rules_version():
    try:
        from GakumasCLI.native import diffrun
        return int(diffrun.MIN_EXAM_RULES)
    except Exception:                                 # noqa: BLE001
        return None


# -- the records -------------------------------------------------------------------------------------------------------
def records_by_idol(rows):
    """{idol: [record]} best first (the ranking's rank, then the score), from hif_histories.histories rows."""
    out = defaultdict(list)
    for r in rows:
        out[r["idol_card_id"]].append(r)
    for idol, rs in out.items():
        rs.sort(key=lambda r: (min((e.get("rank") or 10 ** 6) for e in (r.get("ranking") or [{}])), -(r.get("score") or 0)))
    return dict(out)


def weights(n):
    """w_r of n records best first (module docstring)."""
    top = TOP_RECORDS if n >= 2 * TOP_RECORDS else (n + 1) // 2
    return [TOP_WEIGHT if i < top else REST_WEIGHT for i in range(n)]


def n_eff(ws):
    s, s2 = sum(ws), sum(w * w for w in ws)
    return s * s / s2 if s2 else 0.0


def deck_of(record):
    """[(card id, upgrade, customizes tuple)] of a record's final deck (every source: picked, memory, start)."""
    out = []
    for pc in record.get("produce_cards") or ():
        if not isinstance(pc, dict):
            continue
        c = pc.get("produce_card") or pc
        if not isinstance(c, dict) or not c.get("id"):
            continue
        cz = tuple(sorted((x.get("id"), int(x.get("customize_count") or 1)) for x in c.get("customizes") or ()))
        out.append((c.get("id"), int(c.get("upgrade_count") or 0), cz))
    return out


def supports_of(record):
    return {s.get("id") for s in record.get("support_cards") or ()}


class _Tally:
    """Weighted counts of one set of records: copies >= k per card, holders' upgrades and customizations, and the
    weight of the records holding each support."""

    def __init__(self):
        self.total = 0.0
        self.k = defaultdict(lambda: defaultdict(float))
        self.up = defaultdict(float)
        self.cz = defaultdict(Counter)
        self.sup = defaultdict(float)
        self.supk = defaultdict(lambda: defaultdict(lambda: defaultdict(float)))

    def add(self, record, w, table):
        self.total += w
        sups = supports_of(record)
        for s in sups:
            self.sup[s] += w
        copies = Counter()
        upgraded, czs = set(), defaultdict(Counter)
        for cid, up, cz in deck_of(record):
            copies[cid] += 1
            if up:
                upgraded.add(cid)
            if cz:
                czs[cid][cz] += 1
        for cid, n in copies.items():
            row = table.get(cid) or {}
            for k in range(1, n + 1):
                self.k[cid][k] += w
                if row.get("support") in sups:
                    self.supk[row["support"]][cid][k] += w
            if cid in upgraded:
                self.up[cid] += w
            if czs[cid]:
                self.cz[cid][czs[cid].most_common(1)[0][0]] += w

    def share(self, cid, k, support=None):
        """(h_k, denominator weight)."""
        if support:
            den = self.sup.get(support, 0.0)
            return ((self.supk[support][cid].get(k, 0.0) / den) if den else 0.0), den
        return ((self.k[cid].get(k, 0.0) / self.total) if self.total else 0.0), self.total

    def minus(self, other):
        """This tally without OTHER's records (the plan's pool without the idol card itself)."""
        out = _Tally()
        out.total = self.total - other.total
        for c, ks in self.k.items():
            for k, w in ks.items():
                out.k[c][k] = w - other.k[c].get(k, 0.0)
        for s, w in self.sup.items():
            out.sup[s] = w - other.sup.get(s, 0.0)
        for s, cs in self.supk.items():
            for c, ks in cs.items():
                for k, w in ks.items():
                    out.supk[s][c][k] = w - other.supk[s][c].get(k, 0.0)
        return out


def _ws_neff(ws, support_records):
    """n_eff of the records a support card's events count over (their weights), else of all."""
    return n_eff(support_records) if support_records is not None else n_eff(ws)


def build_packs(rows, master_dir=None, snapshot=None, idols=None):
    """{idol: pack} of every idol card with records (or IDOLS)."""
    table = card_table(master_dir)
    plans = idol_plans(master_dir)
    by_idol = records_by_idol(rows)
    tallies, sup_weights, ws_of = {}, {}, {}
    for idol, recs in by_idol.items():
        ws = weights(len(recs))
        t = _Tally()
        per_sup = defaultdict(list)
        for r, w in zip(recs, ws):
            t.add(r, w, table)
            for s in supports_of(r):
                per_sup[s].append(w)
        tallies[idol], sup_weights[idol], ws_of[idol] = t, per_sup, ws
    plan_tally = {}
    for idol, t in tallies.items():
        p = plans.get(idol)
        pt = plan_tally.setdefault(p, _Tally())
        pt.total += t.total
        for c, ks in t.k.items():
            for k, w in ks.items():
                pt.k[c][k] += w
        for s, w in t.sup.items():
            pt.sup[s] += w
        for s, cs in t.supk.items():
            for c, ks in cs.items():
                for k, w in ks.items():
                    pt.supk[s][c][k] += w
    digest, rules = master_digest(master_dir), exam_rules_version()
    out = {}
    for idol, t in tallies.items():
        if idols and idol not in idols:
            continue
        plan = plans.get(idol)
        prior = plan_tally[plan].minus(t)
        ne = n_eff(ws_of[idol])
        cards = {}
        for cid in set(t.k) | set(prior.k):
            row = table.get(cid)
            if row is None or row["kind"] in ("unique", "trouble"):
                continue
            if row["plan"] not in (plan, "PRODUCE_PLAN_TYPE_COMMON"):
                continue
            sup = row["support"] if row["kind"] == "support" else None
            kmax = max(list(t.k[cid]) + list(prior.k[cid]) + [1])
            hs, raw = [], []
            for k in range(1, kmax + 1):
                h, _den = t.share(cid, k, sup)
                H, _pden = prior.share(cid, k, sup)
                ne_k = _ws_neff(ws_of[idol], sup_weights[idol].get(sup) if sup else None)
                hs.append(round((ne_k * h + ALPHA * H) / (ne_k + ALPHA), 4))
                raw.append(round(t.k[cid].get(k, 0.0) if not sup else t.supk[sup][cid].get(k, 0.0), 1))
            while len(hs) > 1 and hs[-1] < MIN_STORED:
                hs.pop()
                raw.pop()
            if hs[0] < MIN_STORED:
                continue
            held = t.k[cid].get(1, 0.0)
            entry = {"v": hs, "w": raw, "kind": row["kind"], "rarity": row["rarity"]}
            if sup:
                entry["support"] = sup
                entry["of"] = round(t.sup.get(sup, 0.0), 1)
            if held:
                entry["u"] = round(t.up.get(cid, 0.0) / held, 3)
                if t.cz.get(cid):
                    cz, w = t.cz[cid].most_common(1)[0]
                    entry["cz"] = [list(x) for x in cz]
                    entry["cz_share"] = round(w / held, 3)
            cards[cid] = entry
        pool = defaultdict(list)
        for cid, row in table.items():
            if row["kind"] == "pool" and row["plan"] in (plan, "PRODUCE_PLAN_TYPE_COMMON") and row["rarity"] in ("R", "SR", "SSR"):
                pool[row["rarity"]].append(cid)
        out[idol] = {"format": FORMAT, "algo": ALGO, "idol": idol, "plan": plan, "records": len(by_idol[idol]),
                     "weight": t.total, "n_eff": round(ne, 2), "alpha": ALPHA,
                     "weights": f"best {TOP_RECORDS} x{TOP_WEIGHT:g}, rest x{REST_WEIGHT:g}",
                     "snapshot": snapshot, "master": digest, "exam_rules": rules,
                     "pool": {k: sorted(v) for k, v in sorted(pool.items())}, "cards": dict(sorted(cards.items()))}
    return out


def ranking_rows(ranking):
    """hif_histories rows of a ranking store, read with a card projection that keeps a deck card whose customization the
    capture chain's strict projection refuses (the capture decoder produce_history._project_produce_card drops the
    whole card: 1,452 deck cards in 1,418 of the 15, mostly one with a 328-byte customize).
    """
    from GakumasCLI.produce import hif_histories as HH
    _parse, PH = HH._wire_decoder()
    strict = PH._project_produce_card

    def tolerant(data):
        card = strict(data)
        if card is not None:
            return card
        fields = PH._index(data, allowed={1, 2, 3, 100}, repeated={3})
        if fields is None:
            return None
        try:
            cid = PH._text(PH._one(fields, 1, required=True), data)
            up = PH._varint(PH._one(fields, 2), default=0)
        except ValueError:
            return None
        if not cid or not str(cid).startswith("p_card-") or up is None:
            return None
        cz = []
        for f in fields.get(3, []):
            item = PH._project_customize(f.value_bytes(data)) if f.wire_type == 2 else None
            if item is not None:
                cz.append(item)
        return {"id": cid, "upgrade_count": up, "produce_card_skin_id": "", "customizes": cz}

    PH._project_produce_card = tolerant
    try:
        return list(HH.histories(root="none", ranking=str(ranking)))
    finally:
        PH._project_produce_card = strict


def ranking_snapshot(ranking):
    """{records, sha12} of the ranking store's records.jsonl (what a pack was built from)."""
    p = Path(ranking) / "records.jsonl"
    if not p.is_file():
        return None
    data = p.read_bytes()
    return {"records": data.count(b"\n"), "sha12": hashlib.sha256(data).hexdigest()[:12]}


def write_packs(packs, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index = {}
    for idol, pack in sorted(packs.items()):
        pack["swap_k"] = len(pack.get("swap") or ())
        text = json.dumps(pack, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        (out_dir / f"{idol}.json").write_text(text + "\n", encoding="utf-8")
        index[idol] = {"records": pack["records"], "cards": len(pack["cards"]), "bytes": len(text) + 1,
                       "route_ref": bool(pack.get("route_ref")), "st": (pack.get("st") or {}).get("source"),
                       "swap": len(pack.get("swap") or ())}
    some = next(iter(packs.values()), {})
    meta = {"format": FORMAT, "algo": ALGO, "snapshot": some.get("snapshot"), "master": some.get("master"),
            "exam_rules": some.get("exam_rules"), "idols": index}
    (out_dir / "index.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                                        encoding="utf-8")
    return meta


# -- loading on a device -------------------------------------------------------------------------------------------
def _hub():
    try:
        import GakumasCLI.paths as P
        hub = os.environ.get("GKMS_HUB_URL") or P.setting("GKMS_HUB_URL")
        if not hub:
            from GakumasCLI.station import device_config as DC
            cfg = DC.load(DC.config_path(kind="station"))
            hub = cfg.get("hub") or next(iter(DC.hub_list(cfg)), None)
        return hub.rstrip("/") if hub else None
    except Exception:                                 # noqa: BLE001
        return None


def _fresh(pack, master_dir=None):
    if not pack or pack.get("format") != FORMAT or pack.get("algo") != ALGO or "swap_k" not in pack:
        return False
    try:
        return pack.get("master") == master_digest(master_dir)
    except OSError:
        return True


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _fetch(idol, hub, cache_dir):
    try:
        with urllib.request.urlopen(f"{hub}/api/pickv/{idol}", timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("format") == FORMAT and data.get("idol") == idol:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            (Path(cache_dir) / f"{idol}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with _LOCK:
                _PACKS.pop(idol, None)
    except Exception:                                 # noqa: BLE001
        pass
    finally:
        with _LOCK:
            _FETCHING.discard(idol)


def pack_for(idol, pack_dir=None, cache_dir=None, fetch=True, master_dir=None):
    """(pack, state): state "ok", "stale" (built on another master), "loading" (being fetched from the website) or
    "none".
    """
    if not idol:
        return None, "none"
    with _LOCK:
        hit = _PACKS.get(idol)
    if hit is not None:
        return hit
    found, state = None, "none"
    for d in (Path(pack_dir or PACK_DIR), Path(cache_dir or CACHE_DIR)):
        pack = _read(d / f"{idol}.json")
        if pack is None:
            continue
        if _fresh(pack, master_dir):
            found, state = pack, "ok"
            break
        state = "stale"
    if found is None and fetch:
        hub = _hub()
        if hub:
            with _LOCK:
                start = idol not in _FETCHING
                _FETCHING.add(idol)
            if start:
                threading.Thread(target=_fetch, args=(idol, hub, cache_dir or CACHE_DIR), daemon=True).start()
            if state == "none":
                state = "loading"
            return None, state
    with _LOCK:
        _PACKS[idol] = (found, state)
    return found, state


def clear_cache():
    with _LOCK:
        _PACKS.clear()


STATE_TEXT = {"ok": None, "stale": "选卡包已过期", "loading": "选卡包下载中", "none": "没有选卡包"}


# -- V of cards and options ------------------------------------------------------------------------------------------
def v_of(pack, cid, k):
    """V_k of card CID (k >= 1); 0.0 for a card of the plan's pool no top record keeps that many of; None when the
    pack does not know the card at all (a unique card, or one outside the pack's plan and pool)."""
    if not pack or k < 1:
        return None
    e = (pack.get("cards") or {}).get(cid)
    if e is not None:
        vs = e["v"]
        return vs[k - 1] if k <= len(vs) else 0.0
    if UNIQUE in (cid or ""):
        return None
    in_pool = any(cid in ids for ids in (pack.get("pool") or {}).values())
    return 0.0 if in_pool else None


def held_counts(deck):
    """{card id: copies} of deck keys "<id>+<upgrade>" (or dicts with produce_card_id)."""
    out = Counter()
    for c in deck or ():
        cid = c.get("produce_card_id") if isinstance(c, dict) else str(c).split("+")[0]
        if cid:
            out[cid] += 1
    return out


def _cid(res):
    if isinstance(res, dict):
        return res.get("id")
    return str(res).split("+")[0] if res else None


def option_v(o, pack, held):
    """V's value of one hif_advice plan option, with its reading: {v, kind, card, k, text} or None."""
    lab = o.get("label") or ""
    act = lab.split(" ", 1)[0]
    if o.get("kind") != "card":
        return None
    if act in ("take", "buy"):
        cid = _cid(o.get("resource"))
        k = held.get(cid, 0) + 1
        v = v_of(pack, cid, k)
        if v is None:
            return None
        x = {"v": v, "kind": "take", "card": cid, "k": k, "text": reading(pack, cid, k),
             "unstable": _unstable(pack, cid, k)}
        st = st_of(pack, o.get("resource")) if k == 1 else None
        if st is not None:
            x["st"] = st
        return x
    if act == "change":
        cin, cout = _cid(o.get("resource")), _cid(o.get("removed"))
        kin, kout = held.get(cin, 0) + 1, max(1, held.get(cout, 0))
        vin, vout = v_of(pack, cin, kin), v_of(pack, cout, kout)
        if vin is None or vout is None:
            return None
        x = {"v": round(vin - vout, 4), "kind": "change", "card": cin, "k": kin, "out": cout, "vin": vin,
             "text": f"{reading(pack, cin, kin)}；换出的那张 {reading(pack, cout, kout)}"}
        sin = st_of(pack, o.get("resource")) if kin == 1 else None
        sout = st_of(pack, o.get("removed"))
        if sin is not None and sout is not None:
            x["st"] = [round(sin[0] - sout[0], 6), round(math.hypot(sin[1], sout[1]), 6)]
        return x
    if act == "delete":
        cout = _cid(o.get("removed"))
        k = max(1, held.get(cout, 0))
        v = v_of(pack, cout, k)
        return None if v is None else {"v": round(-v, 4), "kind": "delete", "card": cout, "k": k,
                                       "text": reading(pack, cout, k)}
    if act == "upgrade":
        cid = _cid(o.get("resource"))
        e = (pack.get("cards") or {}).get(cid) or {}
        v = v_of(pack, cid, 1)
        if v is None:
            return None
        u = e.get("u", 0.0)
        return {"v": round(v * u, 4), "kind": "upgrade", "card": cid, "k": 1,
                "text": f"{reading(pack, cid, 1)}，其中 {round(100 * u)}% 强化过"}
    return None


def _unstable(pack, cid, k):
    return k in ((((pack or {}).get("cards") or {}).get(cid) or {}).get("unstable") or ())


def reading(pack, cid, k):
    """「前段 63/100 有」 / 「前段 12/100 有 2 张以上」 (the weighted share on 100)."""
    v = v_of(pack, cid, k)
    if v is None:
        return "前段无记录"
    e = (pack.get("cards") or {}).get(cid) or {}
    n = round(100 * v)
    where = f"（带 {e['support']} 的记录里）" if e.get("support") else ""
    return f"前段{where} {n}/100 有" + (f" {k} 张以上" if k > 1 else "")


def annotate(plan, pack, deck=None):
    """{option label: {v, kind, card, k, text, rank}} for the options V reads, best first by rank."""
    if not pack or not plan:
        return {}
    held = held_counts(deck if deck is not None else deck_keys(plan))
    vals = {}
    for o in plan.get("options") or ():
        x = option_v(o, pack, held)
        if x is not None:
            vals[o["label"]] = x
    by = "st" if _st_ranks(vals) else "v"
    key = (lambda k: -vals[k]["st"][0]) if by == "st" else (lambda k: -vals[k]["v"])
    for i, lab in enumerate(sorted(vals, key=key)):
        vals[lab]["rank"] = i + 1
        vals[lab]["by"] = by
    if by == "st":
        st_shares(vals)
    else:
        shares(vals)
    return vals


def st_of(pack, res):
    """[w, se] of a card in the pack's strength-board regression, or None."""
    w = ((pack or {}).get("st") or {}).get("w") or {}
    if not w or not res:
        return None
    cid = _cid(res)
    if isinstance(res, dict):
        up = int(res.get("upgrade") or 0)
    else:
        up = int(str(res).split("+")[1]) if "+" in str(res) else 0
    hit = w.get(f"{cid}+{up}")
    if hit is None and up in (0, 1):
        hit = w.get(f"{cid}+{1 - up}")
    return hit


def _st_ranks(vals):
    """ST's V ranks a card decision when every option V reads is a take or a change ST knows (a first copy: ST measures
    having the card, not a second one)."""
    return bool(vals) and all(x["kind"] in ("take", "change") and x.get("st") is not None for x in vals.values())


def st_shares(vals):
    """the card's percentages when ST ranks: each option's summed chance of beating each other option, Phi((w_i - w_j) /
    sqrt(se_i^2 + se_j^2)), as a share of all of them in whole percent summing to 100 (two options ST cannot tell
    apart read about 50 / 50)."""
    labs = list(vals)
    if len(labs) == 1:
        vals[labs[0]]["pct"] = 100
        return vals
    score = {}
    for a in labs:
        wa, sa = vals[a]["st"]
        score[a] = sum(0.5 * (1 + math.erf((wa - vals[b]["st"][0]) / max(1e-9, math.hypot(sa, vals[b]["st"][1]))
                                             / math.sqrt(2))) for b in labs if b != a)
    total = sum(score.values())
    raw = {lab: 100.0 * score[lab] / total for lab in labs}
    pct = {lab: int(math.floor(r)) for lab, r in raw.items()}
    for lab in sorted(raw, key=lambda k: (-(raw[k] - pct[k]), -raw[k], k))[:100 - sum(pct.values())]:
        pct[lab] += 1
    for lab in labs:
        vals[lab]["pct"] = pct[lab]
    return vals


def _weight(x):
    """An option's weight in the card's percentages: what V says it is worth, never below 0 -- a take or a buy its
    V_{n+1}, an upgrade V_1 x u, a change V(in) - V(out), a delete how far the card's V_n is below TAU (the shop's own
    delete rule)."""
    if x["kind"] == "delete":
        return max(0.0, TAU + x["v"])
    return max(0.0, x["v"])


def shares(vals):
    """each option's ``pct``, its weight's share of the options V reads in whole percent, summing to exactly 100 (the
    largest remainders round up); None for every option when no option has any weight."""
    ws = {lab: _weight(x) for lab, x in vals.items()}
    total = sum(ws.values())
    if total <= 0:
        for x in vals.values():
            x["pct"] = None
        return vals
    raw = {lab: 100.0 * w / total for lab, w in ws.items()}
    pct = {lab: int(math.floor(r)) for lab, r in raw.items()}
    for lab in sorted(raw, key=lambda k: (-(raw[k] - pct[k]), -raw[k], k))[:100 - sum(pct.values())]:
        pct[lab] += 1
    for lab, x in vals.items():
        x["pct"] = pct[lab]
    return vals


def deck_keys(plan):
    if "live_deck" in (plan or {}):
        return None if plan["live_deck"] is None else [
            f"{c.get('produce_card_id')}+{int(c.get('current_upgrade_count') or 0)}" for c in plan["live_deck"]]
    for stage in (plan or {}).get("stages") or ():
        cards = (stage.get("loadout") or {}).get("deck_cards")
        if cards is not None:
            return [f"{c.get('produce_card_id')}+{int(c.get('current_upgrade_count') or 0)}" for c in cards]
    return None


def pick(plan, vals, tau=None):
    """(label or None, skip): V's choice among VALS (annotate) for a card decision."""
    tau = TAU if tau is None else tau
    if not vals:
        return None, False
    top = max(vals, key=lambda k: vals[k]["v"])
    x = vals[top]
    dk = (plan or {}).get("decision") or {}
    kind = dk.get("kind")
    from GakumasCLI.produce.catch_v import passable
    if kind == "card_offer" and passable(dk) and x["kind"] == "take" and x["v"] < tau:
        return None, True
    if kind in ("shop", "interval") and x["kind"] in ("take", "upgrade") and x["v"] < tau:
        return None, True
    if x["kind"] == "change" and x["v"] <= 0 and kind in ("shop", "interval"):
        return None, True
    best = min(vals, key=lambda k: vals[k].get("rank") or 0) if all("rank" in v for v in vals.values()) else top
    return best, False


def runner_up(vals, choice, x_tie=None):
    """the option V's choice is too close to -- the best option bringing in another card, when the two are closer than
    X_TIE in frequency V, or within ST_Z combined standard errors when ST ranks -- or None."""
    if not choice or choice not in vals:
        return None
    x = vals[choice]
    rest = [k for k in vals if k != choice and vals[k].get("card") != x.get("card")]
    if not rest:
        return None
    if x.get("by") == "st":
        other = max(rest, key=lambda k: vals[k]["st"][0])
        (w1, s1), (w2, s2) = x["st"], vals[other]["st"]
        return other if w1 - w2 < ST_Z * math.hypot(s1, s2) else None
    other = max(rest, key=lambda k: vals[k]["v"])
    return other if x["v"] - vals[other]["v"] < (X_TIE if x_tie is None else x_tie) else None


MIN_TIE_SEEDS = 8


def final_pick(plan, vals, rows, x_tie=None):
    """(label or None, skip, how): V's choice, with the simulation breaking a close call."""
    choice, skip = pick(plan, vals)
    if skip or not choice:
        return choice, skip, "v"
    other = runner_up(vals, choice, x_tie)
    if other is None:
        return choice, False, "v"
    by_label = {r.get("label"): r for r in rows or () if r.get("per_seed")}
    a, b = by_label.get(choice), by_label.get(other)
    if a is None or b is None:
        return choice, False, "pending"
    from GakumasCLI.produce.hif_advice import paired_gap
    m, ci, n = paired_gap(a, b)
    if n < MIN_TIE_SEEDS or m is None or ci is None or abs(m) - ci <= 0:
        return choice, False, "pending"
    return (choice if m > 0 else other), False, "sim"


def removal_by_v(deck, pack, allow_trouble=True, k=None):
    """(delete / change): the deck cards a change select could give up, likeliest first -- the current rule's order
    (hif_advice.removal_candidates over the whole deck: trouble cards first, the idol's own card never), with the
    cards the pack knows re-ordered among themselves by V_n (n: the copies held), lowest first, in the places the rule
    gave known cards.
    """
    import GakumasCLI.produce.hif_advice as HA
    k = HA.REMOVAL_CANDIDATES if k is None else k
    order = HA.removal_candidates(deck, allow_trouble=allow_trouble, k=10 ** 6)
    held = held_counts(deck)
    slots, known = [], []
    for i, c in enumerate(order):
        v = None if HA.is_trouble(c["id"]) else v_of(pack, c["id"], max(1, held.get(c["id"], 0)))
        if v is not None:
            slots.append(i)
            known.append((v, i, c))
    known.sort(key=lambda x: (x[0], x[1]))
    out = list(order)
    for i, (_v, _i, c) in zip(slots, known):
        out[i] = c
    return out[:k]


def v_score(pack, key, held=None):
    """0-100 for produce_plan's defaults "v": round(100 x V_{n+1}) of card KEY ("<id>+<upgrade>"), or None."""
    cid = str(key).split("+")[0]
    v = v_of(pack, cid, (held or {}).get(cid, 0) + 1)
    return None if v is None else round(100 * v)


def reroll_gain(pack, produce_id, held, offered):
    """E[best V_{n+1} of a new OFFER_SIZE-card offer] - best V_{n+1} of OFFERED (card ids), or None."""
    shares = OFFER_RARITY.get(produce_id)
    pool = (pack or {}).get("pool") or {}
    if not shares or not pool:
        return None
    dist = []
    for rar, share in shares.items():
        ids = pool.get(rar) or []
        for cid in ids:
            dist.append(((v_of(pack, cid, held.get(cid, 0) + 1) or 0.0), share / len(ids)))
    tot = sum(p for _, p in dist)
    if not tot:
        return None
    dist.sort()
    cdf, acc, xs = [], 0.0, []
    for v, p in dist:
        acc += p / tot
        xs.append(v)
        cdf.append(acc)
    prev_x, prev_f, e = 0.0, 0.0, 0.0
    for x, f in zip(xs, cdf):
        e += (x - prev_x) * (1 - prev_f ** OFFER_SIZE)
        prev_x, prev_f = x, f
    now = max(((v_of(pack, c, held.get(c, 0) + 1) or 0.0) for c in offered), default=0.0)
    return round(e - now, 4)


# -- the produce plan's decision input ---------------------------------------------------------------------------------
def maa_decision(plan, rows, separated, pack, age, kind=None, indicator_lead=None):
    """The input of ``scenario.produce_plan.decide`` with V deciding."""
    from GakumasCLI.produce import catch_v as CV
    d = CV.maa_decision(plan, rows, separated, None, age, kind)
    d["indicator"] = {"lead": d["advice"].get("lead"), "separated": d["advice"].get("separated")}
    if not pack:
        return d
    held = held_counts(deck_keys(plan))
    vals = annotate(plan, pack)
    by_id, labels = {}, {}
    for o in plan.get("options") or ():
        idx = o.get("index", o.get("position"))
        x = vals.get(o.get("label"))
        if idx is None or x is None:
            continue
        if idx not in by_id or x["v"] > by_id[idx]:
            by_id[idx], labels[idx] = x["v"], o["label"]
    d["v"] = by_id
    d["v_score"] = {}
    for i, lab in labels.items():
        x = vals[lab]
        if x["kind"] in ("take", "upgrade"):
            d["v_score"][i] = round(100 * x["v"])
        elif x["kind"] == "change":
            d["v_score"][i] = round(100 * x["vin"])
    choice, skip, how = final_pick(plan, vals, rows)
    cards = [o for o in d["options"] if o.get("card")]
    idx_of = {o["label"]: o.get("index", o.get("position")) for o in plan.get("options") or ()}
    lead = idx_of.get(choice) if choice else None
    lead = lead if lead in [o["id"] for o in d["options"]] else None
    d["advice"] = dict(d["advice"], lead=lead, separated=bool(cards) and all(o["id"] in by_id for o in cards),
                       ready=True, source="v", tie=how)
    rank = {}
    for o in plan.get("options") or ():
        idx, x = idx_of.get(o.get("label")), vals.get(o.get("label"))
        if idx is not None and x is not None:
            rank[idx] = max(rank.get(idx, -10 ** 6), -int(x.get("rank") or 10 ** 5))
    if lead is not None:
        rank[lead] = 1
    d["v_rank"] = rank
    if skip:
        d["v_skip"] = True
    if d.get("removable"):
        d["v_deck"] = {k: v for k in d["removable"] if (v := v_of(pack, k.split("+")[0], max(1, held.get(k.split("+")[0], 0)))) is not None}
        d["v_score_deck"] = {k: round(100 * v) for k, v in d["v_deck"].items()}
    for s in (d.get("services") or {}).values():
        s["v"] = {k: v for k in s["cards"] if (v := v_of(pack, k.split("+")[0], max(1, held.get(k.split("+")[0], 0)))) is not None}
        if s["action"] == "upgrade":
            s["v"] = {k: round(v * (((pack.get("cards") or {}).get(k.split("+")[0]) or {}).get("u", 0.0)), 4)
                      for k, v in s["v"].items()}
        s["v_score"] = {k: round(100 * v) for k, v in s["v"].items()}
    dk = (plan.get("decision") or {})
    if dk.get("kind") == "card_offer" and dk.get("effect_type") != "card_change_select":
        g = reroll_gain(pack, dk.get("produce_id"), held, [o["card"].split("+")[0] for o in cards])
        if g is not None:
            d["v_reroll"] = {"gain": g, "worth": g > REROLL_GAIN}
    return d


# -- validation ---------------------------------------------------------------------------------------------
def split_check(rows, master_dir=None, min_records=5):
    """every idol card's records split by rank parity, V built on each half (the plan prior from the same half of the
    other idol cards); {cells, p90, unstable: [(idol, card, k, dv)]} over the cells a card is held in at least
    MIN_RECORDS records of the idol card."""
    by_idol = records_by_idol(rows)
    halves = ([], [])
    for idol, recs in by_idol.items():
        for i, r in enumerate(recs):
            halves[i % 2].append(r)
    a = build_packs(halves[0], master_dir)
    b = build_packs(halves[1], master_dir)
    full = build_packs(rows, master_dir)
    dvs, unstable = [], []
    for idol, pack in full.items():
        if idol not in a or idol not in b:
            continue
        for cid, e in pack["cards"].items():
            if sum(1 for k, w in enumerate(e["w"]) if k == 0 and w >= min_records) == 0:
                continue
            for k in range(1, len(e["v"]) + 1):
                if k > 1 and (len(e["w"]) < k or e["w"][k - 1] < min_records):
                    continue
                dv = abs((v_of(a[idol], cid, k) or 0.0) - (v_of(b[idol], cid, k) or 0.0))
                dvs.append(dv)
                if dv > 0.25:
                    unstable.append((idol, cid, k, round(dv, 3)))
    dvs.sort()
    q = lambda p: round(dvs[min(len(dvs) - 1, int(p * len(dvs)))], 4) if dvs else None   # noqa: E731
    return {"cells": len(dvs), "p50": q(0.5), "p90": q(0.9), "p99": q(0.99), "unstable": unstable}


def ledger_decks(path):
    """{source: Counter(card id)} of the deck before each decision of a produce ledger (produce_ledger.py rows: the
    row's ``state.deck`` is the deck the decision met)."""
    out = {}
    for line in open(path, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            deck = (r.get("state") or {}).get("deck")
            if deck is not None and r.get("source") not in out:
                out[r["source"]] = Counter(c.get("id") for c in deck)
    return out


def mark_unstable(packs, rows, master_dir=None, limit=0.25):
    """flag the V_k a rank-parity split moves by more than LIMIT (``unstable``: [k,...] on the card); the card then shows
    「（不稳）」.
    """
    s = split_check(rows, master_dir)
    n = 0
    for idol, cid, k, _dv in s["unstable"]:
        e = ((packs.get(idol) or {}).get("cards") or {}).get(cid)
        if e is not None:
            e.setdefault("unstable", []).append(k)
            n += 1
    return n


def truth_check(k1a, packs, taus=TAU_GRID, decks=None):
    """DECKS (ledger_decks): the held copies at each decision; a decision without its deck reads every option as a first
    copy (V_1).
    """
    rows = k1a["rows"]
    out = {"rows": len(rows), "covered": 0, "with_deck": 0, "v_ok": 0, "player_ok": 0, "old_v_ok": 0, "by_tau": {},
           "by_rarity": {}, "items": []}
    by_tau = {t: {"ok": 0, "passed": 0} for t in taus}
    rar = defaultdict(lambda: [0, 0])
    for r in rows:
        pack = packs.get(r["idol"])
        if not pack:
            continue
        opts = list(r["means"])
        held = (decks or {}).get(r["key"].split("|")[0])
        out["with_deck"] += held is not None
        held = held or Counter()
        vs = {o: v_of(pack, o.split("+")[0], held.get(o.split("+")[0], 0) + 1) for o in opts}
        known = {o: v for o, v in vs.items() if v is not None}
        if len(known) < 2:
            continue
        out["covered"] += 1
        best = max(known, key=lambda o: known[o])
        ok = best in r["acceptable"]
        out["v_ok"] += ok
        out["player_ok"] += bool(r.get("player_ok"))
        out["old_v_ok"] += bool(r.get("v_ok"))
        e = (pack.get("cards") or {}).get(best.split("+")[0]) or {}
        rar[e.get("rarity") or "?"][0] += 1
        rar[e.get("rarity") or "?"][1] += ok
        for t in taus:
            if known[best] < t:
                by_tau[t]["passed"] += 1
            else:
                by_tau[t]["ok"] += ok
        out["items"].append({"key": r["key"], "idol": r["idol"], "v_pick": best, "v": known[best], "ok": ok,
                             "acceptable": r["acceptable"], "player": r.get("player")})
    out["by_tau"] = {str(t): x for t, x in by_tau.items()}
    out["by_rarity"] = {k: {"picks": v[0], "ok": v[1]} for k, v in sorted(rar.items())}
    return out


def chosen_tau(by_tau):
    """'s rule: the highest hit count over TAU_GRID, ties to the smaller tau."""
    return min(by_tau, key=lambda t: (-by_tau[t]["ok"], float(t)))


# -- route references ------------------------------------------------------------------------------------------------
def load_templates(paths):
    """{idol: [template]} from the research data templates_*.json files (gkms.strength.templates.v1)."""
    out = {}
    for p in paths:
        d = _read(p)
        if not d or d.get("format") != "gkms.strength.templates.v1":
            continue
        for idol, c in (d.get("cards") or {}).items():
            out.setdefault(idol, []).extend(c.get("templates") or ())
    for idol in out:
        out[idol].sort(key=lambda t: t.get("rank") or 10 ** 6)
    return out


SWAP_DROP = ("schema", "idol_card_id", "produce_id", "round")


def swap_templates(templates, k=SWAP_K):
    """K of an idol card's replayable templates (load_templates, rank order) that have both 本戦 auditions, evenly spaced
    over the ranks: [{record (12 chars), rank, mid1: {seed, loadout}, final: {...}}], the loadouts without their None
    fields, their seed and SWAP_DROP.
    """
    both = [t for t in templates if (t.get("mid1") or {}).get("loadout") and (t.get("final") or {}).get("loadout")]
    if len(both) < k:
        return []
    out = []
    for i in range(k):
        t = both[int((i + 0.5) * len(both) / k)]
        e = {"record": str(t.get("record") or "")[:12], "rank": t.get("rank")}
        for rnd in ("mid1", "final"):
            lo = {key: v for key, v in t[rnd]["loadout"].items()
                  if v is not None and key not in SWAP_DROP and key != "seed"}
            e[rnd] = {"seed": int(t[rnd]["seed"]), "loadout": lo}
        out.append(e)
    return out


def _star_of(catalog, cfg, stage, lo):
    """(star, largest relative permil miss): the template's スター性 at the audition, read back from its score bonus
    permils (audition_eval.permils = base x (1000 + A) x (1000 + S) / 10^7 x 10, A = EFFECT_BONUS, S =
    produce_effects.star_bonus(stage, star)) -- a template carries the permils, not the star.
    """
    import GakumasCLI.produce.produce_effects as pe
    from GakumasCLI.produce import audition_eval as AE
    params = {k: int(v) for k, v in lo["parameters"].items()}
    base = catalog.base_permils(cfg, {int(k): v for k, v in params.items()})
    got = {str(k): int(v) for k, v in lo["score_bonus_permils"].items()}
    ss = sorted(got[str(k)] * 10 ** 6 / (base[k] * (1000 + AE.EFFECT_BONUS)) - 1000 for k in base if base[k])
    s_est = ss[len(ss) // 2] if ss else 200
    bl, w = pe.STAR_BONUS_LINE[stage]
    star = (s_est - 200) * bl / 800 if s_est < 1000 else bl + (s_est - 1000) * w / 1000
    best = None
    for cand in range(max(0, int(star) - 3), int(star) + 4):
        new = AE.permils(catalog, cfg, params, AE.EFFECT_BONUS, pe.star_bonus(stage, cand))
        miss = max(abs(new[k] - got[k]) / max(1, got[k]) for k in got)
        if best is None or miss < best[1]:
            best = (cand, miss)
    return best[0], round(best[1], 4)


def _native_search(master):
    """The production job on the native core in this process (route_search_native.install, as bench/run.py)."""
    import GakumasCLI.paths as P
    os.environ.setdefault("GKMS_NATIVE_LIB", str(P.NATIVE_LIB))
    os.environ.setdefault("GKMS_NATIVE_STRINGS", str(P.NATIVE_STRINGS))
    from GakumasCLI.native import route_search_native
    route_search_native.install()
    route_search_native._native(master)


def route_ref_of(templates, master, transitions=1_000_000, prior=None, search=None):
    """The ``route_ref`` of one idol card: for each 本戦 audition the median-ranked template that has it, searched on
    its own exam seed with the production job (honsen_ref._search) for per-attribute scores.
    """
    from GakumasCLI.produce import audition_eval as AE
    from GakumasCLI.produce import honsen_ref as HR
    if search is None:
        _native_search(master)
        pr = HR._prior(prior)
        search = lambda lo, seeds: HR._search(master, lo, seeds, transitions, pr)   # noqa: E731
    out, used = {}, {}
    for st, rnd in (("audition_mid1", "mid1"), ("audition_final", "final")):
        have = [t for t in templates if (t.get(rnd) or {}).get("loadout")]
        if not have:
            continue
        t = have[len(have) // 2]
        lo = dict(t[rnd]["loadout"])
        res = search(lo, [int(t[rnd]["seed"])])
        if not res:
            continue
        n = len(res)
        cfg = AE.battle_config(master.audition_catalog, lo["idol_card_id"], HR.STAGE[st])
        star, dev = _star_of(master.audition_catalog, cfg, HR.STAGE[st], lo)
        out[st] = {"cfg": cfg, "params": {k: int(v) for k, v in lo["parameters"].items()},
                   "permils": {k: int(v) for k, v in lo["score_bonus_permils"].items()},
                   "scores": [round(sum(r[1][i] for r in res) / n, 1) for i in range(3)],
                   "score": round(sum(r[0] for r in res) / n, 1), "recorded": t[rnd].get("recorded_score"),
                   "star": star, "permil_fit": dev, "seeds": n}
        used[st] = t.get("record", "")[:12]
    if "audition_final" not in out:
        return None
    out["template"] = used
    return out


# -- the strength board's regression V ---------------------------------------------------------------------------
def st_model(path):
    """(idol, entry) of one ST regression model -- the research data/<stNN>/cloud/<job>/model.json, or a copy named
    <stNN>__<job>.json -- read only (the ST line's files are not changed): entry {source, path, games, r2_cv, w:
    {"<id>+<upgrade>": [w, se]}}.
    """
    path = Path(path)
    if path.name == "model.json":
        job, st = path.parent.name, path.parent.parent.parent.name
    else:
        st, job = path.stem.split("__", 1)
    m = json.loads(path.read_text(encoding="utf-8"))
    base, custom = {}, {}
    for k, e in (m.get("weights") or {}).items():
        b = k.split("*")[0]
        pair = [round(float(e.get("w") or 0.0), 6), round(float(e.get("se") or 0.0), 6)]
        if "*" in k:
            if b not in custom or pair[0] > custom[b][0]:
                custom[b] = pair
        else:
            base[b] = pair
    w = dict(custom)
    w.update(base)
    idol = "i_card-" + job.replace("_", "-")
    return idol, {"source": f"ST-{st[2:]}", "path": f"produce/p6/{st}/cloud/{job}/model.json",
                  "games": m.get("games"), "r2_cv": m.get("r2_cv"), "w": dict(sorted(w.items()))}


# -- CLI ------------------------------------------------------------------------------------------------------------------
def _cmd_build(args):
    t0 = time.time()
    rows = ranking_rows(args.ranking)
    packs = build_packs(rows, args.tables, snapshot=ranking_snapshot(args.ranking), idols=set(args.idol or ()) or None)
    print("unstable cells", mark_unstable(packs, rows, args.tables), flush=True)
    if args.route_from:
        for idol in packs:
            old = _read(Path(args.route_from) / f"{idol}.json")
            if old and old.get("route_ref") and old.get("exam_rules") == packs[idol]["exam_rules"]:
                packs[idol]["route_ref"] = old["route_ref"]
            if old and old.get("swap"):
                packs[idol]["swap"] = old["swap"]
    for path in args.st or ():
        idol, entry = st_model(path)
        if idol in packs:
            if args.st_commit:
                entry["commit"] = args.st_commit
            packs[idol]["st"] = entry
    if args.templates:
        from GakumasCLI.scenario.exam_session import Master
        master = Master.load(_master_dir(args.tables))
        temps = load_templates(args.templates)
        for idol in sorted(packs):
            if idol in temps and swap_templates(temps[idol]):
                packs[idol]["swap"] = swap_templates(temps[idol])
            if idol in temps:
                ref = route_ref_of(temps[idol], master, transitions=args.transitions)
                if ref:
                    packs[idol]["route_ref"] = ref
                    print("route_ref", idol, {k: round(v["score"]) for k, v in ref.items() if k.startswith("audition")},
                          flush=True)
    meta = write_packs(packs, args.out)
    print("packs", len(packs), "route_ref", sum(1 for x in meta["idols"].values() if x["route_ref"]),
          "bytes", sum(x["bytes"] for x in meta["idols"].values()), "seconds", round(time.time() - t0, 1))


def _cmd_swap(args):
    """the swap-in templates added to the packs in OUT, and swap_k on every pack (everything else kept)."""
    out = Path(args.out)
    temps = load_templates(args.templates)
    packs = {}
    for f in sorted(out.glob("i_card-*.json")):
        pk = _read(f)
        if not pk or pk.get("format") != FORMAT:
            continue
        sw = swap_templates(temps.get(pk["idol"]) or [], args.k)
        if sw:
            pk["swap"] = sw
        packs[pk["idol"]] = pk
    meta = write_packs(packs, out)
    print("packs", len(packs), "swap", sum(1 for x in meta["idols"].values() if x["swap"]),
          "bytes", sum(x["bytes"] for x in meta["idols"].values()))


def _cmd_validate(args):
    rows = ranking_rows(args.ranking)
    s = split_check(rows, args.tables)
    print("SPLIT cells", s["cells"], "p50", s["p50"], "p90", s["p90"], "p99", s["p99"], "unstable", len(s["unstable"]))
    out = {"split": s}
    if args.k1a:
        packs = {}
        for p in Path(args.out).glob("i_card-*.json"):
            pk = _read(p)
            packs[pk["idol"]] = pk
        decks = {}
        for p in args.ledger or ():
            decks.update(ledger_decks(p))
        t = truth_check(_read(args.k1a), packs, decks=decks)
        print("TRUTH rows", t["rows"], "covered", t["covered"], "with_deck", t["with_deck"], "v_ok", t["v_ok"],
              "old_v_ok", t["old_v_ok"], "player_ok", t["player_ok"])
        for tau, x in t["by_tau"].items():
            print("TAU", tau, "ok", x["ok"], "passed", x["passed"])
        print("CHOSEN_TAU", chosen_tau(t["by_tau"]))
        print("RARITY", json.dumps(t["by_rarity"], ensure_ascii=False))
        out["truth"] = t
    if args.json:
        Path(args.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def _cmd_show(args):
    pack, state = pack_for(args.idol, pack_dir=args.dir, fetch=False)
    if not pack:
        print(state)
        return
    print(args.idol, pack["plan"], "records", pack["records"], "n_eff", pack["n_eff"], "snapshot", pack["snapshot"])
    for cid, e in sorted(pack["cards"].items(), key=lambda kv: -kv[1]["v"][0])[:args.top]:
        print(f"{cid:28s} {e['rarity']:3s} {e['kind']:7s} V {' '.join(f'{v:.2f}' for v in e['v'])}  u {e.get('u', '-')}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("out")
    b.add_argument("--ranking", required=True)
    b.add_argument("--tables")
    b.add_argument("--idol", action="append")
    b.add_argument("--templates", nargs="*")
    b.add_argument("--transitions", type=int, default=1_000_000)
    b.add_argument("--route-from", help="take each pack's route_ref from this earlier build (same exam rules)")
    b.add_argument("--st", nargs="*", help="ST regression models (produce/p6/<stNN>/cloud/<job>/model.json)")
    b.add_argument("--st-commit", help="the research data commit the ST models were read at")
    w = sub.add_parser("swap", help="add the 選抜 swap-in templates to built packs in place")
    w.add_argument("out")
    w.add_argument("--templates", nargs="+", required=True)
    w.add_argument("-k", type=int, default=SWAP_K)
    v = sub.add_parser("validate")
    v.add_argument("out")
    v.add_argument("--ranking", required=True)
    v.add_argument("--tables")
    v.add_argument("--k1a")
    v.add_argument("--ledger", action="append", help="produce ledgers for the held copies at each truth decision")
    v.add_argument("--json")
    s = sub.add_parser("show")
    s.add_argument("idol")
    s.add_argument("--dir")
    s.add_argument("--top", type=int, default=25)
    args = ap.parse_args(argv)
    {"build": _cmd_build, "swap": _cmd_swap, "validate": _cmd_validate, "show": _cmd_show}[args.cmd](args)


if __name__ == "__main__":
    main()
