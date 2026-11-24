# Copyright (C) 2026 花吹雪また一年P
# SPDX-License-Identifier: AGPL-3.0-or-later
"""the two data files of the simulator, with the source of every number.

    python -B -m GKMSPS.tools.build_ps_data <gakumasu-diff clone> <the research data clone>      (from the repository root)
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
DATA = HERE / "data"


def mirror_rows(clone, name):
    import yaml
    loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    with open(Path(clone) / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.load(f, Loader=loader)


def git_head(path):
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def master_ps(mirror):
    dear = {}
    for r in mirror_rows(mirror, "CharacterDearnessLevel"):
        if r.get("produceSkills"):
            dear.setdefault(r["characterId"], {})[str(r["dearnessLevel"])] = [[s["id"], s["level"]] for s in r["produceSkills"]]
    grade = [[r["grade"].replace("ResultGrade_", ""), r["threshold"]] for r in mirror_rows(mirror, "ProduceGrade")
             if r["produceGroupId"] == "produce_group-003"]
    true_end = {}
    for r in mirror_rows(mirror, "CharacterTrueEndBonus"):
        ch = r["id"].replace("character_true_end_bonus-", "")
        true_end.setdefault(ch, {})[r["produceType"].replace("ProduceType_", "")] = {
            "vocal": r["produceVocal"], "dance": r["produceDance"], "visual": r["produceVisual"],
            "g_vocal": r["produceVocalGrowthRatePermil"], "g_dance": r["produceDanceGrowthRatePermil"],
            "g_visual": r["produceVisualGrowthRatePermil"], "stamina": r["produceStamina"]}
    return {"format": "gkms.ps.master.v1", "source": {"mirror": "vertesan/gakumasu-diff", "commit": git_head(mirror)},
            "dearness_skills": dear, "grade": grade, "true_end_bonus": true_end}


def counts(section, prefix):
    out = {}
    for k, v in section.items():
        if k.startswith(prefix):
            out[k[len(prefix):]] = v
    return out


def offer_family(effect_id):
    """reward-set effect id -> (produce, kind) of the rarity table: school1 / school3 / activity2 / activity3 (選抜),
    school / activity (本戦); None for a family the table does not cover."""
    f = re.sub(r"^p_effect-produce_reward_set-p_rd-", "", effect_id or "")
    m = re.match(r"produce_00(7|8)-event_(school|activity)(?:-before_(\d))?", f)
    if not m:
        return None
    return f"produce-00{m.group(1)}", m.group(2) + (m.group(3) or "")


OFFER_UPGRADE_COUNTS = {
    "produce-007": {"activity2": {"R": [219, 198], "SR": [199, 156], "SSR": [53, 23]},
                    "activity3": {"R": [63, 54], "SR": [62, 49], "SSR": [37, 25]},
                    "present_list": {"R": [348, 78], "SR": [789, 205], "SSR": [297, 76]}},
    "produce-008": {"activity": {"R": [33, 29], "SR": [32, 21], "SSR": [13, 7]},
                    "present_list": {"R": [135, 47], "SR": [426, 96], "SSR": [243, 47]}},
}


def draw_models(research):
    ps01 = json.load(open(DATA / "ps01_measure.json", encoding="utf-8"))
    cs = json.load(open(Path(research) / "produce" / "catch" / "catch_stats_modes.json", encoding="utf-8"))
    offer = {}
    for key, v in cs["sources"].items():
        pid, fam = key.split("|", 1)
        if pid not in ("produce-007", "produce-008") or fam in ("*",):
            continue
        if fam == "present":
            kind = "present"
        elif fam in ("shop", "interval"):
            kind = fam
        else:
            ok = offer_family("p_effect-produce_reward_set-p_rd-" + fam)
            if ok is None:
                continue
            kind = ok[1]
        tab = offer.setdefault(pid, {}).setdefault(kind, {"R": 0, "SR": 0, "SSR": 0})
        for r, x in v["rarity"].items():
            tab[r] += x["n"]
    shop_card = {}
    for k, n in ps01["shop_card_rarity"].items():
        pid, step, rar = k.split("|")
        if int(step) == 1:
            continue
        shop_card.setdefault(pid, {}).setdefault(str(int(step)), {"R": 0, "SR": 0, "SSR": 0})[rar] += n
    upgrade = {}
    for k, n in ps01["shop_price"].items():
        m = re.match(r"card (\w+)\+(\d)\|", k)
        if m:
            upgrade.setdefault(m.group(1), [0, 0])[int(m.group(2))] += n
    drink = {}
    for k, n in ps01["shop_drink_rarity"].items():
        pid, rar = k.split("|")
        drink.setdefault(pid, {"R": 0, "SR": 0, "SSR": 0})[rar] += n
    return {
        "format": "gkms.ps.draw_models.v1",
        "labels_version": 1,
        "source": {"ps01_measure": "GKMSPS/data/ps01_measure.json",
                   "catch_stats": "research data " + git_head(research)[:12] + ": offer rarity by source family"},
        "sp_lesson": {"base_permil": ps01["sp_lesson"]["additive"]["base_permil"], "grade": "B",
                      "rule": "chance(min(1000, rate + base)) per lesson candidate",
                      "n": ps01["sp_lesson"]["candidates"]},
        "sub_attribute": {"higher_permil": 700, "grade": "B",
                          "n": sum(ps01["sub_attribute"].values()),
                          "rule": "of the two other attributes, the one with the higher audition criterion"},
        "offer_rarity": {"grade": "B", "rule": "rarity weights per (produce, source kind), then a card of that rarity "
                         "uniformly from the pool (card weights inside a rarity are the server's, unknown)",
                         "table": offer},
        "shop": {"grade": "B", "card_rarity_by_step": shop_card,
                 "card_unupgraded_by_rarity": {r: v[0] for r, v in upgrade.items()},
                 "card_upgraded_by_rarity": {r: v[1] for r, v in upgrade.items()},
                 "drink_rarity": drink,
                 "card_price": {"R": [100, 100], "SR": [50, 110], "SSR": [100, 150]},
                 "drink_price": {"R": 50, "SR": 75, "SSR": 100},
                 "sale_positions": [1, 5], "sale_permil": 300,
                 "service_price": [100, 125, 150, 175], "refresh_cost": 0},
        "interval": {"grade": "B", "source": "recorded runs: shop and interval offers",
                     "card_price": {"R": [30, 30], "SR": [50, 50], "SSR": [50, 80]},
                     "change_price": {"R": 30, "SR": 50, "SSR": 80},
                     "drink_price": {"R": 50, "SR": 75, "SSR": 100},
                     "card_rarity_permil": {"R": 250, "SR": 500, "SSR": 250}, "ssr_upgraded_permil": 467,
                     "drink_ssr_permil": 500, "upgrade_price": [100, 125], "stamina_per_10p": 2,
                     "refresh_cost": [10, 10, 20, 30, 40, 50, 50]},
        "offer_upgrade": {"grade": "B", "source": "recorded runs (GKMSPS/tools/build_ps_data.py OFFER_UPGRADE_COUNTS)",
                          "rule": "each offered card with an upgraded form is upgraded on its own with the permil of its "
                                  "(produce, kind, rarity); kinds not listed offer unupgraded cards",
                          "permil": {pid: {k: {r: n[1] * 1000 // n[0] for r, n in v.items()} for k, v in kinds.items()}
                                     for pid, kinds in OFFER_UPGRADE_COUNTS.items()},
                          "n": OFFER_UPGRADE_COUNTS},
        "support_event": {"grade": "B", "source": "GakumasCLI/produce/hif_schedule.py RATE / rolls",
                          "rate_permil": {"1": 68, "2": 60, "3": 35}},
        "npc_score": {"grade": "A range + assumption", "rule": "uniform in [scoreMin, scoreMax]"},
        "starter_delete": {"grade": "A effect + assumption", "rule": "2 of the starter cards in the deck, equal weights"},
    }


def main(argv):
    mirror, research = argv[1], argv[2]
    DATA.mkdir(exist_ok=True)
    for name, doc in (("master_ps.json", master_ps(mirror)), ("draw_models.v1.json", draw_models(research))):
        with open(DATA / name, "w", encoding="utf-8") as f:
            json.dump(doc, f, ensure_ascii=False, sort_keys=True, **({"separators": (",", ":")} if name == "master_ps.json" else {"indent": 1}))
            f.write("\n")
    print(json.dumps({"ok": True}))


if __name__ == "__main__":
    main(sys.argv)
