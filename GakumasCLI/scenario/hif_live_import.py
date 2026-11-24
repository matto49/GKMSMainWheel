#!/usr/bin/env python3
# Copyright (C) 2026 花吹雪また一年P and contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
"""H.I.F rounds from a live capture: the live opening path.

    python -B scenario/hif_live_import.py <decoded dir> [--out-dir DIR] [--check]
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from GakumasCLI.engine.card_data import effective_audition_parameter_permil          # noqa: E402

AUDITION_START = "/client.api.Produce/StepAuditionStart"
AUDITION_BATTLE_END = "/client.api.Produce/StepAuditionEndExamBattle"
USER_GET = "/client.api.User/Get"
WHOLE = ("user_produce_progress", "user_produce_progress_audition")
ROW_KEYS = {"user_produce_progress_schedule_list": "step_number",
            "user_produce_progress_shop_list": "position_number",
            "user_produce_progress_interval_list": "position_number",
            "user_produce_progress_present_list": "position_number"}


def load_frames(decoded_dir):
    frames = []
    for path in sorted(Path(decoded_dir).glob("*.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        frames.append({"seq": int(row.get("event_sequence") or 0), "rpc": row.get("rpc_path"),
                       "dir": row.get("direction"), "status": row.get("decode_status"),
                       "decoded": row.get("decoded") or {}, "file": path.name})
    frames.sort(key=lambda f: f["seq"])
    return frames


class ProduceState:
    """The produce-side part of ``user_data``, kept current from the wire."""

    def __init__(self):
        self.data = {}
        self.snapshot_seq = None
        self.invalid_reason = None
        self.card_state_complete = False

    def load(self, user_data, seq):
        self.data = {k: copy.deepcopy(v) for k, v in user_data.items() if k.startswith("user_produce")}
        self.snapshot_seq = seq
        self.invalid_reason = None
        self.card_state_complete = (
            isinstance(user_data.get("user_produce_progress"), dict)
            and isinstance(user_data.get("user_produce_progress_produce_card_list"), list)
            and bool(user_data["user_produce_progress_produce_card_list"])
            and isinstance(user_data.get("user_produce_progress_support_card_list"), list))

    def update(self, updated, deleted=None):
        for key, value in (updated or {}).items():
            if not key.startswith("user_produce"):
                continue
            if key in WHOLE or not isinstance(value, list):
                self.data[key] = copy.deepcopy(value)
                continue
            rk = ROW_KEYS.get(key, "number")
            rows = {r.get(rk): r for r in self.data.get(key) or []}
            for r in value:
                rows[r.get(rk)] = copy.deepcopy(r)
            self.data[key] = [rows[n] for n in sorted(rows, key=lambda n: (n is None, n))]
        for key, value in (deleted or {}).items():
            if key.startswith("user_produce") and isinstance(value, list):
                rk = ROW_KEYS.get(key, "number")
                gone = {r.get(rk) for r in value}
                self.data[key] = [r for r in self.data.get(key) or [] if r.get(rk) not in gone]

    def npc_scores(self, step_number=None):
        """The NPC scores of the audition at ``step_number`` (default: the progress's step); [] when unseen."""
        if step_number is None:
            step_number = (self.data.get("user_produce_progress") or {}).get("step_number")
        for row in self.data.get("user_produce_progress_schedule_list") or []:
            if row.get("step_number") == step_number:
                return [int(n.get("score") or 0) for n in row.get("audition_npcs") or []]
        return []

    def round(self):
        progress = self.data.get("user_produce_progress") or {}
        audition = self.data.get("user_produce_progress_audition") or {}
        a = int(progress.get("audition_effect_parameter_bonus_permil") or 0)
        s = int(audition.get("star_bonus_permil") or 0)
        v = int(audition.get("vote_bonus_permil") or 0)
        base = {1: int(audition.get("vocal_permil") or 0), 2: int(audition.get("dance_permil") or 0),
                3: int(audition.get("visual_permil") or 0)}
        deck = []
        for card in sorted(self.data.get("user_produce_progress_produce_card_list") or [],
                           key=lambda r: int(r.get("number") or 0)):
            if card.get("deleted"):
                continue
            entry = {"produce_card_id": card["produce_card_id"],
                     "current_upgrade_count": int(card.get("upgrade_count") or 0)}
            if card.get("customizes"):
                entry["customizes"] = [{"id": c["id"], "customize_count": int(c.get("customize_count") or 1)}
                                       for c in card["customizes"]]
            deck.append(entry)
        supports = sorted(self.data.get("user_produce_progress_support_card_list") or [],
                          key=lambda r: r.get("number") or 0)
        return {
            "step_type": int(progress.get("step_type") or 0),
            "step_id": progress.get("step_id"),
            "step_number": progress.get("step_number"),
            "step_select_number": progress.get("step_select_number"),
            "produce_id": progress.get("produce_id"),
            "audition_number": int(audition.get("step_select_number")
                                   or progress.get("step_select_number") or 0) or None,
            "vote_bonus_permil": v,
            "seed": int(progress.get("exam_seed_value") or 0),
            "idol_card_id": progress.get("idol_card_id"),
            "parameters": {1: int(progress.get("vocal") or 0), 2: int(progress.get("dance") or 0),
                           3: int(progress.get("visual") or 0)},
            "base_permils": base,
            "audition_effect_parameter_bonus_permil": a,
            "star_bonus_permil": s,
            "score_bonus_permils": {k: effective_audition_parameter_permil(p, a, v, s) for k, p in base.items()},
            "max_stamina": int(progress.get("max_stamina") or 0),
            "starting_stamina": int(progress.get("stamina") or 0),
            "deck_cards": deck,
            "produce_item_ids": [i["produce_item_id"] for i in progress.get("produce_items") or []],
            "drink_ids": list(progress.get("produce_drink_ids") or []),
            "support_cards": [{"support_card_id": r["support_card_id"],
                               "permil": int(r.get("produce_card_upgrade_permil") or 0)} for r in supports],
            "memory_ability_enchant_ids": [e["id"] for e in progress.get("exam_status_enchants") or []],
            "research_card_ids": (list(progress.get("research_external_produce_card_ids") or [])
                                  if progress.get("is_research") else []),
            "customize_item_enchant_ids": [e["id"] for e in
                                           progress.get("exam_permanent_audition_status_enchants") or []],
        }


def _commands(turn_logs):
    """Per turn: the command list as engine actions (no turn ends yet)."""
    turns = []
    for log in turn_logs:
        actions = []
        for cmd in log.get("commands") or []:
            kind = cmd.get("command_type")
            if kind == 2:
                actions.append(("play", int(cmd.get("use_index") or 0)))
            elif kind == 3:
                actions.append(("drink", int(cmd.get("use_index") or 0)))
            elif kind == 12:
                continue
            elif cmd.get("select_indexes") is not None:
                actions.append(("select", tuple(int(i) for i in cmd["select_indexes"])))
            else:
                raise ValueError(f"unknown command {cmd!r}")
        turns.append({"turn": int(log.get("turn") or 0), "actions": actions,
                      "hand": [(h["id"], int(h.get("upgrade_count") or 0))
                               for h in log.get("start_play_hand_produce_cards") or []],
                      "stamina": int(log.get("stamina") or 0), "score": int(log.get("score") or 0),
                      "block": int(log.get("block") or 0)})
    return turns


def rounds_from_frames(frames):
    state, rounds, pending = ProduceState(), [], None
    for f in frames:
        dec = f["decoded"]
        if f["status"] != "decoded":
            continue
        if f["rpc"] == USER_GET and f["dir"] == "in" and dec.get("user_data"):
            state.load(dec["user_data"], f["seq"])
        common = dec.get("common_response") or {}
        if state.snapshot_seq is not None and (common.get("updated_data") or common.get("deleted_data")):
            state.update(common.get("updated_data"), common.get("deleted_data"))
        if f["rpc"] == AUDITION_START and f["dir"] == "in":
            if state.snapshot_seq is None:
                continue
            pending = dict(state.round(), start_frame=f["file"], snapshot_seq=state.snapshot_seq,
                           card_state_complete=bool(state.card_state_complete))
        elif f["rpc"] == AUDITION_BATTLE_END and f["dir"] == "out" and pending and dec.get("turn_end_logs"):
            result = dec.get("result") or {}
            pending.update(recorded_score=int(result.get("score") or 0), rank=int(result.get("rank") or 0),
                           limit_turn=len(dec["turn_end_logs"]), turns=_commands(dec["turn_end_logs"]),
                           npc_scores=[int(n.get("score") or 0) for n in dec.get("npc_results") or []],
                           end_frame=f["file"])
            rounds.append(pending)
            pending = None
    return rounds


def _step(session, action):
    """``ExamSession.step`` split at the turn boundary."""
    kind, closing = action[0], None
    if kind == "play":
        index = int(action[1])
        if index < 0 or index >= len(session.deck.hand) or not session.can_play(session.deck.hand[index]):
            raise ValueError(f"illegal action {action!r}")
        session.play(index)
        if session.cleared or session.state.playable <= 0:
            closing = False
    elif kind == "drink":
        index = int(action[1])
        if index < 0 or index >= len(session.drinks):
            raise ValueError(f"illegal action {action!r}")
        session.use_drink(index)
        if session.cleared or session.state.playable <= 0:
            closing = False
    elif kind == "end":
        closing = session.state.playable > 0
    else:
        raise ValueError(f"unknown action {action!r}")
    if closing is None:
        return None
    score = int(session.state.score)
    session.end_turn(closing)
    session._replay_close_stamina = int(session.state.stamina)
    if not session.finished:
        session.start_turn()
    return score


def replay(master, rnd):
    """Rebuild the session from the round's loadout, follow the recorded
    commands, and compare turn by turn.
    """
    from GakumasCLI.scenario.hif_replay_import import to_loadout
    from GakumasCLI.scenario.hif import build_hif_loadout_session
    session, _ = build_hif_loadout_session(master, to_loadout(rnd))
    session.state.select_index_queue = [list(a[1]) for t in rnd["turns"] for a in t["actions"]
                                        if a[0] == "select"]
    route, diffs, per_turn = [], [], []
    for t in rnd["turns"]:
        if session.finished:
            diffs.append({"turn": t["turn"], "field": "finished_early"})
            break
        if session.turn != t["turn"]:
            diffs.append({"turn": t["turn"], "field": "turn", "engine": session.turn})
        hand = [(c["produce_card_id"], int(c.get("current_upgrade_count") or 0)) for c in session.deck.hand]
        if hand != [tuple(c) for c in t["hand"]]:
            diffs.append({"turn": t["turn"], "field": "hand", "engine": hand, "game": t["hand"]})
        turn_score = None
        for action in t["actions"]:
            route.append(action)
            if action[0] == "select":
                continue
            try:
                closed = _step(session, action)
            except ValueError as exc:
                diffs.append({"turn": t["turn"], "field": "illegal", "action": action, "error": str(exc)})
                return route, {"diffs": diffs, "per_turn": per_turn, "score": int(session.state.score)}
            if closed is not None:
                turn_score = closed
        if not session.finished and session.turn == t["turn"]:
            turn_score = _step(session, ("end",))
            route.append(("end",))
        stamina = int(getattr(session, "_replay_close_stamina", session.state.stamina))
        per_turn.append({"turn": t["turn"], "engine_score": turn_score, "game_score": t["score"],
                         "engine_stamina": stamina, "game_stamina": t["stamina"]})
        if t["score"] is not None and turn_score is not None and int(t["score"]) != int(turn_score):
            diffs.append({"turn": t["turn"], "field": "score", "engine": int(turn_score),
                          "game": int(t["score"])})
        if t["stamina"] is not None and int(t["stamina"]) != stamina:
            diffs.append({"turn": t["turn"], "field": "stamina", "engine": stamina,
                          "game": int(t["stamina"])})
    return route, {"diffs": diffs, "per_turn": per_turn, "score": int(session.state.score)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("decoded_dir")
    ap.add_argument("--out-dir")
    ap.add_argument("--check", action="store_true", help="replay each round and compare with the game")
    ap.add_argument("--master-dir", default=str(ROOT / "ro" / "master" / "tables"))
    args = ap.parse_args(argv)
    rounds = rounds_from_frames(load_frames(args.decoded_dir))
    master = None
    if args.check:
        from GakumasCLI.scenario.exam_session import Master
        master = Master.load(args.master_dir)
    rc = 0
    for rnd in rounds:
        if not rnd["card_state_complete"]:
            print(f"{rnd['step_id']} seed {rnd['seed']}: skipped -- the login snapshot had no complete card list")
            continue
        head = (f"{rnd['step_id']} step_type {rnd['step_type']} seed {rnd['seed']} {rnd['idol_card_id']}: "
                f"deck {len(rnd['deck_cards'])}, items {len(rnd['produce_item_ids'])}, "
                f"drinks {len(rnd['drink_ids'])}, supports {len(rnd['support_cards'])}, "
                f"permils {rnd['score_bonus_permils']}, turns {rnd['limit_turn']}, "
                f"recorded {rnd['recorded_score']:,}")
        print(head)
        if master is not None:
            route, report = replay(master, rnd)
            rnd["actions"] = route
            ok = report["score"] == rnd["recorded_score"] and not report["diffs"]
            rc |= not ok
            print(f"  engine {report['score']:,} vs game {rnd['recorded_score']:,} "
                  f"({report['score'] - rnd['recorded_score']:+,}); {len(report['diffs'])} turn diffs")
            for d in report["diffs"][:12]:
                print("   ", json.dumps(d, ensure_ascii=False))
            rnd["check"] = report
        if args.out_dir:
            out = Path(args.out_dir)
            out.mkdir(parents=True, exist_ok=True)
            name = f"{rnd['idol_card_id']}_{rnd['seed']}_{rnd['step_type']}.json"
            (out / name).write_text(json.dumps(rnd, ensure_ascii=False, indent=1, default=list), encoding="utf-8")
    if not rounds:
        print("no audition round with a login snapshot before it")
        rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
