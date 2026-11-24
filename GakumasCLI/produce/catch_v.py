# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The produce plan's decision input for MAA (scenario/produce_plan.decide): a 培育建议 decision's options, the search's
ratings and services (maa_decision), and a 日程 step's (route_decision).
"""
import re

_CHANGE = re.compile(r"^change (p_card-[^+]+)\+(\d) -> (p_card-[^+]+)(?:\+(\d))?$")
_SERVICE = re.compile(r"^(delete|upgrade) (p_card-[^+ ]+)(?:\+(\d))?$")


def _card_choice(plan):
    d = (plan or {}).get("decision") or {}
    return d.get("kind") == "card_offer"


# -- the produce plan's decision input -----------------------------------------------------------------
def _option_index(o):
    return o.get("index", o.get("position"))


LEAVE_ID = "leave"
SKIP_ID = "skip"
REROLL_ID = "reroll"


def passable(d):
    """a card offer the game lets the player pass: an effect's select with pick_count_min 0 -- not a change select, not a
    present (every lesson / event reward the server sent had pick_count_min 1: hif_produce_advice _build_view's
    must_pick)."""
    return (d.get("kind") == "card_offer" and d.get("source") != "present"
            and d.get("effect_type") != "card_change_select" and int(d.get("pick_min") or 0) == 0)


def maa_kind(d):
    """produce_plan's decision kind of a tracker decision (hif_advice.Tracker.decision)."""
    dk = d["kind"]
    return {"card_offer": "card_change" if d.get("effect_type") == "card_change_select" else "card_pick",
            "drink_offer": "drink_pick", "drink_discard": "drink_discard", "shop": "shop",
            "interval": "interval"}.get(dk, dk)


def maa_decision(plan, rows, separated, bundle, age, kind=None):
    """scenario.produce_plan.decide's input for a 培育建议 decision."""
    d = plan["decision"]
    if kind is None:
        kind = maa_kind(d)
    rating = {r.get("label"): r.get("rating") for r in rows or ()}
    by_label = {r.get("label"): r for r in rows or ()}
    lead = None
    if rows:
        best = max((r for r in rows if r.get("rating") is not None), key=lambda r: r["rating"], default=None)
        lead = best and best.get("label")
    opts, seen = [], {}
    for o in plan.get("options") or ():
        idx = _option_index(o)
        if idx is None:
            continue
        res = o.get("resource") if isinstance(o.get("resource"), dict) else None
        card = f"{res['id']}+{int(res.get('upgrade') or 0)}" if res and res.get("id") else None
        r = rating.get(o["label"])
        if idx in seen:
            prev = seen[idx]
            if r is not None and (prev["rating"] is None or r > prev["rating"]):
                prev.update(rating=r, label=o["label"])
            continue
        seen[idx] = {"id": idx, "label": o["label"], "card": card, "rating": r, "price": o.get("price")}
        if o.get("kind") == "drink" and isinstance(o.get("resource"), str):
            seen[idx]["drink"] = o["resource"]
            row = by_label.get(o["label"]) or {}
            if row.get("with_drop"):
                seen[idx]["with_drop"] = row["with_drop"]
        opts.append(seen[idx])
    lead_id = next((x["id"] for x in opts if lead and x["label"] == lead), None)
    out = {"kind": kind, "produce": d.get("produce_id"), "step": d.get("step"), "options": opts,
           "advice": {"ready": bool(rows), "separated": bool(separated), "lead": lead_id, "age": age},
           "v": None, "deck": deck_keys(plan)}
    if d.get("produce_point") is not None:
        out["p_points"] = int(d["produce_point"])
    if d.get("drinks") is not None:
        out["drinks_held"] = list(d["drinks"])
        gained, held = {}, {}
        for o in plan.get("options") or ():
            row = by_label.get(o.get("label")) or {}
            r = row.get("rating_alone", row.get("rating"))
            if r is None:
                continue
            if o.get("kind") == "drink" and _option_index(o) is not None:
                gained[_option_index(o)] = r
            elif o.get("kind") == "drop" and isinstance(o.get("resource"), str):
                held[o["resource"]] = -r
        if gained or held:
            out["drink_values"] = {"gained": gained, "held": held}
    if kind in ("card_pick", "card_change"):
        if passable(d):
            out["skip_id"] = SKIP_ID
        out["rerolls_left"] = int(d.get("rerolls_left") or 0)
        if out["rerolls_left"] > 0:
            out["reroll_id"] = REROLL_ID
    if kind in ("shop", "interval"):
        out["leave_id"] = LEAVE_ID
        out["rerolls"] = int(d.get("rerolls") or 0)
    removable = []
    for o in plan.get("options") or ():
        m = _CHANGE.match(o.get("label") or "")
        if m and f"{m.group(1)}+{m.group(2)}" not in removable:
            removable.append(f"{m.group(1)}+{m.group(2)}")
    if removable:
        out["removable"] = removable
        out["v_deck"], out["v_score_deck"] = {}, {}
    if kind in ("shop", "interval"):
        services = {}
        for o in plan.get("options") or ():
            idx, lab = _option_index(o), o.get("label") or ""
            m, c = _SERVICE.match(lab), _CHANGE.match(lab)
            if idx is None or not (m or c):
                continue
            act, key = (m.group(1), f"{m.group(2)}+{m.group(3) or 0}") if m else ("change", f"{c.group(1)}+{c.group(2)}")
            s = services.setdefault(idx, {"action": act, "cards": []})
            if key not in s["cards"]:
                s["cards"].append(key)
        for s in services.values():
            s["v"], s["v_score"] = {}, {}
        if services:
            out["services"] = services
    return out


def deck_keys(plan):
    """the deck now as card keys "<id>+<upgrade>" (the plan's stage loadouts carry the live deck), or None."""
    if "live_deck" in plan:
        return None if plan["live_deck"] is None else [
            f"{c.get('produce_card_id')}+{int(c.get('current_upgrade_count') or 0)}" for c in plan["live_deck"]]
    for stage in plan.get("stages") or ():
        cards = (stage.get("loadout") or {}).get("deck_cards")
        if cards is not None:
            return [f"{c.get('produce_card_id')}+{int(c.get('current_upgrade_count') or 0)}" for c in cards]
    return None


def route_decision(route, step, age, triangle_steps=(), candidates=(), produce=None):
    """scenario.produce_plan.decide's input for a 日程 step from the panel's route view (hif_produce_advice route_view's
    `out`: plan {step: choice}, options {step: {choice: vs_best}}).
    """
    if not route or route.get("state") != "done":
        return {"kind": "route", "produce": (route or {}).get("produce_id") or produce, "step": step,
                "options": [{"id": c, "rating": None} for c in candidates or ()],
                "advice": {"ready": False, "lead": None, "age": age}}
    opts = [{"id": c, "rating": v} for c, v in sorted(((route.get("options") or {}).get(step) or {}).items(),
                                                       key=lambda kv: -(kv[1] if kv[1] is not None else -1e18))]
    lead = (route.get("plan") or {}).get(step)
    if not opts and lead:
        opts = [{"id": lead, "rating": 0}]
    tris = (route.get("option_triangles") or {}).get(step) or {}
    clear = (route.get("option_clear") or {}).get(step) or {}
    if clear:
        top = max((c.get("rating") for c in clear.values() if c.get("rating") is not None), default=None)
        for o in opts:
            c = clear.get(o["id"]) or {}
            o["triangles"] = int(c.get("short", 0))
            o["triangle"] = o["triangles"] > 0
            o["rating_clear"] = None if top is None or c.get("rating") is None else round(c["rating"] - top, 1)
        lead_n = int((clear.get(lead) or {}).get("short", 0))
        return {"kind": "route", "produce": route.get("produce_id"), "step": step, "options": opts,
                "advice": {"ready": True, "lead": lead, "age": age}, "triangle": lead_n > 0 or step in triangle_steps,
                "lead_triangles": lead_n, "criteria": True}
    tri = bool(tris.get(lead)) or step in triangle_steps
    for o in opts:
        o["triangle"] = bool(tris.get(o["id"]))
        o["triangles"] = len(tris.get(o["id"]) or ())
    return {"kind": "route", "produce": route.get("produce_id"), "step": step, "options": opts,
            "advice": {"ready": True, "lead": lead, "age": age}, "triangle": tri,
            "lead_triangles": len(tris.get(lead) or ()) or int(tri)}
