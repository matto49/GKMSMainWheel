# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""An audition of a simulated produce on the native exam core (the same builder as the workbench and the website:
``GakumasCLI.scenario.hif.build_hif_loadout_session`` over a ``gkms.hif_loadout.v1`` document).
"""
from __future__ import annotations

import threading

from GKMSPS.ps.data import ATTRS, name

_LOCK = threading.Lock()
_M = {}
STEP_TAIL = {"mid1": "AUDITION_MID1", "mid2": "AUDITION_MID2", "final": "AUDITION_FINAL"}
ENCHANT_TYPES = ("EXAM_STATUS_ENCHANT", "EXAM_PERMANENT_AUDITION_STATUS_ENCHANT")


def master():
    with _LOCK:
        return master_unlocked()


def stage_key(produce_id, stage):
    return {("produce-007", "mid1"): "sel1", ("produce-007", "mid2"): "sel2", ("produce-007", "final"): "sel3",
            ("produce-008", "mid1"): "main1", ("produce-008", "final"): "main2"}[(produce_id, stage)]


def citem_enchants(sim):
    out = []
    for ci in sim.s["citems"]:
        for eid in (sim.d.citems.get(ci) or {}).get("produceEffectIds") or ():
            if sim.d.effect_type(eid) in ENCHANT_TYPES:
                ench = sim.d.effect(eid).get("produceExamStatusEnchantId")
                if ench and ench not in out:
                    out.append(ench)
    return out


def loadout(sim, stage):
    from GakumasCLI.produce import audition_eval as AE, produce_effects as pe
    s, d = sim.s, sim.d
    pid = s["produce_id"]
    row = d.difficulty(s["idol"], pid, STEP_TAIL[stage])
    params = {str(i + 1): int(s["params"][a]) for i, a in enumerate(ATTRS)}
    permils = AE.permils(master().audition_catalog, row["produceExamBattleConfigId"], params, s["audition_bonus"],
                         pe.star_bonus(stage_key(pid, stage), s["star"]))
    deck = []
    for c in sorted((c for c in s["deck"] if not c.get("deleted")), key=lambda c: c["n"]):
        x = {"produce_card_id": c["id"], "current_upgrade_count": int(c["up"])}
        if c.get("cz"):
            x["customizes"] = [{"id": cz, "customize_count": int(n)} for cz, n in c["cz"]]
        deck.append(x)
    return {"schema": "gkms.hif_loadout.v1", "idol_card_id": s["idol"], "round": stage, "produce_id": pid,
            "parameters": params, "score_bonus_permils": permils, "deck_cards": deck, "include_produce_default": False,
            "produce_item_ids": [i["id"] for i in s["items"]], "drink_ids": list(s["drinks"]),
            "support_cards": [{"support_card_id": x["id"], "permil": int(x["permil"])} for x in s["supports"]],
            "max_stamina": int(s["max_stamina"]), "starting_stamina": max(1, int(s["stamina"])),
            "lesson_start_enchant_ids": list(s["memory_enchants"]), "main_item_enchant_ids": citem_enchants(sim),
            "research_card_ids": []}


def npc_scores(sim, stage, stream):
    """Each rival's score: fixed where scoreMin = scoreMax, else uniform in the range."""
    row = sim.d.difficulty(sim.s["idol"], sim.s["produce_id"], STEP_TAIL[stage])
    out = []
    for r in sorted(sim.d.npc_groups.get(row["produceExamBattleNpcGroupId"], ()), key=lambda r: r["number"]):
        lo, hi = int(r.get("scoreMin") or 0), int(r.get("scoreMax") or 0)
        out.append(lo if hi <= lo else lo + stream.uniform(hi - lo + 1))
    return out


def open_session(lo, seed):
    from GakumasCLI.scenario.hif import build_hif_loadout_session
    session, _meta = build_hif_loadout_session(master(), dict(lo), seed=int(seed))
    return session


def _card_label(session, view):
    nm = view.get("name") or view.get("produce_card_id") or view.get("id") or "?"
    up = int(view.get("current_upgrade_count") or view.get("upgrade") or 0)
    return nm + "+" * up


def options(session):
    o = session.observe()
    hand = o.get("hand") or []
    drinks = list(getattr(session, "drinks", []) or [])
    out = []
    for a in session.legal_actions():
        if a[0] == "play":
            v = hand[a[1]] if a[1] < len(hand) else {}
            out.append({"label": "出牌 " + _card_label(session, v), "a": ["play", int(a[1])]})
        elif a[0] == "drink":
            did = drinks[a[1]] if a[1] < len(drinks) else str(a[1])
            from GKMSPS.ps.data import data
            nm = (data().drinks.get(did) or {}).get("name") or did
            out.append({"label": f"喝饮料 {nm}", "a": ["drink", int(a[1])]})
        else:
            out.append({"label": "结束回合", "a": ["end"]})
    return out


def step(session, a):
    session.step(tuple(a) if a[0] != "select" else ("select", list(a[1])), observe=False)


def result(session):
    o = session.observe()
    return {"score": int(o["score"]), "parts": [int(o["score_vocal"]), int(o["score_dance"]), int(o["score_visual"])],
            "stamina": int(o["stamina"]), "drinks_left": list(getattr(session, "drinks", []) or [])}


def searcher():
    """The native policy search (the workbench's), one per process."""
    with _LOCK:
        if "ns" not in _M:
            import json
            import os
            from GakumasCLI import paths
            from GakumasCLI.native.diffrun import Native
            from GakumasCLI.native.native_search import NativeSearch
            lib = os.environ.get("GKMS_NATIVE_LIB") or str(paths.NATIVE_DLL if os.name == "nt" else paths.NATIVE_DIR / "libgkmscore.so")
            nat = Native(lib)
            with open(paths.APP_ROOT / "data" / "strings.json", encoding="utf-8") as f:
                nat.load_master(master_unlocked(), json.load(f))
            _M["ns"] = NativeSearch(nat)
        return _M["ns"]


def master_unlocked():
    if "m" not in _M:
        import GakumasCLI.tools.hif_seed_solve as H
        _M["m"] = H.master()
    return _M["m"]


SHORT_SEARCH_SECONDS = 30.0


def split_ruleset(split):
    """The 控分 rules of ``split`` ({vocal|dance|visual: [lo, hi]} whole percent of the exam's own score, in
    ``GakumasCLI.scenario.timeline.split_check``'s form) for the native search, None without one.
    """
    if not split:
        return None
    from GakumasCLI.native import native_search as N
    from GakumasCLI.scenario import timeline as TL
    ns = searcher()
    if not ns.rules_ok or N.timeline_version(ns.nat.lib) < 2:
        raise RuntimeError("服务器上的引擎不支持控分（gk_timeline_version 2 以前的内核）")
    return TL.RuleSet(TL.split_rules(split))


def plan(session, budget=2.0, search_seed=182, split=None, transitions=None):
    """The engine's line from the session's state (GNRPA on the native core, ``budget`` seconds): a list of actions;
    under ``split`` the line keeps the score's Vo / Da / Vi shares in its ranges where it can.
    """
    from GakumasCLI.native.native_search import decode_actions  # noqa: F401
    limit = {"transitions": int(transitions)} if transitions else {"deadline": float(budget)}
    path, st = searcher().policy_search(session, seed=int(search_seed), rules=split_ruleset(split), **limit)
    if st.get("status") != 0:
        return []
    return [list(a) if a[0] != "select" else ["select", list(a[1])] for a in path]


def shares(parts):
    """Each part's permil of the score (0s before any score), as the 控分 rules read them (timeline.split_shares)."""
    total = sum(int(x or 0) for x in parts)
    return [int(x or 0) * 1000 // total if total > 0 else 0 for x in parts]


def split_distance(parts, split):
    """Permil the shares lie outside ``split``'s ranges (0: inside every one), timeline.split_report's distance."""
    out = 0
    for x, key in zip(shares(parts), ("vocal", "dance", "visual")):
        lo, hi = (split or {}).get(key, [0, 100])
        out += max(0, lo * 10 - x) + max(0, x - hi * 10)
    return out


def search_line(session, budget=SHORT_SEARCH_SECONDS, split=None, search_seed=182):
    """``plan`` on a copy of the session, and where its line ends when followed: {line, finished, score, parts,
    shares, stamina, split_distance}.
    """
    line = plan(session.clone(), budget, search_seed, split)
    end = session.clone()
    for a in line:
        if end.finished:
            break
        step(end, a)
    o = end.observe()
    parts = [int(o["score_vocal"]), int(o["score_dance"]), int(o["score_visual"])]
    return {"line": line, "finished": bool(end.finished), "score": int(o["score"]), "parts": parts,
            "shares": shares(parts), "stamina": int(o["stamina"]),
            "split_distance": split_distance(parts, split) if split else None}


def view(session):
    """What the exam screen shows (turn, score, stamina, hand)."""
    o = session.observe()
    return {k: o.get(k) for k in ("turn", "limit_turn", "score", "stamina", "max_stamina", "block", "deck_count",
                                  "grave_count", "hand")}
