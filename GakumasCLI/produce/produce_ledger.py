# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Produce-layer decision ledger.

    python -B produce_ledger.py OUT.jsonl FRAMES [FRAMES ...]
"""
import gzip
import json
import sys

STEP_TYPE = {
    1: "lesson_vo", 2: "lesson_vo_sp", 3: "lesson_vo_hard", 4: "lesson_da", 5: "lesson_da_sp",
    6: "lesson_da_hard", 7: "lesson_vi", 8: "lesson_vi_sp", 9: "lesson_vi_hard", 10: "event",
    11: "event_activity", 12: "event_school", 13: "shop", 14: "refresh", 15: "present",
    16: "audition_mid1", 17: "audition_mid2", 18: "audition_final",
    19: "self_lesson_vo", 20: "self_lesson_vo_sp", 21: "self_lesson_da", 22: "self_lesson_da_sp",
    23: "self_lesson_vi", 24: "self_lesson_vi_sp", 25: "business", 26: "event_business",
    27: "fan_present", 28: "customize", 47: "interval", 48: "event_school_vo",
    49: "event_school_da", 50: "event_school_vi",
}
for _n, _a in ((35, "vo"), (39, "da"), (43, "vi")):
    STEP_TYPE[_n] = f"open_lesson_{_a}"
    STEP_TYPE[_n + 1] = f"open_lesson_{_a}_sp"
    STEP_TYPE[_n + 2] = f"open_lesson_{_a}_star"
    STEP_TYPE[_n + 3] = f"open_lesson_{_a}_sp_star"
for _n, _a in ((29, "vo"), (31, "da"), (33, "vi")):
    STEP_TYPE[_n] = f"legend_lesson_{_a}"
    STEP_TYPE[_n + 1] = f"legend_lesson_{_a}_sp"

# client.enums.ProduceEffectType (client_enums.proto of the run's schema-work)
EFFECT_KIND = {
    33: "card_upgrade", 34: "card_duplicate", 35: "card_duplicate_upgrade", 36: "card_change",
    37: "card_change_upgrade", 38: "card_delete", 43: "reward", 44: "reward_set",
    129: "customize_item_upgrade", 132: "card_change_select",
}
# client.enums.ProduceResourceType
RESOURCE = {
    1: "card", 2: "item", 3: "drink", 4: "produce_point", 5: "stamina", 6: "vocal", 7: "dance",
    8: "visual", 9: "vote", 10: "star", 11: "customize_item", 100: "high_score_gold",
    994: "card_change", 995: "card_customize", 997: "card_upgrade", 998: "card_delete", 999: "set",
}
PICK_RANGE = {1: "select", 2: "random", 3: "all"}
# effects whose pick indexes select cards of the current deck, not offered rewards
DECK_TARGET_TYPES = {33, 34, 35, 36, 37, 38}


def read_frames(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                if raw.endswith("\n"):
                    raise
                return


class FrameCache:
    """the frames of capture files, kept between reads."""

    TAIL = 64

    def __init__(self):
        import threading
        self._files = {}
        self._lock = threading.Lock()
        self.reread = 0
        self.skipped = 0

    def frames(self, path):
        with self._lock:
            return self._frames(str(path))

    def _frames(self, p):
        import os
        st = os.stat(p)
        have = self._files.get(p)
        if have is not None and have["mtime"] == st.st_mtime_ns and have["size"] == st.st_size:
            return have["frames"]
        if p.endswith(".gz"):
            have = {"frames": list(read_frames(p)), "offset": 0, "tail": b""}
        else:
            have = self._read(p, have, st.st_size)
        have["mtime"], have["size"] = st.st_mtime_ns, st.st_size
        self._files[p] = have
        return have["frames"]

    def _read(self, p, have, size):
        with open(p, "rb") as fh:
            start = 0
            if have is not None and 0 < have["offset"] <= size:
                fh.seek(have["offset"] - len(have["tail"]))
                if fh.read(len(have["tail"])) == have["tail"]:
                    start = have["offset"]
                else:
                    self.reread += 1
            fh.seek(start)
            data = fh.read()
        end = data.rfind(b"\n") + 1
        frames = list(have["frames"]) if start else []
        for raw in data[:end].split(b"\n"):
            line = raw.strip()
            if not line:
                continue
            try:
                frames.append(json.loads(line.decode("utf-8")))
            except ValueError:
                if start:
                    self.reread += 1
                    return self._read(p, None, size)
                self.skipped += 1
        offset = start + end
        if end:
            tail = data[max(0, end - self.TAIL):end]
            if len(tail) < self.TAIL and start:
                tail = (have["tail"] + tail)[-self.TAIL:]
        else:
            tail = have["tail"] if start else b""
        return {"frames": frames, "offset": offset, "tail": tail}


def method(frame):
    return (frame.get("rpc") or "").rsplit("/", 1)[-1]


def is_produce(frame):
    return ".Produce/" in (frame.get("rpc") or "")


def resource(r):
    out = {"type": RESOURCE.get(r.get("resource_type"), r.get("resource_type"))}
    for key in ("resource_id", "quantity", "upgrade_count", "price", "next_price",
                "discount_permil", "stamina_recover_value"):
        if r.get(key) not in (None, "", 0) or key == "resource_id" and r.get(key):
            out[key] = r.get(key)
    if r.get("resource_level") and "upgrade_count" not in out:
        out["upgrade_count"] = r["resource_level"]
    return out


class Produce:
    def __init__(self, uuid):
        self.uuid = uuid
        self.progress = {}
        self.cards = {}
        self.order = []
        self.schedule = {}
        self.effects = {}
        self.shop = {}
        self.interval = {}
        self.present = {}
        self.events = {}
        self.last_event = None

    # --- state updates -------------------------------------------------
    def apply(self, common):
        upd = (common or {}).get("updated_data") or {}
        dele = (common or {}).get("deleted_data") or {}
        if upd.get("user_produce_progress"):
            self.progress = dict(upd["user_produce_progress"])
        for c in upd.get("user_produce_progress_produce_card_list") or []:
            n = c.get("number")
            if n not in self.cards:
                self.order.append(n)
            self.cards[n] = c
        for c in dele.get("user_produce_progress_produce_card_list") or []:
            n = c.get("number")
            if n in self.cards and not self.cards[n].get("deleted"):
                self.cards[n] = dict(self.cards[n], deleted=True)
        for s in upd.get("user_produce_progress_schedule_list") or []:
            self.schedule[s.get("step_number")] = s
        for s in dele.get("user_produce_progress_schedule_list") or []:
            self.schedule.pop(s.get("step_number"), None)
        for e in upd.get("user_produce_progress_effect_list") or []:
            self.effects[e.get("number")] = e
        for e in dele.get("user_produce_progress_effect_list") or []:
            self.effects.pop(e.get("number"), None)
        for key, store in (("user_produce_progress_shop_list", self.shop),
                           ("user_produce_progress_interval_list", self.interval),
                           ("user_produce_progress_present_list", self.present)):
            rows = upd.get(key) or []
            if len(rows) > 1:
                store.clear()
            for s in rows:
                store[s.get("position_number")] = s
            for s in dele.get(key) or []:
                store.pop(s.get("position_number"), None)
        for e in upd.get("user_produce_progress_event_list") or []:
            self.events[e.get("number")] = e
            self.last_event = e.get("produce_step_event_detail_id")
        for e in dele.get("user_produce_progress_event_list") or []:
            self.events.pop(e.get("number"), None)

    def deck(self):
        return [c for n, c in sorted(self.cards.items()) if not c.get("deleted")]

    def snapshot(self):
        p = self.progress
        return {
            "step_number": p.get("step_number"),
            "step_type": STEP_TYPE.get(p.get("step_type"), p.get("step_type")),
            "vocal": p.get("vocal"), "dance": p.get("dance"), "visual": p.get("visual"),
            "stamina": p.get("stamina"), "max_stamina": p.get("max_stamina"),
            "produce_point": p.get("produce_point"), "star": p.get("star"),
            "drinks": list(p.get("produce_drink_ids") or []),
            "items": [i.get("produce_item_id") for i in p.get("produce_items") or []],
            "remain_reroll": p.get("produce_card_remain_select_reroll_count"),
            "remain_exclude": p.get("produce_card_remain_exclude_count"),
            "deck": [{"number": c.get("number"), "id": c.get("produce_card_id"),
                      "upgrade": c.get("upgrade_count", 0),
                      "customizes": len(c.get("customizes") or [])} for c in self.deck()],
        }

    def next_offer(self):
        """The pending effect the client resolves next (is_next, select first)."""
        cands = [e for e in self.effects.values() if e.get("is_next")]
        if not cands:
            cands = [e for e in self.effects.values()
                     if e.get("pick_range_type") == 1 and e.get("rewards")]
        if not cands:
            return None
        cands.sort(key=lambda e: (e.get("pick_range_type") != 1, e.get("number", 0)))
        return cands[0]


def offer_view(effect):
    return {
        "effect_number": effect.get("number"),
        "effect_type": EFFECT_KIND.get(effect.get("type"), effect.get("type")),
        "produce_effect_id": effect.get("produce_effect_id"),
        "pick_range": PICK_RANGE.get(effect.get("pick_range_type"), effect.get("pick_range_type")),
        "pick_count": [effect.get("pick_count_min"), effect.get("pick_count_max")],
        "candidates": [resource(r) for r in effect.get("rewards") or []],
        "offered_indexes": effect.get("pick_indexes"),
        "origin_owner_id": effect.get("origin_owner_id"),
        "origin_id": effect.get("origin_id"),
    }


def slots_view(store):
    return [dict(resource(s), position=pos, purchased=bool(s.get("purchased")),
                 discount=sum(int(x or 0) for x in s.get("effect_resource_discount_permils") or []))
            for pos, s in sorted(store.items())]


def card_changes(before, produce):
    """Card-list rows that differ after a response (new, deleted, upgraded, customized)."""
    out = []
    for n, c in sorted(produce.cards.items()):
        b = before.get(n)
        if b == c:
            continue
        kind = "new" if b is None else (
            "deleted" if c.get("deleted") and not b.get("deleted") else (
                "upgraded" if c.get("upgrade_count", 0) != b.get("upgrade_count", 0) else (
                    "customized" if (c.get("customizes") or []) != (b.get("customizes") or [])
                    else ("changed" if c.get("produce_card_id") != b.get("produce_card_id") else "other"))))
        out.append({"number": n, "id": c.get("produce_card_id"), "upgrade": c.get("upgrade_count", 0),
                    "kind": kind, "origin_type": c.get("origin_type")})
    return out


def deck_index_card(produce, index):
    deck = produce.deck()
    by_number = deck[index] if index is not None and 0 <= index < len(deck) else None
    first_seen = [n for n in produce.order if not produce.cards[n].get("deleted")]
    by_order = produce.cards[first_seen[index]] if index is not None and 0 <= index < len(first_seen) else None
    return {"by_number": by_number and by_number.get("number"),
            "by_first_seen": by_order and by_order.get("number")}


def run(paths):
    produces = {}
    current = None
    pending = None
    out = []

    def finish(resp, produce, rec, before_cards, before_effects):
        dec = (resp or {}).get("decoded") or {}
        produce.apply(dec.get("common_response"))
        if rec["method"] == "ActivateEffect":
            resolve_activation(rec, produce, before_effects)
        rec["response_status"] = (resp or {}).get("status")
        rec["provided"] = [resource(r) for r in dec.get("provided_rewards") or []]
        if "before_produce_point" in dec or "after_produce_point" in dec:
            rec["produce_point"] = [dec.get("before_produce_point"), dec.get("after_produce_point")]
        if "before_stamina" in dec or "after_stamina" in dec:
            rec["stamina"] = [dec.get("before_stamina"), dec.get("after_stamina")]
        rec["card_changes"] = card_changes(before_cards, produce)
        rec["drinks_after"] = list(produce.progress.get("produce_drink_ids") or [])
        kind = rec["kind"]
        if kind in ("reward_reroll", "reward_exclude"):
            num = (rec.get("offer") or {}).get("effect_number")
            offer = produce.effects.get(num) if num is not None else produce.next_offer()
            rec["after"] = offer_view(offer) if offer else None
        if kind in ("shop_reroll",):
            rec["after"] = slots_view(produce.shop)
        if kind in ("interval_reroll",):
            rec["after"] = slots_view(produce.interval)
        out.append(rec)

    for path in paths:
        run_id = path.replace("\\", "/").rsplit("/", 1)[-1].split(".")[0]
        for frame in read_frames(path):
            if not is_produce(frame):
                continue
            m = method(frame)
            dec = frame.get("decoded") or {}
            src = f"{run_id}#seq={frame.get('seq')}"
            if frame.get("dir") == "out":
                if m == "Start":
                    current = Produce(None)
                    current.start_request = dec
                else:
                    uuid = dec.get("device_produce_uuid")
                    if uuid and not str(uuid).startswith("adv_") and len(str(uuid)) > 8:
                        current = produces.setdefault(uuid, Produce(uuid))
                if current is None:
                    continue
                rec = decision(m, dec, current)
                if rec is None:
                    pending = None
                    continue
                rec.update({"source": src, "uuid": current.uuid, "method": m,
                            "produce_id": current.progress.get("produce_id")
                            or (getattr(current, "start_request", {}) or {}).get("produce_id"),
                            "idol_card_id": current.progress.get("idol_card_id"),
                            "state": current.snapshot()})
                pending = (current, rec, {n: dict(c) for n, c in current.cards.items()},
                           {n: dict(e) for n, e in current.effects.items()})
            else:
                if m == "Start" and dec.get("device_produce_uuid") and current is not None:
                    current.uuid = dec["device_produce_uuid"]
                    produces[current.uuid] = current
                if pending is not None and pending[1]["method"] == m:
                    produce, rec, before, before_effects = pending
                    finish(frame, produce, rec, before, before_effects)
                    pending = None
                elif current is not None:
                    current.apply(dec.get("common_response"))
    return out, produces


def resolve_activation(rec, produce, before_effects):
    """Re-attach an ActivateEffect to the effect the server actually resolved."""
    resolved = []
    for num, e in before_effects.items():
        after = produce.effects.get(num)
        if after is None or (e.get("is_next") and not after.get("is_next")):
            if e.get("rewards") or e.get("pick_range_type"):
                resolved.append(e)
    picks = rec.get("pick_indexes") or []
    if len(resolved) > 1:
        fit = [e for e in resolved if picks and all(0 <= i < len(e.get("rewards") or []) for i in picks)]
        resolved = fit or resolved
    rec["resolved_effects"] = len(resolved)
    if not resolved:
        if not picks:
            rec["kind"] = "activate_auto"
        return
    offer = sorted(resolved, key=lambda e: e.get("number", 0))[0]
    rec["offer"] = offer_view(offer)
    rec["player_choice"] = offer.get("pick_range_type") == 1
    rec.pop("decode_suspect", None)
    if offer.get("type") in DECK_TARGET_TYPES:
        deck = rec["state"]["deck"]
        rec["targets"] = [deck[i] for i in picks if 0 <= i < len(deck)]
        rec["chosen"] = []
        if any(not 0 <= i < len(deck) for i in picks):
            rec["decode_suspect"] = "pick_indexes out of deck range"
    else:
        cands = offer.get("rewards") or []
        rec["chosen"] = [resource(cands[i]) for i in picks if 0 <= i < len(cands)]
        if any(not 0 <= i < len(cands) for i in picks):
            rec["decode_suspect"] = "pick_indexes out of candidate range"
    cands = offer.get("rewards") or []
    rec["skipped"] = offer.get("pick_range_type") == 1 and not picks
    if offer.get("type") == 44 and offer.get("pick_range_type") == 1:
        types = {RESOURCE.get(r.get("resource_type")) for r in cands}
        rec["kind"] = {"card": "card_pick", "drink": "drink_pick", "item": "item_pick",
                       "customize_item": "customize_item_pick"}.get(
            types.pop() if len(types) == 1 else None, "reward_pick")
    elif offer.get("type") in EFFECT_KIND:
        rec["kind"] = EFFECT_KIND[offer["type"]]
    else:
        rec["kind"] = f"activate_type_{offer.get('type')}"


def decision(m, dec, produce):
    """Build the record for a decision-carrying request, or None."""
    if m == "Next":
        step = produce.progress.get("step_number")
        nxt = produce.schedule.get((step or 0) + 1) or {}
        return {"kind": "schedule", "candidates": [STEP_TYPE.get(t, t) for t in nxt.get("step_types") or []],
                "chosen": STEP_TYPE.get(dec.get("step_type"), dec.get("step_type")),
                "select_number": dec.get("select_number"), "schedule_step": (step or 0) + 1}
    if m == "ActivateEffect":
        offer = produce.next_offer()
        rec = {"kind": "activate", "pick_indexes": dec.get("pick_indexes") or []}
        if offer:
            rec["offer"] = offer_view(offer)
            cands = offer.get("rewards") or []
            rec["chosen"] = [resource(cands[i]) for i in rec["pick_indexes"] if 0 <= i < len(cands)]
            if any(not 0 <= i < len(cands) for i in rec["pick_indexes"]):
                rec["decode_suspect"] = "pick_indexes out of candidate range"
            if offer.get("type") == 44 and offer.get("pick_range_type") == 1:
                types = {RESOURCE.get(r.get("resource_type")) for r in cands}
                rec["kind"] = {"card": "card_pick", "drink": "drink_pick",
                               "item": "item_pick"}.get(types.pop() if len(types) == 1 else None,
                                                         "reward_pick")
            elif offer.get("type") in EFFECT_KIND:
                rec["kind"] = EFFECT_KIND[offer["type"]]
        return rec
    if m == "RerollSelectProduceCard":
        offer = produce.next_offer()
        return {"kind": "reward_reroll", "offer": offer_view(offer) if offer else None}
    if m == "ExcludeProduceCard":
        offer = produce.next_offer()
        rec = {"kind": "reward_exclude", "pick_index": dec.get("pick_index"),
               "offer": offer_view(offer) if offer else None}
        if offer and dec.get("pick_index") is not None:
            cands = offer.get("rewards") or []
            i = dec["pick_index"]
            rec["excluded"] = resource(cands[i]) if 0 <= i < len(cands) else None
        return rec
    if m == "StepPresentReceive":
        pres = produce.present.get(dec.get("position_number")) or {}
        cands = pres.get("rewards") or []
        return {"kind": "present_pick", "position": dec.get("position_number"),
                "candidates": [resource(r) for r in cands],
                "chosen": [resource(cands[i]) for i in dec.get("reward_indexes") or [] if 0 <= i < len(cands)],
                "reward_indexes": dec.get("reward_indexes")}
    if m in ("StepShopStart", "StepIntervalStart"):
        store = produce.shop if m == "StepShopStart" else produce.interval
        return {"kind": "shop_visit" if m == "StepShopStart" else "interval_visit",
                "candidates": slots_view(store)}
    if m in ("StepShopBuy", "StepIntervalBuy"):
        store = produce.shop if m == "StepShopBuy" else produce.interval
        slot = store.get(dec.get("position_number")) or {}
        rec = {"kind": "shop_buy" if m == "StepShopBuy" else "interval_buy",
               "position": dec.get("position_number"), "slot": resource(slot) if slot else None,
               "candidates": slots_view(store)}
        if dec.get("number") is not None:
            rec["target_number"] = dec.get("number")
        if dec.get("deck_produce_card_index") is not None:
            rec["deck_index"] = dec.get("deck_produce_card_index")
            rec["deck_index_resolves"] = deck_index_card(produce, dec.get("deck_produce_card_index"))
        return rec
    if m in ("StepShopReroll", "StepIntervalReroll"):
        store = produce.shop if m == "StepShopReroll" else produce.interval
        return {"kind": "shop_reroll" if m == "StepShopReroll" else "interval_reroll",
                "before": slots_view(store)}
    if m == "StepCustomizeSelect":
        return {"kind": "customize", "deck_index": dec.get("deck_produce_card_index"),
                "deck_index_resolves": deck_index_card(produce, dec.get("deck_produce_card_index")),
                "customize_id": dec.get("produce_card_customize_id")}
    if m == "StepEvent" and dec.get("suggestion_index") is not None:
        return {"kind": "event_choice", "suggestion_index": dec.get("suggestion_index"),
                "event_detail_id": produce.last_event}
    if m == "StepBusinessSelect":
        return {"kind": "business_choice", "business_type": dec.get("business_type")}
    if m in ("StepAuditionRefresh", "StepRefresh"):
        return {"kind": "refresh" if m == "StepRefresh" else "audition_refresh"}
    if m == "DiscardDrink":
        drinks = list(produce.progress.get("produce_drink_ids") or [])
        return {"kind": "discard_drink", "drink_indexes": dec.get("drink_indexes"),
                "discarded": [drinks[i] for i in dec.get("drink_indexes") or [] if 0 <= i < len(drinks)]}
    return None


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    out, produces = run(argv[2:])
    with open(argv[1], "w", encoding="utf-8", newline="\n") as fh:
        for rec in out:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    kinds = {}
    for rec in out:
        kinds[rec["kind"]] = kinds.get(rec["kind"], 0) + 1
    print(json.dumps({"decisions": len(out), "produces": len(produces), "kinds": kinds},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
