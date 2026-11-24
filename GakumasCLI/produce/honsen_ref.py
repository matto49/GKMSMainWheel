# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""the route card rates a 本戦 route on the player's own deck instead of a recorded reference 本戦."""
from __future__ import annotations

import json

import GakumasCLI.produce.produce_effects as pe
from GakumasCLI.produce import audition_eval as AE
from GakumasCLI.produce import hif_advice as HA

ATTRS = ("vocal", "dance", "visual")
STAGE = {"audition_mid1": "main1", "audition_final": "main2"}
STEP = {"audition_mid1": 7, "audition_final": 9}
SEEDS = 3
TRANSITIONS = 1_000_000
SEARCH_SEED = 182
_CACHE = {}
CAL_SEARCH_SEEDS = (182, 183, 184)
CAL_MIN, CAL_MAX = 0.5, 3.0
_CAL = {}


def _key(p, ahead):
    live = HA.live_parts(p)
    keep = {k: live[k] for k in ("deck_cards", "produce_item_ids", "drink_ids", "support_cards",
                                 "lesson_start_enchant_ids", "main_item_enchant_ids")}
    return json.dumps([p.progress.get("idol_card_id"), ahead, keep, sorted((p.memories or {}).keys())],
                      sort_keys=True, ensure_ascii=False)


class OwnRef:
    """hif_route.ExamRef on this produce's own 本戦 auditions (see the module docstring)."""

    def __init__(self, rounds, catalog, effect_bonus, star_permil, known=None, idol=None, level=1.0):
        self.rounds, self.cat = rounds, catalog
        self.A, self.star_permil = int(effect_bonus), int(star_permil)
        self.level = float(level)
        self.known = dict(known or {})
        self.p8 = {"uuid": "own", "idol_card_id": idol}
        args = []
        for st in ("audition_mid1", "audition_final"):
            r = rounds.get(st)
            args += [{k: r["params"][str(i + 1)] for i, k in enumerate(ATTRS)} if r else {k: 0 for k in ATTRS},
                     r["star"] if r else 0]
        self.ref = self.rating_at(*args) if "audition_final" in rounds else None

    def score(self, st, params, star):
        """The audition's score at ``params`` ({vocal, dance, visual}) and ``star``, its searched play held fixed."""
        if st in self.known:
            return float(self.known[st])
        r = self.rounds.get(st)
        if r is None:
            return 0.0
        prm = {str(i + 1): max(0, int(params[k])) for i, k in enumerate(ATTRS)}
        new = AE.permils(self.cat, r["cfg"], prm, self.A, pe.star_bonus(STAGE[st], int(star)))
        return self.level * sum(r["scores"][i] * new[str(i + 1)] / max(1, r["permils"][str(i + 1)]) for i in range(3))

    def rating_at(self, p_mid1, star_mid1, p_final, star_final):
        s1 = self.score("audition_mid1", p_mid1, star_mid1)
        s2 = self.score("audition_final", p_final, star_final)
        return pe.hif_rating([p_final[k] for k in ATTRS], star_final, int(s1 * 1.2), int(s2), self.star_permil)


def _search(master, lo, seeds, transitions, prior, search_seeds=(SEARCH_SEED,)):
    """[(score, [vocal, dance, visual])] of the best path on each exam seed and search seed (the production job, then a
    replay).
    """
    from GakumasCLI.scenario import hif_route_search as HRS
    from GakumasCLI.scenario.hif import build_hif_loadout_session
    out = []
    for s, ss in ((s, ss) for s in seeds for ss in search_seeds):
        session, _ = build_hif_loadout_session(master, dict(lo, seed=int(s)))
        try:
            path, best, _steps = HRS._search_paths(session, None, nrpa_seconds=0, nrpa_transitions=int(transitions),
                                                   tail_seconds=0.0, seed=int(ss), prior=prior, from_scratch=True,
                                                   stabilization=3, run_transitions=500000)
        except ValueError:
            continue
        try:
            st = HRS._replay(session, path).state
        except ValueError:
            pm = [int(lo["score_bonus_permils"][k]) for k in ("1", "2", "3")]
            out.append((int(best), [int(best) * x / max(1, sum(pm)) for x in pm]))
            continue
        out.append((int(best), [int(st.score_vocal), int(st.score_dance), int(st.score_visual)]))
    return out


def _prior(prior=None):
    import GakumasCLI.paths as P
    from GakumasCLI.scenario import hif_route_search as HRS
    return prior if prior is not None else HRS.load_prior(str(P.ENGINE_ROOT / "priors" / "prior_rev80_drink_turns.json"))


def calibration(tracker, p, master, model, transitions=TRANSITIONS, prior=None, search=None):
    """(factor, source) -- this run's level on the searched scores (see CAL_SEARCH_SEEDS), or (1.0, None) when no
    audition of the run is played yet or its ratio says nothing.
    """
    prog = p.progress or {}
    idol = prog.get("idol_card_id")
    played = None
    snap = (p.audition_snaps or {}).get(16)
    if snap and p.scores.get(snap["step"]):
        played = (p, snap, "main1", "第一轮")
    else:
        for q in reversed(list((tracker.produces or {}).values()) if tracker is not None else []):
            qp = q.progress or {}
            snap = (getattr(q, "audition_snaps", None) or {}).get(18)
            if (q is not p and qp.get("produce_id") == HA.SELECTION and qp.get("idol_card_id") == idol and snap
                    and q.scores.get(snap["step"])):
                played = (q, snap, "sel3", "选拔最后一场")
                break
    if played is None:
        return 1.0, None
    q, snap, stage, label = played
    key = (id(q), stage, snap["step"], q.scores[snap["step"]])
    if key not in _CAL:
        lo = AE.audition_loadout(HA.Frozen(snap), stage, master.audition_catalog, model)
        seed = int(snap["progress"].get("exam_seed_value") or 1)
        if search is None:
            pr = _prior(prior)
            search = lambda lo_, sd, ss: _search(master, lo_, sd, transitions, pr, ss)   # noqa: E731
        res = search(lo, [seed], CAL_SEARCH_SEEDS)
        _CAL[key] = sum(r[0] for r in res) / len(res) if res else None
    searched, player = _CAL[key], int(q.scores[snap["step"]])
    if not searched:
        return 1.0, None
    factor = player / searched
    src = {"label": label, "stage": stage, "player": player, "search": round(searched)}
    if factor < CAL_MIN:
        return 1.0, None
    return min(CAL_MAX, factor), src


def own_ref(p, master, model, projection=None, seeds=SEEDS, transitions=TRANSITIONS, prior=None, search=None,
            level=None):
    """OwnRef of produce ``p`` (hif_advice.Tracker's, a 本戦) at its current step."""
    catalog = master.audition_catalog
    prog = p.progress
    step = int(prog.get("step_number") or 0)
    ahead = [st for st, n in STEP.items() if n > step]
    known = {"audition_mid1": p.scores.get(7) or 0} if "audition_mid1" not in ahead else {}
    key = _key(p, ahead)
    rounds = _CACHE.get(key)
    if rounds is None:
        if search is None:
            prior = _prior(prior)
            search = lambda lo, sd: _search(master, lo, sd, transitions, prior)   # noqa: E731
        rounds = {}
        proj = projection or {}
        live = HA.live_parts(p)
        for st in ahead:
            rnd = {"audition_mid1": "mid1", "audition_final": "final"}[st]
            got = proj.get(rnd) or {}
            star = int(got["star"]) if got.get("star") is not None else int(prog.get("star") or 0)
            stamina = got["stamina"] if got.get("stamina") is not None else HA.start_stamina(live, rnd, step)
            lo = AE.audition_loadout(p, STAGE[st], catalog, model, params=got.get("params"), star=star, stamina=stamina)
            if not (st == "audition_final" and step >= 9):
                lo["drink_ids"] = HA.expected_drinks(live["drink_ids"], prog.get("idol_card_id"), rnd)
            res = search(lo, HA.exam_seeds(key, st, seeds))
            if not res:
                raise LookupError(f"{st}: no search of the own deck finished")
            n = len(res)
            rounds[st] = {"cfg": AE.battle_config(catalog, prog.get("idol_card_id"), STAGE[st]),
                          "params": {k: int(v) for k, v in lo["parameters"].items()}, "star": star,
                          "permils": {k: int(v) for k, v in lo["score_bonus_permils"].items()},
                          "scores": [sum(r[1][i] for r in res) / n for i in range(3)],
                          "score": sum(r[0] for r in res) / n, "seeds": len(res)}
        if len(_CACHE) > 16:
            _CACHE.clear()
        _CACHE[key] = rounds
    effect = int(prog.get("audition_effect_parameter_bonus_permil") or AE.EFFECT_BONUS)
    factor, source = level if level else (1.0, None)
    ref = OwnRef(rounds, catalog, effect, int(prog.get("star_permil") or 0), known=known,
                 idol=prog.get("idol_card_id"), level=factor)
    ref.level_from = source
    return ref
