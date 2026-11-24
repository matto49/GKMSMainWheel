# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""an audition ahead built from the live produce state alone (no recorded template), and its value from the production
search on several exam seeds.

    python -B -m GakumasCLI.produce.audition_eval check <the run archive clone> [--json OUT]
    python -B -m GakumasCLI.produce.audition_eval eval LOADOUT.json --seeds N [--transitions T]
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict

import GakumasCLI.produce.produce_effects as pe
from GakumasCLI.produce import hif_advice as HA

SELECTION, MAIN = HA.SELECTION, HA.MAIN
STAGE = {(SELECTION, 16): "sel1", (SELECTION, 17): "sel2", (SELECTION, 18): "sel3", (MAIN, 16): "main1",
         (MAIN, 18): "main2"}
ROUND = {"sel1": "mid1", "sel2": "mid2", "sel3": "final", "main1": "mid1", "main2": "final"}
STEP_NAME = {"mid1": "AUDITION_MID1", "mid2": "AUDITION_MID2", "final": "AUDITION_FINAL"}
STEP_TYPE = {"mid1": 16, "mid2": 17, "final": 18}
FILE_STEP = {16: "AuditionMid1", 17: "AuditionMid2", 18: "AuditionFinal"}
EFFECT_BONUS = 500
FIELDS = ("deck_cards", "produce_item_ids", "drink_ids", "support_cards", "lesson_start_enchant_ids",
          "main_item_enchant_ids", "parameters", "score_bonus_permils", "starting_stamina", "max_stamina")


def battle_config(catalog, idol, stage):
    pid = SELECTION if stage.startswith("sel") else MAIN
    return catalog.difficulty(pe.difficulty_id(idol), STEP_NAME[ROUND[stage]], pid)["produceExamBattleConfigId"]


def permils(catalog, cfg, params, effect_bonus, star_bonus):
    base = catalog.base_permils(cfg, {int(k): int(v) for k, v in params.items()})
    return {str(k): -(-base[k] * (1000 + int(effect_bonus)) * (1000 + int(star_bonus)) // 10 ** 7) * 10 for k in base}


def _known(*values):
    """The first value that is not None."""
    return next(v for v in values if v is not None)


def audition_loadout(p, stage, catalog, model=None, *, params=None, star=None, stamina=None):
    """The ``stage`` audition (sel1 .. sel3, main1, main2) of produce state ``p`` (hif_advice.Tracker's)."""
    prog = p.progress
    live = HA.live_parts(p)
    idol = prog.get("idol_card_id")
    params = {str(k): int(v) for k, v in (params or live["parameters"]).items()}
    star = int(prog.get("star") or 0) if star is None else int(star)
    effect = int(prog.get("audition_effect_parameter_bonus_permil") or EFFECT_BONUS)
    rnd = ROUND[stage]
    lo = {"schema": "gkms.hif_loadout.v1", "idol_card_id": idol, "round": rnd,
          "produce_id": SELECTION if stage.startswith("sel") else MAIN, "step_type": STEP_TYPE[rnd],
          "parameters": params,
          "score_bonus_permils": permils(catalog, battle_config(catalog, idol, stage), params, effect,
                                         pe.star_bonus(stage, star)),
          "deck_cards": live["deck_cards"], "include_produce_default": False,
          "produce_item_ids": live["produce_item_ids"], "drink_ids": live["drink_ids"],
          "support_cards": live["support_cards"],
          "max_stamina": live["max_stamina"],
          "starting_stamina": max(1, min(int(_known(stamina, live["stamina"], live["max_stamina"])), live["max_stamina"])),
          "research_card_ids": []}
    lse = list(live["lesson_start_enchant_ids"])
    if not lse and stage.startswith("main") and model is not None and p.memories:
        lse = HA.memory_enchants(p, model)
    lo["lesson_start_enchant_ids"] = lse
    lo["main_item_enchant_ids"] = list(live["main_item_enchant_ids"])
    return lo


def _norm(field, v):
    if field == "deck_cards":
        return sorted((c.get("produce_card_id"), int(c.get("current_upgrade_count") or 0),
                       tuple(sorted((x.get("id"), int(x.get("customize_count") or 1)) for x in c.get("customizes") or ())))
                      for c in v or ())
    if field in ("produce_item_ids", "drink_ids", "lesson_start_enchant_ids", "main_item_enchant_ids"):
        return sorted(v or ())
    if field == "support_cards":
        return sorted((s.get("support_card_id"), int(s.get("permil") or 0)) for s in v or ())
    if field in ("parameters", "score_bonus_permils"):
        return {str(k): int(x) for k, x in (v or {}).items()}
    return int(v or 0)


def compare(built, recorded):
    """{field: True / False} for FIELDS."""
    return {f: _norm(f, built.get(f)) == _norm(f, recorded.get(f)) for f in FIELDS}


def check(arch, out=None):
    """Every recorded H.I.F audition of the archive against its state-built loadout."""
    from GakumasCLI.engine.audition import AuditionCatalog
    catalog = AuditionCatalog.load(pe.MASTER_DIR)
    model = pe.Model()
    rows, per_field, misses = [], Counter(), defaultdict(list)
    from GakumasCLI.corpus.archive import open_archive
    a = open_archive(arch)
    for e in a.entries("live-runs"):
        if "hif" not in (e.get("scenarios") or [e.get("scenario")]):
            continue
        files = a.files(e)
        loads = {p: f for p, f in files.items() if p.startswith("hif/loadout_")}
        if "frames.jsonl" not in files or not loads:
            continue
        try:
            frames = [json.loads(x) for x in a.read(e, "frames.jsonl").splitlines() if x.strip()]
        except (OSError, ValueError, KeyError):
            continue
        tr, event = HA.Tracker(), False
        for f in frames:
            f.setdefault("status", "decoded")
            dec = f.get("decoded") or {}
            if f.get("dir") == "out" and (f.get("rpc") or "").endswith("/Start") and "produce_id" in dec:
                event = bool(dec.get("is_research") or dec.get("is_high_score_rush"))
            if not tr.feed(f) or event or f.get("dir") == "out" or not (f.get("rpc") or "").endswith("/StepAuditionStart"):
                continue
            p = tr.current
            prog = p.progress
            stage = STAGE.get((prog.get("produce_id"), prog.get("step_type")))
            seed = prog.get("exam_seed_value")
            name = f"hif/loadout_{FILE_STEP.get(prog.get('step_type'))}_{seed}.json"
            if stage is None or name not in loads:
                continue
            rec = json.loads(a.read(e, name))
            if rec.get("produce_id") != prog.get("produce_id"):
                continue
            try:
                built = audition_loadout(p, stage, catalog, model)
            except Exception as exc:                    # noqa: BLE001
                rows.append({"run": e["id"], "stage": stage, "error": f"{type(exc).__name__}: {exc}"[:200]})
                continue
            eq = compare(built, rec)
            rows.append({"run": e["id"], "stage": stage, "seed": seed, "full": p.full, **eq})
            for k, v in eq.items():
                per_field[k, p.full] += v
                if not v and len(misses[k]) < 5:
                    misses[k].append((e["id"], stage, p.full, _norm(k, built.get(k)), _norm(k, rec.get(k))))
    n = sum(1 for r in rows if "error" not in r)
    nf = sum(1 for r in rows if r.get("full"))
    print(f"auditions {len(rows)} built {n} errors {len(rows) - n}; with the whole state followed {nf}")
    for k in FIELDS:
        print(f"  {k:26s} equal {per_field[k, True]} of {nf}; recording started mid-produce {per_field[k, False]} of {n - nf}")
    for k, xs in misses.items():
        for x in xs[:3]:
            print("   ", k, str(x)[:300])
    if out:
        with open(out, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return rows


def evaluate(loadout, seeds, transitions=16_000_000, prior_path=None):
    """[(exam seed, score)] of ``loadout`` on exam seeds 1..N: one production search job each (search seed 182, from
    scratch, runs of 500k transitions, stabilization 3, the production prior), ``transitions`` per seed, no tail."""
    import os
    import GakumasCLI.paths as P
    from GakumasCLI.native import route_search_native as RSN
    from GakumasCLI.scenario import hif_route_search as HRS
    from GakumasCLI.scenario.exam_session import Master
    from GakumasCLI.scenario.hif import build_hif_loadout_session
    os.environ.setdefault("GKMS_NATIVE_LIB", str(P.NATIVE_LIB))
    os.environ.setdefault("GKMS_NATIVE_STRINGS", str(P.NATIVE_STRINGS))
    master = Master.load(P.ENGINE_ROOT / "ro" / "master" / "tables")
    RSN.install()
    RSN._native(master)
    prior = HRS.load_prior(str(prior_path or P.ENGINE_ROOT / "priors" / "prior_rev80_drink_turns.json"))
    out = []
    for s in range(1, int(seeds) + 1):
        session, _ = build_hif_loadout_session(master, dict(loadout, seed=s))
        _path, best, _steps = HRS._search_paths(session, None, nrpa_seconds=0, nrpa_transitions=int(transitions),
                                                tail_seconds=0.0, seed=182, prior=prior, from_scratch=True,
                                                stabilization=3, run_transitions=500000)
        out.append((s, best))
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "check":
        check(argv[1], argv[argv.index("--json") + 1] if "--json" in argv else None)
        return 0
    if argv and argv[0] == "eval":
        lo = json.load(open(argv[1], encoding="utf-8"))
        res = evaluate(lo, int(argv[argv.index("--seeds") + 1]),
                       int(argv[argv.index("--transitions") + 1]) if "--transitions" in argv else 16_000_000)
        scores = [s for _, s in res if s]
        print(json.dumps({"seeds": res, "mean": round(sum(scores) / max(1, len(scores))),
                          "min": min(scores, default=None), "max": max(scores, default=None)}))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
