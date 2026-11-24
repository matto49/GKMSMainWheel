# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""H.I.F formation planner, part 1 (support cards).

    python -B formation.py extra MIRROR_DIR OUT.json                       # master tables the local master lacks
    python -B formation.py pool OUT.json FRAMES [FRAMES ...]               # own + rental candidates from frames
    python -B formation.py explain ACTIVATIONS.jsonl SELECTION_UUID [OUT.json]   # per card, model vs measured
    python -B formation.py cards ACTIVATIONS.jsonl SELECTION_UUID OUT.json [--level N] [--rarity SSR,SR]
    python -B formation.py best ACTIVATIONS.jsonl SELECTION_UUID POOL.json OUT.json [--memories]
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import GakumasCLI.produce.produce_effects as pe   # noqa: E402
from GakumasCLI.produce.produce_ledger import STEP_TYPE, read_frames, method, is_produce   # noqa: E402

HERE = Path(__file__).resolve().parent
EXTRA = HERE / "master_extra.json"
ATTRS = ("vocal", "dance", "visual")
PTYPE = {1: "vocal", 2: "dance", 3: "visual"}
# open-lesson level by produce and step (本戦 lessons are produce_007 level 04)
LESSON_LEVEL = {("produce-007", 2): "01", ("produce-007", 4): "01", ("produce-007", 9): "02",
                ("produce-007", 11): "02", ("produce-007", 15): "03", ("produce-007", 18): "03",
                ("produce-008", 2): "04", ("produce-008", 5): "04"}
STAR_PERMIL = 500
PARAM_CLAMP = 3200
STAR_CLAMP = 1110
EVENT_P = 0.825
SP_BASE_P = 0.17
PARAM_EFFECT = {"VOCAL_ADDITION": "vocal", "DANCE_ADDITION": "dance", "VISUAL_ADDITION": "visual"}
GROWTH_EFFECT = {"VOCAL_GROWTH_RATE_ADDITION": "vocal", "DANCE_GROWTH_RATE_ADDITION": "dance",
                 "VISUAL_GROWTH_RATE_ADDITION": "visual"}
SP_EFFECT = {"LESSON_VOCAL_SP_CHANGE_RATE_PERMIL_ADDITION": ("vocal",),
             "LESSON_DANCE_SP_CHANGE_RATE_PERMIL_ADDITION": ("dance",),
             "LESSON_VISUAL_SP_CHANGE_RATE_PERMIL_ADDITION": ("visual",),
             "LESSON_SP_CHANGE_RATE_PERMIL_ADDITION": ATTRS}


# -- master tables the local master does not carry --------------------------------------------
def extra(mirror_dir, out_path):
    import yaml
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    mirror = Path(mirror_dir)
    load = lambda t: yaml.load(open(mirror / f"{t}.yaml", encoding="utf-8"), Loader=loader)  # noqa: E731
    details = {r["id"]: r.get("produceEffectIds") or [] for r in load("ProduceStepEventDetail")
               if str(r.get("id", "")).startswith("event-detail-s_card-")}
    events = defaultdict(list)
    for r in load("ProduceEventSupportCard"):
        events[r["supportCardId"]].append({"number": r["number"], "level": r["supportCardLevel"],
                                           "detail": r["produceStepEventDetailId"],
                                           "effects": details.get(r["produceStepEventDetailId"], [])})
    import subprocess
    try:
        commit = subprocess.run(["git", "-C", str(mirror), "rev-parse", "HEAD"], capture_output=True,
                                text=True).stdout.strip()
    except OSError:
        commit = None
    rank = lambda s: int(str(s).rsplit("_", 1)[1])  # noqa: E731
    idol_cards = {
        "level_limit_status_up": [
            {"id": r["id"], "rank": rank(r["rank"]), "vocal": r.get("produceVocal") or 0,
             "dance": r.get("produceDance") or 0, "visual": r.get("produceVisual") or 0,
             "stamina": (r.get("effectValue") or 0) if "IdolCardLevelLimitEffectType_ProduceStamina"
             in (r.get("effectTypes") or []) else 0} for r in load("IdolCardLevelLimitStatusUp")],
        "potential": [
            {"id": r["id"], "rank": rank(r["rank"]), "g_vocal": r.get("produceVocalGrowthRatePermil") or 0,
             "g_dance": r.get("produceDanceGrowthRatePermil") or 0,
             "g_visual": r.get("produceVisualGrowthRatePermil") or 0,
             "stamina": (r.get("effectValue") or 0) if "IdolCardPotentialEffectType_ProduceStamina"
             in (r.get("effectTypes") or []) else 0,
             "item_change": "IdolCardPotentialEffectType_InitialProduceItemChange" in (r.get("effectTypes") or [])}
            for r in load("IdolCardPotential")
            if any(r.get(k) for k in ("produceVocalGrowthRatePermil", "produceDanceGrowthRatePermil",
                                      "produceVisualGrowthRatePermil"))
            or {"IdolCardPotentialEffectType_ProduceStamina", "IdolCardPotentialEffectType_InitialProduceItemChange"}
            & set(r.get("effectTypes") or [])],
        "level_limit_produce_skill": [
            {"id": r["id"], "rank": rank(r["rank"]), "skill": r["produceSkillId"], "level": r["produceSkillLevel"]}
            for r in load("IdolCardLevelLimitProduceSkill")]}
    out = {"source": {"mirror": "vertesan/gakumasu-diff", "commit": commit},
           "open_lessons": {r["id"]: {k: r[k] for k in ("mainParameter", "subParameter", "star", "stamina")}
                            for r in load("ProduceStepOpenLesson")},
           "support_events": {k: sorted(v, key=lambda e: e["number"]) for k, v in sorted(events.items())},
           "idol_cards": idol_cards}
    json.dump(out, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=0, sort_keys=True)
    return out


# -- reference route --------------------------------------------------------------------------
class Route:
    """The reference 選抜 produce and the 本戦 that followed it (when recorded)."""

    def __init__(self, activations_path, selection_uuid, master=pe.MASTER_DIR):
        produces = [json.loads(line) for line in open(activations_path, encoding="utf-8")]
        sel = [p for p in produces if p["uuid"].startswith(selection_uuid)]
        if len(sel) != 1 or sel[0].get("summary_kind") != "selection_memory":
            raise SystemExit(f"{selection_uuid}: not one 選抜 produce with an End summary")
        self.sel = sel[0]
        if pe.coverage(self.sel) != "full":
            print(f"warning: {self.sel['uuid'][:8]} is not fully covered by the frames; its route is incomplete",
                  file=sys.stderr)
        smid = self.sel["summary"].get("user_selection_memory_id")
        self.final = [p for p in produces if p.get("produce_id") == "produce-008" and p.get("summary")
                      and p.get("selection_memory_id") == smid and pe.coverage(p) == "full"]
        etypes = {r["id"]: pe._name(r.get("produceEffectType")) for r in pe._rows("ProduceEffect", master)}
        self.events = pe.events_of(self.sel, etypes)
        for p in self.final[:1]:
            self.events += [(ph, ev) for ph, ev in pe.events_of(p, etypes) if ph != "PRODUCE_START"]
        self.lessons = []
        for p in [self.sel] + self.final[:1]:
            self.lessons += lessons_of(p)
        s = self.sel["summary"]
        self.growth = {a: s.get(f"{a}_growth_rate_permil") or 0 for a in ATTRS}
        self.sp = {a: s.get(f"lesson_{a}_sp_change_rate_permil") or 0 for a in ATTRS}
        rental = {c["id"]: c["rental"] for c in (self.sel.get("formation") or {}).get("support_cards") or []}
        self.formation = [(c["support_card_id"], c.get("level") or 0, rental.get(c["support_card_id"], False))
                          for c in s.get("support_cards") or []]
        mrental = {m["id"]: m["rental"] for m in (self.sel.get("formation") or {}).get("memories") or []}
        self.memories = [(m["memory"], mrental.get(m["memory"].get("user_memory_id"), False))
                         for m in s.get("memories") or [] if m.get("memory")]
        last = (self.final[0] if self.final else self.sel)
        self.end_params = {a: last["last"].get(a) or 0 for a in ATTRS}
        self.end_star = last["last"].get("star") or 0
        fin = [a for a in (self.final[0].get("auditions") if self.final else []) if a["step_type"] == "audition_final"]
        self.pre_r2_star = fin[-1]["before"]["star"] if fin else self.end_star
        self.complete = bool(self.final)
        self.measured = {}
        for r in last.get("support_state") or []:
            for sid, n in zip(r.get("produce_skill_ids") or [], r.get("produce_skill_trigger_counts") or []):
                self.measured[(r["support_card_id"], sid)] = int(n)


def lessons_of(p):
    """Open lessons a produce took: (produce, step, main, sub, star lesson, SP taken)."""
    subs = {}
    for r in p.get("schedule") or []:
        types, sp = r.get("step_types") or [], r.get("step_sub_parameter_types") or []
        for t, s in zip(types, sp):
            subs[(r.get("step_number"), t)] = PTYPE.get(s)
    taken = {r.get("step_number"): r.get("selected_step_type") for r in p.get("schedule") or []}
    out = []
    for step, t in sorted(taken.items(), key=lambda x: x[0] or 0):
        name = STEP_TYPE.get(t, "")
        lk = pe.lesson_of(name)
        if not lk or not name.startswith("open_lesson") or (p.get("produce_id"), step) not in LESSON_LEVEL:
            continue
        out.append({"produce": p["produce_id"], "step": step, "main": lk[0], "sub": subs.get((step, t)),
                    "star": name.endswith("_star"), "sp": lk[1] == "sp"})
    return out


# -- exam side: what one more parameter point / star is worth in the H.I.F rating -------------
STAR_BONUS_SLOPE = 3.0
STAR_BONUS_CAP = 2000


def _slope(regimes, x):
    prev = 0
    for th, _base, mul in regimes:
        if x <= th:
            return mul
        prev = th
    return 0


def exam_weights(route, master=pe.MASTER_DIR):
    """Rating per parameter point and per star, first order around the reference 本戦: the
    parameter term (2 / point below 3200), the star term (7.5 / star below 1110), and the two
    audition terms -- score_X x d(permil_X)/d(param_X) / permil_X for each parameter (the play held
    fixed, which is exact to first order for the best play), score x 3 / (1000 + S) per star while
    the star bonus S is under its cap -- times the slope of the round's rating piece (round 1
    on score, which is point / 1.2; round 2 plus its star gain x 1.5 x 7.5).
    """
    w = {a: 2.0 for a in ATTRS}
    w["star"] = 7.5
    w["source"] = "parameter and star terms only"
    if not route.final:
        return w
    from GakumasCLI.engine.audition import AuditionCatalog
    cat = AuditionCatalog.load(master)
    last = {}
    for a in route.final[0].get("auditions") or []:
        last[a["step_type"]] = a
    idol = route.final[0].get("idol_card_id") or route.sel.get("idol_card_id")
    detail = {}
    for st, rating_slope in (("audition_mid1", None), ("audition_final", None)):
        a = last.get(st)
        if not a or not a.get("server"):
            continue
        row = cat.difficulty(pe.difficulty_id(idol), st.upper(), "produce-008")
        cfg = cat.battle_configs[row["produceExamBattleConfigId"]]
        params = {i + 1: a["before"][k] for i, k in enumerate(ATTRS)}
        factor = 1.0 - cat.penalty(cfg["id"], params)
        score = a["server"]["score"]
        if st == "audition_mid1":
            slope = _slope(pe.HIF_ROUND1, score)
        else:
            slope = _slope(pe.HIF_ROUND2, score) + 7.5 * 1.5 * _slope(pe.HIF_STAR_GAIN, score)
        parts = {}
        for i, k in enumerate(ATTRS):
            col = ("vocal", "dance", "visual")[i]
            p0 = params[i + 1]
            d_raw = cat._interpolate(cfg["produceExamBattleScoreConfigId"], col, p0 + 1) - \
                cat._interpolate(cfg["produceExamBattleScoreConfigId"], col, p0)
            base = a["server"].get(f"{k}_permil") or 1
            d_score = a["scores"][i] * d_raw * factor / base
            w[k] += slope * d_score
            parts[k] = round(slope * d_score, 4)
        S = a["server"].get("star_bonus_permil") or 0
        d_star = score * STAR_BONUS_SLOPE / (1000 + S) if S < STAR_BONUS_CAP else 0.0
        w["star"] += slope * d_star
        parts["star"] = round(slope * d_star, 4)
        detail[st] = {"score": score, "rating_slope": slope, "per_point": parts, "star_bonus_permil": S}
    w["source"] = "parameter, star and audition terms"
    w["auditions"] = detail
    return w


class ExamModel:
    """H.I.F rating change for a change of parameters and star, the recorded 本戦 play held
    fixed: each audition's per-parameter score scales with its base permil (AuditionCatalog,
    the game's own rounding and shortfall penalty) and with 1000 + star bonus (+3 permil per
    star up to 2000); the rating is produce_effects.hif_rating on the new scores (round-1 input
    = point = score x 1.2).
    """

    def __init__(self, route, master=pe.MASTER_DIR):
        self.route, self.rounds = route, {}
        if not route.final:
            return
        from GakumasCLI.engine.audition import AuditionCatalog
        self.cat = AuditionCatalog.load(master)
        idol = route.final[0].get("idol_card_id") or route.sel.get("idol_card_id")
        last = {}
        for a in route.final[0].get("auditions") or []:
            last[a["step_type"]] = a
        for st in ("audition_mid1", "audition_final"):
            a = last.get(st)
            if not a or not a.get("server"):
                self.rounds = {}
                return
            row = self.cat.difficulty(pe.difficulty_id(idol), st.upper(), "produce-008")
            params = {i + 1: a["before"][k] for i, k in enumerate(ATTRS)}
            self.rounds[st] = {"cfg": row["produceExamBattleConfigId"], "params": params,
                               "base": self.cat.base_permils(row["produceExamBattleConfigId"], params),
                               "scores": a["scores"], "score": a["server"]["score"],
                               "S": a["server"].get("star_bonus_permil") or 0, "star": a["before"]["star"]}
        self.ref = self.rating({a: 0 for a in ATTRS}, 0)

    def rating(self, d_params, d_star):
        out = {}
        for st, r in self.rounds.items():
            params = {i + 1: max(0, r["params"][i + 1] + d_params[k]) for i, k in enumerate(ATTRS)}
            base = self.cat.base_permils(r["cfg"], params)
            S = min(STAR_BONUS_CAP, max(0, r["S"] + STAR_BONUS_SLOPE * d_star)) if r["S"] < STAR_BONUS_CAP or d_star < 0 \
                else r["S"]
            k_star = (1000 + S) / (1000 + r["S"])
            out[st] = sum(r["scores"][i] * base[i + 1] / r["base"][i + 1] for i in range(3)) * k_star
        fin = self.rounds["audition_final"]
        params = [fin["params"][i + 1] + d_params[k] for i, k in enumerate(ATTRS)]
        return pe.hif_rating(params, fin["star"] + d_star, int(out["audition_mid1"] * 1.2), int(out["audition_final"]))

    def delta(self, d_params, d_star):
        if not self.rounds:
            end, star0 = self.route.end_params, self.route.pre_r2_star
            return (sum(2 * (min(PARAM_CLAMP, end[a] + d_params[a]) - min(PARAM_CLAMP, end[a])) for a in ATTRS)
                    + 7.5 * (min(STAR_CLAMP, star0 + d_star) - min(STAR_CLAMP, star0)))
        return self.rating(d_params, d_star) - self.ref


# -- what one card brings ---------------------------------------------------------------------
class Planner:
    def __init__(self, route, master=pe.MASTER_DIR, extra_path=EXTRA):
        self.M, self.cs, self.route = pe.Model(master), pe.CardSearch(master), route
        self.effects = {r["id"]: r for r in pe._rows("ProduceEffect", master)}
        self.cards = {r["id"]: r for r in pe._rows("SupportCard", master)}
        x = json.load(open(extra_path, encoding="utf-8"))
        self.lessons_t, self.sevents = x["open_lessons"], x["support_events"]
        self.w = exam_weights(route, master)
        self.exam = ExamModel(route, master)
        self.ref_mems = [self.memory(m) for m, _ in route.memories]
        ref = [self.card(cid, lv) for cid, lv, _ in route.formation] + self.ref_mems
        self.g0 = {a: route.growth[a] - sum(c["growth"][a] for c in ref) for a in ATTRS}
        self.sp0 = {a: route.sp[a] - sum(c["sp"][a] for c in ref) for a in ATTRS}
        self.ref_part = self.formation_part(ref)

    def card(self, card_id, level):
        out = {"id": card_id, "level": level, "params": dict.fromkeys(ATTRS, 0.0), "star": 0.0,
               "growth": dict.fromkeys(ATTRS, 0), "sp": dict.fromkeys(ATTRS, 0), "value_up": 0,
               "upgrade_permil": 0, "other": defaultdict(float), "abilities": [], "events": []}
        for order, sk in self.M.support_skills(card_id, level):
            if sk is None:
                continue
            v = sk.value[0] or 0
            if sk.effect_type in GROWTH_EFFECT:
                out["growth"][GROWTH_EFFECT[sk.effect_type]] += v
            elif sk.effect_type in SP_EFFECT:
                for a in SP_EFFECT[sk.effect_type]:
                    out["sp"][a] += v
            elif sk.effect_type == "SUPPORT_CARD_EVENT_PARAMETER_ADDITION_VALUE_UP":
                out["value_up"] += v
            elif sk.effect_type == "SUPPORT_CARD_PRODUCE_CARD_UPGRADE_PROBABILITY_UP":
                out["upgrade_permil"] += v
            else:
                n = pe.occasions_count(sk, self.route.events, self.cs) * sk.rate_permil / 1000
                if sk.effect_type in PARAM_EFFECT:
                    out["params"][PARAM_EFFECT[sk.effect_type]] += n * v
                else:
                    out["other"][sk.effect_type] += n * v
                out["abilities"].append({"skill": sk.id, "phase": sk.phase, "count": n, "value": v,
                                         "effect": sk.effect_type})
        for e in self.sevents.get(card_id, []):
            if level < e["level"]:
                continue
            for eid in e["effects"]:
                ef = self.effects.get(eid) or {}
                et = pe._name(ef.get("produceEffectType"), "PRODUCE_EFFECT_TYPE_")
                base = ef.get("effectValueMin") or 0
                if et in PARAM_EFFECT:
                    v = base * (1000 + out["value_up"]) // 1000
                    out["params"][PARAM_EFFECT[et]] += EVENT_P * v
                    out["events"].append({"number": e["number"], "effect": et, "value": v})
                else:
                    out["other"]["event:" + (et or eid)] += EVENT_P
                    out["events"].append({"number": e["number"], "effect": et or eid})
        out["other"] = dict(out["other"])
        base = (self.cards.get(card_id) or {}).get("produceCardUpgradePermil") or 0
        out["exam_upgrade_permil"] = base * (1000 + out["upgrade_permil"]) // 1000
        out["exam_upgrade_parameter"] = pe._name((self.cards.get(card_id) or {}).get(
            "produceCardUpgradeLessonParameterType"), "PRODUCE_PARAMETER_TYPE_")
        return out

    def memory(self, mem):
        """What one memory brings on the produce side (its abilities, replayed like a card's);
        its exam side -- audition enchants, its skill card -- is listed in `exam`, not valued."""
        out = {"id": mem.get("user_memory_id"), "level": None, "params": dict.fromkeys(ATTRS, 0.0), "star": 0.0,
               "growth": dict.fromkeys(ATTRS, 0), "sp": dict.fromkeys(ATTRS, 0), "value_up": 0,
               "upgrade_permil": 0, "other": defaultdict(float), "abilities": [], "events": [], "exam": [],
               "card": mem.get("produce_card"), "card_phase": mem.get("produce_card_phase_type"),
               "idol_card_id": mem.get("idol_card_id"), "grade": mem.get("grade")}
        for ab in mem.get("abilities") or []:
            sk = self.M.memory_skill(ab["id"], ab.get("level"))
            if sk is None:
                out["exam"].append({"ability": ab["id"], "unresolved": True})
                continue
            v = sk.value[0] or 0
            if sk.effect_type in GROWTH_EFFECT:
                out["growth"][GROWTH_EFFECT[sk.effect_type]] += v
            elif sk.effect_type in SP_EFFECT:
                for a in SP_EFFECT[sk.effect_type]:
                    out["sp"][a] += v
            elif sk.phase.startswith("START_AUDITION") or sk.effect_type.startswith("EXAM"):
                out["exam"].append({"ability": ab["id"], "phase": sk.phase, "effect": sk.effect_type,
                                    "effect_id": sk.effect_id})
            else:
                n = pe.occasions_count(sk, self.route.events, self.cs) * sk.rate_permil / 1000
                if sk.effect_type in PARAM_EFFECT:
                    out["params"][PARAM_EFFECT[sk.effect_type]] += n * v
                else:
                    out["other"][sk.effect_type] += n * v
                out["abilities"].append({"skill": sk.id, "phase": sk.phase, "count": n, "value": v,
                                         "effect": sk.effect_type})
        out["other"] = dict(out["other"])
        return out

    # -- formation level: lessons depend on the summed growth and SP rates --------------------
    def lesson_gain(self, growth, sp):
        gain, star = dict.fromkeys(ATTRS, 0.0), 0.0
        for les in self.route.lessons:
            lv = LESSON_LEVEL[(les["produce"], les["step"])]
            kind = "star" if les["star"] else "parameter"
            sub = les["sub"] or "vocal"
            q = 1 - (1 - SP_BASE_P) * (1 - min(1000, max(0, sp[les["main"]])) / 1000)
            for is_sp, w in ((False, 1 - q), (True, q)):
                row = self.lessons_t[f"p_step_open_lesson-produce_007-{lv}-{kind}{'-sp' if is_sp else ''}-sub_{sub[:2]}"]
                gain[les["main"]] += w * (row["mainParameter"] * (1000 + growth[les["main"]]) // 1000)
                gain[sub] += w * (row["subParameter"] * (1000 + growth[sub]) // 1000)
                star += w * (row["star"] * (1000 + STAR_PERMIL) // 1000)
        return gain, star

    def formation_part(self, cards):
        growth = {a: getattr(self, "g0", self.route.growth)[a] + sum(c["growth"][a] for c in cards) for a in ATTRS}
        sp = {a: getattr(self, "sp0", self.route.sp)[a] + sum(c["sp"][a] for c in cards) for a in ATTRS}
        lessons, star = self.lesson_gain(growth, sp)
        params = {a: lessons[a] + sum(c["params"][a] for c in cards) for a in ATTRS}
        return {"params": params, "star": star, "growth": growth, "sp": sp, "lessons": lessons,
                "upgrade_permil": sum(c["upgrade_permil"] for c in cards)}

    def value(self, cards):
        part = self.formation_part(cards)
        proj = {a: self.route.end_params[a] - self.ref_part["params"][a] + part["params"][a] for a in ATTRS}
        star = self.route.pre_r2_star - self.ref_part["star"] + part["star"]
        d_params = {a: proj[a] - self.route.end_params[a] for a in ATTRS}
        rating = self.exam.delta(d_params, star - self.route.pre_r2_star)
        return rating, proj, star, part


# -- candidates -------------------------------------------------------------------------------
def pool_from_frames(paths):
    """Own cards: the latest User/Get inventory or, before that, every own card a recorded formation used at its level;
    rentals: the latest ListRentalSupportCard response."""
    own, used, rentals = None, {}, None
    mem_own, mem_seen, start_req = None, {}, None
    for path in paths:
        for f in read_frames(path):
            d = f.get("decoded") or {}
            if f.get("dir") == "out" and is_produce(f) and method(f) == "Start":
                start_req = d
            if f.get("dir") != "in":
                continue
            if f.get("rpc") == "/client.api.User/Get" and (d.get("user_data") or {}).get("user_support_card_list"):
                own = [{"id": r["support_card_id"], "level": r.get("level") or 1,
                        "limit_rank": r.get("level_limit_rank") or 0}
                       for r in d["user_data"]["user_support_card_list"]]
            if f.get("rpc") == "/client.api.User/Get" and (d.get("user_data") or {}).get("user_memory_list"):
                mem_own = d["user_data"]["user_memory_list"]
            if is_produce(f) and method(f) == "End" and d.get("selection_memory"):
                rent = {m.get("user_memory_id"): bool(m.get("rental_public_user_id"))
                        for m in (start_req or {}).get("memories") or []}
                for m in d["selection_memory"].get("memories") or []:
                    mm = m.get("memory") or {}
                    if mm.get("abilities"):
                        mem_seen[mm["user_memory_id"]] = (mm, rent.get(mm["user_memory_id"], False))
            if is_produce(f) and method(f) == "ListRentalSupportCard":
                rentals = []
                for key in ("support_cards", "suggestion_support_cards", "npc_support_cards"):
                    for r in d.get(key) or []:
                        rentals.append({"id": r["support_card_id"], "level": r.get("level") or 1,
                                        "limit_rank": r.get("level_limit_rank") or 0, "list": key})
            for s in ((d.get("common_response") or {}).get("updated_data") or {}).get(
                    "user_produce_progress_support_card_list") or []:
                if not s.get("is_rental") and s.get("support_card_id"):
                    used[s["support_card_id"]] = max(used.get(s["support_card_id"], 0), s.get("level") or 0)
    source = "inventory" if own else "formations used"
    if own is None:
        own = [{"id": k, "level": v} for k, v in sorted(used.items())]
    strip = lambda m: {k: v for k, v in m.items() if k not in ("image_path", "rental_public_user_id")}  # noqa: E731
    return {"own": own, "rentals": rentals or [], "own_source": source,
            "memories_own": [strip(m) for m in mem_own] if mem_own else
            [strip(m) for m, r in mem_seen.values() if not r],
            "memories_rental": [strip(m) for m, r in mem_seen.values() if r],
            "memories_source": "inventory" if mem_own else "memories used in recorded 選抜 (rental ones: contents only "
                                                           "for those rented; ListRentalMemory carries no contents)"}


def best(planner, pool, keep=5, memories=None):
    """5 own + 1 rental support cards, distinct card ids, with the given memories (default: the
    reference's).
    """
    own = [(c["id"], c["level"]) for c in pool["own"]]
    rentals = [(c["id"], c["level"]) for c in pool["rentals"]]
    mems = planner.ref_mems if memories is None else memories
    cache = {}

    def card(cid, lv):
        if (cid, lv) not in cache:
            cache[(cid, lv)] = planner.card(cid, lv)
        return cache[(cid, lv)]

    def score(sel):
        return planner.value([card(*x) for x in sel] + mems)[0]

    alone = sorted(own, key=lambda x: -score([x]))
    chosen = []
    for x in alone:
        if x[0] not in {c[0] for c in chosen}:
            chosen.append(x)
        if len(chosen) == keep:
            break
    rental = max((r for r in rentals if r[0] not in {c[0] for c in chosen}),
                 key=lambda r: score(chosen + [r]), default=None)
    sel = chosen + ([rental] if rental else [])
    cur = score(sel)
    improved = True
    while improved:
        improved = False
        for i in range(len(sel)):
            pool_i = rentals if (rental and i == len(sel) - 1) else own
            for x in pool_i:
                if x[0] in {c[0] for j, c in enumerate(sel) if j != i}:
                    continue
                trial = sel[:i] + [x] + sel[i + 1:]
                s = score(trial)
                if s > cur + 1e-9:
                    sel, cur, improved = trial, s, True
    rating, proj, star, part = planner.value([card(*x) for x in sel] + mems)
    return {"own": sel[:keep], "rental": sel[keep] if len(sel) > keep else None, "rating_part": rating,
            "params": proj, "star": star, "growth": part["growth"], "sp": part["sp"],
            "cards": [{k: v for k, v in card(*x).items() if k != "abilities"} for x in sel]}


def best_memories(planner, pool, cards, keep=3):
    """3 own + 1 rental memories for fixed support cards, by their produce-side value only
    (start parameters, growth, SP, lesson triggers); the exam side is listed per memory."""
    own = [planner.memory(m) for m in pool.get("memories_own") or []]
    rentals = [planner.memory(m) for m in pool.get("memories_rental") or []]
    score = lambda sel: planner.value(cards + sel)[0]          # noqa: E731
    chosen = []
    for m in sorted(own, key=lambda m: -score([m])):
        if m["id"] not in {c["id"] for c in chosen}:
            chosen.append(m)
        if len(chosen) == keep:
            break
    rental = max((r for r in rentals if r["id"] not in {c["id"] for c in chosen}),
                 key=lambda r: score(chosen + [r]), default=None)
    sel = chosen + ([rental] if rental else [])
    return sel, score(sel)


def memory_table(planner, pool, cards):
    """Every pool memory's produce-side gain swapped in for one reference memory (best slot),
    with its exam-side items listed: the exam side is what the 本戦 enchants and the memory's
    card do in the auditions and is not valued here."""
    out = []
    ref = planner.ref_mems
    base = planner.value(cards + ref)[0]
    for src, rental in (("memories_own", False), ("memories_rental", True)):
        for m in pool.get(src) or []:
            mm = planner.memory(m)
            if mm["id"] in {r["id"] for r in ref}:
                gain, replaced = base, "(in the reference)"
            else:
                swaps = [(planner.value(cards + ref[:i] + [mm] + ref[i + 1:])[0], ref[i]["id"])
                         for i in range(len(ref))]
                gain, replaced = max(swaps) if swaps else (base, None)
            out.append({"memory": mm["id"], "rental": rental, "idol_card_id": mm["idol_card_id"],
                        "grade": mm["grade"], "produce_side_gain": round(gain - base, 1), "replaces": replaced,
                        "params": mm["params"], "growth": mm["growth"], "sp": mm["sp"],
                        "exam": [x.get("effect_id") or x.get("ability") for x in mm["exam"]], "card": mm["card"]})
    return sorted(out, key=lambda x: -x["produce_side_gain"])


def best_both(planner, pool, rounds=2, choose_memories=False):
    """Support cards, and with choose_memories the memories in turn (produce side only)."""
    mems = planner.ref_mems
    res = None
    for _ in range(rounds):
        res = best(planner, pool, memories=mems)
        if not choose_memories or not (pool.get("memories_own") or pool.get("memories_rental")):
            break
        cards = [planner.card(cid, lv) for cid, lv in res["own"] + ([res["rental"]] if res["rental"] else [])]
        mems, _ = best_memories(planner, pool, cards)
    res = best(planner, pool, memories=mems)
    res["memories"] = [{"id": m["id"], "idol_card_id": m["idol_card_id"], "grade": m["grade"],
                        "params": m["params"], "growth": m["growth"], "sp": m["sp"], "card": m["card"],
                        "exam": m["exam"]} for m in mems]
    res["memories_chosen"] = "by produce side" if choose_memories else "the reference's"
    cards = [planner.card(cid, lv) for cid, lv in res["own"] + ([res["rental"]] if res["rental"] else [])]
    res["memory_table"] = memory_table(planner, pool, cards)
    return res


# -- explain: per card, model vs what the produce measured --------------------------------------
def explain(planner, route):
    """Per card of the reference formation: the model's ability counts against the cumulative
    trigger counts the server holds at the end (本戦 End when the pair is complete, else the 選抜
    End), its event values against the event rows, and the lesson part."""
    rows = []
    event_rows = defaultdict(list)
    for p in [route.sel] + route.final[:1]:
        for a in p.get("activations") or []:
            if a["origin_type"] == 5 and str(a["owner"]).startswith("event-detail-s_card") and a.get("value"):
                event_rows[a["owner"][len("event-detail-"):-3]].append(a["value"])
    for cid, lv, rental in route.formation:
        c = planner.card(cid, lv)
        abil = []
        for a in c["abilities"]:
            measured = route.measured.get((cid, a["skill"]))
            abil.append({"skill": a["skill"], "model": a["count"], "measured": measured, "value": a["value"],
                         "effect": a["effect"]})
        rows.append({"card": cid, "level": lv, "rental": rental, "params": c["params"], "growth": c["growth"],
                     "sp": c["sp"], "value_up": c["value_up"], "upgrade_permil": c["upgrade_permil"],
                     "abilities": abil, "event_values_model": [e["value"] for e in c["events"] if "value" in e],
                     "event_values_measured": event_rows.get(cid, [])})
    start_rows = defaultdict(lambda: {"params": dict.fromkeys(ATTRS, 0), "growth": dict.fromkeys(ATTRS, 0)})
    for a in route.sel.get("activations") or []:
        if a["origin_type"] != 4 or a["rpc"] != "Start":
            continue
        et = pe._name((planner.effects.get(a["effect_id"]) or {}).get("produceEffectType"), "PRODUCE_EFFECT_TYPE_")
        if et in PARAM_EFFECT:
            start_rows[a["owner"]]["params"][PARAM_EFFECT[et]] += a.get("value") or 0
        elif et in GROWTH_EFFECT:
            start_rows[a["owner"]]["growth"][GROWTH_EFFECT[et]] += a.get("value") or 0
    mems = []
    for (m, rental), mm in zip(route.memories, planner.ref_mems):
        start = {k: v for k, v in mm["params"].items()}
        mems.append({"memory": mm["id"], "rental": rental, "idol_card_id": mm["idol_card_id"],
                     "model": {"params": start, "growth": mm["growth"], "sp": mm["sp"]},
                     "start_rows": start_rows.get(mm["id"]), "lesson_abilities": mm["abilities"],
                     "exam": mm["exam"], "card": mm["card"], "card_phase": mm["card_phase"]})
    return {"selection": route.sel["uuid"], "final": [p["uuid"] for p in route.final],
            "complete_pair": route.complete, "g0": planner.g0, "sp0": planner.sp0,
            "lessons": route.lessons, "formation_part": planner.ref_part, "cards": rows, "memories": mems}


def main(argv):
    cmd = argv[1]
    if cmd == "extra":
        x = extra(argv[2], argv[3])
        print(len(x["open_lessons"]), "open lessons,", len(x["support_events"]), "support cards with events,",
              x["source"])
        return
    if cmd == "pool":
        res = pool_from_frames(argv[3:])
        json.dump(res, open(argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(res["own_source"], len(res["own"]), "own,", len(res["rentals"]), "rentals")
        return
    route = Route(argv[2], argv[3])
    planner = Planner(route)
    print("weights", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in planner.w.items() if k != "auditions"},
          file=sys.stderr)
    if cmd == "explain":
        res = explain(planner, route)
        if len(argv) > 4:
            json.dump(res, open(argv[4], "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
        for c in res["cards"]:
            diff = [a for a in c["abilities"] if a["measured"] is None or abs(a["model"] - a["measured"]) > 1e-9]
            print(c["card"], c["level"], "rental" if c["rental"] else "own",
                  {k: round(v, 1) for k, v in c["params"].items()}, "growth", c["growth"], "sp", c["sp"],
                  "events", c["event_values_model"], "vs", c["event_values_measured"],
                  "ability counts off:", [(a["skill"][28:80], a["model"], a["measured"]) for a in diff])
        for m in res["memories"]:
            print("memory", m["memory"][:8], "rental" if m["rental"] else "own", m["idol_card_id"],
                  "model", {k: round(v) for k, v in m["model"]["params"].items()}, m["model"]["growth"],
                  "Start rows", m["start_rows"], "exam items", len(m["exam"]))
        print("g0", res["g0"], "sp0", res["sp0"], "lessons", len(res["lessons"]), "complete pair", res["complete_pair"])
    elif cmd == "cards":
        level = int(argv[argv.index("--level") + 1]) if "--level" in argv else None
        rar = set(argv[argv.index("--rarity") + 1].split(",")) if "--rarity" in argv else {"SSR", "SR"}
        ref_cards = [planner.card(cid, lv) for cid, lv, _ in route.formation]
        mems = planner.ref_mems
        out = []
        for cid, r in sorted(planner.cards.items()):
            if pe._name(r.get("rarity"), "SUPPORT_CARD_RARITY_") not in rar or cid not in planner.M.levels:
                continue
            lv = level or max(t for rows in planner.M.levels[cid].values() for t, _, _ in rows)
            c = planner.card(cid, lv)
            swaps = [(planner.value(ref_cards[:i] + [c] + ref_cards[i + 1:] + mems)[0], route.formation[i][0])
                     for i in range(len(ref_cards)) if route.formation[i][0] != cid]
            gain, replaced = max(swaps) if swaps else (0.0, None)
            out.append({"id": cid, "name": r.get("name"), "type": pe._name(r.get("type"), "SUPPORT_CARD_TYPE_"),
                        "plan": pe._name(r.get("planType"), "PRODUCE_PLAN_TYPE_"), "level": lv,
                        "rating_gain_in_reference": round(gain, 1), "replaces": replaced,
                        "params": {k: round(v, 1) for k, v in c["params"].items()},
                        "growth": c["growth"], "sp": c["sp"], "value_up": c["value_up"],
                        "upgrade_permil": c["upgrade_permil"]})
        out.sort(key=lambda x: -x["rating_gain_in_reference"])
        json.dump({"reference": route.sel["uuid"], "formation": route.formation, "weights": planner.w,
                   "cards": out}, open(argv[4], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        for x in out[:15]:
            print(x["rating_gain_in_reference"], "for", x["replaces"], x["id"], x["name"], x["type"], x["level"],
                  x["params"], x["growth"], x["sp"])
    elif cmd == "best":
        pool = json.load(open(argv[4], encoding="utf-8"))
        res = best_both(planner, pool, choose_memories="--memories" in argv)
        ref = planner.value([planner.card(cid, lv) for cid, lv, _ in route.formation] + planner.ref_mems)
        res["reference"] = {"formation": route.formation, "rating_part": ref[0], "params": ref[1], "star": ref[2]}
        json.dump(res, open(argv[5], "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
        print("best", res["own"], "rental", res["rental"], round(res["rating_part"], 1),
              {k: round(v) for k, v in res["params"].items()}, round(res["star"]))
        print("memories", res["memories_chosen"], [(m["id"][:8], m["idol_card_id"]) for m in res["memories"]])
        for m in res["memory_table"][:6]:
            print("  memory", m["memory"][:8], "rental" if m["rental"] else "own", m["idol_card_id"], "produce side",
                  m["produce_side_gain"], "for", (m["replaces"] or "")[:8], "exam items", len(m["exam"]))
        print("ref ", route.formation, round(ref[0], 1), {k: round(v) for k, v in ref[1].items()}, round(ref[2]))


if __name__ == "__main__":
    main(sys.argv)
