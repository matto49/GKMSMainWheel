# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""H.I.F route planner, part 3 v1 (選抜 and 本戦).

    python -B hif_route.py replay ACTIVATIONS.jsonl OUT.json
    python -B hif_route.py plan ACTIVATIONS.jsonl UUID OUT.json [--from STEP] [--ref UUID] [--beam K]
                                [--exo UUID|none] [--margin POINTS] [--free-card-steps]
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
import GakumasCLI.produce.produce_effects as pe   # noqa: E402
from GakumasCLI.produce.produce_ledger import STEP_TYPE   # noqa: E402

ATTRS = ("vocal", "dance", "visual")
SHORT = {"vo": "vocal", "da": "dance", "vi": "visual"}
EXTRA = HERE / "master_extra.json"
STAR_PERMIL = 500
# school events: parameter by (produce, step); ProduceStepEventSuggestion p_effect-<attr>_addition-<base>
SCHOOL_BASE = {("produce-007", 3): 120, ("produce-007", 6): 120, ("produce-007", 10): 150,
               ("produce-007", 17): 150, ("produce-008", 1): 180, ("produce-008", 4): 180}
SCHOOL_STAMINA = 5
LESSON_PP = {"produce-007": 30, "produce-008": 50}
REFRESH_PERMIL = 500
ACTIVITY_PERMIL = 600
AUDITION_REST = 0.56
FAN_PRESENT_PP = 80
# per (produce, audition): mean stamina change over the exam, star and P point rewards
AUDITION = {("produce-007", "audition_mid1"): {"stamina": -13, "star": 60, "pp": 0, "growth": (20, 80)},
            ("produce-007", "audition_mid2"): {"stamina": -22, "star": 165, "pp": 0, "growth": (80, 200)},
            ("produce-007", "audition_final"): {"stamina": -33, "star": 165, "pp": 0, "growth": (100, 220)},
            ("produce-008", "audition_mid1"): {"stamina": -23, "star": 180, "pp": 200, "growth": None},
            ("produce-008", "audition_final"): {"stamina": -25, "star": 225, "pp": 0, "growth": None}}
PARAM_LIMIT = 3000
STAR_BONUS_SLOPE, STAR_BONUS_CAP = 3, 2000
TRIANGLE_POINTS = 100.0
AUDITION_STEP = {"audition_mid1": "AUDITION_MID1", "audition_mid2": "AUDITION_MID2", "audition_final": "AUDITION_FINAL"}
ADD = {"VOCAL_ADDITION": "vocal", "DANCE_ADDITION": "dance", "VISUAL_ADDITION": "visual"}
CARD_PHASES = {"GET_PRODUCE_CARD", "CHANGE_PRODUCE_CARD", "DELETE_PRODUCE_CARD", "UPGRADE_PRODUCE_CARD",
               "BUY_SHOP_ITEM_PRODUCE_CARD", "GET_PRODUCE_ITEM", "CUSTOMIZE_PRODUCE_CARD",
               "GET_PRODUCE_DRINK", "BUY_SHOP_ITEM_PRODUCE_DRINK"}
SHARE_POWER = 3


def lsum(xs):
    """floats added left to right."""
    t = 0
    for x in xs:
        t = t + x
    return t


def parse_choice(name):
    """('lesson', attr, sp, star) / ('school', attr) / (name,)."""
    if name.startswith("open_lesson_"):
        rest = name[len("open_lesson_"):].split("_")
        return ("lesson", SHORT[rest[0]], "sp" in rest[1:], "star" in rest[1:])
    if name.startswith("event_school_"):
        return ("school", SHORT[name[len("event_school_"):]])
    return (name,)


def lesson_key(choice):
    _, attr, sp, star = choice
    return f"{attr}{'_sp' if sp else ''}{'_star' if star else ''}_produce_step_open_lesson_id"


class World:
    """Master rows, the ability model and the produces of one activations file."""

    def __init__(self, act_path=None, master=pe.MASTER_DIR, produces=None):
        self.M, self.cs = pe.Model(master), pe.CardSearch(master)
        self.lessons = json.load(open(EXTRA, encoding="utf-8"))["open_lessons"]
        self.produces = list(produces) if produces is not None else \
            [json.loads(line) for line in open(act_path, encoding="utf-8")]
        self.etypes = {r["id"]: pe._name(r.get("produceEffectType")) for r in pe._rows("ProduceEffect", master)}
        self.master = master
        self._cat = None

    def get(self, prefix):
        found = [p for p in self.produces if p["uuid"].startswith(prefix)]
        if len(found) != 1:
            raise SystemExit(f"{prefix}: {len(found)} produces")
        return found[0]

    def selection_of(self, p8):
        sid = p8.get("selection_memory_id")
        return next((p for p in self.produces if p.get("produce_id") == "produce-007" and sid
                     and (p.get("summary") or {}).get("user_selection_memory_id") == sid), None)

    @property
    def cat(self):
        if self._cat is None:
            from GakumasCLI.engine.audition import AuditionCatalog
            self._cat = AuditionCatalog.load(self.master)
        return self._cat


# -- the abilities in play ------------------------------------------------------------------
def support_cards(p, world):
    rows = p.get("support_state") or []
    if not rows and p.get("produce_id") == "produce-008":
        sel = world.selection_of(p)
        rows = (sel or {}).get("support_state") or []
    return [(r["support_card_id"], r.get("level") or 0) for r in rows if r.get("support_card_id")]


def carried_counts(p, world):
    """本戦: the trigger counts the 選抜 ended with (support abilities and P-items carry over);
    選抜: none.
    """
    if p.get("produce_id") != "produce-008":
        return {}
    sel = world.selection_of(p)
    out = {}
    for r in (sel or {}).get("support_state") or []:
        for sid, n in zip(r.get("produce_skill_ids") or [], r.get("produce_skill_trigger_counts") or []):
            out[("support:" + r["support_card_id"], sid)] = float(n)
    for it in ((sel or {}).get("summary") or {}).get("produce_items") or []:
        if it.get("trigger_count"):
            out[("item:" + it["id"], it["id"])] = float(it["trigger_count"])
    return out


def init_counts(abil, carried):
    return {(src, sk.id, sk.effect_id): carried[(src, sk.id)] for src, sk in abil if (src, sk.id) in carried}


def abilities(p, world, items):
    out = []
    for cid, lv in support_cards(p, world):
        for _, sk in world.M.support_skills(cid, lv):
            if sk is not None:
                out.append(("support:" + cid, sk))
    if p.get("produce_id") == "produce-007":
        for m in (p.get("summary") or {}).get("memories") or []:
            for ab in (m.get("memory") or {}).get("abilities") or []:
                sk = world.M.memory_skill(ab["id"], ab.get("level"))
                if sk is not None:
                    out.append(("memory:" + (m["memory"].get("user_memory_id") or "?"), sk))
    for it in items:
        for sk in world.M.item_skills(it):
            out.append(("item:" + it, sk))
    return [(src, sk) for src, sk in out if sk.phase != "PRODUCE_START"]


# -- the step model -------------------------------------------------------------------------
class State:
    __slots__ = ("v", "counts", "abil", "deck", "log", "aud", "opp")

    def __init__(self, v, counts, abil, deck):
        self.v, self.counts, self.abil, self.deck = dict(v), dict(counts), abil, deck
        self.log, self.aud = [], {}
        self.opp = 0.0

    def copy(self):
        s = State(self.v, self.counts, self.abil, self.deck)
        s.log, s.aud, s.opp = list(self.log), dict(self.aud), self.opp
        return s


def _add(s, key, x):
    v = s.v
    if key in ATTRS:
        v[key] = min(v["limit"], v[key] + x)
    elif key == "stamina":
        v["stamina"] = max(0.0, min(v["max_stamina"], v["stamina"] + x))
    else:
        v[key] += x


def fire(s, phase, ev, world, track=None):
    """Every ability of `phase` whose condition `ev` meets: its effect x rate, its cap counted."""
    full = None
    for src, sk in s.abil:
        if sk.phase != phase:
            continue
        if full is None:
            full = dict(ev, deck=s.deck)
        if not pe.fires(sk, phase, full, world.cs):
            continue
        key = (src, sk.id, sk.effect_id)
        n = s.counts.get(key, 0.0)
        if sk.cap and n >= sk.cap:
            continue
        rate = (sk.rate_permil or 1000) / 1000
        s.counts[key] = n + rate
        val = (sk.value[0] or 0) * rate
        et = sk.effect_type
        if et in ADD:
            _add(s, ADD[et], val)
        elif et == "STAR_ADDITION":
            _add(s, "star", val * (1000 + STAR_PERMIL) / 1000)
        elif et == "STAMINA_RECOVER_FIX":
            _add(s, "stamina", val)
        elif et == "PRODUCE_POINT_ADDITION":
            _add(s, "pp", val)
        else:
            continue
        if track is not None:
            track[et] += val


def audition_shares(scores):
    tot = sum(scores)
    return [x / tot for x in scores] if tot > 0 else [1 / 3] * 3


def selection_reward(f, r, shares):
    """選抜 audition parameters before growth: F each + floor(R x share), the largest takes the rest."""
    big = max(range(3), key=lambda i: shares[i])
    base = [f + math.floor(r * shares[i]) for i in range(3)]
    base[big] = 3 * f + r - sum(base[i] for i in range(3) if i != big)
    return base


def do_step(s, produce_id, step, row, choice, world, shares=None, track=None):
    """Apply one step's choice to the state (in place)."""
    v, c = s.v, parse_choice(choice)
    s.log.append((step, choice))
    if c[0] == "lesson":
        les = world.lessons.get(row.get(lesson_key(c)) or "")
        if les is None:
            raise KeyError((step, choice, row.get(lesson_key(c))))
        kind = "sp" if c[2] else "normal"
        _add(s, "stamina", -les["stamina"])
        fire(s, "START_LESSON", {"attr": c[1], "kind": kind}, world, track)
        sub = row.get(lesson_key(c)).rsplit("sub_", 1)[1]
        _add(s, c[1], les["mainParameter"] * (1000 + v["g_" + c[1]]) // 1000)
        _add(s, SHORT[sub], les["subParameter"] * (1000 + v["g_" + SHORT[sub]]) // 1000)
        _add(s, "star", les["star"] * (1000 + STAR_PERMIL) // 1000)
        _add(s, "pp", LESSON_PP[produce_id])
        fire(s, "END_LESSON", {"attr": c[1], "kind": kind}, world, track)
        fire(s, "END_LESSON_BEFORE_PRESENT", {"attr": c[1], "kind": kind}, world, track)
    elif c[0] == "school":
        base = SCHOOL_BASE.get((produce_id, step)) or (180 if produce_id == "produce-008" else (120 if step < 7 else 150))
        _add(s, c[1], base)
        _add(s, "stamina", -SCHOOL_STAMINA)
        fire(s, "END_STEP_EVENT_SCHOOL", {}, world, track)
    elif c[0] == "refresh":
        fire(s, "START_REFRESH", {}, world, track)
        _add(s, "stamina", v["max_stamina"] * REFRESH_PERMIL // 1000)
    elif c[0] == "event_activity":
        _add(s, "stamina", v["max_stamina"] * ACTIVITY_PERMIL // 1000)
        fire(s, "END_STEP_EVENT_ACTIVITY", {}, world, track)
    elif c[0] in ("fan_present", "present"):
        fire(s, "START_PRESENT", {}, world, track)
        _add(s, "pp", FAN_PRESENT_PP)
    elif c[0] == "shop":
        fire(s, "START_SHOP", {}, world, track)
    elif c[0] == "customize":
        fire(s, "START_CUSTOMIZE", {}, world, track)
    elif c[0].startswith("audition_"):
        a = AUDITION.get((produce_id, c[0]))
        if a is None:
            raise LookupError(f"{produce_id} 的日程里有 {c[0]}，模型没有这场审查（记录的日程不是这一局的）")
        _add(s, "stamina", round(v["max_stamina"] * AUDITION_REST))
        s.aud[c[0]] = {k: v[k] for k in ATTRS + ("star", "stamina", "max_stamina")}
        fire(s, "START_AUDITION", {}, world, track)
        fire(s, "START_AUDITION_" + pe.AUDITION[c[0]], {}, world, track)
        _add(s, "stamina", a["stamina"])
        if a["growth"]:
            if not shares:
                tot = lsum(max(1.0, v[k]) ** SHARE_POWER for k in ATTRS)
                shares = [max(1.0, v[k]) ** SHARE_POWER / tot for k in ATTRS]
            base = selection_reward(a["growth"][0], a["growth"][1], shares)
            for i, k in enumerate(ATTRS):
                _add(s, k, base[i] + base[i] * v["g_" + k] // 1000)
        _add(s, "star", a["star"])
        _add(s, "pp", a["pp"])
        fire(s, "END_AUDITION", {}, world, track)
        fire(s, "END_AUDITION_" + pe.AUDITION[c[0]], {}, world, track)


def start_state(p, world, occ_index=0):
    """The produce's state after occasion `occ_index` (0 = Start)."""
    o = p["occasions"][occ_index]
    st = o.get("state") or {}
    limit = PARAM_LIMIT + sum(a.get("value") or 0 for a in p.get("activations") or []
                              if a["rpc"] == "Start" and "parameter_limit_up" in (a.get("effect_id") or ""))
    v = {k: float(st.get(k) or 0) for k in ATTRS + ("stamina", "max_stamina", "star")}
    v["pp"] = float(st.get("produce_point") or 0)
    for k in ATTRS:
        v["g_" + k] = st.get(f"{k}_growth_rate_permil") or 0
    v["limit"] = limit
    items = [i for oo in p["occasions"][:occ_index + 1] for i in oo.get("items_gained") or []]
    return v, items, o.get("deck") or []


def schedule(p):
    return {r["step_number"]: r for r in p.get("schedule") or [] if r.get("step_number")}


# -- replay: the recorded choices through the model --------------------------------------------
def actual_after(p):
    """Step -> the recorded state when the step is left (the last occasion acting on it)."""
    out = {}
    for o in p["occasions"]:
        if o.get("at_step") and o.get("state"):
            out[o["at_step"]] = (o["state"], o["rpc"])
    return out


def recorded_card_events(p, world):
    """Step -> the recorded card and item occasions (before the first step: counted at the first)."""
    first = min(schedule(p) or [1])
    by = defaultdict(list)
    for ph, ev in pe.events_of(p, world.etypes):
        if ph in CARD_PHASES:
            by[ev.get("step") or first].append((ph, ev))
    return by


def replay(world):
    out = []
    for p in world.produces:
        r = replay_one(world, p)
        if r:
            out.append(r)
    return out


def replay_one(world, p, card_events=True):
    """The recorded choices through the step model; with card_events, the recorded card and item
    occasions fire abilities too (without: what `plan` simulates)."""
    if p.get("produce_id") not in ("produce-007", "produce-008") or not p["occasions"] \
            or p["occasions"][0]["rpc"] != "Start" or not p["occasions"][0].get("state"):
        return None
    v, items, deck = start_state(p, world)
    abil = abilities(p, world, items)
    s = State(v, init_counts(abil, carried_counts(p, world)), abil, deck)
    sched, act, cards = schedule(p), actual_after(p), recorded_card_events(p, world)
    auds = {a["step_type"]: a for a in p.get("auditions") or []}
    steps, held = [], set(items)
    for step in sorted(sched):
        row = sched[step]
        choice = STEP_TYPE.get(row.get("selected_step_type"), row.get("selected_step_type"))
        if not choice or step not in act:
            break
        track = defaultdict(float)
        shares = audition_shares(auds[choice]["scores"]) if choice in auds else None
        do_step(s, p["produce_id"], step, row, choice, world, shares, track)
        for ph, ev in (cards.get(step, []) if card_events else []):
            if ph == "GET_PRODUCE_ITEM" and ev.get("item") and ev["item"] not in held:
                held.add(ev["item"])
                s.abil = s.abil + [("item:" + ev["item"], sk) for sk in world.M.item_skills(ev["item"])
                                   if sk.phase != "PRODUCE_START"]
            fire(s, ph, {k: ev.get(k) for k in ("card", "upgrade", "item", "drink")}, world, track)
        if any(o.get("deck") for o in p["occasions"] if o.get("at_step") == step):
            s.deck = [o for o in p["occasions"] if o.get("at_step") == step][-1].get("deck") or s.deck
        a, rpc = act[step]
        model = {k: round(s.v[k], 1) for k in ATTRS + ("star", "stamina", "pp")}
        rec = {k: a.get(k) for k in ATTRS + ("star", "stamina")}
        rec["pp"] = a.get("produce_point")
        row_out = {"step": step, "choice": choice, "model": model, "recorded": rec, "left_by": rpc,
                   "abilities": {k: round(x, 1) for k, x in track.items()}}
        if choice in auds and AUDITION.get((p["produce_id"], choice), {}).get("growth"):
            g = AUDITION[(p["produce_id"], choice)]["growth"]
            row_out["audition_base"] = {"model": selection_reward(g[0], g[1], shares),
                                        "recorded": [a.get("lesson_growth_result", {}).get(k) for k in ATTRS]}
        steps.append(row_out)
    if not steps:
        return None
    last = steps[-1]
    err = {k: (last["model"][k] - (last["recorded"][k] or 0)) for k in ATTRS + ("star",)}
    rnd = defaultdict(float)
    for a in p.get("activations") or []:
        if a["origin_type"] == 5 and (a.get("step") or 0) <= last["step"] and \
                not any(a["effect_id"].endswith(f"-{b:04d}_{b:04d}") for b in set(SCHOOL_BASE.values())):
            for k in ATTRS:
                rnd[k] += a.get("d_" + k) or 0
    start = {k: v[k] for k in ATTRS + ("star",)}
    gain = {k: (last["recorded"][k] or 0) - start[k] for k in ATTRS + ("star",)}
    return {"uuid": p["uuid"], "produce": p["produce_id"], "idol": p.get("idol_card_id"),
                "coverage": pe.coverage(p), "steps_replayed": len(steps), "last_step": last["step"],
                "end_error": {k: round(e, 1) for k, e in err.items()},
                "end_error_of_gain": {k: round(err[k] / gain[k], 4) if gain[k] else None for k in err},
                "end_error_of_total": {k: round(err[k] / (last["recorded"][k] or 1), 4) for k in err},
                "random_events": dict(rnd),
                "end_error_with_random_events": {k: round(err[k] + rnd.get(k, 0), 1) for k in ATTRS},
                "steps": steps}


def exo_flows(world, p):
    """Step -> what the recorded produce gained at that step beyond the plan model (cards, items,
    presents, rewards, random events, purchases): added to every route at that step."""
    r = replay_one(world, p, card_events=False)
    out, prev = {}, {k: 0.0 for k in ATTRS + ("star", "stamina")}
    for st in (r or {}).get("steps") or []:
        res = {k: (st["recorded"][k] or 0) - st["model"][k] for k in prev}
        out[st["step"]] = {k: res[k] - prev[k] for k in prev}
        prev = res
    decks = {}
    for o in p["occasions"]:
        if o.get("at_step") and o.get("deck"):
            decks[o["at_step"]] = o["deck"]
    return {"flows": out, "decks": decks}


# -- rating ---------------------------------------------------------------------------------------
class ExamRef:
    """A recorded 本戦 with both auditions: rating for other parameters / stars, the play held fixed."""

    def __init__(self, p8, world):
        self.p8, self.cat, self.rounds = p8, world.cat, {}
        idol = p8.get("idol_card_id")
        last = {}
        for a in p8.get("auditions") or []:
            last[a["step_type"]] = a
        for st in ("audition_mid1", "audition_final"):
            a = last.get(st)
            if not a or not a.get("server"):
                raise ValueError(f"{p8['uuid'][:8]}: no {st} with a server row")
            row = self.cat.difficulty(pe.difficulty_id(idol), st.upper(), "produce-008")
            params = {i + 1: a["before"][k] for i, k in enumerate(ATTRS)}
            self.rounds[st] = {"cfg": row["produceExamBattleConfigId"], "params": params,
                               "base": self.cat.base_permils(row["produceExamBattleConfigId"], params),
                               "scores": a["scores"], "S": a["server"].get("star_bonus_permil") or 0,
                               "star": a["before"]["star"]}
        st0 = p8["occasions"][0].get("state") or {}
        self.start = {k: float(st0.get(k) or 0) for k in ATTRS + ("star",)}
        sel = world.selection_of(p8)
        summ = (sel or {}).get("summary") or {}
        self.sel_end = {k: float(summ[k]) for k in ATTRS + ("star",)} if all(summ.get(k) is not None for k in ATTRS + ("star",)) \
            else dict(self.start)
        self.ref = self.rating_at(*(self._ref_args()))

    def _ref_args(self):
        r1, r2 = self.rounds["audition_mid1"], self.rounds["audition_final"]
        p = lambda r: {k: r["params"][i + 1] for i, k in enumerate(ATTRS)}   # noqa: E731
        return p(r1), r1["star"], p(r2), r2["star"]

    def _score(self, r, params, star):
        prm = {i + 1: max(0, int(params[k])) for i, k in enumerate(ATTRS)}
        base = self.cat.base_permils(r["cfg"], prm)
        d = star - r["star"]
        S = min(STAR_BONUS_CAP, max(0, r["S"] + STAR_BONUS_SLOPE * d)) if r["S"] < STAR_BONUS_CAP or d < 0 else r["S"]
        return lsum(r["scores"][i] * base[i + 1] / r["base"][i + 1] for i in range(3)) * (1000 + S) / (1000 + r["S"])

    def rating_at(self, p_mid1, star_mid1, p_final, star_final):
        s1 = self._score(self.rounds["audition_mid1"], p_mid1, star_mid1)
        s2 = self._score(self.rounds["audition_final"], p_final, star_final)
        return pe.hif_rating([p_final[k] for k in ATTRS], star_final, int(s1 * 1.2), int(s2))

    def after_selection(self, v):
        """Rating when a 本戦 starts from this 選抜 end state: the reference's 本戦 moved by the difference
        to the 選抜 end it started from."""
        p1, s1, p2, s2 = self._ref_args()
        d = {k: v[k] - self.sel_end[k] for k in ATTRS}
        ds = v["star"] - self.sel_end["star"]
        return self.rating_at({k: p1[k] + d[k] for k in ATTRS}, s1 + ds, {k: p2[k] + d[k] for k in ATTRS}, s2 + ds)


def pick_ref(p, world, ref=None):
    if ref:
        return ExamRef(world.get(ref), world)
    idol = p.get("idol_card_id")
    cands = []
    if p.get("produce_id") == "produce-008":
        cands.append(p)
    else:
        sid = (p.get("summary") or {}).get("user_selection_memory_id")
        cands += [q for q in world.produces if q.get("produce_id") == "produce-008" and sid and q.get("selection_memory_id") == sid]
    cands += [q for q in world.produces if q.get("produce_id") == "produce-008" and q.get("idol_card_id") == idol]
    for q in cands:
        try:
            return ExamRef(q, world)
        except (ValueError, KeyError):
            continue
    raise SystemExit(f"{p['uuid'][:8]}: no recorded 本戦 of {idol} with both auditions")


class WeightedRef:
    """a rating object whose parameters count each attribute's gain from START times its weight -- p' = start + w x (p -
    start) -- before the wrapped reference rates them.
    """

    def __init__(self, ref, start, weights):
        self.inner, self.start = ref, dict(start)
        self.w = {"vocal": float(weights.get("vo", 1.0)), "dance": float(weights.get("da", 1.0)),
                  "visual": float(weights.get("vi", 1.0))}
        self.p8 = getattr(ref, "p8", None)
        self.ref = getattr(ref, "ref", None)
        self.rounds = getattr(ref, "rounds", None)

    def _p(self, params):
        return dict(params, **{k: self.start.get(k, 0) + self.w[k] * (params[k] - self.start.get(k, 0)) for k in ATTRS})

    def rating_at(self, p_mid1, star_mid1, p_final, star_final):
        return self.inner.rating_at(self._p(p_mid1), star_mid1, self._p(p_final), star_final)

    def after_selection(self, v):
        return self.inner.after_selection(self._p(v))

    def __getattr__(self, name):
        return getattr(self.inner, name)


def selection_gains(world):
    """the player's own median 本戦 gains from its start to each audition ({audition_mid1 / audition_final: {vocal, dance,
    visual, star}}), over every recorded 本戦 with both auditions; None without one.
    """
    rows = {"audition_mid1": defaultdict(list), "audition_final": defaultdict(list)}
    for q in world.produces:
        if q.get("produce_id") != "produce-008" or not q.get("occasions"):
            continue
        st0 = q["occasions"][0].get("state") or {}
        if not all(st0.get(k) is not None for k in ATTRS + ("star",)):
            continue
        last = {}
        for a in q.get("auditions") or []:
            last[a.get("step_type")] = a
        if not all(st in last and (last[st].get("before") or {}).get("vocal") is not None for st in rows):
            continue
        for st, acc in rows.items():
            for k in ATTRS + ("star",):
                acc[k].append(float(last[st]["before"][k]) - float(st0[k]))
    if not rows["audition_final"]["vocal"]:
        return None
    med = lambda xs: sorted(xs)[len(xs) // 2]      # noqa: E731
    return {st: {k: med(v) for k, v in acc.items()} for st, acc in rows.items()}


class TemplateRef:
    """ExamRef's interface on a top deck -- the idol card's pick pack ``route_ref``."""

    def __init__(self, route_ref, catalog, star_permil=500, gains=None, idol=None):
        from GakumasCLI.produce import audition_eval as AE
        from GakumasCLI.produce import honsen_ref as HR
        rounds = {st: route_ref[st] for st in ("audition_mid1", "audition_final") if st in route_ref}
        self.own = HR.OwnRef(rounds, catalog, AE.EFFECT_BONUS, int(star_permil), idol=idol)
        self.rounds, self.cat, self.gains = rounds, catalog, gains
        self.p8 = {"uuid": "template", "idol_card_id": idol}
        self.ref = self.own.ref
        self.template = dict(route_ref.get("template") or {})

    def rating_at(self, p_mid1, star_mid1, p_final, star_final):
        return self.own.rating_at(p_mid1, star_mid1, p_final, star_final)

    def after_selection(self, v):
        if not self.gains:
            raise LookupError("no recorded 本戦 to carry a 選抜 end to the auditions")
        g1, g2 = self.gains["audition_mid1"], self.gains["audition_final"]
        return self.rating_at({k: v[k] + g1[k] for k in ATTRS}, v["star"] + g1["star"],
                              {k: v[k] + g2[k] for k in ATTRS}, v["star"] + g2["star"])


def template_ref(world, idol, star_permil=500, pack=None, produce_id="produce-008"):
    """TemplateRef of IDOL from its pick pack (pickv.pack_for, no fetch here: the produce card fetched it), or None:
    no pack, no ``route_ref``, or a 選抜 without the player's own 本戦 gains."""
    if pack is None:
        from GakumasCLI.produce import pickv
        pack, _state = pickv.pack_for(idol, fetch=False)
    rr = (pack or {}).get("route_ref")
    if not rr or "audition_final" not in rr:
        return None
    gains = selection_gains(world)
    if produce_id == "produce-007" and not gains:
        return None
    return TemplateRef(rr, world.cat, star_permil, gains, idol)


def value(s, produce_id, ref, margin=0):
    """The rating of a state; `margin` plays the 本戦 auditions with that many points fewer in every
    parameter (the planning objective: a route resting on a parameter just above a shortfall
    baseline loses its value when the unmodelled flows come out lower)."""
    v = s.v
    if produce_id == "produce-007":
        return ref.after_selection({k: v[k] - (margin if k in ATTRS else 0) for k in v})
    a1 = dict(s.aud.get("audition_mid1") or {k: v[k] for k in ATTRS + ("star",)})
    a2 = dict(s.aud.get("audition_final") or {k: v[k] for k in ATTRS + ("star",)})
    for a in (a1, a2):
        for k in ATTRS:
            a[k] -= margin
    return ref.rating_at(a1, a1["star"], a2, a2["star"])


OPPORTUNITY_V1 = {"pp_per_100": {"produce-007": 100.0, "produce-008": 500.0},
                  "choice": {"produce-008|event_activity": 450.0}}


# -- plan ---------------------------------------------------------------------------------------
def step_with_exo(t, p, step, row, ch, world, shares, exo):
    """One step of the model, then the recorded flows of that step the model leaves out."""
    do_step(t, p["produce_id"], step, row, ch, world, (shares or {}).get(ch))
    for key, x in (exo or {}).get("flows", {}).get(step, {}).items():
        _add(t, key, x)
    if (exo or {}).get("decks", {}).get(step):
        t.deck = exo["decks"][step]


class Planner:
    """Routes over the steps `todo` of one produce from state s0 (the rest of the produce as recorded)."""

    def __init__(self, p, world, ref, s0, todo, shares, exo, margin=0, keep=None, opportunity=None):
        self.p, self.world, self.ref, self.s0, self.todo, self.margin = p, world, ref, s0, todo, margin
        self.opportunity = opportunity
        self.shares, self.exo, self.sched = shares, exo, schedule(p)
        self.cands = {st: [STEP_TYPE.get(t, t) for t in self.sched[st].get("step_types") or []] for st in todo}
        for st, ch in (keep or {}).items():
            if st in self.cands and ch in self.cands[st]:
                self.cands[st] = [ch]
        self.held = {st for st, ch in (keep or {}).items() if self.cands.get(st) == [ch]}
        self.cache = {}
        self.crit = audition_configs(p, world)

    def value(self, s):
        """The planning objective: the rating with the margin, less the 選抜 auditions' △ tie-break."""
        val = value(s, self.p["produce_id"], self.ref, self.margin) + s.opp
        short = triangle_penalty(s, self.p["produce_id"], self.crit, self.world, self.margin)
        return val - TRIANGLE_POINTS * short * 10 if short else val

    def step(self, t, st, ch):
        """step_with_exo, plus (with `opportunity`) the value of what the step hands out."""
        pp0 = t.v.get("pp", 0)
        step_with_exo(t, self.p, st, self.sched[st], ch, self.world, self.shares, self.exo)
        o = self.opportunity
        if o:
            pid = self.p["produce_id"]
            t.opp += max(0.0, t.v.get("pp", 0) - pp0) / 100.0 * (o.get("pp_per_100") or {}).get(pid, 0.0)
            t.opp += (o.get("choice") or {}).get(f"{pid}|{parse_choice(ch)[0]}", 0.0)

    def feasible(self, s, step, ch):
        c = parse_choice(ch)
        if c[0] == "lesson":
            les = self.world.lessons.get(self.sched[step].get(lesson_key(c)) or "")
            return les is not None and (step in self.held or s.v["stamina"] >= les["stamina"])
        if c[0] == "school":
            return step in self.held or s.v["stamina"] >= SCHOOL_STAMINA
        return True

    def run(self, route):
        """route: step -> choice."""
        key = tuple(route[st] for st in self.todo)
        if key in self.cache:
            return self.cache[key]
        s = self.s0.copy()
        for st in self.todo:
            if not self.feasible(s, st, route[st]):
                self.cache[key] = (None, None)
                return self.cache[key]
            self.step(s, st, route[st])
        self.cache[key] = (self.value(s), s)
        return self.cache[key]

    def beam(self, k):
        """Per step, the k best states by rating for each whole stamina value."""
        states = [(self.s0, {})]
        for st in self.todo:
            nxt = []
            for s, route in states:
                for ch in self.cands[st]:
                    if not self.feasible(s, st, ch):
                        continue
                    t = s.copy()
                    self.step(t, st, ch)
                    nxt.append((self.value(t), t, {**route, st: ch}))
            groups = defaultdict(list)
            for val, t, route in nxt:
                groups[int(t.v["stamina"])].append((val, t, route))
            states = [(t, route) for g in groups.values() for _, t, route in sorted(g, key=lambda x: -x[0])[:k]]
        return [route for _, route in states]

    def improve(self, route, fixed=()):
        """Change one step at a time while the rating rises."""
        best_val = self.run(route)[0]
        best_val = -1e18 if best_val is None else best_val
        for _ in range(6):
            moved = False
            for st in self.todo:
                if st in fixed:
                    continue
                for ch in self.cands[st]:
                    if ch == route[st]:
                        continue
                    trial = {**route, st: ch}
                    val = self.run(trial)[0]
                    if val is not None and val > best_val + 1e-9:
                        route, best_val, moved = trial, val, True
            if not moved:
                break
        return route, (None if best_val == -1e18 else best_val)

    def improve_clear(self, route, fixed, shorts):
        """change one step at a time toward fewer parameters under their 審査基準 at the auditions ahead, then a smaller
        total shortfall, then the rating -- the continuation that clears the criteria, where ``improve`` keeps the
        rating's (so every option read 「2 △」).
        """
        def key(rt):
            val, st_ = self.run(rt)
            if val is None:
                return None
            n, gap = shorts(st_, rt)
            return (-n, -round(gap, 6), val)
        best_k = key(route)
        for _ in range(6):
            moved = False
            for st in self.todo:
                if st in fixed:
                    continue
                for ch in self.cands[st]:
                    if ch == route[st]:
                        continue
                    trial = {**route, st: ch}
                    k = key(trial)
                    if k is not None and (best_k is None or k > best_k):
                        route, best_k, moved = trial, k, True
            if not moved:
                break
        return route, (None if best_k is None else (-best_k[0], -best_k[1], best_k[2]))


def audition_configs(p, world):
    """audition step name -> its battle config id, for this produce's idol card (None when the master has no row)."""
    out = {}
    idol, pid = p.get("idol_card_id"), p.get("produce_id")
    for name, tail in AUDITION_STEP.items():
        try:
            out[name] = world.cat.difficulty(pe.difficulty_id(idol), tail, pid)["produceExamBattleConfigId"]
        except (KeyError, AttributeError, TypeError):
            continue
    return out


def triangle_penalty(s, produce_id, configs, world, margin=0):
    """Σ over this produce's 選抜 auditions in the state of their △ shortfall (neu.audition.penalty), each parameter taken
    ``margin`` lower; 0 for a 本戦 (its rating plays the penalty)."""
    if produce_id != "produce-007":
        return 0.0
    total = 0.0
    for name, cfg in configs.items():
        a = s.aud.get(name)
        if a is None:
            continue
        total += world.cat.penalty(cfg, {i + 1: a[k] - margin for i, k in enumerate(ATTRS)})
    return total


def triangles(s, configs, world):
    """every audition of the state with a parameter below its criterion: the card's △ line."""
    out = []
    for name, cfg in configs.items():
        a = s.aud.get(name)
        if a is None:
            continue
        crit = world.cat.criteria(cfg)
        short = {k: [round(a[k]), crit[i + 1]] for i, k in enumerate(ATTRS) if crit.get(i + 1) and a[k] < crit[i + 1]}
        if short:
            out.append({"audition": name, "short": short,
                        "penalty": round(world.cat.penalty(cfg, {i + 1: a[k] for i, k in enumerate(ATTRS)}), 4)})
    return out


def audition_checks(s, crits, route, todo):
    """each audition on the route ahead, in route order, with the parameters it is played with and its 審査基準
    (ProduceExamBattleConfig vocal / dance / visual): ``pass`` True = ○ (at or above), False = △ (below: neu.audition
    takes the whole audition's score down).
    """
    out, seen = [], set()
    for st in todo:
        name = parse_choice(route[st])[0]
        a, crit = s.aud.get(name), crits.get(name)
        if name in seen or a is None or not crit:
            continue
        seen.add(name)
        criteria = {k: crit.get(i + 1) for i, k in enumerate(ATTRS)}
        out.append({"audition": name, "params": {k: round(a[k]) for k in ATTRS}, "criteria": criteria,
                    "pass": {k: criteria[k] is None or a[k] >= criteria[k] for k in ATTRS},
                    "star": round(a.get("star", 0)), "stamina": round(a.get("stamina", 0)),
                    "max_stamina": round(a.get("max_stamina", 0))})
    return out


def card_step(row):
    """A step without a lesson or school candidate: shop, customize, present, activity, rest, interval."""
    return not any(parse_choice(STEP_TYPE.get(t, t))[0] in ("lesson", "school") for t in row.get("step_types") or [])


def full_rating(s, star_permil=500):
    """a 本戦 state's rating with both auditions at their full scores (produce_effects.hif_rating_full): the parameters at
    the end and the stars the Final starts with."""
    a2 = s.aud.get("audition_final") or {}
    star = a2.get("star", s.v.get("star", 0))
    return pe.hif_rating_full([s.v[k] for k in ATTRS], star, star_permil)


def plan(world, uuid, from_step=None, ref=None, k=8, exo_from=None, margin=40, free_card_steps=False, hold=None,
         opportunity=None, exam_ref=None, star_permil=500, attr_weight=None):
    """The `plan` command (module docstring)."""
    p = world.get(uuid)
    sched = schedule(p)
    steps = sorted(sched)
    first = from_step or steps[0]
    todo = [x for x in steps if x >= first]
    r = exam_ref if exam_ref is not None else pick_ref(p, world, ref)
    shares = {a["step_type"]: audition_shares(a["scores"]) for a in p.get("auditions") or []}
    own = exo_flows(world, p)
    exo_p = world.get(exo_from) if exo_from and exo_from != "none" else p
    ahead = exo_flows(world, exo_p) if exo_from != "none" else {"flows": {}, "decks": {}}
    exo = {"flows": {st: (own if st < first else ahead)["flows"].get(st, {}) for st in steps},
           "decks": {st: (own if st < first else ahead)["decks"].get(st) for st in steps}}
    past = {st: STEP_TYPE.get(sched[st].get("selected_step_type"), sched[st].get("selected_step_type"))
            for st in steps if st < first}
    start_kind = "replayed"
    mid_login = bool(p.get("lost_start")) and bool(((p.get("occasions") or [{}])[0]).get("at_step"))
    if all(past.values()) and not mid_login:
        v, items, deck = start_state(p, world, 0)
        abil = abilities(p, world, items)
        s0 = State(v, init_counts(abil, carried_counts(p, world)), abil, deck)
        for st in steps:
            if st >= first:
                break
            step_with_exo(s0, p, st, sched[st], past[st], world, shares, exo)
    else:
        occ = p["occasions"]
        cut = next((i for i, o in enumerate(occ) if (o.get("at_step") or 0) >= first), len(occ))
        idx = max([i for i in range(cut) if occ[i].get("state")] or [0])
        v, items, deck = start_state(p, world, idx)
        abil = abilities(p, world, items)
        s0 = State(v, init_counts(abil, carried_counts(p, world)), abil, deck)
        start_kind = "latest"
    s0.log = []
    if attr_weight and any(float(attr_weight.get(a, 1.0)) != 1.0 for a in ("vo", "da", "vi")):
        r = WeightedRef(r, {k: s0.v[k] for k in ATTRS}, attr_weight)
    recorded = {st: STEP_TYPE.get(sched[st].get("selected_step_type"), sched[st].get("selected_step_type"))
                for st in todo}
    keep = {} if free_card_steps else {st: recorded[st] for st in todo if card_step(sched[st]) and recorded[st]}
    keep.update({st: ch for st, ch in (hold or {}).items() if st in todo and ch})
    P = Planner(p, world, r, s0, todo, shares, exo, margin, keep, opportunity)
    full = p["produce_id"] == "produce-008" and any(st >= 9 for st in todo)

    def ahead(s, route):
        """The △ of the auditions still on the route from `first`."""
        return [t for t in triangles(s, P.crit, world)
                if any(st >= first and parse_choice(route[st])[0] == t["audition"] for st in todo)]

    def shorts(s, route):
        """(parameters under their 審査基準 at the auditions ahead, Σ of 1 - parameter / criterion over them)."""
        ts = ahead(s, route)
        return (sum(len(t["short"]) for t in ts),
                sum(1.0 - v / c for t in ts for v, c in t["short"].values() if c))
    rec_val, rec_s = P.run(recorded) if all(recorded.values()) else (None, None)
    seeds = P.beam(k) + ([recorded] if rec_val is not None else [])
    best, best_val = None, None
    for route in sorted(seeds, key=lambda rt: -(P.run(rt)[0] or -1e18))[:4]:
        rt, val = P.improve(route)
        if val is not None and (best_val is None or val > best_val):
            best, best_val = rt, val
    if best is None:
        held_lessons = [f"第{st}步 {ch}" for st, ch in sorted(keep.items()) if st in P.held
                        and parse_choice(ch)[0] == "lesson"
                        and world.lessons.get(sched[st].get(lesson_key(parse_choice(ch))) or "") is None]
        raise LookupError("模型里没有这节课的数据：" + "、".join(held_lessons) if held_lessons
                          else f"第{first}步起没有体力够走完的路线（模型）")
    for _ in range(8):
        decisions, better = [], None
        for i, st in enumerate(todo):
            if len(P.cands[st]) < 2:
                continue
            opts = []
            for ch in P.cands[st]:
                rt, val = P.improve({**best, st: ch}, fixed=set(todo[:i + 1]))
                end_s = P.run(rt)[1] if val is not None else None
                clear = None
                if st == first:
                    rt2, k2 = P.improve_clear({**best, st: ch}, set(todo[:i + 1]), shorts)
                    if k2 is not None:
                        clear = {"short": k2[0], "shortfall": round(k2[1], 3), "rating": round(k2[2], 1),
                                 "route": [[x, rt2[x]] for x in todo if x > st]}
                opts.append({"choice": ch, "best_rating": None if val is None else round(val, 1),
                             "triangles": [t["audition"] for t in ahead(end_s, rt)] if end_s is not None else [],
                             "clear": clear,
                             "full_rating": full_rating(end_s, star_permil) if end_s is not None and full else None})
                if val is not None and val > best_val + 1e-6 and better is None:
                    better = (rt, val)
            top = max((o["best_rating"] for o in opts if o["best_rating"] is not None), default=None)
            for o in opts:
                o["vs_best"] = None if o["best_rating"] is None or top is None else round(o["best_rating"] - top, 1)
            ftop = max((o["full_rating"] for o in opts if o.get("full_rating") is not None), default=None)
            for o in opts:
                o["full_vs_best"] = None if o.get("full_rating") is None or ftop is None else o["full_rating"] - ftop
            decisions.append({"step": st, "plan": best[st], "recorded": recorded.get(st), "options": opts})
            if better:
                break
        if not better:
            break
        best, best_val = better
    end = lambda s: {x: round(s.v[x], 1) for x in ATTRS + ("star", "stamina", "pp")}   # noqa: E731
    best_s = P.run(best)[1]
    nominal = lambda s: round(value(s, p["produce_id"], r), 1)   # noqa: E731
    return {"uuid": p["uuid"], "produce": p["produce_id"], "idol": p.get("idol_card_id"), "from_step": first,
            "start": start_kind, "reference_本戦": r.p8["uuid"], "reference_idol": r.p8.get("idol_card_id"),
            "reference_rating": r.ref,
            "flows_ahead_from": None if exo_from == "none" else exo_p["uuid"],
            "margin": margin, "card_steps_held": sorted(keep),
            "plan": {"route": [[st, best[st]] for st in todo], "rating": nominal(best_s),
                     "rating_with_margin": round(best_val, 1), "end": end(best_s),
                     "opportunity": round(best_s.opp, 1) if opportunity else None,
                     "rating_full": full_rating(best_s, star_permil) if full else None},
            "recorded_route_model": None if rec_val is None else
            {"route": [[st, recorded[st]] for st in todo], "rating": nominal(rec_s),
             "rating_with_margin": round(rec_val, 1), "end": end(rec_s)},
            "plan_minus_recorded": None if rec_val is None else round(nominal(best_s) - nominal(rec_s), 1),
            "plan_minus_recorded_with_margin": None if rec_val is None else round(best_val - rec_val, 1),
            "triangles": ahead(best_s, best), "held": {st: ch for st, ch in (hold or {}).items() if st in todo},
            "auditions": audition_checks(best_s, {n: world.cat.criteria(cfg) for n, cfg in P.crit.items()}, best, todo),
            "decisions": decisions}


def live_plan(world, uuid, step, *, memories=None, k=8, margin=40, hold=None, opportunity=None, exam_ref=None,
              star_permil=500, attr_weight=None, template=True):
    """the route card of a produce in progress (the panel / website 日程, at each step): `plan` from the state before STEP
    with what a live record lacks filled in --
    """
    p = world.get(uuid)
    sched = schedule(p)
    if not [st for st in sched if st >= step]:
        raise LookupError(f"{uuid[:8]}: no schedule from step {step}")
    idol, pid = p.get("idol_card_id"), p.get("produce_id")
    if pid == "produce-007" and memories and not (p.get("summary") or {}).get("memories"):
        q = dict(p, summary=dict(p.get("summary") or {}, memories=[{"memory": m} for m in memories]))
        world.produces = [q if x is p else x for x in world.produces]
    others = [q for q in world.produces if q.get("uuid") != uuid and q.get("produce_id") == pid
              and q.get("idol_card_id") == idol and max(schedule(q) or [0]) >= step and q.get("occasions")]
    exo = max(others, key=lambda q: min(q.get("runs") or [""]))["uuid"] if others else "none"
    ref, kind = None, "own"
    if exam_ref is not None:
        out = plan(world, uuid, step, None, k, exo, margin, hold=hold, opportunity=opportunity, exam_ref=exam_ref,
                   star_permil=star_permil, attr_weight=attr_weight)
        out.update(reference_kind="own_deck", flows_ahead_from=None if exo == "none" else exo)
        return out
    try:
        pick_ref(world.get(uuid), world)
    except SystemExit:
        tref = template_ref(world, idol, star_permil, produce_id=pid) if template else None
        if tref is not None:
            out = plan(world, uuid, step, None, k, exo, margin, hold=hold, opportunity=opportunity, exam_ref=tref,
                       star_permil=star_permil, attr_weight=attr_weight)
            out.update(reference_kind="template", flows_ahead_from=None if exo == "none" else exo,
                       template=tref.template)
            return out
        cards = {r["id"]: r for r in pe._rows("IdolCard", world.master)}
        me = cards.get(idol) or {}
        finals = sorted((q for q in world.produces if q.get("produce_id") == "produce-008"
                         and len(q.get("auditions") or []) >= 2), key=lambda q: min(q.get("runs") or [""]), reverse=True)
        for kind, same in (("character", lambda c: c.get("characterId") == me.get("characterId")),
                           ("plan", lambda c: c.get("planType") == me.get("planType")), ("any", lambda c: True)):
            for q in finals:
                if same(cards.get(q.get("idol_card_id")) or {}):
                    try:
                        ExamRef(q, world)
                    except (ValueError, KeyError):
                        continue
                    ref = q["uuid"]
                    break
            if ref:
                break
        if ref is None:
            raise LookupError("no recorded 本戦 with both auditions to rate a route by")
    out = plan(world, uuid, step, ref, k, exo, margin, hold=hold, opportunity=opportunity, star_permil=star_permil,
               attr_weight=attr_weight)
    out.update(reference_kind=kind, flows_ahead_from=None if exo == "none" else exo)
    return out


def main(argv):
    cmd = argv[1]
    world = World(argv[2])
    if cmd == "replay":
        res = replay(world)
        json.dump(res, open(argv[3], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        for r in res:
            print(r["uuid"][:8], r["produce"], r["idol"], r["coverage"], "steps", r["steps_replayed"],
                  "end error", r["end_error"], "of total", r["end_error_of_total"],
                  "with recorded random events", r["end_error_with_random_events"])
    elif cmd == "plan":
        frm = int(argv[argv.index("--from") + 1]) if "--from" in argv else None
        ref = argv[argv.index("--ref") + 1] if "--ref" in argv else None
        k = int(argv[argv.index("--beam") + 1]) if "--beam" in argv else 8
        exo = argv[argv.index("--exo") + 1] if "--exo" in argv else None
        margin = float(argv[argv.index("--margin") + 1]) if "--margin" in argv else 40
        res = plan(world, argv[3], frm, ref, k, exo, margin, "--free-card-steps" in argv,
                   opportunity=OPPORTUNITY_V1 if "--opportunity" in argv else None)
        json.dump(res, open(argv[4], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(json.dumps({x: res[x] for x in ("uuid", "reference_本戦", "reference_rating", "margin", "plan_minus_recorded",
                                              "plan_minus_recorded_with_margin")},
                         ensure_ascii=False))
        print("plan    ", res["plan"]["rating"], res["plan"]["end"])
        if res["recorded_route_model"]:
            print("recorded", res["recorded_route_model"]["rating"], res["recorded_route_model"]["end"])
        for d in res["decisions"]:
            print(d["step"], "plan", d["plan"], "recorded", d["recorded"], [(o["choice"], o["vs_best"]) for o in d["options"]])


if __name__ == "__main__":
    main(sys.argv)
