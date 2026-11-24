# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The server's produce state and how the simulator takes it over."""
from __future__ import annotations

import collections
import re

from GKMSPS.ps.data import ATTRS, data

KEYED = {"user_produce_progress_produce_card_list": "number", "user_produce_progress_support_card_list": "number",
         "user_produce_progress_memory_list": "number", "user_produce_progress_schedule_list": "step_number",
         "user_produce_progress_present_list": "position_number", "user_produce_progress_effect_list": "number",
         "user_produce_progress_shop_list": "position_number", "user_produce_progress_interval_list": "position_number",
         "user_produce_progress_produce_customize_item_list": "number", "user_produce_progress_event_list": "number"}
TRUE_END = {1: "FirstStar", 2: "NextIdolAudition", 3: "HatsuboshiIdolFestival"}


class Server:
    """The server's produce state, merged from each response's updated_data / deleted_data."""

    def __init__(self):
        self.p = {}
        self.lists = {k: {} for k in KEYED}

    def apply(self, resp):
        cr = resp.get("common_response") or {}
        upd, dele = cr.get("updated_data") or {}, cr.get("deleted_data") or {}
        for k, rows in dele.items():
            if k in KEYED:
                for r in rows:
                    self.lists[k].pop(r.get(KEYED[k], 0), None)
        if upd.get("user_produce_progress"):
            self.p = dict(upd["user_produce_progress"])
        for k, rows in upd.items():
            if k in KEYED:
                for r in rows:
                    self.lists[k][r.get(KEYED[k], 0)] = r

    def deck(self, with_deleted=False):
        rows = self.lists["user_produce_progress_produce_card_list"]
        return [rows[n] for n in sorted(rows) if with_deleted or not rows[n].get("deleted")]

    def view(self):
        p = self.p
        deck = collections.Counter()
        for c in self.deck():
            cz = tuple(sorted((x["id"], int(x.get("customize_count") or 1)) for x in c.get("customizes") or ()))
            deck[(c["produce_card_id"], int(c.get("upgrade_count") or 0), cz)] += 1
        return {"vocal": int(p.get("vocal") or 0), "dance": int(p.get("dance") or 0), "visual": int(p.get("visual") or 0),
                "stamina": int(p.get("stamina") or 0), "max_stamina": int(p.get("max_stamina") or 0),
                "pp": int(p.get("produce_point") or 0), "star": int(p.get("star") or 0),
                "g_vocal": int(p.get("vocal_growth_rate_permil") or 0), "g_dance": int(p.get("dance_growth_rate_permil") or 0),
                "g_visual": int(p.get("visual_growth_rate_permil") or 0),
                "reroll": int(p.get("produce_card_remain_select_reroll_count") or 0),
                "exclude": int(p.get("produce_card_remain_exclude_count") or 0),
                "drinks": sorted(p.get("produce_drink_ids") or ()),
                "items": sorted(i["produce_item_id"] for i in p.get("produce_items") or ()),
                "citems": sorted(c["produce_customize_item_id"]
                                 for c in self.lists["user_produce_progress_produce_customize_item_list"].values()),
                "deck": deck}


class LiveServer:
    """``Server``'s view (``p``, ``lists``, ``deck``) over a produce the panel follows (hif_advice._Produce): the
    lists are the produce's own dicts, read, never written."""

    def __init__(self, produce):
        self.p = produce.progress or {}
        cards = getattr(produce, "cards", {}) or {}
        self.lists = {k: {} for k in KEYED}
        self.lists.update({
            "user_produce_progress_produce_card_list": cards,
            "user_produce_progress_support_card_list": getattr(produce, "supports", {}) or {},
            "user_produce_progress_memory_list": getattr(produce, "memories", {}) or {},
            "user_produce_progress_schedule_list": getattr(produce, "schedule", {}) or {},
            "user_produce_progress_event_list": getattr(produce, "events", {}) or {},
            "user_produce_progress_effect_list": getattr(produce, "effects", {}) or {},
            "user_produce_progress_shop_list": getattr(produce, "shop", {}) or {},
            "user_produce_progress_interval_list": getattr(produce, "interval", {}) or {},
            "user_produce_progress_present_list": getattr(produce, "present", {}) or {},
            "user_produce_progress_produce_customize_item_list": getattr(produce, "customize_items", {}) or {},
        })

    def deck(self, with_deleted=False):
        rows = self.lists["user_produce_progress_produce_card_list"]
        return [rows[n] for n in sorted(rows) if with_deleted or not rows[n].get("deleted")]


def import_state(sim, sv):
    s, d, p = sim.s, sim.d, sv.p
    s["params"] = {a: int(p.get(a) or 0) for a in ATTRS}
    s["growth"] = {a: int(p.get(f"{a}_growth_rate_permil") or 0) for a in ATTRS}
    s["stamina"], s["max_stamina"] = int(p.get("stamina") or 0), int(p.get("max_stamina") or 0)
    s["pp"], s["star"], s["star_permil"] = int(p.get("produce_point") or 0), int(p.get("star") or 0), int(p.get("star_permil") or 0)
    s["reroll"] = int(p.get("produce_card_remain_select_reroll_count") or 0)
    s["exclude"] = int(p.get("produce_card_remain_exclude_count") or 0)
    s["excluded"] = list(p.get("exclude_produce_card_ids") or ())
    s["sp_rate"] = {a: int(p.get(f"lesson_{a}_sp_change_rate_permil") or 0) for a in ATTRS}
    s["drinks"] = list(p.get("produce_drink_ids") or ())
    base = int(d.setting(s["produce_id"])["produceDrinkPossessLimit"])
    s["drink_limit"] = base + int(p.get("produce_drink_possess_additional_count") or 0)
    s["continues"] = int(d.setting(s["produce_id"]).get("continueCount") or 3) - int(p.get("continue_count") or 0)
    s["deck"] = []
    for c in sv.deck(with_deleted=True):
        x = {"n": int(c["number"]), "id": c["produce_card_id"], "up": int(c.get("upgrade_count") or 0)}
        if c.get("deleted"):
            x["deleted"] = True
        if c.get("customizes"):
            x["cz"] = [[z["id"], int(z.get("customize_count") or 1)] for z in c["customizes"]]
        s["deck"].append(x)
    s["next_card"] = max([c["n"] for c in s["deck"]] + [0])
    counts = {}
    s["items"] = []
    for it in p.get("produce_items") or ():
        s["items"].append({"id": it["produce_item_id"], "n": int(it.get("fire_count") or 0)})
        counts["item:" + it["produce_item_id"]] = int(it.get("fire_count") or 0)
    s["citems"] = [c["produce_customize_item_id"] for _, c in
                   sorted(sv.lists["user_produce_progress_produce_customize_item_list"].items())]
    sups = [r for _, r in sorted(sv.lists["user_produce_progress_support_card_list"].items())]
    for i, r in enumerate(sups):
        if i >= len(s["supports"]):
            break
        for sid, n in zip(r.get("produce_skill_ids") or (), r.get("produce_skill_trigger_counts") or ()):
            for key in [k for k in _skill_keys(sim, f"support:{i}:{r['support_card_id']}", sid)]:
                counts[key] = int(n or 0)
    for j, r in enumerate(sorted(sv.lists["user_produce_progress_memory_list"].values(), key=lambda r: r.get("number") or 0)):
        for ab in r.get("abilities") or ():
            sk = d.M.memory_skill(ab["id"], ab.get("level"))
            if sk is not None:
                counts[f"memory:{j}|{sk.id}|{sk.effect_id}"] = int(ab.get("trigger_count") or 0)
    for c in sv.lists["user_produce_progress_produce_customize_item_list"].values():
        counts["citem:" + c["produce_customize_item_id"]] = int(c.get("trigger_count") or 0)
    s["counts"] = counts
    s["wappen"] = counts.get("item:pitem_00-3-265-0", s.get("wappen", 0))
    fired = collections.defaultdict(set)
    for e in sv.lists["user_produce_progress_event_list"].values():
        mm = re.match(r"event-detail-(s_card-\d-\d+)-(\d+)$", e.get("produce_step_event_detail_id") or "")
        if mm:
            fired[mm.group(1)].add(int(mm.group(2)))
    for sc in s["supports"]:
        sc["fired"] = sorted(fired.get(sc["id"], ()))
    for day, r in sv.lists["user_produce_progress_schedule_list"].items():
        row = {}
        for k, v in r.items():
            if k.endswith("_produce_step_open_lesson_id") and v:
                a = next((x for x in ATTRS if k.startswith(x)), None)
                if a:
                    row[a] = v
        if row:
            s["schedule"][str(day)] = row
    s["step"] = int(p.get("step_number") or s.get("step") or 0)


def _skill_keys(sim, src, sid):
    """The simulator's count keys for one support skill: its source, id and the effect of the level the card has."""
    out = []
    for s_src, s_sid, lv in sim.s["skills"]:
        if s_src == src and s_sid == sid:
            r = sim.d.M.skills.get((sid, lv)) or {}
            out += [f"{src}|{sid}|{r[f'produceEffectId{i}']}" for i in (1, 2, 3) if r.get(f"produceEffectId{i}")]
    return out


def formation_of(d, upd, resp):
    p = upd["user_produce_progress"]
    panels = {}
    for e in resp.get("effect_results") or []:
        if (e.get("origin") or {}).get("origin_type") != 11:
            continue
        for (sheet, lv), r in d.panels.items():
            if e.get("produce_effect_id") in (r.get("produceEffectIds") or ()):
                panels[sheet] = max(panels.get(sheet, 0), lv)
    sups = [{"id": x["support_card_id"], "level": int(x.get("level") or 1), "rental": bool(x.get("is_rental"))}
            for x in sorted(upd.get("user_produce_progress_support_card_list") or [], key=lambda x: x.get("number") or 0)]
    mems = []
    for m in sorted(upd.get("user_produce_progress_memory_list") or [], key=lambda x: x.get("number") or 0):
        mem = dict(m.get("memory") or {})
        mem["abilities"] = [{"id": a["id"], "level": a.get("level", 1)} for a in m.get("abilities") or mem.get("abilities") or []]
        mems.append(mem)
    return {"idol": {"id": p["idol_card_id"], "level_limit_rank": int(p.get("idol_card_level_limit_rank") or 0),
                     "potential_rank": 0},
            "supports": sups, "memories": mems,
            "account": {"producer_level": int(p.get("producer_level") or 0),
                        "dearness_level": int(p.get("start_dearness_level") or 0), "growth_panel": panels,
                        "true_ends": [TRUE_END[x] for x in p.get("true_end_produce_types") or () if x in TRUE_END]}}


def main_sim(f, sv, start_resp=None, root="00" * 32):
    """A 本戦 opened from the server's own 本戦 state: the selection memory is made from a 選抜 start of the same
    formation (skills, support events) with the server's deck; every step then imports the server's state anyway.
    """
    from GKMSPS.ps.sim import Sim
    sel = Sim(f, root="00" * 32)
    sm = sel.selection_memory()
    prima = data().prima_card(f["idol"]["id"]) if f["idol"].get("prima_stella") else None
    deck = []
    for c in sorted(sv.lists["user_produce_progress_produce_card_list"].values(), key=lambda c: int(c.get("number") or 0)):
        if c.get("deleted") or (prima and c["produce_card_id"] == prima):
            continue
        deck.append({"id": c["produce_card_id"], "up": int(c.get("upgrade_count") or 0),
                     "cz": [[x["id"], int(x.get("customize_count") or 1)] for x in c.get("customizes") or ()]})
    p = sv.p
    fired = collections.Counter()
    for e in (start_resp or {}).get("effect_results") or ():
        o = e.get("origin") or {}
        if o.get("origin_type") == 6 and o.get("origin_owner_id"):
            fired[o["origin_owner_id"]] += 1
    items = [{"id": it["produce_item_id"], "n": max(0, int(it.get("fire_count") or 0) - fired[it["produce_item_id"]])}
             for it in p.get("produce_items") or ()]
    counts = dict(sm["counts"])
    counts.update({"item:" + it["id"]: it["n"] for it in items})
    sm.update(items=items, counts=counts,
              citems=[c["produce_customize_item_id"] for _, c in
                      sorted(sv.lists["user_produce_progress_produce_customize_item_list"].items())])
    sm.update(deck=deck, params={a: int(p.get(a) or 0) for a in ATTRS},
              growth={a: int(p.get(f"{a}_growth_rate_permil") or 0) for a in ATTRS},
              max_stamina=int(p.get("max_stamina") or 0), star=int(p.get("star") or 0),
              star_permil=int(p.get("star_permil") or 0))
    return Sim.honsen(sm, root=root)
